# -*- coding: utf-8 -*-
"""Tests for the new-ideas DiscoveryService, its storage trio, and endpoints.

All bar data is synthetic; the network fetch layer is stubbed so no test ever
hits yfinance/Massive.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager

END = date(2026, 6, 5)


def _make_db(tmp_dir: str) -> DatabaseManager:
    db_path = os.path.join(tmp_dir, "test_discovery.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


def _teardown_db(tmp) -> None:
    DatabaseManager.reset_instance()
    Config.reset_instance()
    os.environ.pop("DATABASE_PATH", None)
    tmp.cleanup()


def make_bars(*, n=300, start=50.0, drift=0.002, pattern=None, volume=1_000_000.0,
              last_volume=None, volume_pattern=None, end=END):
    """Synthetic daily bars ending at `end`. `pattern(i)` overrides the constant drift;
    `volume_pattern(i)` overrides the constant volume (takes precedence over last_volume)."""
    bars = []
    price = start
    for i in range(n):
        ret = pattern(i) if pattern else drift
        price *= (1 + ret)
        if volume_pattern:
            vol = volume_pattern(i)
        else:
            vol = last_volume if (last_volume is not None and i == n - 1) else volume
        bars.append({
            "date": (end - timedelta(days=n - 1 - i)).isoformat(),
            "close": round(price, 6),
            "volume": vol,
        })
    return bars


def history(symbol, bars, name=None, market_cap=None):
    return {"symbol": symbol, "name": name or symbol, "bars": bars, "market_cap": market_cap}


# Alternating-return patterns with enough variance for correlation math.
def xlk_pattern(i):
    return 0.01 if i % 2 == 0 else -0.004


def xle_pattern(i):
    return -0.004 if i % 2 == 0 else 0.008


class TestScreenRow(unittest.TestCase):
    """Pure per-symbol screen computation on synthetic bars."""

    def _row(self, bars, **kwargs):
        from src.services.discovery_service import _screen_row

        defaults = dict(min_price=5.0, min_avg_dollar_volume=20_000_000.0,
                        spy_perf_1m=2.0, spy_perf_3m=6.0)
        defaults.update(kwargs)
        return _screen_row("TEST", {"symbol": "TEST", "bars": bars}, **defaults)

    def test_near_52w_high_true_for_rising_stock(self):
        row = self._row(make_bars(drift=0.003))
        self.assertTrue(row["qualified"])
        self.assertTrue(row["near_52w_high"])
        self.assertGreaterEqual(row["pct_of_52w_high"], 0.98)

    def test_near_52w_high_false_after_drawdown(self):
        # Rise then fall 20% from the high.
        def pattern(i):
            return 0.005 if i < 250 else -0.005
        row = self._row(make_bars(pattern=pattern))
        self.assertTrue(row["qualified"])
        self.assertFalse(row["near_52w_high"])

    def test_unusual_volume_requires_up_day(self):
        # 3x average volume on an up day -> flagged.
        up = self._row(make_bars(drift=0.002, last_volume=3_000_000.0))
        self.assertTrue(up["up_day"])
        self.assertTrue(up["unusual_volume"])
        self.assertAlmostEqual(up["volume_ratio"], 3.0, places=1)
        # Same spike on a down day -> not flagged.
        def down_last(i):
            return -0.02 if i == 299 else 0.002
        down = self._row(make_bars(pattern=down_last, last_volume=3_000_000.0))
        self.assertFalse(down["up_day"])
        self.assertFalse(down["unusual_volume"])

    def test_relative_strength_vs_spy(self):
        row = self._row(make_bars(drift=0.004), spy_perf_1m=2.0, spy_perf_3m=6.0)
        self.assertIsNotNone(row["rs_1m_pct"])
        self.assertAlmostEqual(row["rs_1m_pct"], row["perf_1m_pct"] - 2.0, places=2)
        self.assertAlmostEqual(row["rs_3m_pct"], row["perf_3m_pct"] - 6.0, places=2)
        # No SPY data -> RS not computable.
        no_spy = self._row(make_bars(drift=0.004), spy_perf_1m=None, spy_perf_3m=None)
        self.assertIsNone(no_spy["rs_1m_pct"])
        self.assertIsNone(no_spy["rs_3m_pct"])

    def test_liquidity_gate(self):
        cheap = self._row(make_bars(start=1.0, drift=0.0))  # below $5
        self.assertFalse(cheap["qualified"])
        illiquid = self._row(make_bars(start=10.0, drift=0.001, volume=100_000.0))  # ~$1M ADV
        self.assertFalse(illiquid["qualified"])
        missing = self._row([])
        self.assertEqual(missing["status"], "missing")
        self.assertFalse(missing["qualified"])

    # -------------------------------------------------- quiet accumulation

    @staticmethod
    def _flat_tail(i):
        """Price rises, then goes dead flat over the last 5 sessions."""
        return 0.003 if i < 295 else 0.0

    @staticmethod
    def _lifted_volume(i):
        """Volume doubles over the last 5 sessions (sustained, not a spike)."""
        return 2_000_000.0 if i >= 295 else 1_000_000.0

    def test_quiet_accumulation_true_on_sustained_volume_flat_price(self):
        row = self._row(make_bars(pattern=self._flat_tail, volume_pattern=self._lifted_volume))
        self.assertTrue(row["quiet_accumulation"])
        self.assertAlmostEqual(row["quiet_accum_volume_ratio"], 2.0, places=2)
        # A 1.67x latest bar on a flat day is NOT the unusual-volume screen.
        self.assertFalse(row["unusual_volume"])

    def test_quiet_accumulation_false_without_flat_price(self):
        # Same volume lift, but price keeps ripping (+1%/day -> ~5.1% over 5 sessions).
        def rising_tail(i):
            return 0.003 if i < 295 else 0.01
        row = self._row(make_bars(pattern=rising_tail, volume_pattern=self._lifted_volume))
        self.assertAlmostEqual(row["quiet_accum_volume_ratio"], 2.0, places=2)
        self.assertFalse(row["quiet_accumulation"])

    def test_quiet_accumulation_false_on_single_spike(self):
        # One 3x day among 5 quiet ones averages 1.4x -- below the 1.5x gate.
        row = self._row(make_bars(pattern=self._flat_tail, last_volume=3_000_000.0))
        self.assertAlmostEqual(row["quiet_accum_volume_ratio"], 1.4, places=2)
        self.assertFalse(row["quiet_accumulation"])

    def test_quiet_accumulation_false_on_short_history(self):
        # 25 bars < the 26 required (window*5 + 1): flag off, ratio not computed.
        row = self._row(make_bars(n=25, pattern=self._flat_tail, volume_pattern=self._lifted_volume))
        self.assertFalse(row["quiet_accumulation"])
        self.assertIsNone(row["quiet_accum_volume_ratio"])
        empty = self._row([])
        self.assertFalse(empty["quiet_accumulation"])
        self.assertIsNone(empty["quiet_accum_volume_ratio"])

    # ----------------------------------------------- beaten-down reversal

    @staticmethod
    def _reversal_pattern(i):
        """Rise, crash ~70% from the high, then a one-month recovery."""
        if i < 150:
            return 0.005
        if i < 250:
            return -0.012
        return 0.004

    def test_beaten_down_reversal_true(self):
        row = self._row(make_bars(pattern=self._reversal_pattern, last_volume=1_500_000.0))
        self.assertLessEqual(row["pct_of_52w_high"], 0.60)
        self.assertGreater(row["rs_1m_pct"], 0)
        self.assertGreaterEqual(row["volume_ratio"], 1.25)
        self.assertTrue(row["beaten_down_reversal"])
        # 1.5x participation is not the 2x unusual-volume screen.
        self.assertFalse(row["unusual_volume"])

    def test_beaten_down_reversal_false_when_not_beaten_down(self):
        # Strong uptrend near its high: volume + RS gates pass, drawdown gate fails.
        row = self._row(make_bars(drift=0.004, last_volume=1_500_000.0))
        self.assertGreater(row["pct_of_52w_high"], 0.60)
        self.assertFalse(row["beaten_down_reversal"])

    def test_beaten_down_reversal_false_without_positive_rs(self):
        # Still falling: deep drawdown + volume, but 1M RS is negative.
        def falling(i):
            return 0.005 if i < 150 else -0.008
        row = self._row(make_bars(pattern=falling, last_volume=1_500_000.0))
        self.assertLessEqual(row["pct_of_52w_high"], 0.60)
        self.assertLess(row["rs_1m_pct"], 0)
        self.assertFalse(row["beaten_down_reversal"])
        # None-safe: no SPY benchmark -> RS unknown -> flag stays off.
        no_spy = self._row(
            make_bars(pattern=self._reversal_pattern, last_volume=1_500_000.0),
            spy_perf_1m=None, spy_perf_3m=None,
        )
        self.assertIsNone(no_spy["rs_1m_pct"])
        self.assertFalse(no_spy["beaten_down_reversal"])

    def test_beaten_down_reversal_false_without_volume(self):
        # Recovery on average (1.0x) volume: participation gate fails.
        row = self._row(make_bars(pattern=self._reversal_pattern))
        self.assertLessEqual(row["pct_of_52w_high"], 0.60)
        self.assertGreater(row["rs_1m_pct"], 0)
        self.assertLess(row["volume_ratio"], 1.25)
        self.assertFalse(row["beaten_down_reversal"])


class TestBuildSnapshot(unittest.TestCase):
    """End-to-end build_snapshot with a stubbed universe + fetch layer."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        os.environ.pop("RESEARCH_DISCOVERY_SYMBOL_LIMIT", None)
        os.environ.pop("RESEARCH_DISCOVERY_MIN_ADV", None)
        self.db = _make_db(self.tmp.name)
        self.db.add_watchlist_symbol("WLST", source="manual")
        self.db.save_sector_rotation_snapshot({
            "as_of": END.isoformat(),
            "constituents": [
                {"symbol": "XLK", "group": "sectors", "rank_change": {"1M": 2}},
                {"symbol": "XLE", "group": "sectors", "rank_change": {"1M": -1}},
            ],
        })
        self.histories = {
            "SPY": history("SPY", make_bars(start=400.0, drift=0.001)),
            "XLK": history("XLK", make_bars(start=200.0, pattern=xlk_pattern)),
            "XLE": history("XLE", make_bars(start=80.0, pattern=xle_pattern)),
            "STRN": history("STRN", make_bars(start=50.0, drift=0.004)),
            "WEAK": history("WEAK", make_bars(start=100.0, drift=-0.001)),
            "VOLX": history("VOLX", make_bars(start=30.0, drift=0.001, last_volume=3_000_000.0)),
            "SECT": history("SECT", make_bars(start=60.0, pattern=xlk_pattern)),
            "SECF": history("SECF", make_bars(start=40.0, pattern=xle_pattern)),
            "ILLQ": history("ILLQ", make_bars(start=10.0, drift=0.002, volume=100_000.0)),
            "CHEP": history("CHEP", make_bars(start=1.0, drift=0.001)),
            "WLST": history("WLST", make_bars(start=70.0, drift=0.003)),
        }
        self.universe = ["STRN", "WEAK", "VOLX", "SECT", "SECF", "ILLQ", "CHEP", "WLST"]
        self.service = self._make_service()

    def tearDown(self):
        _teardown_db(self.tmp)

    def _make_service(self):
        from src.services.discovery_service import DiscoveryService

        service = DiscoveryService(db=self.db)
        universe = list(self.universe)
        service._discovery_universe = lambda *, limit, watchlist: (
            universe[:limit],
            {"label": "test", "source": "test", "total_available": len(universe), "warnings": []},
        )
        histories = self.histories
        service.free_data._fetch_histories = lambda symbols, *, days, timeout_seconds=None: {
            symbol: histories.get(symbol, {"symbol": symbol, "bars": []}) for symbol in symbols
        }
        return service

    def test_snapshot_screens_and_exclusions(self):
        payload = self.service.build_snapshot()
        by_symbol = {row["symbol"]: row for row in payload["constituents"]}

        # Liquidity gate + exclusions.
        self.assertEqual(set(by_symbol), {"STRN", "WEAK", "VOLX", "SECT", "SECF"})
        self.assertNotIn("WLST", by_symbol)   # watchlist excluded
        self.assertNotIn("ILLQ", by_symbol)   # ADV below $20M
        self.assertNotIn("CHEP", by_symbol)   # price below $5
        self.assertEqual(payload["qualified_size"], 5)
        self.assertEqual(payload["universe_size"], len(self.universe) - 1)  # WLST filtered pre-fetch
        self.assertEqual(payload["as_of"], END.isoformat())

        # 52-week high screen.
        self.assertTrue(by_symbol["STRN"]["near_52w_high"])
        self.assertFalse(by_symbol["WEAK"]["near_52w_high"])

        # RS top decile: strongest grinder tops the RS percentile, the decliner does not.
        self.assertEqual(by_symbol["STRN"]["rs_percentile"], 1.0)
        self.assertTrue(by_symbol["STRN"]["rs_top_decile"])
        self.assertFalse(by_symbol["WEAK"]["rs_top_decile"])

        # Unusual volume screen.
        self.assertTrue(by_symbol["VOLX"]["unusual_volume"])
        self.assertFalse(by_symbol["STRN"]["unusual_volume"])

        # Sector tailwind: SECT tracks rising XLK, SECF tracks falling-rank XLE.
        self.assertEqual(by_symbol["SECT"]["sector_symbol"], "XLK")
        self.assertTrue(by_symbol["SECT"]["sector_tailwind"])
        self.assertEqual(by_symbol["SECF"]["sector_symbol"], "XLE")
        self.assertFalse(by_symbol["SECF"]["sector_tailwind"])

    def test_ranking_and_candidate_scores(self):
        payload = self.service.build_snapshot()
        rows = payload["constituents"]
        scores = [row["composite_score"] for row in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual([row["rank"] for row in rows], list(range(1, len(rows) + 1)))
        # Top names carry the Signals-page scorer language.
        for row in rows:  # all 5 are within the scored top-40
            self.assertIn("candidate_score", row)
            self.assertIn(row["checklist_status"], {"pass", "watchlist", "reject", "no_data"})
            self.assertTrue(row["entry_zone"])
            self.assertTrue(row["reason"])
        # The strongest grinder should outrank the decliner.
        self.assertLess(rows.index(next(r for r in rows if r["symbol"] == "STRN")),
                        rows.index(next(r for r in rows if r["symbol"] == "WEAK")))

    def test_composite_scores_regression(self):
        """Composite stays momentum-only: the new watch flags must not move it.

        Expected values were captured from this exact fixture BEFORE the
        quiet_accumulation / beaten_down_reversal screens were added.
        """
        payload = self.service.build_snapshot()
        composites = {row["symbol"]: row["composite_score"] for row in payload["constituents"]}
        self.assertEqual(
            composites,
            {"SECF": 75.0, "STRN": 66.67, "VOLX": 66.67, "SECT": 46.67, "WEAK": 0.0},
        )
        # None of the fixture names trips the new flags (no false positives).
        for row in payload["constituents"]:
            self.assertFalse(row["quiet_accumulation"])
            self.assertFalse(row["beaten_down_reversal"])

    def test_new_screen_flag_scored_outside_top_n(self):
        """A flagged row below the composite top-N still gets the scorer language."""
        def lifted_volume(i):
            return 2_000_000.0 if i >= 295 else 1_000_000.0
        # Slight downtrend + flat tail volume lift: quiet accumulation fires,
        # but the momentum composite ranks it near the bottom.
        self.histories["QUIE"] = history(
            "QUIE", make_bars(start=30.0, drift=-0.0005, volume_pattern=lifted_volume)
        )
        self.universe.append("QUIE")
        service = self._make_service()
        with patch("src.services.discovery_service.SCORED_TOP_N", 2):
            payload = service.build_snapshot()
        by_symbol = {row["symbol"]: row for row in payload["constituents"]}
        quie = by_symbol["QUIE"]
        self.assertTrue(quie["quiet_accumulation"])
        self.assertGreater(quie["rank"], 2)  # outside the scored top-N
        self.assertIn("candidate_score", quie)
        self.assertIn("quiet accumulation", quie["reason"])
        # Unflagged rows outside the top-N stay unscored.
        weak = by_symbol["WEAK"]
        self.assertGreater(weak["rank"], 2)
        self.assertFalse(weak["quiet_accumulation"] or weak["beaten_down_reversal"])
        self.assertNotIn("candidate_score", weak)

    def test_sector_bonus_raises_composite(self):
        with_bonus = {r["symbol"]: r for r in self.service.build_snapshot()["constituents"]}
        # Re-run with no rotation risers: same data, no tailwind bonus anywhere.
        service = self._make_service()
        service._sector_context = lambda: (["XLK", "XLE"], set(), [])
        without_bonus = {r["symbol"]: r for r in service.build_snapshot()["constituents"]}
        self.assertFalse(without_bonus["SECT"]["sector_tailwind"])
        self.assertGreater(
            with_bonus["SECT"]["composite_score"],
            without_bonus["SECT"]["composite_score"],
        )

    def test_missing_benchmark_adds_warning(self):
        histories = {k: v for k, v in self.histories.items() if k != "SPY"}
        service = self._make_service()
        service.free_data._fetch_histories = lambda symbols, *, days, timeout_seconds=None: {
            symbol: histories.get(symbol, {"symbol": symbol, "bars": []}) for symbol in symbols
        }
        payload = service.build_snapshot()
        self.assertTrue(any("relative-strength screen skipped" in w for w in payload["warnings"]))
        for row in payload["constituents"]:
            self.assertIsNone(row["rs_1m_pct"])

    def test_run_daily_snapshot_persists(self):
        payload = self.service.run_daily_snapshot()
        self.assertEqual(payload["persisted"]["as_of"], END.isoformat())
        saved = self.db.get_latest_discovery_snapshot()
        self.assertEqual(saved["as_of"], END.isoformat())
        self.assertEqual(saved["qualified_size"], 5)
        self.assertEqual({r["symbol"] for r in saved["constituents"]},
                         {"STRN", "WEAK", "VOLX", "SECT", "SECF"})


class TestDiscoveryUniverse(unittest.TestCase):
    """Universe priority order, ETF/watchlist/test-issue exclusion."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ.pop("RESEARCH_US_WATCHLIST", None)
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def _make_service(self, directory_rows):
        from src.services.discovery_service import DiscoveryService

        service = DiscoveryService(db=self.db)
        service.free_data._fetch_nasdaq_directory = lambda: (directory_rows, "ts")
        service.free_data.market_data.get_default_us_universe = lambda *, limit=250: ["DFLT", "ETF1"]
        return service

    def test_priority_and_exclusions(self):
        self.db.save_discovery_snapshot({
            "as_of": (END - timedelta(days=1)).isoformat(),
            "universe_size": 5,
            "qualified_size": 1,
            "constituents": [{"symbol": "PREV"}],
        })
        rows = [
            {"symbol": "AAA", "is_etf": False, "test_issue": "N"},
            {"symbol": "PREV", "is_etf": False, "test_issue": "N"},
            {"symbol": "DFLT", "is_etf": False, "test_issue": "N"},
            {"symbol": "WLST", "is_etf": False, "test_issue": "N"},
            {"symbol": "ETF1", "is_etf": True, "test_issue": "N"},
            {"symbol": "TSTQ", "is_etf": False, "test_issue": "Y"},
            {"symbol": "BBB", "is_etf": False, "test_issue": "N"},
        ]
        service = self._make_service(rows)
        symbols, meta = service._discovery_universe(limit=10, watchlist={"WLST"})
        # Priority order: yesterday's qualified, then default liquid names, then sample.
        self.assertEqual(symbols[:2], ["PREV", "DFLT"])
        self.assertNotIn("ETF1", symbols)  # ETF excluded
        self.assertNotIn("WLST", symbols)  # watchlist excluded
        self.assertNotIn("TSTQ", symbols)  # test issue excluded
        self.assertIn("AAA", symbols)
        self.assertEqual(meta["total_available"], 5)  # stock pool size

    def test_directory_failure_falls_back_to_priority_list(self):
        from src.services.discovery_service import DiscoveryService

        service = DiscoveryService(db=self.db)

        def boom():
            raise RuntimeError("nasdaq down")

        service.free_data._fetch_nasdaq_directory = boom
        service.free_data.market_data.get_default_us_universe = lambda *, limit=250: ["DFLT", "SPY"]
        symbols, meta = service._discovery_universe(limit=10, watchlist=set())
        self.assertIn("DFLT", symbols)
        self.assertNotIn("SPY", symbols)  # known ETF stripped in fallback
        self.assertTrue(any("Nasdaq universe fetch failed" in w for w in meta["warnings"]))


class TestStorageRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def test_save_and_read_back(self):
        payload = {
            "as_of": END.isoformat(),
            "universe_size": 500,
            "qualified_size": 42,
            "constituents": [{"symbol": "STRN", "rank": 1, "composite_score": 97.5, "reason": "near high"}],
            "warnings": ["test warning"],
            "summary": "Discovery test summary.",
        }
        saved = self.db.save_discovery_snapshot(payload)
        self.assertEqual(saved["as_of"], END.isoformat())
        latest = self.db.get_latest_discovery_snapshot()
        self.assertEqual(latest["universe_size"], 500)
        self.assertEqual(latest["qualified_size"], 42)
        self.assertEqual(latest["constituents"][0]["symbol"], "STRN")
        self.assertEqual(latest["warnings"], ["test warning"])
        self.assertEqual(latest["summary"], "Discovery test summary.")

    def test_upsert_one_row_per_as_of(self):
        self.db.save_discovery_snapshot({"as_of": END.isoformat(), "qualified_size": 1, "constituents": []})
        self.db.save_discovery_snapshot({"as_of": END.isoformat(), "qualified_size": 9, "constituents": []})
        history_rows = self.db.get_discovery_history(days=3650)
        self.assertEqual(len(history_rows), 1)
        self.assertEqual(history_rows[0]["qualified_size"], 9)

    def test_history_ordering(self):
        for offset, qualified in ((2, 5), (1, 6), (0, 7)):
            self.db.save_discovery_snapshot({
                "as_of": (date.today() - timedelta(days=offset)).isoformat(),
                "qualified_size": qualified,
                "constituents": [],
            })
        history_rows = self.db.get_discovery_history(days=30)
        self.assertEqual([row["qualified_size"] for row in history_rows], [5, 6, 7])
        self.assertEqual(self.db.get_latest_discovery_snapshot()["qualified_size"], 7)


class TestDigestDiscoverySection(unittest.TestCase):
    """collect_brief carries a discovery section; renderer adds lines only when data exists."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)
        from src.services.daily_digest import DailyDigestService
        self.service = DailyDigestService(state_path=Path(self.tmp.name) / "alert_state.json")

    def tearDown(self):
        _teardown_db(self.tmp)

    def test_missing_discovery_renders_nothing(self):
        brief = self.service.collect_brief()
        self.assertEqual(brief["discovery"]["status"], "missing")
        self.assertEqual(self.service._discovery_section(brief["discovery"]), [])
        self.assertNotIn("New ideas", self.service.build_digest())

    def test_completed_discovery_renders_top5(self):
        self.db.save_discovery_snapshot({
            "as_of": date.today().isoformat(),
            "universe_size": 500,
            "qualified_size": 7,
            "constituents": [
                {"symbol": f"SYM{i}", "rank": i + 1, "composite_score": 90 - i,
                 "candidate_score": 80 - i, "checklist_status": "watchlist",
                 "reason": f"reason {i}",
                 "quiet_accumulation": i == 0, "beaten_down_reversal": i == 1}
                for i in range(7)
            ],
            "summary": "test",
        })
        brief = self.service.collect_brief()
        self.assertEqual(brief["discovery"]["status"], "completed")
        self.assertEqual(brief["discovery"]["freshness"], "fresh")
        self.assertEqual(len(brief["discovery"]["top"]), 7)
        # The new watch flags ride along for the web Today card badges.
        self.assertTrue(brief["discovery"]["top"][0]["quiet_accumulation"])
        self.assertFalse(brief["discovery"]["top"][0]["beaten_down_reversal"])
        self.assertTrue(brief["discovery"]["top"][1]["beaten_down_reversal"])
        lines = self.service._discovery_section(brief["discovery"])
        rendered = "\n".join(lines)
        self.assertIn("## New ideas", rendered)
        self.assertIn("- **SYM0** 80 (watchlist) — reason 0", rendered)
        self.assertIn("SYM4", rendered)
        self.assertNotIn("SYM5", rendered)  # only top 5 rendered
        self.assertIn(rendered, self.service.build_digest())


class TestDiscoveryApi(unittest.TestCase):
    """GET/POST /api/v1/research/free-data/discovery/* via TestClient (no network)."""

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
        resp = self.client.get("/api/v1/research/free-data/discovery/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "missing")
        self.assertIn("summary", data)

    def test_latest_seeded(self):
        DatabaseManager.get_instance().save_discovery_snapshot({
            "as_of": END.isoformat(),
            "universe_size": 500,
            "qualified_size": 2,
            "constituents": [
                {"symbol": "STRN", "rank": 1, "composite_score": 95.0, "reason": "near high"},
                {"symbol": "VOLX", "rank": 2, "composite_score": 80.0, "reason": "volume"},
            ],
            "warnings": [],
            "summary": "seeded",
        })
        resp = self.client.get("/api/v1/research/free-data/discovery/latest")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["as_of"], END.isoformat())
        self.assertEqual([row["symbol"] for row in data["constituents"]], ["STRN", "VOLX"])

    def test_run_endpoint_uses_service(self):
        fake = {
            "as_of": END.isoformat(),
            "universe_size": 10,
            "qualified_size": 1,
            "constituents": [{"symbol": "STRN", "rank": 1}],
            "warnings": [],
            "summary": "fake run",
            "persisted": {"as_of": END.isoformat(), "id": 1},
        }
        with patch("src.services.discovery_service.DiscoveryService.run_daily_snapshot", return_value=fake):
            resp = self.client.post("/api/v1/research/free-data/discovery/run")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "completed")
        self.assertEqual(data["summary"], "fake run")

    def test_daily_brief_includes_discovery(self):
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("discovery", data)
        self.assertEqual(data["discovery"]["status"], "missing")
        for key in ("as_of", "age_days", "freshness"):
            self.assertIn(key, data["discovery"])


