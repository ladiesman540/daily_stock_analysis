# -*- coding: utf-8 -*-
"""Tests for WatchlistSymbol model, DatabaseManager watchlist methods, WatchlistService."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager, WatchlistSymbol


def _make_db(tmp_dir: str) -> DatabaseManager:
    """Create an isolated in-file SQLite DatabaseManager for testing."""
    db_path = os.path.join(tmp_dir, "test_watchlist.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


class TestWatchlistDbMethods(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.tmp.cleanup()

    def test_empty_initially(self) -> None:
        rows = self.db.get_watchlist_symbols()
        self.assertEqual(rows, [])

    def test_add_and_get(self) -> None:
        result = self.db.add_watchlist_symbol("AAPL", note="flagship", source="manual")
        self.assertEqual(result["symbol"], "AAPL")
        self.assertEqual(result["note"], "flagship")
        self.assertEqual(result["source"], "manual")

        rows = self.db.get_watchlist_symbols()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "AAPL")

    def test_add_uppercase_normalisation(self) -> None:
        self.db.add_watchlist_symbol("msft")
        rows = self.db.get_watchlist_symbols()
        self.assertEqual(rows[0]["symbol"], "MSFT")

    def test_add_idempotent(self) -> None:
        """Adding the same symbol twice should not raise and should not duplicate."""
        self.db.add_watchlist_symbol("NVDA")
        self.db.add_watchlist_symbol("NVDA")
        rows = self.db.get_watchlist_symbols()
        self.assertEqual(len(rows), 1)

    def test_remove_existing(self) -> None:
        self.db.add_watchlist_symbol("TSLA")
        removed = self.db.remove_watchlist_symbol("TSLA")
        self.assertTrue(removed)
        self.assertEqual(self.db.get_watchlist_symbols(), [])

    def test_remove_nonexistent(self) -> None:
        removed = self.db.remove_watchlist_symbol("NONEXISTENT")
        self.assertFalse(removed)

    def test_order_by_created_at_then_symbol(self) -> None:
        for sym in ["ZZZ", "AAA", "MMM"]:
            self.db.add_watchlist_symbol(sym)
        rows = self.db.get_watchlist_symbols()
        symbols = [r["symbol"] for r in rows]
        # Verify stable ordering by created_at, then symbol
        # Inserted in order ZZZ, AAA, MMM; returns in insertion order by created_at
        self.assertEqual(symbols, ["ZZZ", "AAA", "MMM"])

    def test_remove_case_insensitive(self) -> None:
        self.db.add_watchlist_symbol("AMZN")
        removed = self.db.remove_watchlist_symbol("amzn")
        self.assertTrue(removed)
        self.assertEqual(self.db.get_watchlist_symbols(), [])


class TestWatchlistService(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        _make_db(self.tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        self.tmp.cleanup()

    def _service(self):
        from src.services.watchlist_service import WatchlistService
        return WatchlistService()

    def test_empty_table_no_env_returns_empty(self) -> None:
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        symbols = self._service().get_symbols()
        self.assertEqual(symbols, [])

    def test_empty_table_seeds_from_env(self) -> None:
        os.environ["RESEARCH_US_WATCHLIST"] = "AAPL,MSFT,NVDA"
        symbols = self._service().get_symbols()
        self.assertIn("AAPL", symbols)
        self.assertIn("MSFT", symbols)
        self.assertIn("NVDA", symbols)
        self.assertEqual(len(symbols), 3)

    def test_seed_only_once(self) -> None:
        """Second call with env set should NOT re-import when table is already populated."""
        os.environ["RESEARCH_US_WATCHLIST"] = "AAPL"
        svc = self._service()
        svc.get_symbols()  # seeds once

        # Manually add a second symbol, change env
        svc.add("GOOG")
        os.environ["RESEARCH_US_WATCHLIST"] = "TSLA,AMD"  # different env — should be ignored

        symbols = svc.get_symbols()
        # Should still have AAPL+GOOG, not TSLA/AMD
        self.assertIn("AAPL", symbols)
        self.assertIn("GOOG", symbols)
        self.assertNotIn("TSLA", symbols)
        self.assertNotIn("AMD", symbols)

    def test_add_and_remove(self) -> None:
        svc = self._service()
        svc.add("PLTR", note="test note")
        self.assertIn("PLTR", svc.get_symbols())
        removed = svc.remove("PLTR")
        self.assertTrue(removed)
        self.assertNotIn("PLTR", svc.get_symbols())

    def test_env_dedup(self) -> None:
        """Duplicate entries in RESEARCH_US_WATCHLIST should be deduplicated."""
        os.environ["RESEARCH_US_WATCHLIST"] = "AAPL,AAPL,MSFT"
        symbols = self._service().get_symbols()
        self.assertEqual(symbols.count("AAPL"), 1)
        self.assertEqual(len(symbols), 2)

    def test_env_seed_source(self) -> None:
        """Seeded symbols should have source='env_seed'."""
        os.environ["RESEARCH_US_WATCHLIST"] = "COHR"
        self._service().get_symbols()
        rows = DatabaseManager.get_instance().get_watchlist_symbols()
        self.assertEqual(rows[0]["source"], "env_seed")

    def test_manual_add_source(self) -> None:
        svc = self._service()
        svc.add("AMD")
        rows = DatabaseManager.get_instance().get_watchlist_symbols()
        self.assertEqual(rows[0]["source"], "manual")


class TestWatchlistApi(unittest.TestCase):
    """Integration tests hitting the FastAPI watchlist endpoints via TestClient."""

    def setUp(self) -> None:
        try:
            import litellm  # noqa: F401
        except ModuleNotFoundError:
            from unittest.mock import MagicMock
            sys.modules["litellm"] = MagicMock()

        try:
            from fastapi.testclient import TestClient
            from api.app import create_app
            self._TestClient = TestClient
            self._create_app = create_app
        except Exception as exc:
            self.skipTest(f"FastAPI not available: {exc}")
            return

        self.tmp = tempfile.TemporaryDirectory()
        data_dir = Path(self.tmp.name)
        self.db_path = data_dir / "test.db"
        self.env_path = data_dir / ".env"
        self.env_path.write_text(
            "\n".join([
                "STOCK_LIST=NVDA",
                "GEMINI_API_KEY=test",
                "ADMIN_AUTH_ENABLED=false",
                f"DATABASE_PATH={self.db_path}",
            ]) + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(self.env_path)
        os.environ["DATABASE_PATH"] = str(self.db_path)
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        static_dir = data_dir / "empty-static"
        static_dir.mkdir()
        app = self._create_app(static_dir=static_dir)
        self.client = self._TestClient(app)

    def tearDown(self) -> None:
        if not hasattr(self, 'tmp'):
            return
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        self.tmp.cleanup()

    def test_get_watchlist_empty(self) -> None:
        resp = self.client.get("/api/v1/research/watchlist")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("items", data)
        self.assertEqual(data["items"], [])

    def test_add_and_list(self) -> None:
        add_resp = self.client.post("/api/v1/research/watchlist", json={"symbol": "aapl", "note": "hello"})
        self.assertEqual(add_resp.status_code, 200)
        item = add_resp.json()
        self.assertEqual(item["symbol"], "AAPL")
        self.assertEqual(item["note"], "hello")
        self.assertEqual(item["source"], "manual")

        list_resp = self.client.get("/api/v1/research/watchlist")
        self.assertEqual(list_resp.status_code, 200)
        symbols = [i["symbol"] for i in list_resp.json()["items"]]
        self.assertIn("AAPL", symbols)

    def test_add_idempotent(self) -> None:
        self.client.post("/api/v1/research/watchlist", json={"symbol": "MSFT"})
        self.client.post("/api/v1/research/watchlist", json={"symbol": "MSFT"})
        list_resp = self.client.get("/api/v1/research/watchlist")
        symbols = [i["symbol"] for i in list_resp.json()["items"]]
        self.assertEqual(symbols.count("MSFT"), 1)

    def test_delete_symbol(self) -> None:
        self.client.post("/api/v1/research/watchlist", json={"symbol": "TSLA"})
        del_resp = self.client.delete("/api/v1/research/watchlist/TSLA")
        self.assertEqual(del_resp.status_code, 200)
        self.assertTrue(del_resp.json()["removed"])

        list_resp = self.client.get("/api/v1/research/watchlist")
        symbols = [i["symbol"] for i in list_resp.json()["items"]]
        self.assertNotIn("TSLA", symbols)

    def test_delete_nonexistent(self) -> None:
        del_resp = self.client.delete("/api/v1/research/watchlist/NONEXISTENT")
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(del_resp.json()["removed"])

    def test_add_whitespace_symbol_rejected(self) -> None:
        """POST with whitespace-only symbol must return 422 (validation error)."""
        resp = self.client.post("/api/v1/research/watchlist", json={"symbol": "  "})
        self.assertEqual(resp.status_code, 422)


if __name__ == "__main__":
    unittest.main()
