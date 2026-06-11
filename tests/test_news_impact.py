# -*- coding: utf-8 -*-
"""Tests for news market-impact scoring (Phase 5).

Covers: the idempotent SQLite column-ensure helper, candidate selection
(dedupe / cap / unscored-today-only), the single batched LLM call (stubbed at
the service boundary — never a real call), fail-open on malformed LLM JSON,
digest collection/rendering, and the daily-brief payload carrying headlines.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# src.agent.runner (JSON parsing helper) imports litellm transitively; stub it
# when the test environment does not have it installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from unittest.mock import MagicMock
    sys.modules["litellm"] = MagicMock()

from src.config import Config
from src.storage import DatabaseManager, NewsIntel

IMPACT_COLUMNS = ("impact_score", "impact_label", "impact_reason", "impact_scored_at")


def _make_db(tmp_dir: str) -> DatabaseManager:
    db_path = os.path.join(tmp_dir, "test_news_impact.db")
    os.environ["DATABASE_PATH"] = db_path
    Config.reset_instance()
    DatabaseManager.reset_instance()
    return DatabaseManager.get_instance()


def _teardown_db(tmp) -> None:
    DatabaseManager.reset_instance()
    Config.reset_instance()
    os.environ.pop("DATABASE_PATH", None)
    tmp.cleanup()


def seed_headline(
    db: DatabaseManager,
    *,
    title: str,
    url: str,
    code: str = "NVDA",
    source: str = "Reuters",
    fetched_at: Optional[datetime] = None,
    impact_score: Optional[int] = None,
    impact_label: Optional[str] = None,
) -> int:
    with db.get_session() as session:
        row = NewsIntel(
            code=code,
            title=title,
            url=url,
            source=source,
            fetched_at=fetched_at or datetime.now(),
            impact_score=impact_score,
            impact_label=impact_label,
            impact_reason="seeded" if impact_score is not None else None,
            impact_scored_at=datetime.now() if impact_score is not None else None,
        )
        session.add(row)
        session.commit()
        return row.id


class FakeLLMAdapter:
    """Service-boundary stub for LLMToolAdapter — records calls, never hits a network."""

    def __init__(self, content: str = "", provider: str = "test"):
        self.content = content
        self.provider = provider
        self.calls: List[Dict[str, Any]] = []

    def call_text(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return SimpleNamespace(
            content=self.content,
            provider=self.provider,
            model="test/model",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )


def _items_json(items: List[Dict[str, Any]]) -> str:
    return json.dumps({"items": items})


class TestEnsureSqliteColumns(unittest.TestCase):
    """The column-ensure helper ALTERs legacy tables and is idempotent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        _teardown_db(self.tmp)

    def _columns(self, db_path: str, table: str) -> set:
        with sqlite3.connect(db_path) as conn:
            return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}

    def test_adds_missing_columns_to_legacy_table(self):
        db_path = os.path.join(self.tmp.name, "test_news_impact.db")
        # Simulate a pre-Phase-5 install: news_intel exists without impact columns.
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE news_intel ("
                "id INTEGER PRIMARY KEY, code VARCHAR(10) NOT NULL, "
                "title VARCHAR(300) NOT NULL, url VARCHAR(1000) NOT NULL)"
            )
        self.assertFalse(set(IMPACT_COLUMNS) & self._columns(db_path, "news_intel"))

        _make_db(self.tmp.name)
        columns = self._columns(db_path, "news_intel")
        for column in IMPACT_COLUMNS:
            self.assertIn(column, columns)

    def test_idempotent_on_second_init(self):
        db_path = os.path.join(self.tmp.name, "test_news_impact.db")
        _make_db(self.tmp.name)
        # Re-init against the same file: must not raise, columns still present.
        _make_db(self.tmp.name)
        columns = self._columns(db_path, "news_intel")
        for column in IMPACT_COLUMNS:
            self.assertIn(column, columns)