class TestHelperEdgeCases(unittest.TestCase):
    """Edge-case tests for helper functions: _percentiles, _correlation, _daily_returns_by_date."""

    def test_percentiles_single_value_no_zero_division_error(self):
        """_percentiles with exactly 1 valid value returns 1.0, no ZeroDivisionError."""
        from src.services.discovery_service import _percentiles
        rows = [{"value": 10.0}, {"value": None}, {"value": None}]
        result = _percentiles(rows, "value")
        self.assertEqual(result[0], 1.0)
        self.assertIsNone(result[1])
        self.assertIsNone(result[2])

    def test_correlation_zero_variance_series_returns_none(self):
        """_correlation with a zero-variance series returns None, no error."""
        from src.services.discovery_service import _correlation
        constant_a = {"2026-01-01": 5.0, "2026-01-02": 5.0, "2026-01-03": 5.0}
        varying_b = {"2026-01-01": 1.0, "2026-01-02": 2.0, "2026-01-03": 3.0}
        result = _correlation(constant_a, varying_b, min_overlap=3)
        self.assertIsNone(result)

    def test_correlation_insufficient_overlap_returns_none(self):
        """_correlation with fewer than min_overlap observations returns None (sector tailwind degrades gracefully)."""
        from src.services.discovery_service import _correlation
        a = {"2026-01-01": 1.0, "2026-01-02": 2.0}
        b = {"2026-01-03": 3.0, "2026-01-04": 4.0}
        result = _correlation(a, b, min_overlap=5)
        self.assertIsNone(result)

    def test_percentiles_all_tied_values_deterministic(self):
        """All-tied screen values through _percentiles yield deterministic fractional-rank output."""
        from src.services.discovery_service import _percentiles
        rows = [{"score": 10.0}, {"score": 10.0}, {"score": 10.0}]
        result = _percentiles(rows, "score")
        # All equal → positions 0, 1, 2 → fractional ranks 0/(3-1)=0, 1/2=0.5, 2/2=1.0
        self.assertEqual(result, [0.0, 0.5, 1.0])


if __name__ == "__main__":
    unittest.main()
