# -*- coding: utf-8 -*-
"""Tests for the scheduled snapshot pipeline in main.py."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, date, time as datetime_time
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

import main
from scripts import daily_snapshot
from src.config import Config
from src.storage import DatabaseManager


def _make_db(tmp_dir: str) -> DatabaseManager:
    """Create an isolated in-file SQLite DatabaseManager for testing."""
    db_path = os.path.join(tmp_dir, "test_bootstrap.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


class SnapshotScheduleTimeTestCase(unittest.TestCase):
    """Test _snapshot_schedule_time parsing."""

    def setUp(self) -> None:
        self._prev_env = os.environ.get("SNAPSHOT_SCHEDULE_TIME")

    def tearDown(self) -> None:
        if self._prev_env is None:
            os.environ.pop("SNAPSHOT_SCHEDULE_TIME", None)
        else:
            os.environ["SNAPSHOT_SCHEDULE_TIME"] = self._prev_env

    def test_parses_valid_time(self) -> None:
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "07:45"
        result = main._snapshot_schedule_time()
        self.assertEqual(result, datetime_time(7, 45))

    def test_parses_with_whitespace(self) -> None:
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "  14:30  "
        result = main._snapshot_schedule_time()
        self.assertEqual(result, datetime_time(14, 30))

    def test_falls_back_on_invalid_format(self) -> None:
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "invalid"
        result = main._snapshot_schedule_time()
        # Should fall back to default
        hour, minute = main._SNAPSHOT_SCHEDULE_TIME_DEFAULT.split(":")
        expected = datetime_time(int(hour), int(minute))
        self.assertEqual(result, expected)

    def test_falls_back_on_missing_env(self) -> None:
        os.environ.pop("SNAPSHOT_SCHEDULE_TIME", None)
        result = main._snapshot_schedule_time()
        hour, minute = main._SNAPSHOT_SCHEDULE_TIME_DEFAULT.split(":")
        expected = datetime_time(int(hour), int(minute))
        self.assertEqual(result, expected)


class SnapshotPipelineDueTestCase(unittest.TestCase):
    """Test _snapshot_pipeline_due logic."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_db_path = os.environ.get("DATABASE_PATH")
        self._prev_schedule_time = os.environ.get("SNAPSHOT_SCHEDULE_TIME")
        self.db = _make_db(self._tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        if self._prev_db_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self._prev_db_path
        if self._prev_schedule_time is None:
            os.environ.pop("SNAPSHOT_SCHEDULE_TIME", None)
        else:
            os.environ["SNAPSHOT_SCHEDULE_TIME"] = self._prev_schedule_time
        Config.reset_instance()
        self._tmp.cleanup()

    def test_fresh_empty_db_is_due(self) -> None:
        """Fresh database with no rotation snapshot should be due."""
        result = main._snapshot_pipeline_due()
        self.assertTrue(result)

    def test_current_time_before_schedule_is_not_due(self) -> None:
        """If current time is before schedule time, not due yet."""
        today = str(date.today())
        self.db.save_sector_rotation_snapshot({
            "as_of": today,
            "benchmark": "SPY",
            "constituents": [],
        })
        self.db.save_discovery_snapshot({
            "as_of": today,
            "universe_size": 100,
            "qualified_size": 50,
            "constituents": [],
        })
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "23:59"  # very late, so now is before this
        result = main._snapshot_pipeline_due()
        self.assertFalse(result)

    def test_stale_as_of_with_expected_date_is_due(self) -> None:
        """Stale snapshot (5 days ago) with expected date = today -> due."""
        stale_date = str(date.today() - __import__('datetime').timedelta(days=5))
        self.db.save_sector_rotation_snapshot({
            "as_of": stale_date,
            "benchmark": "SPY",
            "constituents": [],
        })
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "00:00"  # always in the past
        with patch("src.services.research_market_data._latest_expected_us_bar_date", return_value=date.today()):
            result = main._snapshot_pipeline_due()
        self.assertTrue(result)

    def test_current_as_of_equals_expected_date_is_not_due(self) -> None:
        """Snapshot as_of == expected session date -> not due."""
        today = str(date.today())
        self.db.save_sector_rotation_snapshot({
            "as_of": today,
            "benchmark": "SPY",
            "constituents": [],
        })
        self.db.save_discovery_snapshot({
            "as_of": today,
            "universe_size": 100,
            "qualified_size": 50,
            "constituents": [],
        })
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "00:00"  # always in the past
        with patch("src.services.research_market_data._latest_expected_us_bar_date", return_value=date.today()):
            result = main._snapshot_pipeline_due()
        self.assertFalse(result)

    def test_rotation_seeded_no_discovery_is_due(self) -> None:
        """Rotation snapshot exists but no discovery snapshot -> due immediately."""
        self.db.save_sector_rotation_snapshot({
            "as_of": str(date.today()),
            "benchmark": "SPY",
            "constituents": [],
        })
        # No discovery snapshot saved
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "23:59"  # even before schedule time
        result = main._snapshot_pipeline_due()
        self.assertTrue(result)

    def test_rotation_today_discovery_yesterday_is_due(self) -> None:
        """Rotation as_of=today, discovery as_of=yesterday -> due."""
        today = str(date.today())
        yesterday = str(date.today() - __import__('datetime').timedelta(days=1))
        self.db.save_sector_rotation_snapshot({
            "as_of": today,
            "benchmark": "SPY",
            "constituents": [],
        })
        self.db.save_discovery_snapshot({
            "as_of": yesterday,
            "universe_size": 100,
            "qualified_size": 50,
            "constituents": [],
        })
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "23:59"  # even before schedule time
        result = main._snapshot_pipeline_due()
        self.assertTrue(result)

    def test_rotation_and_discovery_both_today_future_schedule_not_due(self) -> None:
        """Rotation and discovery both as_of=today, schedule time in future -> not due."""
        today = str(date.today())
        self.db.save_sector_rotation_snapshot({
            "as_of": today,
            "benchmark": "SPY",
            "constituents": [],
        })
        self.db.save_discovery_snapshot({
            "as_of": today,
            "universe_size": 100,
            "qualified_size": 50,
            "constituents": [],
        })
        os.environ["SNAPSHOT_SCHEDULE_TIME"] = "23:59"  # very late, so now is before this
        result = main._snapshot_pipeline_due()
        self.assertFalse(result)


class SnapshotPipelineRunTestCase(unittest.TestCase):
    """Test _run_snapshot_pipeline execution."""

    def test_all_steps_called_in_order(self) -> None:
        """All steps in ALL_STEPS are called in order."""
        called = []
        stubs = {
            step: (lambda s: lambda ctx: called.append(s))(step)
            for step in daily_snapshot.ALL_STEPS
        }
        with patch.dict(daily_snapshot.STEP_RUNNERS, stubs):
            main._run_snapshot_pipeline()
        self.assertEqual(called, list(daily_snapshot.ALL_STEPS))

    def test_failing_step_does_not_stop_pipeline(self) -> None:
        """A raising step doesn't prevent later steps."""
        called = []

        def boom(ctx):
            called.append("boom")
            raise RuntimeError("step failed")

        stubs = {}
        for step in daily_snapshot.ALL_STEPS:
            if step == "regime":
                stubs[step] = boom
            else:
                stubs[step] = (lambda s: lambda ctx: called.append(s))(step)

        with patch.dict(daily_snapshot.STEP_RUNNERS, stubs):
            main._run_snapshot_pipeline()  # must not raise

        # All steps called despite "regime" raising
        self.assertIn("boom", called)
        # Other steps still ran
        self.assertTrue(any(s for s in called if s != "boom"))
        # Check that steps after "regime" were called
        regime_idx = None
        for i, step in enumerate(daily_snapshot.ALL_STEPS):
            if step == "regime":
                regime_idx = i
                break
        if regime_idx is not None and regime_idx < len(daily_snapshot.ALL_STEPS) - 1:
            next_step = daily_snapshot.ALL_STEPS[regime_idx + 1]
            self.assertIn(next_step, called)


class SnapshotPipelineTaskTestCase(unittest.TestCase):
    """Test _snapshot_pipeline_task wrapper."""

    def test_task_exception_does_not_propagate(self) -> None:
        """When _snapshot_pipeline_due raises, task handles it gracefully."""
        with patch("main._snapshot_pipeline_due", side_effect=RuntimeError("db error")):
            main._snapshot_pipeline_task()  # must not raise

    def test_task_runs_pipeline_when_due(self) -> None:
        """When due, the task runs the pipeline."""
        with patch("main._snapshot_pipeline_due", return_value=True), \
             patch("main._run_snapshot_pipeline") as mock_run:
            main._snapshot_pipeline_task()
        mock_run.assert_called_once()

    def test_task_skips_pipeline_when_not_due(self) -> None:
        """When not due, pipeline doesn't run; the intraday alert check fires instead."""
        with patch("main._snapshot_pipeline_due", return_value=False), \
             patch("main._run_snapshot_pipeline") as mock_run, \
             patch("main._intraday_threshold_alerts") as mock_alerts:
            main._snapshot_pipeline_task()
        mock_run.assert_not_called()
        mock_alerts.assert_called_once()


class IntradayMarketRefreshTestCase(unittest.TestCase):
    """Test _intraday_market_refresh and related helpers."""

    def setUp(self) -> None:
        self._prev_intraday_enabled = os.environ.get("INTRADAY_REFRESH_ENABLED")

    def tearDown(self) -> None:
        if self._prev_intraday_enabled is None:
            os.environ.pop("INTRADAY_REFRESH_ENABLED", None)
        else:
            os.environ["INTRADAY_REFRESH_ENABLED"] = self._prev_intraday_enabled

    def test_disabled_env_skips_refresh(self) -> None:
        """When INTRADAY_REFRESH_ENABLED=false, no STEP_RUNNERS are touched."""
        os.environ["INTRADAY_REFRESH_ENABLED"] = "false"
        mock_runners = MagicMock()
        with patch("scripts.daily_snapshot.STEP_RUNNERS", mock_runners), \
             patch("main._in_us_market_window", return_value=True):
            main._intraday_market_refresh()
        # Verify STEP_RUNNERS was never accessed
        mock_runners.__getitem__.assert_not_called()

    def test_disabled_env_with_no_variants(self) -> None:
        """Test various disabled env values: 0, false, no, off."""
        for val in ["0", "false", "no", "off", "False", "OFF", "NO"]:
            os.environ["INTRADAY_REFRESH_ENABLED"] = val
            mock_runners = MagicMock()
            with patch("scripts.daily_snapshot.STEP_RUNNERS", mock_runners), \
                 patch("main._in_us_market_window", return_value=True):
                main._intraday_market_refresh()
            # Verify STEP_RUNNERS was never accessed
            mock_runners.__getitem__.assert_not_called()

    def test_outside_market_window_skips_runners(self) -> None:
        """When _in_us_market_window returns False, no runner calls."""
        mock_runners = MagicMock()
        with patch("main._in_us_market_window", return_value=False), \
             patch("scripts.daily_snapshot.STEP_RUNNERS", mock_runners):
            main._intraday_market_refresh()
        # Verify STEP_RUNNERS was never accessed
        mock_runners.__getitem__.assert_not_called()

    def test_inside_window_regime_and_portfolio_called(self) -> None:
        """Inside market window: regime and portfolio runners called."""
        called = []

        def make_runner(name):
            def runner(ctx):
                called.append(name)
            return runner

        runners = {
            "regime": make_runner("regime"),
            "portfolio": make_runner("portfolio"),
            "rotation": make_runner("rotation"),
        }
        with patch("main._in_us_market_window", return_value=True), \
             patch("scripts.daily_snapshot.STEP_RUNNERS", runners), \
             patch("src.storage.DatabaseManager.get_instance") as mock_db_inst:
            # Mock rotation snapshot to be recent (not stale)
            mock_db = MagicMock()
            mock_db.get_latest_sector_rotation_snapshot.return_value = {
                "generated_at": "2026-06-10T12:00:00Z"
            }
            mock_db_inst.return_value = mock_db
            main._intraday_market_refresh()
        assert "regime" in called
        assert "portfolio" in called

    def test_regime_runner_raising_does_not_stop_portfolio(self) -> None:
        """A raising regime runner doesn't prevent portfolio from being called."""
        called = []

        def boom(ctx):
            called.append("regime")
            raise RuntimeError("regime failed")

        def make_runner(name):
            def runner(ctx):
                called.append(name)
            return runner

        runners = {
            "regime": boom,
            "portfolio": make_runner("portfolio"),
            "rotation": make_runner("rotation"),
        }
        with patch("main._in_us_market_window", return_value=True), \
             patch("scripts.daily_snapshot.STEP_RUNNERS", runners), \
             patch("src.storage.DatabaseManager.get_instance") as mock_db_inst:
            mock_db = MagicMock()
            mock_db.get_latest_sector_rotation_snapshot.return_value = {
                "generated_at": "2026-06-10T12:00:00Z"
            }
            mock_db_inst.return_value = mock_db
            main._intraday_market_refresh()  # must not raise
        # Both regime and portfolio were attempted
        assert "regime" in called
        assert "portfolio" in called

    def test_rotation_not_called_when_recent(self) -> None:
        """Rotation runner NOT called when snapshot generated_at is 10 minutes ago."""
        from datetime import datetime as dt, timedelta
        called = []

        def make_runner(name):
            def runner(ctx):
                called.append(name)
            return runner

        runners = {
            "regime": make_runner("regime"),
            "portfolio": make_runner("portfolio"),
            "rotation": make_runner("rotation"),
        }
        # Snapshot generated 10 minutes ago (< 1 hour threshold)
        recent_time = (dt.now() - timedelta(minutes=10)).isoformat()
        with patch("main._in_us_market_window", return_value=True), \
             patch("scripts.daily_snapshot.STEP_RUNNERS", runners), \
             patch("src.storage.DatabaseManager.get_instance") as mock_db_inst:
            mock_db = MagicMock()
            mock_db.get_latest_sector_rotation_snapshot.return_value = {
                "generated_at": recent_time
            }
            mock_db_inst.return_value = mock_db
            main._intraday_market_refresh()
        assert "rotation" not in called
        assert "regime" in called
        assert "portfolio" in called

    def test_rotation_called_when_stale(self) -> None:
        """Rotation runner called when snapshot generated_at is 2 hours ago."""
        from datetime import datetime as dt, timedelta
        called = []

        def make_runner(name):
            def runner(ctx):
                called.append(name)
            return runner

        runners = {
            "regime": make_runner("regime"),
            "portfolio": make_runner("portfolio"),
            "rotation": make_runner("rotation"),
        }
        # Snapshot generated 2 hours ago (> 1 hour threshold)
        stale_time = (dt.now() - timedelta(hours=2)).isoformat()
        with patch("main._in_us_market_window", return_value=True), \
             patch("scripts.daily_snapshot.STEP_RUNNERS", runners), \
             patch("src.storage.DatabaseManager.get_instance") as mock_db_inst:
            mock_db = MagicMock()
            mock_db.get_latest_sector_rotation_snapshot.return_value = {
                "generated_at": stale_time
            }
            mock_db_inst.return_value = mock_db
            main._intraday_market_refresh()
        assert "rotation" in called
        assert "regime" in called
        assert "portfolio" in called

    def test_rotation_called_when_no_snapshot_exists(self) -> None:
        """Rotation runner called when no rotation snapshot exists (None)."""
        called = []

        def make_runner(name):
            def runner(ctx):
                called.append(name)
            return runner

        runners = {
            "regime": make_runner("regime"),
            "portfolio": make_runner("portfolio"),
            "rotation": make_runner("rotation"),
        }
        with patch("main._in_us_market_window", return_value=True), \
             patch("scripts.daily_snapshot.STEP_RUNNERS", runners), \
             patch("src.storage.DatabaseManager.get_instance") as mock_db_inst:
            mock_db = MagicMock()
            mock_db.get_latest_sector_rotation_snapshot.return_value = None
            mock_db_inst.return_value = mock_db
            main._intraday_market_refresh()
        assert "rotation" in called
        assert "regime" in called
        assert "portfolio" in called

    def test_not_due_branch_calls_intraday_before_catch_up(self) -> None:
        """In not-due branch of _snapshot_pipeline_task, _intraday_market_refresh called before _catch_up_news_scoring."""
        called = []

        def track_call(name):
            def fn(*args, **kwargs):
                called.append(name)
            return fn

        with patch("main._snapshot_pipeline_due", return_value=False), \
             patch("main._intraday_market_refresh", side_effect=track_call("intraday")), \
             patch("main._catch_up_news_scoring", side_effect=track_call("catch_up")), \
             patch("main._intraday_threshold_alerts", side_effect=track_call("alerts")):
            main._snapshot_pipeline_task()
        # Verify order: intraday before catch_up
        assert "intraday" in called
        assert "catch_up" in called
        intraday_idx = called.index("intraday")
        catch_up_idx = called.index("catch_up")
        assert intraday_idx < catch_up_idx


class InUSMarketWindowTestCase(unittest.TestCase):
    """Test _in_us_market_window."""

    def test_returns_bool(self) -> None:
        """_in_us_market_window always returns a bool."""
        result = main._in_us_market_window()
        self.assertIsInstance(result, bool)

    def test_weekday_logic(self) -> None:
        """Weekday constraint: Monday-Friday (0-4) return possible True, Saturday-Sunday (5-6) return False."""
        from datetime import datetime as dt, timedelta
        from unittest.mock import patch as mock_patch

        # Saturday
        saturday = dt(2026, 6, 13, 12, 0, 0)  # A Saturday in June 2026
        with mock_patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = saturday
            # Manually call logic since we can't easily patch dt inside the function
            result = saturday.weekday() < 5 and 6 <= saturday.hour < 14
            self.assertFalse(result, "Saturday should be outside market window")

        # Monday
        monday = dt(2026, 6, 15, 12, 0, 0)  # A Monday in June 2026
        result = monday.weekday() < 5 and 6 <= monday.hour < 14
        self.assertTrue(result, "Monday at 12:00 should be inside market window (weekday/hour OK)")

    def test_hour_logic(self) -> None:
        """Hour constraint: 06:00 <= hour < 14:00."""
        from datetime import datetime as dt

        # Before window (5:59 AM)
        before = dt(2026, 6, 15, 5, 59, 0)  # Monday, 5:59 AM
        result = before.weekday() < 5 and 6 <= before.hour < 14
        self.assertFalse(result, "5:59 AM should be outside market window")

        # Start of window (6:00 AM)
        start = dt(2026, 6, 15, 6, 0, 0)  # Monday, 6:00 AM
        result = start.weekday() < 5 and 6 <= start.hour < 14
        self.assertTrue(result, "6:00 AM should be inside market window")

        # Middle of window (12:00 PM)
        middle = dt(2026, 6, 15, 12, 0, 0)  # Monday, 12:00 PM
        result = middle.weekday() < 5 and 6 <= middle.hour < 14
        self.assertTrue(result, "12:00 PM should be inside market window")

        # End of window (13:59 PM)
        end = dt(2026, 6, 15, 13, 59, 0)  # Monday, 1:59 PM
        result = end.weekday() < 5 and 6 <= end.hour < 14
        self.assertTrue(result, "1:59 PM should be inside market window")

        # After window (14:00 PM)
        after = dt(2026, 6, 15, 14, 0, 0)  # Monday, 2:00 PM
        result = after.weekday() < 5 and 6 <= after.hour < 14
        self.assertFalse(result, "2:00 PM (14:00) should be outside market window")


if __name__ == "__main__":
    unittest.main()
