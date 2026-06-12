# -*- coding: utf-8 -*-
"""Tests for DailyDigestService.collect_brief() and the daily-brief endpoint."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager, SignalCandidate, SignalRun

BRIEF_SECTIONS = ("regime", "rotation", "cycle", "signals")
FRESHNESS_KEYS = ("as_of", "age_days", "freshness")


def _make_db(tmp_dir: str) -> DatabaseManager:
    """Create an isolated in-file SQLite DatabaseManager for testing."""
    db_path = os.path.join(tmp_dir, "test_daily_brief.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


def _make_service(tmp_dir: str):
    from src.services.daily_digest import DailyDigestService
    return DailyDigestService(state_path=Path(tmp_dir) / "alert_state.json")


def _seed_all(db: DatabaseManager) -> None:
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.save_market_regime_snapshot({
        "as_of": yesterday,
        "regime": "Neutral",
        "score": 60.0,
        "confidence": "medium",
        "volatility": {"vix": 18.0, "vix3m": 19.0, "term_inverted": False},
        "market_breadth": {"above_sma50_pct": 50.0},
    })
    db.save_market_regime_snapshot({
        "as_of": today,
        "regime": "Risk-on",
        "score": 70.0,
        "confidence": "high",
        "volatility": {"vix": 15.5, "vix3m": 17.0, "term_inverted": False},
        "market_breadth": {"above_sma50_pct": 62.0},
    })
    db.save_sector_rotation_snapshot({
        "as_of": today,
        "benchmark": "SPY",
        "constituents": [
            {
                "symbol": "SOXX",
                "quadrant": "leading",
                "ranks": {"1M": 1},
                "composite_rank": 1,
                "rank_change": {"1M": 2},
            },
        ],
        "leaders": {
            "1M": {
                "leaders": [{"symbol": "SOXX", "return_pct": 8.0, "rank_change": 2}],
                "laggards": [{"symbol": "GDX", "return_pct": -9.0, "rank_change": -3}],
            },
        },
    })
    db.save_macro_cycle_snapshot({
        "as_of": today,
        "phase": "mid",
        "confidence": "medium",
        "indicators": [{"id": "claims", "label": "Claims", "status": "ok", "plain": "Claims stable."}],
        "playbook": ["XLK", "SMH"],
        "crypto": {"gauge": "defensive", "plain_english": "BTC below 200DMA."},
    })
    with db.get_session() as session:
        run = SignalRun(run_type="daily", status="completed", universe="us",
                        started_at=datetime.now(), completed_at=datetime.now())
        session.add(run)
        session.flush()
        session.add(SignalCandidate(
            signal_run_id=run.id, symbol="SOXX", asset_type="stock",
            candidate_score=82.0, checklist_status="pass", entry_zone="550.00 - 579.00",
        ))
        session.add(SignalCandidate(
            signal_run_id=run.id, symbol="NODATA", asset_type="stock",
            candidate_score=None, checklist_status="reject",
        ))
        session.commit()


class TestCollectBriefEmpty(unittest.TestCase):
    """Empty database: every section reports missing but keeps the freshness contract."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        _make_db(self.tmp.name)
        self.service = _make_service(self.tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.tmp.cleanup()

    def test_sections_and_freshness_keys_present(self) -> None:
        brief = self.service.collect_brief()
        for section in BRIEF_SECTIONS:
            self.assertIn(section, brief)
            self.assertEqual(brief[section]["status"], "missing")
            for key in FRESHNESS_KEYS:
                self.assertIn(key, brief[section])
            self.assertIsNone(brief[section]["age_days"])
            self.assertEqual(brief[section]["freshness"], "unknown")

    def test_digest_renders_missing_brief(self) -> None:
        content = self.service.build_digest()
        self.assertIn("# 📊 Daily Market Snapshot", content)
        self.assertIn("No regime snapshot persisted yet", content)
        # Rotation/cycle/signals render nothing when missing.
        self.assertNotIn("## Rotation", content)
        self.assertNotIn("## Cycle", content)
        self.assertNotIn("## Top signals", content)


class TestCollectBriefSeeded(unittest.TestCase):
    """Seeded database: payload shape and renderer parity."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)
        _seed_all(self.db)
        self.service = _make_service(self.tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.tmp.cleanup()

    def test_payload_shape(self) -> None:
        brief = self.service.collect_brief()
        for section in BRIEF_SECTIONS:
            self.assertEqual(brief[section]["status"], "completed", section)
            for key in FRESHNESS_KEYS:
                self.assertIn(key, brief[section])
            self.assertEqual(brief[section]["age_days"], 0, section)
            self.assertEqual(brief[section]["freshness"], "fresh", section)

    def test_regime_payload(self) -> None:
        regime = self.service.collect_brief()["regime"]
        self.assertEqual(regime["latest"]["regime"], "Risk-on")
        self.assertEqual(regime["latest"]["vix"], 15.5)
        self.assertEqual(regime["previous"]["regime"], "Neutral")

    def test_rotation_payload_carries_constituents(self) -> None:
        rotation = self.service.collect_brief()["rotation"]
        constituent = rotation["constituents"][0]
        self.assertEqual(constituent["symbol"], "SOXX")
        self.assertEqual(constituent["quadrant"], "leading")
        self.assertEqual(constituent["rank_change"], {"1M": 2})

    def test_signals_payload(self) -> None:
        signals = self.service.collect_brief()["signals"]
        self.assertEqual([item["symbol"] for item in signals["top"]], ["SOXX"])
        self.assertEqual(signals["top"][0]["candidate_score"], 82.0)
        self.assertEqual(signals["unscored_count"], 1)

    def test_renderers_consume_brief(self) -> None:
        brief = self.service.collect_brief()
        regime_md = "\n".join(self.service._regime_section(brief["regime"]))
        self.assertIn("## Regime: Risk-on — 70.0/100 (+10.0 vs", regime_md)
        self.assertIn("- VIX 15.5 / VIX3M 17.0 (term structure normal)", regime_md)
        self.assertIn("⚠️ Regime changed from Neutral", regime_md)

        rotation_md = "\n".join(self.service._rotation_section(brief["rotation"]))
        self.assertIn("## Rotation (vs SPY", rotation_md)
        self.assertIn("**1M** hot: SOXX +8.0% ↑2 | not: GDX -9.0% ↓3", rotation_md)

        cycle_md = "\n".join(self.service._cycle_section(brief["cycle"]))
        self.assertIn("## Cycle: Mid (confidence medium)", cycle_md)
        self.assertIn("Mid-cycle playbook: XLK, SMH", cycle_md)

        signals_md = "\n".join(self.service._signals_section(brief["signals"]))
        self.assertIn("- **SOXX** 82 (pass) — entry 550.00 - 579.00", signals_md)
        self.assertIn("- _1 symbols unscored (data unavailable)_", signals_md)

        # build_digest is exactly the rendered brief sections joined together.
        digest = self.service.build_digest()
        for fragment in (regime_md, rotation_md, cycle_md, signals_md):
            self.assertIn(fragment, digest)

    def test_stale_freshness(self) -> None:
        orig_db_path = os.environ.get("DATABASE_PATH")
        stale_db_dir = tempfile.TemporaryDirectory()
        try:
            db = _make_db(stale_db_dir.name)
            db.save_market_regime_snapshot({
                "as_of": (date.today() - timedelta(days=10)).isoformat(),
                "regime": "Neutral",
                "score": 50.0,
                "confidence": "low",
            })
            regime = _make_service(stale_db_dir.name).collect_brief()["regime"]
            self.assertEqual(regime["status"], "completed")
            self.assertEqual(regime["age_days"], 10)
            self.assertEqual(regime["freshness"], "stale")
        finally:
            DatabaseManager.reset_instance()
            Config.reset_instance()
            if orig_db_path is not None:
                os.environ["DATABASE_PATH"] = orig_db_path
            else:
                os.environ.pop("DATABASE_PATH", None)
            stale_db_dir.cleanup()


class TestScorecardBriefSection(unittest.TestCase):
    """Brief 'scorecard' key: slim shape, real/sim separation, digest byte-parity."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)
        _seed_all(self.db)
        self.service = _make_service(self.tmp.name)

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        os.environ.pop("DATABASE_PATH", None)
        self.tmp.cleanup()

    def _seed_outcomes(self) -> None:
        today = date.today()
        old = (today - timedelta(days=40)).isoformat()
        latest = (today - timedelta(days=3)).isoformat()

        def row(symbol, as_of, status, *, screens, candidate_score=70.0, **extra):
            base = {
                "as_of": as_of, "symbol": symbol, "entry_close": 100.0,
                "composite_score": 92.0, "candidate_score": candidate_score,
                "screens": screens, "simulated": False, "status": status,
            }
            base.update(extra)
            return base

        self.db.upsert_discovery_flag_outcomes([
            # Flagged real rows: 2 hits, 1 flop, 1 open -> batting 2/3.
            row("AAA", old, "hit", screens=["near_52w_high"], days_to_hit=12, max_gain_pct=25.0),
            row("BBB", old, "hit", screens=["rs_top_decile"], days_to_hit=30, max_gain_pct=31.0),
            row("CCC", old, "flop", screens=["unusual_volume"], max_gain_pct=4.0),
            row("DDD", latest, "open", screens=["near_52w_high"]),
            # Baseline-only real row (no screens, no candidate_score): flop.
            row("EEE", old, "flop", screens=[], candidate_score=None),
            # Simulated row: must never leak into the real stats.
            row("SIM", old, "hit", screens=["near_52w_high"], simulated=True, days_to_hit=5),
        ])

    def test_missing_without_outcome_rows(self) -> None:
        section = self.service.collect_brief()["scorecard"]
        self.assertEqual(section["status"], "missing")
        for key in FRESHNESS_KEYS:
            self.assertIn(key, section)
        self.assertEqual(section["freshness"], "unknown")

    def test_completed_shape_and_separation(self) -> None:
        self._seed_outcomes()
        section = self.service.collect_brief()["scorecard"]
        self.assertEqual(section["status"], "completed")
        # as_of = latest REAL flag's as_of date (3 days old -> fresh).
        self.assertEqual(section["as_of"], (date.today() - timedelta(days=3)).isoformat())
        self.assertEqual(section["age_days"], 3)
        self.assertEqual(section["freshness"], "fresh")

        flagged = section["real"]["flagged"]
        self.assertEqual(flagged["flags"], 4)
        self.assertEqual(flagged["hits"], 2)
        self.assertEqual(flagged["decided"], 3)
        self.assertEqual(flagged["open"], 1)
        self.assertEqual(flagged["batting_average"], round(2 / 3, 4))
        self.assertEqual(flagged["avg_days_to_hit"], 21.0)
        baseline = section["real"]["baseline"]
        self.assertEqual(baseline["decided"], 4)
        self.assertEqual(baseline["batting_average"], 0.5)
        self.assertEqual(section["real"]["edge"], round(2 / 3 - 0.5, 4))
        # Simulated block present, separate, and not merged into real.
        self.assertEqual(section["simulated"]["flagged"]["hits"], 1)
        self.assertEqual(section["simulated"]["flagged"]["batting_average"], 1.0)
        # Slim payload: breakdowns stay on the /scorecard endpoint only.
        self.assertNotIn("by_screen", section["real"])

    def test_digest_byte_parity_and_no_scorecard_text(self) -> None:
        before = self.service.build_digest()
        self._seed_outcomes()
        after = self.service.build_digest()
        self.assertEqual(before, after)
        self.assertNotIn("Scorecard", after)
        self.assertNotIn("scorecard", after)
        self.assertNotIn("batting", after.lower())


class TestDailyBriefApi(unittest.TestCase):
    """Integration test hitting GET /api/v1/research/free-data/daily-brief via TestClient."""

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
        self.tmp.cleanup()

    def test_daily_brief_empty_db(self) -> None:
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        for section in BRIEF_SECTIONS:
            self.assertIn(section, data)
            self.assertEqual(data[section]["status"], "missing")
            for key in FRESHNESS_KEYS:
                self.assertIn(key, data[section])

    def test_daily_brief_seeded(self) -> None:
        _seed_all(DatabaseManager.get_instance())
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["regime"]["latest"]["regime"], "Risk-on")
        self.assertEqual(data["rotation"]["constituents"][0]["quadrant"], "leading")
        self.assertEqual(data["cycle"]["phase"], "mid")
        self.assertEqual(data["signals"]["top"][0]["symbol"], "SOXX")
        for section in BRIEF_SECTIONS:
            self.assertEqual(data[section]["freshness"], "fresh")


if __name__ == "__main__":
    unittest.main()
