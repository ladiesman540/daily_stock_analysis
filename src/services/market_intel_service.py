# -*- coding: utf-8 -*-
"""Resilient market intelligence aggregation for analyst workflows.

The app should not depend on one generic search provider for current
investment context. This service gathers typed evidence first (official
filings and optional finance news APIs), then uses the existing generic
search stack as a last resort.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests

from src.search_service import SearchResponse, SearchResult

logger = logging.getLogger(__name__)

_US_EQUITY_RE = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")
_SEC_FORMS_OF_INTEREST = {"8-K", "10-Q", "10-K", "6-K", "20-F", "40-F"}


@dataclass
class ProviderHealth:
    """Current health status for one intel provider."""

    name: str
    status: str
    message: str = ""
    retry_after_s: Optional[int] = None


@dataclass
class MarketIntelPacket:
    """Structured evidence packet passed to search tools and LLM agents."""

    symbol: str
    name: str
    query: str
    dimensions: Dict[str, SearchResponse] = field(default_factory=dict)
    provider_health: List[ProviderHealth] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)

    @property
    def results(self) -> List[SearchResult]:
        seen: set[str] = set()
        merged: List[SearchResult] = []
        for dimension in ("latest_news", "announcements", "earnings", "risk_check", "market_analysis", "industry"):
            response = self.dimensions.get(dimension)
            if not response or not response.results:
                continue
            for item in response.results:
                key = (item.url or item.title or "").strip().lower()
                if not key or key in seen:
                    continue
                seen.add(key)
                merged.append(item)
        return merged

    def data_quality(self) -> Dict[str, Any]:
        providers_ok = [p.name for p in self.provider_health if p.status == "ok"]
        providers_degraded = [p.name for p in self.provider_health if p.status == "degraded"]
        official_count = sum(1 for item in self.results if "sec" in item.source.lower())
        article_count = len(self.results)
        if article_count == 0:
            confidence = "low"
        elif official_count and len(providers_ok) <= 1:
            confidence = "medium"
        elif providers_degraded:
            confidence = "medium"
        else:
            confidence = "high"
        return {
            "confidence": confidence,
            "results_count": article_count,
            "official_sources_count": official_count,
            "providers_ok": providers_ok,
            "providers_degraded": providers_degraded,
            "diagnostics": list(self.diagnostics),
        }


class MarketIntelService:
    """Gather current market evidence with typed-source priority and fail-open search."""

    def __init__(
        self,
        *,
        session: Optional[requests.Session] = None,
        search_service_factory: Optional[Callable[[], Any]] = None,
        cache_ttl_s: int = 1800,
        provider_cooldown_s: int = 300,
        allow_generic_search: Optional[bool] = None,
    ):
        self.session = session or requests.Session()
        self.search_service_factory = search_service_factory
        self.cache_ttl_s = max(30, int(cache_ttl_s))
        self.provider_cooldown_s = max(30, int(provider_cooldown_s))
        self.allow_generic_search = (
            _env_bool("MARKET_INTEL_ALLOW_GENERIC_SEARCH", True)
            if allow_generic_search is None
            else bool(allow_generic_search)
        )
        self._cache: Dict[str, Tuple[float, MarketIntelPacket]] = {}
        self._cache_lock = threading.RLock()
        self._cooldowns: Dict[str, Tuple[float, str]] = {}
        self._sec_ticker_cache: Optional[Dict[str, int]] = None
        self._sec_ticker_cache_at = 0.0

    def search_stock_news_response(
        self,
        stock_code: str,
        stock_name: str,
        *,
        max_results: int = 5,
    ) -> SearchResponse:
        """Return news-like evidence while preserving the legacy SearchResponse API."""

        packet = self.get_stock_intel(stock_code, stock_name, max_results=max_results)
        return self._packet_to_search_response(packet, max_results=max_results)

    def search_comprehensive_intel(
        self,
        stock_code: str,
        stock_name: str,
        *,
        max_searches: int = 6,
    ) -> Dict[str, SearchResponse]:
        """Return dimensioned intel compatible with the legacy comprehensive tool."""

        packet = self.get_stock_intel(stock_code, stock_name, max_results=max(3, max_searches))
        return packet.dimensions

    def format_intel_report(self, intel_results: Dict[str, SearchResponse], stock_name: str) -> str:
        """Format dimensioned intel in English for LLM consumption."""

        labels = {
            "latest_news": "Latest News",
            "announcements": "Official Filings / Announcements",
            "earnings": "Earnings / Financials",
            "risk_check": "Risk Check",
            "market_analysis": "Market Analysis",
            "industry": "Industry / Theme",
        }
        lines = [f"{stock_name} market intelligence evidence"]
        for key in ("latest_news", "announcements", "earnings", "risk_check", "market_analysis", "industry"):
            response = intel_results.get(key)
            if not response:
                continue
            lines.append(f"\n{labels.get(key, key)} (provider: {response.provider})")
            if response.success and response.results:
                for index, item in enumerate(response.results[:4], 1):
                    date_text = f" [{item.published_date}]" if item.published_date else ""
                    lines.append(f"{index}. {item.title}{date_text}")
                    if item.snippet:
                        lines.append(f"   {item.snippet[:240]}")
                    if item.url:
                        lines.append(f"   URL: {item.url}")
            elif response.error_message:
                lines.append(f"   No usable evidence: {response.error_message}")
            else:
                lines.append("   No usable evidence found.")
        return "\n".join(lines)

    def get_stock_intel(
        self,
        stock_code: str,
        stock_name: str,
        *,
        max_results: int = 5,
    ) -> MarketIntelPacket:
        """Build a typed evidence packet for a stock or ETF."""

        symbol = (stock_code or "").strip().upper()
        name = (stock_name or symbol).strip() or symbol
        max_results = max(1, int(max_results))
        cache_key = f"stock:{symbol}:{name.lower()}:{max_results}"

        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        packet = MarketIntelPacket(
            symbol=symbol,
            name=name,
            query=f"{name} {symbol} current market intelligence",
        )

        if self._is_us_equity(symbol):
            sec_response = self._sec_filings_response(symbol, max_results=max_results)
            packet.dimensions["announcements"] = sec_response
            if sec_response.success and sec_response.results:
                packet.dimensions["earnings"] = SearchResponse(
                    query=sec_response.query,
                    results=[
                        item
                        for item in sec_response.results
                        if any(form in item.title for form in ("10-Q", "10-K", "20-F", "40-F"))
                    ][:max_results]
                    or sec_response.results[:max_results],
                    provider=sec_response.provider,
                    success=True,
                )

        alpha_response = self._alpha_vantage_news_response(symbol, max_results=max_results)
        if alpha_response is not None:
            packet.dimensions["latest_news"] = alpha_response

        fallback_dimensions = self._generic_search_dimensions(symbol, name, max_searches=max_results)
        for key, response in fallback_dimensions.items():
            if key == "latest_news" and packet.dimensions.get("latest_news", SearchResponse("", [], "")).results:
                packet.dimensions.setdefault("market_analysis", response)
                continue
            packet.dimensions.setdefault(key, response)

        if "latest_news" not in packet.dimensions:
            announcement_fallback = packet.dimensions.get("announcements")
            if announcement_fallback is not None:
                packet.dimensions["latest_news"] = announcement_fallback
            else:
                packet.dimensions["latest_news"] = SearchResponse(
                    query=packet.query,
                    results=[],
                    provider="MarketIntel",
                    success=False,
                    error_message="No configured live news provider returned evidence.",
                )

        packet.provider_health = self._current_provider_health()
        packet.diagnostics = [
            health.message
            for health in packet.provider_health
            if health.status in {"degraded", "skipped"} and health.message
        ]
        self._put_cache(cache_key, packet)
        return packet

    def _sec_filings_response(self, symbol: str, *, max_results: int) -> SearchResponse:
        query = f"{symbol} SEC latest filings"
        provider = "SEC EDGAR"
        if self._provider_in_cooldown("sec"):
            return SearchResponse(query=query, results=[], provider=provider, success=False, error_message="SEC provider is cooling down.")

        try:
            cik = self._sec_cik_for_symbol(symbol)
            if not cik:
                return SearchResponse(query=query, results=[], provider=provider, success=False, error_message=f"No SEC CIK found for {symbol}.")
            cik_padded = str(cik).zfill(10)
            data = self._get_json(
                "sec",
                f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
                headers=self._sec_headers(),
                timeout=12,
            )
            recent = (data.get("filings") or {}).get("recent") or {}
            results = self._sec_results_from_recent(symbol, cik, recent, max_results=max_results)
            self._mark_provider_ok("sec")
            return SearchResponse(
                query=query,
                results=results,
                provider=provider,
                success=bool(results),
                error_message=None if results else "SEC returned no recent filings of interest.",
            )
        except Exception as exc:
            self._mark_provider_degraded("sec", str(exc))
            logger.info("SEC filings fetch failed for %s: %s", symbol, exc)
            return SearchResponse(query=query, results=[], provider=provider, success=False, error_message=str(exc))

    def _alpha_vantage_news_response(self, symbol: str, *, max_results: int) -> Optional[SearchResponse]:
        api_key = os.getenv("ALPHA_VANTAGE_API_KEY") or os.getenv("ALPHAVANTAGE_API_KEY")
        if not api_key:
            try:
                from src.config import get_config

                api_key = getattr(get_config(), "alpha_vantage_api_key", None)
            except Exception:
                api_key = None
        if not api_key:
            self._mark_provider_skipped("alpha_vantage", "Alpha Vantage news skipped: ALPHA_VANTAGE_API_KEY is not configured.")
            return None
        query = f"{symbol} market news sentiment"
        provider = "Alpha Vantage"
        if self._provider_in_cooldown("alpha_vantage"):
            return SearchResponse(query=query, results=[], provider=provider, success=False, error_message="Alpha Vantage is cooling down.")
        try:
            data = self._get_json(
                "alpha_vantage",
                "https://www.alphavantage.co/query",
                params={
                    "function": "NEWS_SENTIMENT",
                    "tickers": symbol,
                    "sort": "LATEST",
                    "limit": max_results,
                    "apikey": api_key,
                },
                timeout=12,
            )
            if data.get("Information") or data.get("Note"):
                message = str(data.get("Information") or data.get("Note"))
                self._mark_provider_degraded("alpha_vantage", message)
                return SearchResponse(query=query, results=[], provider=provider, success=False, error_message=message)
            results: List[SearchResult] = []
            for item in data.get("feed") or []:
                title = str(item.get("title") or "").strip()
                url = str(item.get("url") or "").strip()
                if not title or not url:
                    continue
                published = _alpha_time_to_date(item.get("time_published"))
                results.append(
                    SearchResult(
                        title=title,
                        snippet=str(item.get("summary") or "").strip(),
                        url=url,
                        source=str(item.get("source") or "Alpha Vantage").strip() or "Alpha Vantage",
                        published_date=published,
                    )
                )
                if len(results) >= max_results:
                    break
            self._mark_provider_ok("alpha_vantage")
            return SearchResponse(
                query=query,
                results=results,
                provider=provider,
                success=bool(results),
                error_message=None if results else "Alpha Vantage returned no articles.",
            )
        except Exception as exc:
            self._mark_provider_degraded("alpha_vantage", str(exc))
            logger.info("Alpha Vantage news fetch failed for %s: %s", symbol, exc)
            return SearchResponse(query=query, results=[], provider=provider, success=False, error_message=str(exc))

    def _generic_search_dimensions(self, symbol: str, name: str, *, max_searches: int) -> Dict[str, SearchResponse]:
        if not self.allow_generic_search:
            self._mark_provider_skipped("generic_search", "Generic web search skipped by MARKET_INTEL_ALLOW_GENERIC_SEARCH=false.")
            return {}
        if self._should_skip_public_searxng_only():
            self._mark_provider_skipped(
                "generic_search",
                "Generic web search skipped: only public SearXNG is configured, which is too unreliable for analyst evidence.",
            )
            return {}
        if self._provider_in_cooldown("generic_search"):
            return {}
        try:
            search_service = self._get_search_service()
            if not getattr(search_service, "is_available", False):
                self._mark_provider_skipped("generic_search", "Generic web search skipped: no search provider is available.")
                return {}
            dimensions = search_service.search_comprehensive_intel(
                stock_code=symbol,
                stock_name=name,
                max_searches=max_searches,
            )
            if dimensions:
                self._mark_provider_ok("generic_search")
            else:
                self._mark_provider_degraded("generic_search", "Generic web search returned no dimensions.")
            return dimensions or {}
        except Exception as exc:
            self._mark_provider_degraded("generic_search", str(exc))
            logger.info("Generic search intel failed for %s: %s", symbol, exc)
            return {}

    def _get_search_service(self) -> Any:
        if self.search_service_factory is not None:
            return self.search_service_factory()
        from src.search_service import get_search_service

        return get_search_service()

    def _should_skip_public_searxng_only(self) -> bool:
        if _env_bool("MARKET_INTEL_ALLOW_PUBLIC_SEARXNG", False):
            return False
        try:
            from src.config import get_config

            config = get_config()
            has_keyed_provider = any(
                [
                    getattr(config, "bocha_api_keys", []),
                    getattr(config, "tavily_api_keys", []),
                    getattr(config, "anspire_api_keys", []),
                    getattr(config, "brave_api_keys", []),
                    getattr(config, "serpapi_keys", []),
                    getattr(config, "minimax_api_keys", []),
                    getattr(config, "searxng_base_urls", []),
                ]
            )
            return bool(not has_keyed_provider and getattr(config, "searxng_public_instances_enabled", False))
        except Exception:
            return False

    def _sec_cik_for_symbol(self, symbol: str) -> Optional[int]:
        tickers = self._sec_company_tickers()
        return tickers.get(symbol.upper())

    def _sec_company_tickers(self) -> Dict[str, int]:
        now = time.time()
        if self._sec_ticker_cache is not None and now - self._sec_ticker_cache_at < 86400:
            return self._sec_ticker_cache
        data = self._get_json(
            "sec",
            "https://www.sec.gov/files/company_tickers.json",
            headers=self._sec_headers(),
            timeout=12,
        )
        mapping: Dict[str, int] = {}
        rows: Iterable[Any]
        rows = data.values() if isinstance(data, dict) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").strip().upper()
            cik = row.get("cik_str")
            if ticker and cik is not None:
                try:
                    mapping[ticker] = int(cik)
                except (TypeError, ValueError):
                    continue
        self._sec_ticker_cache = mapping
        self._sec_ticker_cache_at = now
        return mapping

    def _get_json(
        self,
        provider: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 12,
    ) -> Dict[str, Any]:
        if self._provider_in_cooldown(provider):
            raise RuntimeError(f"{provider} provider is cooling down")
        response = self.session.get(url, params=params, headers=headers, timeout=timeout)
        if response.status_code == 429:
            retry_after = _retry_after_seconds(response.headers.get("Retry-After")) or self.provider_cooldown_s
            self._mark_provider_degraded(provider, f"{provider} rate limited with HTTP 429", retry_after_s=retry_after)
            raise RuntimeError(f"{provider} rate limited with HTTP 429")
        if response.status_code in {403, 408, 500, 502, 503, 504}:
            self._mark_provider_degraded(provider, f"{provider} HTTP {response.status_code}")
            raise RuntimeError(f"{provider} HTTP {response.status_code}")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"{provider} returned non-object JSON")
        return payload

    @staticmethod
    def _sec_results_from_recent(symbol: str, cik: int, recent: Dict[str, Any], *, max_results: int) -> List[SearchResult]:
        forms = recent.get("form") or []
        filing_dates = recent.get("filingDate") or []
        report_dates = recent.get("reportDate") or []
        accessions = recent.get("accessionNumber") or []
        docs = recent.get("primaryDocument") or []
        descriptions = recent.get("primaryDocDescription") or []
        results: List[SearchResult] = []
        cik_path = str(int(cik))
        for index, form in enumerate(forms):
            form_text = str(form or "").strip().upper()
            if form_text not in _SEC_FORMS_OF_INTEREST:
                continue
            accession = _list_get(accessions, index)
            primary_doc = _list_get(docs, index)
            filing_date = _list_get(filing_dates, index)
            report_date = _list_get(report_dates, index)
            description = _list_get(descriptions, index) or form_text
            if not accession or not primary_doc:
                continue
            accession_path = str(accession).replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_path}/{accession_path}/{primary_doc}"
            snippet = f"Official SEC filing for {symbol}. Form {form_text} filed {filing_date or 'unknown date'}."
            if report_date:
                snippet += f" Report date: {report_date}."
            results.append(
                SearchResult(
                    title=f"{symbol} {form_text}: {description}",
                    snippet=snippet,
                    url=url,
                    source="SEC EDGAR",
                    published_date=str(filing_date or "") or None,
                )
            )
            if len(results) >= max_results:
                break
        return results

    def _packet_to_search_response(self, packet: MarketIntelPacket, *, max_results: int) -> SearchResponse:
        results = packet.results[:max_results]
        quality = packet.data_quality()
        diagnostics = "; ".join(quality["diagnostics"][:3])
        provider_names = quality["providers_ok"] or ["MarketIntel"]
        return SearchResponse(
            query=packet.query,
            results=results,
            provider=", ".join(provider_names),
            success=bool(results),
            error_message=None if results else diagnostics or "No market intelligence evidence found.",
        )

    def _get_cached(self, key: str) -> Optional[MarketIntelPacket]:
        with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            ts, packet = entry
            if time.time() - ts > self.cache_ttl_s:
                self._cache.pop(key, None)
                return None
            return packet

    def _put_cache(self, key: str, packet: MarketIntelPacket) -> None:
        with self._cache_lock:
            if len(self._cache) > 250:
                oldest = sorted(self._cache, key=lambda item: self._cache[item][0])[:50]
                for item in oldest:
                    self._cache.pop(item, None)
            self._cache[key] = (time.time(), packet)

    @staticmethod
    def _is_us_equity(symbol: str) -> bool:
        return bool(_US_EQUITY_RE.match((symbol or "").upper()))

    @staticmethod
    def _sec_headers() -> Dict[str, str]:
        user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        if not user_agent:
            user_agent = "daily-stock-analysis/1.0 contact@example.com"
        return {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        }

    def _provider_in_cooldown(self, provider: str) -> bool:
        entry = self._cooldowns.get(provider)
        if not entry:
            return False
        until, _ = entry
        if time.time() >= until:
            self._cooldowns.pop(provider, None)
            return False
        return True

    def _mark_provider_ok(self, provider: str) -> None:
        self._cooldowns.pop(provider, None)

    def _mark_provider_degraded(self, provider: str, message: str, *, retry_after_s: Optional[int] = None) -> None:
        retry_after = retry_after_s or self.provider_cooldown_s
        self._cooldowns[provider] = (time.time() + retry_after, message)

    def _mark_provider_skipped(self, provider: str, message: str) -> None:
        self._cooldowns.setdefault(provider, (0.0, message))

    def _current_provider_health(self) -> List[ProviderHealth]:
        providers = ["sec", "alpha_vantage", "generic_search"]
        health: List[ProviderHealth] = []
        now = time.time()
        for provider in providers:
            entry = self._cooldowns.get(provider)
            if not entry:
                health.append(ProviderHealth(name=provider, status="ok"))
                continue
            until, message = entry
            if until <= 0:
                health.append(ProviderHealth(name=provider, status="skipped", message=message))
            elif until > now:
                health.append(
                    ProviderHealth(
                        name=provider,
                        status="degraded",
                        message=message,
                        retry_after_s=max(1, int(until - now)),
                    )
                )
            else:
                health.append(ProviderHealth(name=provider, status="ok"))
        return health


_market_intel_service: Optional[MarketIntelService] = None
_market_intel_lock = threading.Lock()


def get_market_intel_service() -> MarketIntelService:
    """Return the shared MarketIntelService singleton."""

    global _market_intel_service
    if _market_intel_service is None:
        with _market_intel_lock:
            if _market_intel_service is None:
                _market_intel_service = MarketIntelService()
    return _market_intel_service


def reset_market_intel_service() -> None:
    """Reset singleton state for tests and config reloads."""

    global _market_intel_service
    with _market_intel_lock:
        _market_intel_service = None


def _alpha_time_to_date(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text[:15], "%Y%m%dT%H%M%S").date().isoformat()
    except ValueError:
        return None


def _list_get(values: Any, index: int) -> Optional[str]:
    if isinstance(values, list) and index < len(values):
        value = values[index]
        if value is None:
            return None
        return str(value)
    return None


def _retry_after_seconds(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}
