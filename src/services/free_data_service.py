# -*- coding: utf-8 -*-
"""Free, API-clean data connectors for research workflows."""

from __future__ import annotations

import csv
import copy
import io
import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import RLock
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from src.services.research_market_data import ResearchMarketDataService
from src.storage import DatabaseManager


SEC_HEADERS = {
    "Accept-Encoding": "gzip, deflate",
}

NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"
CFTC_COT_URL = "https://publicreporting.cftc.gov/resource/srt6-5q2f.json"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FINRA_CDN_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{yyyymmdd}.txt"
FINRA_LEGACY_URL = "https://regsho.finra.org/CNMSshvol{yyyymmdd}.txt"

_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_LOCK = RLock()


MACRO_SERIES = [
    {"id": "DGS10", "label": "10Y Treasury Yield", "unit": "%"},
    {"id": "T10Y2Y", "label": "10Y minus 2Y Curve", "unit": "%"},
    {"id": "DFF", "label": "Effective Fed Funds", "unit": "%"},
    {"id": "BAMLH0A0HYM2", "label": "High Yield Spread", "unit": "%"},
    {"id": "DTWEXBGS", "label": "Broad Dollar Index", "unit": "index"},
]

MARKET_REGIME_INDEX_SYMBOLS = ["SPY", "QQQ", "IWM"]
MARKET_REGIME_RATIO_PAIRS = [
    ("QQQ", "SPY", "Growth vs market"),
    ("IWM", "SPY", "Small caps vs market"),
    ("RSP", "SPY", "Equal-weight vs cap-weight"),
    ("HYG", "TLT", "Credit risk appetite"),
]
MARKET_REGIME_SECTOR_SYMBOLS = ["XLK", "XLC", "XLY", "XLF", "XLI", "XLE", "XLV", "XLP", "XLU", "XLB", "SMH"]

# Honest free-tier breadth: ~50 liquid index/sector/industry/credit ETFs instead of
# pretending to scan the whole market through rate-limited free APIs.
BREADTH_ETF_PROXY_SYMBOLS = [
    # Broad indexes and style
    "SPY", "QQQ", "IWM", "DIA", "RSP", "MDY", "IWF", "IWD", "MTUM", "QUAL",
    # SPDR sectors
    "XLK", "XLC", "XLY", "XLF", "XLI", "XLE", "XLV", "XLP", "XLU", "XLB", "XLRE",
    # Industries and themes
    "SMH", "SOXX", "XBI", "IBB", "ITB", "XHB", "KRE", "KBE", "IGV", "ARKK",
    "XME", "XOP", "XRT", "IYT", "JETS", "GDX", "TAN", "URA",
    # Credit and rates
    "HYG", "LQD", "TLT", "IEF",
    # International
    "EEM", "EFA", "FXI", "EWJ", "EWZ",
    # Commodities
    "GLD", "SLV", "USO",
]

DEFAULT_BREADTH_UNIVERSE = "us_etf_proxy"
DEFAULT_BREADTH_UNIVERSE_LIMIT = 100
DEFAULT_BREADTH_MIN_PRICE = 5.0
DEFAULT_BREADTH_MIN_AVG_DOLLAR_VOLUME = 10_000_000.0


SEC_FACT_METRICS = [
    {
        "key": "revenue",
        "label": "Revenue",
        "unit": "USD",
        "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"],
    },
    {
        "key": "gross_profit",
        "label": "Gross Profit",
        "unit": "USD",
        "tags": ["GrossProfit"],
    },
    {
        "key": "operating_income",
        "label": "Operating Income",
        "unit": "USD",
        "tags": ["OperatingIncomeLoss"],
    },
    {
        "key": "net_income",
        "label": "Net Income",
        "unit": "USD",
        "tags": ["NetIncomeLoss", "ProfitLoss"],
    },
    {
        "key": "eps_diluted",
        "label": "Diluted EPS",
        "unit": "USD/shares",
        "tags": ["EarningsPerShareDiluted"],
    },
    {
        "key": "shares_diluted",
        "label": "Diluted Shares",
        "unit": "shares",
        "tags": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    },
]


@dataclass(frozen=True)
class SourceStatus:
    name: str
    category: str
    status: str
    quality: str
    cost: str
    latency: str
    coverage: str
    caveat: str
    last_checked_at: str
    detail: Optional[str] = None
    sample: Optional[Dict[str, Any]] = None
    best_for: Optional[List[str]] = None
    limits: Optional[List[str]] = None
    refresh: Optional[str] = None
    decision_use: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "quality": self.quality,
            "cost": self.cost,
            "latency": self.latency,
            "coverage": self.coverage,
            "caveat": self.caveat,
            "last_checked_at": self.last_checked_at,
            "detail": self.detail,
            "sample": self.sample or {},
            "best_for": self.best_for or [],
            "limits": self.limits or [],
            "refresh": self.refresh or "",
            "decision_use": self.decision_use or "",
        }
        return payload