class TestSelection(unittest.TestCase):
    """Unscored-today selection: dedupe by title, cap at 40, scored/old rows excluded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def _service(self, adapter=None):
        from src.services.news_impact_service import NewsImpactService
        return NewsImpactService(db=self.db, llm_adapter=adapter or FakeLLMAdapter())

    def test_dedupes_titles_and_excludes_scored_and_old(self):
        seed_headline(self.db, title="Fed cuts rates", url="https://a/1")
        seed_headline(self.db, title="  fed cuts RATES  ", url="https://a/2")  # dupe title
        seed_headline(self.db, title="Already scored", url="https://a/3",
                      impact_score=50, impact_label="sector")
        seed_headline(self.db, title="Yesterday news", url="https://a/4",
                      fetched_at=datetime.now() - timedelta(days=2))
        candidates = self._service()._select_unscored_today()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["title"].lower(), "fed cuts rates")

    def test_caps_at_40_headlines(self):
        for i in range(45):
            seed_headline(self.db, title=f"Unique headline {i}", url=f"https://a/{i}")
        candidates = self._service()._select_unscored_today()
        self.assertEqual(len(candidates), 40)


class TestScoring(unittest.TestCase):
    """One batched call with all candidates; no call when nothing unscored; fail-open."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def _service(self, adapter):
        from src.services.news_impact_service import NewsImpactService
        return NewsImpactService(db=self.db, llm_adapter=adapter)

    def _row(self, row_id: int) -> NewsIntel:
        with self.db.get_session() as session:
            row = session.get(NewsIntel, row_id)
            session.expunge(row)
            return row

    def test_single_batched_call_scores_all_candidates(self):
        ids = [
            seed_headline(self.db, title="Fed surprise cut", url="https://a/1", code="SPY"),
            seed_headline(self.db, title="Chip subsidy expanded", url="https://a/2", code="NVDA"),
            seed_headline(self.db, title="CEO sells shares", url="https://a/3", code="TSLA"),
        ]
        adapter = FakeLLMAdapter(content=_items_json([
            {"id": ids[0], "score": 90, "label": "market_moving", "reason": "Macro shock"},
            {"id": ids[1], "score": 60, "label": "sector", "reason": "Semis tailwind"},
            {"id": ids[2], "score": 30, "label": "stock_specific", "reason": "Single name"},
        ]))
        result = self._service(adapter).run_daily_scoring()

        self.assertEqual(len(adapter.calls), 1)
        user_prompt = adapter.calls[0]["messages"][1]["content"]
        for row_id in ids:
            self.assertIn(f"id={row_id}", user_prompt)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["candidates"], 3)
        self.assertEqual(result["scored"], 3)
        row = self._row(ids[0])
        self.assertEqual(row.impact_score, 90)
        self.assertEqual(row.impact_label, "market_moving")
        self.assertEqual(row.impact_reason, "Macro shock")
        self.assertIsNotNone(row.impact_scored_at)

    def test_no_call_when_nothing_unscored(self):
        seed_headline(self.db, title="Already scored", url="https://a/1",
                      impact_score=70, impact_label="sector")
        adapter = FakeLLMAdapter()
        result = self._service(adapter).run_daily_scoring()
        self.assertEqual(len(adapter.calls), 0)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["scored"], 0)

    def test_rerun_same_day_does_not_rescore(self):
        row_id = seed_headline(self.db, title="Fed surprise cut", url="https://a/1")
        first = FakeLLMAdapter(content=_items_json(
            [{"id": row_id, "score": 90, "label": "market_moving", "reason": "Macro"}]
        ))
        self._service(first).run_daily_scoring()
        second = FakeLLMAdapter()
        result = self._service(second).run_daily_scoring()
        self.assertEqual(len(second.calls), 0)
        self.assertEqual(result["status"], "skipped")

    def test_malformed_llm_json_fails_open(self):
        row_id = seed_headline(self.db, title="Fed surprise cut", url="https://a/1")
        adapter = FakeLLMAdapter(content="Sorry, here are my thoughts in prose...")
        result = self._service(adapter).run_daily_scoring()
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["scored"], 0)
        row = self._row(row_id)
        self.assertIsNone(row.impact_score)
        self.assertIsNone(row.impact_scored_at)

    def test_llm_error_response_fails_open(self):
        row_id = seed_headline(self.db, title="Fed surprise cut", url="https://a/1")
        adapter = FakeLLMAdapter(content="All LLM models failed", provider="error")
        result = self._service(adapter).run_daily_scoring()
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(self._row(row_id).impact_score)

    def test_invalid_items_are_skipped(self):
        row_id = seed_headline(self.db, title="Fed surprise cut", url="https://a/1")
        adapter = FakeLLMAdapter(content=_items_json([
            {"id": 999999, "score": 90, "label": "market_moving", "reason": "Unknown id"},
            {"id": row_id, "score": 90, "label": "mega_bullish", "reason": "Bad label"},
        ]))
        result = self._service(adapter).run_daily_scoring()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["scored"], 0)
        self.assertIsNone(self._row(row_id).impact_score)


