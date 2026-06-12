# -*- coding: utf-8 -*-
"""New-ideas discovery service.

Daily scan over a liquidity-gated slice of the US stock universe looking for
names that are NOT already on the watchlist: new/near 52-week highs, top-decile
relative strength vs SPY, unusual volume on up days, plus a sector-tailwind
bonus from the persisted rotation snapshot. Candidates are ranked by composite
percentile and the top ideas are run through the same GrindingLeaderScorer the
Signals page uses, so both surfaces speak the same checklist language. Two
independent watch flags — quiet accumulation and beaten-down reversal — ride
along on every row but never enter the momentum composite.

All bars come through FreeDataService._fetch_histories (fail-open, writes
through to the stock_daily cache), so re-runs on the same day are cheap.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from src.services.free_data_service import (
    BREADTH_ETF_PROXY_SYMBOLS,
    DEFAULT_BREADTH_MIN_PRICE,
    FreeDataService,
    _env_float,
    _env_int,
    _modal_bar_date,
    _now_iso,
    _stable_sample,
    _unique_symbols,
    _valid_price_volume_bars,
)
from src.services.research_scoring import GrindingLeaderScorer
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

BENCHMARK = "SPY"
DEFAULT_DISCOVERY_SYMBOL_LIMIT = 500
DEFAULT_DISCOVERY_MIN_ADV = 20_000_000.0
DISCOVERY_MIN_PRICE = DEFAULT_BREADTH_MIN_PRICE  # $5, mirrors the breadth gate
# First uncached scan of ~500 symbols takes ~9-10 minutes behind the yfinance
# limiter; the default RESEARCH_HISTORY_FETCH_TIMEOUT_SECONDS (120s) would
# silently truncate the universe, so discovery passes its own timeout.
DISCOVERY_FETCH_TIMEOUT_SECONDS = 900
HISTORY_DAYS = 320  # 252 bars for the 52-week high + 200 bars for the scorer
MIN_SCREEN_BARS = 64  # enough closes for the 3-month RS lookback
NEAR_HIGH_THRESHOLD = 0.98
UNUSUAL_VOLUME_RATIO = 2.0
RS_TOP_DECILE_PERCENTILE = 0.90
SECTOR_BONUS = 0.05
SECTOR_CORRELATION_MIN = 0.40
SCORED_TOP_N = 40

# Independent watch flags (NEVER part of the momentum composite — a deep-
# drawdown name scoring on a momentum composite would be incoherent).
# Quiet accumulation: avg volume over the last QUIET_ACCUM_WINDOW sessions vs
# the prior 4*window sessions (window 5 -> baseline vol[-25:-5]), while the
# close moved no more than QUIET_ACCUM_MAX_ABS_CHG_PCT over the window.
# Requires window*5 + 1 bars (26 by default). A single 2x day is noise
# (that's unusual_volume); a week of >=1.5x volume on a flat price is absorption.
QUIET_ACCUM_VOL_RATIO = 1.5
QUIET_ACCUM_WINDOW = 5
QUIET_ACCUM_MAX_ABS_CHG_PCT = 3.0
# Beaten-down reversal: 40%+ off the 52-week high (turnaround territory) with
# the earliest turn evidence — positive 1M RS vs SPY on above-average volume.
REVERSAL_MAX_PCT_OF_HIGH = 0.60
REVERSAL_MIN_RS_1M = 0.0
REVERSAL_MIN_VOLUME_RATIO = 1.25

# Down-day relative-strength screen (runs inside the discovery step, zero extra fetches).
DEFAULT_DOWN_DAY_THRESHOLD_PCT = -0.75
DOWN_DAY_BEAT_SPY_PP = 1.0
DOWN_DAY_STOCK_LOOKBACK_DAYS = 10  # calendar days into the stock_daily cache for two sessions


class DiscoveryService:
    """Build, rank, and persist the daily new-ideas discovery snapshot."""

    def __init__(
        self,
        *,
        free_data: Optional[FreeDataService] = None,
        db: Optional[DatabaseManager] = None,
        scorer: Optional[GrindingLeaderScorer] = None,
    ):
        self.free_data = free_data or FreeDataService()
        self.db = db or DatabaseManager.get_instance()
        self.scorer = scorer or GrindingLeaderScorer()

    def run_daily_snapshot(self) -> Dict[str, Any]:
        payload = self.build_snapshot()
        try:
            saved = self.db.save_discovery_snapshot(payload)
            payload["persisted"] = {"as_of": saved.get("as_of"), "id": saved.get("id")}
        except Exception as exc:
            logger.warning("Discovery snapshot persist failed: %s", exc)
            payload["persisted"] = {"status": "failed", "error": str(exc)}
        return payload

    def build_snapshot(self) -> Dict[str, Any]:
        limit = max(1, min(_env_int("RESEARCH_DISCOVERY_SYMBOL_LIMIT", DEFAULT_DISCOVERY_SYMBOL_LIMIT), 5000))
        min_adv = _env_float("RESEARCH_DISCOVERY_MIN_ADV", DEFAULT_DISCOVERY_MIN_ADV)
        warnings: List[str] = []

        watchlist = self._watchlist_symbols(warnings)
        symbols, universe_meta = self._discovery_universe(limit=limit, watchlist=watchlist)
        warnings.extend(universe_meta.get("warnings") or [])
        # Belt-and-braces: discovery is for NEW ideas, so watchlist names never rank.
        symbols = [symbol for symbol in symbols if symbol not in watchlist]

        sector_symbols, sector_risers, sector_warnings = self._sector_context()
        warnings.extend(sector_warnings)

        fetch_symbols = _unique_symbols([*symbols, BENCHMARK, *sector_symbols])
        histories = self.free_data._fetch_histories(
            fetch_symbols, days=HISTORY_DAYS, timeout_seconds=DISCOVERY_FETCH_TIMEOUT_SECONDS
        )

        spy_perf_1m, spy_perf_3m = _benchmark_performance(histories.get(BENCHMARK) or {})
        if spy_perf_3m is None:
            warnings.append(f"Benchmark {BENCHMARK} history unavailable; relative-strength screen skipped.")

        rows = [
            _screen_row(
                symbol,
                histories.get(symbol) or {},
                min_price=DISCOVERY_MIN_PRICE,
                min_avg_dollar_volume=min_adv,
                spy_perf_1m=spy_perf_1m,
                spy_perf_3m=spy_perf_3m,
            )
            for symbol in symbols
        ]
        missing = sum(1 for row in rows if row.get("status") != "ok")
        if missing:
            warnings.append(f"{missing} symbols returned no usable bars and were skipped.")

        qualified = [row for row in rows if row.get("qualified")]
        as_of = _modal_bar_date(qualified) or date.today().isoformat()
        stale = [row for row in qualified if row.get("last_bar_date") != as_of]
        if stale:
            warnings.append(
                f"{len(stale)} symbols excluded because their latest bar predates the snapshot date {as_of}."
            )
            qualified = [row for row in qualified if row.get("last_bar_date") == as_of]

        _attach_percentiles(qualified)
        self._attach_sector_tailwind(qualified, histories, sector_symbols, sector_risers)
        _attach_composite(qualified)
        qualified.sort(key=lambda row: -(row.get("composite_score") or 0.0))
        for index, row in enumerate(qualified):
            row["rank"] = index + 1
        # Score the union of the composite top-N and any new-screen flagged
        # row, so flagged names carry the checklist language even when their
        # momentum composite ranks them low.
        scored_rows = qualified[:SCORED_TOP_N] + [
            row for row in qualified[SCORED_TOP_N:]
            if row.get("quiet_accumulation") or row.get("beaten_down_reversal")
        ]
        self._attach_candidate_scores(scored_rows, histories, benchmark_perf_3m=spy_perf_3m)
        for row in qualified:
            row["reason"] = _reason(row)

        top3 = [row["symbol"] for row in qualified[:3]]
        summary = (
            f"Discovery as of {as_of}: {len(qualified)} of {len(symbols)} scanned stocks passed the "
            f"liquidity gate (price ≥ ${DISCOVERY_MIN_PRICE:g}, ADV ≥ ${min_adv:,.0f}). "
            f"Top ideas: {', '.join(top3) if top3 else 'none'}."
        )

        return {
            "as_of": as_of,
            "universe": universe_meta.get("label"),
            "universe_source": universe_meta.get("source"),
            "total_available": universe_meta.get("total_available"),
            "universe_size": len(symbols),
            "qualified_size": len(qualified),
            "excluded_stale_count": len(stale),
            "min_price": DISCOVERY_MIN_PRICE,
            "min_avg_dollar_volume": min_adv,
            "constituents": qualified,
            "warnings": warnings,
            "summary": summary,
            "generated_at": _now_iso(),
        }

    # ------------------------------------------------------------------ inputs

    def _watchlist_symbols(self, warnings: List[str]) -> Set[str]:
        try:
            from src.services.watchlist_service import WatchlistService

            return set(WatchlistService().get_symbols())
        except Exception as exc:
            warnings.append(f"Watchlist read failed; watchlist exclusion skipped: {exc}")
            return set()

    def _discovery_universe(self, *, limit: int, watchlist: Set[str]) -> Tuple[List[str], Dict[str, Any]]:
        """Same selection logic as FreeDataService._breadth_universe, with discovery's
        priority order: yesterday's qualified names, then default liquid names, then a
        stable sample of the remaining Nasdaq-listed stock pool. Watchlist and ETFs
        are excluded."""
        warnings: List[str] = []
        prev_qualified: List[str] = []
        try:
            previous = self.db.get_latest_discovery_snapshot()
            prev_qualified = [
                str(row.get("symbol") or "")
                for row in (previous or {}).get("constituents") or []
                if row.get("symbol")
            ]
        except Exception as exc:
            warnings.append(f"Previous discovery snapshot read failed: {exc}")

        priority = _unique_symbols([
            *prev_qualified,
            *self.free_data.market_data.get_default_us_universe(limit=100),
        ])
        try:
            rows, _ = self.free_data._fetch_nasdaq_directory()
            active = [row for row in rows if row.get("test_issue") != "Y" and row.get("symbol")]
            pool = [row["symbol"] for row in active if row.get("is_etf") is False]
            pool_set = set(pool)
            selected = [symbol for symbol in priority if symbol in pool_set and symbol not in watchlist]
            seen = set(selected)
            remaining = [symbol for symbol in pool if symbol not in seen and symbol not in watchlist]
            selected.extend(_stable_sample(remaining, max(0, limit - len(selected))))
            return _unique_symbols(selected)[:limit], {
                "label": "US listed stocks (ex-watchlist, ex-ETF)",
                "source": "Nasdaq Trader Symbol Directory plus prior qualified/default liquid names",
                "total_available": len(pool),
                "warnings": warnings,
            }
        except Exception as exc:
            warnings.append(f"Nasdaq universe fetch failed; discovery used the priority list only: {exc}")
            known_etfs = set(BREADTH_ETF_PROXY_SYMBOLS)
            symbols = [s for s in priority if s not in watchlist and s not in known_etfs][:limit]
            return symbols, {
                "label": "Fallback discovery universe (prior qualified + defaults)",
                "source": "previous snapshot / RESEARCH_US_SYMBOLS / defaults",
                "total_available": len(symbols),
                "warnings": warnings,
            }

    def _sector_context(self) -> Tuple[List[str], Set[str], List[str]]:
        """Sector ETF symbols and the 1M rank-change risers from the latest rotation snapshot."""
        try:
            rotation = self.db.get_latest_sector_rotation_snapshot()
        except Exception as exc:
            return [], set(), [f"Rotation snapshot read failed; sector tailwind bonus skipped: {exc}"]
        if not rotation:
            return [], set(), ["No rotation snapshot persisted yet; sector tailwind bonus skipped."]
        sectors = [
            row for row in rotation.get("constituents") or []
            if row.get("group") == "sectors" and row.get("symbol")
        ]
        symbols = [str(row["symbol"]) for row in sectors]
        risers = {
            str(row["symbol"])
            for row in sectors
            if ((row.get("rank_change") or {}).get("1M") or 0) > 0
        }
        return symbols, risers, []

    # ------------------------------------------------------------------ enrich

    def _attach_sector_tailwind(
        self,
        qualified: List[Dict[str, Any]],
        histories: Dict[str, Dict[str, Any]],
        sector_symbols: List[str],
        sector_risers: Set[str],
    ) -> None:
        """Map each stock to its best-correlated sector ETF; bonus when that sector is rising."""
        sector_returns = {
            symbol: _daily_returns_by_date((histories.get(symbol) or {}).get("bars") or [])
            for symbol in sector_symbols
        }
        sector_returns = {symbol: returns for symbol, returns in sector_returns.items() if returns}
        for row in qualified:
            row["sector_symbol"] = None
            row["sector_tailwind"] = False
            if not sector_returns:
                continue
            stock_returns = _daily_returns_by_date((histories.get(row["symbol"]) or {}).get("bars") or [])
            best_symbol = None
            best_corr: Optional[float] = None
            for symbol, returns in sector_returns.items():
                corr = _correlation(stock_returns, returns)
                if corr is None:
                    continue
                if best_corr is None or corr > best_corr:
                    best_corr = corr
                    best_symbol = symbol
            if best_symbol is not None and best_corr is not None and best_corr >= SECTOR_CORRELATION_MIN:
                row["sector_symbol"] = best_symbol
                row["sector_tailwind"] = best_symbol in sector_risers

    def _attach_candidate_scores(
        self,
        rows: List[Dict[str, Any]],
        histories: Dict[str, Dict[str, Any]],
        *,
        benchmark_perf_3m: Optional[float],
    ) -> None:
        """Run GrindingLeaderScorer on the top composite names so discovery speaks
        the same candidate_score/checklist language as the Signals page."""
        for row in rows:
            payload = histories.get(row["symbol"]) or {}
            try:
                score = self.scorer.score(
                    symbol=row["symbol"],
                    asset_type="stock",
                    name=str(payload.get("name") or row["symbol"]),
                    bars=payload.get("bars") or [],
                    market_cap=payload.get("market_cap"),
                    benchmark_perf_3m=benchmark_perf_3m,
                )
            except Exception as exc:
                logger.debug("Discovery scorer failed for %s: %s", row["symbol"], exc)
                continue
            row["candidate_score"] = score.candidate_score
            row["checklist_status"] = score.checklist_status
            row["entry_zone"] = score.entry_zone

    # ------------------------------------------------------------ down-day screen

    def run_down_day_screen(self, *, discovery_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build + persist the down-day relative-strength snapshot (no extra fetches)."""
        payload = self.build_down_day_screen(discovery_payload=discovery_payload)
        try:
            saved = self.db.save_down_day_rs_snapshot(payload)
            payload["persisted"] = {"as_of": saved.get("as_of"), "id": saved.get("id")}
        except Exception as exc:
            logger.warning("Down-day snapshot persist failed: %s", exc)
            payload["persisted"] = {"status": "failed", "error": str(exc)}
        return payload

    def build_down_day_screen(self, *, discovery_payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """When SPY's 1D return is at/below the threshold, list what held up.

        Everything comes from data already in hand: cached SPY bars, the latest
        persisted rotation snapshot's 1D sector returns, and the stock_daily bar
        cache for watchlist symbols plus today's discovery qualifiers. "Holding
        up" = green on the day OR beat SPY by >= 1 percentage point.
        """
        threshold = _env_float("DOWN_DAY_THRESHOLD_PCT", DEFAULT_DOWN_DAY_THRESHOLD_PCT)
        warnings: List[str] = []

        spy_bars: List[Dict[str, Any]] = []
        try:
            spy_payload = self.free_data.market_data.get_us_equity_history(BENCHMARK)
            spy_bars = _valid_price_volume_bars((spy_payload or {}).get("bars") or [])
        except Exception as exc:
            warnings.append(f"{BENCHMARK} history unavailable; down-day screen skipped: {exc}")
        if len(spy_bars) < 2:
            if not warnings:
                warnings.append(f"{BENCHMARK} history unavailable; down-day screen skipped.")
            return _down_day_payload(
                as_of=date.today().isoformat(), spy_return=None, threshold=threshold,
                triggered=False, last_down_day=None, sectors=[], stocks=[],
                scanned=0, missing=0, warnings=warnings,
            )

        as_of = spy_bars[-1]["date"]
        spy_return = (spy_bars[-1]["close"] / spy_bars[-2]["close"] - 1.0) * 100

        # Check for bar gap: if the prior bar is >4 calendar days before, warn.
        try:
            last_bar_date = date.fromisoformat(spy_bars[-1]["date"])
            prior_bar_date = date.fromisoformat(spy_bars[-2]["date"])
            gap = (last_bar_date - prior_bar_date).days
            if gap > 4:
                warnings.append(
                    f"SPY prior bar is {gap} calendar days before {as_of}; "
                    f"down-day return may span multiple sessions."
                )
        except (ValueError, KeyError):
            # Fail-open: if date parsing fails, don't warn
            pass

        triggered = spy_return <= threshold
        last_down_day = _last_down_day(spy_bars, threshold)

        sectors: List[Dict[str, Any]] = []
        stocks: List[Dict[str, Any]] = []
        scanned = 0
        missing = 0
        if triggered:
            sectors = self._sectors_holding_up(as_of=as_of, spy_return=spy_return, warnings=warnings)
            stocks, scanned, missing = self._stocks_holding_up(
                as_of=as_of, spy_return=spy_return,
                discovery_payload=discovery_payload, warnings=warnings,
            )
        return _down_day_payload(
            as_of=as_of, spy_return=spy_return, threshold=threshold,
            triggered=triggered, last_down_day=last_down_day, sectors=sectors,
            stocks=stocks, scanned=scanned, missing=missing, warnings=warnings,
        )

    def _sectors_holding_up(self, *, as_of: str, spy_return: float, warnings: List[str]) -> List[Dict[str, Any]]:
        """Sector ETFs holding up, read straight off the persisted rotation snapshot (no refetch)."""
        try:
            rotation = self.db.get_latest_sector_rotation_snapshot()
        except Exception as exc:
            warnings.append(f"Rotation snapshot read failed; sector hold-up list skipped: {exc}")
            return []
        if not rotation:
            warnings.append("No rotation snapshot persisted yet; sector hold-up list skipped.")
            return []
        if rotation.get("as_of") != as_of:
            warnings.append(
                f"Rotation snapshot as_of {rotation.get('as_of')} predates the down-day session {as_of}; "
                "sector hold-up list skipped."
            )
            return []
        rows: List[Dict[str, Any]] = []
        for row in rotation.get("constituents") or []:
            if row.get("group") != "sectors" or not row.get("symbol"):
                continue
            ret = (row.get("returns") or {}).get("1D")
            if ret is None or not _holding_up(float(ret), spy_return):
                continue
            rows.append({
                "symbol": str(row["symbol"]),
                "label": row.get("label"),
                "return_1d_pct": round(float(ret), 2),
            })
        rows.sort(key=lambda item: -item["return_1d_pct"])
        return rows

    def _stocks_holding_up(
        self,
        *,
        as_of: str,
        spy_return: float,
        discovery_payload: Optional[Dict[str, Any]],
        warnings: List[str],
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """Watchlist + today's discovery qualifiers, bars from the stock_daily cache only."""
        watchlist = self._watchlist_symbols(warnings)
        qualifiers: List[str] = []
        payload = discovery_payload
        if payload is None:
            try:
                payload = self.db.get_latest_discovery_snapshot()
            except Exception as exc:
                warnings.append(f"Discovery snapshot read failed; qualifiers skipped in the down-day screen: {exc}")
                payload = None
        if payload:
            if payload.get("as_of") == as_of:
                qualifiers = [
                    str(row.get("symbol")) for row in payload.get("constituents") or [] if row.get("symbol")
                ]
            else:
                warnings.append(
                    f"Discovery snapshot as_of {payload.get('as_of')} predates the down-day session {as_of}; "
                    "qualifiers skipped."
                )
        symbols = [s for s in _unique_symbols([*sorted(watchlist), *qualifiers]) if s != BENCHMARK]

        as_of_date = date.fromisoformat(as_of)
        start = as_of_date - timedelta(days=DOWN_DAY_STOCK_LOOKBACK_DAYS)
        rows: List[Dict[str, Any]] = []
        missing = 0
        for symbol in symbols:
            try:
                daily = self.db.get_data_range(symbol, start, as_of_date)
            except Exception as exc:
                logger.debug("Down-day bar cache read failed for %s: %s", symbol, exc)
                missing += 1
                continue
            closes = [(row.date, row.close) for row in daily if row.date is not None and row.close]
            if len(closes) < 2 or closes[-1][0].isoformat() != as_of or closes[-2][1] <= 0:
                missing += 1
                continue
            ret = (closes[-1][1] / closes[-2][1] - 1.0) * 100
            if not _holding_up(ret, spy_return):
                continue
            rs = ret - spy_return
            rows.append({
                "symbol": symbol,
                "source": "watchlist" if symbol in watchlist else "discovery",
                "return_1d_pct": round(ret, 2),
                "rs_vs_spy_pp": round(rs, 2),
                "reason": "green on the day" if ret >= 0 else f"beat SPY by {rs:.1f}pp",
            })
        rows.sort(key=lambda item: -item["rs_vs_spy_pp"])
        if missing:
            warnings.append(f"{missing} symbols had no usable cached bars for {as_of} and were skipped.")
        return rows, len(symbols), missing


# ---------------------------------------------------------------------- screens


def _screen_row(
    symbol: str,
    payload: Dict[str, Any],
    *,
    min_price: float,
    min_avg_dollar_volume: float,
    spy_perf_1m: Optional[float],
    spy_perf_3m: Optional[float],
) -> Dict[str, Any]:
    """Pure per-symbol screen computation on cached bars (no network)."""
    bars = _valid_price_volume_bars(payload.get("bars") or [])
    closes = [bar["close"] for bar in bars]
    volumes = [bar["volume"] for bar in bars]
    close = closes[-1] if closes else None
    dollar_volumes = [bar["close"] * bar["volume"] for bar in bars[-21:-1]]
    avg_dollar_volume_20d = (sum(dollar_volumes) / len(dollar_volumes)) if dollar_volumes else None
    qualified = bool(
        close is not None
        and close >= min_price
        and len(closes) >= MIN_SCREEN_BARS
        and len(bars) >= 21
        and avg_dollar_volume_20d is not None
        and avg_dollar_volume_20d >= min_avg_dollar_volume
    )

    high_window = closes[-252:] if closes else []
    high_52w = max(high_window) if high_window else None
    pct_of_52w_high = (close / high_52w) if close is not None and high_52w else None
    near_52w_high = bool(pct_of_52w_high is not None and pct_of_52w_high >= NEAR_HIGH_THRESHOLD)

    perf_1m = _perf(closes, 21)
    perf_3m = _perf(closes, 63)
    rs_1m_pct = perf_1m - spy_perf_1m if perf_1m is not None and spy_perf_1m is not None else None
    rs_3m_pct = perf_3m - spy_perf_3m if perf_3m is not None and spy_perf_3m is not None else None

    avg_volume_20 = (sum(volumes[-21:-1]) / 20) if len(volumes) >= 21 else None
    latest_volume = volumes[-1] if volumes else None
    volume_ratio = (
        latest_volume / avg_volume_20
        if latest_volume is not None and avg_volume_20
        else None
    )
    up_day = bool(len(closes) >= 2 and closes[-1] > closes[-2])
    unusual_volume = bool(up_day and volume_ratio is not None and volume_ratio >= UNUSUAL_VOLUME_RATIO)

    # Quiet accumulation (independent watch flag): sustained volume lift on a
    # flat price. With the default window of 5 the baseline is vol[-25:-5] and
    # 26 bars are required (window*5 for the two volume averages, plus the
    # close one session before the window for the flat-price check).
    qa_window = max(1, _env_int("QUIET_ACCUM_WINDOW", QUIET_ACCUM_WINDOW))
    qa_span = qa_window * 5
    quiet_accum_volume_ratio: Optional[float] = None
    quiet_price_chg_pct: Optional[float] = None
    if len(bars) >= qa_span + 1:
        baseline_volumes = volumes[-qa_span:-qa_window]
        baseline_avg = sum(baseline_volumes) / len(baseline_volumes)
        if baseline_avg > 0:
            quiet_accum_volume_ratio = (sum(volumes[-qa_window:]) / qa_window) / baseline_avg
        window_ago_close = closes[-qa_window - 1]
        if window_ago_close:
            quiet_price_chg_pct = abs(closes[-1] / window_ago_close - 1.0) * 100
    quiet_accumulation = bool(
        quiet_accum_volume_ratio is not None
        and quiet_accum_volume_ratio >= _env_float("QUIET_ACCUM_VOL_RATIO", QUIET_ACCUM_VOL_RATIO)
        and quiet_price_chg_pct is not None
        and quiet_price_chg_pct <= _env_float("QUIET_ACCUM_MAX_ABS_CHG_PCT", QUIET_ACCUM_MAX_ABS_CHG_PCT)
    )

    # Beaten-down reversal (independent watch flag): deep drawdown with the
    # earliest turn evidence — positive 1M RS vs SPY on above-average volume.
    beaten_down_reversal = bool(
        pct_of_52w_high is not None
        and pct_of_52w_high <= _env_float("REVERSAL_MAX_PCT_OF_HIGH", REVERSAL_MAX_PCT_OF_HIGH)
        and rs_1m_pct is not None
        and rs_1m_pct > _env_float("REVERSAL_MIN_RS_1M", REVERSAL_MIN_RS_1M)
        and volume_ratio is not None
        and volume_ratio >= _env_float("REVERSAL_MIN_VOLUME_RATIO", REVERSAL_MIN_VOLUME_RATIO)
    )

    return {
        "symbol": symbol,
        "name": payload.get("name") or symbol,
        "status": "ok" if bars else "missing",
        "last_bar_date": bars[-1]["date"] if bars else None,
        "close": round(close, 4) if close is not None else None,
        "avg_dollar_volume_20d": round(avg_dollar_volume_20d, 0) if avg_dollar_volume_20d is not None else None,
        "qualified": qualified,
        "pct_of_52w_high": round(pct_of_52w_high, 4) if pct_of_52w_high is not None else None,
        "near_52w_high": near_52w_high,
        "perf_1m_pct": round(perf_1m, 2) if perf_1m is not None else None,
        "perf_3m_pct": round(perf_3m, 2) if perf_3m is not None else None,
        "rs_1m_pct": round(rs_1m_pct, 2) if rs_1m_pct is not None else None,
        "rs_3m_pct": round(rs_3m_pct, 2) if rs_3m_pct is not None else None,
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "up_day": up_day,
        "unusual_volume": unusual_volume,
        "quiet_accum_volume_ratio": (
            round(quiet_accum_volume_ratio, 2) if quiet_accum_volume_ratio is not None else None
        ),
        "quiet_accumulation": quiet_accumulation,
        "beaten_down_reversal": beaten_down_reversal,
    }


def _attach_percentiles(qualified: List[Dict[str, Any]]) -> None:
    """Cross-sectional percentiles (0..1) feeding the composite + the RS-decile flag."""
    _percentile_into(qualified, value_key="pct_of_52w_high", target_key="high_percentile")
    rs1 = _percentiles(qualified, "rs_1m_pct")
    rs3 = _percentiles(qualified, "rs_3m_pct")
    for index, row in enumerate(qualified):
        parts = [p for p in (rs1[index], rs3[index]) if p is not None]
        row["rs_percentile"] = round(sum(parts) / len(parts), 4) if parts else None
        row["rs_top_decile"] = bool(
            row["rs_percentile"] is not None and row["rs_percentile"] >= RS_TOP_DECILE_PERCENTILE
        )
        # Climactic volume only counts in the composite when it confirms an up day.
        ratio = row.get("volume_ratio")
        row["_volume_signal"] = ratio if (row.get("up_day") and ratio is not None) else 0.0
    _percentile_into(qualified, value_key="_volume_signal", target_key="volume_percentile")
    for row in qualified:
        row.pop("_volume_signal", None)


def _attach_composite(qualified: List[Dict[str, Any]]) -> None:
    for row in qualified:
        parts = [
            p for p in (row.get("high_percentile"), row.get("rs_percentile"), row.get("volume_percentile"))
            if p is not None
        ]
        if not parts:
            row["composite_score"] = None
            continue
        composite = sum(parts) / len(parts)
        if row.get("sector_tailwind"):
            composite += SECTOR_BONUS
        row["composite_score"] = round(min(1.0, composite) * 100, 2)


def _percentiles(rows: List[Dict[str, Any]], value_key: str) -> List[Optional[float]]:
    """Return fractional-rank percentiles [0, 1]; tied values get position-based percentiles."""
    scored = [(index, row.get(value_key)) for index, row in enumerate(rows)]
    valid = [(index, value) for index, value in scored if value is not None]
    result: List[Optional[float]] = [None] * len(rows)
    if not valid:
        return result
    valid.sort(key=lambda item: item[1])
    n = len(valid)
    for position, (index, _) in enumerate(valid):
        result[index] = position / (n - 1) if n > 1 else 1.0
    return result


def _percentile_into(rows: List[Dict[str, Any]], *, value_key: str, target_key: str) -> None:
    for row, percentile in zip(rows, _percentiles(rows, value_key)):
        row[target_key] = round(percentile, 4) if percentile is not None else None


def _reason(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    if row.get("near_52w_high"):
        pct = row.get("pct_of_52w_high")
        parts.append("at/near its 52-week high" + (f" ({pct * 100:.0f}%)" if pct is not None else ""))
    if row.get("rs_top_decile"):
        parts.append("top-decile relative strength vs SPY (1M/3M)")
    if row.get("unusual_volume"):
        ratio = row.get("volume_ratio")
        parts.append((f"{ratio:.1f}x" if ratio is not None else "unusual") + " average volume on an up day")
    if row.get("quiet_accumulation"):
        ratio = row.get("quiet_accum_volume_ratio")
        parts.append(
            "quiet accumulation ("
            + (f"{ratio:.1f}x" if ratio is not None else "elevated")
            + " average volume this week on a flat price)"
        )
    if row.get("beaten_down_reversal"):
        pct = row.get("pct_of_52w_high")
        parts.append(
            "beaten-down reversal setup"
            + (f" ({pct * 100:.0f}% of its 52-week high" if pct is not None else " (deep drawdown")
            + ", RS turning positive on volume)"
        )
    if row.get("sector_tailwind"):
        parts.append(f"rising sector tailwind ({row.get('sector_symbol')})")
    return "; ".join(parts) if parts else "ranked by composite trend/RS/volume strength"


# ----------------------------------------------------------- down-day helpers


def _holding_up(return_pct: float, spy_return_pct: float) -> bool:
    """Green on the day, or beat SPY by at least DOWN_DAY_BEAT_SPY_PP points."""
    return return_pct >= 0 or (return_pct - spy_return_pct) >= DOWN_DAY_BEAT_SPY_PP


def _last_down_day(bars: List[Dict[str, Any]], threshold: float) -> Optional[str]:
    """Most recent session (newest first) whose 1D return is at/below the threshold."""
    for index in range(len(bars) - 1, 0, -1):
        prior = bars[index - 1]["close"]
        if not prior or prior <= 0:
            continue
        if (bars[index]["close"] / prior - 1.0) * 100 <= threshold:
            return bars[index]["date"]
    return None


def _down_day_payload(
    *,
    as_of: str,
    spy_return: Optional[float],
    threshold: float,
    triggered: bool,
    last_down_day: Optional[str],
    sectors: List[Dict[str, Any]],
    stocks: List[Dict[str, Any]],
    scanned: int,
    missing: int,
    warnings: List[str],
) -> Dict[str, Any]:
    spy_text = f"{spy_return:+.2f}%" if spy_return is not None else "n/a"
    if triggered:
        top = ", ".join(row["symbol"] for row in stocks[:3]) or "none"
        summary = (
            f"Down day: SPY {spy_text} on {as_of} (threshold {threshold:g}%). "
            f"{len(stocks)} of {scanned} tracked stocks and {len(sectors)} sectors held up. Top: {top}."
        )
    elif spy_return is None:
        summary = f"Down-day screen skipped on {as_of}: no {BENCHMARK} bars available."
    else:
        summary = f"No down day: SPY {spy_text} on {as_of} (threshold {threshold:g}%)."
    return {
        "as_of": as_of,
        "benchmark": BENCHMARK,
        "spy_return_pct": round(spy_return, 2) if spy_return is not None else None,
        "threshold_pct": threshold,
        "triggered": triggered,
        "last_down_day": last_down_day,
        "sectors_holding_up": sectors,
        "stocks_holding_up": stocks,
        "stocks_scanned": scanned,
        "stocks_missing": missing,
        "warnings": warnings,
        "summary": summary,
        "generated_at": _now_iso(),
    }


# ---------------------------------------------------------------------- helpers


def _perf(closes: List[float], lookback: int) -> Optional[float]:
    if len(closes) <= lookback:
        return None
    prior = closes[-lookback - 1]
    if not prior:
        return None
    return (closes[-1] - prior) / prior * 100


def _benchmark_performance(payload: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    closes = [bar["close"] for bar in _valid_price_volume_bars(payload.get("bars") or [])]
    return _perf(closes, 21), _perf(closes, 63)


def _daily_returns_by_date(bars: List[Dict[str, Any]], *, window: int = 64) -> Dict[str, float]:
    cleaned = _valid_price_volume_bars(bars)[-window:]
    returns: Dict[str, float] = {}
    previous: Optional[float] = None
    for bar in cleaned:
        if previous and previous > 0:
            returns[bar["date"]] = bar["close"] / previous - 1.0
        if bar["close"] and bar["close"] > 0:
            previous = bar["close"]
    return returns


def _correlation(a: Dict[str, float], b: Dict[str, float], *, min_overlap: int = 20) -> Optional[float]:
    common = sorted(set(a) & set(b))
    if len(common) < min_overlap:
        return None
    xs = [a[key] for key in common]
    ys = [b[key] for key in common]
    n = len(common)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x ** 0.5 * var_y ** 0.5)
