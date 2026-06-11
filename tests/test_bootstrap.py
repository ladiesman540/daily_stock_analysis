# -*- coding: utf-8 -*-
"""Tests for the one-time first-boot bootstrap snapshot in main.py."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

import main
from scripts import daily_snapshot
from src.config import Config
from src.storage import DatabaseManager

EXPECTED_BOOTSTRAP_STEPS = [
    step for step in daily_snapshot.ALL_STEPS if step not in ("analysis", "notify")
]


def _make_db(tmp_dir: str) -> DatabaseManager:
    """Create an isolated in-file SQLite DatabaseManager for testing."""
    db_path = os.path.join(tmp_dir, "test_bootstrap.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


class BootstrapSelectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._prev_db_path = os.environ.get("DATABASE_PATH")
        self.db = _make_db(self._tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        if self._prev_db_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = self._prev_db_path
        Config.reset_instance()
        self._tmp.cleanup()

    def test_empty_db_needs_bootstrap(self) -> None:
        self.assertTrue(main._needs_bootstrap_snapshot())

    def test_seeded_rotation_row_skips_bootstrap(self) -> None:
        self.db.save_sector_rotation_snapshot({
            "as_of": "2026-06-10",
            "benchmark": "SPY",
            "constituents": [],
        })
        self.assertFalse(main._needs_bootstrap_snapshot())

    def test_seeded_regime_row_skips_bootstrap(self) -> None:
        self.db.save_market_regime_snapshot({
            "as_of": "2026-06-10",
            "regime": "Neutral",
            "score": 50.0,
        })
        self.assertFalse(main._needs_bootstrap_snapshot())


class BootstrapRunnerTestCase(unittest.TestCase):
    def test_runs_default_steps_excluding_analysis_and_notify(self) -> None:
        called = []
        stubs = {
            name: (lambda n: lambda ctx: called.append(n))(name)
            for name in daily_snapshot.ALL_STEPS
        }
        with patch.dict(daily_snapshot.STEP_RUNNERS, stubs):
            main._run_bootstrap_snapshot(delay_seconds=0)
        self.assertEqual(called, EXPECTED_BOOTSTRAP_STEPS)
        self.assertNotIn("analysis", called)
        self.assertNotIn("notify", called)

    def test_step_exception_does_not_propagate(self) -> None:
        called = []

        def boom(ctx):
            raise RuntimeError("boom")

        stubs = {}
        for name in daily_snapshot.ALL_STEPS:
            if name == "regime":
                stubs[name] = boom
            else:
                stubs[name] = (lambda n: lambda ctx: called.append(n))(name)
        with patch.dict(daily_snapshot.STEP_RUNNERS, stubs):
            main._run_bootstrap_snapshot(delay_seconds=0)  # must not raise
        # Later steps still ran despite the regime failure.
        self.assertEqual(
            called,
            [step for step in EXPECTED_BOOTSTRAP_STEPS if step != "regime"],
        )

    def test_maybe_start_skips_when_db_seeded(self) -> None:
        with patch.object(main, "_needs_bootstrap_snapshot", return_value=False), \
             patch("threading.Thread") as thread_cls:
            main._maybe_start_bootstrap_snapshot()
        thread_cls.assert_not_called()

    def test_maybe_start_spawns_daemon_thread_when_fresh(self) -> None:
        with patch.object(main, "_needs_bootstrap_snapshot", return_value=True), \
             patch("threading.Thread") as thread_cls:
            main._maybe_start_bootstrap_snapshot()
        thread_cls.assert_called_once()
        self.assertTrue(thread_cls.call_args.kwargs.get("daemon"))
        self.assertEqual(thread_cls.call_args.kwargs.get("target"), main._run_bootstrap_snapshot)
        thread_cls.return_value.start.assert_called_once()

    def test_selector_exception_does_not_propagate(self) -> None:
        with patch.object(main, "_needs_bootstrap_snapshot", side_effect=RuntimeError("db down")):
            main._maybe_start_bootstrap_snapshot()  # must not raise


if __name__ == "__main__":
    unittest.main()