class TestDigestHeadlines(unittest.TestCase):
    """Digest collect/render: top 3 by score, correct emoji, noise excluded, empty -> nothing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _make_db(self.tmp.name)

    def tearDown(self):
        _teardown_db(self.tmp)

    def _service(self):
        from src.services.daily_digest import DailyDigestService
        return DailyDigestService(state_path=Path(self.tmp.name) / "alert_state.json")

    def test_top3_by_score_with_emoji_and_noise_excluded(self):
        seed_headline(self.db, title="Fed shock", url="https://a/1",
                      impact_score=92, impact_label="market_moving")
        seed_headline(self.db, title="Semis rally", url="https://a/2", source="Bloomberg",
                      impact_score=65, impact_label="sector")
        seed_headline(self.db, title="CEO interview", url="https://a/3",
                      impact_score=40, impact_label="stock_specific")
        seed_headline(self.db, title="Top 10 stocks to watch", url="https://a/4",
                      impact_score=80, impact_label="noise")
        seed_headline(self.db, title="Minor filing", url="https://a/5",
                      impact_score=10, impact_label="stock_specific")

        service = self._service()
        payload = service.collect_brief()["headlines"]
        self.assertEqual(payload["status"], "completed")
        titles = [item["title"] for item in payload["top"]]
        self.assertEqual(titles, ["Fed shock", "Semis rally", "CEO interview"])

        lines = service._headlines_section(payload)
        rendered = "\n".join(lines)
        self.assertIn("## Headlines", rendered)
        self.assertIn("- 🔴 [92] Fed shock", rendered)
        self.assertIn("- 🟠 [65] Semis rally — Bloomberg", rendered)
        self.assertIn("- ⚪ [40] CEO interview", rendered)
        self.assertNotIn("Top 10 stocks to watch", rendered)
        self.assertNotIn("Minor filing", rendered)

    def test_renders_nothing_when_no_scored_headlines(self):
        # Unscored rows exist but nothing scored -> section is absent entirely.
        seed_headline(self.db, title="Unscored headline", url="https://a/1")
        service = self._service()
        payload = service.collect_brief()["headlines"]
        self.assertEqual(payload["status"], "missing")
        self.assertEqual(service._headlines_section(payload), [])
        self.assertNotIn("## Headlines", service.build_digest())


class TestDailyBriefApiHeadlines(unittest.TestCase):
    """GET /api/v1/research/free-data/daily-brief carries the headlines key."""

    def setUp(self):
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

    def test_daily_brief_includes_headlines_missing_when_unscored(self):
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("headlines", data)
        self.assertEqual(data["headlines"]["status"], "missing")
        for key in ("as_of", "age_days", "freshness"):
            self.assertIn(key, data["headlines"])

    def test_daily_brief_includes_scored_headlines(self):
        seed_headline(DatabaseManager.get_instance(), title="Fed shock", url="https://a/1",
                      impact_score=92, impact_label="market_moving")
        resp = self.client.get("/api/v1/research/free-data/daily-brief")
        self.assertEqual(resp.status_code, 200)
        headlines = resp.json()["headlines"]
        self.assertEqual(headlines["status"], "completed")
        self.assertEqual(headlines["top"][0]["title"], "Fed shock")
        self.assertEqual(headlines["top"][0]["impact_label"], "market_moving")
        self.assertEqual(headlines["freshness"], "fresh")


if __name__ == "__main__":
    unittest.main()
