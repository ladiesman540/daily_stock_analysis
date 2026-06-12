# -*- coding: utf-8 -*-
"""Research and signal analyst endpoints."""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.schemas.research import (
    AddWatchlistRequest,
    CreateResearchIdeaRequest,
    FreeDataFundamentalsResponse,
    FreeDataMacroResponse,
    FreeDataShortVolumeResponse,
    FreeDataStatusResponse,
    FreeDataUniverseResponse,
    LatestRotationMemoResponse,
    MarketBreadthResponse,
    MarketBreadthRunRequest,
    MarketRegimeResponse,
    PositioningResponse,
    PriceHistoryResponse,
    ResearchIdeaItem,
    ResearchIdeasResponse,
    SignalRunRequest,
    SignalRunResponse,
    WatchlistResponse,
    WatchlistSymbolItem,
    XOAuthCallbackResponse,
    XOAuthStartResponse,
    XStatusResponse,
    XSyncRequest,
    XSyncResponse,
)
from src.services.free_data_service import FreeDataService
from src.services.positioning_service import PositioningService
from src.services.research_market_data import ResearchMarketDataService
from src.services.research_service import ResearchService

logger = logging.getLogger(__name__)

router = APIRouter()


def _service() -> ResearchService:
    return ResearchService()


def _free_data_service() -> FreeDataService:
    return FreeDataService()


@router.get("/x/status", response_model=XStatusResponse)
def get_x_status() -> XStatusResponse:
    return XStatusResponse(**_service().x_status())


@router.post("/x/oauth/start", response_model=XOAuthStartResponse)
def start_x_oauth() -> XOAuthStartResponse:
    return XOAuthStartResponse(**_service().start_x_oauth())


