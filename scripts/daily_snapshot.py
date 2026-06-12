#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily market snapshot orchestrator (run by launchd after US close).

Steps run in order, each isolated so one failure does not kill the run:
  breadth   - refresh the ETF-proxy market breadth cache
  regime    - compute + persist the quantitative regime snapshot (incl. VIX)
  rotation  - multi-timeframe sector rotation snapshot (RS vs SPY, RRG quadrants)
  cycle     - business-cycle phase from FRED + crypto cycle gauge (reads rotation)
  signals   - run the signal scan over the US + crypto universes
  discovery - scan the liquidity-gated US stock universe for new (non-watchlist) ideas
  scorecard - grade past discovery flags against the +20%-within-90-days hit gate
  analysis  - refresh the LLM analysis report for each RESEARCH_US_WATCHLIST symbol
  news      - score today's persisted headlines for market impact (one batched LLM call)
  portfolio - paper-trade the system's own recommendations and grade open positions
  backtest  - grade past analyses against actual outcomes
  notify    - send the Telegram/email digest and threshold alerts

Usage:
  python scripts/daily_snapshot.py                 # full run
  python scripts/daily_snapshot.py --steps regime,portfolio,notify   # intraday check
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402

logger = logging.getLogger("daily_snapshot")

ALL_STEPS = ["breadth", "regime", "rotation", "cycle", "signals", "discovery", "scorecard", "analysis", "news", "portfolio", "backtest", "notify"]


def run_breadth(context: dict) -> dict:
    import os

    from src.services.free_data_service import DEFAULT_BREADTH_UNIVERSE, FreeDataService

    universe = os.getenv("RESEARCH_BREADTH_UNIVERSE", DEFAULT_BREADTH_UNIVERSE).strip() or DEFAULT_BREADTH_UNIVERSE
    payload = FreeDataService(cache_enabled=False).run_market_breadth_cache(universe=universe)
    return {
        "as_of": payload.get("as_of"),
        "universe": payload.get("universe"),
        "symbols_passing_liquidity": payload.get("symbols_passing_liquidity"),
        "above_sma50_pct": payload.get("above_sma50_pct"),
        "excluded_stale_count": payload.get("excluded_stale_count"),
    }


def run_regime(context: dict) -> dict:
    from src.services.free_data_service import FreeDataService

    payload = FreeDataService(cache_enabled=False).run_market_regime_snapshot()
    return {
        "as_of": payload.get("as_of"),
        "regime": payload.get("regime"),
        "score": payload.get("score"),
        "vix": (payload.get("volatility") or {}).get("vix"),
        "persisted": (payload.get("persisted") or {}).get("as_of"),
    }


def run_rotation(context: dict) -> dict:
    from src.services.rotation_service import RotationService

    payload = RotationService().run_daily_snapshot()
    top = [row["symbol"] for row in (payload.get("constituents") or [])[:3]]
    return {
        "as_of": payload.get("as_of"),
        "symbols_ranked": payload.get("symbols_ranked"),
        "composite_top3": top,
        "warnings": len(payload.get("warnings") or []),
        "persisted": (payload.get("persisted") or {}).get("as_of"),
    }


def run_cycle(context: dict) -> dict:
    from src.services.macro_cycle_service import MacroCycleService

    payload = MacroCycleService().run_daily_snapshot()
    return {
        "as_of": payload.get("as_of"),
        "phase": payload.get("phase"),
        "confidence": payload.get("confidence"),
        "divergence": payload.get("divergence"),
        "crypto_gauge": (payload.get("crypto") or {}).get("gauge"),
        "persisted": (payload.get("persisted") or {}).get("as_of"),
    }


def run_signals(context: dict) -> dict:
    from src.services.research_service import ResearchService

    payload = ResearchService().run_signal_scan(run_type="daily")
    candidates = payload.get("candidates") or []
    scored = [row for row in candidates if row.get("candidate_score") is not None]
    diagnostics = payload.get("diagnostics") or []
    return {
        "run_id": payload.get("id"),
        "candidates": len(candidates),
        "scored": len(scored),
        "unscored": len(candidates) - len(scored),
        "diagnostics": len(diagnostics),
        "diagnostics_sample": [str(d) for d in diagnostics[:3]],
    }


def run_discovery(context: dict) -> dict:
    from src.services.discovery_service import DiscoveryService

    service = DiscoveryService()
    payload = service.run_daily_snapshot()
    top = [row["symbol"] for row in (payload.get("constituents") or [])[:3]]
    result = {
        "as_of": payload.get("as_of"),
        "universe_size": payload.get("universe_size"),
        "qualified_size": payload.get("qualified_size"),
        "top3": top,
        "warnings": len(payload.get("warnings") or []),
        "persisted": (payload.get("persisted") or {}).get("as_of"),
    }
    # Down-day relative-strength screen: zero extra fetches (cached SPY bars,
    # the persisted rotation snapshot, the stock_daily cache). Fail-open: its
    # failure must not fail the discovery step output.
    try:
        down = service.run_down_day_screen(discovery_payload=payload)
        result["down_day"] = {
            "as_of": down.get("as_of"),
            "spy_return_pct": down.get("spy_return_pct"),
            "triggered": down.get("triggered"),
            "stocks_holding_up": len(down.get("stocks_holding_up") or []),
            "persisted": (down.get("persisted") or {}).get("as_of"),
        }
    except Exception as exc:
        logger.warning("down-day screen failed (discovery snapshot unaffected): %s", exc)
        result["down_day"] = {"status": "failed", "error": str(exc)}
    return result


