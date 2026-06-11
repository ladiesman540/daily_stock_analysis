# -*- coding: utf-8 -*-
"""Tests for the UX-overhaul backend additions.

Covers the cache-only sparklines endpoint, the regime brief history /
breadth_trend additions (with digest parity), and the portfolio
equity-history endpoint. All data is seeded into a temp SQLite DB; no test
ever hits the network.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager, PortfolioDailySnapshot

END = date.today()


def seed_stock_bars(db: DatabaseManager, symbol: str, closes, *, end=END) -> None:
    """Write daily closes into the stock_daily cache used by the endpoint."""
    n = len(closes)
    df = pd.DataFrame([
        {
            "date": (end - timedelta(days=n - 1 - i)),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000_000.0,
        }
        for i, close in enumerate(closes)
    ])
    db.save_daily_data(df, symbol, "Test")


def seed_regime_rows(db: DatabaseManager) -> None:
    db.save_market_regime_snapshot({
        "as_of": (END - timedelta(days=1)).isoformat(),
        "regime": "Neutral",
        "score": 60.0,
        "confidence": "medium",
        "volatility": {"vix": 18.0, "vix3m": 19.0, "term_inverted": False},
        "market_breadth": {"above_sma50_pct": 50.0},
    })
    db.save_market_regime_snapshot({
        "as_of": END.isoformat(),
        "regime": "Risk-on",
        "score": 70.0,
        "confidence": "high",
        "volatility": {"vix": 15.5, "vix3m": 17.0, "term_inverted": False},
        "market_breadth": {"above_sma50_pct": 62.0},
    })


def seed_breadth_rows(db: DatabaseManager, *, universe: str = "us_etf_proxy") -> None:
    for offset, pct in ((2, 48.0), (1, 55.0), (0, 62.0)):
        db.save_market_breadth_cache({
            "as_of": (END - timedelta(days=offset)).isoformat(),
            "universe": universe,
            "status": "completed",
            "above_sma50_pct": pct,
        })


def seed_equity_snapshots(db: DatabaseManager, *, account_id: int = 1, days: int = 3) -> None:
    with db.session_scope() as session:
        for offset in range(days):
            session.add(PortfolioDailySnapshot(
                account_id=account_id,
                snapshot_date=END - timedelta(days=offset),
                cost_method="fifo",
                base_currency="USD",
                total_cash=1_000.0,
                total_market_value=9_000.0 + offset * 100.0,
                total_equity=10_000.0 + offset * 100.0,
                unrealized_pnl=0.0,
                realized_pnl=0.0,
                fee_total=0.0,
                tax_total=0.0,
                fx_stale=False,
            ))


class ApiTestCase(unittest.TestCase):
    """Shared TestClient bootstrap (same pattern as TestDownDayApi)."""

    def setUp(self):
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
        os.environ.pop("RESEARCH_BREADTH_UNIVERSE", None)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        static_dir = data_dir / "empty-static"
        static_dir.mkdir()
        app = self._create_app(static_dir=static_dir)
        self.client = self._TestClient(app)
        self.db = DatabaseManager.get_instance()

    def tearDown(self):
        if not hasattr(self, 'tmp'):
            return
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.tmp.cleanup()


class TestSparklinesApi(ApiTestCase):
    """GET /api/v1/research/free-data/sparklines reads the stock_daily cache only."""

    def test_happy_path(self):
        seed_stock_bars(self.db, "NVDA", [100.0, 101.0, 102.0])
        seed_stock_bars(self.db, "AAPL", [200.0, 201.0])
        resp = self.client.get("/api/v1/research/free-data/sparklines?symbols=NVDA,AAPL&days=30")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(set(data["series"]), {"NVDA", "AAPL"})
        nvda = data["series"]["NVDA"]
        self.assertEqual(len(nvda), 3)
        self.assertEqual(nvda[-1]["close"], 102.0)
        self.assertEqual(nvda[-1]["date"], END.isoformat())
        # Oldest first.
        self.assertEqual([p["close"] for p in nvda], [100.0, 101.0, 102.0])

    def test_unknown_symbols_omitted_and_input_normalized(self):
        seed_stock_bars(self.db, "NVDA", [100.0, 101.0])
        resp = self.client.get(
            "/api/v1/research/free-data/sparklines?symbols=nvda,%20NVDA%20,GHOST,,"
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(set(data["series"]), {"NVDA"})

    def test_symbols_capped_at_60(self):
        seed_stock_bars(self.db, "SYM0", [10.0, 11.0])
        seed_stock_bars(self.db, "SYM70", [10.0, 11.0])
        symbols = ",".join(f"SYM{i}" for i in range(75))
        resp = self.client.get(f"/api/v1/research/free-data/sparklines?symbols={symbols}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # SYM70 is beyond the 60-symbol cap, so it never gets read.
        self.assertIn("SYM0", data["series"])
        self.assertNotIn("SYM70", data["series"])

    def test_days_clamped(self):
        # 10 daily bars; days=5 keeps only the most recent window.
        seed_stock_bars(self.db, "NVDA", [float(i) for i in range(100, 110)])
        resp = self.client.get("/api/v1/research/free-data/sparklines?symbols=NVDA&days=1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["days"], 5)
        self.assertEqual(len(data["series"]["NVDA"]), 6)  # today plus 5 lookback days
        resp = self.client.get("/api/v1/research/free-data/sparklines?symbols=NVDA&days=500")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["days"], 90)


class TestSymbolNamesApi(ApiTestCase):
    """GET /api/v1/research/free-data/symbol-names resolves rotation labels first,
    then the (stubbed) Nasdaq directory. Never hits the network."""

    DIRECTORY_KEY = "free-data:directory-names"

    def setUp(self):
        super().setUp()
        from src.services import free_data_service
        # The directory map is cached in a module-level cache shared across
        # FreeDataService instances; clear it so stubs never leak between tests.
        free_data_service._invalidate_cache(self.DIRECTORY_KEY)

    def tearDown(self):
        from src.services import free_data_service
        free_data_service._invalidate_cache(self.DIRECTORY_KEY)
        super().tearDown()

    def _stub_directory(self, rows):
        from unittest.mock import patch
        from src.services.free_data_service import FreeDataService

        patcher = patch.object(
            FreeDataService, "_fetch_nasdaq_directory", return_value=(rows, "ts"),
        )
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        return mock

    def test_rotation_labels_without_directory(self):
        """Rotation-universe ETFs resolve from ROTATION_GROUPS; the redundant
        "(SYM)" suffix is stripped and the directory is never consulted."""
        mock = self._stub_directory([])
        resp = self.client.get("/api/v1/research/free-data/symbol-names?symbols=SOXX,SPY,GLD")
        self.assertEqual(resp.status_code, 200)
        names = resp.json()["names"]
        self.assertEqual(names["SOXX"], "Semis")  # "Semis (SOXX)" cleaned
        self.assertEqual(names["SPY"], "S&P 500")
        self.assertEqual(names["GLD"], "Gold")
        mock.assert_not_called()

    def test_directory_name_with_suffix_cleanup(self):
        self._stub_directory([
            {"symbol": "AMPG", "name": "AmpliTech Group, Inc. - Common Stock", "is_etf": False, "test_issue": "N"},
            {"symbol": "FOOA", "name": "Foo Holdings - Class A Ordinary Shares", "is_etf": False, "test_issue": "N"},
            {"symbol": "BARZ", "name": "Bar-Mart Stores Co.", "is_etf": False, "test_issue": "N"},
        ])
        resp = self.client.get("/api/v1/research/free-data/symbol-names?symbols=ampg,%20FOOA%20,BARZ,,")
        self.assertEqual(resp.status_code, 200)
        names = resp.json()["names"]
        self.assertEqual(names["AMPG"], "AmpliTech Group, Inc.")
        self.assertEqual(names["FOOA"], "Foo Holdings")
        # No security-type suffix after " - " → name kept verbatim.
        self.assertEqual(names["BARZ"], "Bar-Mart Stores Co.")

    def test_unknown_symbol_omitted(self):
        self._stub_directory([
            {"symbol": "AMPG", "name": "AmpliTech Group, Inc. - Common Stock", "is_etf": False, "test_issue": "N"},
        ])
        resp = self.client.get("/api/v1/research/free-data/symbol-names?symbols=AMPG,GHOST")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json()["names"]), {"AMPG"})

    def test_directory_failure_fails_open(self):
        from unittest.mock import patch
        from src.services.free_data_service import FreeDataService

        patcher = patch.object(
            FreeDataService, "_fetch_nasdaq_directory", side_effect=RuntimeError("nasdaq down"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        resp = self.client.get("/api/v1/research/free-data/symbol-names?symbols=SOXX,AMPG")
        self.assertEqual(resp.status_code, 200)
        # Rotation label still resolves; the directory-only symbol is omitted.
        self.assertEqual(resp.json()["names"], {"SOXX": "Semis"})

    def test_symbols_capped_at_100(self):
        self._stub_directory([
            {"symbol": "SYM0", "name": "Sym Zero Corp. - Common Stock", "is_etf": False, "test_issue": "N"},
            {"symbol": "SYM104", "name": "Sym One-Oh-Four Corp. - Common Stock", "is_etf": False, "test_issue": "N"},
        ])
        symbols = ",".join(f"SYM{i}" for i in range(105))
        resp = self.client.get(f"/api/v1/research/free-data/symbol-names?symbols={symbols}")
        self.assertEqual(resp.status_code, 200)
        names = resp.json()["names"]
        self.assertIn("SYM0", names)
        # SYM104 is beyond the 100-symbol cap, so it is never resolved.
        self.assertNotIn("SYM104", names)


class TestRegimeBriefAdditions(ApiTestCase):
    """Regime brief carries history + breadth_trend; digest rendering is unchanged."""

    def test_history_and_breadth_trend(self):
        seed_regime_rows(self.db)
        seed_breadth_rows(self.db)
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        regime = resp.json()["regime"]
        self.assertEqual(regime["status"], "completed")
        self.assertEqual(len(regime["history"]), 2)
        self.assertEqual(
            regime["history"][-1],
            {"as_of": END.isoformat(), "score": 70.0, "vix": 15.5},
        )
        self.assertEqual(len(regime["breadth_trend"]), 3)
        self.assertEqual(
            regime["breadth_trend"][-1],
            {"as_of": END.isoformat(), "pct_above_50dma": 62.0},
        )
        # Oldest first.
        self.assertEqual([p["pct_above_50dma"] for p in regime["breadth_trend"]],
                         [48.0, 55.0, 62.0])

    def test_breadth_trend_falls_back_to_us_stocks(self):
        seed_regime_rows(self.db)
        seed_breadth_rows(self.db, universe="us_stocks")
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        regime = resp.json()["regime"]
        self.assertEqual(len(regime["breadth_trend"]), 3)

    def test_breadth_trend_empty_without_rows(self):
        seed_regime_rows(self.db)
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        regime = resp.json()["regime"]
        self.assertEqual(regime["breadth_trend"], [])

    def test_digest_unchanged_by_new_fields(self):
        """The renderers ignore history/breadth_trend, so the digest stays identical."""
        from src.services.daily_digest import DailyDigestService

        seed_regime_rows(self.db)
        seed_breadth_rows(self.db)
        service = DailyDigestService(state_path=Path(self.tmp.name) / "alert_state.json")
        brief = service.collect_brief()
        self.assertIn("history", brief["regime"])
        self.assertIn("breadth_trend", brief["regime"])
        digest = service.build_digest()
        # New keys never leak into the rendered markdown.
        self.assertNotIn("breadth_trend", digest)
        self.assertNotIn("pct_above_50dma", digest)
        # Digest equals the same renderers fed a brief stripped of the new keys.
        stripped = dict(brief["regime"])
        stripped.pop("history")
        stripped.pop("breadth_trend")
        self.assertEqual(service._regime_section(brief["regime"]),
                         service._regime_section(stripped))


class TestEquityHistoryApi(ApiTestCase):
    """GET /api/v1/portfolio/equity-history reads PortfolioDailySnapshot rows."""

    def test_empty(self):
        resp = self.client.get("/api/v1/portfolio/equity-history")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["days"], 90)
        self.assertEqual(data["points"], [])

    def test_seeded(self):
        seed_equity_snapshots(self.db, account_id=1, days=3)
        resp = self.client.get("/api/v1/portfolio/equity-history?days=90")
        self.assertEqual(resp.status_code, 200)
        points = resp.json()["points"]
        self.assertEqual(len(points), 3)
        # Ascending by date; equity/currency mapped from the snapshot row.
        self.assertEqual([p["date"] for p in points],
                         [(END - timedelta(days=o)).isoformat() for o in (2, 1, 0)])
        self.assertEqual(points[-1], {
            "date": END.isoformat(),
            "equity": 10_000.0,
            "currency": "USD",
        })

    def test_account_filter_and_window(self):
        seed_equity_snapshots(self.db, account_id=1, days=3)
        seed_equity_snapshots(self.db, account_id=2, days=1)
        resp = self.client.get("/api/v1/portfolio/equity-history?account_id=2")
        self.assertEqual(len(resp.json()["points"]), 1)
        # days=1 keeps only the last calendar day's snapshots for account 1.
        resp = self.client.get("/api/v1/portfolio/equity-history?account_id=1&days=1")
        points = resp.json()["points"]
        self.assertEqual([p["date"] for p in points],
                         [(END - timedelta(days=1)).isoformat(), END.isoformat()])

    def test_multi_account_same_date_aggregation(self):
        """When account_id is omitted and multiple accounts have snapshots on same date,
        equity values should be summed with unique dates."""
        # Account 1: 3 days of snapshots with USD currency
        with self.db.session_scope() as session:
            for offset in range(3):
                session.add(PortfolioDailySnapshot(
                    account_id=1,
                    snapshot_date=END - timedelta(days=offset),
                    cost_method="fifo",
                    base_currency="USD",
                    total_cash=1_000.0,
                    total_market_value=9_000.0 + offset * 100.0,
                    total_equity=10_000.0 + offset * 100.0,
                    unrealized_pnl=0.0,
                    realized_pnl=0.0,
                    fee_total=0.0,
                    tax_total=0.0,
                    fx_stale=False,
                ))
        # Account 2: same dates as account 1, also USD
        with self.db.session_scope() as session:
            for offset in range(3):
                session.add(PortfolioDailySnapshot(
                    account_id=2,
                    snapshot_date=END - timedelta(days=offset),
                    cost_method="fifo",
                    base_currency="USD",
                    total_cash=2_000.0,
                    total_market_value=8_000.0 + offset * 50.0,
                    total_equity=10_000.0 + offset * 50.0,
                    unrealized_pnl=0.0,
                    realized_pnl=0.0,
                    fee_total=0.0,
                    tax_total=0.0,
                    fx_stale=False,
                ))

        # No account_id filter: should aggregate by date, sum equity.
        resp = self.client.get("/api/v1/portfolio/equity-history?days=90")
        self.assertEqual(resp.status_code, 200)
        points = resp.json()["points"]

        # Should have 3 unique dates (no duplicates).
        self.assertEqual(len(points), 3)
        dates = [p["date"] for p in points]
        self.assertEqual(len(set(dates)), 3, "Dates should be unique (no duplicates)")

        # Verify dates are in ascending order.
        self.assertEqual(dates, sorted(dates))

        # Verify equity is summed: account1 (10k+offset*100) + account2 (10k+offset*50).
        expected_equities = [
            10_000.0 + 0 * 100.0 + 10_000.0 + 0 * 50.0,  # Today
            10_000.0 + 1 * 100.0 + 10_000.0 + 1 * 50.0,  # Yesterday
            10_000.0 + 2 * 100.0 + 10_000.0 + 2 * 50.0,  # Two days ago
        ]
        # Dates are ascending, so we need to reverse the expected equities.
        expected_equities.reverse()
        actual_equities = [p["equity"] for p in points]
        self.assertEqual(actual_equities, expected_equities)

        # All should have USD currency.
        self.assertEqual([p["currency"] for p in points], ["USD", "USD", "USD"])


if __name__ == "__main__":
    unittest.main()
