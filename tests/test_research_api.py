# -*- coding: utf-8 -*-
"""API tests for research signals and source ingestion."""

from __future__ import annotations

import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

try:
    from fastapi.testclient import TestClient

    import src.auth as auth
    from api.app import create_app
    from src.config import Config
    from src.storage import DatabaseManager

    _API_IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    TestClient = None
    auth = None
    create_app = None
    Config = None
    DatabaseManager = None
    _API_IMPORT_ERROR = exc


def _reset_auth_globals() -> None:
    if auth is None:
        return
    auth._auth_enabled = None
    auth._session_secret = None
    auth._password_hash_salt = None
    auth._password_hash_stored = None
    auth._rate_limit = {}


def _bars():
    closes = []
    for idx in range(260):
        base = 60 * (1.003 ** idx)
        if idx >= 197:
            base = closes[196] * (1 + 0.22 * ((idx - 196) / 63))
        if idx >= 239:
            base = closes[238] * (1 + 0.10 * ((idx - 238) / 21))
        closes.append(base * (1 + 0.012 * math.sin(idx * 1.7)))
    return [
        {
            "date": f"2025-01-{(idx % 28) + 1:02d}",
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": 1_000_000,
        }
        for idx, close in enumerate(closes)
    ]


class FakeResearchMarketData:
    def get_us_equity_history(self, symbol: str):
        return {
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "asset_type": "stock",
            "market_cap": 5_000_000_000,
            "bars": _bars(),
        }

    def get_crypto_history(self, symbol: str):
        return {
            "symbol": symbol.upper(),
            "name": symbol.upper(),
            "asset_type": "crypto",
            "market_cap": 10_000_000_000,
            "bars": _bars(),
            "missing_data": ["funding", "open_interest"],
        }


