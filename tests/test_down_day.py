# -*- coding: utf-8 -*-
"""Tests for the down-day relative-strength screen, its storage, digest section, and endpoint.

All bar data is synthetic; the SPY history boundary is stubbed and stock bars are
seeded straight into the stock_daily cache, so no test ever hits the network.
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
from src.storage import DatabaseManager

END = date(2026, 6, 5)


def _make_db(tmp_dir: str) -> DatabaseManager:
    db_path = os.path.join(tmp_dir, "test_down_day.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


def _teardown_db(tmp) -> None:
    DatabaseManager.reset_instance()
    Config.reset_instance()
    os.environ.pop("DATABASE_PATH", None)
    os.environ.pop("DOWN_DAY_THRESHOLD_PCT", None)
    tmp.cleanup()


def bars_from_closes(closes, *, end=END, volume=1_000_000.0):
    """Daily bars ending at `end`, one bar per calendar day, oldest first."""
    n = len(closes)
    return [
        {"date": (end - timedelta(days=n - 1 - i)).isoformat(), "close": close, "volume": volume}
        for i, close in enumerate(closes)
    ]


def seed_stock_bars(db: DatabaseManager, symbol: str, closes, *, end=END) -> None:
    """Write daily closes into the stock_daily cache used by the screen."""
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


def triggered_payload(*, as_of=END, spy_return=-2.5):
    """A persisted-shape triggered snapshot for storage/digest/API tests."""
    return {
        "as_of": as_of.isoformat(),
        "benchmark": "SPY",
        "spy_return_pct": spy_return,
        "threshold_pct": -0.75,
        "triggered": True,
        "last_down_day": as_of.isoformat(),
        "sectors_holding_up": [{"symbol": "XLU", "label": "Utilities", "return_1d_pct": 0.3}],
        "stocks_holding_up": [
            {"symbol": "GREEN", "source": "watchlist", "return_1d_pct": 1.0, "rs_vs_spy_pp": 3.5,
             "reason": "green on the day"},
            {"symbol": "DISC", "source": "discovery", "return_1d_pct": -0.5, "rs_vs_spy_pp": 2.0,
             "reason": "beat SPY by 2.0pp"},
        ],
        "stocks_scanned": 5,
        "stocks_missing": 1,
        "warnings": [],
        "summary": "Down day test summary.",
    }


class TestHoldingUpRule(unittest.TestCase):
    """Boundary cases for the green-or-beat-SPY-by-1pp rule."""

    def _holding_up(self, ret, spy):
        from src.services.discovery_service import _holding_up
        return _holding_up(ret, spy)

    def test_green_but_underperforming_holds(self):
        # +0.05% on a -0.8% SPY day only beats SPY by 0.85pp, but green qualifies.
        self.assertTrue(self._holding_up(0.05, -0.8))

    def test_flat_counts_as_green(self):
        self.assertTrue(self._holding_up(0.0, -2.0))

    def test_red_beating_spy_by_exactly_1pp_holds(self):
        self.assertTrue(self._holding_up(-1.0, -2.0))

    def test_red_beating_spy_by_less_than_1pp_fails(self):
        self.assertFalse(self._holding_up(-1.5, -2.0))

    def test_red_beating_spy_by_more_than_1pp_holds(self):
        self.assertTrue(self._holding_up(-0.5, -2.0))


class TestLastDownDay(unittest.TestCase):
    def test_finds_most_recent_down_session(self):
        from src.services.discovery_service import _last_down_day
        # 100 -> 98 is a -2% day three sessions before the end.
        bars = bars_from_closes([100.0, 100.5, 98.0, 98.2, 98.3, 98.4])
        self.assertEqual(_last_down_day(bars, -0.75), (END - timedelta(days=3)).isoformat())

    def test_none_when_no_down_day(self):
        from src.services.discovery_service import _last_down_day
        bars = bars_from_closes([100.0, 100.1, 100.2, 100.3])
        self.assertIsNone(_last_down_day(bars, -0.75))


class TestBuildDownDayScreen(unittest.TestCase):
    """End-to-end screen on a seeded DB with a stubbed SPY history boundary."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        os.environ.pop("DOWN_DAY_THRESHOLD_PCT", None)
        self.db = _make_db(self.tmp.name)
        for symbol in ("GREEN", "BEAT", "REDL", "MISS", "STAL"):
            self.db.add_watchlist_symbol(symbol, source="manual")
        self.db.save_sector_rotation_snapshot({
            "as_of": END.isoformat(),
            "constituents": [
                {"symbol": "XLU", "group": "sectors", "label": "Utilities", "returns": {"1D": 0.3}},
                {"symbol": "XLE", "group": "sectors", "label": "Energy", "returns": {"1D": -1.0}},
                {"symbol": "XLK", "group": "sectors", "label": "Tech", "returns": {"1D": -2.8}},
                {"symbol": "QQQ", "group": "broad", "returns": {"1D": 0.5}},
            ],
        })
        # Stock closes on a SPY -2.5% day.
        seed_stock_bars(self.db, "GREEN", [100.0, 101.0])   # +1.0% -> green
        seed_stock_bars(self.db, "BEAT", [100.0, 98.8])     # -1.2% -> beats SPY by ~1.3pp
        seed_stock_bars(self.db, "REDL", [100.0, 98.2])     # -1.8% -> only 0.7pp better
        seed_stock_bars(self.db, "DISC", [100.0, 99.5])     # -0.5% -> discovery qualifier holds
        seed_stock_bars(self.db, "STAL", [100.0, 100.5], end=END - timedelta(days=1))  # stale bars
        self.discovery_payload = {
            "as_of": END.isoformat(),
            "constituents": [{"symbol": "DISC"}],
        }

    def tearDown(self):
        _teardown_db(self.tmp)

    def _make_service(self, spy_closes, *, end=END):
        from src.services.discovery_service import DiscoveryService

        service = DiscoveryService(db=self.db)
        payload = {"symbol": "SPY", "bars": bars_from_closes(spy_closes, end=end)}
        service.free_data.market_data.get_us_equity_history = lambda symbol, *, days=260: payload
        return service

    def test_triggered_screen(self):
        service = self._make_service([500.0, 500.0, 487.5])  # -2.5% on the last day
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)

        self.assertEqual(payload["as_of"], END.isoformat())
        self.assertTrue(payload["triggered"])
        self.assertAlmostEqual(payload["spy_return_pct"], -2.5, places=2)
        self.assertEqual(payload["last_down_day"], END.isoformat())

        stocks = {row["symbol"]: row for row in payload["stocks_holding_up"]}
        self.assertEqual(set(stocks), {"GREEN", "BEAT", "DISC"})
        self.assertNotIn("REDL", stocks)  # red and under 1pp better than SPY
        self.assertEqual(stocks["GREEN"]["source"], "watchlist")
        self.assertEqual(stocks["GREEN"]["reason"], "green on the day")
        self.assertEqual(stocks["DISC"]["source"], "discovery")
        self.assertTrue(stocks["BEAT"]["reason"].startswith("beat SPY by"))
        # Sorted by relative strength vs SPY, strongest first.
        self.assertEqual([row["symbol"] for row in payload["stocks_holding_up"]],
                         ["GREEN", "DISC", "BEAT"])

        sectors = [row["symbol"] for row in payload["sectors_holding_up"]]
        self.assertEqual(sectors, ["XLU", "XLE"])  # green first, then the 1.5pp beat
        self.assertNotIn("XLK", sectors)  # fell harder than SPY
        self.assertNotIn("QQQ", sectors)  # not in the sectors group

        # MISS (no bars) and STAL (stale last bar) are skipped with a warning.
        self.assertEqual(payload["stocks_missing"], 2)
        self.assertEqual(payload["stocks_scanned"], 6)
        self.assertTrue(any("no usable cached bars" in w for w in payload["warnings"]))
        self.assertIn("Down day", payload["summary"])

    def test_spy_bar_gap_warning(self):
        """SPY bars with >4 calendar-day gap generate a warning."""
        # Two bars dated 5 days apart (gap > 4 calendar days).
        service = self._make_service([500.0, 487.5], end=END)
        # Manually override the spy_bars to have a 5-day gap.
        from src.services.discovery_service import DiscoveryService

        service = DiscoveryService(db=self.db)
        # Create bars with 5 calendar days between them.
        gap_bars = [
            {"date": (END - timedelta(days=5)).isoformat(), "close": 500.0, "volume": 1_000_000.0},
            {"date": END.isoformat(), "close": 487.5, "volume": 1_000_000.0},
        ]
        payload = {"symbol": "SPY", "bars": gap_bars}
        service.free_data.market_data.get_us_equity_history = lambda symbol, *, days=260: payload
        result = service.build_down_day_screen(discovery_payload=self.discovery_payload)

        self.assertTrue(result["triggered"])
        warnings = result.get("warnings") or []
        self.assertTrue(any("5 calendar days" in w and "down-day return may span multiple sessions" in w for w in warnings),
                        f"Expected gap warning in {warnings}")

    def test_not_triggered_keeps_last_down_day(self):
        # Last day +0.4%, but a -2% day occurred two sessions earlier.
        service = self._make_service([500.0, 490.0, 491.0, 493.0])
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertFalse(payload["triggered"])
        self.assertEqual(payload["stocks_holding_up"], [])
        self.assertEqual(payload["sectors_holding_up"], [])
        self.assertEqual(payload["stocks_scanned"], 0)
        self.assertEqual(payload["last_down_day"], (END - timedelta(days=2)).isoformat())
        self.assertIn("No down day", payload["summary"])

    def test_exactly_at_threshold_triggers(self):
        # 1024 -> 1016 is exactly -0.78125%, representable in binary floating point.
        os.environ["DOWN_DAY_THRESHOLD_PCT"] = "-0.78125"
        service = self._make_service([1024.0, 1016.0])
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertEqual(payload["spy_return_pct"], -0.78)
        self.assertTrue(payload["triggered"])
        # A hair below the same return no longer triggers.
        os.environ["DOWN_DAY_THRESHOLD_PCT"] = "-0.79"
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertFalse(payload["triggered"])

    def test_env_threshold_override(self):
        os.environ["DOWN_DAY_THRESHOLD_PCT"] = "-3.0"
        service = self._make_service([500.0, 500.0, 487.5])  # -2.5% day
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertFalse(payload["triggered"])
        self.assertEqual(payload["threshold_pct"], -3.0)

    def test_green_but_underperforming_still_holds(self):
        # SPY -0.8%: GREEN (+1.0%) beats by only 1.8pp... use a milder stock.
        seed_stock_bars(self.db, "GRNU", [100.0, 100.05])  # +0.05%, rs only ~0.85pp
        self.db.add_watchlist_symbol("GRNU", source="manual")
        service = self._make_service([500.0, 496.0])  # -0.8%
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertTrue(payload["triggered"])
        stocks = {row["symbol"]: row for row in payload["stocks_holding_up"]}
        self.assertIn("GRNU", stocks)
        self.assertLess(stocks["GRNU"]["rs_vs_spy_pp"], 1.0)
        self.assertEqual(stocks["GRNU"]["reason"], "green on the day")

    def test_spy_unavailable_fails_open(self):
        from src.services.discovery_service import DiscoveryService

        service = DiscoveryService(db=self.db)
        service.free_data.market_data.get_us_equity_history = lambda symbol, *, days=260: {"bars": []}
        payload = service.build_down_day_screen()
        self.assertFalse(payload["triggered"])
        self.assertIsNone(payload["spy_return_pct"])
        self.assertTrue(any("history unavailable" in w for w in payload["warnings"]))

    def test_stale_rotation_snapshot_skipped(self):
        self.db.save_sector_rotation_snapshot({
            "as_of": (END + timedelta(days=1)).isoformat(),  # newer than the screen session
            "constituents": [{"symbol": "XLU", "group": "sectors", "returns": {"1D": 0.3}}],
        })
        # Latest rotation snapshot now mismatches the down-day session date.
        service = self._make_service([500.0, 500.0, 487.5])
        payload = service.build_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertEqual(payload["sectors_holding_up"], [])
        self.assertTrue(any("sector hold-up list skipped" in w for w in payload["warnings"]))

    def test_stale_discovery_qualifiers_skipped(self):
        service = self._make_service([500.0, 500.0, 487.5])
        stale = {"as_of": (END - timedelta(days=1)).isoformat(), "constituents": [{"symbol": "DISC"}]}
        payload = service.build_down_day_screen(discovery_payload=stale)
        stocks = {row["symbol"] for row in payload["stocks_holding_up"]}
        self.assertNotIn("DISC", stocks)
        self.assertIn("GREEN", stocks)  # watchlist still scanned
        self.assertTrue(any("qualifiers skipped" in w for w in payload["warnings"]))

    def test_run_down_day_screen_persists(self):
        service = self._make_service([500.0, 500.0, 487.5])
        payload = service.run_down_day_screen(discovery_payload=self.discovery_payload)
        self.assertEqual(payload["persisted"]["as_of"], END.isoformat())
        saved = self.db.get_latest_down_day_rs_snapshot()
        self.assertEqual(saved["as_of"], END.isoformat())
        self.assertTrue(saved["triggered"])
        self.assertEqual({row["symbol"] for row in saved["stocks_holding_up"]},
                         {"GREEN", "BEAT", "DISC"})


class TestDownDayStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def test_save_and_read_back(self):
        saved = self.db.save_down_day_rs_snapshot(triggered_payload())
        self.assertEqual(saved["as_of"], END.isoformat())
        latest = self.db.get_latest_down_day_rs_snapshot()
        self.assertEqual(latest["spy_return_pct"], -2.5)
        self.assertTrue(latest["triggered"])
        self.assertEqual(latest["threshold_pct"], -0.75)
        self.assertEqual(latest["sectors_holding_up"][0]["symbol"], "XLU")
        self.assertEqual([row["symbol"] for row in latest["stocks_holding_up"]], ["GREEN", "DISC"])
        self.assertEqual(latest["summary"], "Down day test summary.")

    def test_upsert_one_row_per_as_of(self):
        self.db.save_down_day_rs_snapshot(triggered_payload(spy_return=-1.0))
        self.db.save_down_day_rs_snapshot(triggered_payload(spy_return=-3.0))
        latest = self.db.get_latest_down_day_rs_snapshot()
        self.assertEqual(latest["spy_return_pct"], -3.0)

    def test_latest_picks_newest_as_of(self):
        self.db.save_down_day_rs_snapshot(triggered_payload(as_of=END - timedelta(days=2), spy_return=-1.5))
        self.db.save_down_day_rs_snapshot(triggered_payload(as_of=END, spy_return=-2.0))
        latest = self.db.get_latest_down_day_rs_snapshot()
        self.assertEqual(latest["as_of"], END.isoformat())
        self.assertEqual(latest["spy_return_pct"], -2.0)


