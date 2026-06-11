# -*- coding: utf-8 -*-
"""Research and weekly signal services."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from sqlalchemy import and_, desc, select

from src.services.research_extraction import AssetMentionDraft, extract_asset_mentions
from src.services.research_market_data import (
    DEFAULT_CRYPTO_SYMBOLS,
    DEFAULT_US_UNIVERSE,
    ResearchMarketDataService,
)
from src.services.research_scoring import CandidateScore, GrindingLeaderScorer
from src.storage import (
    DatabaseManager,
    ExtractedAssetMention,
    ResearchItem,
    ResearchSource,
    RotationMemo,
    SignalCandidate,
    SignalRun,
    SourceCallScore,
    XOAuthAccount,
)

logger = logging.getLogger(__name__)

_PENDING_OAUTH: Dict[str, Dict[str, Any]] = {}


class XBookmarkClient:
    """Small X API v2 client for OAuth and bookmark sync."""

    auth_url = "https://twitter.com/i/oauth2/authorize"
    token_url = "https://api.x.com/2/oauth2/token"
    me_url = "https://api.x.com/2/users/me"

    def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
        client_id: str,
        client_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
        auth = (client_id, client_secret) if client_secret else None
        response = requests.post(self.token_url, data=payload, auth=auth, timeout=15)
        response.raise_for_status()
        return response.json()

    def refresh_token(
        self,
        *,
        refresh_token: str,
        client_id: str,
        client_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        }
        auth = (client_id, client_secret) if client_secret else None
        response = requests.post(self.token_url, data=payload, auth=auth, timeout=15)
        response.raise_for_status()
        return response.json()

    def get_me(self, access_token: str) -> Dict[str, Any]:
        response = requests.get(
            self.me_url,
            headers={"Authorization": f"Bearer {access_token}"},
            params={"user.fields": "id,username,name"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("data", {})

    def get_bookmarks_page(
        self,
        *,
        user_id: str,
        access_token: str,
        pagination_token: Optional[str] = None,
        max_results: int = 100,
    ) -> Dict[str, Any]:
        params = {
            "max_results": max(10, min(100, int(max_results))),
            "tweet.fields": "created_at,author_id,entities,public_metrics,lang,conversation_id",
            "user.fields": "id,username,name,verified,public_metrics,profile_image_url",
            "media.fields": "media_key,type,url,preview_image_url,width,height,alt_text",
            "expansions": "author_id,attachments.media_keys,referenced_tweets.id,referenced_tweets.id.author_id",
        }
        if pagination_token:
            params["pagination_token"] = pagination_token
        response = requests.get(
            f"https://api.x.com/2/users/{user_id}/bookmarks",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()


class ResearchService:
    """Application service backing `/api/v1/research`."""

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        market_data: Optional[ResearchMarketDataService] = None,
        x_client: Optional[XBookmarkClient] = None,
        scorer: Optional[GrindingLeaderScorer] = None,
    ):
        self.db = db_manager or DatabaseManager.get_instance()
        self.market_data = market_data or ResearchMarketDataService()
        self.x_client = x_client or XBookmarkClient()
        self.scorer = scorer or GrindingLeaderScorer()

    # ------------------------------------------------------------------
    # X OAuth and bookmark sync
    # ------------------------------------------------------------------

    def x_status(self) -> Dict[str, Any]:
        client_id = _x_client_id()
        redirect_uri = _x_redirect_uri()
        with self.db.get_session() as session:
            account_count = session.execute(select(XOAuthAccount)).scalars().all()
            active = [row for row in account_count if row.is_active]
            last_sync = max((row.last_sync_at for row in active if row.last_sync_at), default=None)
            return {
                "configured": bool(client_id and redirect_uri),
                "connected": bool(active),
                "account_count": len(active),
                "last_sync_at": _iso(last_sync),
                "accounts": [
                    {
                        "id": row.id,
                        "provider_user_id": row.provider_user_id,
                        "username": row.username,
                        "display_name": row.display_name,
                        "expires_at": _iso(row.expires_at),
                        "last_sync_at": _iso(row.last_sync_at),
                    }
                    for row in active
                ],
            }

    def start_x_oauth(self) -> Dict[str, Any]:
        client_id = _x_client_id()
        redirect_uri = _x_redirect_uri()
        if not client_id or not redirect_uri:
            return {
                "configured": False,
                "auth_url": "",
                "message": "Set X_CLIENT_ID and X_REDIRECT_URI to enable OAuth.",
            }

        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = _pkce_challenge(verifier)
        _PENDING_OAUTH[state] = {
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "created_at": datetime.now(),
        }
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "tweet.read users.read bookmark.read offline.access",
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return {
            "configured": True,
            "state": state,
            "auth_url": f"{self.x_client.auth_url}?{urlencode(params)}",
        }

    def complete_x_oauth(self, *, code: str, state: str) -> Dict[str, Any]:
        pending = _PENDING_OAUTH.pop(state, None)
        if not pending:
            return {"success": False, "error": "invalid_state", "message": "OAuth state is missing or expired."}
        if pending["created_at"] < datetime.now() - timedelta(minutes=15):
            return {"success": False, "error": "expired_state", "message": "OAuth state expired."}

        client_id = _x_client_id()
        if not client_id:
            return {"success": False, "error": "missing_client", "message": "X_CLIENT_ID is not configured."}

        token_payload = self.x_client.exchange_code(
            code=code,
            code_verifier=pending["code_verifier"],
            redirect_uri=pending["redirect_uri"],
            client_id=client_id,
            client_secret=_x_client_secret(),
        )
        access_token = token_payload.get("access_token") or ""
        if not access_token:
            return {"success": False, "error": "missing_access_token", "message": "X did not return an access token."}
        me = self.x_client.get_me(access_token)
        provider_user_id = str(me.get("id") or "")
        if not provider_user_id:
            return {"success": False, "error": "missing_user", "message": "X did not return user identity."}

        expires_at = None
        if token_payload.get("expires_in"):
            expires_at = datetime.now() + timedelta(seconds=int(token_payload["expires_in"]))

        with self.db.get_session() as session:
            row = session.execute(
                select(XOAuthAccount).where(XOAuthAccount.provider_user_id == provider_user_id)
            ).scalar_one_or_none()
            if row is None:
                row = XOAuthAccount(provider_user_id=provider_user_id, access_token=access_token)
                session.add(row)
            row.username = me.get("username")
            row.display_name = me.get("name")
            row.access_token = access_token
            row.refresh_token = token_payload.get("refresh_token") or row.refresh_token
            row.token_type = token_payload.get("token_type")
            row.scope = token_payload.get("scope")
            row.expires_at = expires_at
            row.is_active = True
            row.updated_at = datetime.now()
            session.commit()
            return {"success": True, "account": _x_account_dict(row)}

    def sync_x_bookmarks(self, *, account_id: Optional[int] = None, max_pages: int = 3) -> Dict[str, Any]:
        with self.db.get_session() as session:
            query = select(XOAuthAccount).where(XOAuthAccount.is_active.is_(True))
            if account_id is not None:
                query = query.where(XOAuthAccount.id == account_id)
            account = session.execute(query.order_by(desc(XOAuthAccount.updated_at))).scalar_one_or_none()
            if account is None:
                return {"success": False, "status": "not_connected", "imported": 0, "mentions": 0}
            account_data = _x_account_dict(account)

        access_token = self._valid_x_access_token(account_data)
        imported = 0
        mentions = 0
        next_token = None
        pages = 0
        while pages < max(1, min(int(max_pages), 10)):
            pages += 1
            try:
                payload = self.x_client.get_bookmarks_page(
                    user_id=account_data["provider_user_id"],
                    access_token=access_token,
                    pagination_token=next_token,
                )
            except requests.HTTPError as exc:
                status_code = getattr(exc.response, "status_code", None)
                if status_code == 429:
                    return {
                        "success": False,
                        "status": "rate_limited",
                        "imported": imported,
                        "mentions": mentions,
                        "pages": pages - 1,
                        "next_token": next_token,
                    }
                raise
            page_imported, page_mentions = self._store_x_bookmark_payload(payload)
            imported += page_imported
            mentions += page_mentions
            next_token = (payload.get("meta") or {}).get("next_token")
            if not next_token:
                break

        with self.db.get_session() as session:
            account = session.get(XOAuthAccount, account_data["id"])
            if account:
                account.last_sync_at = datetime.now()
                account.updated_at = datetime.now()
                session.commit()

        return {
            "success": True,
            "status": "synced",
            "imported": imported,
            "mentions": mentions,
            "pages": pages,
            "next_token": next_token,
        }

    # ------------------------------------------------------------------
    # Ideas
    # ------------------------------------------------------------------

    def create_idea(self, *, content: str, title: Optional[str] = None, source_type: str = "manual") -> Dict[str, Any]:
        content = (content or "").strip()
        if not content:
            raise ValueError("content is required")
        external_id = f"manual:{hashlib.sha256(content.encode('utf-8')).hexdigest()[:24]}"
        source = self._upsert_source(
            platform="manual",
            external_id="manual",
            username="manual",
            display_name="Manual ideas",
        )
        with self.db.get_session() as session:
            item = session.execute(
                select(ResearchItem).where(
                    and_(ResearchItem.source_type == source_type, ResearchItem.external_id == external_id)
                )
            ).scalar_one_or_none()
            if item is None:
                item = ResearchItem(
                    source_id=source["id"],
                    source_type=source_type,
                    external_id=external_id,
                    title=title or "Manual idea",
                    content=content,
                    published_at=datetime.now(),
                    bookmarked_at=datetime.now(),
                )
                session.add(item)
                session.flush()
            else:
                item.content = content
                item.title = title or item.title
                item.updated_at = datetime.now()
            item_dict = _research_item_dict(item, mentions=[])
            session.commit()

        extracted = extract_asset_mentions(content)
        self._store_mentions(item_dict["id"], source["id"], extracted)
        return self.get_idea(item_dict["id"]) or item_dict

    def get_idea(self, item_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            item = session.get(ResearchItem, item_id)
            if item is None:
                return None
            mentions = session.execute(
                select(ExtractedAssetMention).where(ExtractedAssetMention.research_item_id == item.id)
            ).scalars().all()
            return _research_item_dict(item, mentions=[_mention_dict(row) for row in mentions])

    def list_ideas(
        self,
        *,
        limit: int = 50,
        asset_type: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            query = select(ResearchItem).order_by(desc(ResearchItem.created_at)).limit(max(1, min(int(limit), 200)))
            items = list(session.execute(query).scalars().all())
            if not items:
                return []
            item_ids = [item.id for item in items]
            mention_query = select(ExtractedAssetMention).where(ExtractedAssetMention.research_item_id.in_(item_ids))
            if asset_type:
                mention_query = mention_query.where(ExtractedAssetMention.asset_type == asset_type)
            if symbol:
                mention_query = mention_query.where(ExtractedAssetMention.asset_symbol == symbol.upper())
            mentions = list(session.execute(mention_query).scalars().all())
            mention_map: Dict[int, List[Dict[str, Any]]] = {}
            for mention in mentions:
                mention_map.setdefault(int(mention.research_item_id or 0), []).append(_mention_dict(mention))
            if asset_type or symbol:
                items = [item for item in items if mention_map.get(item.id)]
            return [_research_item_dict(item, mentions=mention_map.get(item.id, [])) for item in items]

    # ------------------------------------------------------------------
    # Signal runs and rotation memo
    # ------------------------------------------------------------------

    def run_signal_scan(
        self,
        *,
        symbols: Optional[List[str]] = None,
        crypto_symbols: Optional[List[str]] = None,
        include_us: bool = True,
        include_crypto: bool = True,
        run_type: str = "weekly",
    ) -> Dict[str, Any]:
        parameters = {
            "symbols": symbols,
            "crypto_symbols": crypto_symbols,
            "include_us": include_us,
            "include_crypto": include_crypto,
            "run_type": run_type,
        }
        with self.db.get_session() as session:
            run = SignalRun(
                run_type=run_type,
                status="running",
                universe="us_crypto",
                started_at=datetime.now(),
                parameters=json.dumps(parameters, ensure_ascii=False),
            )
            session.add(run)
            session.commit()
            run_id = int(run.id)

        diagnostics: List[str] = []
        scored: List[CandidateScore] = []
        benchmark_perf_3m = self._benchmark_perf_3m()
        if include_us:
            default_us = self.market_data.get_default_us_universe() if symbols is None else DEFAULT_US_UNIVERSE
            for symbol in _normalize_symbols(symbols or default_us):
                payload = self.market_data.get_us_equity_history(symbol)
                if payload.get("error"):
                    diagnostics.append(f"{symbol}: {payload['error']}")
                self._evaluate_source_calls(payload, asset_type="stock")
                scored.append(
                    self._score_payload(payload, asset_type="stock", benchmark_perf_3m=benchmark_perf_3m)
                )

        if include_crypto:
            default_crypto = (
                self.market_data.get_default_crypto_universe()
                if crypto_symbols is None
                else DEFAULT_CRYPTO_SYMBOLS
            )
            crypto_list = _normalize_symbols(crypto_symbols or default_crypto)
            for symbol in crypto_list:
                payload = self.market_data.get_crypto_history(symbol)
                if payload.get("error"):
                    diagnostics.append(f"{symbol}: {payload['error']}")
                self._evaluate_source_calls(payload, asset_type="crypto")
                scored.append(
                    self._score_payload(payload, asset_type="crypto", benchmark_perf_3m=benchmark_perf_3m)
                )

        unscored = [item for item in scored if item.candidate_score is None]
        if unscored:
            diagnostics.append(
                f"{len(unscored)} symbols unscored (insufficient data): "
                + ", ".join(item.symbol for item in unscored[:20])
            )
        # Unscorable rows (None) sort to the bottom instead of masquerading as zeros.
        scored.sort(
            key=lambda item: (item.candidate_score is not None, item.candidate_score or 0.0),
            reverse=True,
        )
        with self.db.get_session() as session:
            for candidate in scored:
                session.add(_candidate_row(run_id, candidate))
            run = session.get(SignalRun, run_id)
            if run:
                run.status = "completed"
                run.completed_at = datetime.now()
                run.diagnostics = json.dumps(diagnostics, ensure_ascii=False)
            session.commit()

        memo = self._create_rotation_memo(run_id, scored, diagnostics)
        return self.get_signal_run(run_id) | {"rotation_memo": memo}

    def get_signal_run(self, run_id: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            run = session.get(SignalRun, run_id)
            if run is None:
                raise ValueError(f"signal run not found: {run_id}")
            candidates = session.execute(
                select(SignalCandidate)
                .where(SignalCandidate.signal_run_id == run_id)
                .order_by(desc(SignalCandidate.candidate_score))
            ).scalars().all()
            return {
                "id": run.id,
                "run_type": run.run_type,
                "status": run.status,
                "universe": run.universe,
                "started_at": _iso(run.started_at),
                "completed_at": _iso(run.completed_at),
                "parameters": _json_loads(run.parameters, {}),
                "diagnostics": _json_loads(run.diagnostics, []),
                "candidates": [_signal_candidate_dict(row) for row in candidates],
            }

    def latest_rotation_memo(self) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            memo = session.execute(
                select(RotationMemo).order_by(desc(RotationMemo.generated_at)).limit(1)
            ).scalar_one_or_none()
            if memo is None:
                return None
            return _rotation_memo_dict(memo)

    def _benchmark_perf_3m(self) -> Optional[float]:
        """3-month SPY return used as the relative-strength baseline (one fetch per scan)."""
        try:
            payload = self.market_data.get_us_equity_history("SPY")
            closes = [
                float(bar["close"])
                for bar in payload.get("bars") or []
                if bar.get("close") is not None
            ]
            if len(closes) <= 63:
                return None
            base = closes[-64]
            return (closes[-1] - base) / base * 100 if base else None
        except Exception:
            return None

    def _score_payload(
        self,
        payload: Dict[str, Any],
        *,
        asset_type: str,
        benchmark_perf_3m: Optional[float] = None,
    ) -> CandidateScore:
        symbol = str(payload.get("symbol") or "").upper()
        evidence = self._recent_source_evidence(symbol)
        score = self.scorer.score(
            symbol=symbol,
            asset_type=asset_type,
            name=str(payload.get("name") or symbol),
            bars=payload.get("bars") or [],
            market_cap=payload.get("market_cap"),
            source_evidence=evidence,
            benchmark_perf_3m=benchmark_perf_3m,
        )
        metrics = dict(score.metrics)
        metrics["data_source"] = payload.get("source") or "unknown"
        metrics["data_quality"] = payload.get("data_quality") or {
            "source": payload.get("source") or "unknown",
            "tier": "unknown",
            "warnings": payload.get("data_warnings") or [],
        }
        metrics["data_warnings"] = payload.get("data_warnings") or []
        return replace(score, metrics=metrics)

    def _create_rotation_memo(
        self,
        run_id: int,
        candidates: List[CandidateScore],
        diagnostics: List[str],
    ) -> Dict[str, Any]:
        ranked = [candidate for candidate in candidates if candidate.candidate_score is not None]
        unscored_count = len(candidates) - len(ranked)
        top = ranked[:8]
        passes = [candidate for candidate in ranked if candidate.checklist_status == "pass"]
        watchlist = [candidate for candidate in ranked if candidate.checklist_status == "watchlist"]
        theme_counts: Dict[str, int] = {}
        for candidate in candidates:
            for evidence in candidate.source_evidence:
                for tag in evidence.get("catalyst_tags", []) or []:
                    theme_counts[tag] = theme_counts.get(tag, 0) + 1
        themes = [
            {"theme": tag, "mentions": count}
            for tag, count in sorted(theme_counts.items(), key=lambda item: item[1], reverse=True)
        ][:10]
        top_text = ", ".join(f"{item.symbol} ({item.candidate_score:.0f})" for item in top) or "no scored candidates"
        summary = (
            f"Weekly rotation scan ranked {len(ranked)} assets. "
            f"{len(passes)} passed the checklist and {len(watchlist)} are watchlist candidates. "
            f"Top ranked assets: {top_text}."
        )
        if unscored_count:
            summary += f" {unscored_count} symbols could not be scored (insufficient data) and were excluded from rankings."
        if diagnostics:
            summary += f" Data gaps: {len(diagnostics)} symbols had missing or partial data."
        with self.db.get_session() as session:
            memo = RotationMemo(
                signal_run_id=run_id,
                title="Weekly Rotation Memo",
                summary=summary,
                themes_json=json.dumps(themes, ensure_ascii=False),
                generated_at=datetime.now(),
            )
            session.add(memo)
            session.commit()
            return _rotation_memo_dict(memo)

    def _recent_source_evidence(self, symbol: str) -> List[Dict[str, Any]]:
        cutoff = datetime.now() - timedelta(days=45)
        with self.db.get_session() as session:
            rows = session.execute(
                select(ExtractedAssetMention, ResearchItem, ResearchSource)
                .join(ResearchItem, ResearchItem.id == ExtractedAssetMention.research_item_id)
                .join(ResearchSource, ResearchSource.id == ExtractedAssetMention.source_id, isouter=True)
                .where(
                    and_(
                        ExtractedAssetMention.asset_symbol == symbol.upper(),
                        ExtractedAssetMention.created_at >= cutoff,
                    )
                )
                .order_by(desc(ExtractedAssetMention.created_at))
                .limit(5)
            ).all()
            return [
                {
                    "source": source.username if source else item.source_type,
                    "url": item.url,
                    "direction": mention.direction,
                    "time_horizon": mention.time_horizon,
                    "confidence": mention.confidence,
                    "source_credibility": source.credibility_score if source else None,
                    "source_hit_rate": source.hit_rate if source else None,
                    "source_calls_tracked": source.calls_tracked if source else None,
                    "catalyst_tags": _json_loads(mention.catalyst_tags, []),
                    "excerpt": mention.extracted_text,
                }
                for mention, item, source in rows
            ]

    def _evaluate_source_calls(self, payload: Dict[str, Any], *, asset_type: str) -> None:
        symbol = str(payload.get("symbol") or "").upper()
        dated_closes = _dated_closes(payload.get("bars") or [])
        if not symbol or len(dated_closes) < 2:
            return

        cutoff = datetime.now() - timedelta(days=180)
        source_ids: set[int] = set()
        with self.db.get_session() as session:
            mentions = session.execute(
                select(ExtractedAssetMention).where(
                    and_(
                        ExtractedAssetMention.asset_symbol == symbol,
                        ExtractedAssetMention.asset_type == asset_type,
                        ExtractedAssetMention.created_at >= cutoff,
                        ExtractedAssetMention.source_id.is_not(None),
                    )
                )
            ).scalars().all()
            for mention in mentions:
                if not mention.source_id:
                    continue
                mention_date = mention.created_at or cutoff
                target_date = mention_date + timedelta(days=30)
                if datetime.now() < target_date:
                    continue
                start = _close_on_or_after(dated_closes, mention_date)
                end = _close_on_or_after(dated_closes, target_date)
                if start is None or end is None or not start["close"]:
                    continue
                return_pct = (end["close"] - start["close"]) / start["close"] * 100
                outcome = _call_outcome(direction=mention.direction, return_pct=return_pct)
                score = session.execute(
                    select(SourceCallScore).where(
                        and_(
                            SourceCallScore.mention_id == mention.id,
                            SourceCallScore.horizon_days == 30,
                        )
                    )
                ).scalar_one_or_none()
                if score is None:
                    score = SourceCallScore(
                        source_id=int(mention.source_id),
                        mention_id=mention.id,
                        symbol=symbol,
                        asset_type=asset_type,
                        horizon_days=30,
                    )
                    session.add(score)
                score.direction = mention.direction
                score.start_price = start["close"]
                score.end_price = end["close"]
                score.return_pct = round(return_pct, 4)
                score.outcome = outcome
                score.evaluated_at = datetime.now()
                source_ids.add(int(mention.source_id))

            for source_id in source_ids:
                scores = session.execute(
                    select(SourceCallScore).where(SourceCallScore.source_id == source_id)
                ).scalars().all()
                source = session.get(ResearchSource, source_id)
                if not source or not scores:
                    continue
                wins = sum(1 for item in scores if item.outcome == "win")
                tracked = len(scores)
                hit_rate = wins / tracked * 100
                source.calls_tracked = tracked
                source.hit_rate = round(hit_rate, 2)
                source.credibility_score = round(max(0, min(100, 50 + (hit_rate - 50) * 0.8)), 2)
                source.updated_at = datetime.now()
            session.commit()

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    def _valid_x_access_token(self, account: Dict[str, Any]) -> str:
        expires_at_raw = account.get("expires_at")
        expires_at = datetime.fromisoformat(expires_at_raw) if expires_at_raw else None
        if expires_at is None or expires_at > datetime.now() + timedelta(minutes=2):
            return str(account["access_token"])
        refresh_token = account.get("refresh_token")
        if not refresh_token:
            return str(account["access_token"])
        payload = self.x_client.refresh_token(
            refresh_token=refresh_token,
            client_id=_x_client_id() or "",
            client_secret=_x_client_secret(),
        )
        access_token = payload.get("access_token") or account["access_token"]
        new_expires_at = datetime.now() + timedelta(seconds=int(payload.get("expires_in") or 7200))
        with self.db.get_session() as session:
            row = session.get(XOAuthAccount, int(account["id"]))
            if row:
                row.access_token = access_token
                row.refresh_token = payload.get("refresh_token") or row.refresh_token
                row.expires_at = new_expires_at
                row.updated_at = datetime.now()
                session.commit()
        return str(access_token)

    def _store_x_bookmark_payload(self, payload: Dict[str, Any]) -> tuple[int, int]:
        users = {
            str(user.get("id")): user
            for user in ((payload.get("includes") or {}).get("users") or [])
            if user.get("id")
        }
        imported = 0
        mentions_count = 0
        for tweet in payload.get("data") or []:
            tweet_id = str(tweet.get("id") or "")
            text = str(tweet.get("text") or "")
            if not tweet_id or not text:
                continue
            author = users.get(str(tweet.get("author_id"))) or {}
            source = self._upsert_source(
                platform="x",
                external_id=str(author.get("id") or tweet.get("author_id") or "unknown"),
                username=author.get("username"),
                display_name=author.get("name"),
            )
            with self.db.get_session() as session:
                item = session.execute(
                    select(ResearchItem).where(
                        and_(ResearchItem.source_type == "x_bookmark", ResearchItem.external_id == tweet_id)
                    )
                ).scalar_one_or_none()
                if item is None:
                    item = ResearchItem(
                        source_id=source["id"],
                        source_type="x_bookmark",
                        external_id=tweet_id,
                        url=f"https://x.com/{source.get('username') or 'i'}/status/{tweet_id}",
                        title=f"X bookmark {tweet_id}",
                        content=text,
                        published_at=_parse_dt(tweet.get("created_at")),
                        bookmarked_at=datetime.now(),
                        raw_payload=json.dumps(tweet, ensure_ascii=False),
                    )
                    session.add(item)
                    session.flush()
                    imported += 1
                else:
                    item.content = text
                    item.raw_payload = json.dumps(tweet, ensure_ascii=False)
                    item.updated_at = datetime.now()
                item_id = int(item.id)
                session.commit()
            extracted = extract_asset_mentions(text)
            mentions_count += self._store_mentions(item_id, source["id"], extracted)
        return imported, mentions_count

    def _upsert_source(
        self,
        *,
        platform: str,
        external_id: str,
        username: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        with self.db.get_session() as session:
            source = session.execute(
                select(ResearchSource).where(
                    and_(ResearchSource.platform == platform, ResearchSource.external_id == external_id)
                )
            ).scalar_one_or_none()
            if source is None:
                source = ResearchSource(platform=platform, external_id=external_id)
                session.add(source)
                session.flush()
            source.username = username or source.username or external_id
            source.display_name = display_name or source.display_name
            if platform == "x" and source.username:
                source.profile_url = f"https://x.com/{source.username}"
            source.updated_at = datetime.now()
            payload = _source_dict(source)
            session.commit()
            return payload

    def _store_mentions(self, item_id: int, source_id: Optional[int], mentions: List[AssetMentionDraft]) -> int:
        if not mentions:
            return 0
        count = 0
        with self.db.get_session() as session:
            for mention in mentions:
                existing = session.execute(
                    select(ExtractedAssetMention).where(
                        and_(
                            ExtractedAssetMention.research_item_id == item_id,
                            ExtractedAssetMention.asset_symbol == mention.symbol,
                            ExtractedAssetMention.direction == mention.direction,
                        )
                    )
                ).scalar_one_or_none()
                if existing is None:
                    existing = ExtractedAssetMention(
                        research_item_id=item_id,
                        source_id=source_id,
                        asset_symbol=mention.symbol,
                        asset_type=mention.asset_type,
                        direction=mention.direction,
                    )
                    session.add(existing)
                    count += 1
                existing.time_horizon = mention.time_horizon
                existing.confidence = mention.confidence
                existing.catalyst_tags = json.dumps(mention.catalyst_tags, ensure_ascii=False)
                existing.extracted_text = mention.extracted_text
            session.commit()
        return count


def _candidate_row(run_id: int, candidate: CandidateScore) -> SignalCandidate:
    return SignalCandidate(
        signal_run_id=run_id,
        symbol=candidate.symbol,
        asset_type=candidate.asset_type,
        name=candidate.name,
        candidate_score=candidate.candidate_score,
        checklist_status=candidate.checklist_status,
        entry_zone=candidate.entry_zone,
        invalidation=candidate.invalidation,
        risk_reward=candidate.risk_reward,
        source_evidence=json.dumps(candidate.source_evidence, ensure_ascii=False),
        why_not_higher=candidate.why_not_higher,
        metrics_json=json.dumps(candidate.metrics, ensure_ascii=False),
    )


def _normalize_symbols(symbols: List[str]) -> List[str]:
    result = []
    for symbol in symbols:
        cleaned = (symbol or "").strip().upper()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _x_client_id() -> str:
    return os.getenv("X_CLIENT_ID", "").strip()


def _x_client_secret() -> Optional[str]:
    return os.getenv("X_CLIENT_SECRET", "").strip() or None


def _x_redirect_uri() -> str:
    return os.getenv("X_REDIRECT_URI", "").strip()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value else None


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        parsed = json.loads(value)
    except Exception:
        return default
    return default if parsed is None else parsed


def _dated_closes(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    closes = []
    for row in bars:
        date_value = _parse_dt(row.get("date"))
        if date_value is None:
            continue
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0:
            continue
        closes.append({"date": date_value, "close": close})
    closes.sort(key=lambda item: item["date"])
    return closes


def _close_on_or_after(dated_closes: List[Dict[str, Any]], when: datetime) -> Optional[Dict[str, Any]]:
    for row in dated_closes:
        if row["date"].date() >= when.date():
            return row
    return None


def _call_outcome(*, direction: Optional[str], return_pct: float) -> str:
    normalized = (direction or "neutral").lower()
    if normalized == "bullish":
        return "win" if return_pct > 0 else "loss"
    if normalized == "bearish":
        return "win" if return_pct < 0 else "loss"
    return "neutral"


def _source_dict(row: ResearchSource) -> Dict[str, Any]:
    return {
        "id": row.id,
        "platform": row.platform,
        "external_id": row.external_id,
        "username": row.username,
        "display_name": row.display_name,
        "profile_url": row.profile_url,
        "credibility_score": row.credibility_score,
        "calls_tracked": row.calls_tracked,
        "hit_rate": row.hit_rate,
    }


def _mention_dict(row: ExtractedAssetMention) -> Dict[str, Any]:
    return {
        "id": row.id,
        "asset_symbol": row.asset_symbol,
        "asset_type": row.asset_type,
        "direction": row.direction,
        "time_horizon": row.time_horizon,
        "confidence": row.confidence,
        "catalyst_tags": _json_loads(row.catalyst_tags, []),
        "extracted_text": row.extracted_text,
        "created_at": _iso(row.created_at),
    }


def _research_item_dict(row: ResearchItem, *, mentions: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "id": row.id,
        "source_id": row.source_id,
        "source_type": row.source_type,
        "external_id": row.external_id,
        "url": row.url,
        "title": row.title,
        "content": row.content,
        "published_at": _iso(row.published_at),
        "bookmarked_at": _iso(row.bookmarked_at),
        "created_at": _iso(row.created_at),
        "mentions": mentions,
    }


def _x_account_dict(row: XOAuthAccount) -> Dict[str, Any]:
    return {
        "id": row.id,
        "provider_user_id": row.provider_user_id,
        "username": row.username,
        "display_name": row.display_name,
        "access_token": row.access_token,
        "refresh_token": row.refresh_token,
        "expires_at": _iso(row.expires_at),
        "last_sync_at": _iso(row.last_sync_at),
    }


def _signal_candidate_dict(row: SignalCandidate) -> Dict[str, Any]:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "asset_type": row.asset_type,
        "name": row.name,
        "candidate_score": row.candidate_score,
        "checklist_status": row.checklist_status,
        "entry_zone": row.entry_zone,
        "invalidation": row.invalidation,
        "risk_reward": row.risk_reward,
        "source_evidence": _json_loads(row.source_evidence, []),
        "why_not_higher": row.why_not_higher,
        "metrics": _json_loads(row.metrics_json, {}),
        "created_at": _iso(row.created_at),
    }


def _rotation_memo_dict(row: RotationMemo) -> Dict[str, Any]:
    return {
        "id": row.id,
        "signal_run_id": row.signal_run_id,
        "title": row.title,
        "summary": row.summary,
        "themes": _json_loads(row.themes_json, []),
        "generated_at": _iso(row.generated_at),
        "created_at": _iso(row.created_at),
    }