@unittest.skipIf(_API_IMPORT_ERROR is not None, f"backend API dependencies unavailable: {_API_IMPORT_ERROR}")
class ResearchApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        _reset_auth_globals()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.env_path = self.data_dir / ".env"
        self.db_path = self.data_dir / "research_api_test.db"
        self.env_path.write_text(
            "\n".join(
                [
                    "STOCK_LIST=NVDA",
                    "GEMINI_API_KEY=test",
                    "ADMIN_AUTH_ENABLED=false",
                    f"DATABASE_PATH={self.db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ.pop("X_CLIENT_ID", None)
        os.environ.pop("X_REDIRECT_URI", None)
        Config.reset_instance()
        DatabaseManager.reset_instance()
        app = create_app(static_dir=self.data_dir / "empty-static")
        self.client = TestClient(app)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("X_CLIENT_ID", None)
        os.environ.pop("X_REDIRECT_URI", None)
        self.temp_dir.cleanup()

    def test_manual_idea_round_trip_extracts_mentions(self) -> None:
        create_resp = self.client.post(
            "/api/v1/research/ideas",
            json={"content": "$NVDA grinding leader with AI capex catalyst and Bitcoin beta.", "title": "Manual idea"},
        )
        self.assertEqual(create_resp.status_code, 200)
        created = create_resp.json()
        symbols = {mention["asset_symbol"] for mention in created["mentions"]}
        self.assertIn("NVDA", symbols)
        self.assertIn("BTC", symbols)

        list_resp = self.client.get("/api/v1/research/ideas", params={"symbol": "NVDA"})
        self.assertEqual(list_resp.status_code, 200)
        self.assertEqual(len(list_resp.json()["items"]), 1)

    def test_x_status_and_sync_fail_open_without_connection(self) -> None:
        status_resp = self.client.get("/api/v1/research/x/status")
        self.assertEqual(status_resp.status_code, 200)
        status = status_resp.json()
        self.assertFalse(status["configured"])
        self.assertFalse(status["connected"])

        sync_resp = self.client.post("/api/v1/research/x/sync", json={"max_pages": 1})
        self.assertEqual(sync_resp.status_code, 200)
        payload = sync_resp.json()
        self.assertFalse(payload["success"])
        self.assertEqual(payload["status"], "not_connected")

    def test_x_oauth_start_returns_configured_auth_url(self) -> None:
        os.environ["X_CLIENT_ID"] = "client-123"
        os.environ["X_REDIRECT_URI"] = "http://127.0.0.1:8000/api/v1/research/x/oauth/callback"

        resp = self.client.post("/api/v1/research/x/oauth/start")
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["configured"])
        self.assertIn("client-123", payload["auth_url"])
        self.assertIn("code_challenge", payload["auth_url"])

    def test_weekly_signal_run_uses_mocked_market_data(self) -> None:
        with patch("src.services.research_service.ResearchMarketDataService", FakeResearchMarketData):
            resp = self.client.post(
                "/api/v1/research/signal-runs",
                json={"symbols": ["NVDA"], "crypto_symbols": ["BTC"], "include_us": True, "include_crypto": True},
            )

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(len(payload["candidates"]), 2)
        self.assertIsNotNone(payload["rotation_memo"])
        self.assertEqual({item["symbol"] for item in payload["candidates"]}, {"NVDA", "BTC"})
        self.assertTrue(all(item["checklist_status"] == "pass" for item in payload["candidates"]))

        memo_resp = self.client.get("/api/v1/research/rotation-memos/latest")
        self.assertEqual(memo_resp.status_code, 200)
        memo = memo_resp.json()["memo"]
        self.assertIsNotNone(memo)
        self.assertIn("Weekly rotation scan", memo["summary"])

    def test_positioning_endpoint_returns_gamma_context(self) -> None:
        fake_service = MagicMock()
        fake_service.analyze.return_value = {
            "symbol": "NVDA",
            "asset_type": "stock",
            "as_of": "2026-06-02T00:00:00Z",
            "underlying_price": 125.0,
            "positioning_bias": "balanced positive-gamma",
            "confidence": {"score": 80, "label": "high"},
            "gamma": {
                "contract_count": 2,
                "contracts_with_gamma": 2,
                "gamma_regime": "positive_gamma",
                "call_wall": {"strike": 130.0},
                "put_wall": {"strike": 115.0},
            },
            "crowding": {"crowding_risk_score": 35, "risk_level": "low", "flags": []},
            "short_pressure": {"short_interest": {}, "fails_to_deliver": {}},
            "cot_macro_context": {},
            "sources": [],
            "data_gaps": [],
            "what_to_watch": ["Watch the call wall."],
            "methodology": ["OI-implied gamma estimate."],
        }

        with patch("api.v1.endpoints.research.PositioningService", return_value=fake_service):
            resp = self.client.get("/api/v1/research/positioning/nvda", params={"asset_type": "stock"})

        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertEqual(payload["symbol"], "NVDA")
        self.assertEqual(payload["gamma"]["gamma_regime"], "positive_gamma")
        fake_service.analyze.assert_called_once_with("nvda", asset_type="stock")

    def test_market_breadth_endpoints_return_latest_and_run_cache(self) -> None:
        fake_service = MagicMock()
        latest_payload = {
            "status": "completed",
            "universe": "us_stocks",
            "generated_at": "2026-06-04T20:00:00Z",
            "as_of": "2026-06-03",
            "summary": "Daily breadth cache ready.",
            "symbols_requested": 2,
            "symbols_scanned": 2,
            "symbols_with_data": 2,
            "symbols_passing_liquidity": 2,
            "above_sma20_count": 1,
            "above_sma20_pct": 50.0,
            "above_sma50_count": 1,
            "above_sma50_pct": 50.0,
            "above_sma200_count": 1,
            "above_sma200_pct": 50.0,
            "new_high_52w_count": 1,
            "new_high_52w_pct": 50.0,
            "new_low_52w_count": 0,
            "new_low_52w_pct": 0.0,
            "advancers_count": 1,
            "decliners_count": 1,
            "source_counts": {"massive": 2},
            "calculation_steps": [{"step": "Breadth math"}],
            "sample_constituents": [],
            "failures": [],
            "warnings": [],
        }
        fake_service.get_latest_market_breadth.return_value = latest_payload
        fake_service.run_market_breadth_cache.return_value = {**latest_payload, "id": 1}

        with patch("api.v1.endpoints.research.FreeDataService", return_value=fake_service):
            latest_resp = self.client.get("/api/v1/research/free-data/market-breadth/latest")
            run_resp = self.client.post(
                "/api/v1/research/free-data/market-breadth/run",
                json={"universe": "us_stocks", "limit": 2},
            )

        self.assertEqual(latest_resp.status_code, 200)
        self.assertEqual(latest_resp.json()["above_sma50_pct"], 50.0)
        self.assertEqual(run_resp.status_code, 200)
        self.assertEqual(run_resp.json()["id"], 1)
        fake_service.get_latest_market_breadth.assert_called_once_with(universe="us_etf_proxy")
        fake_service.run_market_breadth_cache.assert_called_once_with(
            universe="us_stocks",
            limit=2,
            min_price=None,
            min_avg_dollar_volume=None,
        )


if __name__ == "__main__":
    unittest.main()
