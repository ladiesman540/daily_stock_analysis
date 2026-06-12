# -*- coding: utf-8 -*-
"""Tests for the discovery hit-rate scorecard: evaluate_flag, storage DAO,
ScorecardService.run_daily_evaluation/build_summary, API, and pipeline step.

All bar data is synthetic; the network fetch layer is stubbed so no test ever
hits yfinance/Massive.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager

TODAY = date.today()
AS_OF = date(2026, 1, 2)

SCORECARD_ENV_KEYS = (
    "SCORECARD_WINDOW_DAYS",
    "SCORECARD_HIT_PCT",
    "SCORECARD_FLOP_PCT",
    "SCORECARD_LOOKBACK_DAYS",
    "SCORECARD_FETCH_LIMIT",
    "SCORECARD_BOOTSTRAP_WEEKS",
    "SCORECARD_BOOTSTRAP_DEEP_DAYS",
)


def _make_db(tmp_dir: str) -> DatabaseManager:
    db_path = os.path.join(tmp_dir, "test_scorecard.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


def _teardown_db(tmp) -> None:
    DatabaseManager.reset_instance()
    Config.reset_instance()
    os.environ.pop("DATABASE_PATH", None)
    tmp.cleanup()


def _pop_scorecard_env() -> None:
    for key in SCORECARD_ENV_KEYS:
        os.environ.pop(key, None)


def closes(as_of, points):
    """[(day_offset, close)] -> [(date, close)] forward bars."""
    return [(as_of + timedelta(days=offset), close) for offset, close in points]


def outcome_row(symbol, as_of, status, **kwargs):
    row = {
        "as_of": as_of.isoformat() if isinstance(as_of, date) else as_of,
        "symbol": symbol,
        "entry_close": 100.0,
        "composite_score": 80.0,
        "candidate_score": 60.0,
        "screens": ["near_52w_high"],
        "simulated": False,
        "status": status,
    }
    row.update(kwargs)
    return row


def save_bars(db, symbol, points, *, base=None):
    """Persist synthetic (day_offset, close) bars relative to `base` into stock_daily."""
    base = base or AS_OF
    df = pd.DataFrame({
        "date": [base + timedelta(days=offset) for offset, _ in points],
        "close": [close for _, close in points],
        "volume": [1_000_000.0] * len(points),
    })
    db.save_daily_data(df, symbol, "TestSource")


class TestEvaluateFlag(unittest.TestCase):
    """Pure evaluate_flag boundary math (no DB, no network)."""

    def _eval(self, points, *, entry=100.0, today=None, **kwargs):
        from src.services.scorecard_service import evaluate_flag

        return evaluate_flag(
            entry, AS_OF, closes(AS_OF, points),
            today=today or AS_OF + timedelta(days=30), **kwargs,
        )

    def test_hit_at_exact_threshold(self):
        result = self._eval([(5, 110.0), (10, 120.0), (15, 150.0)])
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["hit_date"], AS_OF + timedelta(days=10))
        self.assertEqual(result["days_to_hit"], 10)
        self.assertEqual(result["max_gain_pct"], 20.0)
        self.assertEqual(result["current_return_pct"], 20.0)
        self.assertEqual(result["last_eval_bar_date"], AS_OF + timedelta(days=10))

    def test_flop_at_exact_threshold(self):
        result = self._eval([(5, 80.0)])
        self.assertEqual(result["status"], "flop")
        self.assertIsNone(result["hit_date"])
        self.assertEqual(result["current_return_pct"], -20.0)

    def test_first_touched_wins_flop_before_hit(self):
        result = self._eval([(3, 79.0), (10, 125.0)])
        self.assertEqual(result["status"], "flop")

    def test_first_touched_wins_hit_before_flop(self):
        result = self._eval([(3, 121.0), (10, 75.0)])
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["days_to_hit"], 3)

    def test_bars_walked_in_date_order_even_if_shuffled(self):
        result = self._eval([(10, 125.0), (3, 79.0)])
        self.assertEqual(result["status"], "flop")

    def test_hit_outside_window_does_not_count(self):
        result = self._eval([(91, 130.0)], today=AS_OF + timedelta(days=120))
        self.assertEqual(result["status"], "expired")
        self.assertIsNone(result["max_gain_pct"])

    def test_window_boundary_day_inclusive(self):
        result = self._eval([(90, 125.0)], today=AS_OF + timedelta(days=120))
        self.assertEqual(result["status"], "hit")
        self.assertEqual(result["days_to_hit"], 90)

    def test_open_inside_expiry_slack(self):
        result = self._eval([(89, 110.0)], today=AS_OF + timedelta(days=95))
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["current_return_pct"], 10.0)

    def test_expired_after_slack(self):
        result = self._eval([(20, 115.0), (89, 110.0)], today=AS_OF + timedelta(days=98))
        self.assertEqual(result["status"], "expired")
        self.assertEqual(result["max_gain_pct"], 15.0)
        self.assertEqual(result["current_return_pct"], 10.0)

    def test_no_forward_bars_today_flag_stays_open(self):
        result = self._eval([], today=AS_OF)
        self.assertEqual(result["status"], "open")
        for key in ("hit_date", "days_to_hit", "max_gain_pct", "current_return_pct", "last_eval_bar_date"):
            self.assertIsNone(result[key])

    def test_bars_on_or_before_as_of_ignored(self):
        result = self._eval([(0, 200.0), (-3, 10.0), (5, 105.0)])
        self.assertEqual(result["status"], "open")
        self.assertEqual(result["current_return_pct"], 5.0)
        self.assertEqual(result["max_gain_pct"], 5.0)

    def test_custom_thresholds(self):
        result = self._eval([(5, 110.0)], hit_pct=10.0)
        self.assertEqual(result["status"], "hit")
        result = self._eval([(5, 91.0)], flop_pct=-9.0)
        self.assertEqual(result["status"], "flop")


class TestBarsUntil(unittest.TestCase):
    """The single bootstrap truncation chokepoint: nothing after d may leak through."""

    BARS = [
        {"date": "2026-01-02", "close": 100.0},
        {"date": "2026-01-05", "close": 101.0},
        {"date": "2026-01-06", "close": 102.0},
    ]

    def _until(self, bars, d):
        from src.services.scorecard_service import _bars_until

        return _bars_until(bars, d)

    def test_bar_after_cutoff_is_invisible(self):
        sliced = self._until(self.BARS, "2026-01-05")
        self.assertEqual([bar["date"] for bar in sliced], ["2026-01-02", "2026-01-05"])

    def test_bar_exactly_at_cutoff_included(self):
        self.assertEqual(self._until(self.BARS, "2026-01-02"), [self.BARS[0]])

    def test_cutoff_before_all_bars_yields_empty(self):
        self.assertEqual(self._until(self.BARS, "2026-01-01"), [])

    def test_cutoff_after_all_bars_keeps_everything(self):
        self.assertEqual(self._until(self.BARS, "2026-02-01"), self.BARS)

    def test_datetime_strings_truncated_to_date(self):
        bars = [{"date": "2026-01-05T00:00:00", "close": 1.0}]
        self.assertEqual(self._until(bars, "2026-01-04"), [])
        self.assertEqual(self._until(bars, "2026-01-05"), bars)

    def test_missing_or_empty_dates_dropped(self):
        bars = [{"date": None, "close": 1.0}, {"close": 2.0}, {"date": "", "close": 3.0}]
        self.assertEqual(self._until(bars, "2026-01-05"), [])

    def test_empty_input(self):
        self.assertEqual(self._until([], "2026-01-05"), [])


class TestSampleBootstrapDates(unittest.TestCase):
    """Bootstrap as-of sampling: every 5th session, window-bound, never at/after real snapshots."""

    TODAY = date(2026, 6, 1)

    def _sample(self, trading_dates, **kwargs):
        from src.services.scorecard_service import _sample_bootstrap_dates

        kwargs.setdefault("today", self.TODAY)
        return _sample_bootstrap_dates(trading_dates, **kwargs)

    def _daily_sessions(self, days):
        return [(self.TODAY - timedelta(days=offset)).isoformat() for offset in range(days)]

    def test_every_fifth_session_within_window(self):
        sampled = self._sample(self._daily_sessions(100), weeks=10, before=None)
        start = (self.TODAY - timedelta(weeks=10)).isoformat()
        self.assertTrue(sampled)
        self.assertTrue(all(d >= start for d in sampled))
        self.assertEqual(sampled, sorted(sampled))
        self.assertEqual(sampled[0], start)  # ascending from the window start
        offsets = [(date.fromisoformat(d) - date.fromisoformat(sampled[0])).days for d in sampled]
        self.assertEqual(offsets, list(range(0, 70 + 1, 5)))

    def test_strictly_before_earliest_real_snapshot(self):
        cutoff = (self.TODAY - timedelta(days=30)).isoformat()
        sampled = self._sample(self._daily_sessions(100), weeks=10, before=cutoff)
        self.assertTrue(sampled)
        self.assertTrue(all(d < cutoff for d in sampled))
        # A sampled date exactly at the cutoff is excluded ("strictly before").
        at_cutoff = self._sample([cutoff], weeks=10, before=cutoff)
        self.assertEqual(at_cutoff, [])

    def test_no_real_snapshot_keeps_all_sampled_dates(self):
        sessions = self._daily_sessions(100)
        self.assertEqual(
            self._sample(sessions, weeks=10, before=None),
            self._sample(sessions, weeks=10, before="2099-01-01"),
        )

    def test_real_snapshot_older_than_window_yields_empty(self):
        cutoff = (self.TODAY - timedelta(weeks=20)).isoformat()
        self.assertEqual(self._sample(self._daily_sessions(100), weeks=10, before=cutoff), [])

    def test_duplicate_and_unsorted_sessions_normalized(self):
        sessions = self._daily_sessions(20)
        shuffled = sessions[::-1] + sessions[:5]
        self.assertEqual(
            self._sample(shuffled, weeks=4, before=None),
            self._sample(sessions, weeks=4, before=None),
        )


class TestOutcomeStorage(unittest.TestCase):
    """upsert_discovery_flag_outcomes / get_discovery_flag_outcomes / open symbols DAO."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def test_upsert_idempotent_and_preserves_created_at(self):
        self.db.upsert_discovery_flag_outcomes([outcome_row("HITX", TODAY - timedelta(days=10), "open")])
        first = self.db.get_discovery_flag_outcomes()["rows"][0]
        time.sleep(0.01)
        written = self.db.upsert_discovery_flag_outcomes([
            outcome_row("HITX", TODAY - timedelta(days=10), "hit",
                        hit_date=(TODAY - timedelta(days=2)).isoformat(), days_to_hit=8,
                        max_gain_pct=25.0, current_return_pct=25.0),
        ])
        self.assertEqual(written, 1)
        payload = self.db.get_discovery_flag_outcomes()
        self.assertEqual(payload["total"], 1)  # no duplicate row
        row = payload["rows"][0]
        self.assertEqual(row["status"], "hit")
        self.assertEqual(row["days_to_hit"], 8)
        self.assertEqual(row["created_at"], first["created_at"])
        self.assertGreater(row["updated_at"], first["updated_at"])

    def test_filters_total_and_pagination(self):
        self.db.upsert_discovery_flag_outcomes([
            outcome_row("AAA", TODAY - timedelta(days=5), "hit", screens=["near_52w_high"]),
            outcome_row("BBB", TODAY - timedelta(days=5), "flop", screens=["rs_top_decile"]),
            outcome_row("CCC", TODAY - timedelta(days=4), "open", screens=["near_52w_high", "unusual_volume"]),
            outcome_row("DDD", TODAY - timedelta(days=200), "expired", screens=[]),
            outcome_row("SIM", TODAY - timedelta(days=5), "hit", simulated=True, screens=[]),
        ])
        self.assertEqual(self.db.get_discovery_flag_outcomes()["total"], 5)
        self.assertEqual(self.db.get_discovery_flag_outcomes(status="hit")["total"], 2)
        self.assertEqual(self.db.get_discovery_flag_outcomes(status="hit", simulated=False)["total"], 1)
        self.assertEqual(self.db.get_discovery_flag_outcomes(simulated=True)["total"], 1)
        self.assertEqual(self.db.get_discovery_flag_outcomes(days=30)["total"], 4)
        # Screen filter matches the exact quoted token from screens_json.
        screened = self.db.get_discovery_flag_outcomes(screen="near_52w_high")
        self.assertEqual(screened["total"], 2)
        self.assertEqual({row["symbol"] for row in screened["rows"]}, {"AAA", "CCC"})
        self.assertEqual(self.db.get_discovery_flag_outcomes(screen="unusual_volume")["total"], 1)
        # Pagination: newest as_of first, total unaffected by limit/offset.
        page = self.db.get_discovery_flag_outcomes(limit=2, offset=0)
        self.assertEqual(page["total"], 5)
        self.assertEqual([row["symbol"] for row in page["rows"]], ["CCC", "AAA"])
        page2 = self.db.get_discovery_flag_outcomes(limit=2, offset=2)
        self.assertEqual([row["symbol"] for row in page2["rows"]], ["BBB", "SIM"])

    def test_open_symbols_distinct_real_only(self):
        self.db.upsert_discovery_flag_outcomes([
            outcome_row("AAA", TODAY - timedelta(days=10), "open"),
            outcome_row("AAA", TODAY - timedelta(days=5), "open"),
            outcome_row("BBB", TODAY - timedelta(days=5), "hit"),
            outcome_row("CCC", TODAY - timedelta(days=5), "open", simulated=True),
        ])
        self.assertEqual(self.db.get_open_discovery_flag_symbols(), ["AAA"])
        self.assertEqual(self.db.get_open_discovery_flag_symbols(simulated=True), ["CCC"])