@router.get("/x/oauth/callback", response_model=XOAuthCallbackResponse)
def complete_x_oauth(
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
) -> XOAuthCallbackResponse:
    try:
        return XOAuthCallbackResponse(**_service().complete_x_oauth(code=code, state=state))
    except Exception as exc:
        logger.error("X OAuth callback failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "x_oauth_failed", "message": str(exc)})


@router.post("/x/sync", response_model=XSyncResponse)
def sync_x_bookmarks(request: XSyncRequest) -> XSyncResponse:
    try:
        return XSyncResponse(**_service().sync_x_bookmarks(account_id=request.account_id, max_pages=request.max_pages))
    except Exception as exc:
        logger.error("X bookmark sync failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "x_sync_failed", "message": str(exc)})


@router.get("/ideas", response_model=ResearchIdeasResponse)
def list_ideas(
    limit: int = Query(50, ge=1, le=200),
    asset_type: Optional[str] = Query(None, pattern="^(stock|crypto)$"),
    symbol: Optional[str] = Query(None, min_length=1, max_length=32),
) -> ResearchIdeasResponse:
    return ResearchIdeasResponse(items=[ResearchIdeaItem(**item) for item in _service().list_ideas(
        limit=limit,
        asset_type=asset_type,
        symbol=symbol,
    )])


@router.post("/ideas", response_model=ResearchIdeaItem)
def create_idea(request: CreateResearchIdeaRequest) -> ResearchIdeaItem:
    try:
        return ResearchIdeaItem(**_service().create_idea(
            content=request.content,
            title=request.title,
            source_type=request.source_type,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})


@router.post("/signal-runs", response_model=SignalRunResponse)
def create_signal_run(request: SignalRunRequest) -> SignalRunResponse:
    try:
        return SignalRunResponse(**_service().run_signal_scan(
            symbols=request.symbols,
            crypto_symbols=request.crypto_symbols,
            include_us=request.include_us,
            include_crypto=request.include_crypto,
            run_type=request.run_type,
        ))
    except Exception as exc:
        logger.error("Signal scan failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "signal_scan_failed", "message": str(exc)})


@router.get("/signal-runs/{run_id}", response_model=SignalRunResponse)
def get_signal_run(run_id: int) -> SignalRunResponse:
    try:
        return SignalRunResponse(**_service().get_signal_run(run_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": str(exc)})


@router.get("/rotation-memos/latest", response_model=LatestRotationMemoResponse)
def get_latest_rotation_memo() -> LatestRotationMemoResponse:
    return LatestRotationMemoResponse(memo=_service().latest_rotation_memo())


@router.get("/price-history/{symbol}", response_model=PriceHistoryResponse)
def get_price_history(
    symbol: str,
    asset_type: str = Query("stock", pattern="^(stock|crypto)$"),
    days: int = Query(260, ge=30, le=520),
) -> PriceHistoryResponse:
    try:
        service = ResearchMarketDataService()
        payload = (
            service.get_crypto_history(symbol, days=days)
            if asset_type == "crypto"
            else service.get_us_equity_history(symbol, days=days)
        )
        payload.setdefault("asset_type", asset_type)
        return PriceHistoryResponse(**payload)
    except Exception as exc:
        logger.error("Price history fetch failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "price_history_failed", "message": str(exc)})


@router.get("/positioning/{symbol}", response_model=PositioningResponse)
def get_positioning(
    symbol: str,
    asset_type: str = Query("stock", pattern="^(stock|crypto)$"),
) -> PositioningResponse:
    try:
        return PositioningResponse(**PositioningService().analyze(symbol, asset_type=asset_type))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})
    except Exception as exc:
        logger.error("Positioning analysis failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "positioning_failed", "message": str(exc)})


@router.get("/free-data/status", response_model=FreeDataStatusResponse)
def get_free_data_status() -> FreeDataStatusResponse:
    try:
        return FreeDataStatusResponse(**_free_data_service().get_status())
    except Exception as exc:
        logger.error("Free data status failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "free_data_status_failed", "message": str(exc)})


@router.get("/free-data/universe", response_model=FreeDataUniverseResponse)
def get_free_data_universe(limit: int = Query(5000, ge=1, le=10000)) -> FreeDataUniverseResponse:
    try:
        return FreeDataUniverseResponse(**_free_data_service().get_us_universe(limit=limit))
    except Exception as exc:
        logger.error("Free data universe failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "free_data_universe_failed", "message": str(exc)})


@router.get("/free-data/macro", response_model=FreeDataMacroResponse)
def get_free_data_macro() -> FreeDataMacroResponse:
    try:
        return FreeDataMacroResponse(**_free_data_service().get_macro_snapshot())
    except Exception as exc:
        logger.error("Free data macro failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "free_data_macro_failed", "message": str(exc)})


@router.get("/free-data/market-regime", response_model=MarketRegimeResponse)
def get_market_regime() -> MarketRegimeResponse:
    try:
        return MarketRegimeResponse(**_free_data_service().get_market_regime())
    except Exception as exc:
        logger.error("Market regime failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "market_regime_failed", "message": str(exc)})


@router.get("/free-data/rotation/latest")
def get_rotation_latest() -> Dict[str, Any]:
    try:
        from src.storage import DatabaseManager

        snapshot = DatabaseManager.get_instance().get_latest_sector_rotation_snapshot()
        if not snapshot:
            return {
                "status": "missing",
                "summary": "No rotation snapshot persisted yet. Run the daily snapshot job (rotation step).",
            }
        return {"status": "completed", **snapshot}
    except Exception as exc:
        logger.error("Rotation latest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "rotation_latest_failed", "message": str(exc)})


@router.get("/free-data/rotation/history")
def get_rotation_history(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    try:
        from src.storage import DatabaseManager

        history = DatabaseManager.get_instance().get_sector_rotation_history(days=days)
        slim = [
            {
                "as_of": row.get("as_of"),
                "symbols_ranked": row.get("symbols_ranked"),
                "ranks": {
                    str(item.get("symbol")): item.get("composite_rank")
                    for item in row.get("constituents") or []
                    if item.get("composite_rank") is not None
                },
            }
            for row in history
        ]
        return {"days": days, "count": len(slim), "snapshots": slim}
    except Exception as exc:
        logger.error("Rotation history failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "rotation_history_failed", "message": str(exc)})


@router.post("/free-data/rotation/run")
def run_rotation_snapshot() -> Dict[str, Any]:
    try:
        from src.services.rotation_service import RotationService

        payload = RotationService().run_daily_snapshot()
        return {"status": "completed", **payload}
    except Exception as exc:
        logger.error("Rotation run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "rotation_run_failed", "message": str(exc)})


@router.get("/free-data/discovery/latest")
def get_discovery_latest() -> Dict[str, Any]:
    try:
        from src.storage import DatabaseManager

        snapshot = DatabaseManager.get_instance().get_latest_discovery_snapshot()
        if not snapshot:
            return {
                "status": "missing",
                "summary": "No discovery snapshot persisted yet. Run the daily snapshot job (discovery step).",
            }
        return {"status": "completed", **snapshot}
    except Exception as exc:
        logger.error("Discovery latest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "discovery_latest_failed", "message": str(exc)})


@router.post("/free-data/discovery/run")
def run_discovery_snapshot() -> Dict[str, Any]:
    try:
        from src.services.discovery_service import DiscoveryService

        payload = DiscoveryService().run_daily_snapshot()
        return {"status": "completed", **payload}
    except Exception as exc:
        logger.error("Discovery run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "discovery_run_failed", "message": str(exc)})


@router.get("/free-data/down-day-rs/latest")
def get_down_day_rs_latest() -> Dict[str, Any]:
    try:
        from src.storage import DatabaseManager

        snapshot = DatabaseManager.get_instance().get_latest_down_day_rs_snapshot()
        if not snapshot:
            return {
                "status": "missing",
                "summary": "No down-day snapshot persisted yet. Run the daily snapshot job (discovery step).",
            }
        return {"status": "completed", **snapshot}
    except Exception as exc:
        logger.error("Down-day RS latest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "down_day_rs_latest_failed", "message": str(exc)})


@router.get("/scorecard")
def get_scorecard(
    days: int = Query(400, ge=1, le=1000),
    include_simulated: bool = Query(True),
) -> Dict[str, Any]:
    """Hit-rate scorecard summary; real and simulated flag stats are never merged.

    Each population block splits into "flagged" (headline: passed >=1 screen or
    made the scored top-40) and "baseline" (all liquidity-qualified rows, the
    control group), plus an "edge" delta between their batting averages.
    """
    try:
        from src.services.scorecard_service import ScorecardService

        summary = ScorecardService().build_summary(days=days, include_simulated=include_simulated)
        real_total = ((summary.get("real") or {}).get("baseline") or {}).get("flags") or 0
        sim_total = ((summary.get("simulated") or {}).get("baseline") or {}).get("flags") or 0
        if not real_total and not sim_total:
            return {
                "status": "missing",
                "summary": "No discovery flags graded yet. Run the daily snapshot job (scorecard step).",
            }
        return {"status": "completed", **summary}
    except Exception as exc:
        logger.error("Scorecard summary failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "scorecard_failed", "message": str(exc)})


@router.get("/scorecard/flags")
def get_scorecard_flags(
    status: Optional[str] = Query(None, pattern="^(open|hit|flop|expired)$"),
    simulated: Optional[bool] = Query(None),
    screen: Optional[str] = Query(None, min_length=1, max_length=64, pattern="^[a-z0-9_]+$"),
    days: int = Query(400, ge=1, le=1000),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Filtered scorecard flag rows, newest first, with pagination."""
    try:
        from src.storage import DatabaseManager

        payload = DatabaseManager.get_instance().get_discovery_flag_outcomes(
            status=status, simulated=simulated, screen=screen,
            days=days, limit=limit, offset=offset,
        )
        return {"days": days, "limit": limit, "offset": offset, **payload}
    except Exception as exc:
        logger.error("Scorecard flags failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "scorecard_flags_failed", "message": str(exc)})


@router.post("/scorecard/run")
def run_scorecard_evaluation() -> Dict[str, Any]:
    """Seed + evaluate discovery flags now (same work as the pipeline scorecard step)."""
    try:
        from src.services.scorecard_service import ScorecardService

        return {"status": "completed", **ScorecardService().run_daily_evaluation()}
    except Exception as exc:
        logger.error("Scorecard run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "scorecard_run_failed", "message": str(exc)})


# One bootstrap at a time per process: acquired by the request thread, released
# by the worker thread when the (10-15 min) simulation finishes or fails.
_bootstrap_lock = threading.Lock()


@router.post("/scorecard/bootstrap")
def run_scorecard_bootstrap() -> Dict[str, Any]:
    """Kick off the one-time simulated-history bootstrap in a background thread.

    Returns immediately: {"status": "started"} or {"status": "already_running"}.
    Results land in discovery_flag_outcomes with simulated=True; progress and
    failures are logged (fail-open, nothing else is affected).
    """
    if not _bootstrap_lock.acquire(blocking=False):
        return {"status": "already_running"}

    def _worker() -> None:
        try:
            from src.services.scorecard_service import ScorecardService

            result = ScorecardService().run_bootstrap()
            logger.info("Scorecard bootstrap finished: %s", result)
        except Exception as exc:
            logger.error("Scorecard bootstrap failed: %s", exc, exc_info=True)
        finally:
            _bootstrap_lock.release()

    try:
        threading.Thread(target=_worker, name="scorecard-bootstrap", daemon=True).start()
    except Exception:
        # Thread never started, so _worker's finally can't release the lock.
        _bootstrap_lock.release()
        raise
    return {"status": "started"}


@router.get("/free-data/cycle/latest")
def get_cycle_latest() -> Dict[str, Any]:
    try:
        from src.storage import DatabaseManager

        snapshot = DatabaseManager.get_instance().get_latest_macro_cycle_snapshot()
        if not snapshot:
            return {
                "status": "missing",
                "summary": "No macro cycle snapshot persisted yet. Run the daily snapshot job (cycle step).",
            }
        return {"status": "completed", **snapshot}
    except Exception as exc:
        logger.error("Cycle latest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "cycle_latest_failed", "message": str(exc)})


@router.post("/free-data/cycle/run")
def run_cycle_snapshot() -> Dict[str, Any]:
    try:
        from src.services.macro_cycle_service import MacroCycleService

        payload = MacroCycleService().run_daily_snapshot()
        return {"status": "completed", **payload}
    except Exception as exc:
        logger.error("Cycle run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "cycle_run_failed", "message": str(exc)})


@router.get("/free-data/daily-brief")
def get_daily_brief() -> Dict[str, Any]:
    """Aggregate regime/rotation/cycle/signals brief shared with the Telegram digest."""
    try:
        from src.services.daily_digest import DailyDigestService

        return DailyDigestService().collect_brief()
    except Exception as exc:
        logger.error("Daily brief failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "daily_brief_failed", "message": str(exc)})


@router.get("/free-data/market-regime/history")
def get_market_regime_history(days: int = Query(90, ge=1, le=365)) -> Dict[str, Any]:
    try:
        from src.storage import DatabaseManager

        history = DatabaseManager.get_instance().get_market_regime_history(days=days)
        return {"days": days, "count": len(history), "snapshots": history}
    except Exception as exc:
        logger.error("Market regime history failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "market_regime_history_failed", "message": str(exc)})


@router.get("/free-data/sparklines")
def get_free_data_sparklines(
    symbols: str = Query(..., min_length=1, description="Comma-separated symbols, capped at 60"),
    days: int = Query(30, description="Lookback window in calendar days, clamped to [5, 90]"),
) -> Dict[str, Any]:
    """Batch close-price sparklines from the stock_daily cache only (no network)."""
    try:
        from src.storage import DatabaseManager

        days = max(5, min(90, days))
        parsed: List[str] = []
        for token in symbols.split(","):
            symbol = token.strip().upper()
            if symbol and symbol not in parsed:
                parsed.append(symbol)
        parsed = parsed[:60]
        db = DatabaseManager.get_instance()
        end = date.today()
        start = end - timedelta(days=days)
        series: Dict[str, List[Dict[str, Any]]] = {}
        for symbol in parsed:
            try:
                rows = db.get_data_range(symbol, start, end)
            except Exception as exc:
                logger.warning("Sparkline read failed for %s: %s", symbol, exc)
                continue
            points = [
                {"date": row.date.isoformat(), "close": row.close}
                for row in rows
                if row.date is not None and row.close is not None
            ]
            if points:
                series[symbol] = points
        return {"days": days, "series": series}
    except Exception as exc:
        logger.error("Sparklines failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "sparklines_failed", "message": str(exc)})


# Nasdaq directory security names end in " - <security type>" boilerplate
# ("AmpliTech Group, Inc. - Common Stock"); strip the tail only when it starts
# with one of these well-known phrases.
_SECURITY_TYPE_PREFIXES = (
    "common stock",
    "common shares",
    "ordinary share",
    "american depositary",
    "depositary share",
    "ads",
    "adr",
    "class a",
    "class b",
    "class c",
    "warrant",
    "unit",
    "etf",
)


# Trailing type phrases that appear without the " - " separator
# ("Voyager Technologies, Inc. Class A Common Stock").
_SECURITY_TYPE_TAIL_RE = re.compile(
    r"\s+(?:class\s+[a-c]\s+)?(?:common\s+(?:stock|shares)|ordinary\s+shares?|"
    r"american\s+depositary\s+shares?|depositary\s+shares?|warrants?|units?)\s*$",
    re.IGNORECASE,
)


def _clean_security_name(name: str) -> str:
    base, sep, tail = name.rpartition(" - ")
    if sep and base.strip() and tail.strip().lower().startswith(_SECURITY_TYPE_PREFIXES):
        name = base
    name = _SECURITY_TYPE_TAIL_RE.sub("", name).strip()
    # Directory rows sometimes double the issuer ("Cinemark Holdings Inc
    # Cinemark Holdings, Inc."); collapse when the two halves match modulo
    # punctuation.
    tokens = name.split()
    if len(tokens) >= 4 and len(tokens) % 2 == 0:
        half = len(tokens) // 2
        normalize = lambda ts: [t.strip(".,").lower() for t in ts]  # noqa: E731
        if normalize(tokens[:half]) == normalize(tokens[half:]):
            name = " ".join(tokens[half:])
    return name.strip()


def _rotation_label_map() -> Dict[str, str]:
    """ETF display labels from the rotation universe ("Semis (SOXX)" → "Semis")."""
    from src.services.rotation_service import ROTATION_GROUPS

    labels: Dict[str, str] = {}
    for members in ROTATION_GROUPS.values():
        for symbol, label in members:
            cleaned = re.sub(r"\s*\(" + re.escape(symbol) + r"\)\s*$", "", label).strip()
            labels[symbol] = cleaned or label
    return labels


@router.get("/free-data/symbol-names")
def get_free_data_symbol_names(
    symbols: str = Query(..., min_length=1, description="Comma-separated symbols, capped at 100"),
) -> Dict[str, Any]:
    """Batch symbol → display name: rotation ETF labels first, then the cached
    Nasdaq directory (~12h TTL). Unknown symbols are omitted; per-symbol and
    directory failures are swallowed so the endpoint fails open."""
    try:
        parsed: List[str] = []
        for token in symbols.split(","):
            symbol = token.strip().upper()
            if symbol and symbol not in parsed:
                parsed.append(symbol)
        parsed = parsed[:100]

        names: Dict[str, str] = {}
        rotation_labels = _rotation_label_map()
        unresolved: List[str] = []
        for symbol in parsed:
            label = rotation_labels.get(symbol)
            if label:
                names[symbol] = label
            else:
                unresolved.append(symbol)

        if unresolved:
            directory: Dict[str, Any] = {}
            try:
                directory = _free_data_service().get_directory_names().get("names") or {}
            except Exception as exc:
                logger.warning("Symbol-names directory unavailable: %s", exc)
            for symbol in unresolved:
                try:
                    raw = directory.get(symbol)
                    if not raw:
                        continue
                    cleaned = _clean_security_name(str(raw))
                    if cleaned:
                        names[symbol] = cleaned
                except Exception as exc:
                    logger.warning("Symbol-name resolution failed for %s: %s", symbol, exc)
                    continue
        return {"names": names}
    except Exception as exc:
        logger.error("Symbol names failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "symbol_names_failed", "message": str(exc)})


@router.get("/free-data/market-breadth/latest", response_model=MarketBreadthResponse)
def get_latest_market_breadth(
    universe: str = Query("us_etf_proxy", pattern="^(us_etf_proxy|us_stocks|us_etfs|us_listed)$"),
) -> MarketBreadthResponse:
    try:
        return MarketBreadthResponse(**_free_data_service().get_latest_market_breadth(universe=universe))
    except Exception as exc:
        logger.error("Market breadth latest failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "market_breadth_latest_failed", "message": str(exc)})


@router.post("/free-data/market-breadth/run", response_model=MarketBreadthResponse)
def run_market_breadth_cache(request: MarketBreadthRunRequest) -> MarketBreadthResponse:
    try:
        return MarketBreadthResponse(**_free_data_service().run_market_breadth_cache(
            universe=request.universe,
            limit=request.limit,
            min_price=request.min_price,
            min_avg_dollar_volume=request.min_avg_dollar_volume,
        ))
    except Exception as exc:
        logger.error("Market breadth cache run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "market_breadth_run_failed", "message": str(exc)})


@router.get("/free-data/fundamentals/{symbol}", response_model=FreeDataFundamentalsResponse)
def get_free_data_fundamentals(symbol: str) -> FreeDataFundamentalsResponse:
    try:
        return FreeDataFundamentalsResponse(**_free_data_service().get_fundamentals(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})
    except Exception as exc:
        logger.error("Free data fundamentals failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "free_data_fundamentals_failed", "message": str(exc)})


@router.get("/free-data/short-volume/{symbol}", response_model=FreeDataShortVolumeResponse)
def get_free_data_short_volume(symbol: str) -> FreeDataShortVolumeResponse:
    try:
        return FreeDataShortVolumeResponse(**_free_data_service().get_finra_short_volume(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "message": str(exc)})
    except Exception as exc:
        logger.error("Free data short volume failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "free_data_short_volume_failed", "message": str(exc)})


@router.get("/watchlist", response_model=WatchlistResponse)
def get_watchlist() -> WatchlistResponse:
    try:
        from src.services.watchlist_service import WatchlistService

        rows = WatchlistService().get_rows()
        return WatchlistResponse(items=[WatchlistSymbolItem(**row) for row in rows])
    except Exception as exc:
        logger.error("Get watchlist failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "watchlist_get_failed", "message": str(exc)})


@router.post("/watchlist", response_model=WatchlistSymbolItem)
def add_watchlist_symbol(request: AddWatchlistRequest) -> WatchlistSymbolItem:
    try:
        from src.services.watchlist_service import WatchlistService

        row = WatchlistService().add(request.symbol, note=request.note, source="manual")
        return WatchlistSymbolItem(**row)
    except Exception as exc:
        logger.error("Add watchlist symbol failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "watchlist_add_failed", "message": str(exc)})


@router.delete("/watchlist/{symbol}", response_model=Dict[str, Any])
def remove_watchlist_symbol(symbol: str) -> Dict[str, Any]:
    try:
        from src.services.watchlist_service import WatchlistService

        removed = WatchlistService().remove(symbol)
        return {"symbol": symbol.upper(), "removed": removed}
    except Exception as exc:
        logger.error("Remove watchlist symbol failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail={"error": "watchlist_remove_failed", "message": str(exc)})
