# -*- coding: utf-8 -*-
"""Multi-timeframe sector rotation engine.

Ranks the liquid ETF-proxy universe by relative strength vs SPY across daily,
weekly, monthly, quarterly, semiannual, and annual horizons; classifies each ETF
into an RRG-style quadrant (Leading / Weakening / Lagging / Improving); and diffs
ranks against the previous persisted snapshot so "newly hot / newly cold" is a
first-class signal. All bars come from the local daily-bar cache.
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.services.research_market_data import ResearchMarketDataService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

BENCHMARK = "SPY"

# (symbol, label) per group. A unit test asserts these stay in sync with
# BREADTH_ETF_PROXY_SYMBOLS in free_data_service so both jobs share one universe.
ROTATION_GROUPS: Dict[str, List[Tuple[str, str]]] = {
    "index_style": [
        ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("IWM", "Small caps"), ("DIA", "Dow 30"),
        ("RSP", "Equal-weight S&P"), ("MDY", "Mid caps"), ("IWF", "Growth"), ("IWD", "Value"),
        ("MTUM", "Momentum"), ("QUAL", "Quality"),
    ],
    "sectors": [
        ("XLK", "Technology"), ("XLC", "Communications"), ("XLY", "Discretionary"),
        ("XLF", "Financials"), ("XLI", "Industrials"), ("XLE", "Energy"), ("XLV", "Health care"),
        ("XLP", "Staples"), ("XLU", "Utilities"), ("XLB", "Materials"), ("XLRE", "Real estate"),
    ],
    "industries": [
        ("SMH", "Semis"), ("SOXX", "Semis (SOXX)"), ("XBI", "Biotech"), ("IBB", "Biotech (IBB)"),
        ("ITB", "Homebuilders"), ("XHB", "Housing"), ("KRE", "Regional banks"), ("KBE", "Banks"),
        ("IGV", "Software"), ("ARKK", "Speculative growth"), ("XME", "Metals & mining"),
        ("XOP", "Oil & gas E&P"), ("XRT", "Retail"), ("IYT", "Transports"), ("JETS", "Airlines"),
        ("GDX", "Gold miners"), ("TAN", "Solar"), ("URA", "Uranium"),
    ],
    "bonds_intl_cmd": [
        ("HYG", "High yield"), ("LQD", "IG credit"), ("TLT", "20Y+ Treasuries"),
        ("IEF", "7-10Y Treasuries"), ("EEM", "Emerging mkts"), ("EFA", "Developed intl"),
        ("FXI", "China"), ("EWJ", "Japan"), ("EWZ", "Brazil"),
        ("GLD", "Gold"), ("SLV", "Silver"), ("USO", "Oil"),
    ],
}

# 1D is display-only (daily hot/not); it is excluded from the composite because
# single-day moves are noise for rotation ranking.
HORIZONS: Dict[str, int] = {"1D": 1, "1W": 5, "1M": 21, "3M": 63, "6M": 126, "12M": 252}
COMPOSITE_WEIGHTS: Dict[str, float] = {"1W": 0.10, "1M": 0.30, "3M": 0.30, "6M": 0.20, "12M": 0.10}

RS_TREND_WINDOW = 63   # RS-ratio baseline (≈ one quarter)
RS_MOMENTUM_LOOKBACK = 21  # RS-momentum lookback (≈ one month)


def _symbol_meta() -> Dict[str, Dict[str, str]]:
    meta: Dict[str, Dict[str, str]] = {}
    for group, members in ROTATION_GROUPS.items():
        for symbol, label in members:
            meta[symbol] = {"label": label, "group": group}
    return meta


def horizon_return(closes: List[float], lookback: int) -> Optional[float]:
    """Simple return over `lookback` bars; None when history is too short."""
    if len(closes) <= lookback or not closes[-1 - lookback]:
        return None
    return closes[-1] / closes[-1 - lookback] - 1.0


def relative_return(ret: Optional[float], benchmark_ret: Optional[float]) -> Optional[float]:
    if ret is None or benchmark_ret is None:
        return None
    return (1.0 + ret) / (1.0 + benchmark_ret) - 1.0


def rrg_quadrant(rs_line: List[float]) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """RRG-style classification from a date-aligned ETF/benchmark ratio series.

    rs_ratio    = 100 * current RS / SMA63(RS)         (above 100 = relatively strong)
    rs_momentum = 100 * rs_ratio_now / rs_ratio_21d_ago (above 100 = strengthening)
    """
    needed = RS_TREND_WINDOW + RS_MOMENTUM_LOOKBACK + 1
    if len(rs_line) < needed:
        return None, None, None

    def _ratio(series: List[float]) -> Optional[float]:
        window = series[-RS_TREND_WINDOW:]
        sma = sum(window) / len(window)
        return 100.0 * series[-1] / sma if sma else None

    ratio_now = _ratio(rs_line)
    ratio_then = _ratio(rs_line[:-RS_MOMENTUM_LOOKBACK])
    if ratio_now is None or ratio_then is None or not ratio_then:
        return None, None, None
    momentum = 100.0 * ratio_now / ratio_then
    # Momentum exactly at 100 (e.g. a constant-rate decliner) is a continuation,
    # not a turn: classification falls back to the ratio side of the axis.
    # Constant-rate series land within float noise of 100, so compare with a
    # tolerance instead of exact equality.
    eps = 1e-9
    if ratio_now >= 100:
        quadrant = "leading" if momentum >= 100 - eps else "weakening"
    else:
        quadrant = "improving" if momentum > 100 + eps else "lagging"
    return round(ratio_now, 2), round(momentum, 2), quadrant


def composite_score(percentiles: Dict[str, Optional[float]]) -> Optional[float]:
    """Weighted mean of per-horizon RS percentiles (0..1), renormalized over
    whatever horizons are available."""
    total = 0.0
    weight_sum = 0.0
    for horizon, weight in COMPOSITE_WEIGHTS.items():
        value = percentiles.get(horizon)
        if value is None:
            continue
        total += value * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None
    return round(total / weight_sum * 100, 2)


class RotationService:
    def __init__(
        self,
        *,
        market_data: Optional[ResearchMarketDataService] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self.market_data = market_data or ResearchMarketDataService()
        self.db = db or DatabaseManager.get_instance()

    def run_daily_snapshot(self) -> Dict[str, Any]:
        payload = self.build_snapshot()
        try:
            saved = self.db.save_sector_rotation_snapshot(payload)
            payload["persisted"] = {"as_of": saved.get("as_of"), "id": saved.get("id")}
        except Exception as exc:
            logger.warning("Rotation snapshot persist failed: %s", exc)
            payload["persisted"] = {"status": "failed", "error": str(exc)}
        return payload

    def build_snapshot(self) -> Dict[str, Any]:
        meta = _symbol_meta()
        symbols = list(meta.keys())
        days = _env_int("ROTATION_HISTORY_DAYS", 320)
        histories = self._fetch_histories(symbols, days=days)
        warnings: List[str] = []

        bench_bars = _dated_closes((histories.get(BENCHMARK) or {}).get("bars") or [])
        if len(bench_bars) < 60:
            warnings.append(f"Benchmark {BENCHMARK} has insufficient history; rotation snapshot skipped.")
            return {
                "as_of": date.today().isoformat(),
                "benchmark": BENCHMARK,
                "universe": "us_etf_proxy",
                "symbols_total": len(symbols),
                "symbols_ranked": 0,
                "constituents": [],
                "leaders": {},
                "warnings": warnings,
                "summary": "Rotation snapshot unavailable: benchmark bars missing.",
                "generated_at": _now_iso(),
            }
        bench_closes = [bar[1] for bar in bench_bars]
        bench_by_date = dict(bench_bars)
        bench_returns = {name: horizon_return(bench_closes, lb) for name, lb in HORIZONS.items()}
        as_of = bench_bars[-1][0]

        rows: List[Dict[str, Any]] = []
        for symbol in symbols:
            payload = histories.get(symbol) or {}
            bars = _dated_closes(payload.get("bars") or [])
            if payload.get("error") or len(bars) < 30:
                warnings.append(f"{symbol}: {payload.get('error') or 'insufficient bars'}")
                continue
            closes = [bar[1] for bar in bars]
            returns = {name: horizon_return(closes, lb) for name, lb in HORIZONS.items()}
            rel = {
                name: relative_return(returns[name], bench_returns[name])
                for name in HORIZONS
            }
            rs_line = [
                close / bench_by_date[when]
                for when, close in bars
                if when in bench_by_date and bench_by_date[when]
            ]
            rs_ratio, rs_momentum, quadrant = rrg_quadrant(rs_line)
            rows.append(
                {
                    "symbol": symbol,
                    "label": meta[symbol]["label"],
                    "group": meta[symbol]["group"],
                    "last_close": round(closes[-1], 4),
                    "bar_as_of": bars[-1][0],
                    "returns": {k: _round_pct(v) for k, v in returns.items()},
                    "rel": {k: _round_pct(v) for k, v in rel.items()},
                    "rs_ratio": rs_ratio,
                    "rs_momentum": rs_momentum,
                    "quadrant": quadrant,
                }
            )

        self._attach_ranks(rows)
        self._attach_rank_changes(rows, as_of=as_of)
        leaders = self._leaders_by_horizon(rows)
        ranked = [row for row in rows if row.get("composite_rank") is not None]
        top3 = [row["symbol"] for row in sorted(ranked, key=lambda r: r["composite_rank"])[:3]]
        summary = (
            f"Rotation snapshot as of {as_of}: {len(ranked)} of {len(symbols)} ETFs ranked vs {BENCHMARK}. "
            f"Composite leaders: {', '.join(top3) if top3 else 'none'}."
        )

        return {
            "as_of": as_of,
            "benchmark": BENCHMARK,
            "universe": "us_etf_proxy",
            "symbols_total": len(symbols),
            "symbols_ranked": len(ranked),
            "constituents": sorted(
                rows, key=lambda r: (r.get("composite_rank") is None, r.get("composite_rank") or 0)
            ),
            "leaders": leaders,
            "warnings": warnings,
            "summary": summary,
            "generated_at": _now_iso(),
        }

    def _attach_ranks(self, rows: List[Dict[str, Any]]) -> None:
        """Per-horizon RS ranks (1 = strongest, benchmark excluded) + composite."""
        candidates = [row for row in rows if row["symbol"] != BENCHMARK]
        for row in rows:
            row["ranks"] = {}
            row["percentiles"] = {}
        for horizon in HORIZONS:
            scored = [row for row in candidates if row["rel"].get(horizon) is not None]
            scored.sort(key=lambda r: r["rel"][horizon], reverse=True)
            n = len(scored)
            for index, row in enumerate(scored):
                row["ranks"][horizon] = index + 1
                row["percentiles"][horizon] = 1.0 - index / (n - 1) if n > 1 else 1.0
        for row in candidates:
            score = composite_score(row["percentiles"])
            row["composite_score"] = score
        ranked = [row for row in candidates if row.get("composite_score") is not None]
        ranked.sort(key=lambda r: r["composite_score"], reverse=True)
        for index, row in enumerate(ranked):
            row["composite_rank"] = index + 1
        for row in rows:
            row.setdefault("composite_score", None)
            row.setdefault("composite_rank", None)
            row.pop("percentiles", None)

    def _attach_rank_changes(self, rows: List[Dict[str, Any]], *, as_of: str) -> None:
        """Diff ranks vs the previous persisted snapshot; first run leaves nulls."""
        try:
            previous = self.db.get_previous_sector_rotation_snapshot(
                before=date.fromisoformat(str(as_of)[:10])
            )
        except Exception as exc:
            logger.debug("Previous rotation snapshot read failed: %s", exc)
            previous = None
        prev_rows = {
            str(item.get("symbol")): item
            for item in (previous or {}).get("constituents") or []
        }
        prev_top3_1m = {
            item["symbol"]
            for item in sorted(
                (item for item in prev_rows.values() if (item.get("ranks") or {}).get("1M")),
                key=lambda item: item["ranks"]["1M"],
            )[:3]
        }
        for row in rows:
            prev = prev_rows.get(row["symbol"]) or {}
            prev_ranks = prev.get("ranks") or {}
            row["rank_change"] = {
                horizon: (prev_ranks[horizon] - row["ranks"][horizon])
                if horizon in row.get("ranks", {}) and horizon in prev_ranks
                else None
                for horizon in HORIZONS
            }
            rank_1m = row.get("ranks", {}).get("1M")
            in_top3 = rank_1m is not None and rank_1m <= 3
            was_top3 = row["symbol"] in prev_top3_1m
            row["entered_top3_1m"] = bool(prev_rows) and in_top3 and not was_top3
            row["exited_top3_1m"] = bool(prev_rows) and was_top3 and not in_top3

    @staticmethod
    def _leaders_by_horizon(rows: List[Dict[str, Any]], *, count: int = 3) -> Dict[str, Any]:
        """Pre-digested leaders/laggards per horizon for the digest and UI header."""
        leaders: Dict[str, Any] = {}
        for horizon in HORIZONS:
            scored = [
                row for row in rows
                if row["symbol"] != BENCHMARK and (row.get("ranks") or {}).get(horizon)
            ]
            scored.sort(key=lambda r: r["ranks"][horizon])

            def _entry(row: Dict[str, Any]) -> Dict[str, Any]:
                return {
                    "symbol": row["symbol"],
                    "label": row["label"],
                    "rank": row["ranks"][horizon],
                    "return_pct": (row.get("returns") or {}).get(horizon),
                    "rel_pct": (row.get("rel") or {}).get(horizon),
                    "rank_change": (row.get("rank_change") or {}).get(horizon),
                    "entered_top3_1m": row.get("entered_top3_1m", False),
                }

            leaders[horizon] = {
                "leaders": [_entry(row) for row in scored[:count]],
                "laggards": [_entry(row) for row in scored[-count:][::-1]],
            }
        return leaders

    def _fetch_histories(self, symbols: List[str], *, days: int) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        max_workers = min(_env_int("RESEARCH_HISTORY_MAX_WORKERS", 3), max(1, len(symbols)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.market_data.get_us_equity_history, symbol, days=days): symbol
                for symbol in symbols
            }
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results[symbol] = future.result()
                except Exception as exc:
                    results[symbol] = {"symbol": symbol, "bars": [], "error": str(exc)}
        return results


def _dated_closes(bars: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
    cleaned = []
    for bar in bars:
        when = str(bar.get("date") or "")[:10]
        close = bar.get("close")
        if not when or close is None:
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            continue
        if value > 0:
            cleaned.append((when, value))
    cleaned.sort(key=lambda item: item[0])
    return cleaned


def _round_pct(value: Optional[float]) -> Optional[float]:
    return round(value * 100, 2) if value is not None else None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
