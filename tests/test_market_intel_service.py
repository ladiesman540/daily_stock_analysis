# -*- coding: utf-8 -*-
"""Tests for resilient market intelligence aggregation."""

from __future__ import annotations

import os
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import requests

from src.search_service import SearchResponse, SearchResult
from src.services.market_intel_service import MarketIntelService


class FakeResponse:
    def __init__(self, status_code: int, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses: Dict[str, FakeResponse]):
        self.responses = responses
        self.calls: List[str] = []

    def get(self, url: str, params=None, headers=None, timeout=None):  # noqa: D401, ANN001
        if "alphavantage" in url:
            key = "alpha"
        elif "company_tickers" in url:
            key = "sec_tickers"
        elif "submissions" in url:
            key = "sec_submissions"
        else:
            key = url
        self.calls.append(key)
        return self.responses.get(key, FakeResponse(404, {}))


class FakeSearchService:
    is_available = True

    def search_comprehensive_intel(self, stock_code: str, stock_name: str, max_searches: int = 6):
        return {
            "latest_news": SearchResponse(
                query=f"{stock_name} {stock_code} latest news",
                provider="FakeSearch",
                success=True,
                results=[
                    SearchResult(
                        title=f"{stock_code} fresh catalyst",
                        snippet="Generic search fallback found a recent catalyst.",
                        url="https://example.com/news",
                        source="Example News",
                        published_date="2026-06-01",
                    )
                ],
            )
        }


class MarketIntelServiceTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        os.environ.pop("ALPHA_VANTAGE_API_KEY", None)
        os.environ.pop("ALPHAVANTAGE_API_KEY", None)
        os.environ.pop("MARKET_INTEL_ALLOW_PUBLIC_SEARXNG", None)

    def test_sec_filings_work_without_generic_search(self) -> None:
        session = FakeSession(
            {
                "sec_tickers": FakeResponse(200, {"0": {"ticker": "NVDA", "cik_str": 1045810}}),
                "sec_submissions": FakeResponse(
                    200,
                    {
                        "filings": {
                            "recent": {
                                "form": ["8-K", "10-Q", "4"],
                                "filingDate": ["2026-05-28", "2026-05-27", "2026-05-20"],
                                "reportDate": ["2026-05-27", "2026-04-30", ""],
                                "accessionNumber": ["0001045810-26-000123", "0001045810-26-000111", "x"],
                                "primaryDocument": ["nvda-20260527.htm", "nvda-20260430x10q.htm", "x.htm"],
                                "primaryDocDescription": ["Current report", "Quarterly report", "Ownership filing"],
                            }
                        }
                    },
                ),
            }
        )

        service = MarketIntelService(session=session, allow_generic_search=False)
        packet = service.get_stock_intel("NVDA", "NVIDIA", max_results=5)
        response = service.search_stock_news_response("NVDA", "NVIDIA", max_results=5)

        self.assertTrue(response.success)
        self.assertEqual(response.provider, "sec")
        self.assertIn("SEC EDGAR", response.results[0].source)
        self.assertIn("NVDA 8-K", response.results[0].title)
        self.assertIn("announcements", packet.dimensions)
        self.assertIn("earnings", packet.dimensions)
        quality = packet.data_quality()
        self.assertEqual(quality["official_sources_count"], 2)
        self.assertIn("generic_search", {p.name for p in packet.provider_health if p.status == "skipped"})

    def test_alpha_rate_limit_sets_cooldown_and_skips_second_call(self) -> None:
        os.environ["ALPHA_VANTAGE_API_KEY"] = "test-key"
        session = FakeSession(
            {
                "sec_tickers": FakeResponse(200, {}),
                "alpha": FakeResponse(429, {}, headers={"Retry-After": "7"}),
            }
        )
        service = MarketIntelService(session=session, allow_generic_search=False, provider_cooldown_s=60)

        first = service.get_stock_intel("NVDA", "NVIDIA", max_results=3)
        second = service.get_stock_intel("AAPL", "Apple", max_results=3)

        self.assertEqual(session.calls.count("alpha"), 1)
        degraded = [p for p in second.provider_health if p.name == "alpha_vantage" and p.status == "degraded"]
        self.assertTrue(degraded)
        self.assertIn("rate limited", degraded[0].message)
        self.assertIn("alpha_vantage", first.data_quality()["providers_degraded"])

    def test_generic_search_fallback_is_used_when_configured(self) -> None:
        session = FakeSession({"sec_tickers": FakeResponse(200, {})})
        service = MarketIntelService(
            session=session,
            search_service_factory=lambda: FakeSearchService(),
            allow_generic_search=True,
        )

        with patch.object(service, "_should_skip_public_searxng_only", return_value=False):
            response = service.search_stock_news_response("NVDA", "NVIDIA", max_results=3)

        self.assertTrue(response.success)
        self.assertEqual(response.results[0].source, "Example News")
        self.assertIn("generic_search", response.provider)


if __name__ == "__main__":
    unittest.main()