class TestRunDailyEvaluation(unittest.TestCase):
    """Seeding from discovery history, evaluation on cached bars, catch-up fetch."""

    OLD = TODAY - timedelta(days=120)
    NEW = TODAY - timedelta(days=4)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _pop_scorecard_env()
        self.db = _make_db(self.tmp.name)
        self.db.save_discovery_snapshot({
            "as_of": self.OLD.isoformat(),
            "qualified_size": 4,
            "constituents": [
                {"symbol": "HITX", "close": 100.0, "composite_score": 95.0,
                 "candidate_score": 70.0, "near_52w_high": True, "rs_top_decile": True},
                {"symbol": "FLOP", "close": 100.0, "composite_score": 65.0, "unusual_volume": True},
                {"symbol": "EXPR", "close": 100.0, "composite_score": 50.0},
                {"symbol": "NOCL", "close": None},
            ],
        })
        self.db.save_discovery_snapshot({
            "as_of": self.NEW.isoformat(),
            "qualified_size": 3,
            "constituents": [
                {"symbol": "OPN1", "close": 50.0, "composite_score": 88.0, "near_52w_high": True},
                {"symbol": "HITX", "close": 130.0, "composite_score": 91.0, "near_52w_high": True},
                {"symbol": "OPN2", "close": 20.0, "composite_score": 72.0},
            ],
        })
        # Forward bars: HITX hits on day 10; FLOP flops on day 5 before a fake
        # rebound; EXPR never touches either threshold (window long over).
        save_bars(self.db, "HITX", [(0, 100.0), (10, 125.0)], base=self.OLD)
        save_bars(self.db, "FLOP", [(0, 100.0), (5, 78.0), (20, 130.0)], base=self.OLD)
        save_bars(self.db, "EXPR", [(0, 100.0), (30, 110.0)], base=self.OLD)
        save_bars(self.db, "OPN1", [(0, 50.0), (1, 51.0)], base=self.NEW)  # fresh bars
        # OPN2 has no cached bars at all -> stale once its flag is open.
        self.fetch_calls = []

    def tearDown(self):
        _pop_scorecard_env()
        _teardown_db(self.tmp)

    def _make_service(self, fetch=None):
        from src.services.scorecard_service import ScorecardService

        service = ScorecardService(db=self.db)
        recorder = self.fetch_calls

        def default_fetch(symbols, *, days, timeout_seconds=None):
            recorder.append(list(symbols))
            return {symbol: {"symbol": symbol, "bars": []} for symbol in symbols}

        service.free_data._fetch_histories = fetch or default_fetch
        return service

    def test_first_run_seeds_and_grades(self):
        result = self._make_service().run_daily_evaluation()
        self.assertEqual(result["seeded"], 7)
        self.assertEqual(result["skipped_invalid"], 1)  # NOCL: no entry close
        self.assertEqual(result["new"], 6)
        self.assertEqual(result["evaluated"], 6)
        self.assertEqual(result["hit"], 1)
        self.assertEqual(result["flop"], 1)
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["open"], 3)  # OPN1, OPN2, HITX (new flag, no forward bars)
        self.assertEqual(result["persisted"], 6)
        # No open flags persisted before this run -> nothing to catch up.
        self.assertEqual(self.fetch_calls, [])

        rows = {(row["as_of"], row["symbol"]): row
                for row in self.db.get_discovery_flag_outcomes()["rows"]}
        hit = rows[(self.OLD.isoformat(), "HITX")]
        self.assertEqual(hit["status"], "hit")
        self.assertEqual(hit["days_to_hit"], 10)
        self.assertEqual(hit["hit_date"], (self.OLD + timedelta(days=10)).isoformat())
        self.assertEqual(hit["max_gain_pct"], 25.0)
        self.assertEqual(hit["screens"], ["near_52w_high", "rs_top_decile"])
        self.assertEqual(hit["entry_close"], 100.0)
        self.assertEqual(rows[(self.OLD.isoformat(), "FLOP")]["status"], "flop")
        self.assertEqual(rows[(self.OLD.isoformat(), "EXPR")]["status"], "expired")
        self.assertEqual(rows[(self.NEW.isoformat(), "HITX")]["status"], "open")
        self.assertNotIn((self.OLD.isoformat(), "NOCL"), rows)

    def test_second_run_skips_terminal_and_fetches_only_stale_open(self):
        service = self._make_service()
        service.run_daily_evaluation()
        self.fetch_calls.clear()
        result = service.run_daily_evaluation()
        self.assertEqual(result["skipped_terminal"], 3)
        self.assertEqual(result["evaluated"], 3)
        self.assertEqual(result["open"], 3)
        # Open symbols: HITX (stale: bars 110 days old), OPN1 (fresh), OPN2 (no bars).
        self.assertEqual(result["catch_up"]["open_symbols"], 3)
        self.assertEqual(result["catch_up"]["stale"], 2)
        # One fetch batch; symbol order within it is not contractual.
        self.assertEqual(len(self.fetch_calls), 1)
        self.assertEqual(set(self.fetch_calls[0]), {"HITX", "OPN2"})

    def test_fetch_cap_respected(self):
        service = self._make_service()
        service.run_daily_evaluation()
        self.fetch_calls.clear()
        os.environ["SCORECARD_FETCH_LIMIT"] = "1"
        result = service.run_daily_evaluation()
        self.assertEqual(result["catch_up"]["stale"], 2)
        self.assertEqual(result["catch_up"]["fetched"], 1)
        self.assertEqual(self.fetch_calls, [["HITX"]])

    def test_catch_up_fetch_fails_open(self):
        service = self._make_service()
        service.run_daily_evaluation()

        def boom(symbols, *, days, timeout_seconds=None):
            raise RuntimeError("provider down")

        service.free_data._fetch_histories = boom
        result = service.run_daily_evaluation()
        self.assertEqual(result["evaluated"], 3)
        self.assertIn("provider down", result["catch_up"]["error"])

    def test_env_thresholds_applied(self):
        os.environ["SCORECARD_HIT_PCT"] = "5"
        result = self._make_service().run_daily_evaluation()
        # EXPR gained 10% on day 30 -> a hit under the 5% threshold.
        self.assertEqual(result["hit"], 2)
        rows = {row["symbol"]: row for row in self.db.get_discovery_flag_outcomes(status="hit")["rows"]}
        self.assertEqual(rows["EXPR"]["hit_threshold_pct"], 5.0)

    def test_lookback_guard_covers_expiry_slack(self):
        # A foolish SCORECARD_LOOKBACK_DAYS=90 must be raised to window + slack
        # + 1 (98): otherwise 91-97-day-old open flags would sit outside the
        # evaluation window forever. The 98-day-old flag proves effective >= 98.
        for age, symbol in ((97, "OPN97"), (98, "EXP98")):
            self.db.save_discovery_snapshot({
                "as_of": (TODAY - timedelta(days=age)).isoformat(),
                "qualified_size": 1,
                "constituents": [{"symbol": symbol, "close": 100.0, "composite_score": 60.0}],
            })
        os.environ["SCORECARD_LOOKBACK_DAYS"] = "90"
        self._make_service().run_daily_evaluation()
        rows = {row["symbol"]: row for row in self.db.get_discovery_flag_outcomes()["rows"]}
        self.assertEqual(rows["OPN97"]["status"], "open")     # still inside slack, kept alive
        self.assertEqual(rows["EXP98"]["status"], "expired")  # graded terminal, not stranded


