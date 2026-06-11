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
        """When not due, pipeline doesn't run."""
        with patch("main._snapshot_pipeline_due", return_value=False), \
             patch("main._run_snapshot_pipeline") as mock_run:
            main._snapshot_pipeline_task()
        mock_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
