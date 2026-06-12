# -*- coding: utf-8 -*-
"""Tests for the intraday threshold-alert layer in DailyDigestService.

Covers the three new checks (discovery high-conviction, watchlist movers,
down-day trigger) with their dedup/cap/fail-open matrices, alert-state
pruning, an existing-behavior (regime flip) regression, and the main.py tick
wrapper. All data is seeded into a temp SQLite DB and the notifier is a
recording stub, so no test ever hits the network.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager

from src.services.research_market_data import _latest_expected_us_bar_date

# The most recent expected US trading session: snapshots/bars dated here pass
# the _is_latest_us_session gate; anything older is "stale" for alerting.
SESSION = _latest_expected_us_bar_date()

_ALERT_ENV_KEYS = (
    "DISCOVERY_ALERT_MIN_COMPOSITE",
    "DISCOVERY_ALERT_MIN_CANDIDATE",
    "WATCHLIST_MOVER_ALERT_PCT",
    "WATCHLIST_MOVER_VOLUME_RATIO",
    "ALERTS_INTRADAY_ENABLED",
)


def _make_db(tmp_dir: str) -> DatabaseManager:
    db_path = os.path.join(tmp_dir, "test_threshold_alerts.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


class StubNotifier:
    """Recording notification stub: always available, captures sent content."""

    def __init__(self):
        self.sent = []

    def is_available(self) -> bool:
        return True

    def send(self, content, **kwargs) -> bool:
        self.sent.append(content)
        return True


def discovery_row(symbol, composite=93.0, candidate=74.0, name=None, reason=None):
    return {
        "symbol": symbol,
        "name": name if name is not None else f"{symbol} Inc",
        "composite_score": composite,
        "candidate_score": candidate,
        "reason": reason or "strong tape",
    }


def seed_bars(db: DatabaseManager, symbol: str, closes, volumes=None, *, end=SESSION) -> None:
    """Write daily closes/volumes into the stock_daily cache, one bar per
    calendar day ending at `end` (only the last bar's date is gate-checked)."""
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000.0] * n
    df = pd.DataFrame([
        {
            "date": (end - timedelta(days=n - 1 - i)),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volumes[i],
        }
        for i, close in enumerate(closes)
    ])
    db.save_daily_data(df, symbol, "Test")


def down_day_payload(*, as_of=SESSION, triggered=True, spy_return=-2.5, stocks=None):
    if stocks is None:
        stocks = ["GREEN", "DISC"]
    return {
        "as_of": as_of.isoformat(),
        "benchmark": "SPY",
        "spy_return_pct": spy_return,
        "threshold_pct": -0.75,
        "triggered": triggered,
        "last_down_day": as_of.isoformat(),
        "sectors_holding_up": [{"symbol": "XLU", "label": "Utilities", "return_1d_pct": 0.3}],
        "stocks_holding_up": [
            {"symbol": sym, "source": "watchlist", "return_1d_pct": 1.0, "rs_vs_spy_pp": 3.5,
             "reason": "green on the day"}
            for sym in stocks
        ],
        "stocks_scanned": len(stocks),
        "stocks_missing": 0,
        "warnings": [],
        "summary": "Down day test summary.",
    }