class TestRunBootstrap(unittest.TestCase):
    """Retroactive bootstrap: lookahead guard, simulated separation, counts.

    Synthetic universe (daily bars TODAY-200 .. TODAY, all fetches stubbed):
    - SPY:  slow riser (the benchmark + trading-date source).
    - STDY: steady +0.3%/day riser -> always at its 52w high once enough bars
      exist; early flags hit +20%, late flags stay open.
    - SPKE: the lookahead trap. Early bars at 150, then flat at 100 through
      PIVOT (TODAY-60); from TODAY-59 it gaps to 160 and keeps rising (a fresh
      52w high). Pre-pivot dates must NOT flag it (pct_of_52w_high = 0.67);
      post-pivot sampled dates must.
    A real DiscoveryDaily snapshot sits at TODAY-30, so every simulated date
    must land strictly before it.
    """

    WEEKS = 20
    HISTORY_DAYS = 200
    PIVOT = TODAY - timedelta(days=60)          # last flat SPKE session
    EARLIEST_REAL = TODAY - timedelta(days=30)  # real discovery snapshot date

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _pop_scorecard_env()
        os.environ.pop("RESEARCH_DISCOVERY_MIN_ADV", None)
        os.environ.pop("RESEARCH_DISCOVERY_SYMBOL_LIMIT", None)
        self.db = _make_db(self.tmp.name)
        self.db.save_discovery_snapshot({
            "as_of": self.EARLIEST_REAL.isoformat(),
            "qualified_size": 1,
            "constituents": [{"symbol": "REAL", "close": 10.0}],
        })
        self.histories = {
            "SPY": self._bars(lambda i: 400.0 + 0.1 * i),
            "STDY": self._bars(lambda i: 50.0 * (1.003 ** i)),
            "SPKE": self._bars(self._spike_close),
        }
        self.fetch_calls = []

    def tearDown(self):
        _pop_scorecard_env()
        _teardown_db(self.tmp)

    def _bars(self, close_fn):
        start = TODAY - timedelta(days=self.HISTORY_DAYS)
        return [
            {"date": (start + timedelta(days=i)).isoformat(), "close": close_fn(i), "volume": 1_000_000.0}
            for i in range(self.HISTORY_DAYS + 1)
        ]

    def _spike_close(self, i):
        bar_date = TODAY - timedelta(days=self.HISTORY_DAYS) + timedelta(days=i)
        if i < 10:
            return 150.0  # the early 52w high the flat stretch sits 33% under
        if bar_date <= self.PIVOT:
            return 100.0
        return 160.0 + 0.05 * i  # fresh 52w highs, strictly after PIVOT

    def _run(self, **kwargs):
        from src.services.discovery_service import DiscoveryService
        from src.services.scorecard_service import ScorecardService

        service = ScorecardService(db=self.db)
        histories = self.histories
        recorder = self.fetch_calls

        def fake_fetch(symbols, *, days, timeout_seconds=None):
            recorder.append({"symbols": list(symbols), "days": days, "timeout": timeout_seconds})
            return {s: {"symbol": s, "bars": histories.get(s) or []} for s in symbols}

        service.free_data._fetch_histories = fake_fetch
        with patch.object(DiscoveryService, "_watchlist_symbols", return_value=set()), \
                patch.object(DiscoveryService, "_discovery_universe",
                             return_value=(["STDY", "SPKE"], {"warnings": []})):
            return service, service.run_bootstrap(weeks=self.WEEKS, **kwargs)

    def _expected_dates(self):
        from src.services.scorecard_service import _sample_bootstrap_dates

        return _sample_bootstrap_dates(
            [bar["date"] for bar in self.histories["SPY"]],
            weeks=self.WEEKS, before=self.EARLIEST_REAL.isoformat(), today=TODAY,
        )

    def test_counts_shape_and_totals(self):
        _, result = self._run()
        for key in ("dates_simulated", "flags", "hit", "flop", "expired", "open",
                    "fetched_symbols", "skipped_dates", "duration_s"):
            self.assertIn(key, result)
        expected_dates = self._expected_dates()
        self.assertTrue(expected_dates)
        self.assertEqual(result["dates_sampled"], len(expected_dates))
        self.assertEqual(result["dates_simulated"], len(expected_dates))
        self.assertEqual(result["skipped_dates"], 0)
        self.assertEqual(result["fetched_symbols"], 3)
        self.assertEqual(result["earliest_real_as_of"], self.EARLIEST_REAL.isoformat())
        self.assertEqual(result["flags"], result["hit"] + result["flop"] + result["expired"] + result["open"])
        self.assertGreater(result["hit"], 0)    # early STDY flags reached +20%
        self.assertGreater(result["open"], 0)   # late flags are still inside the window
        self.assertEqual(result["persisted"], result["flags"])
        # One deep fetch of universe + SPY.
        self.assertEqual(len(self.fetch_calls), 1)
        self.assertEqual(set(self.fetch_calls[0]["symbols"]), {"STDY", "SPKE", "SPY"})

    def test_all_rows_simulated_and_before_real_snapshot(self):
        self._run()
        sim = self.db.get_discovery_flag_outcomes(simulated=True)
        self.assertGreater(sim["total"], 0)
        self.assertTrue(all(row["simulated"] for row in sim["rows"]))
        self.assertTrue(all(row["as_of"] < self.EARLIEST_REAL.isoformat() for row in sim["rows"]))
        # Nothing leaked into the real population.
        self.assertEqual(self.db.get_discovery_flag_outcomes(simulated=False)["total"], 0)

    def test_lookahead_regression_spike_after_d_never_flags_at_d(self):
        self._run()
        rows = {(row["as_of"], row["symbol"]): row
                for row in self.db.get_discovery_flag_outcomes(simulated=True)["rows"]}
        expected_dates = self._expected_dates()
        pivot_iso = self.PIVOT.isoformat()
        pre_dates = [d for d in expected_dates if d <= pivot_iso and (d, "SPKE") in rows]
        post_dates = [d for d in expected_dates if d > pivot_iso]
        self.assertTrue(pre_dates and post_dates)
        for d in pre_dates:
            row = rows[(d, "SPKE")]
            self.assertEqual(row["screens"], [], f"SPKE wrongly flagged at {d} on post-{d} data")
            self.assertIsNone(row["candidate_score"])
        # The same symbol IS flagged once the spike is inside its visible bars,
        # and its entry is the spiked close, not the flat pre-pivot price.
        flagged_post = rows[(post_dates[0], "SPKE")]
        self.assertIn("near_52w_high", flagged_post["screens"])
        self.assertGreater(flagged_post["entry_close"], 150.0)
        # Steady riser is flagged at the very dates the trap symbol is not.
        self.assertIn("near_52w_high", rows[(pre_dates[-1], "STDY")]["screens"])
        # sector_tailwind can never appear: no historical rotation snapshots.
        self.assertTrue(all("sector_tailwind" not in row["screens"] for row in rows.values()))

    def test_outcomes_graded_forward_from_entry_close(self):
        self._run()
        rows = {(row["as_of"], row["symbol"]): row
                for row in self.db.get_discovery_flag_outcomes(simulated=True)["rows"]}
        expected_dates = self._expected_dates()
        # Earliest qualified date (the first sampled date lacks the 64-bar minimum).
        d = next(date_ for date_ in expected_dates if (date_, "STDY") in rows)
        row = rows[(d, "STDY")]
        self.assertEqual(row["status"], "hit")  # +0.3%/day -> +20% in ~62 days
        self.assertAlmostEqual(row["days_to_hit"], 62, delta=3)
        self.assertAlmostEqual(row["entry_close"], 50.0 * (1.003 ** (200 - (TODAY - date.fromisoformat(d)).days)), places=2)
        # Late flags can't have decided yet.
        last = rows[(expected_dates[-1], "STDY")]
        self.assertEqual(last["status"], "open")

    def test_real_row_with_same_key_untouched(self):
        expected_dates = self._expected_dates()
        target = expected_dates[-1]  # a date the bootstrap will definitely flag STDY on
        self.db.upsert_discovery_flag_outcomes([
            outcome_row("STDY", target, "hit", entry_close=999.0, days_to_hit=1, simulated=False),
        ])
        self._run()
        real = self.db.get_discovery_flag_outcomes(simulated=False)
        self.assertEqual(real["total"], 1)
        self.assertEqual(real["rows"][0]["entry_close"], 999.0)
        self.assertEqual(real["rows"][0]["days_to_hit"], 1)
        sim_rows = {row["as_of"]: row for row in self.db.get_discovery_flag_outcomes(simulated=True)["rows"]
                    if row["symbol"] == "STDY"}
        self.assertIn(target, sim_rows)
        self.assertNotEqual(sim_rows[target]["entry_close"], 999.0)

    def test_never_writes_discovery_daily(self):
        before = self.db.get_discovery_history(days=400)
        self._run()
        after = self.db.get_discovery_history(days=400)
        self.assertEqual(len(after), len(before))
        self.assertEqual(self.db.get_earliest_discovery_as_of(), self.EARLIEST_REAL.isoformat())

    def test_symbol_limit_caps_universe(self):
        _, result = self._run(symbol_limit=1)
        self.assertEqual(result["universe_size"], 1)
        symbols = {row["symbol"] for row in self.db.get_discovery_flag_outcomes(simulated=True)["rows"]}
        self.assertEqual(symbols, {"STDY"})

    def test_rerun_is_idempotent(self):
        _, first = self._run()
        _, second = self._run()
        self.assertEqual(first["flags"], second["flags"])
        self.assertEqual(self.db.get_discovery_flag_outcomes(simulated=True)["total"], first["flags"])

    def test_no_spy_bars_fails_open(self):
        self.histories["SPY"] = []
        _, result = self._run()
        self.assertEqual(result["dates_simulated"], 0)
        self.assertEqual(result["flags"], 0)
        self.assertTrue(any("No bootstrap dates" in w for w in result["warnings"]))


