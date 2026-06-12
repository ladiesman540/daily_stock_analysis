# -*- coding: utf-8 -*-
"""Discovery hit-rate scorecard service.

Grades every persisted discovery flag against the trust gate: did the name
close at/above +20% within 90 calendar days of being flagged (hit), close
at/below -20% first (flop), or do neither before the window plus a 7-day
slack ran out (expired)? Flags still inside the window stay open. Batting
average = hits / (hits + flops + expired); open flags never count.

Forward bars come from the stock_daily cache. Symbols that drop out of the
daily discovery fetch set would starve of forward bars, so each run does a
bounded catch-up fetch (capped, fail-open) for open flags with stale bars.
Real and simulated (bootstrap) outcomes are stored side by side but NEVER
merged in any summary.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.services.free_data_service import (
    FreeDataService,
    _env_float,
    _env_int,
    _now_iso,
    _unique_symbols,
    _valid_price_volume_bars,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

# Discovery screen booleans persisted into screens_json. Future screens are
# picked up by appending their key here; no schema change needed.
SCREEN_KEYS = (
    "near_52w_high",
    "rs_top_decile",
    "unusual_volume",
    "sector_tailwind",
    "quiet_accumulation",
    "beaten_down_reversal",
)

DEFAULT_WINDOW_DAYS = 90
DEFAULT_HIT_PCT = 20.0
DEFAULT_FLOP_PCT = -20.0
DEFAULT_LOOKBACK_DAYS = 400
DEFAULT_FETCH_LIMIT = 300
# Wait a week past the window before calling a flag expired: cached bars can
# lag a few sessions, and a late catch-up fetch may still reveal a hit/flop.
EXPIRY_SLACK_DAYS = 7
CATCHUP_FETCH_TIMEOUT_SECONDS = 600
# A cached bar within this many calendar days counts as fresh (weekends/holidays safe).
STALE_BAR_AGE_DAYS = 4
# (floor, label) checked top-down; None floor catches everything below 60.
SCORE_BANDS = ((90.0, "90+"), (80.0, "80-90"), (70.0, "70-80"), (60.0, "60-70"), (None, "<60"))

# Retroactive bootstrap (simulated history). 39 weeks back leaves the most
# recent simulated window fully gradeable; 600 calendar days of bars keeps
# >= 200 trading bars in front of the scorer even at the earliest sampled date.
DEFAULT_BOOTSTRAP_WEEKS = 39
DEFAULT_BOOTSTRAP_DEEP_DAYS = 600
BOOTSTRAP_FETCH_TIMEOUT_SECONDS = 1800
BOOTSTRAP_SAMPLE_EVERY_N_SESSIONS = 5  # one simulated snapshot per trading week


def evaluate_flag(
    entry_close: float,
    as_of: date,
    closes: Iterable[Tuple[date, float]],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    hit_pct: float = DEFAULT_HIT_PCT,
    flop_pct: float = DEFAULT_FLOP_PCT,
    today: Optional[date] = None,
) -> Dict[str, Any]:
    """Grade one discovery flag against forward closes (pure, no I/O).

    Walks bars in date order; only closes strictly after as_of and within
    window_days CALENDAR days count. First close at/above +hit_pct wins (hit),
    first close at/below flop_pct wins (flop) - whichever bar comes first.
    With neither touched: expired once today is past the window plus
    EXPIRY_SLACK_DAYS, otherwise open.
    """
    today = today or date.today()
    result: Dict[str, Any] = {
        "status": "open",
        "hit_date": None,
        "days_to_hit": None,
        "max_gain_pct": None,
        "current_return_pct": None,
        "last_eval_bar_date": None,
    }
    window_bars = [
        (bar_date, close)
        for bar_date, close in sorted(closes)
        if close and bar_date > as_of and (bar_date - as_of).days <= window_days
    ]
    for bar_date, close in window_bars:
        # Round before comparing so a close at exactly +20%/-20% counts as
        # touched despite binary float noise (120/100 -> 19.999999999999996).
        gain = round((close / entry_close - 1.0) * 100, 4)
        result["max_gain_pct"] = gain if result["max_gain_pct"] is None else max(result["max_gain_pct"], gain)
        result["current_return_pct"] = gain
        result["last_eval_bar_date"] = bar_date
        if gain >= hit_pct:
            result["status"] = "hit"
            result["hit_date"] = bar_date
            result["days_to_hit"] = (bar_date - as_of).days
            break
        if gain <= flop_pct:
            result["status"] = "flop"
            break
    if result["status"] == "open" and (today - as_of).days > window_days + EXPIRY_SLACK_DAYS:
        result["status"] = "expired"
    for key in ("max_gain_pct", "current_return_pct"):
        if result[key] is not None:
            result[key] = round(result[key], 2)
    return result


def _bars_until(bars: List[Dict[str, Any]], as_of: str) -> List[Dict[str, Any]]:
    """All bars dated at/before ISO date `as_of` - THE single truncation chokepoint.

    Every per-date computation in the bootstrap (screens, percentiles,
    composite, scorer) sees ONLY this function's output, so lookahead bias
    cannot leak in anywhere else. ISO-string comparison is safe because bar
    dates are normalized YYYY-MM-DD strings; longer datetime strings are
    truncated to their date part first. Bars without a date are dropped.
    """
    sliced: List[Dict[str, Any]] = []
    for bar in bars:
        bar_date = str(bar.get("date") or "")[:10]
        if bar_date and bar_date <= as_of:
            sliced.append(bar)
    return sliced


def _sample_bootstrap_dates(
    trading_dates: Iterable[str],
    *,
    weeks: int,
    before: Optional[str],
    today: date,
) -> List[str]:
    """Every Nth trading date covering `weeks` back, strictly before `before`.

    `before` is the earliest REAL discovery snapshot date (simulated history
    must never overlap real snapshots); None means no real snapshot exists yet
    and every sampled date is kept.
    """
    start = (today - timedelta(weeks=max(1, weeks))).isoformat()
    in_window = sorted({str(d)[:10] for d in trading_dates if str(d)[:10] >= start})
    sampled = in_window[::BOOTSTRAP_SAMPLE_EVERY_N_SESSIONS]
    if before:
        sampled = [d for d in sampled if d < before]
    return sampled


class ScorecardService:
    """Seed, evaluate, and summarize discovery flag outcomes."""

    def __init__(
        self,
        *,
        free_data: Optional[FreeDataService] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self.free_data = free_data or FreeDataService()
        self.db = db or DatabaseManager.get_instance()

    def run_daily_evaluation(self) -> Dict[str, Any]:
        """Seed flags from discovery history, evaluate every non-terminal one, upsert.

        Idempotent: terminal rows (hit/flop/expired) are never re-evaluated.
        Returns plain counts for the pipeline step summary.
        """
        today = date.today()
        window_days = max(1, _env_int("SCORECARD_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))
        hit_pct = _env_float("SCORECARD_HIT_PCT", DEFAULT_HIT_PCT)
        flop_pct = _env_float("SCORECARD_FLOP_PCT", DEFAULT_FLOP_PCT)
        # Lookback must cover the full grading horizon (window + expiry slack
        # + 1): a smaller SCORECARD_LOOKBACK_DAYS would strand still-open flags
        # aged window..window+slack days outside the evaluation window forever.
        lookback_days = max(
            window_days + EXPIRY_SLACK_DAYS + 1,
            _env_int("SCORECARD_LOOKBACK_DAYS", DEFAULT_LOOKBACK_DAYS),
        )
        fetch_limit = max(0, _env_int("SCORECARD_FETCH_LIMIT", DEFAULT_FETCH_LIMIT))

        existing: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in self.db.get_discovery_flag_outcomes(days=lookback_days, simulated=False)["rows"]:
            existing[(row["as_of"], row["symbol"])] = row

        # Catch-up fetch BEFORE evaluation so refreshed bars feed this run.
        # Uses only previously persisted open flags: today's brand-new flags
        # were just fetched by the discovery step and are never stale.
        catch_up = self._catch_up_fetch(window_days=window_days, fetch_limit=fetch_limit, today=today)

        seeded = new_count = skipped_terminal = skipped_invalid = 0
        work: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for snapshot in self.db.get_discovery_history(days=lookback_days):
            as_of = snapshot.get("as_of")
            if not as_of:
                continue
            for row in snapshot.get("constituents") or []:
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                seeded += 1
                close = row.get("close")
                if not close or float(close) <= 0:
                    skipped_invalid += 1
                    continue
                prior = existing.get((as_of, symbol))
                if prior is not None and prior.get("status") != "open":
                    skipped_terminal += 1
                    continue
                if prior is None:
                    new_count += 1
                work[(as_of, symbol)] = {
                    "as_of": as_of,
                    "symbol": symbol,
                    "entry_close": float(close),
                    "composite_score": row.get("composite_score"),
                    "candidate_score": row.get("candidate_score"),
                    "screens": [key for key in SCREEN_KEYS if row.get(key)],
                    "simulated": False,
                }
        # Persisted open rows no longer re-derivable from history (e.g. a
        # purged snapshot) keep getting evaluated until they go terminal.
        for key, row in existing.items():
            if row.get("status") == "open" and key not in work and row.get("entry_close"):
                work[key] = {
                    "as_of": row["as_of"],
                    "symbol": row["symbol"],
                    "entry_close": float(row["entry_close"]),
                    "composite_score": row.get("composite_score"),
                    "candidate_score": row.get("candidate_score"),
                    "screens": row.get("screens") or [],
                    "simulated": False,
                }

        counts = {"open": 0, "hit": 0, "flop": 0, "expired": 0}
        results: List[Dict[str, Any]] = []
        bars_cache: Dict[str, Tuple[date, List[Tuple[date, float]]]] = {}
        # Sorted by (as_of, symbol) so each symbol's EARLIEST flag is evaluated
        # first: _closes_since caches the widest bar window on the first
        # encounter per symbol, and later flags (later as_of, narrower window)
        # are served by slicing that cache instead of re-reading the DB. Out of
        # order would still be correct (_closes_since refetches when the cached
        # start is too late) but wastes a query per symbol.
        for (as_of, _symbol), row in sorted(work.items()):
            as_of_date = date.fromisoformat(as_of)
            closes = self._closes_since(row["symbol"], as_of_date, cache=bars_cache, today=today)
            outcome = evaluate_flag(
                row["entry_close"], as_of_date, closes,
                window_days=window_days, hit_pct=hit_pct, flop_pct=flop_pct, today=today,
            )
            counts[outcome["status"]] += 1
            results.append({
                **row,
                **outcome,
                "window_days": window_days,
                "hit_threshold_pct": hit_pct,
                "flop_threshold_pct": flop_pct,
            })
        persisted = self.db.upsert_discovery_flag_outcomes(results) if results else 0
        return {
            "as_of": today.isoformat(),
            "seeded": seeded,
            "new": new_count,
            "skipped_terminal": skipped_terminal,
            "skipped_invalid": skipped_invalid,
            "evaluated": len(results),
            **counts,
            "persisted": persisted,
            "catch_up": catch_up,
        }

    def build_summary(
        self,
        *,
        days: int = DEFAULT_LOOKBACK_DAYS,
        include_simulated: bool = True,
    ) -> Dict[str, Any]:
        """Scorecard summary; real and simulated stats are NEVER merged.

        Shape note for API consumers (Phase 3 UI): each population block
        ("real" / "simulated") splits into two sub-populations carrying the
        same fields (flags, hits, flops, expired, open, decided,
        batting_average, avg_days_to_hit, avg_max_gain_pct):

        - "flagged": rows that passed >=1 screen or carry a candidate_score
          (made the scored top-40) - ideas the system actually surfaced.
          This is the headline batting average.
        - "baseline": ALL rows (the liquidity-qualified universe), kept
          deliberately as the control group; its hit rate is the market base
          rate that "flagged" must beat to prove edge.

        "edge" = flagged.batting_average - baseline.batting_average (null
        when either is null). by_screen / by_score_band / by_month are
        computed over flagged rows only.
        """
        real_rows = self.db.get_discovery_flag_outcomes(days=days, simulated=False)["rows"]
        simulated_block: Optional[Dict[str, Any]] = None
        if include_simulated:
            sim_rows = self.db.get_discovery_flag_outcomes(days=days, simulated=True)["rows"]
            if sim_rows:
                simulated_block = _summary_block(sim_rows)
        caveats = [
            "Batting average = hits / (hits + flops + expired); open flags are excluded.",
            "Headline stats use 'flagged' rows (passed >=1 screen or made the scored "
            "top-40); 'baseline' covers the whole liquidity-qualified universe as the "
            "control group its hit rate must beat.",
        ]
        if simulated_block:
            caveats.append(
                "Simulated history replays today's universe over past dates - delisted "
                "losers are missing, so simulated hit rates are an optimistic ceiling. "
                "Real and simulated stats are never merged."
            )
        return {
            "days": days,
            "window_days": max(1, _env_int("SCORECARD_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)),
            "hit_threshold_pct": _env_float("SCORECARD_HIT_PCT", DEFAULT_HIT_PCT),
            "flop_threshold_pct": _env_float("SCORECARD_FLOP_PCT", DEFAULT_FLOP_PCT),
            "real": _summary_block(real_rows),
            "simulated": simulated_block,
            "caveats": caveats,
            "generated_at": _now_iso(),
        }

    # ------------------------------------------------------------------ bootstrap

    def preview_bootstrap_dates(self, *, weeks: Optional[int] = None) -> Dict[str, Any]:
        """Sampled bootstrap as-of dates from CACHED SPY bars only (no network).

        Backs the script's --dry-run: trading dates come straight out of the
        stock_daily cache, so an uncached SPY simply yields an empty list
        instead of triggering a fetch.
        """
        from src.services.discovery_service import BENCHMARK

        weeks = weeks or _env_int("SCORECARD_BOOTSTRAP_WEEKS", DEFAULT_BOOTSTRAP_WEEKS)
        today = date.today()
        rows = self.db.get_data_range(BENCHMARK, today - timedelta(weeks=max(1, weeks)), today)
        trading_dates = [row.date.isoformat() for row in rows if row.date is not None]
        before = self._earliest_real_discovery_as_of()
        dates = _sample_bootstrap_dates(trading_dates, weeks=weeks, before=before, today=today)
        return {
            "weeks": weeks,
            "earliest_real_as_of": before,
            "spy_cached_sessions": len(trading_dates),
            "dates": dates,
            "note": "Dates come from cached SPY bars only; an empty list usually means "
                    "SPY has no cached history yet (run a snapshot or the real bootstrap).",
        }

    def run_bootstrap(
        self,
        *,
        weeks: Optional[int] = None,
        deep_days: Optional[int] = None,
        symbol_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build simulated scorecard history by replaying discovery over past dates.

        One deep write-through fetch of today's discovery universe (+SPY), then
        for every ~5th SPY trading date covering `weeks` back (strictly before
        the earliest REAL discovery snapshot): slice every symbol's bars through
        _bars_until (the lookahead guard), rerun the discovery screens /
        percentiles / composite / scorer on the slice, and grade each qualified
        row forward in memory with evaluate_flag - the ONLY place post-date
        bars are touched, and only for outcome grading. Rows are upserted with
        simulated=True; DiscoveryDaily is NEVER written. sector_tailwind stays
        False everywhere (no historical rotation snapshots; survivorship bias
        applies too - both disclosed in the summary caveats).
        """
        from src.services.discovery_service import (
            BENCHMARK,
            DEFAULT_DISCOVERY_MIN_ADV,
            DEFAULT_DISCOVERY_SYMBOL_LIMIT,
            DISCOVERY_MIN_PRICE,
            SCORED_TOP_N,
            DiscoveryService,
            _attach_composite,
            _attach_percentiles,
            _benchmark_performance,
            _screen_row,
        )

        started = time.time()
        today = date.today()
        weeks = weeks or _env_int("SCORECARD_BOOTSTRAP_WEEKS", DEFAULT_BOOTSTRAP_WEEKS)
        deep_days = deep_days or _env_int("SCORECARD_BOOTSTRAP_DEEP_DAYS", DEFAULT_BOOTSTRAP_DEEP_DAYS)
        window_days = max(1, _env_int("SCORECARD_WINDOW_DAYS", DEFAULT_WINDOW_DAYS))
        hit_pct = _env_float("SCORECARD_HIT_PCT", DEFAULT_HIT_PCT)
        flop_pct = _env_float("SCORECARD_FLOP_PCT", DEFAULT_FLOP_PCT)
        min_adv = _env_float("RESEARCH_DISCOVERY_MIN_ADV", DEFAULT_DISCOVERY_MIN_ADV)
        warnings: List[str] = []

        discovery = DiscoveryService(free_data=self.free_data, db=self.db)
        watchlist = discovery._watchlist_symbols(warnings)
        limit = max(1, min(_env_int("RESEARCH_DISCOVERY_SYMBOL_LIMIT", DEFAULT_DISCOVERY_SYMBOL_LIMIT), 5000))
        symbols, universe_meta = discovery._discovery_universe(limit=limit, watchlist=watchlist)
        warnings.extend(universe_meta.get("warnings") or [])
        symbols = [symbol for symbol in symbols if symbol not in watchlist]
        if symbol_limit:
            symbols = symbols[: max(1, symbol_limit)]

        # Deep write-through fetch: bars land in stock_daily, so re-runs are nearly free.
        histories = self.free_data._fetch_histories(
            _unique_symbols([*symbols, BENCHMARK]),
            days=deep_days,
            timeout_seconds=BOOTSTRAP_FETCH_TIMEOUT_SECONDS,
        )
        cleaned = {
            symbol: _valid_price_volume_bars((histories.get(symbol) or {}).get("bars") or [])
            for symbol in histories
        }
        fetched_symbols = sum(1 for bars in cleaned.values() if bars)

        before = self._earliest_real_discovery_as_of()
        as_of_dates = _sample_bootstrap_dates(
            (bar["date"] for bar in cleaned.get(BENCHMARK) or []),
            weeks=weeks, before=before, today=today,
        )
        if not as_of_dates:
            warnings.append("No bootstrap dates to simulate (no SPY bars, or every "
                            "sampled date is at/after the earliest real snapshot).")

        # Forward closes for outcome grading come from the FULL fetched bars -
        # the only post-as-of data the bootstrap ever touches.
        forward_closes = {
            symbol: [(date.fromisoformat(bar["date"]), bar["close"]) for bar in bars]
            for symbol, bars in cleaned.items()
        }

        totals = {"open": 0, "hit": 0, "flop": 0, "expired": 0}
        flags = persisted = dates_simulated = skipped_dates = 0
        for as_of in as_of_dates:
            try:
                sliced = {
                    symbol: {
                        "bars": _bars_until(cleaned.get(symbol) or [], as_of),
                        "name": (histories.get(symbol) or {}).get("name"),
                        "market_cap": (histories.get(symbol) or {}).get("market_cap"),
                    }
                    for symbol in _unique_symbols([*symbols, BENCHMARK])
                }
                spy_perf_1m, spy_perf_3m = _benchmark_performance(sliced[BENCHMARK])
                rows = [
                    _screen_row(
                        symbol, sliced[symbol],
                        min_price=DISCOVERY_MIN_PRICE, min_avg_dollar_volume=min_adv,
                        spy_perf_1m=spy_perf_1m, spy_perf_3m=spy_perf_3m,
                    )
                    for symbol in symbols
                ]
                # Mirror build_snapshot: a symbol without a bar ON the sampled
                # session would be screened on stale data, so it sits out.
                qualified = [row for row in rows if row.get("qualified") and row.get("last_bar_date") == as_of]
                _attach_percentiles(qualified)
                for row in qualified:
                    # No historical rotation snapshots exist: the sector
                    # tailwind screen/bonus is disabled in simulation.
                    row["sector_symbol"] = None
                    row["sector_tailwind"] = False
                _attach_composite(qualified)
                qualified.sort(key=lambda row: -(row.get("composite_score") or 0.0))
                discovery._attach_candidate_scores(
                    qualified[:SCORED_TOP_N], sliced, benchmark_perf_3m=spy_perf_3m
                )

                as_of_date = date.fromisoformat(as_of)
                results: List[Dict[str, Any]] = []
                for row in qualified:
                    close = row.get("close")
                    if not close or float(close) <= 0:
                        continue
                    outcome = evaluate_flag(
                        float(close), as_of_date, forward_closes.get(row["symbol"]) or [],
                        window_days=window_days, hit_pct=hit_pct, flop_pct=flop_pct, today=today,
                    )
                    totals[outcome["status"]] += 1
                    results.append({
                        "as_of": as_of,
                        "symbol": row["symbol"],
                        "entry_close": float(close),
                        "composite_score": row.get("composite_score"),
                        "candidate_score": row.get("candidate_score"),
                        "screens": [key for key in SCREEN_KEYS if row.get(key)],
                        "simulated": True,
                        **outcome,
                        "window_days": window_days,
                        "hit_threshold_pct": hit_pct,
                        "flop_threshold_pct": flop_pct,
                    })
                flags += len(results)
                if results:
                    persisted += self.db.upsert_discovery_flag_outcomes(results)
                dates_simulated += 1
            except Exception as exc:
                logger.warning("Scorecard bootstrap skipped %s: %s", as_of, exc)
                skipped_dates += 1
        return {
            "as_of": today.isoformat(),
            "weeks": weeks,
            "deep_days": deep_days,
            "universe_size": len(symbols),
            "earliest_real_as_of": before,
            "dates_sampled": len(as_of_dates),
            "dates_simulated": dates_simulated,
            "skipped_dates": skipped_dates,
            "flags": flags,
            **totals,
            "persisted": persisted,
            "fetched_symbols": fetched_symbols,
            "duration_s": round(time.time() - started, 1),
            "warnings": warnings,
        }

    def _earliest_real_discovery_as_of(self) -> Optional[str]:
        """Earliest real DiscoveryDaily.as_of; None (all dates kept) on empty/error."""
        try:
            return self.db.get_earliest_discovery_as_of()
        except Exception as exc:
            logger.warning("Earliest discovery as_of read failed; bootstrap keeps all sampled dates: %s", exc)
            return None

    # ------------------------------------------------------------------ internals

    def _catch_up_fetch(self, *, window_days: int, fetch_limit: int, today: date) -> Dict[str, Any]:
        """Refetch bars for open flags whose cache went stale (capped, fail-open)."""
        info: Dict[str, Any] = {"open_symbols": 0, "stale": 0, "fetched": 0}
        try:
            symbols = self.db.get_open_discovery_flag_symbols(simulated=False)
            info["open_symbols"] = len(symbols)
            stale = [symbol for symbol in symbols if self._bars_stale(symbol, today=today)]
            info["stale"] = len(stale)
            fetch_symbols = stale[:fetch_limit]
            if fetch_symbols:
                self.free_data._fetch_histories(
                    fetch_symbols,
                    days=window_days + EXPIRY_SLACK_DAYS + 30,
                    timeout_seconds=CATCHUP_FETCH_TIMEOUT_SECONDS,
                )
                info["fetched"] = len(fetch_symbols)
        except Exception as exc:
            logger.warning("Scorecard catch-up fetch failed (evaluation continues on cached bars): %s", exc)
            info["error"] = str(exc)
        return info

    def _bars_stale(self, symbol: str, *, today: date) -> bool:
        """True when the stock_daily cache has no bar within STALE_BAR_AGE_DAYS."""
        try:
            rows = self.db.get_data_range(symbol, today - timedelta(days=STALE_BAR_AGE_DAYS), today)
            return not any(row.date is not None for row in rows)
        except Exception as exc:
            logger.debug("Scorecard staleness check failed for %s: %s", symbol, exc)
            return True  # unreadable cache -> try to refresh it

    def _closes_since(
        self,
        symbol: str,
        start: date,
        *,
        cache: Dict[str, Tuple[date, List[Tuple[date, float]]]],
        today: date,
    ) -> List[Tuple[date, float]]:
        """(date, close) bars after `start` from the stock_daily cache, memoized per symbol."""
        cached = cache.get(symbol)
        if cached is None or cached[0] > start:
            try:
                rows = self.db.get_data_range(symbol, start, today)
            except Exception as exc:
                logger.debug("Scorecard bar read failed for %s: %s", symbol, exc)
                rows = []
            cached = (start, [
                (row.date, float(row.close))
                for row in rows
                if row.date is not None and row.close
            ])
            cache[symbol] = cached
        return [(bar_date, close) for bar_date, close in cached[1] if bar_date > start]


