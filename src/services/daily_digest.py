# -*- coding: utf-8 -*-
"""Daily market digest and threshold alerts for the scheduled snapshot job.

The digest summarizes the persisted quantitative regime, breadth, top signal
candidates, and the paper portfolio. Threshold alerts (regime flip, VIX stress,
paper stop/target hits) fire at most once per condition per day, with the
"already alerted" state persisted under data/.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.notification import NotificationService
from src.storage import DatabaseManager, NewsIntel, SignalCandidate, SignalRun

logger = logging.getLogger(__name__)

_STATE_FILENAME = "alert_state.json"

_IMPACT_EMOJI = {"market_moving": "🔴", "sector": "🟠", "stock_specific": "⚪"}


def _apply_freshness(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach as_of-derived age/freshness metadata (same pattern as FreeDataService.get_latest_market_breadth)."""
    payload.setdefault("as_of", None)
    age_days = None
    if payload.get("as_of"):
        try:
            age_days = (date.today() - datetime.fromisoformat(str(payload["as_of"])[:10]).date()).days
        except ValueError:
            age_days = None
    payload["age_days"] = age_days
    payload["freshness"] = "fresh" if age_days is not None and age_days <= 4 else "stale" if age_days is not None else "unknown"
    return payload


def _slim_scorecard_population(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Brief-sized {flagged, baseline, edge} cut of a scorecard population block.

    Drops the by_screen/by_score_band/by_month breakdowns (the /scorecard page
    fetches the full summary itself) and keeps only what the Today card shows.
    """
    block = block or {}
    flagged = block.get("flagged") or {}
    baseline = block.get("baseline") or {}
    return {
        "flagged": {
            "flags": flagged.get("flags"),
            "open": flagged.get("open"),
            "hits": flagged.get("hits"),
            "decided": flagged.get("decided"),
            "batting_average": flagged.get("batting_average"),
            "avg_days_to_hit": flagged.get("avg_days_to_hit"),
            "avg_max_gain_pct": flagged.get("avg_max_gain_pct"),
        },
        "baseline": {
            "decided": baseline.get("decided"),
            "batting_average": baseline.get("batting_average"),
        },
        "edge": block.get("edge"),
    }


def _is_latest_us_session(as_of: Any) -> bool:
    """True when an as-of date is the most recent expected US trading session."""
    if not as_of:
        return False
    try:
        from src.services.research_market_data import _latest_expected_us_bar_date

        return datetime.fromisoformat(str(as_of)[:10]).date() >= _latest_expected_us_bar_date()
    except Exception:
        return False


def _prune_dated_state(state: Dict[str, Any], *, max_age_days: int = 60) -> None:
    """Drop per-symbol dedup entries (symbol -> ISO date) older than the window.

    Keeps data/alert_state.json from growing forever as symbols rotate through
    the discovery/watchlist alert checks. Scalar state keys are untouched.
    """
    cutoff = date.today() - timedelta(days=max_age_days)
    for key in ("discovery_high_conviction_on", "watchlist_mover_on"):
        entries = state.get(key)
        if not isinstance(entries, dict):
            continue
        kept: Dict[str, Any] = {}
        for symbol, stamp in entries.items():
            try:
                if datetime.fromisoformat(str(stamp)[:10]).date() >= cutoff:
                    kept[symbol] = stamp
            except ValueError:
                continue
        state[key] = kept


class DailyDigestService:
    def __init__(
        self,
        *,
        notifier: Optional[NotificationService] = None,
        db: Optional[DatabaseManager] = None,
        state_path: Optional[Path] = None,
    ):
        self.notifier = notifier or NotificationService()
        self.db = db or DatabaseManager.get_instance()
        self.state_path = state_path or Path(os.getenv("DATA_DIR", "data")) / _STATE_FILENAME

    # ------------------------------------------------------------------ digest

    def send_daily_digest(self, *, paper_result: Optional[Dict[str, Any]] = None) -> bool:
        content = self.build_digest(paper_result=paper_result)
        if not self.notifier.is_available():
            logger.warning("No notification channel configured; digest not sent.")
            logger.info("Digest content:\n%s", content)
            return False
        return self.notifier.send(content, email_send_to_all=True)

    def build_digest(self, *, paper_result: Optional[Dict[str, Any]] = None) -> str:
        brief = self.collect_brief()
        sections = ["# 📊 Daily Market Snapshot"]
        sections.extend(self._regime_section(brief["regime"]))
        sections.extend(self._rotation_section(brief["rotation"]))
        sections.extend(self._cycle_section(brief["cycle"]))
        sections.extend(self._signals_section(brief["signals"]))
        sections.extend(self._discovery_section(brief["discovery"]))
        sections.extend(self._down_day_section(brief["down_day_rs"]))
        sections.extend(self._headlines_section(brief["headlines"]))
        sections.extend(self._paper_section(paper_result))
        return "\n".join(sections)

    # ------------------------------------------------------------------ brief data

    def collect_brief(self) -> Dict[str, Any]:
        """Aggregate payload shared by the Telegram digest and the web daily brief.

        Each section carries ``as_of`` / ``age_days`` / ``freshness`` plus a
        ``status`` of ``completed`` or ``missing``. The ``_*_section`` markdown
        renderers consume this exact payload so the two outputs cannot drift.

        Note: The paper-portfolio section is NOT included here; it is fetched
        separately by the web TodayPage via /api/v1/portfolio and passed directly
        to build_digest() as ``paper_result``.
        """
        return {
            "regime": self._collect_regime(),
            "rotation": self._collect_rotation(),
            "cycle": self._collect_cycle(),
            "signals": self._collect_signals(),
            "discovery": self._collect_discovery(),
            "down_day_rs": self._collect_down_day(),
            "headlines": self._collect_headlines(),
            "scorecard": self._collect_scorecard(),
        }

    def _collect_regime(self) -> Dict[str, Any]:
        history = self._regime_history()
        if not history:
            return _apply_freshness({"status": "missing"})
        latest = history[-1]
        previous = history[-2] if len(history) >= 2 else None
        return _apply_freshness({
            "status": "completed",
            "as_of": latest.get("as_of"),
            "latest": latest,
            "previous": previous,
            "history": [
                {"as_of": row.get("as_of"), "score": row.get("score"), "vix": row.get("vix")}
                for row in history
            ],
            "breadth_trend": self._breadth_trend(),
        })

    def _breadth_trend(self) -> List[Dict[str, Any]]:
        """Last 30 cached breadth rows slimmed for the regime mini-trend (fail-open)."""
        try:
            from src.services.free_data_service import DEFAULT_BREADTH_UNIVERSE

            universe = os.getenv("RESEARCH_BREADTH_UNIVERSE", DEFAULT_BREADTH_UNIVERSE).strip() or DEFAULT_BREADTH_UNIVERSE
            rows = self.db.get_market_breadth_history(days=30, universe=universe)
            if not rows and universe != "us_stocks":
                rows = self.db.get_market_breadth_history(days=30, universe="us_stocks")
            return [
                {"as_of": row.get("as_of"), "pct_above_50dma": row.get("above_sma50_pct")}
                for row in rows
            ]
        except Exception as exc:
            logger.warning("Breadth trend read failed: %s", exc)
            return []

    def _collect_rotation(self) -> Dict[str, Any]:
        try:
            snapshot = self.db.get_latest_sector_rotation_snapshot()
        except Exception as exc:
            logger.warning("Rotation section failed: %s", exc)
            snapshot = None
        if not snapshot:
            return _apply_freshness({"status": "missing"})
        return _apply_freshness({"status": "completed", **snapshot})

    def _collect_cycle(self) -> Dict[str, Any]:
        try:
            snapshot = self.db.get_latest_macro_cycle_snapshot()
        except Exception as exc:
            logger.warning("Cycle section failed: %s", exc)
            snapshot = None
        if not snapshot:
            return _apply_freshness({"status": "missing"})
        return _apply_freshness({"status": "completed", **snapshot})

    def _collect_signals(self) -> Dict[str, Any]:
        try:
            with self.db.get_session() as session:
                run = session.execute(
                    select(SignalRun).order_by(desc(SignalRun.id)).limit(1)
                ).scalar_one_or_none()
                if run is None:
                    return _apply_freshness({"status": "missing"})
                run_id = run.id
                run_stamp = run.completed_at or run.started_at
                rows = session.execute(
                    select(SignalCandidate)
                    .where(
                        SignalCandidate.signal_run_id == run_id,
                        SignalCandidate.candidate_score.isnot(None),
                    )
                    .order_by(desc(SignalCandidate.candidate_score))
                    .limit(5)
                ).scalars().all()
                unscored = session.execute(
                    select(SignalCandidate.id).where(
                        SignalCandidate.signal_run_id == run_id,
                        SignalCandidate.candidate_score.is_(None),
                    )
                ).scalars().all()
                top = [
                    {
                        "symbol": row.symbol,
                        "asset_type": row.asset_type,
                        "name": row.name,
                        "candidate_score": row.candidate_score,
                        "checklist_status": row.checklist_status,
                        "entry_zone": row.entry_zone,
                    }
                    for row in rows
                ]
        except Exception as exc:
            logger.warning("Signals section failed: %s", exc)
            return _apply_freshness({"status": "missing"})
        return _apply_freshness({
            "status": "completed",
            "as_of": run_stamp.date().isoformat() if run_stamp else None,
            "run_id": run_id,
            "top": top,
            "unscored_count": len(unscored),
        })

    def _collect_discovery(self) -> Dict[str, Any]:
        try:
            snapshot = self.db.get_latest_discovery_snapshot()
        except Exception as exc:
            logger.warning("Discovery section failed: %s", exc)
            snapshot = None
        if not snapshot:
            return _apply_freshness({"status": "missing"})
        top = [
            {
                "symbol": row.get("symbol"),
                "name": row.get("name"),
                "composite_score": row.get("composite_score"),
                "candidate_score": row.get("candidate_score"),
                "checklist_status": row.get("checklist_status"),
                "entry_zone": row.get("entry_zone"),
                "reason": row.get("reason"),
                # Per-screen flags consumed by the web Today card badges only;
                # the digest renderer ignores them (output unchanged).
                "near_52w_high": row.get("near_52w_high"),
                "rs_top_decile": row.get("rs_top_decile"),
                "unusual_volume": row.get("unusual_volume"),
                "sector_tailwind": row.get("sector_tailwind"),
                "quiet_accumulation": row.get("quiet_accumulation"),
                "beaten_down_reversal": row.get("beaten_down_reversal"),
            }
            for row in (snapshot.get("constituents") or [])[:10]
        ]
        return _apply_freshness({
            "status": "completed",
            "as_of": snapshot.get("as_of"),
            "universe_size": snapshot.get("universe_size"),
            "qualified_size": snapshot.get("qualified_size"),
            "top": top,
            "summary": snapshot.get("summary"),
        })

    def _collect_down_day(self) -> Dict[str, Any]:
        try:
            snapshot = self.db.get_latest_down_day_rs_snapshot()
        except Exception as exc:
            logger.warning("Down-day section failed: %s", exc)
            snapshot = None
        if not snapshot:
            return _apply_freshness({"status": "missing"})
        payload = _apply_freshness({"status": "completed", **snapshot})
        payload["is_latest_session"] = _is_latest_us_session(payload.get("as_of"))
        return payload

    def _collect_headlines(self) -> Dict[str, Any]:
        """Top 3 of today's impact-scored headlines (noise excluded)."""
        try:
            start = datetime.now() - timedelta(hours=26)
            with self.db.get_session() as session:
                rows = session.execute(
                    select(NewsIntel)
                    .where(
                        NewsIntel.fetched_at >= start,
                        NewsIntel.impact_score.isnot(None),
                        NewsIntel.impact_label != "noise",
                    )
                    .order_by(desc(NewsIntel.impact_score))
                    .limit(3)
                ).scalars().all()
                top = [
                    {
                        "id": row.id,
                        "title": row.title,
                        "source": row.source,
                        "code": row.code,
                        "url": row.url,
                        "published_at": row.published_date.isoformat() if row.published_date else None,
                        "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
                        "impact_score": row.impact_score,
                        "impact_label": row.impact_label,
                        "impact_reason": row.impact_reason,
                    }
                    for row in rows
                ]
        except Exception as exc:
            logger.warning("Headlines section failed: %s", exc)
            return _apply_freshness({"status": "missing"})
        if not top:
            return _apply_freshness({"status": "missing"})
        return _apply_freshness({
            "status": "completed",
            "as_of": date.today().isoformat(),
            "top": top,
        })

    def _collect_scorecard(self) -> Dict[str, Any]:
        """Slim hit-rate scorecard stats for the web brief (fail-open).

        No ``_*_section`` renderer reads this key, so the Telegram digest stays
        byte-identical (regression-tested in tests/test_daily_brief.py).
        ``as_of`` = the latest REAL flag's as_of date — the freshest gradeable
        idea the system surfaced, matching the discovery card's freshness
        semantics; falls back to the summary's generated_at date when only
        simulated (bootstrap) rows exist.
        """
        try:
            from src.services.scorecard_service import ScorecardService

            summary = ScorecardService(db=self.db).build_summary()
            real_flags = ((summary.get("real") or {}).get("baseline") or {}).get("flags") or 0
            simulated = summary.get("simulated")
            if not real_flags and not simulated:
                return _apply_freshness({"status": "missing"})
            as_of = None
            if real_flags:
                from src.services.scorecard_service import DEFAULT_LOOKBACK_DAYS

                rows = self.db.get_discovery_flag_outcomes(
                    simulated=False, limit=1, days=DEFAULT_LOOKBACK_DAYS
                )["rows"]
                as_of = rows[0]["as_of"] if rows else None
            if not as_of:
                as_of = str(summary.get("generated_at") or "")[:10] or None
            return _apply_freshness({
                "status": "completed",
                "as_of": as_of,
                "window_days": summary.get("window_days"),
                "hit_threshold_pct": summary.get("hit_threshold_pct"),
                "real": _slim_scorecard_population(summary.get("real")),
                "simulated": _slim_scorecard_population(simulated) if simulated else None,
            })
        except Exception as exc:
            logger.warning("Scorecard section failed: %s", exc)
            return _apply_freshness({"status": "missing"})

    def _regime_history(self) -> List[Dict[str, Any]]:
        try:
            return self.db.get_market_regime_history(days=14)
        except Exception as exc:
            logger.warning("Regime history read failed: %s", exc)
            return []

    # ------------------------------------------------------------------ renderers

    def _regime_section(self, payload: Dict[str, Any]) -> List[str]:
        if payload.get("status") != "completed":
            return ["", "## Regime", "No regime snapshot persisted yet — run the snapshot job."]
        latest = payload["latest"]
        previous = payload.get("previous")
        delta = ""
        if previous and latest.get("score") is not None and previous.get("score") is not None:
            change = latest["score"] - previous["score"]
            delta = f" ({change:+.1f} vs {previous.get('as_of')})"
        lines = [
            "",
            f"## Regime: {latest.get('regime')} — {latest.get('score')}/100{delta}",
            f"- As of {latest.get('as_of')}, confidence {latest.get('confidence') or 'n/a'}",
        ]
        if latest.get("vix") is not None:
            term = "inverted ⚠️" if latest.get("term_inverted") else "normal"
            vix_line = f"- VIX {latest['vix']}"
            if latest.get("vix3m") is not None:
                vix_line += f" / VIX3M {latest['vix3m']}"
            lines.append(f"{vix_line} (term structure {term})")
        if latest.get("breadth_above_50dma_pct") is not None:
            lines.append(f"- Breadth: {latest['breadth_above_50dma_pct']}% of tracked ETFs above their 50DMA")
        if previous and previous.get("regime") and previous["regime"] != latest.get("regime"):
            lines.append(f"- ⚠️ Regime changed from {previous['regime']} on {previous.get('as_of')}")
        return lines

    def _rotation_section(self, payload: Dict[str, Any]) -> List[str]:
        if payload.get("status") != "completed":
            return []
        snapshot = payload
        leaders = snapshot.get("leaders") or {}

        def _fmt(entry: Dict[str, Any]) -> str:
            change = entry.get("rank_change")
            if entry.get("entered_top3_1m"):
                marker = " (new)"
            elif change is None:
                marker = ""
            elif change > 0:
                marker = f" ↑{change}"
            elif change < 0:
                marker = f" ↓{abs(change)}"
            else:
                marker = ""
            ret = entry.get("return_pct")
            ret_text = f" {ret:+.1f}%" if ret is not None else ""
            return f"{entry['symbol']}{ret_text}{marker}"

        lines = ["", f"## Rotation (vs {snapshot.get('benchmark') or 'SPY'}, as of {snapshot.get('as_of')})"]
        for horizon in ("1D", "1W", "1M", "3M"):
            block = leaders.get(horizon) or {}
            tops = block.get("leaders") or []
            bottoms = block.get("laggards") or []
            if not tops:
                continue
            lines.append(
                f"- **{horizon}** hot: {', '.join(_fmt(e) for e in tops)} | "
                f"not: {', '.join(_fmt(e) for e in bottoms)}"
            )
        return lines

    def _cycle_section(self, payload: Dict[str, Any]) -> List[str]:
        if payload.get("status") != "completed":
            return []
        snapshot = payload
        phase = str(snapshot.get("phase") or "unknown")
        highlights = [
            reading.get("plain")
            for reading in snapshot.get("indicators") or []
            if reading.get("plain")
        ][:3]
        lines = [
            "",
            f"## Cycle: {phase.capitalize()} (confidence {snapshot.get('confidence') or 'n/a'})",
        ]
        if highlights:
            lines.append("- " + " · ".join(highlights))
        playbook = snapshot.get("playbook") or []
        if playbook:
            lines.append(f"- {phase.capitalize()}-cycle playbook: {', '.join(playbook)}")
        if snapshot.get("divergence"):
            lines.append(f"- ⚠️ {snapshot.get('divergence_note') or 'Tape leadership diverges from the phase playbook.'}")
        crypto = snapshot.get("crypto") or {}
        if crypto.get("gauge"):
            lines.append(f"- Crypto: {crypto.get('plain_english') or crypto['gauge']}")
        return lines

    def _signals_section(self, payload: Dict[str, Any]) -> List[str]:
        if payload.get("status") != "completed":
            return []
        top = payload.get("top") or []
        if not top:
            return ["", "## Top signals", "No scored candidates in the latest scan."]
        lines = ["", "## Top signals"]
        for item in top:
            lines.append(
                f"- **{item['symbol']}** {item['candidate_score']:.0f} ({item['checklist_status']}) — entry {item['entry_zone']}"
            )
        if payload.get("unscored_count"):
            lines.append(f"- _{payload['unscored_count']} symbols unscored (data unavailable)_")
        return lines

    def _discovery_section(self, payload: Dict[str, Any]) -> List[str]:
        if payload.get("status") != "completed":
            return []
        top = (payload.get("top") or [])[:5]
        if not top:
            return ["", "## New ideas", "No new ideas passed the discovery screens today."]
        lines = ["", f"## New ideas (non-watchlist, as of {payload.get('as_of')})"]
        for item in top:
            score = item.get("candidate_score")
            score_text = f" {score:.0f}" if score is not None else ""
            status = item.get("checklist_status")
            status_text = f" ({status})" if status else ""
            reason = item.get("reason") or "ranked by composite strength"
            lines.append(f"- **{item.get('symbol')}**{score_text}{status_text} — {reason}")
        return lines

    def _down_day_section(self, payload: Dict[str, Any]) -> List[str]:
        """Render only when the latest snapshot is an actual triggered down day."""
        if payload.get("status") != "completed" or not payload.get("triggered"):
            return []
        if payload.get("freshness") == "stale":
            return []
        spy = payload.get("spy_return_pct")
        spy_text = f"{spy:+.2f}%" if spy is not None else "down"
        lines = ["", f"## Down day (SPY {spy_text}, as of {payload.get('as_of')})"]
        stocks = ", ".join(
            str(row.get("symbol")) for row in (payload.get("stocks_holding_up") or [])[:8] if row.get("symbol")
        )
        sectors = ", ".join(
            str(row.get("symbol")) for row in (payload.get("sectors_holding_up") or []) if row.get("symbol")
        )
        if stocks:
            lines.append(f"- **Holding up on a {spy_text} day:** {stocks}")
        if sectors:
            lines.append(f"- **Sectors holding up:** {sectors}")
        if not stocks and not sectors:
            lines.append("- Nothing held up — broad selloff.")
        return lines

    def _headlines_section(self, payload: Dict[str, Any]) -> List[str]:
        """Render only when scored headlines exist; noise is excluded at collection."""
        if payload.get("status") != "completed":
            return []
        top = payload.get("top") or []
        if not top:
            return []
        lines = ["", f"## Headlines (as of {payload.get('as_of')})"]
        for item in top:
            emoji = _IMPACT_EMOJI.get(item.get("impact_label") or "", "⚪")
            score = item.get("impact_score")
            score_text = f" [{score}]" if score is not None else ""
            source_text = f" — {item['source']}" if item.get("source") else ""
            lines.append(f"- {emoji}{score_text} {item.get('title')}{source_text}")
        return lines

    def _paper_section(self, paper_result: Optional[Dict[str, Any]]) -> List[str]:
        if not paper_result:
            return []
        snapshot = paper_result.get("snapshot") or {}
        lines = ["", "## Paper portfolio (system signals)"]
        if snapshot.get("total_equity") is not None:
            lines.append(
                f"- Equity ${snapshot['total_equity']:,.0f} | cash ${snapshot.get('total_cash') or 0:,.0f} "
                f"| unrealized {snapshot.get('unrealized_pnl') or 0:+,.0f} | realized {snapshot.get('realized_pnl') or 0:+,.0f}"
            )
        for item in paper_result.get("opened") or []:
            lines.append(f"- 🟢 Opened {item['symbol']} @ {item['price']} ({item['fill_date']})")
        for item in paper_result.get("closed") or []:
            emoji = "🎯" if item["reason"] == "target" else "🛑"
            lines.append(
                f"- {emoji} Closed {item['symbol']} @ {item['exit_price']} ({item['reason']}, {item['pnl_pct']:+.1f}%)"
            )
        open_positions = paper_result.get("open_positions") or []
        lines.append(f"- Open positions: {len(open_positions)}")
        return lines

    # ------------------------------------------------------------------ alerts

    def send_threshold_alerts(self, *, paper_result: Optional[Dict[str, Any]] = None) -> List[str]:
        """Fire regime/VIX/paper/discovery/watchlist/down-day alerts, each at
        most once per condition (deduped via the persisted alert state)."""
        state = self._load_state()
        today = date.today().isoformat()
        alerts: List[str] = []
        history = self._regime_history()
        latest = history[-1] if history else None
        previous = history[-2] if len(history) >= 2 else None

        if latest:
            regime = str(latest.get("regime") or "")
            if regime and state.get("last_regime") and regime != state["last_regime"]:
                alerts.append(
                    f"⚠️ **Regime change**: {state['last_regime']} → {regime} "
                    f"(score {latest.get('score')}/100, as of {latest.get('as_of')})"
                )
            state["last_regime"] = regime or state.get("last_regime")

            vix = latest.get("vix")
            if vix is not None and vix >= 20 and state.get("vix_above_20_on") != today:
                alerts.append(f"⚠️ **VIX stress**: VIX at {vix} (≥ 20)")
                state["vix_above_20_on"] = today
            if (
                vix is not None
                and previous
                and previous.get("vix")
                and previous["vix"] > 0
                and (vix - previous["vix"]) / previous["vix"] * 100 >= 15
                and state.get("vix_spike_on") != today
            ):
                alerts.append(
                    f"⚠️ **VIX spike**: {previous['vix']} → {vix} "
                    f"({(vix - previous['vix']) / previous['vix'] * 100:+.0f}%)"
                )
                state["vix_spike_on"] = today

        for item in (paper_result or {}).get("closed") or []:
            emoji = "🎯" if item["reason"] == "target" else "🛑"
            alerts.append(
                f"{emoji} Paper position **{item['symbol']}** closed: {item['reason']} "
                f"at {item['exit_price']} ({item['pnl_pct']:+.1f}%)"
            )

        alerts.extend(self._cycle_and_rotation_alerts(state, today))
        alerts.extend(self._discovery_high_conviction_alerts(state, today))
        alerts.extend(self._watchlist_mover_alerts(state, today))
        alerts.extend(self._down_day_trigger_alert(state, today))

        _prune_dated_state(state)
        self._save_state(state)
        if alerts and self.notifier.is_available():
            self.notifier.send("# 🚨 Market alerts\n\n" + "\n\n".join(alerts), email_send_to_all=True)
        elif alerts:
            logger.warning("Alerts raised but no notification channel configured: %s", alerts)
        return alerts

    def _cycle_and_rotation_alerts(self, state: Dict[str, Any], today: str) -> List[str]:
        """Cycle phase change + 1M top-3 rotation entries/exits, deduped via state."""
        alerts: List[str] = []
        try:
            cycle = self.db.get_latest_macro_cycle_snapshot()
        except Exception:
            cycle = None
        if cycle and cycle.get("phase") and cycle["phase"] != "unknown":
            phase = str(cycle["phase"])
            if state.get("last_cycle_phase") and phase != state["last_cycle_phase"]:
                alerts.append(
                    f"⚠️ **Cycle phase change**: {state['last_cycle_phase']} → {phase} "
                    f"(confidence {cycle.get('confidence') or 'n/a'})"
                )
            state["last_cycle_phase"] = phase

        try:
            rotation = self.db.get_latest_sector_rotation_snapshot()
        except Exception:
            rotation = None
        if rotation:
            ranked_1m = sorted(
                (
                    row
                    for row in rotation.get("constituents") or []
                    if (row.get("ranks") or {}).get("1M")
                ),
                key=lambda row: row["ranks"]["1M"],
            )
            top3 = sorted(row["symbol"] for row in ranked_1m[:3])
            previous_top3 = state.get("rotation_top3_1m")
            if previous_top3 is not None and top3 != previous_top3 and state.get("rotation_top3_alert_on") != today:
                entered = sorted(set(top3) - set(previous_top3))
                exited = sorted(set(previous_top3) - set(top3))
                parts = []
                if entered:
                    parts.append(f"{', '.join(entered)} entered the 1M top-3")
                if exited:
                    parts.append(f"{', '.join(exited)} dropped out")
                if parts:
                    alerts.append(f"📈 **Rotation**: {'; '.join(parts)}")
                    state["rotation_top3_alert_on"] = today
            if top3:
                state["rotation_top3_1m"] = top3
        return alerts

    def _discovery_high_conviction_alerts(self, state: Dict[str, Any], today: str) -> List[str]:
        """New high-conviction discovery ideas (both score gates), max 3 per day.

        "New" = absent from the previous snapshot's qualifying set (all
        qualifying rows count as new when no previous snapshot exists). A
        symbol re-alerts at most once every 30 days via
        state["discovery_high_conviction_on"][symbol] = as_of. Fail-open.
        """
        try:
            from src.services.free_data_service import _env_float

            history = self.db.get_discovery_history(days=7)
            latest = history[-1] if history else None
            if not latest or not _is_latest_us_session(latest.get("as_of")):
                return []
            as_of = str(latest.get("as_of"))[:10]
            as_of_date = datetime.fromisoformat(as_of).date()
            min_composite = _env_float("DISCOVERY_ALERT_MIN_COMPOSITE", 90.0)
            min_candidate = _env_float("DISCOVERY_ALERT_MIN_CANDIDATE", 70.0)

            def _qualifying(snapshot: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
                return [
                    row
                    for row in (snapshot or {}).get("constituents") or []
                    if (row.get("composite_score") or 0) >= min_composite
                    and (row.get("candidate_score") or 0) >= min_candidate
                ]

            previous = history[-2] if len(history) >= 2 else None
            previous_symbols = {
                str(row.get("symbol") or "").strip().upper() for row in _qualifying(previous)
            }
            seen = state.setdefault("discovery_high_conviction_on", {})
            fresh: List[Dict[str, Any]] = []
            for row in _qualifying(latest):
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol or symbol in previous_symbols:
                    continue
                if seen.get(symbol):
                    try:
                        last = datetime.fromisoformat(str(seen[symbol])[:10]).date()
                        if (as_of_date - last).days < 30:
                            continue
                    except ValueError:
                        pass
                fresh.append(row)
            fresh.sort(key=lambda row: -(row.get("composite_score") or 0))
            alerts: List[str] = []
            for row in fresh[:3]:
                symbol = str(row.get("symbol") or "").strip().upper()
                name = str(row.get("name") or "").strip()
                name_text = f" ({name})" if name else ""
                reason = row.get("reason") or "ranked by composite strength"
                alerts.append(
                    f"🎯 New high-conviction idea: {symbol}{name_text} — "
                    f"composite {row.get('composite_score'):.0f}, "
                    f"candidate {row.get('candidate_score'):.0f}. {reason}"
                )
                seen[symbol] = as_of
            return alerts
        except Exception as exc:
            logger.warning("High-conviction discovery alerts failed (skipped): %s", exc)
            return []

    def _watchlist_mover_alerts(self, state: Dict[str, Any], today: str) -> List[str]:
        """Big 1-day moves / volume surges on watchlist symbols, aggregated
        into ONE message, once per symbol per session. Per-symbol fail-open."""
        try:
            from src.services.free_data_service import _env_float
            from src.services.watchlist_service import WatchlistService

            symbols = WatchlistService().get_symbols()
            end = date.fromisoformat(today)
        except Exception as exc:
            logger.warning("Watchlist mover alerts failed (skipped): %s", exc)
            return []
        if not symbols:
            return []
        move_threshold = _env_float("WATCHLIST_MOVER_ALERT_PCT", 5.0)
        volume_threshold = _env_float("WATCHLIST_MOVER_VOLUME_RATIO", 2.5)
        seen = state.setdefault("watchlist_mover_on", {})
        parts: List[str] = []
        for symbol in symbols:
            try:
                bars = self.db.get_data_range(symbol, end - timedelta(days=45), end)
                if len(bars) < 2 or not _is_latest_us_session(bars[-1].date):
                    continue
                bar_date = bars[-1].date.isoformat()
                if seen.get(symbol) == bar_date:
                    continue
                closes = [bar.close for bar in bars]
                volumes = [bar.volume for bar in bars]
                move_pct = None
                if closes[-1] and closes[-2]:
                    move_pct = (closes[-1] / closes[-2] - 1) * 100
                vol_ratio = None
                baseline = [v for v in volumes[-21:-1] if v]
                # A thin baseline (newly added symbol) makes the ratio noise.
                if len(baseline) < 6:
                    baseline = []
                if volumes[-1] and baseline:
                    average = sum(baseline) / len(baseline)
                    if average > 0:
                        vol_ratio = volumes[-1] / average
                big_move = move_pct is not None and abs(move_pct) >= move_threshold
                volume_surge = vol_ratio is not None and vol_ratio >= volume_threshold
                if not big_move and not volume_surge:
                    continue
                text = symbol
                if move_pct is not None:
                    text += f" {move_pct:+.1f}%"
                if volume_surge:
                    text += f" on {vol_ratio:.1f}x volume"
                parts.append(text)
                seen[symbol] = bar_date
            except Exception as exc:
                logger.warning("Watchlist mover check failed for %s (skipped): %s", symbol, exc)
        if not parts:
            return []
        return ["📈 Watchlist movers: " + "; ".join(parts)]

    def _down_day_trigger_alert(self, state: Dict[str, Any], today: str) -> List[str]:
        """One alert per triggered down-day session, with the top holding-up names."""
        try:
            snapshot = self.db.get_latest_down_day_rs_snapshot()
        except Exception as exc:
            logger.warning("Down-day trigger alert failed (skipped): %s", exc)
            return []
        if not snapshot or not snapshot.get("triggered"):
            return []
        as_of = str(snapshot.get("as_of") or "")[:10]
        if not _is_latest_us_session(as_of) or state.get("down_day_alert_on") == as_of:
            return []
        spy = snapshot.get("spy_return_pct")
        spy_text = f"{spy:+.2f}%" if spy is not None else "down"
        # Best holders first: strongest outperformance vs SPY.
        holders = sorted(
            (snapshot.get("stocks_holding_up") or []),
            key=lambda row: row.get("rs_vs_spy_pp") or 0,
            reverse=True,
        )
        holding = ", ".join(
            str(row.get("symbol")) for row in holders[:5] if row.get("symbol")
        )
        message = f"🔻 Down day: SPY {spy_text}"
        if holding:
            message += f" — holding up: {holding}"
        state["down_day_alert_on"] = as_of
        return [message]

    def _load_state(self) -> Dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            logger.warning("Alert state save failed: %s", exc)