def run_scorecard(context: dict) -> dict:
    from src.services.scorecard_service import ScorecardService

    return ScorecardService().run_daily_evaluation()


def run_analysis(context: dict) -> dict:
    from analyzer_service import analyze_stocks
    from src.services.watchlist_service import WatchlistService

    watchlist = WatchlistService().get_symbols()
    if not watchlist:
        return {"skipped": "watchlist is empty (no symbols in DB and RESEARCH_US_WATCHLIST not set)"}
    # No notifier: reports are saved to history (keeps the Home page current);
    # the notify step's digest is the only message sent.
    results = analyze_stocks(watchlist)
    return {
        "requested": len(watchlist),
        "analyzed": len(results),
        "decisions": {r.code: getattr(r, "decision_type", None) for r in results},
    }


def run_news(context: dict) -> dict:
    from src.services.news_impact_service import NewsImpactService

    # Fail-open inside the service: a scoring failure leaves rows unscored
    # and never blocks the snapshot.
    return NewsImpactService().run_daily_scoring()


def run_portfolio(context: dict) -> dict:
    from src.services.paper_trading_service import PaperTradingService

    result = PaperTradingService().run_daily()
    context["paper_result"] = result
    return {
        "opened": len(result.get("opened") or []),
        "closed": len(result.get("closed") or []),
        "open_positions": len(result.get("open_positions") or []),
        "equity": (result.get("snapshot") or {}).get("total_equity"),
    }


def run_backtest(context: dict) -> dict:
    from datetime import datetime, timedelta

    from sqlalchemy import delete, select

    from src.services.backtest_service import BacktestService
    from src.storage import AnalysisHistory, BacktestResult, DatabaseManager

    eval_window_days = 10
    min_age_days = 14  # an analysis needs ~10 trading days of forward bars to grade

    # An insufficient_data verdict permanently blocks re-evaluation; purge verdicts
    # that were recorded before the analysis was old enough so they re-grade now.
    purged = 0
    db = DatabaseManager.get_instance()
    cutoff = datetime.now() - timedelta(days=min_age_days)
    with db.get_session() as session:
        stale_ids = session.execute(
            select(BacktestResult.id)
            .join(AnalysisHistory, AnalysisHistory.id == BacktestResult.analysis_history_id)
            .where(
                BacktestResult.eval_status == "insufficient_data",
                AnalysisHistory.created_at <= cutoff,
            )
        ).scalars().all()
        if stale_ids:
            session.execute(delete(BacktestResult).where(BacktestResult.id.in_(stale_ids)))
            session.commit()
            purged = len(stale_ids)

    result = BacktestService().run_backtest(min_age_days=min_age_days, eval_window_days=eval_window_days)
    return {
        "processed": result.get("processed"),
        "completed": result.get("completed"),
        "insufficient": result.get("insufficient"),
        "errors": result.get("errors"),
        "repurged_insufficient": purged,
    }


def run_notify(context: dict) -> dict:
    from src.services.daily_digest import DailyDigestService

    digest = DailyDigestService()
    paper_result = context.get("paper_result")
    alerts = digest.send_threshold_alerts(paper_result=paper_result)
    sent = False
    if context.get("send_digest", True):
        sent = digest.send_daily_digest(paper_result=paper_result)
    return {
        "digest_sent": sent,
        "alerts": alerts,
        "channel_results": dict(getattr(digest.notifier, "last_send_results", {}) or {}),
    }


STEP_RUNNERS = {
    "breadth": run_breadth,
    "regime": run_regime,
    "rotation": run_rotation,
    "cycle": run_cycle,
    "signals": run_signals,
    "discovery": run_discovery,
    "scorecard": run_scorecard,
    "analysis": run_analysis,
    "news": run_news,
    "portfolio": run_portfolio,
    "backtest": run_backtest,
    "notify": run_notify,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the DSA daily market snapshot.")
    parser.add_argument(
        "--steps",
        default=",".join(ALL_STEPS),
        help=f"Comma-separated subset of: {','.join(ALL_STEPS)}",
    )
    parser.add_argument(
        "--alerts-only",
        action="store_true",
        help="In the notify step, send only threshold alerts (no full digest). For intraday checks.",
    )
    args = parser.parse_args()

    steps = [step.strip() for step in args.steps.split(",") if step.strip()]
    unknown = [step for step in steps if step not in STEP_RUNNERS]
    if unknown:
        parser.error(f"unknown steps: {unknown}")

    setup_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    context: dict = {"send_digest": not args.alerts_only}
    summary: dict = {}
    failures = 0
    for step in steps:
        logger.info("=== step: %s ===", step)
        try:
            summary[step] = STEP_RUNNERS[step](context)
            logger.info("step %s ok: %s", step, summary[step])
        except Exception as exc:
            failures += 1
            summary[step] = {"status": "failed", "error": str(exc)}
            logger.exception("step %s failed: %s", step, exc)

    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 1 if failures == len(steps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