# ---------------------------------------------------------------------- summary math


def _stats_block(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"open": 0, "hit": 0, "flop": 0, "expired": 0}
    for row in rows:
        status = row.get("status")
        if status in counts:
            counts[status] += 1
    decided = counts["hit"] + counts["flop"] + counts["expired"]
    return {
        "total": len(rows),
        **counts,
        "decided": decided,
        "batting_average": round(counts["hit"] / decided, 4) if decided else None,
    }


def _is_flagged(row: Dict[str, Any]) -> bool:
    """True when the system actually surfaced the row: passed >=1 screen or
    made the scored top-40 (candidate_score set). Everything else merely
    cleared the liquidity gate and belongs to the baseline control group only."""
    return bool(row.get("screens")) or row.get("candidate_score") is not None


def _population_stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"open": 0, "hit": 0, "flop": 0, "expired": 0}
    for row in rows:
        status = row.get("status")
        if status in counts:
            counts[status] += 1
    decided = counts["hit"] + counts["flop"] + counts["expired"]
    days_to_hit = [row["days_to_hit"] for row in rows if row.get("days_to_hit") is not None]
    # Decided rows only: open flags carry a partial max_gain_pct that would
    # drag the average around as their windows keep running.
    max_gains = [
        row["max_gain_pct"]
        for row in rows
        if row.get("status") in ("hit", "flop", "expired") and row.get("max_gain_pct") is not None
    ]
    return {
        "flags": len(rows),
        "hits": counts["hit"],
        "flops": counts["flop"],
        "expired": counts["expired"],
        "open": counts["open"],
        "decided": decided,
        "batting_average": round(counts["hit"] / decided, 4) if decided else None,
        "avg_days_to_hit": round(sum(days_to_hit) / len(days_to_hit), 2) if days_to_hit else None,
        "avg_max_gain_pct": round(sum(max_gains) / len(max_gains), 2) if max_gains else None,
    }