class TestPreviewBootstrapDates(unittest.TestCase):
    """--dry-run path: sampled dates from CACHED SPY bars only, zero network."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _pop_scorecard_env()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _pop_scorecard_env()
        _teardown_db(self.tmp)

    def _service(self):
        from src.services.scorecard_service import ScorecardService

        service = ScorecardService(db=self.db)

        def no_network(*args, **kwargs):  # the preview must never fetch
            raise AssertionError("preview_bootstrap_dates must not hit the network")

        service.free_data._fetch_histories = no_network
        return service

    def test_dates_from_cached_spy_only(self):
        save_bars(self.db, "SPY", [(offset, 400.0) for offset in range(0, 60)], base=TODAY - timedelta(days=59))
        self.db.save_discovery_snapshot({
            "as_of": (TODAY - timedelta(days=10)).isoformat(),
            "qualified_size": 0,
            "constituents": [],
        })
        payload = self._service().preview_bootstrap_dates(weeks=8)
        self.assertEqual(payload["weeks"], 8)
        self.assertEqual(payload["earliest_real_as_of"], (TODAY - timedelta(days=10)).isoformat())
        self.assertTrue(payload["dates"])
        self.assertTrue(all(d < payload["earliest_real_as_of"] for d in payload["dates"]))
        cached = {(TODAY - timedelta(days=59) + timedelta(days=offset)).isoformat() for offset in range(0, 60)}
        self.assertTrue(set(payload["dates"]) <= cached)

    def test_empty_cache_yields_empty_dates(self):
        payload = self._service().preview_bootstrap_dates(weeks=4)
        self.assertEqual(payload["dates"], [])
        self.assertIsNone(payload["earliest_real_as_of"])


class TestBuildSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        _pop_scorecard_env()
        self.db = _make_db(self.tmp.name)
        month_old = TODAY - timedelta(days=40)
        recent = TODAY - timedelta(days=10)
        self.db.upsert_discovery_flag_outcomes([
            # Flagged via screens.
            outcome_row("AAA", month_old, "hit", composite_score=95.0, screens=["near_52w_high"],
                        days_to_hit=10, max_gain_pct=25.0),
            outcome_row("BBB", month_old, "hit", composite_score=85.0,
                        screens=["near_52w_high", "rs_top_decile"], days_to_hit=20, max_gain_pct=30.0),
            outcome_row("CCC", recent, "flop", composite_score=65.0, screens=["near_52w_high"],
                        max_gain_pct=-22.0),
            outcome_row("DDD", recent, "expired", composite_score=50.0, screens=["rs_top_decile"]),
            # Flagged via candidate_score only (made the scored top-40, zero screens).
            # Open with a partial max_gain: must NOT contaminate avg_max_gain_pct.
            outcome_row("EEE", recent, "open", composite_score=72.0, candidate_score=55.0,
                        screens=[], max_gain_pct=50.0, current_return_pct=50.0),
            # Baseline-only: cleared the liquidity gate, passed nothing, never scored.
            outcome_row("FFF", recent, "open", composite_score=None, candidate_score=None, screens=[]),
            outcome_row("GGG", recent, "flop", candidate_score=None, screens=[], max_gain_pct=2.0),
            outcome_row("SIM", recent, "hit", simulated=True, screens=["near_52w_high"]),
        ])
        from src.services.scorecard_service import ScorecardService

        self.service = ScorecardService(db=self.db)

    def tearDown(self):
        _teardown_db(self.tmp)

    def test_flagged_headline_excludes_zero_screen_rows(self):
        flagged = self.service.build_summary()["real"]["flagged"]
        self.assertEqual(flagged["flags"], 5)  # FFF/GGG (no screen, no score) excluded
        self.assertEqual(flagged["hits"], 2)
        self.assertEqual(flagged["flops"], 1)
        self.assertEqual(flagged["expired"], 1)
        self.assertEqual(flagged["open"], 1)  # EEE: candidate-score-only still counts
        self.assertEqual(flagged["decided"], 4)
        self.assertEqual(flagged["batting_average"], 0.5)  # 2 / (2+1+1), open excluded
        self.assertEqual(flagged["avg_days_to_hit"], 15.0)  # (10 + 20) / 2
        # Decided rows only: (25 + 30 - 22) / 3; open EEE's partial 50 excluded.
        self.assertEqual(flagged["avg_max_gain_pct"], 11.0)

    def test_baseline_includes_all_rows_and_edge(self):
        real = self.service.build_summary()["real"]
        baseline = real["baseline"]
        self.assertEqual(baseline["flags"], 7)  # whole liquidity-qualified universe
        self.assertEqual(baseline["hits"], 2)
        self.assertEqual(baseline["flops"], 2)  # CCC + baseline-only GGG
        self.assertEqual(baseline["expired"], 1)
        self.assertEqual(baseline["open"], 2)
        self.assertEqual(baseline["decided"], 5)
        self.assertEqual(baseline["batting_average"], 0.4)
        # Decided rows only: (25 + 30 - 22 + 2) / 4; open EEE's partial 50 excluded.
        self.assertEqual(baseline["avg_max_gain_pct"], 8.75)
        self.assertEqual(real["edge"], round(0.5 - 0.4, 4))  # flagged BA - baseline BA

    def test_real_and_simulated_never_merged(self):
        summary = self.service.build_summary()
        self.assertEqual(summary["real"]["baseline"]["flags"], 7)
        self.assertEqual(summary["simulated"]["baseline"]["flags"], 1)
        self.assertEqual(summary["simulated"]["flagged"]["batting_average"], 1.0)
        self.assertTrue(any("optimistic ceiling" in c for c in summary["caveats"]))
        without = self.service.build_summary(include_simulated=False)
        self.assertIsNone(without["simulated"])
        self.assertEqual(without["real"]["baseline"]["flags"], 7)
        self.assertFalse(any("optimistic ceiling" in c for c in without["caveats"]))

    def test_no_simulated_rows_drops_survivorship_caveat(self):
        db2_tmp = tempfile.TemporaryDirectory()
        try:
            db2 = _make_db(db2_tmp.name)
            db2.upsert_discovery_flag_outcomes([outcome_row("AAA", TODAY - timedelta(days=5), "hit")])
            from src.services.scorecard_service import ScorecardService

            summary = ScorecardService(db=db2).build_summary()
            self.assertIsNone(summary["simulated"])
            self.assertFalse(any("optimistic ceiling" in c for c in summary["caveats"]))
        finally:
            _teardown_db(db2_tmp)

    def test_by_screen_from_screens_json(self):
        by_screen = self.service.build_summary()["real"]["by_screen"]
        # Unchanged by the flagged/baseline split: zero-screen rows never had keys.
        self.assertEqual(set(by_screen), {"near_52w_high", "rs_top_decile"})
        self.assertEqual(by_screen["near_52w_high"]["hit"], 2)
        self.assertEqual(by_screen["near_52w_high"]["flop"], 1)
        self.assertEqual(by_screen["near_52w_high"]["batting_average"], round(2 / 3, 4))
        self.assertEqual(by_screen["rs_top_decile"]["hit"], 1)
        self.assertEqual(by_screen["rs_top_decile"]["expired"], 1)
        self.assertEqual(by_screen["rs_top_decile"]["batting_average"], 0.5)

    def test_by_score_band_and_month(self):
        real = self.service.build_summary()["real"]
        bands = real["by_score_band"]
        self.assertEqual(bands["90+"]["hit"], 1)
        self.assertEqual(bands["80-90"]["hit"], 1)
        self.assertEqual(bands["60-70"]["flop"], 1)
        self.assertEqual(bands["<60"]["expired"], 1)
        self.assertEqual(bands["70-80"]["open"], 1)
        # Flagged rows only: baseline-only GGG (score 80) never reaches 80-90.
        self.assertEqual(bands["80-90"]["total"], 1)
        self.assertEqual(sum(block["total"] for block in bands.values()), 5)
        by_month = real["by_month"]
        self.assertEqual(sum(block["total"] for block in by_month.values()), 5)  # flagged only
        for key in by_month:
            self.assertRegex(key, r"^\d{4}-\d{2}$")

    def test_empty_summary(self):
        empty_tmp = tempfile.TemporaryDirectory()
        try:
            db2 = _make_db(empty_tmp.name)
            from src.services.scorecard_service import ScorecardService

            summary = ScorecardService(db=db2).build_summary()
            self.assertEqual(summary["real"]["baseline"]["flags"], 0)
            self.assertIsNone(summary["real"]["flagged"]["batting_average"])
            self.assertIsNone(summary["real"]["edge"])
            self.assertIsNone(summary["simulated"])
        finally:
            _teardown_db(empty_tmp)


class ScorecardApiTestBase(unittest.TestCase):
    """TestClient scaffolding shared by the scorecard API test classes (no tests here)."""

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


class TestScorecardApi(ScorecardApiTestBase):
    """GET/POST /api/v1/research/scorecard* via TestClient (no network)."""

    def _seed(self):
        DatabaseManager.get_instance().upsert_discovery_flag_outcomes([
            outcome_row("AAA", TODAY - timedelta(days=20), "hit", screens=["near_52w_high"]),
            outcome_row("BBB", TODAY - timedelta(days=20), "flop", screens=["rs_top_decile"]),
            # Baseline-only: zero screens, never scored.
            outcome_row("CCC", TODAY - timedelta(days=5), "open", screens=[], candidate_score=None),
            outcome_row("SIM", TODAY - timedelta(days=5), "hit", simulated=True),
        ])

    def test_summary_empty_db(self):
        resp = self.client.get("/api/v1/research/scorecard")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "missing")
        self.assertIn("summary", data)

    def test_summary_seeded(self):
        self._seed()
        resp = self.client.get("/api/v1/research/scorecard")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["real"]["baseline"]["flags"], 3)
        self.assertEqual(data["real"]["flagged"]["flags"], 2)  # CCC baseline-only
        self.assertEqual(data["real"]["flagged"]["batting_average"], 0.5)
        self.assertEqual(data["real"]["edge"], 0.0)
        self.assertEqual(data["simulated"]["baseline"]["flags"], 1)
        self.assertTrue(data["caveats"])

    def test_flags_filters_and_pagination(self):
        self._seed()
        resp = self.client.get("/api/v1/research/scorecard/flags?status=hit")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["total"], 2)
        self.assertEqual({row["symbol"] for row in data["rows"]}, {"AAA", "SIM"})
        resp = self.client.get("/api/v1/research/scorecard/flags?status=hit&simulated=false")
        self.assertEqual([row["symbol"] for row in resp.json()["rows"]], ["AAA"])
        resp = self.client.get("/api/v1/research/scorecard/flags?screen=rs_top_decile")
        self.assertEqual([row["symbol"] for row in resp.json()["rows"]], ["BBB"])
        resp = self.client.get("/api/v1/research/scorecard/flags?limit=1&offset=1")
        data = resp.json()
        self.assertEqual(data["total"], 4)
        self.assertEqual(len(data["rows"]), 1)
        resp = self.client.get("/api/v1/research/scorecard/flags?status=bogus")
        self.assertEqual(resp.status_code, 422)

    def test_run_endpoint_uses_service(self):
        fake = {"as_of": TODAY.isoformat(), "seeded": 3, "evaluated": 3,
                "open": 3, "hit": 0, "flop": 0, "expired": 0, "persisted": 3}
        with patch("src.services.scorecard_service.ScorecardService.run_daily_evaluation", return_value=fake):
            resp = self.client.post("/api/v1/research/scorecard/run")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["evaluated"], 3)


class TestBootstrapEndpoint(ScorecardApiTestBase):
    """POST /scorecard/bootstrap: daemon thread + module lock (service stubbed)."""

    LOCK_TIMEOUT = 5.0

    def _post(self):
        return self.client.post("/api/v1/research/scorecard/bootstrap").json()

    def _wait_for_lock_release(self):
        from api.v1.endpoints import research as research_endpoints

        deadline = time.time() + self.LOCK_TIMEOUT
        while research_endpoints._bootstrap_lock.locked() and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(research_endpoints._bootstrap_lock.locked(), "bootstrap lock never released")

    def test_second_request_while_running_reports_already_running(self):
        import threading

        started = threading.Event()
        release = threading.Event()

        def slow_bootstrap(service, **kwargs):
            started.set()
            release.wait(timeout=self.LOCK_TIMEOUT)
            return {"flags": 0}

        try:
            with patch("src.services.scorecard_service.ScorecardService.run_bootstrap", slow_bootstrap):
                self.assertEqual(self._post(), {"status": "started"})
                self.assertTrue(started.wait(timeout=self.LOCK_TIMEOUT))
                self.assertEqual(self._post(), {"status": "already_running"})
                release.set()
                self._wait_for_lock_release()
                # Once finished, a new bootstrap can start again.
                started.clear()
                self.assertEqual(self._post(), {"status": "started"})
                self.assertTrue(started.wait(timeout=self.LOCK_TIMEOUT))
        finally:
            release.set()
        self._wait_for_lock_release()

    def test_lock_released_when_bootstrap_raises(self):
        with patch("src.services.scorecard_service.ScorecardService.run_bootstrap",
                   side_effect=RuntimeError("boom")):
            self.assertEqual(self._post(), {"status": "started"})
            self._wait_for_lock_release()
            self.assertEqual(self._post(), {"status": "started"})
            self._wait_for_lock_release()


class TestPipelineStep(unittest.TestCase):
    """scorecard step registration + runner smoke."""

    def test_step_registered_right_after_discovery(self):
        from scripts.daily_snapshot import ALL_STEPS, STEP_RUNNERS

        self.assertIn("scorecard", ALL_STEPS)
        self.assertEqual(ALL_STEPS.index("scorecard"), ALL_STEPS.index("discovery") + 1)
        self.assertIn("scorecard", STEP_RUNNERS)

    def test_runner_invokes_service(self):
        from scripts.daily_snapshot import run_scorecard

        fake = {"seeded": 0, "evaluated": 0, "persisted": 0}
        # Patch the class so the runner never builds real DB/data services here.
        with patch("src.services.scorecard_service.ScorecardService") as service_cls:
            service_cls.return_value.run_daily_evaluation.return_value = fake
            self.assertEqual(run_scorecard({}), fake)


if __name__ == "__main__":
    unittest.main()
