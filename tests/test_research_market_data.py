# -*- coding: utf-8 -*-
"""Tests for research market data adapters."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from src.services.research_market_data import ResearchMarketDataService


class ResearchMarketDataServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ResearchMarketDataService()

    def test_default_us_universe_includes_etfs_and_watchlist(self) -> None:
        with patch.dict(os.environ, {"RESEARCH_US_WATCHLIST": "COHR, AAPL, custom"}, clear=False):
            symbols = self.service.get_default_us_universe(limit=100)

        self.assertIn("COHR", symbols)
        self.assertIn("SPY", symbols)
        self.assertIn("QQQ", symbols)
        self.assertIn("CUSTOM", symbols)
        self.assertEqual(len(symbols), len(set(symbols)))

    def test_massive_equity_history_parses_aggregate_bars(self) -> None:
        bars_response = MagicMock()
        bars_response.status_code = 200
        bars_response.json.return_value = {
            "results": [
                {"t": 1_766_966_400_000, "o": 100, "h": 104, "l": 99, "c": 103, "v": 1234567},
                {"t": 1_767_052_800_000, "o": 103, "h": 106, "l": 101, "c": 105, "v": 2345678},
            ]
        }
        profile_response = MagicMock()
        profile_response.status_code = 200
        profile_response.json.return_value = {
            "results": {"name": "Apple Inc.", "market_cap": 3_000_000_000_000, "type": "CS"}
        }

        with patch.dict(os.environ, {"MASSIVE_API_KEY": "test-key"}, clear=False), patch(
            "src.services.research_market_data.requests.get",
            side_effect=[bars_response, profile_response],
        ) as get:
            payload = self.service.get_us_equity_history("aapl", days=2)

        self.assertEqual(payload["source"], "massive")
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertEqual(payload["name"], "Apple Inc.")
        self.assertEqual(payload["market_cap"], 3_000_000_000_000)
        self.assertEqual(len(payload["bars"]), 2)
        self.assertEqual(payload["bars"][-1]["close"], 105)
        self.assertEqual(payload["data_quality"]["tier"], "primary_market_data")
        self.assertTrue(get.called)


if __name__ == "__main__":
    unittest.main()