def _group_stats(rows: List[Dict[str, Any]], keys_for_row) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        for key in keys_for_row(row):
            groups.setdefault(key, []).append(row)
    return {key: _stats_block(items) for key, items in sorted(groups.items())}


def _score_band(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    for floor, label in SCORE_BANDS:
        if floor is None or score >= floor:
            return label
    return None


def _summary_block(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Population block: flagged (headline) vs baseline (control) sub-populations.

    Breakdowns (by_screen / by_score_band / by_month) cover flagged rows only;
    by_screen already is by construction (zero-screen rows contribute no keys).
    """
    flagged_rows = [row for row in rows if _is_flagged(row)]
    flagged = _population_stats(flagged_rows)
    baseline = _population_stats(rows)
    edge: Optional[float] = None
    if flagged["batting_average"] is not None and baseline["batting_average"] is not None:
        edge = round(flagged["batting_average"] - baseline["batting_average"], 4)
    return {
        "flagged": flagged,
        "baseline": baseline,
        "edge": edge,
        "by_screen": _group_stats(flagged_rows, lambda row: row.get("screens") or []),
        "by_score_band": _group_stats(
            flagged_rows,
            lambda row: [band] if (band := _score_band(row.get("composite_score"))) else [],
        ),
        "by_month": _group_stats(
            flagged_rows,
            lambda row: [str(row.get("as_of"))[:7]] if row.get("as_of") else [],
        ),
    }
