# -*- coding: utf-8 -*-
"""Low-cost market data adapters for research signal scans."""

from __future__ import annotations

import csv
import logging
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)


DEFAULT_US_UNIVERSE = [
    "AAPL",
    "NVDA",
    "AMD",
    "AVGO",
    "ARM",
    "PLTR",
    "APP",
    "TSLA",
    "META",
    "MSFT",
    "COIN",
    "MSTR",
    "HOOD",
    "CRWD",
    "NET",
    "RKLB",
    "IONQ",
    "SMCI",
    "COHR",
    "SOFI",
    "RBLX",
    "NFLX",
    "GOOGL",
    "AMZN",
]

DEFAULT_US_ETFS = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "SMH",
    "SOXX",
    "XLK",
    "XLC",
    "XLY",
    "XLF",
    "XLI",
    "XLE",
    "XLV",
    "XBI",
    "ARKK",
]

DEFAULT_CRYPTO_SYMBOLS = [
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "SUI",
    "APT",
    "ARB",
    "OP",
    "NEAR",
    "TIA",
    "SEI",
    "INJ",
    "PENDLE",
    "TRX",
    "TON",
    "SHIB",
    "HBAR",
    "LTC",
    "DOT",
    "UNI",
    "XMR",
    "PEPE",
    "AAVE",
    "BGB",
    "OKB",
    "TAO",
    "ETC",
    "ONDO",
    "ICP",
    "POL",
    "KAS",
    "CRO",
    "MNT",
    "ATOM",
    "ALGO",
    "VET",
    "ENA",
    "FIL",
    "RENDER",
    "FET",
    "WLD",
    "RUNE",
    "JUP",
    "BONK",
    "MKR",
]

_STABLECOIN_SYMBOLS = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "PYUSD", "USDE"}


class _RateLimiter:
    """Process-wide budget so free-tier providers are not burned with 429s."""

    def __init__(self, *, max_calls: int, per_seconds: float):
        self.max_calls = max(1, max_calls)
        self.per_seconds = per_seconds
        self._calls: List[float] = []
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        """Non-blocking: returns False when the budget for the window is spent."""
        now = time.monotonic()
        with self._lock:
            self._calls = [ts for ts in self._calls if now - ts < self.per_seconds]
            if len(self._calls) >= self.max_calls:
                return False
            self._calls.append(now)
            return True

    def wait_acquire(self) -> None:
        """Blocking: spaces calls out instead of refusing them."""
        while True:
            now = time.monotonic()
            with self._lock:
                self._calls = [ts for ts in self._calls if now - ts < self.per_seconds]
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                sleep_for = self.per_seconds - (now - self._calls[0])
            time.sleep(max(0.05, sleep_for))


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


_MASSIVE_LIMITER = _RateLimiter(
    max_calls=_env_int("MASSIVE_RATE_LIMIT_PER_MINUTE", 5),
    per_seconds=60.0,
)
_YFINANCE_LIMITER = _RateLimiter(
    max_calls=_env_int("YFINANCE_RATE_LIMIT_PER_MINUTE", 60),
    per_seconds=60.0,
)

# CoinGecko free tier 429s aggressively; honor Retry-After with a process cooldown.
_COINGECKO_COOLDOWN_UNTIL = 0.0
_COINGECKO_LOCK = threading.Lock()

# Crypto bars and the crypto universe are cached in-process (no DB column fits
# long CoinGecko symbols, and crypto scans rerun within the hour).
_CRYPTO_BARS_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CRYPTO_UNIVERSE_CACHE: Dict[str, Tuple[float, List[str]]] = {}
_CRYPTO_CACHE_LOCK = threading.Lock()

# Name/market-cap profiles so DB-cached equity bars keep their size gate data.
_EQUITY_PROFILE_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_PROFILE_CACHE_TTL_SECONDS = 24 * 3600


def _coingecko_cooling_down() -> bool:
    with _COINGECKO_LOCK:
        return time.monotonic() < _COINGECKO_COOLDOWN_UNTIL


