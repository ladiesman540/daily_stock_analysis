# -*- coding: utf-8 -*-
"""Daily market-impact scoring for persisted news headlines.

The analysis pipeline persists ``NewsIntel`` rows; this service scores today's
unscored headlines with ONE batched LLM call per day (budget enforced naturally:
only unscored rows are selected, and the call is skipped when none qualify, so
re-running the same day never re-scores already-scored rows).

Fail-open by design: any failure (LLM error, malformed JSON, DB error) leaves
rows unscored and never raises — the snapshot pipeline must not be blocked.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.storage import DatabaseManager, NewsIntel, persist_llm_usage

logger = logging.getLogger(__name__)

# Cap on headlines per daily batch (dedupe by title first).
MAX_HEADLINES = 40

IMPACT_LABELS = ("market_moving", "sector", "stock_specific", "noise")

_SYSTEM_PROMPT = """\
You are a market news analyst. Classify the market impact of each headline below.

For each headline return:
- score: integer 0-100 (how much this news can move prices today; 80+ only for \
macro/index-level movers such as Fed decisions, CPI surprises, major geopolitical shocks)
- label: one of "market_moving" (moves the whole market), "sector" (moves an \
industry/sector), "stock_specific" (moves one stock), "noise" (recycled commentary, \
listicles, no tradeable information)
- reason: one short sentence (max 20 words)

Return ONLY a JSON object, no prose:
{"items": [{"id": <headline id>, "score": <0-100>, "label": "<label>", "reason": "<why>"}]}
"""


class NewsImpactService:
    """Score today's unscored ``NewsIntel`` headlines in one batched LLM call."""

    def __init__(self, *, db: Optional[DatabaseManager] = None, llm_adapter: Any = None):
        self.db = db or DatabaseManager.get_instance()
        self._llm_adapter = llm_adapter

    # ------------------------------------------------------------------ public

    def run_daily_scoring(self) -> Dict[str, Any]:
        """Select today's unscored headlines, score them, persist. Never raises."""
        try:
            candidates = self._select_unscored_today()
        except Exception as exc:
            logger.warning("News impact: candidate selection failed (fail-open): %s", exc)
            return {"status": "failed", "candidates": 0, "scored": 0, "error": str(exc)}

        if not candidates:
            return {"status": "skipped", "candidates": 0, "scored": 0,
                    "reason": "no unscored headlines today"}

        try:
            items = self._score_with_llm(candidates)
        except Exception as exc:
            logger.warning("News impact: LLM scoring failed (fail-open, rows left unscored): %s", exc)
            return {"status": "failed", "candidates": len(candidates), "scored": 0, "error": str(exc)}

        if items is None:
            return {"status": "failed", "candidates": len(candidates), "scored": 0,
                    "error": "LLM scoring failed; rows left unscored"}

        try:
            scored = self._persist_scores(items)
        except Exception as exc:
            logger.warning("News impact: persisting scores failed (fail-open): %s", exc)
            return {"status": "failed", "candidates": len(candidates), "scored": 0, "error": str(exc)}

        return {"status": "completed", "candidates": len(candidates), "scored": scored}

    # ------------------------------------------------------------------ selection

    def _select_unscored_today(self) -> List[Dict[str, Any]]:
        """Today's unscored headlines, deduped by title, capped at MAX_HEADLINES."""
        start = datetime.now() - timedelta(hours=26)
        with self.db.get_session() as session:
            rows = session.execute(
                select(NewsIntel)
                .where(
                    NewsIntel.fetched_at >= start,
                    NewsIntel.impact_scored_at.is_(None),
                )
                .order_by(desc(NewsIntel.id))
            ).scalars().all()
            candidates: List[Dict[str, Any]] = []
            seen_titles: set = set()
            for row in rows:
                title_key = (row.title or "").strip().lower()
                if not title_key or title_key in seen_titles:
                    continue
                seen_titles.add(title_key)
                candidates.append({
                    "id": row.id,
                    "title": (row.title or "").strip(),
                    "source": (row.source or "").strip(),
                    "code": (row.code or "").strip(),
                })
                if len(candidates) >= MAX_HEADLINES:
                    break
        return candidates

    # ------------------------------------------------------------------ scoring

    def _adapter(self) -> Any:
        if self._llm_adapter is None:
            from src.agent.llm_adapter import LLMToolAdapter

            self._llm_adapter = LLMToolAdapter()
        return self._llm_adapter

    def _score_with_llm(self, candidates: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
        """One batched call for all candidates. Returns validated items, or None on failure."""
        lines = [
            f"- id={item['id']} | symbol={item['code'] or 'n/a'} | {item['title']}"
            + (f" ({item['source']})" if item["source"] else "")
            for item in candidates
        ]
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"Today is {date.today().isoformat()}. Headlines:\n" + "\n".join(lines)},
        ]
        response = self._adapter().call_text(
            messages,
            temperature=0.2,
            max_tokens=4000,
            timeout=180,
            purpose="data",
        )
        if response.provider == "error":
            logger.warning("News impact: LLM call failed: %s", response.content)
            return None
        persist_llm_usage(response.usage or {}, response.model or "unknown", call_type="news_impact")

        from src.agent.runner import try_parse_json

        parsed = try_parse_json(response.content or "")
        raw_items = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(raw_items, list):
            logger.warning("News impact: could not parse LLM JSON (rows left unscored)")
            return None
        return self._validate_items(raw_items, {item["id"] for item in candidates})

    @staticmethod
    def _validate_items(raw_items: List[Any], candidate_ids: set) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            try:
                row_id = int(raw.get("id"))
                score = max(0, min(100, int(raw.get("score"))))
            except (TypeError, ValueError):
                continue
            label = str(raw.get("label") or "").strip()
            if row_id not in candidate_ids or label not in IMPACT_LABELS:
                continue
            items.append({
                "id": row_id,
                "score": score,
                "label": label,
                "reason": str(raw.get("reason") or "").strip()[:255],
            })
        return items

    # ------------------------------------------------------------------ persistence

    def _persist_scores(self, items: List[Dict[str, Any]]) -> int:
        now = datetime.now()

        def _write(session):
            scored = 0
            for item in items:
                row = session.get(NewsIntel, item["id"])
                if row is None or row.impact_scored_at is not None:
                    continue
                row.impact_score = item["score"]
                row.impact_label = item["label"]
                row.impact_reason = item["reason"]
                row.impact_scored_at = now
                scored += 1
            return scored

        return self.db._run_write_transaction("persist_news_scores", _write)
