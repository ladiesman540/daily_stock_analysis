# -*- coding: utf-8 -*-
"""Tests for free data connectors."""

from __future__ import annotations

from datetime import date, timedelta
import unittest
from unittest.mock import MagicMock, patch

from src.services.free_data_service import (
    FRED_CSV_URL,
    NASDAQ_TRADED_URL,
    FreeDataService,
    _breadth_constituent_row,
    _market_breadth_score,
)


class FakeResponse:
    def __init__(self, *, status_code: int = 200, text: str = "", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_data


class FreeDataServiceTestCase(unittest.TestCase):
    def test_nasdaq_directory_parses_stocks_and_etfs(self) -> None:
        session = MagicMock()
        session.get.return_value = FakeResponse(
            text=(
                "Symbol|Security Name|Listing Exchange|Market Category|ETF|Test Issue|Financial Status|Round Lot Size|NextShares\n"
                "AAPL|Apple Inc.|Q|Q|N|N|N|100|N\n"
                "SPY|SPDR S&P 500 ETF|P| |Y|N| |100|N\n"
                "TEST|Test Issue|Q|Q|N|Y|N|100|N\n"
                "File Creation Time: 0602202618:03||||||||\n"
            )
        )
        service = FreeDataService(session=session)

        payload = service.get_us_universe(limit=10)

        self.assertEqual(payload["stocks"], 1)
        self.assertEqual(payload["etfs"], 1)
        self.assertEqual(payload["total"], 2)
        self.assertIn("AAPL", payload["symbols"])
        self.assertIn("SPY", payload["symbols"])
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], NASDAQ_TRADED_URL)

    def test_fred_macro_snapshot_parses_latest_values(self) -> None:
        session = MagicMock()
        session.get.return_value = FakeResponse(text="observation_date,DGS10\n2026-05-29,4.41\n2026-06-01,4.37\n")
        service = FreeDataService(session=session)

        item = service._fred_series_latest("DGS10", label="10Y", unit="%")

        self.assertEqual(item["status"], "ok")
        self.assertEqual(item["date"], "2026-06-01")
        self.assertEqual(item["value"], 4.37)
        self.assertEqual(session.get.call_args.args[0], FRED_CSV_URL)

    def test_sec_companyfacts_extracts_latest_metric_and_growth(self) -> None:
        session = MagicMock()
        session.get.side_effect = [
            FakeResponse(json_data={"0": {"ticker": "AAPL", "cik_str": 320193}}),
            FakeResponse(
                json_data={
                    "entityName": "Apple Inc.",
                    "facts": {
                        "us-gaap": {
                            "RevenueFromContractWithCustomerExcludingAssessedTax": {
                                "units": {
                                    "USD": [
                                        {"val": 90, "end": "2025-03-31", "fy": 2025, "fp": "Q2", "form": "10-Q", "filed": "2025-05-01"},
                                        {"val": 100, "end": "2026-03-31", "fy": 2026, "fp": "Q2", "form": "10-Q", "filed": "2026-05-01"},
                                    ]
                                }
                            }
                        }
                    },
                }
            ),
        ]
        service = FreeDataService(session=session)

        payload = service.get_fundamentals("aapl")

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["entity_name"], "Apple Inc.")
        revenue = payload["metrics"][0]
        self.assertEqual(revenue["key"], "revenue")
        self.assertEqual(revenue["value"], 100)
        self.assertEqual(revenue["growth_pct"], 11.11)

    def test_finra_short_volume_parses_recent_symbol_row(self) -> None:
        session = MagicMock()
        session.get.return_value = FakeResponse(
            text=(
                "Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market\n"
                "20260601|AAPL|400|5|1000|B,Q,N\n"
            )
        )
        service = FreeDataService(session=session)

        payload = service.get_finra_short_volume("aapl", lookback_days=1)

        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["short_volume"], 400)
        self.assertEqual(payload["short_volume_ratio"], 40.0)
        self.assertIn("not short interest", payload["caveat"])

    def test_breadth_constituent_applies_history_and_liquidity_gates(self) -> None:
        bars = [
            {
                "date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
                "close": 50 + index,
                "volume": 1_000_000,
            }
            for index in range(260)
        ]

        row = _breadth_constituent_row(
            "TEST",
            {"symbol": "TEST", "source": "massive", "bars": bars},
            min_price=5,
            min_avg_dollar_volume=10_000_000,
        )

        self.assertEqual(row["status"], "ok")
        self.assertTrue(row["history_pass"])
        self.assertTrue(row["liquidity_pass"])
        self.assertTrue(row["above_sma20"])
        self.assertTrue(row["above_sma50"])
        self.assertTrue(row["above_sma200"])

    def test_market_breadth_cache_run_saves_percent_above_moving_averages(self) -> None:
        class FakeMarketData:
            def get_us_equity_history(self, symbol: str, days: int = 260):
                start = 100 if symbol == "AAA" else 260
                direction = 1 if symbol == "AAA" else -1
                bars = [
                    {
                        "date": (date(2025, 1, 1) + timedelta(days=index)).isoformat(),
                        "close": start + direction * index * 0.5,
                        "volume": 2_000_000,
                    }
                    for index in range(260)
                ]
                return {"symbol": symbol, "source": "massive", "bars": bars}

            def get_default_us_universe(self, limit: int = 100):
                return ["AAA", "BBB"]

        fake_db = MagicMock()
        fake_db.save_market_breadth_cache.side_effect = lambda payload: {**payload, "id": 1}
        service = FreeDataService(session=MagicMock(), cache_enabled=False)
        service.market_data = FakeMarketData()

        with patch.object(
            service,
            "_breadth_universe",
            return_value=(["AAA", "BBB"], {"label": "test", "source": "unit test", "total_available": 2, "warnings": []}),
        ), patch("src.services.free_data_service.DatabaseManager.get_instance", return_value=fake_db):
            payload = service.run_market_breadth_cache(limit=2, min_price=5, min_avg_dollar_volume=1_000_000)

        self.assertEqual(payload["symbols_passing_liquidity"], 2)
        self.assertEqual(payload["above_sma50_pct"], 50.0)
        self.assertEqual(payload["above_sma200_pct"], 50.0)
        self.assertEqual(_market_breadth_score(payload), 50.0)
        fake_db.save_market_breadth_cache.assert_called_once()


if __name__ == "__main__":
    unittest.main()