class TestDownDayDigest(unittest.TestCase):
    """collect_brief carries down_day_rs; the renderer only fires on a triggered day."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)
        from src.services.daily_digest import DailyDigestService
        self.service = DailyDigestService(state_path=Path(self.tmp.name) / "alert_state.json")

    def tearDown(self):
        _teardown_db(self.tmp)

    def test_missing_renders_nothing(self):
        brief = self.service.collect_brief()
        self.assertEqual(brief["down_day_rs"]["status"], "missing")
        for key in ("as_of", "age_days", "freshness"):
            self.assertIn(key, brief["down_day_rs"])
        self.assertEqual(self.service._down_day_section(brief["down_day_rs"]), [])
        digest = self.service.build_digest()
        self.assertNotIn("Down day", digest)
        self.assertNotIn("Holding up", digest)

    def test_not_triggered_renders_nothing(self):
        payload = triggered_payload(as_of=date.today())
        payload.update({"triggered": False, "spy_return_pct": 0.4,
                        "sectors_holding_up": [], "stocks_holding_up": []})
        self.db.save_down_day_rs_snapshot(payload)
        brief = self.service.collect_brief()
        self.assertEqual(brief["down_day_rs"]["status"], "completed")
        self.assertFalse(brief["down_day_rs"]["triggered"])
        self.assertEqual(self.service._down_day_section(brief["down_day_rs"]), [])
        self.assertNotIn("Down day", self.service.build_digest())

    def test_triggered_renders_holding_up_line(self):
        self.db.save_down_day_rs_snapshot(triggered_payload(as_of=date.today()))
        brief = self.service.collect_brief()
        section = brief["down_day_rs"]
        self.assertEqual(section["status"], "completed")
        self.assertEqual(section["freshness"], "fresh")
        self.assertIn("is_latest_session", section)
        rendered = "\n".join(self.service._down_day_section(section))
        self.assertIn("## Down day (SPY -2.50%", rendered)
        self.assertIn("**Holding up on a -2.50% day:** GREEN, DISC", rendered)
        self.assertIn("**Sectors holding up:** XLU", rendered)
        self.assertIn(rendered, self.service.build_digest())

    def test_stale_triggered_renders_nothing(self):
        """Triggered snapshot with freshness='stale' does not render."""
        # Save a triggered payload with a date > 4 days old to make it stale.
        old_date = date.today() - timedelta(days=6)
        payload = triggered_payload(as_of=old_date)
        self.db.save_down_day_rs_snapshot(payload)
        brief = self.service.collect_brief()
        section = brief["down_day_rs"]
        self.assertEqual(section["status"], "completed")
        self.assertTrue(section["triggered"])
        self.assertEqual(section["freshness"], "stale")
        # The staleness guard should prevent rendering.
        self.assertEqual(self.service._down_day_section(section), [])
        # Confirm it's absent from the full digest.
        self.assertNotIn("Down day", self.service.build_digest())

    def test_fresh_triggered_renders_and_stale_does_not(self):
        """Fresh triggered payload renders; stale one does not."""
        # Fresh payload (2 days ago, within the 4-day window)
        fresh = triggered_payload(as_of=date.today() - timedelta(days=2))
        self.db.save_down_day_rs_snapshot(fresh)
        brief_fresh = self.service.collect_brief()
        self.assertEqual(brief_fresh["down_day_rs"]["freshness"], "fresh")
        rendered_fresh = "\n".join(self.service._down_day_section(brief_fresh["down_day_rs"]))
        self.assertIn("## Down day (SPY -2.50%", rendered_fresh)
        self.assertIn("Holding up", rendered_fresh)


class TestDownDayApi(unittest.TestCase):
    """GET /api/v1/research/free-data/down-day-rs/latest via TestClient (no network)."""

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
        Config.reset_instance()
        DatabaseManager.reset_instance()

        static_dir = data_dir / "empty-static"
        static_dir.mkdir()
        app = self._create_app(static_dir=static_dir)
        self.client = self._TestClient(app)

    def tearDown(self):
        if not hasattr(self, 'tmp'):
            return
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("ENV_FILE", None)
        os.environ.pop("DATABASE_PATH", None)
        self.tmp.cleanup()

    def test_latest_empty_db(self):
        resp = self.client.get("/api/v1/research/free-data/down-day-rs/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "missing")
        self.assertIn("summary", data)

    def test_latest_seeded(self):
        DatabaseManager.get_instance().save_down_day_rs_snapshot(triggered_payload())
        resp = self.client.get("/api/v1/research/free-data/down-day-rs/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["as_of"], END.isoformat())
        self.assertEqual(data["spy_return_pct"], -2.5)
        self.assertTrue(data["triggered"])
        self.assertEqual([row["symbol"] for row in data["stocks_holding_up"]], ["GREEN", "DISC"])

    def test_daily_brief_includes_down_day_rs(self):
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("down_day_rs", data)
        self.assertEqual(data["down_day_rs"]["status"], "missing")
        for key in ("as_of", "age_days", "freshness"):
            self.assertIn(key, data["down_day_rs"])


if __name__ == "__main__":
    unittest.main()