class AlertTestCase(unittest.TestCase):
    """Shared temp-DB + tmp-state + stub-notifier scaffolding."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        for key in _ALERT_ENV_KEYS:
            os.environ.pop(key, None)
        self.db = _make_db(self.tmp.name)
        self.state_path = Path(self.tmp.name) / "alert_state.json"

    def tearDown(self):
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        for key in _ALERT_ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp.cleanup()

    def _service(self):
        from src.services.daily_digest import DailyDigestService

        notifier = StubNotifier()
        service = DailyDigestService(notifier=notifier, db=self.db, state_path=self.state_path)
        return service, notifier

    def _state(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))


class TestHighConvictionAlerts(AlertTestCase):
    def _seed(self, latest_rows, previous_rows=None, *, as_of=SESSION):
        if previous_rows is not None:
            self.db.save_discovery_snapshot({
                "as_of": (as_of - timedelta(days=1)).isoformat(),
                "constituents": previous_rows,
            })
        self.db.save_discovery_snapshot({"as_of": as_of.isoformat(), "constituents": latest_rows})

    def test_new_qualifying_symbol_fires_once(self):
        self._seed(
            [discovery_row("AMPG", 93, 74, name="Amplitech Group", reason="momentum building")],
            previous_rows=[],
        )
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(alerts, [
            "🎯 New high-conviction idea: AMPG (Amplitech Group) — "
            "composite 93, candidate 74. momentum building"
        ])
        self.assertEqual(len(notifier.sent), 1)
        self.assertIn("AMPG", notifier.sent[0])
        # Second call same day: deduped via state, silent.
        service2, notifier2 = self._service()
        self.assertEqual(service2.send_threshold_alerts(), [])
        self.assertEqual(notifier2.sent, [])

    def test_below_either_threshold_silent(self):
        self._seed(
            [
                discovery_row("LOWC", composite=89.9, candidate=80.0),   # composite gate fails
                discovery_row("LOWK", composite=95.0, candidate=69.9),   # candidate gate fails
            ],
            previous_rows=[],
        )
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])

    def test_present_in_previous_snapshot_silent(self):
        self._seed(
            [discovery_row("AMPG", 93, 74)],
            previous_rows=[discovery_row("AMPG", 92, 71)],  # already qualified yesterday
        )
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])

    def test_cap_three_per_day_highest_composite_first(self):
        # No previous snapshot at all: every qualifying row counts as new.
        self._seed([
            discovery_row("AAA", composite=91, candidate=75),
            discovery_row("BBB", composite=92, candidate=75),
            discovery_row("CCC", composite=93, candidate=75),
            discovery_row("DDD", composite=94, candidate=75),
            discovery_row("EEE", composite=95, candidate=75),
        ])
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(len(alerts), 3)
        self.assertIn("EEE", alerts[0])
        self.assertIn("DDD", alerts[1])
        self.assertIn("CCC", alerts[2])
        self.assertEqual(len(notifier.sent), 1)
        # Only the alerted symbols are recorded, so AAA/BBB can fire later.
        state = self._state()
        self.assertEqual(set(state["discovery_high_conviction_on"]), {"EEE", "DDD", "CCC"})

    def test_realert_window_30_days(self):
        self._seed([discovery_row("AMPG", 93, 74)], previous_rows=[])
        # Last alerted 40 days ago: window elapsed, fires again.
        self.state_path.write_text(json.dumps({
            "discovery_high_conviction_on": {"AMPG": (SESSION - timedelta(days=40)).isoformat()},
        }), encoding="utf-8")
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertIn("AMPG", alerts[0])
        self.assertEqual(self._state()["discovery_high_conviction_on"]["AMPG"], SESSION.isoformat())
        # Last alerted 5 days ago: inside the window, silent.
        self.state_path.write_text(json.dumps({
            "discovery_high_conviction_on": {"AMPG": (SESSION - timedelta(days=5)).isoformat()},
        }), encoding="utf-8")
        service2, notifier2 = self._service()
        self.assertEqual(service2.send_threshold_alerts(), [])
        self.assertEqual(notifier2.sent, [])

    def test_stale_snapshot_silent(self):
        self._seed([discovery_row("AMPG", 93, 74)], as_of=SESSION - timedelta(days=5))
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])


class TestWatchlistMoverAlerts(AlertTestCase):
    def test_price_move_fires(self):
        self.db.add_watchlist_symbol("MOVR", source="manual")
        seed_bars(self.db, "MOVR", [100.0] * 21 + [106.0])  # +6.0%, flat volume
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(alerts, ["📈 Watchlist movers: MOVR +6.0%"])
        self.assertEqual(len(notifier.sent), 1)

    def test_volume_surge_alone_fires(self):
        self.db.add_watchlist_symbol("VOLR", source="manual")
        seed_bars(self.db, "VOLR", [100.0] * 22, volumes=[1_000_000.0] * 21 + [3_000_000.0])
        service, _ = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(alerts, ["📈 Watchlist movers: VOLR +0.0% on 3.0x volume"])

    def test_multiple_movers_one_aggregated_message(self):
        self.db.add_watchlist_symbol("MOVR", source="manual")
        self.db.add_watchlist_symbol("VOLR", source="manual")
        seed_bars(self.db, "MOVR", [100.0] * 21 + [94.0])  # -6.0%
        seed_bars(self.db, "VOLR", [100.0] * 22, volumes=[1_000_000.0] * 21 + [3_000_000.0])
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0], "📈 Watchlist movers: MOVR -6.0%; VOLR +0.0% on 3.0x volume")
        self.assertEqual(len(notifier.sent), 1)

    def test_stale_last_bar_skips_symbol(self):
        self.db.add_watchlist_symbol("STAL", source="manual")
        seed_bars(self.db, "STAL", [100.0] * 21 + [106.0], end=SESSION - timedelta(days=3))
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])

    def test_missing_bars_fail_open_others_still_alert(self):
        self.db.add_watchlist_symbol("GHOST", source="manual")  # no cached bars at all
        self.db.add_watchlist_symbol("MOVR", source="manual")
        seed_bars(self.db, "MOVR", [100.0] * 21 + [106.0])
        service, _ = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(alerts, ["📈 Watchlist movers: MOVR +6.0%"])

    def test_second_call_same_session_silent(self):
        self.db.add_watchlist_symbol("MOVR", source="manual")
        seed_bars(self.db, "MOVR", [100.0] * 21 + [106.0])
        service, _ = self._service()
        self.assertEqual(len(service.send_threshold_alerts()), 1)
        service2, notifier2 = self._service()
        self.assertEqual(service2.send_threshold_alerts(), [])
        self.assertEqual(notifier2.sent, [])
        self.assertEqual(self._state()["watchlist_mover_on"]["MOVR"], SESSION.isoformat())


class TestDownDayTriggerAlert(AlertTestCase):
    def test_triggered_fires_once_per_as_of(self):
        self.db.save_down_day_rs_snapshot(down_day_payload(
            stocks=["S1", "S2", "S3", "S4", "S5", "S6"],
        ))
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(alerts, ["🔻 Down day: SPY -2.50% — holding up: S1, S2, S3, S4, S5"])
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(self._state()["down_day_alert_on"], SESSION.isoformat())
        # Same as_of again: silent.
        service2, notifier2 = self._service()
        self.assertEqual(service2.send_threshold_alerts(), [])
        self.assertEqual(notifier2.sent, [])

    def test_not_triggered_silent(self):
        self.db.save_down_day_rs_snapshot(down_day_payload(triggered=False, spy_return=0.4, stocks=[]))
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])

    def test_stale_snapshot_silent(self):
        self.db.save_down_day_rs_snapshot(down_day_payload(as_of=SESSION - timedelta(days=5)))
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])


class TestExistingAlertRegression(AlertTestCase):
    """With none of the new conditions met, the pre-existing alerts are unchanged."""

    def test_regime_flip_still_fires_alone(self):
        self.state_path.write_text(json.dumps({"last_regime": "Neutral"}), encoding="utf-8")
        today = date.today().isoformat()
        self.db.save_market_regime_snapshot({
            "as_of": today,
            "regime": "Risk-on",
            "score": 70.0,
            "confidence": "high",
            "volatility": {"vix": 15.5, "vix3m": 17.0, "term_inverted": False},
            "market_breadth": {"above_sma50_pct": 62.0},
        })
        service, notifier = self._service()
        alerts = service.send_threshold_alerts()
        self.assertEqual(alerts, [
            f"⚠️ **Regime change**: Neutral → Risk-on (score 70.0/100, as of {today})"
        ])
        self.assertEqual(len(notifier.sent), 1)
        self.assertEqual(self._state()["last_regime"], "Risk-on")


class TestStatePruning(AlertTestCase):
    def test_old_dict_entries_dropped_on_save(self):
        old = (date.today() - timedelta(days=90)).isoformat()
        fresh = (date.today() - timedelta(days=5)).isoformat()
        self.state_path.write_text(json.dumps({
            "discovery_high_conviction_on": {"OLD": old, "NEW": fresh},
            "watchlist_mover_on": {"OLD": old, "NEW": fresh},
            "down_day_alert_on": old,
        }), encoding="utf-8")
        service, notifier = self._service()
        self.assertEqual(service.send_threshold_alerts(), [])
        self.assertEqual(notifier.sent, [])
        state = self._state()
        self.assertEqual(state["discovery_high_conviction_on"], {"NEW": fresh})
        self.assertEqual(state["watchlist_mover_on"], {"NEW": fresh})
        # Scalar state keys are never pruned.
        self.assertEqual(state["down_day_alert_on"], old)


class TestIntradayTickWrapper(unittest.TestCase):
    """main._intraday_threshold_alerts() smoke: empty DB, no channel, env gate."""

    def setUp(self):
        from tests.litellm_stub import ensure_litellm_stub

        ensure_litellm_stub()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = self.tmp.name  # keep alert_state.json out of the repo
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        os.environ.pop("DATA_DIR", None)
        os.environ.pop("ALERTS_INTRADAY_ENABLED", None)
        self.tmp.cleanup()

    def test_disabled_env_makes_no_call(self):
        import main
        from src.services.daily_digest import DailyDigestService

        os.environ["ALERTS_INTRADAY_ENABLED"] = "false"
        with patch.object(DailyDigestService, "send_threshold_alerts") as mocked:
            main._intraday_threshold_alerts()  # must not raise
        mocked.assert_not_called()

    def test_enabled_empty_db_no_exception(self):
        import main

        os.environ.pop("ALERTS_INTRADAY_ENABLED", None)
        main._intraday_threshold_alerts()  # empty DB, no channel: fail-open, no raise


if __name__ == "__main__":
    unittest.main()
