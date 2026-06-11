# -*- coding: utf-8 -*-
"""Tests for positioning and gamma analysis."""

from __future__ import annotations

import unittest

from src.services.positioning_service import OptionContract, PositioningService


class FakeOptionsProvider:
    provider_name = "fake_options"

    def fetch_chain(self, symbol: str):
        return {
            "provider": "fake_options",
            "underlying_price": 125.0,
            "contracts": [
                OptionContract(
                    symbol=symbol,
                    expiration="2026-06-19",
                    strike=130.0,
                    option_type="call",
                    open_interest=2000,
                    volume=1500,
                    gamma=0.03,
                    implied_volatility=0.55,
                    provider="fake_options",
                    contract_symbol=f"{symbol}260619C00130000",
                ),
                OptionContract(
                    symbol=symbol,
                    expiration="2026-06-19",
                    strike=115.0,
                    option_type="put",
                    open_interest=1200,
                    volume=300,
                    gamma=0.025,
                    implied_volatility=0.60,
                    provider="fake_options",
                    contract_symbol=f"{symbol}260619P00115000",
                ),
            ],
            "diagnostics": [],
        }


class FakeShortProvider:
    def fetch(self, symbol: str):
        return {
            "short_interest": {
                "short_percent_float": 16.5,
                "short_ratio_days_to_cover": 2.1,
                "shares_short": 50_000_000,
            },
            "fails_to_deliver": {
                "latest_notional": 30_000_000,
                "latest_quantity": 250_000,
                "latest_settlement_date": "2026-05-15",
            },
            "diagnostics": [],
        }


class FakeYahooShortProvider(FakeShortProvider):
    def fetch(self, symbol: str):
        payload = super().fetch(symbol)
        payload["short_interest"]["source"] = "Yahoo Finance via yfinance"
        payload["fails_to_deliver"] = {}
        return payload


class FakeCotProvider:
    def fetch(self, symbol: str, asset_type: str = "stock"):
        return {
            "source": "CFTC public reporting",
            "market": "NASDAQ-100 STOCK INDEX",
            "report_date": "2026-05-26T00:00:00",
            "open_interest": 100_000,
            "noncommercial_long": 65_000,
            "noncommercial_short": 35_000,
            "noncommercial_net": 30_000,
            "noncommercial_net_oi_pct": 30.0,
            "diagnostics": [],
        }


class PositioningServiceTestCase(unittest.TestCase):
    def test_positioning_analysis_computes_gamma_and_crowding(self) -> None:
        service = PositioningService(
            options_provider=FakeOptionsProvider(),
            short_provider=FakeShortProvider(),
            cot_provider=FakeCotProvider(),
        )

        payload = service.analyze("nvda")

        self.assertEqual(payload["symbol"], "NVDA")
        self.assertEqual(payload["asset_type"], "stock")
        self.assertEqual(payload["gamma"]["gamma_regime"], "positive_gamma")
        self.assertEqual(payload["gamma"]["call_wall"]["strike"], 130.0)
        self.assertEqual(payload["gamma"]["put_wall"]["strike"], 115.0)
        self.assertAlmostEqual(payload["gamma"]["put_call_oi_ratio"], 0.6)
        self.assertGreater(payload["crowding"]["crowding_risk_score"], 70)
        self.assertEqual(payload["crowding"]["risk_level"], "high")
        self.assertEqual(payload["positioning_bias"], "squeeze-prone but crowded")
        self.assertEqual(payload["confidence"]["label"], "high")
        self.assertTrue(payload["what_to_watch"])
        self.assertFalse(payload["data_gaps"])

    def test_yfinance_only_gamma_does_not_report_high_confidence(self) -> None:
        class YFinanceOptionsProvider(FakeOptionsProvider):
            def fetch_chain(self, symbol: str):
                payload = super().fetch_chain(symbol)
                payload["provider"] = "yfinance"
                for contract in payload["contracts"]:
                    contract.provider = "yfinance"
                payload["diagnostics"] = ["Using delayed Yahoo/yfinance options data; Greeks are estimated from IV."]
                return payload

        service = PositioningService(
            options_provider=YFinanceOptionsProvider(),
            short_provider=FakeYahooShortProvider(),
            cot_provider=FakeCotProvider(),
        )

        payload = service.analyze("aapl")

        self.assertEqual(payload["gamma"]["provider_contracts"], ["yfinance"])
        self.assertEqual(payload["confidence"]["label"], "medium")
        self.assertLess(payload["confidence"]["score"], 75)

    def test_crypto_fails_open_with_explicit_data_gaps(self) -> None:
        service = PositioningService(
            options_provider=FakeOptionsProvider(),
            short_provider=FakeShortProvider(),
            cot_provider=FakeCotProvider(),
        )

        payload = service.analyze("btc", asset_type="crypto")

        self.assertEqual(payload["asset_type"], "crypto")
        self.assertIn("Equity option gamma is not available", " ".join(payload["data_gaps"]))
        self.assertEqual(payload["gamma"]["contract_count"], 0)


if __name__ == "__main__":
    unittest.main()