class FreeDataService:
    """Collect free data source health, snapshots, and beginner-safe caveats."""

    def __init__(self, *, session: Optional[requests.Session] = None, cache_enabled: Optional[bool] = None):
        injected_session = session is not None
        self.session = session or requests.Session()
        self.cache_enabled = (not injected_session) if cache_enabled is None else cache_enabled
        self.market_data = ResearchMarketDataService()
        self._ticker_cache: Optional[Dict[str, int]] = None

    def get_status(self) -> Dict[str, Any]:
        return self._cached("free-data:status", ttl_seconds=300, producer=self._build_status)

    def _build_status(self) -> Dict[str, Any]:
        statuses = [
            self._massive_status(),
            self._sec_status(),
            self._nasdaq_status(),
            self._fred_status(),
            self._cftc_status(),
            self._finra_status(),
            self._alpha_status(),
            self._x_status(),
            self._yfinance_options_status(),
            self._crypto_status(),
        ]
        ok_count = sum(1 for item in statuses if item.status in {"ok", "configured", "fallback"})
        degraded_count = sum(1 for item in statuses if item.status in {"degraded", "missing"})
        return {
            "generated_at": _now_iso(),
            "ok_count": ok_count,
            "degraded_count": degraded_count,
            "sources": [item.as_dict() for item in statuses],
            "summary": (
                "Free stack is usable for weekly momentum research. "
                "The main weak spot is options/gamma: free plans usually force delayed or estimated data."
            ),
            "cache_policy": "Source checks are cached briefly so free APIs are not burned on every page refresh.",
        }

    def get_us_universe(self, *, limit: int = 5000) -> Dict[str, Any]:
        return self._cached(
            f"free-data:us-universe:{max(1, min(limit, 10000))}",
            ttl_seconds=60 * 60 * 12,
            producer=lambda: self._build_us_universe(limit=limit),
        )

    def _build_us_universe(self, *, limit: int = 5000) -> Dict[str, Any]:
        rows, generated_at = self._fetch_nasdaq_directory()
        active = [row for row in rows if row.get("test_issue") != "Y" and row.get("symbol")]
        stocks = [row for row in active if row.get("is_etf") is False]
        etfs = [row for row in active if row.get("is_etf") is True]
        symbols = [row["symbol"] for row in active[: max(1, min(limit, 10000))]]
        return {
            "source": "Nasdaq Trader Symbol Directory",
            "generated_at": generated_at,
            "total": len(active),
            "stocks": len(stocks),
            "etfs": len(etfs),
            "symbols": symbols,
            "sample": active[:20],
            "caveat": "Universe coverage is free and broad, but scanning every symbol with free price APIs will be rate-limited.",
        }

    def get_directory_names(self) -> Dict[str, Any]:
        """Symbol → security-name map from the Nasdaq Trader directory (cached ~12h)."""
        return self._cached(
            "free-data:directory-names",
            ttl_seconds=60 * 60 * 12,
            producer=self._build_directory_names,
        )

    def _build_directory_names(self) -> Dict[str, Any]:
        rows, generated_at = self._fetch_nasdaq_directory()
        names: Dict[str, str] = {}
        for row in rows:
            symbol = row.get("symbol")
            name = row.get("name")
            if symbol and name and row.get("test_issue") != "Y":
                names[symbol] = str(name)
        return {"source": "Nasdaq Trader Symbol Directory", "generated_at": generated_at, "names": names}

    def get_macro_snapshot(self) -> Dict[str, Any]:
        return self._cached("free-data:macro", ttl_seconds=60 * 10, producer=self._build_macro_snapshot)

    def get_market_regime(self) -> Dict[str, Any]:
        return self._cached("free-data:market-regime", ttl_seconds=60 * 30, producer=self._build_market_regime)

    def get_latest_market_breadth(self, *, universe: str = "us_stocks") -> Dict[str, Any]:
        payload = DatabaseManager.get_instance().get_latest_market_breadth_cache(universe=universe)
        if not payload:
            return {
                "status": "missing",
                "universe": universe,
                "summary": "No daily breadth cache exists yet. Run the cache job after market close to replace the ETF proxy with stock-level breadth.",
                "generated_at": _now_iso(),
                "calculation_steps": [],
                "warnings": ["Daily breadth cache has not been built yet."],
            }
        age_days = None
        if payload.get("as_of"):
            try:
                age_days = (date.today() - datetime.fromisoformat(str(payload["as_of"])[:10]).date()).days
            except ValueError:
                age_days = None
        payload["age_days"] = age_days
        payload["freshness"] = "fresh" if age_days is not None and age_days <= 4 else "stale" if age_days is not None else "unknown"
        payload["summary"] = _market_breadth_summary(payload)
        return payload

    def run_market_breadth_cache(
        self,
        *,
        universe: str = "us_stocks",
        limit: Optional[int] = None,
        min_price: Optional[float] = None,
        min_avg_dollar_volume: Optional[float] = None,
    ) -> Dict[str, Any]:
        resolved_limit = _env_int("RESEARCH_BREADTH_SYMBOL_LIMIT", DEFAULT_BREADTH_UNIVERSE_LIMIT)
        if limit is not None:
            resolved_limit = max(1, min(int(limit), 5000))
        min_price_value = min_price if min_price is not None else _env_float("RESEARCH_BREADTH_MIN_PRICE", DEFAULT_BREADTH_MIN_PRICE)
        min_adv_value = (
            min_avg_dollar_volume
            if min_avg_dollar_volume is not None
            else _env_float("RESEARCH_BREADTH_MIN_AVG_DOLLAR_VOLUME", DEFAULT_BREADTH_MIN_AVG_DOLLAR_VOLUME)
        )
        symbols, universe_meta = self._breadth_universe(universe=universe, limit=resolved_limit)
        histories = self._fetch_histories(symbols, days=260)
        constituents = [
            _breadth_constituent_row(
                symbol,
                histories.get(symbol) or {},
                min_price=min_price_value,
                min_avg_dollar_volume=min_adv_value,
            )
            for symbol in symbols
        ]
        with_data = [row for row in constituents if row.get("close") is not None]
        # One consistent as-of date per snapshot: the modal latest-bar date.
        # Symbols whose latest bar is a different session are marked stale and
        # excluded so the snapshot never mixes e.g. June 3 and June 4 bars.
        as_of = _modal_bar_date(with_data) or date.today().isoformat()
        stale_rows = [row for row in with_data if row.get("last_bar_date") and row["last_bar_date"] != as_of]
        for row in stale_rows:
            row["status"] = "stale_bar"
            row["warning"] = (
                f"Excluded from breadth: latest bar {row.get('last_bar_date')} does not match snapshot date {as_of}."
            )
        eligible = [
            row
            for row in constituents
            if row.get("liquidity_pass") and row.get("history_pass") and row.get("status") == "ok"
        ]
        denominator = len(eligible)
        source_counts: Dict[str, int] = {}
        for row in constituents:
            source = str(row.get("source") or "missing")
            source_counts[source] = source_counts.get(source, 0) + 1

        failures = [
            {
                "symbol": row.get("symbol"),
                "reason": row.get("warning") or "Missing enough price history or liquidity data.",
            }
            for row in constituents
            if row.get("status") != "ok"
        ][:80]
        warnings = list(universe_meta.get("warnings") or [])
        if stale_rows:
            warnings.append(
                f"{len(stale_rows)} symbols excluded because their latest bar predates the snapshot date {as_of} "
                "(excluded_stale_count)."
            )
        if denominator < max(25, len(symbols) * 0.25):
            warnings.append(
                "Few symbols passed the history/liquidity gates, so this breadth snapshot should be treated as lower confidence."
            )
        if len(symbols) < int(universe_meta.get("total_available") or len(symbols)):
            warnings.append(
                "This is a capped free-tier universe, not every listed US stock. Raise RESEARCH_BREADTH_SYMBOL_LIMIT only if your data plan can handle it."
            )

        payload = {
            "status": "completed",
            "source": "daily_cache",
            "generated_at": _now_iso(),
            "as_of": as_of,
            "universe": universe,
            "universe_label": universe_meta.get("label"),
            "universe_source": universe_meta.get("source"),
            "total_available": universe_meta.get("total_available"),
            "symbols_requested": len(symbols),
            "symbols_scanned": len(constituents),
            "symbols_with_data": len(with_data),
            "excluded_stale_count": len(stale_rows),
            "symbols_passing_liquidity": denominator,
            "min_price": min_price_value,
            "min_avg_dollar_volume": min_adv_value,
            "above_sma20_count": sum(1 for row in eligible if row.get("above_sma20")),
            "above_sma20_pct": _pct_of(sum(1 for row in eligible if row.get("above_sma20")), denominator),
            "above_sma50_count": sum(1 for row in eligible if row.get("above_sma50")),
            "above_sma50_pct": _pct_of(sum(1 for row in eligible if row.get("above_sma50")), denominator),
            "above_sma200_count": sum(1 for row in eligible if row.get("above_sma200")),
            "above_sma200_pct": _pct_of(sum(1 for row in eligible if row.get("above_sma200")), denominator),
            "new_high_52w_count": sum(1 for row in eligible if row.get("new_high_52w")),
            "new_high_52w_pct": _pct_of(sum(1 for row in eligible if row.get("new_high_52w")), denominator),
            "new_low_52w_count": sum(1 for row in eligible if row.get("new_low_52w")),
            "new_low_52w_pct": _pct_of(sum(1 for row in eligible if row.get("new_low_52w")), denominator),
            "advancers_count": sum(1 for row in eligible if (row.get("change_1d_pct") or 0) > 0),
            "decliners_count": sum(1 for row in eligible if (row.get("change_1d_pct") or 0) < 0),
            "up_volume": sum(float(row.get("latest_volume") or 0) for row in eligible if (row.get("change_1d_pct") or 0) > 0),
            "down_volume": sum(float(row.get("latest_volume") or 0) for row in eligible if (row.get("change_1d_pct") or 0) < 0),
            "source_counts": source_counts,
            "calculation_steps": _market_breadth_steps(min_price_value, min_adv_value),
            "sample_constituents": sorted(eligible, key=lambda row: str(row.get("symbol") or ""))[:80],
            "failures": failures,
            "warnings": warnings,
        }
        payload["summary"] = _market_breadth_summary(payload)
        saved = DatabaseManager.get_instance().save_market_breadth_cache(payload)
        _invalidate_cache("free-data:market-regime")
        saved.update(
            {
                "universe_label": payload.get("universe_label"),
                "universe_source": payload.get("universe_source"),
                "total_available": payload.get("total_available"),
                "excluded_stale_count": payload.get("excluded_stale_count"),
                "summary": payload["summary"],
                "freshness": "fresh",
                "age_days": 0,
            }
        )
        return saved

    def _breadth_universe(self, *, universe: str, limit: int) -> Tuple[List[str], Dict[str, Any]]:
        manual = os.getenv("RESEARCH_BREADTH_SYMBOLS", "").strip()
        if manual:
            symbols = _unique_symbols(manual.split(","))[:limit]
            return symbols, {
                "label": "Manual breadth universe",
                "source": "RESEARCH_BREADTH_SYMBOLS",
                "total_available": len(symbols),
                "warnings": [],
            }

        if universe == "us_etf_proxy":
            symbols = list(BREADTH_ETF_PROXY_SYMBOLS)[:limit]
            return symbols, {
                "label": "ETF-proxy breadth (liquid index/sector/industry ETFs)",
                "source": "BREADTH_ETF_PROXY_SYMBOLS",
                "total_available": len(BREADTH_ETF_PROXY_SYMBOLS),
                "warnings": [
                    "Breadth is measured across ~50 liquid ETFs, not every listed stock. "
                    "This is honest free-tier coverage rather than a sampled pseudo-universe."
                ],
            }

        warnings: List[str] = []
        from src.services.watchlist_service import WatchlistService

        watchlist = WatchlistService().get_symbols()
        priority = _unique_symbols([
            *self.market_data.get_default_us_universe(limit=100),
            *MARKET_REGIME_INDEX_SYMBOLS,
            *MARKET_REGIME_SECTOR_SYMBOLS,
            *watchlist,
        ])
        try:
            rows, _ = self._fetch_nasdaq_directory()
            active = [row for row in rows if row.get("test_issue") != "Y" and row.get("symbol")]
            if universe == "us_etfs":
                pool = [row["symbol"] for row in active if row.get("is_etf") is True]
                label = "US ETFs"
            elif universe == "us_listed":
                pool = [row["symbol"] for row in active]
                label = "US listed stocks and ETFs"
            else:
                pool = [row["symbol"] for row in active if row.get("is_etf") is False]
                label = "US listed stocks"
            selected = [symbol for symbol in priority if symbol in pool]
            remaining = [symbol for symbol in pool if symbol not in selected]
            selected.extend(_stable_sample(remaining, max(0, limit - len(selected))))
            return _unique_symbols(selected)[:limit], {
                "label": label,
                "source": "Nasdaq Trader Symbol Directory plus watchlist/default liquid names",
                "total_available": len(pool),
                "warnings": warnings,
            }
        except Exception as exc:
            warnings.append(f"Nasdaq universe fetch failed, so the app used the configured fallback list: {exc}")
            symbols = _unique_symbols([*priority, *watchlist])[:limit]
            return symbols, {
                "label": "Fallback US stock/watchlist universe",
                "source": "RESEARCH_US_SYMBOLS / defaults / watchlist",
                "total_available": len(symbols),
                "warnings": warnings,
            }

    def _build_market_regime(self) -> Dict[str, Any]:
        symbols = _unique_symbols([
            *MARKET_REGIME_INDEX_SYMBOLS,
            *(symbol for pair in MARKET_REGIME_RATIO_PAIRS for symbol in pair[:2]),
            *MARKET_REGIME_SECTOR_SYMBOLS,
        ])
        histories = self._fetch_histories(symbols, days=260)
        trend_rows = [_trend_snapshot(symbol, histories.get(symbol) or {}) for symbol in MARKET_REGIME_INDEX_SYMBOLS]
        sector_rows = [_trend_snapshot(symbol, histories.get(symbol) or {}) for symbol in MARKET_REGIME_SECTOR_SYMBOLS]
        ratio_rows = [
            _ratio_snapshot(label=label, numerator=left, denominator=right, histories=histories)
            for left, right, label in MARKET_REGIME_RATIO_PAIRS
        ]

        index_score = _index_trend_score(trend_rows)
        sector_score = _breadth_score(sector_rows)
        breadth_universe = os.getenv("RESEARCH_BREADTH_UNIVERSE", DEFAULT_BREADTH_UNIVERSE).strip() or DEFAULT_BREADTH_UNIVERSE
        market_breadth = self.get_latest_market_breadth(universe=breadth_universe)
        if market_breadth.get("status") != "completed" and breadth_universe != "us_stocks":
            market_breadth = self.get_latest_market_breadth(universe="us_stocks")
        stock_breadth_score = _market_breadth_score(market_breadth)
        breadth_component_score = stock_breadth_score if stock_breadth_score is not None else sector_score
        breadth_component_label = "Cached stock breadth" if stock_breadth_score is not None else "Sector ETF breadth"
        breadth_component_description = (
            "Daily cached stock-level breadth: percent of liquid US stocks above 20/50/200 day moving averages."
            if stock_breadth_score is not None
            else "Sector and theme ETFs above their moving averages. This is a proxy, not full stock breadth."
        )
        risk_score = _risk_ratio_score(ratio_rows)
        macro_snapshot = self.get_macro_snapshot()
        macro_score = _macro_score(macro_snapshot.get("series") or [])
        volatility = _fetch_vix_snapshot()
        volatility_score = _volatility_score(volatility)
        available_components = [
            ("Index trend", index_score, 0.35, "SPY, QQQ, and IWM above or below 50/200 day moving averages."),
            (breadth_component_label, breadth_component_score, 0.20, breadth_component_description),
            ("Rally health", risk_score, 0.20, "Checks whether growth stocks, small caps, equal-weight stocks, and credit are helping the rally broaden out."),
            ("Volatility", volatility_score, 0.15, "VIX level, 5-day change, and VIX/VIX3M term structure. Spiking or inverted vol forces a defensive read."),
            ("Macro pressure", macro_score, 0.10, "FRED rates, credit, and dollar series. This is context, not a trigger."),
        ]
        weighted = [score * weight for _, score, weight, _ in available_components if score is not None]
        weights = [weight for _, score, weight, _ in available_components if score is not None]
        score = round(sum(weighted) / sum(weights), 1) if weights else 0.0
        regime_caveats: List[str] = []
        if volatility.get("term_inverted"):
            capped = min(score, 45.0)
            if capped != score:
                regime_caveats.append(
                    "VIX term structure is inverted (VIX above VIX3M), so the regime score is capped below Risk-On."
                )
                score = capped
        regime = _regime_label(score)
        confidence = _regime_confidence(trend_rows, sector_rows, ratio_rows, macro_snapshot)
        as_of = max(
            [
                str(item)
                for item in [
                    _latest_bar_date([*trend_rows, *sector_rows]),
                    market_breadth.get("as_of") if market_breadth.get("status") == "completed" else None,
                ]
                if item
            ],
            default=None,
        )
        has_stock_breadth = stock_breadth_score is not None

        return {
            "generated_at": _now_iso(),
            "as_of": as_of,
            "regime": regime,
            "score": score,
            "confidence": confidence,
            "summary": _regime_summary(regime, score),
            "components": [
                {
                    "label": label,
                    "score": round(component_score, 1) if component_score is not None else None,
                    "weight": weight,
                    "plain_english": description,
                }
                for label, component_score, weight, description in available_components
            ],
            "index_trends": trend_rows,
            "risk_ratios": ratio_rows,
            "sector_breadth": {
                "label": "Sector ETF breadth proxy",
                "score": round(sector_score, 1) if sector_score is not None else None,
                "above_50_pct": _percent_true(sector_rows, "above_sma50"),
                "above_200_pct": _percent_true(sector_rows, "above_sma200"),
                "symbols": sector_rows,
                "plain_english": "This uses liquid sector/theme ETFs as a practical breadth proxy until the app has a full all-stock breadth cache.",
            },
            "market_breadth": market_breadth,
            "macro": macro_snapshot,
            "volatility": volatility,
            "breadth_status": {
                "status": "daily_cache_ready" if has_stock_breadth else "proxy_ready_full_market_not_cached",
                "have_now": [
                    "Index ETF trend: SPY, QQQ, IWM",
                    "Risk ratios: QQQ/SPY, IWM/SPY, RSP/SPY, HYG/TLT",
                    "Sector ETF breadth proxy",
                    *(
                        [
                            "Daily cached US stock breadth: percent above 20/50/200DMA",
                            "Liquidity and history gates for the breadth universe",
                        ]
                        if has_stock_breadth
                        else []
                    ),
                    "FRED macro snapshot",
                    "Nasdaq Trader symbol universe",
                ],
                "missing_for_full_breadth": (
                    [
                        "Raise the free-tier breadth universe cap when provider limits allow it",
                        "Add sector/industry classification so breadth can be broken down by leadership groups",
                    ]
                    if has_stock_breadth
                    else [
                        "Run the daily market breadth cache job after market close",
                        "Daily cached close history for the configured US stock universe",
                        "Liquidity filters so tiny illiquid names do not distort breadth",
                    ]
                ),
                "plain_english": (
                    "The regime score is using cached stock-level breadth when available, then ETF/index proxies for the rest of the tape."
                    if has_stock_breadth
                    else "We can calculate a useful regime filter now. The stronger stock-level breadth layer needs the daily breadth cache job first."
                ),
            },
            "data_sources": [
                {
                    "name": "Massive / Polygon, fallback yfinance",
                    "status": "available" if any((row.get("source") for row in [*trend_rows, *sector_rows])) else "degraded",
                    "used_for": "Daily bars for SPY, QQQ, IWM, sector ETFs, and risk-ratio ETFs.",
                    "caveat": "Free plans may be delayed or rate-limited. yfinance fallback lowers confidence.",
                },
                {
                    "name": "FRED",
                    "status": "available" if (macro_snapshot.get("series") or []) else "degraded",
                    "used_for": "Rates, yield curve, high-yield spread, and dollar backdrop.",
                    "caveat": "Macro updates slower than stock prices and should frame regime, not trigger trades alone.",
                },
                {
                    "name": "Nasdaq Trader Directory",
                    "status": "available",
                    "used_for": "Defines the broad US stocks and ETFs universe for the future breadth cache.",
                    "caveat": "It provides symbols, not prices.",
                },
                {
                    "name": "Full US breadth cache",
                    "status": "available" if has_stock_breadth else "not_built_yet",
                    "used_for": "Percent of stocks above 20/50/200 day moving averages.",
                    "caveat": (
                        "Uses the latest saved daily cache. The UI shows universe size and all liquidity/history gates."
                        if has_stock_breadth
                        else "Needs a scheduled daily job using market-wide bars plus liquidity filters."
                    ),
                },
            ],
            "caveats": [
                *regime_caveats,
                "This is decision support, not a market prediction.",
                (
                    "Cached stock breadth is used when available; otherwise the app falls back to ETF/index breadth proxies."
                    if has_stock_breadth
                    else "The current version uses ETF/index breadth proxies until full all-stock breadth is cached."
                ),
                "Order-book depth is not included; it is a separate paid intraday data product and is not needed for weekly swing regime.",
            ],
        }

    def run_market_regime_snapshot(self) -> Dict[str, Any]:
        """Compute the regime fresh, persist one row per as-of date, and return the payload."""
        payload = self._build_market_regime()
        if not payload.get("as_of"):
            payload["as_of"] = date.today().isoformat()
        try:
            saved = DatabaseManager.get_instance().save_market_regime_snapshot(payload)
            payload["persisted"] = saved
        except Exception as exc:
            payload["persisted"] = {"status": "failed", "error": str(exc)}
        _invalidate_cache("free-data:market-regime")
        return payload

    def _fetch_histories(
        self, symbols: List[str], *, days: int, timeout_seconds: Optional[int] = None
    ) -> Dict[str, Dict[str, Any]]:
        results: Dict[str, Dict[str, Any]] = {}
        # Free-tier providers throttle hard; keep concurrency low so the per-symbol
        # rate limiters in research_market_data space requests instead of burning 429s.
        max_workers = min(_env_int("RESEARCH_HISTORY_MAX_WORKERS", 3), max(1, len(symbols)))
        if timeout_seconds is None:
            timeout_seconds = _env_int("RESEARCH_HISTORY_FETCH_TIMEOUT_SECONDS", 120 if len(symbols) > 40 else 55)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self.market_data.get_us_equity_history, symbol, days=days): symbol
                for symbol in symbols
            }
            try:
                completed = as_completed(futures, timeout=timeout_seconds)
                for future in completed:
                    symbol = futures[future]
                    try:
                        results[symbol] = future.result()
                    except Exception as exc:
                        results[symbol] = {"symbol": symbol, "bars": [], "error": str(exc)}
            except FuturesTimeoutError:
                pass
            for future, symbol in futures.items():
                if symbol not in results:
                    future.cancel()
                    results[symbol] = {"symbol": symbol, "bars": [], "error": "Timed out fetching daily bars"}
        return results

    def _build_macro_snapshot(self) -> Dict[str, Any]:
        series = []
        executor = ThreadPoolExecutor(max_workers=min(5, len(MACRO_SERIES)))
        try:
            futures = [
                executor.submit(self._fred_series_latest, item["id"], label=item["label"], unit=item["unit"], timeout=5)
                for item in MACRO_SERIES
            ]
            try:
                for future in as_completed(futures, timeout=8):
                    try:
                        series.append(future.result())
                    except Exception as exc:
                        series.append({"id": "unknown", "label": "Macro series", "status": "degraded", "detail": str(exc)})
            except FuturesTimeoutError:
                pass
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        series_by_id = {item.get("id"): item for item in series}
        ordered = [
            series_by_id.get(item["id"]) or {
                "id": item["id"],
                "label": item["label"],
                "unit": item["unit"],
                "status": "degraded",
                "detail": "Timed out.",
            }
            for item in MACRO_SERIES
        ]
        return {
            "source": "FRED public CSV",
            "generated_at": _now_iso(),
            "series": ordered,
            "caveat": "Macro series are official/clean, but most are daily or slower and should frame regime, not trigger entries alone.",
        }

    def get_fundamentals(self, symbol: str) -> Dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            raise ValueError("symbol is required")
        return self._cached(
            f"free-data:fundamentals:{normalized}",
            ttl_seconds=60 * 30,
            producer=lambda: self._build_fundamentals(normalized),
        )

    def _build_fundamentals(self, normalized: str) -> Dict[str, Any]:
        cik = self._sec_cik_for_symbol(normalized)
        if not cik:
            return {
                "symbol": normalized,
                "source": "SEC companyfacts",
                "status": "missing",
                "metrics": [],
                "caveat": "No SEC CIK was found for this symbol.",
            }
        headers = self._sec_headers()
        data = self._get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{str(cik).zfill(10)}.json",
            headers=headers,
            timeout=15,
        )
        facts = ((data.get("facts") or {}).get("us-gaap") or {})
        metrics = [_extract_metric(facts, metric) for metric in SEC_FACT_METRICS]
        metrics = [metric for metric in metrics if metric is not None]
        return {
            "symbol": normalized,
            "cik": cik,
            "entity_name": data.get("entityName") or normalized,
            "source": "SEC companyfacts",
            "status": "ok" if metrics else "missing",
            "generated_at": _now_iso(),
            "metrics": metrics,
            "caveat": "SEC facts are official but company tag choices vary. Missing a metric does not always mean the company failed to report it.",
        }

    def get_finra_short_volume(self, symbol: str, *, lookback_days: int = 10) -> Dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            raise ValueError("symbol is required")
        return self._cached(
            f"free-data:finra-short-volume:{normalized}:{max(1, lookback_days)}:{date.today().isoformat()}",
            ttl_seconds=60 * 15,
            producer=lambda: self._build_finra_short_volume(normalized, lookback_days=lookback_days),
        )

    def _build_finra_short_volume(self, normalized: str, *, lookback_days: int = 10) -> Dict[str, Any]:
        diagnostics = []
        for day in _recent_weekdays(lookback_days):
            yyyymmdd = day.strftime("%Y%m%d")
            for template in (FINRA_CDN_URL, FINRA_LEGACY_URL):
                url = template.format(yyyymmdd=yyyymmdd)
                try:
                    response = self.session.get(url, timeout=12)
                except Exception as exc:
                    diagnostics.append(f"{yyyymmdd}: {exc}")
                    continue
                if response.status_code == 404:
                    continue
                if response.status_code in {403, 429}:
                    diagnostics.append(f"{yyyymmdd}: FINRA returned HTTP {response.status_code}")
                    continue
                if response.status_code != 200:
                    diagnostics.append(f"{yyyymmdd}: FINRA returned HTTP {response.status_code}")
                    continue
                row = _parse_finra_short_volume(response.text, normalized)
                if row:
                    return {
                        "symbol": normalized,
                        "source": "FINRA Daily Short Sale Volume",
                        "status": "ok",
                        "date": day.isoformat(),
                        **row,
                        "caveat": "This is short-sale volume, not short interest. It does not prove open short positions.",
                    }
        return {
            "symbol": normalized,
            "source": "FINRA Daily Short Sale Volume",
            "status": "missing",
            "date": None,
            "short_volume": None,
            "total_volume": None,
            "short_volume_ratio": None,
            "diagnostics": diagnostics[-5:],
            "caveat": "No recent FINRA row was found. FINRA files are posted after market close and can lag on weekends/holidays.",
        }

    def _massive_status(self) -> SourceStatus:
        sample = {}
        status = "missing"
        detail = "MASSIVE_API_KEY/POLYGON_API_KEY is not configured."
        key = os.getenv("MASSIVE_API_KEY", "").strip() or os.getenv("POLYGON_API_KEY", "").strip()
        if key:
            try:
                payload = self.market_data.get_us_equity_history("AAPL", days=30)
                if payload.get("source") == "massive" and payload.get("bars"):
                    status = "ok"
                    quality = "primary"
                    sample = {
                        "symbol": payload.get("symbol"),
                        "bars": len(payload.get("bars") or []),
                        "last_bar_date": (payload.get("data_quality") or {}).get("last_bar_date"),
                    }
                    detail = "Massive stock/ETF bars are live."
                else:
                    status = "degraded"
                    quality = "fallback"
                    detail = payload.get("error") or "Massive did not return bars."
            except Exception as exc:
                status = "degraded"
                quality = "fallback"
                detail = str(exc)
        else:
            quality = "missing"
        return SourceStatus(
            name="Massive / Polygon Stocks",
            category="US stocks + ETFs",
            status=status,
            quality=quality,
            cost="free tier",
            latency="delayed / plan dependent",
            coverage="US stock and ETF daily bars, reference data when available",
            caveat="Free tier can be rate-limited and does not necessarily include options snapshots.",
            last_checked_at=_now_iso(),
            detail=detail,
            sample=sample,
            best_for=["Daily trend bars", "SMA/RSI filters", "ETF inclusion", "Liquidity checks"],
            limits=["Free plans can be delayed", "Options snapshots may require a paid tier", "Rate limits matter for full-universe scans"],
            refresh="Cache 5 minutes in-app; provider latency depends on your plan.",
            decision_use="Primary feed for price trend if status is ok. If it falls back to yfinance, lower confidence.",
        )

    def _sec_status(self) -> SourceStatus:
        try:
            data = self._get_json(
                "https://www.sec.gov/files/company_tickers.json",
                headers=self._sec_headers(),
                timeout=12,
            )
            detail = f"{len(data)} SEC ticker mappings loaded."
            status = "ok"
            quality = "official"
        except Exception as exc:
            detail = str(exc)
            status = "degraded"
            quality = "official"
        return SourceStatus(
            name="SEC EDGAR",
            category="filings + fundamentals",
            status=status,
            quality=quality,
            cost="free",
            latency="official filing time",
            coverage="company submissions, 8-K/10-Q/10-K, companyfacts fundamentals, FTD files",
            caveat="Facts are official but XBRL tags vary by company.",
            last_checked_at=_now_iso(),
            detail=detail,
            best_for=["Official fundamentals", "8-K/10-Q/10-K checks", "Revenue and earnings acceleration"],
            limits=["XBRL tags vary by company", "Filings lag business events", "Not useful for intraday timing"],
            refresh="Cache 30 minutes in-app; SEC filings update on official filing cadence.",
            decision_use="Use for catalyst/fundamental confirmation, not as a standalone entry signal.",
        )

    def _nasdaq_status(self) -> SourceStatus:
        try:
            rows, generated_at = self._fetch_nasdaq_directory()
            stocks = sum(1 for row in rows if row.get("is_etf") is False and row.get("test_issue") != "Y")
            etfs = sum(1 for row in rows if row.get("is_etf") is True and row.get("test_issue") != "Y")
            status = "ok"
            detail = f"{stocks} stocks and {etfs} ETFs loaded."
            sample = {"generated_at": generated_at, "stocks": stocks, "etfs": etfs}
        except Exception as exc:
            status = "degraded"
            detail = str(exc)
            sample = {}
        return SourceStatus(
            name="Nasdaq Trader Directory",
            category="US universe",
            status=status,
            quality="official",
            cost="free",
            latency="daily",
            coverage="listed US stocks and ETFs",
            caveat="Universe is broad; free price APIs still limit how many symbols can be scanned per run.",
            last_checked_at=_now_iso(),
            detail=detail,
            sample=sample,
            best_for=["US stock universe", "ETF universe", "Delisted/test issue filtering"],
            limits=["Does not provide prices", "Does not rank liquidity", "Needs market-data scan after universe creation"],
            refresh="Cache 12 hours in-app; Nasdaq file is generally daily.",
            decision_use="Use to define what can be scanned. Price data still decides whether something is a leader.",
        )

    def _fred_status(self) -> SourceStatus:
        sample = self._fred_series_latest("DGS10", label="10Y Treasury Yield", unit="%", timeout=5)
        status = "ok" if sample.get("status") == "ok" else "degraded"
        return SourceStatus(
            name="FRED",
            category="macro",
            status=status,
            quality="official",
            cost="free",
            latency="daily/weekly/monthly by series",
            coverage="rates, spreads, dollar, inflation and economic regime data",
            caveat="Macro is regime context; it should not be treated as a standalone trade trigger.",
            last_checked_at=_now_iso(),
            detail=sample.get("detail"),
            sample=sample,
            best_for=["Rates regime", "Yield curve", "Credit spreads", "Dollar backdrop"],
            limits=["Mostly daily or slower", "Not stock-specific", "Can explain regime but not exact entry"],
            refresh="Cache 10 minutes in-app; FRED series update on their own cadence.",
            decision_use="Use to size conviction around the macro backdrop, not to buy or sell a ticker by itself.",
        )

    def _cftc_status(self) -> SourceStatus:
        try:
            data = self._get_json(
                CFTC_COT_URL,
                params={"$limit": 1, "$order": "report_date_as_yyyy_mm_dd DESC"},
                timeout=12,
            )
            if isinstance(data, list):
                latest = data[0] if data else {}
            else:
                latest = {}
            status = "ok" if latest else "degraded"
            detail = latest.get("report_date_as_yyyy_mm_dd") or "No latest row returned."
        except Exception as exc:
            status = "degraded"
            detail = str(exc)
            latest = {}
        return SourceStatus(
            name="CFTC COT",
            category="futures positioning",
            status=status,
            quality="official",
            cost="free",
            latency="weekly",
            coverage="futures positioning by participant group",
            caveat="Useful for macro proxies, not individual stock positioning.",
            last_checked_at=_now_iso(),
            detail=detail,
            sample={"market": latest.get("market_and_exchange_names"), "report_date": latest.get("report_date_as_yyyy_mm_dd")},
            best_for=["Macro futures positioning", "Crowded long/short context", "Rates/dollar/index proxies"],
            limits=["Weekly lag", "Futures market only", "Not individual stock positioning"],
            refresh="Cache 5 minutes in status checks; COT itself is weekly.",
            decision_use="Use as a macro positioning warning layer for index, rates, dollar, and commodity-sensitive trades.",
        )

    def _finra_status(self) -> SourceStatus:
        payload = self.get_finra_short_volume("AAPL", lookback_days=8)
        status = "ok" if payload.get("status") == "ok" else "degraded"
        return SourceStatus(
            name="FINRA Short Sale Volume",
            category="short pressure",
            status=status,
            quality="official",
            cost="free",
            latency="after market close / T+1 practical lag",
            coverage="daily aggregate short-sale volume reported to FINRA facilities",
            caveat="Not short interest. It is volume reported as short, not open short positions.",
            last_checked_at=_now_iso(),
            detail=payload.get("date") or payload.get("caveat"),
            sample={k: payload.get(k) for k in ("symbol", "short_volume", "total_volume", "short_volume_ratio")},
            best_for=["Short-sale activity context", "Post-close pressure check", "Possible squeeze context"],
            limits=["Not short interest", "Does not show open short positions", "Usually after market close"],
            refresh="Cache 15 minutes in-app; FINRA files are daily.",
            decision_use="Use as a caution/context input only. A high ratio does not prove a stock is heavily shorted.",
        )

    def _alpha_status(self) -> SourceStatus:
        configured = bool(os.getenv("ALPHA_VANTAGE_API_KEY", "").strip() or os.getenv("ALPHAVANTAGE_API_KEY", "").strip())
        return SourceStatus(
            name="Alpha Vantage News",
            category="news + sentiment",
            status="configured" if configured else "missing",
            quality="typed_api",
            cost="free tier",
            latency="provider dependent",
            coverage="market news sentiment by ticker",
            caveat="Free API quotas are tight, so the app should cache and avoid burning calls on every page load.",
            last_checked_at=_now_iso(),
            detail="API key configured." if configured else "API key missing.",
            best_for=["Ticker news", "Theme/catalyst tagging", "Sentiment hints"],
            limits=["Free quota is tight", "News relevance still needs filtering", "Provider coverage varies by ticker"],
            refresh="Should be cached by ticker/date; do not call on every render.",
            decision_use="Use to support or reject catalyst evidence after price passes the technical gates.",
        )

    def _x_status(self) -> SourceStatus:
        configured = bool(os.getenv("X_CLIENT_ID", "").strip() and os.getenv("X_REDIRECT_URI", "").strip())
        return SourceStatus(
            name="X Bookmarks",
            category="source intelligence",
            status="configured" if configured else "missing",
            quality="official_oauth",
            cost="free/API-plan dependent",
            latency="sync on demand",
            coverage="your private bookmarks when X API access permits bookmark.read",
            caveat="Bearer tokens are not enough for private bookmarks; user-context OAuth is required.",
            last_checked_at=_now_iso(),
            detail="OAuth app configured; connect account from Sources." if configured else "OAuth app not configured.",
            best_for=["Your saved source calls", "Ticker extraction", "Forward source scoring"],
            limits=["Requires user OAuth", "X API access can be plan-limited", "Bookmarks are raw inputs, not truth"],
            refresh="Sync on demand from Sources.",
            decision_use="Use to learn which people and narratives deserve attention by scoring calls after the fact.",
        )

    def _yfinance_options_status(self) -> SourceStatus:
        enabled = os.getenv("POSITIONING_USE_YFINANCE_OPTIONS", "true").strip().lower() not in {"0", "false", "no"}
        return SourceStatus(
            name="yfinance Options Fallback",
            category="options/gamma",
            status="fallback" if enabled else "missing",
            quality="unofficial_estimated",
            cost="free",
            latency="delayed / unofficial",
            coverage="option chains when available; Greeks estimated when missing",
            caveat="Good enough for rough context only. Do not treat this as real dealer flow.",
            last_checked_at=_now_iso(),
            detail="Enabled as fallback." if enabled else "Fallback disabled.",
            best_for=["Rough options context", "Approximate gamma map", "Beginner positioning checklist"],
            limits=["Unofficial", "Delayed", "Greeks may be estimated", "Not real dealer flow"],
            refresh="On demand; keep confidence capped when this is the only options source.",
            decision_use="Use for rough context. Do not treat yfinance-only gamma as a decisive signal.",
        )

    def _crypto_status(self) -> SourceStatus:
        try:
            symbols = self.market_data.get_default_crypto_universe(limit=10)
            status = "ok" if symbols else "degraded"
            detail = f"{len(symbols)} liquid symbols loaded."
            sample = {"symbols": symbols[:5]}
        except Exception as exc:
            status = "degraded"
            detail = str(exc)
            sample = {}
        return SourceStatus(
            name="CoinGecko Spot Crypto",
            category="crypto spot",
            status=status,
            quality="public_api",
            cost="free",
            latency="provider dependent",
            coverage="crypto market cap, spot price, and volume",
            caveat="Spot-only. Funding, open interest, and liquidations need exchange APIs or another free derivatives source.",
            last_checked_at=_now_iso(),
            detail=detail,
            sample=sample,
            best_for=["Top crypto spot leaders", "Market cap universe", "Daily momentum"],
            limits=["Spot-only", "No clean free funding/open-interest layer here", "Exchange derivatives data needs another connector"],
            refresh="Cache per scan where possible; free public API can rate-limit.",
            decision_use="Use for crypto spot momentum. Derivatives crowding needs a separate exchange or derivatives data source.",
        )

    def _cached(self, key: str, *, ttl_seconds: int, producer: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        if not self.cache_enabled:
            return producer()
        now = time.monotonic()
        with _CACHE_LOCK:
            cached = _CACHE.get(key)
            if cached and now - cached[0] < ttl_seconds:
                payload = copy.deepcopy(cached[1])
                payload["cache"] = {"status": "hit", "ttl_seconds": ttl_seconds}
                return payload
        payload = producer()
        with _CACHE_LOCK:
            _CACHE[key] = (now, copy.deepcopy(payload))
        payload = copy.deepcopy(payload)
        payload["cache"] = {"status": "miss", "ttl_seconds": ttl_seconds}
        return payload

    def _fetch_nasdaq_directory(self) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        response = self.session.get(NASDAQ_TRADED_URL, timeout=15)
        response.raise_for_status()
        lines = [line for line in response.text.splitlines() if line.strip()]
        generated_at = None
        data_lines = []
        for line in lines:
            if line.startswith("File Creation Time:"):
                generated_at = line.split(":", 1)[1].strip()
                continue
            data_lines.append(line)
        reader = csv.DictReader(io.StringIO("\n".join(data_lines)), delimiter="|")
        rows = []
        for row in reader:
            symbol = _normalize_symbol(row.get("Symbol") or "")
            if not symbol:
                continue
            rows.append(
                {
                    "symbol": symbol,
                    "name": row.get("Security Name"),
                    "listing_exchange": row.get("Listing Exchange"),
                    "market_category": row.get("Market Category"),
                    "is_etf": str(row.get("ETF") or "").strip().upper() == "Y",
                    "test_issue": str(row.get("Test Issue") or "").strip().upper(),
                }
            )
        return rows, generated_at

    def _sec_cik_for_symbol(self, symbol: str) -> Optional[int]:
        if self._ticker_cache is None:
            data = self._get_json(
                "https://www.sec.gov/files/company_tickers.json",
                headers=self._sec_headers(),
                timeout=12,
            )
            cache: Dict[str, int] = {}
            for row in data.values() if isinstance(data, dict) else []:
                ticker = _normalize_symbol(row.get("ticker") or "")
                cik = row.get("cik_str")
                if ticker and cik is not None:
                    try:
                        cache[ticker] = int(cik)
                    except (TypeError, ValueError):
                        continue
            self._ticker_cache = cache
        return self._ticker_cache.get(symbol)

    def fred_series_history(
        self,
        series_id: str,
        *,
        label: str = "",
        unit: str = "",
        years: Optional[int] = None,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """Full FRED series history (trailing N years). Same CSV source as
        _fred_series_latest, which keeps only the last observation."""
        resolved_years = years if years is not None else _env_int("CYCLE_FRED_HISTORY_YEARS", 8)
        return self._cached(
            f"free-data:fred-history:{series_id}:{resolved_years}",
            ttl_seconds=6 * 3600,
            producer=lambda: self._build_fred_series_history(
                series_id, label=label, unit=unit, years=resolved_years, timeout=timeout
            ),
        )

    def _build_fred_series_history(
        self, series_id: str, *, label: str, unit: str, years: int, timeout: int
    ) -> Dict[str, Any]:
        cutoff = (date.today() - timedelta(days=int(years * 365.25))).isoformat()
        try:
            # cosd = chart observation start date; limits the CSV server-side so
            # 60-year daily series do not time out. One retry for transient stalls.
            # Connection: close — FRED stalls rapid sequential CSV requests on a
            # reused keep-alive connection; a fresh connection per series is reliable.
            response = None
            for attempt in (1, 2):
                try:
                    response = self.session.get(
                        FRED_CSV_URL,
                        params={"id": series_id, "cosd": cutoff},
                        headers={"Connection": "close"},
                        timeout=max(timeout, 15),
                    )
                    break
                except requests.exceptions.Timeout:
                    if attempt == 2:
                        raise
                    time.sleep(1.0)
            response.raise_for_status()
            reader = csv.DictReader(io.StringIO(response.text))
            observations = []
            for row in reader:
                value = _float_or_none(row.get(series_id)) if row.get(series_id) not in {None, "", "."} else None
                when = str(row.get("observation_date") or "")[:10]
                if value is None or not when or when < cutoff:
                    continue
                observations.append({"date": when, "value": value})
            status = "ok" if observations else "missing"
            return {
                "id": series_id,
                "label": label or series_id,
                "unit": unit,
                "status": status,
                "observations": observations,
            }
        except Exception as exc:
            return {
                "id": series_id,
                "label": label or series_id,
                "unit": unit,
                "status": "degraded",
                "detail": str(exc),
                "observations": [],
            }

    def _fred_series_latest(self, series_id: str, *, label: str, unit: str, timeout: int = 8) -> Dict[str, Any]:
        try:
            response = self.session.get(FRED_CSV_URL, params={"id": series_id}, timeout=timeout)
            response.raise_for_status()
            reader = csv.DictReader(io.StringIO(response.text))
            latest = None
            for row in reader:
                value = row.get(series_id)
                if value in {None, "", "."}:
                    continue
                latest = {"date": row.get("observation_date"), "value": _float_or_none(value)}
            if latest and latest["value"] is not None:
                return {"id": series_id, "label": label, "unit": unit, "status": "ok", **latest}
            return {"id": series_id, "label": label, "unit": unit, "status": "missing", "detail": "No numeric observation found."}
        except Exception as exc:
            return {"id": series_id, "label": label, "unit": unit, "status": "degraded", "detail": str(exc)}

    def _get_json(self, url: str, *, params: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: int = 12) -> Any:
        response = self.session.get(url, params=params, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _sec_headers(self) -> Dict[str, str]:
        user_agent = os.getenv("SEC_USER_AGENT", "").strip() or "daily-stock-analysis/1.0 contact@example.com"
        return {**SEC_HEADERS, "User-Agent": user_agent}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _invalidate_cache(*keys: str) -> None:
    if not keys:
        return
    with _CACHE_LOCK:
        for key in keys:
            _CACHE.pop(key, None)


def _stable_sample(values: List[str], limit: int) -> List[str]:
    if limit <= 0:
        return []
    if len(values) <= limit:
        return list(values)
    step = len(values) / limit
    result = []
    seen = set()
    for index in range(limit):
        value = values[min(len(values) - 1, int(index * step))]
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _valid_price_volume_bars(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for row in bars:
        close = _float_or_none(row.get("close"))
        date_value = str(row.get("date") or "")[:10]
        if close is None or close <= 0 or not date_value:
            continue
        cleaned.append(
            {
                "date": date_value,
                "close": close,
                "volume": _float_or_none(row.get("volume")) or 0.0,
            }
        )
    cleaned.sort(key=lambda item: item["date"])
    return cleaned


def _breadth_constituent_row(
    symbol: str,
    payload: Dict[str, Any],
    *,
    min_price: float,
    min_avg_dollar_volume: float,
) -> Dict[str, Any]:
    bars = _valid_price_volume_bars(payload.get("bars") or [])
    closes = [bar["close"] for bar in bars]
    close = closes[-1] if closes else None
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    dollar_volumes = [bar["close"] * bar["volume"] for bar in bars[-20:]]
    avg_dollar_volume_20d = _avg(dollar_volumes)
    latest_volume = bars[-1]["volume"] if bars else None
    change_1d = _pct_change(closes, 1)
    high_window = closes[-252:] if len(closes) >= 252 else closes
    low_window = closes[-252:] if len(closes) >= 252 else closes
    history_pass = sma200 is not None
    price_pass = close is not None and close >= min_price
    liquidity_pass = (
        history_pass
        and price_pass
        and avg_dollar_volume_20d is not None
        and avg_dollar_volume_20d >= min_avg_dollar_volume
    )
    warning = payload.get("error") or "; ".join(payload.get("data_warnings") or [])
    if not bars:
        warning = warning or "No daily bars returned."
    elif not history_pass:
        warning = "Needs at least 200 daily closes before it can count in breadth."
    elif not price_pass:
        warning = f"Filtered out because price is below ${min_price:g}."
    elif avg_dollar_volume_20d is None or avg_dollar_volume_20d < min_avg_dollar_volume:
        warning = f"Filtered out because 20-day average dollar volume is below ${min_avg_dollar_volume:,.0f}."
    return {
        "symbol": symbol,
        "name": payload.get("name") or symbol,
        "source": payload.get("source") or "missing",
        "last_bar_date": bars[-1]["date"] if bars else None,
        "status": "ok" if bars else "missing",
        "close": _round(close),
        "sma20": _round(sma20),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "above_sma20": bool(close is not None and sma20 is not None and close > sma20),
        "above_sma50": bool(close is not None and sma50 is not None and close > sma50),
        "above_sma200": bool(close is not None and sma200 is not None and close > sma200),
        "change_1d_pct": _round(change_1d, 2),
        "avg_dollar_volume_20d": _round(avg_dollar_volume_20d, 0),
        "latest_volume": _round(latest_volume, 0),
        "history_pass": history_pass,
        "price_pass": price_pass,
        "liquidity_pass": liquidity_pass,
        "new_high_52w": bool(close is not None and high_window and close >= max(high_window)),
        "new_low_52w": bool(close is not None and low_window and close <= min(low_window)),
        "warning": warning,
    }


def _avg(values: List[float]) -> Optional[float]:
    clean = [value for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def _pct_of(count: int, denominator: int) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(count / denominator * 100, 1)


def _market_breadth_steps(min_price: float, min_avg_dollar_volume: float) -> List[Dict[str, Any]]:
    return [
        {
            "step": "Build universe",
            "plain_english": "Start from Nasdaq Trader listed symbols, then pin the app watchlist/default liquid names so names you care about are included first.",
        },
        {
            "step": "Fetch daily bars",
            "plain_english": "Pull about one trading year of daily OHLCV data from Massive/Polygon first, then yfinance if the primary feed is unavailable.",
        },
        {
            "step": "History gate",
            "plain_english": "Only count symbols with enough data for a 200-day moving average.",
        },
        {
            "step": "Liquidity gate",
            "plain_english": f"Only count symbols above ${min_price:g} with at least ${min_avg_dollar_volume:,.0f} in 20-day average dollar volume.",
        },
        {
            "step": "Breadth math",
            "plain_english": "For the surviving symbols, calculate percent above 20DMA, 50DMA, and 200DMA, plus 52-week highs/lows and advance/decline volume.",
        },
    ]


def _market_breadth_summary(payload: Dict[str, Any]) -> str:
    if payload.get("status") != "completed":
        return str(payload.get("summary") or "No daily breadth cache exists yet.")
    eligible = int(payload.get("symbols_passing_liquidity") or 0)
    scanned = int(payload.get("symbols_scanned") or 0)
    above50 = payload.get("above_sma50_pct")
    above200 = payload.get("above_sma200_pct")
    as_of = payload.get("as_of") or "latest available close"
    return (
        f"Daily breadth cache as of {as_of}: {eligible} of {scanned} scanned symbols passed history/liquidity gates. "
        f"{format_pct_plain(above50)} are above the 50DMA and {format_pct_plain(above200)} are above the 200DMA."
    )


def _market_breadth_score(payload: Dict[str, Any]) -> Optional[float]:
    if payload.get("status") != "completed" or not payload.get("symbols_passing_liquidity"):
        return None
    above50 = _float_or_none(payload.get("above_sma50_pct"))
    above200 = _float_or_none(payload.get("above_sma200_pct"))
    if above50 is None or above200 is None:
        return None
    return above50 * 0.55 + above200 * 0.45


def format_pct_plain(value: Any) -> str:
    parsed = _float_or_none(value)
    return "N/A" if parsed is None else f"{parsed:.1f}%"


def _extract_metric(facts: Dict[str, Any], definition: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for tag in definition["tags"]:
        fact = facts.get(tag) or {}
        units = fact.get("units") or {}
        unit_candidates = list(units.keys())
        preferred_unit = definition["unit"]
        if preferred_unit in units:
            unit_key = preferred_unit
        elif unit_candidates:
            unit_key = unit_candidates[0]
        else:
            continue
        values = [row for row in units.get(unit_key, []) if row.get("val") is not None and row.get("end")]
        if not values:
            continue
        values.sort(key=lambda row: (str(row.get("end") or ""), str(row.get("filed") or "")))
        latest = values[-1]
        comparable = _previous_comparable(values, latest)
        current_value = _float_or_none(latest.get("val"))
        previous_value = _float_or_none(comparable.get("val")) if comparable else None
        growth = None
        if current_value is not None and previous_value not in {None, 0}:
            growth = (current_value - previous_value) / abs(previous_value) * 100
        return {
            "key": definition["key"],
            "label": definition["label"],
            "tag": tag,
            "unit": unit_key,
            "value": current_value,
            "end": latest.get("end"),
            "filed": latest.get("filed"),
            "form": latest.get("form"),
            "fy": latest.get("fy"),
            "fp": latest.get("fp"),
            "previous_value": previous_value,
            "previous_end": comparable.get("end") if comparable else None,
            "growth_pct": round(growth, 2) if growth is not None else None,
        }
    return None


def _previous_comparable(values: List[Dict[str, Any]], latest: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    latest_fp = latest.get("fp")
    latest_fy = latest.get("fy")
    candidates = [row for row in values if row is not latest]
    if latest_fp and latest_fy:
        comparable = [
            row for row in candidates
            if row.get("fp") == latest_fp and _int_or_none(row.get("fy")) == _int_or_none(latest_fy) - 1
        ]
        if comparable:
            return comparable[-1]
    return candidates[-1] if candidates else None


def _parse_finra_short_volume(text: str, symbol: str) -> Optional[Dict[str, Any]]:
    reader = csv.DictReader(io.StringIO(text), delimiter="|")
    for row in reader:
        if _normalize_symbol(row.get("Symbol") or "") != symbol:
            continue
        short_volume = _float_or_none(row.get("ShortVolume"))
        total_volume = _float_or_none(row.get("TotalVolume"))
        short_exempt = _float_or_none(row.get("ShortExemptVolume"))
        ratio = short_volume / total_volume * 100 if short_volume is not None and total_volume else None
        return {
            "short_volume": short_volume,
            "short_exempt_volume": short_exempt,
            "total_volume": total_volume,
            "short_volume_ratio": round(ratio, 2) if ratio is not None else None,
            "market": row.get("Market"),
        }
    return None


def _trend_snapshot(symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    bars = _valid_close_bars(payload.get("bars") or [])
    closes = [bar["close"] for bar in bars]
    close = closes[-1] if closes else None
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    change_20d = _pct_change(closes, 20)
    return {
        "symbol": symbol,
        "name": payload.get("name") or symbol,
        "source": payload.get("source"),
        "last_bar_date": bars[-1]["date"] if bars else None,
        "close": _round(close),
        "sma20": _round(sma20),
        "sma50": _round(sma50),
        "sma200": _round(sma200),
        "above_sma20": bool(close is not None and sma20 is not None and close > sma20),
        "above_sma50": bool(close is not None and sma50 is not None and close > sma50),
        "above_sma200": bool(close is not None and sma200 is not None and close > sma200),
        "sma50_above_sma200": bool(sma50 is not None and sma200 is not None and sma50 > sma200),
        "change_20d_pct": _round(change_20d, 2),
        "status": "ok" if close is not None and sma200 is not None else "missing",
        "warning": payload.get("error") or "; ".join(payload.get("data_warnings") or []),
    }


def _ratio_snapshot(*, label: str, numerator: str, denominator: str, histories: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    left = _valid_close_bars((histories.get(numerator) or {}).get("bars") or [])
    right = _valid_close_bars((histories.get(denominator) or {}).get("bars") or [])
    right_by_date = {row["date"]: row["close"] for row in right}
    ratios = [
        {"date": row["date"], "value": row["close"] / right_by_date[row["date"]]}
        for row in left
        if row["date"] in right_by_date and right_by_date[row["date"]]
    ]
    values = [row["value"] for row in ratios]
    current = values[-1] if values else None
    sma50 = _sma(values, 50)
    change_20d = _pct_change(values, 20)
    improving = bool(current is not None and sma50 is not None and current > sma50 and (change_20d or 0) > 0)
    return {
        "label": label,
        "pair": f"{numerator}/{denominator}",
        "current": _round(current, 4),
        "sma50": _round(sma50, 4),
        "change_20d_pct": _round(change_20d, 2),
        "risk_on": improving,
        "status": "ok" if current is not None and sma50 is not None else "missing",
        "plain_english": (
            f"{label} is strengthening versus its 50-day trend. That helps the rally look healthier."
            if improving
            else f"{label} is not strengthening versus its 50-day trend. That means this part of the market is not helping the rally right now."
        ),
    }


def _valid_close_bars(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for row in bars:
        close = _float_or_none(row.get("close"))
        date_value = str(row.get("date") or "")[:10]
        if close is None or close <= 0 or not date_value:
            continue
        cleaned.append({"date": date_value, "close": close})
    cleaned.sort(key=lambda item: item["date"])
    return cleaned


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _pct_change(values: List[float], lookback: int) -> Optional[float]:
    if len(values) <= lookback:
        return None
    start = values[-lookback - 1]
    end = values[-1]
    if not start:
        return None
    return (end - start) / abs(start) * 100


def _index_trend_score(rows: List[Dict[str, Any]]) -> Optional[float]:
    valid = [row for row in rows if row.get("status") == "ok"]
    if not valid:
        return None
    points = 0.0
    total = len(valid) * 30
    for row in valid:
        points += 7 if row.get("above_sma20") else 0
        points += 8 if row.get("above_sma50") else 0
        points += 10 if row.get("above_sma200") else 0
        points += 5 if row.get("sma50_above_sma200") else 0
    return points / total * 100


def _breadth_score(rows: List[Dict[str, Any]]) -> Optional[float]:
    valid = [row for row in rows if row.get("status") == "ok"]
    if not valid:
        return None
    above50 = sum(1 for row in valid if row.get("above_sma50")) / len(valid) * 100
    above200 = sum(1 for row in valid if row.get("above_sma200")) / len(valid) * 100
    return above50 * 0.55 + above200 * 0.45


def _risk_ratio_score(rows: List[Dict[str, Any]]) -> Optional[float]:
    valid = [row for row in rows if row.get("status") == "ok"]
    if not valid:
        return None
    return sum(1 for row in valid if row.get("risk_on")) / len(valid) * 100


def _macro_score(series: List[Dict[str, Any]]) -> Optional[float]:
    if not series:
        return None
    by_id = {row.get("id"): row for row in series if row.get("status") == "ok"}
    score = 50.0
    high_yield = _float_or_none((by_id.get("BAMLH0A0HYM2") or {}).get("value"))
    ten_year = _float_or_none((by_id.get("DGS10") or {}).get("value"))
    curve = _float_or_none((by_id.get("T10Y2Y") or {}).get("value"))
    dollar = _float_or_none((by_id.get("DTWEXBGS") or {}).get("value"))
    if high_yield is not None:
        score += 15 if high_yield < 4.5 else -15 if high_yield > 6.0 else 0
    if ten_year is not None:
        score += 8 if ten_year < 4.25 else -8 if ten_year > 5.0 else 0
    if curve is not None:
        score += 6 if curve > -0.25 else -4
    if dollar is not None:
        score += 4 if dollar < 125 else -6 if dollar > 130 else 0
    return max(0.0, min(100.0, score))


def _fetch_vix_snapshot() -> Dict[str, Any]:
    """Pull ^VIX and ^VIX3M from yfinance (not available on free Massive/Polygon plans)."""
    try:
        import yfinance as yf

        vix_hist = yf.Ticker("^VIX").history(period="3mo", interval="1d")
        closes = [float(value) for value in (vix_hist["Close"].tolist() if vix_hist is not None and not vix_hist.empty else []) if value]
        if not closes:
            return {"status": "degraded", "detail": "No VIX data returned."}
        vix = closes[-1]
        change_1d = ((vix - closes[-2]) / closes[-2] * 100) if len(closes) >= 2 and closes[-2] else None
        change_5d = ((vix - closes[-6]) / closes[-6] * 100) if len(closes) >= 6 and closes[-6] else None
        vix3m = None
        try:
            vix3m_hist = yf.Ticker("^VIX3M").history(period="5d", interval="1d")
            vix3m_closes = [float(value) for value in (vix3m_hist["Close"].tolist() if vix3m_hist is not None and not vix3m_hist.empty else []) if value]
            vix3m = vix3m_closes[-1] if vix3m_closes else None
        except Exception:
            vix3m = None
        term_inverted = bool(vix3m is not None and vix > vix3m)
        return {
            "status": "ok",
            "source": "yfinance",
            "vix": round(vix, 2),
            "vix3m": round(vix3m, 2) if vix3m is not None else None,
            "change_1d_pct": round(change_1d, 2) if change_1d is not None else None,
            "change_5d_pct": round(change_5d, 2) if change_5d is not None else None,
            "term_inverted": term_inverted,
            "plain_english": (
                f"VIX {vix:.1f}"
                + (f", VIX3M {vix3m:.1f}" if vix3m is not None else "")
                + ("; term structure inverted — stress is front-loaded." if term_inverted else "; term structure normal.")
            ),
        }
    except Exception as exc:
        return {"status": "degraded", "detail": str(exc)}


def _volatility_score(snapshot: Dict[str, Any]) -> Optional[float]:
    """Map VIX level to 0-100 (calm -> 100), with a haircut for fast spikes and inversion."""
    if snapshot.get("status") != "ok":
        return None
    vix = _float_or_none(snapshot.get("vix"))
    if vix is None:
        return None
    if vix < 15:
        score = 100.0
    elif vix < 20:
        score = 100.0 - (vix - 15) / 5 * 45  # 100 -> 55
    elif vix < 28:
        score = 55.0 - (vix - 20) / 8 * 40  # 55 -> 15
    else:
        score = 0.0
    change_5d = _float_or_none(snapshot.get("change_5d_pct"))
    if change_5d is not None and change_5d >= 25:
        score -= 15
    if snapshot.get("term_inverted"):
        score = min(score, 25.0)
    return max(0.0, min(100.0, round(score, 1)))


def _percent_true(rows: List[Dict[str, Any]], key: str) -> Optional[float]:
    valid = [row for row in rows if row.get("status") == "ok"]
    if not valid:
        return None
    return round(sum(1 for row in valid if row.get(key)) / len(valid) * 100, 1)


def _latest_bar_date(rows: List[Dict[str, Any]]) -> Optional[str]:
    dates = [str(row.get("last_bar_date")) for row in rows if row.get("last_bar_date")]
    return max(dates) if dates else None


def _modal_bar_date(rows: List[Dict[str, Any]]) -> Optional[str]:
    """Most common latest-bar date across constituents; ties resolve to the latest date."""
    counts: Dict[str, int] = {}
    for row in rows:
        value = str(row.get("last_bar_date") or "")
        if value:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _regime_label(score: float) -> str:
    if score >= 70:
        return "Risk-On"
    if score <= 39:
        return "Risk-Off"
    return "Neutral"


def _regime_summary(regime: str, score: float) -> str:
    if regime == "Risk-On":
        return f"Risk appetite is supportive. Breakout and momentum setups get a better backdrop, but overextension still matters. Score: {score:.1f}/100."
    if regime == "Risk-Off":
        return f"The tape is fragile. Breakouts need stricter confirmation, smaller size, or a wait-for-reset plan. Score: {score:.1f}/100."
    return f"The tape is mixed. Selective A+ setups can work, but the app should avoid treating every strong stock as buyable. Score: {score:.1f}/100."


def _regime_confidence(
    trend_rows: List[Dict[str, Any]],
    sector_rows: List[Dict[str, Any]],
    ratio_rows: List[Dict[str, Any]],
    macro_snapshot: Dict[str, Any],
) -> str:
    total = len(trend_rows) + len(sector_rows) + len(ratio_rows)
    ok = sum(1 for row in [*trend_rows, *sector_rows, *ratio_rows] if row.get("status") == "ok")
    macro_ok = sum(1 for row in macro_snapshot.get("series") or [] if row.get("status") == "ok")
    coverage = (ok / total) if total else 0
    if coverage >= 0.8 and macro_ok >= 3:
        return "high"
    if coverage >= 0.5:
        return "medium"
    return "low"


def _round(value: Optional[float], digits: int = 2) -> Optional[float]:
    return round(value, digits) if value is not None else None


def _unique_symbols(values: Iterable[str]) -> List[str]:
    symbols = []
    seen = set()
    for value in values:
        symbol = _normalize_symbol(value)
        if symbol and symbol not in seen:
            seen.add(symbol)
            symbols.append(symbol)
    return symbols


def _recent_weekdays(days: int) -> Iterable[date]:
    current = date.today()
    checked = 0
    while checked < max(1, days):
        if current.weekday() < 5:
            yield current
            checked += 1
        current -= timedelta(days=1)


def _normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper().replace(".", "-")


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