def _coingecko_mark_rate_limited(retry_after: Optional[str]) -> None:
    global _COINGECKO_COOLDOWN_UNTIL
    try:
        cooldown = max(30.0, float(retry_after)) if retry_after else 120.0
    except (TypeError, ValueError):
        cooldown = 120.0
    with _COINGECKO_LOCK:
        _COINGECKO_COOLDOWN_UNTIL = max(_COINGECKO_COOLDOWN_UNTIL, time.monotonic() + cooldown)


def _latest_expected_us_bar_date(now: Optional[datetime] = None) -> date:
    """Most recent US trading session whose daily bar should exist (holiday-naive)."""
    eastern = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    candidate = eastern.date()
    # Before ~17:00 ET the current session's daily bar is incomplete/absent.
    if eastern.hour < 17:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


class ResearchMarketDataService:
    """Fetch low-cost stock and crypto bars with fail-open semantics."""

    def __init__(self):
        self._coin_id_cache: Dict[str, str] = dict(_COINGECKO_IDS)
        self._crypto_market_caps: Dict[str, float] = {}
        self._crypto_names: Dict[str, str] = dict(_CRYPTO_NAMES)
        self._coin_universe_refreshed_at: float = 0.0

    def get_default_us_universe(self, *, limit: int = 250) -> List[str]:
        """Return the US scan universe from env/manual Finviz CSV/fallback defaults."""

        manual = os.getenv("RESEARCH_US_SYMBOLS", "").strip()
        if manual:
            symbols = _normalize_symbols(manual.split(","))
            if symbols:
                return symbols[:limit]

        symbols: List[str] = []
        csv_path = os.getenv("RESEARCH_FINVIZ_CSV_PATH", "").strip() or os.getenv("FINVIZ_CSV_PATH", "").strip()
        if csv_path:
            symbols = _symbols_from_finviz_csv(csv_path)
        if not symbols:
            symbols = list(DEFAULT_US_UNIVERSE)

        if _env_bool("RESEARCH_US_INCLUDE_ETFS", True):
            symbols.extend(DEFAULT_US_ETFS)

        from src.services.watchlist_service import WatchlistService

        watchlist_syms = WatchlistService().get_symbols()
        if watchlist_syms:
            symbols.extend(watchlist_syms)

        return _normalize_symbols(symbols)[:limit]

    def get_default_crypto_universe(self, *, limit: int = 50) -> List[str]:
        """Return top liquid crypto symbols, falling back to a static watchlist.

        The CoinGecko markets call is cached in-process for 24h so repeated scans
        (and coin-id misses) do not burn the free-tier quota.
        """
        cache_key = f"universe:{limit}"
        with _CRYPTO_CACHE_LOCK:
            cached = _CRYPTO_UNIVERSE_CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < 24 * 3600:
                return list(cached[1])
        if _coingecko_cooling_down():
            return DEFAULT_CRYPTO_SYMBOLS[:limit]
        try:
            url = "https://api.coingecko.com/api/v3/coins/markets"
            params = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": max(1, min(250, int(limit) + len(_STABLECOIN_SYMBOLS) + 10)),
                "page": 1,
                "sparkline": "false",
            }
            response = requests.get(url, params=params, timeout=12)
            if response.status_code == 429:
                _coingecko_mark_rate_limited(response.headers.get("Retry-After"))
                return DEFAULT_CRYPTO_SYMBOLS[:limit]
            if response.status_code != 200:
                return DEFAULT_CRYPTO_SYMBOLS[:limit]
            symbols = []
            for row in response.json() or []:
                symbol = str(row.get("symbol") or "").upper()
                coin_id = str(row.get("id") or "")
                if not symbol or symbol in _STABLECOIN_SYMBOLS:
                    continue
                self._coin_id_cache[symbol] = coin_id
                market_cap = _float_or_none(row.get("market_cap"))
                if market_cap:
                    self._crypto_market_caps[symbol] = market_cap
                name = str(row.get("name") or "").strip()
                if name:
                    self._crypto_names[symbol] = name
                if symbol not in symbols:
                    symbols.append(symbol)
                if len(symbols) >= limit:
                    break
            result = symbols or DEFAULT_CRYPTO_SYMBOLS[:limit]
            with _CRYPTO_CACHE_LOCK:
                _CRYPTO_UNIVERSE_CACHE[cache_key] = (time.monotonic(), list(result))
            return result
        except Exception as exc:
            logger.info("Crypto universe fetch failed: %s", exc)
            return DEFAULT_CRYPTO_SYMBOLS[:limit]

    def get_us_equity_history(self, symbol: str, *, days: int = 260) -> Dict[str, Any]:
        cached = self._get_cached_equity_history(symbol, days=days)
        if cached is not None:
            return cached
        payload = self._get_us_equity_history_network(symbol, days=days)
        if payload.get("bars"):
            self._store_equity_bars(symbol, payload)
        return payload

    def _get_us_equity_history_network(self, symbol: str, *, days: int = 260) -> Dict[str, Any]:
        massive_payload = self._get_massive_equity_history(symbol, days=days)
        if massive_payload.get("bars"):
            return massive_payload
        massive_warning = massive_payload.get("error")
        try:
            import yfinance as yf

            _YFINANCE_LIMITER.wait_acquire()
            ticker = yf.Ticker(symbol.upper())
            period = f"{max(days, 260)}d"
            df = ticker.history(period=period, interval="1d", auto_adjust=False)
            if df is None or df.empty:
                return {"symbol": symbol.upper(), "bars": [], "error": "no yfinance data"}
            df = df.reset_index()
            bars = []
            for _, row in df.iterrows():
                bars.append(
                    {
                        "date": str(row.get("Date") or row.get("Datetime") or ""),
                        "open": _float_or_none(row.get("Open")),
                        "high": _float_or_none(row.get("High")),
                        "low": _float_or_none(row.get("Low")),
                        "close": _float_or_none(row.get("Close")),
                        "volume": _float_or_none(row.get("Volume")) or 0.0,
                    }
                )
            info = {}
            try:
                info = ticker.fast_info or {}
            except Exception:
                info = {}
            market_cap = _float_or_none(_get_attr_or_item(info, "market_cap"))
            if not market_cap:
                try:
                    market_cap = _float_or_none((ticker.info or {}).get("marketCap"))
                except Exception:
                    market_cap = None
            return {
                "symbol": symbol.upper(),
                "name": symbol.upper(),
                "asset_type": "stock",
                "market_cap": market_cap,
                "bars": bars[-days:],
                "source": "yfinance",
                "data_quality": _data_quality(
                    source="yfinance",
                    tier="unofficial_fallback",
                    bars=bars[-days:],
                    warnings=[
                        "Using unofficial Yahoo/yfinance fallback data.",
                        *([f"Massive/Polygon unavailable: {massive_warning}"] if massive_warning else []),
                    ],
                ),
                "data_warnings": [
                    "Using unofficial Yahoo/yfinance fallback data.",
                    *([f"Massive/Polygon unavailable: {massive_warning}"] if massive_warning else []),
                ],
            }
        except Exception as exc:
            logger.info("US equity history fetch failed for %s: %s", symbol, exc)
            error = str(exc)
            if massive_warning:
                error = f"{error}; Massive/Polygon unavailable: {massive_warning}"
            return {"symbol": symbol.upper(), "bars": [], "error": error}

    def _get_cached_equity_history(self, symbol: str, *, days: int = 260) -> Optional[Dict[str, Any]]:
        """Serve bars from the stock_daily table when they already cover the latest session."""
        if not _env_bool("RESEARCH_BAR_CACHE_ENABLED", True):
            return None
        normalized = symbol.upper().strip()
        try:
            from src.storage import DatabaseManager

            end = date.today()
            start = end - timedelta(days=max(420, int(days * 2)))
            rows = DatabaseManager.get_instance().get_data_range(normalized, start, end)
        except Exception as exc:
            logger.debug("Bar cache read failed for %s: %s", normalized, exc)
            return None
        if len(rows) < 200:
            return None
        last_bar = rows[-1].date
        if last_bar is None or last_bar < _latest_expected_us_bar_date():
            return None
        bars = [
            {
                "date": row.date.isoformat(),
                "open": _float_or_none(row.open),
                "high": _float_or_none(row.high),
                "low": _float_or_none(row.low),
                "close": _float_or_none(row.close),
                "volume": _float_or_none(row.volume) or 0.0,
            }
            for row in rows
            if row.date is not None and _float_or_none(row.close)
        ][-days:]
        if len(bars) < 200:
            return None
        profile = self._equity_profile(normalized)
        source = str(rows[-1].data_source or "db_cache")
        warnings = [f"Served from local daily-bar cache (originally from {source})."]
        return {
            "symbol": normalized,
            "name": profile.get("name") or normalized,
            "asset_type": "stock",
            "asset_subtype": "etf" if normalized in DEFAULT_US_ETFS else "stock",
            "market_cap": profile.get("market_cap"),
            "bars": bars,
            "source": "db_cache",
            "data_quality": _data_quality(source="db_cache", tier="local_cache", bars=bars, warnings=warnings),
            "data_warnings": warnings,
        }

    def _store_equity_bars(self, symbol: str, payload: Dict[str, Any]) -> None:
        if not _env_bool("RESEARCH_BAR_CACHE_ENABLED", True):
            return
        normalized = symbol.upper().strip()
        bars = payload.get("bars") or []
        if not bars:
            return
        try:
            import pandas as pd

            from src.storage import DatabaseManager

            frame = pd.DataFrame(
                [
                    {
                        "date": str(bar.get("date") or "")[:10],
                        "open": bar.get("open"),
                        "high": bar.get("high"),
                        "low": bar.get("low"),
                        "close": bar.get("close"),
                        "volume": bar.get("volume"),
                    }
                    for bar in bars
                    if bar.get("date") and bar.get("close") is not None
                ]
            )
            if frame.empty:
                return
            DatabaseManager.get_instance().save_daily_data(frame, normalized, data_source=str(payload.get("source") or "research"))
        except Exception as exc:
            logger.debug("Bar cache write failed for %s: %s", normalized, exc)
        name = payload.get("name")
        market_cap = _float_or_none(payload.get("market_cap"))
        if name or market_cap:
            _EQUITY_PROFILE_CACHE[normalized] = (time.monotonic(), {"name": name, "market_cap": market_cap})

    def _equity_profile(self, symbol: str) -> Dict[str, Any]:
        cached = _EQUITY_PROFILE_CACHE.get(symbol)
        if cached and time.monotonic() - cached[0] < _PROFILE_CACHE_TTL_SECONDS:
            return cached[1]
        _YFINANCE_LIMITER.wait_acquire()
        profile = _yfinance_market_profile(symbol)
        if profile:
            _EQUITY_PROFILE_CACHE[symbol] = (time.monotonic(), profile)
        return profile or {}

    def _get_massive_equity_history(self, symbol: str, *, days: int = 260) -> Dict[str, Any]:
        api_key = _massive_api_key()
        normalized = symbol.upper().strip()
        if not api_key:
            return {"symbol": normalized, "bars": [], "error": "MASSIVE_API_KEY/POLYGON_API_KEY is not configured"}
        if not _MASSIVE_LIMITER.try_acquire():
            return {"symbol": normalized, "bars": [], "error": "local rate-limit budget exhausted; using fallback"}

        end = date.today()
        start = end - timedelta(days=max(370, int(days * 2)))
        base_url = os.getenv("MASSIVE_API_BASE_URL", "https://api.massive.com").rstrip("/")
        urls = [f"{base_url}/v2/aggs/ticker/{normalized}/range/1/day/{start.isoformat()}/{end.isoformat()}"]
        if base_url != "https://api.polygon.io" and _env_bool("MASSIVE_USE_LEGACY_POLYGON_FALLBACK", True):
            urls.append(f"https://api.polygon.io/v2/aggs/ticker/{normalized}/range/1/day/{start.isoformat()}/{end.isoformat()}")

        last_error = ""
        for url in urls:
            try:
                response = requests.get(
                    url,
                    params={
                        "adjusted": "true",
                        "sort": "asc",
                        "limit": 5000,
                        "apiKey": api_key,
                    },
                    timeout=15,
                )
                if response.status_code in {401, 403}:
                    last_error = "API key rejected or plan does not include this endpoint"
                    continue
                if response.status_code == 429:
                    last_error = "rate limited"
                    continue
                response.raise_for_status()
                payload = response.json()
                results = payload.get("results") or []
                bars = [_massive_bar(row) for row in results]
                bars = [row for row in bars if row][-days:]
                if not bars:
                    last_error = str(payload.get("status") or "no aggregate bars returned")
                    continue
                profile = self._get_massive_market_profile(normalized, api_key=api_key, base_url=base_url)
                profile_warnings = list(profile.get("warnings") or [])
                return {
                    "symbol": normalized,
                    "name": profile.get("name") or normalized,
                    "asset_type": "stock",
                    "asset_subtype": profile.get("asset_subtype") or ("etf" if normalized in DEFAULT_US_ETFS else "stock"),
                    "market_cap": profile.get("market_cap"),
                    "bars": bars,
                    "source": "massive",
                    "data_quality": _data_quality(
                        source="Massive (formerly Polygon)",
                        tier="primary_market_data",
                        bars=bars,
                        warnings=[
                            "Free plans may be delayed or rate-limited; check provider plan for exact latency.",
                            *profile_warnings,
                        ],
                    ),
                    "data_warnings": [
                        "Free plans may be delayed or rate-limited; check provider plan for exact latency.",
                        *profile_warnings,
                    ],
                }
            except Exception as exc:
                last_error = str(exc)
        return {"symbol": normalized, "bars": [], "error": last_error or "Massive/Polygon returned no data"}

    def _get_massive_market_profile(self, symbol: str, *, api_key: str, base_url: str) -> Dict[str, Any]:
        profile: Dict[str, Any] = {"warnings": []}
        try:
            response = requests.get(
                f"{base_url}/v3/reference/tickers/{symbol}",
                params={"apiKey": api_key},
                timeout=12,
            )
            if response.status_code == 200:
                result = (response.json() or {}).get("results") or {}
                profile["name"] = result.get("name") or symbol
                profile["market_cap"] = _float_or_none(result.get("market_cap"))
                ticker_type = str(result.get("type") or "").upper()
                profile["asset_subtype"] = "etf" if ticker_type == "ETF" or symbol in DEFAULT_US_ETFS else "stock"
                if profile.get("market_cap") is not None:
                    return profile
            elif response.status_code in {401, 403, 429}:
                profile["warnings"].append("Massive profile endpoint was unavailable for market cap.")
        except Exception as exc:
            profile["warnings"].append(f"Massive profile fetch failed: {exc}")

        fallback = _yfinance_market_profile(symbol)
        if fallback:
            profile.update({k: v for k, v in fallback.items() if v is not None})
            profile["warnings"].append("Market cap/name filled from yfinance because Massive profile data was incomplete.")
        elif symbol in DEFAULT_US_ETFS:
            profile["asset_subtype"] = "etf"
            profile["warnings"].append("ETF market cap unavailable; liquidity gates are used as the practical size filter.")
        else:
            profile["warnings"].append("Market cap unavailable; size gate may use liquidity as a proxy.")
        return profile

    def get_crypto_history(self, symbol: str, *, days: int = 260) -> Dict[str, Any]:
        """Daily crypto bars: Binance -> Kraken -> CoinGecko, with an in-process cache."""
        normalized = symbol.upper().strip()
        ttl = _env_int("RESEARCH_CRYPTO_CACHE_TTL_SECONDS", 3600)
        cache_key = f"{normalized}:{days}"
        with _CRYPTO_CACHE_LOCK:
            cached = _CRYPTO_BARS_CACHE.get(cache_key)
            if cached and time.monotonic() - cached[0] < ttl:
                return dict(cached[1])

        errors: List[str] = []
        payload = self._get_binance_crypto_history(normalized, days=days, errors=errors)
        if payload is None:
            payload = self._get_kraken_crypto_history(normalized, days=days, errors=errors)
        if payload is None:
            payload = self._get_coingecko_crypto_history(normalized, days=days, errors=errors)
        if payload is None:
            return {
                "symbol": normalized,
                "asset_type": "crypto",
                "bars": [],
                "error": "; ".join(errors) or "no crypto data source returned bars",
            }
        with _CRYPTO_CACHE_LOCK:
            _CRYPTO_BARS_CACHE[cache_key] = (time.monotonic(), dict(payload))
        return payload

    def _crypto_payload(
        self,
        normalized: str,
        bars: List[Dict[str, Any]],
        *,
        days: int,
        source: str,
        warnings: List[str],
    ) -> Dict[str, Any]:
        trimmed = bars[-days:]
        return {
            "symbol": normalized,
            "name": self._crypto_names.get(normalized, normalized),
            "asset_type": "crypto",
            "market_cap": self._crypto_market_caps.get(normalized),
            "bars": trimmed,
            "source": source,
            "data_quality": _data_quality(source=source, tier="spot_market_data", bars=trimmed, warnings=warnings),
            "data_warnings": warnings,
        }

    def _get_binance_crypto_history(
        self, normalized: str, *, days: int, errors: List[str]
    ) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": f"{normalized}USDT",
                    "interval": "1d",
                    "limit": max(1, min(1000, days + 5)),
                },
                timeout=12,
            )
            if response.status_code != 200:
                errors.append(f"binance status {response.status_code}")
                return None
            rows = response.json() or []
            bars = []
            for row in rows:
                if not isinstance(row, list) or len(row) < 6:
                    continue
                bars.append(
                    {
                        "date": datetime.fromtimestamp(float(row[0]) / 1000, tz=timezone.utc).date().isoformat(),
                        "open": _float_or_none(row[1]),
                        "high": _float_or_none(row[2]),
                        "low": _float_or_none(row[3]),
                        "close": _float_or_none(row[4]),
                        "volume": _float_or_none(row[5]) or 0.0,
                    }
                )
            if not bars:
                errors.append("binance returned no klines")
                return None
            warnings = ["Spot OHLCV from Binance USDT pair; funding/open-interest data not included."]
            return self._crypto_payload(normalized, bars, days=days, source="binance", warnings=warnings)
        except Exception as exc:
            errors.append(f"binance: {exc}")
            return None

    def _get_kraken_crypto_history(
        self, normalized: str, *, days: int, errors: List[str]
    ) -> Optional[Dict[str, Any]]:
        try:
            response = requests.get(
                "https://api.kraken.com/0/public/OHLC",
                params={"pair": f"{normalized}USD", "interval": 1440},
                timeout=12,
            )
            if response.status_code != 200:
                errors.append(f"kraken status {response.status_code}")
                return None
            data = response.json() or {}
            if data.get("error"):
                errors.append(f"kraken: {';'.join(data['error'])}")
                return None
            result = data.get("result") or {}
            series = next((value for key, value in result.items() if key != "last"), None)
            if not series:
                errors.append("kraken returned no OHLC series")
                return None
            bars = []
            for row in series:
                if not isinstance(row, list) or len(row) < 7:
                    continue
                bars.append(
                    {
                        "date": datetime.fromtimestamp(float(row[0]), tz=timezone.utc).date().isoformat(),
                        "open": _float_or_none(row[1]),
                        "high": _float_or_none(row[2]),
                        "low": _float_or_none(row[3]),
                        "close": _float_or_none(row[4]),
                        "volume": _float_or_none(row[6]) or 0.0,
                    }
                )
            if not bars:
                errors.append("kraken returned no usable bars")
                return None
            warnings = [
                "Spot OHLCV from Kraken USD pair; funding/open-interest data not included.",
                "Kraken history is capped at ~720 daily bars.",
            ]
            return self._crypto_payload(normalized, bars, days=days, source="kraken", warnings=warnings)
        except Exception as exc:
            errors.append(f"kraken: {exc}")
            return None

    def _get_coingecko_crypto_history(
        self, normalized: str, *, days: int, errors: List[str]
    ) -> Optional[Dict[str, Any]]:
        if _coingecko_cooling_down():
            errors.append("coingecko cooling down after rate limit")
            return None
        coin_id = self._resolve_coin_id(normalized)
        if not coin_id:
            errors.append("unsupported crypto symbol")
            return None
        try:
            url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
            params = {"vs_currency": "usd", "days": max(days, 260), "interval": "daily"}
            response = requests.get(url, params=params, timeout=12)
            if response.status_code == 429:
                _coingecko_mark_rate_limited(response.headers.get("Retry-After"))
                errors.append("coingecko status 429")
                return None
            if response.status_code != 200:
                errors.append(f"coingecko status {response.status_code}")
                return None
            data = response.json()
            prices = data.get("prices") or []
            volumes = data.get("total_volumes") or []
            market_caps = data.get("market_caps") or []
            bars = []
            for index, item in enumerate(prices):
                if not isinstance(item, list) or len(item) < 2:
                    continue
                ts_ms, close = item[0], item[1]
                volume = volumes[index][1] if index < len(volumes) and len(volumes[index]) > 1 else 0
                day = datetime.fromtimestamp(float(ts_ms) / 1000, tz=timezone.utc).date().isoformat()
                close_f = float(close)
                bars.append(
                    {
                        "date": day,
                        "open": close_f,
                        "high": close_f,
                        "low": close_f,
                        "close": close_f,
                        "volume": float(volume or 0),
                    }
                )
            if not bars:
                errors.append("coingecko returned no prices")
                return None
            if market_caps and isinstance(market_caps[-1], list) and len(market_caps[-1]) > 1:
                market_cap = _float_or_none(market_caps[-1][1])
                if market_cap:
                    self._crypto_market_caps[normalized] = market_cap
            warnings = [
                "Synthetic OHLC from CoinGecko closes (open=high=low=close).",
                "Spot data only; crypto funding, open interest, and liquidation data are not included.",
            ]
            return self._crypto_payload(normalized, bars, days=days, source="coingecko", warnings=warnings)
        except Exception as exc:
            logger.info("Crypto history fetch failed for %s: %s", normalized, exc)
            errors.append(f"coingecko: {exc}")
            return None

    def _resolve_coin_id(self, symbol: str) -> Optional[str]:
        normalized = symbol.upper()
        if normalized in self._coin_id_cache:
            return self._coin_id_cache[normalized]
        # Refresh the id map at most once per day instead of once per unknown symbol.
        if time.monotonic() - self._coin_universe_refreshed_at > 24 * 3600:
            self._coin_universe_refreshed_at = time.monotonic()
            self.get_default_crypto_universe(limit=50)
        return self._coin_id_cache.get(normalized)


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _get_attr_or_item(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _symbols_from_finviz_csv(csv_path: str) -> List[str]:
    path = Path(csv_path).expanduser()
    if not path.exists() or not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = {name.lower(): name for name in reader.fieldnames or [] if name}
            ticker_field = fieldnames.get("ticker") or fieldnames.get("symbol")
            if not ticker_field:
                return []
            symbols = []
            for row in reader:
                symbol = str(row.get(ticker_field) or "").strip().upper()
                if symbol and symbol not in symbols:
                    symbols.append(symbol)
            return symbols
    except OSError as exc:
        logger.info("Finviz CSV import failed for %s: %s", csv_path, exc)
        return []


def _normalize_symbols(symbols: List[str]) -> List[str]:
    result = []
    for symbol in symbols:
        cleaned = (symbol or "").strip().upper()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _massive_api_key() -> str:
    return os.getenv("MASSIVE_API_KEY", "").strip() or os.getenv("POLYGON_API_KEY", "").strip()


def _massive_bar(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    close = _float_or_none(row.get("c"))
    ts_ms = _float_or_none(row.get("t"))
    if close is None or ts_ms is None:
        return None
    return {
        "date": datetime.utcfromtimestamp(ts_ms / 1000).date().isoformat(),
        "open": _float_or_none(row.get("o")),
        "high": _float_or_none(row.get("h")),
        "low": _float_or_none(row.get("l")),
        "close": close,
        "volume": _float_or_none(row.get("v")) or 0.0,
    }


def _yfinance_market_profile(symbol: str) -> Dict[str, Any]:
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol.upper())
        info = {}
        try:
            info = ticker.fast_info or {}
        except Exception:
            info = {}
        market_cap = _float_or_none(_get_attr_or_item(info, "market_cap"))
        name = None
        if not market_cap or not name:
            try:
                raw_info = ticker.info or {}
                market_cap = market_cap or _float_or_none(raw_info.get("marketCap"))
                name = raw_info.get("longName") or raw_info.get("shortName")
            except Exception:
                pass
        return {"market_cap": market_cap, "name": name}
    except Exception:
        return {}


def _data_quality(*, source: str, tier: str, bars: List[Dict[str, Any]], warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    last_bar_date = None
    if bars:
        last_bar_date = str(bars[-1].get("date") or "") or None
    stale_days = None
    freshness = "unknown"
    if last_bar_date:
        try:
            last = datetime.fromisoformat(last_bar_date[:10]).date()
            stale_days = (date.today() - last).days
            freshness = "fresh" if stale_days <= 4 else "stale"
        except ValueError:
            freshness = "unknown"
    return {
        "source": source,
        "tier": tier,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "last_bar_date": last_bar_date,
        "stale_days": stale_days,
        "freshness": freshness,
        "warnings": [item for item in (warnings or []) if item],
    }


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


_COINGECKO_IDS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "TRX": "tron",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "SUI": "sui",
    "APT": "aptos",
    "ARB": "arbitrum",
    "OP": "optimism",
    "NEAR": "near",
    "TIA": "celestia",
    "SEI": "sei-network",
    "INJ": "injective-protocol",
    "PENDLE": "pendle",
    "TON": "the-open-network",
    "SHIB": "shiba-inu",
    "HBAR": "hedera-hashgraph",
    "LTC": "litecoin",
    "DOT": "polkadot",
    "UNI": "uniswap",
    "XMR": "monero",
    "PEPE": "pepe",
    "AAVE": "aave",
    "BGB": "bitget-token",
    "OKB": "okb",
    "TAO": "bittensor",
    "ETC": "ethereum-classic",
    "ONDO": "ondo-finance",
    "ICP": "internet-computer",
    "POL": "polygon-ecosystem-token",
    "KAS": "kaspa",
    "CRO": "crypto-com-chain",
    "MNT": "mantle",
    "ATOM": "cosmos",
    "ALGO": "algorand",
    "VET": "vechain",
    "ENA": "ethena",
    "FIL": "filecoin",
    "RENDER": "render-token",
    "FET": "fetch-ai",
    "WLD": "worldcoin-wld",
    "RUNE": "thorchain",
    "JUP": "jupiter-exchange-solana",
    "BONK": "bonk",
    "MKR": "maker",
}

_CRYPTO_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "BNB": "BNB",
    "XRP": "XRP",
    "DOGE": "Dogecoin",
    "ADA": "Cardano",
    "TRX": "TRON",
    "AVAX": "Avalanche",
    "LINK": "Chainlink",
    "SUI": "Sui",
    "APT": "Aptos",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "NEAR": "Near",
    "TIA": "Celestia",
    "SEI": "Sei",
    "INJ": "Injective",
    "PENDLE": "Pendle",
    "TON": "Toncoin",
    "SHIB": "Shiba Inu",
    "HBAR": "Hedera",
    "LTC": "Litecoin",
    "DOT": "Polkadot",
    "UNI": "Uniswap",
    "XMR": "Monero",
    "PEPE": "Pepe",
    "AAVE": "Aave",
    "BGB": "Bitget Token",
    "OKB": "OKB",
    "TAO": "Bittensor",
    "ETC": "Ethereum Classic",
    "ONDO": "Ondo",
    "ICP": "Internet Computer",
    "POL": "Polygon",
    "KAS": "Kaspa",
    "CRO": "Cronos",
    "MNT": "Mantle",
    "ATOM": "Cosmos",
    "ALGO": "Algorand",
    "VET": "VeChain",
    "ENA": "Ethena",
    "FIL": "Filecoin",
    "RENDER": "Render",
    "FET": "Fetch.ai",
    "WLD": "Worldcoin",
    "RUNE": "THORChain",
    "JUP": "Jupiter",
    "BONK": "Bonk",
    "MKR": "Maker",
}
