# -*- coding: utf-8 -*-
"""Research signal API schemas."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ResearchMentionItem(BaseModel):
    id: int
    asset_symbol: str
    asset_type: str
    direction: str
    time_horizon: Optional[str] = None
    confidence: Optional[float] = None
    catalyst_tags: List[str] = Field(default_factory=list)
    extracted_text: Optional[str] = None
    created_at: Optional[str] = None


class ResearchIdeaItem(BaseModel):
    id: int
    source_id: Optional[int] = None
    source_type: str
    external_id: str
    url: Optional[str] = None
    title: Optional[str] = None
    content: str
    published_at: Optional[str] = None
    bookmarked_at: Optional[str] = None
    created_at: Optional[str] = None
    mentions: List[ResearchMentionItem] = Field(default_factory=list)


class ResearchIdeasResponse(BaseModel):
    items: List[ResearchIdeaItem] = Field(default_factory=list)


class CreateResearchIdeaRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=20000)
    title: Optional[str] = Field(None, max_length=300)
    source_type: str = Field("manual", max_length=32)


class XStatusResponse(BaseModel):
    configured: bool
    connected: bool
    account_count: int
    last_sync_at: Optional[str] = None
    accounts: List[Dict[str, Any]] = Field(default_factory=list)


class XOAuthStartResponse(BaseModel):
    configured: bool
    auth_url: str = ""
    state: Optional[str] = None
    message: Optional[str] = None


class XOAuthCallbackResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    message: Optional[str] = None
    account: Optional[Dict[str, Any]] = None


class XSyncRequest(BaseModel):
    account_id: Optional[int] = None
    max_pages: int = Field(3, ge=1, le=10)


class XSyncResponse(BaseModel):
    success: bool
    status: str
    imported: int = 0
    mentions: int = 0
    pages: int = 0
    next_token: Optional[str] = None


class WatchlistSymbolItem(BaseModel):
    symbol: str
    note: Optional[str] = None
    source: str
    created_at: Optional[str] = None


class WatchlistResponse(BaseModel):
    items: List[WatchlistSymbolItem] = Field(default_factory=list)


class AddWatchlistRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16, pattern=r"^[A-Za-z0-9.\-]{1,16}$")
    note: Optional[str] = Field(None, max_length=256)


class SignalRunRequest(BaseModel):
    symbols: Optional[List[str]] = None
    crypto_symbols: Optional[List[str]] = None
    include_us: bool = True
    include_crypto: bool = True
    run_type: str = Field("weekly", max_length=32)


class SignalCandidateItem(BaseModel):
    id: int
    symbol: str
    asset_type: str
    name: Optional[str] = None
    # None = unscored (insufficient data), distinct from a real 0.
    candidate_score: Optional[float] = None
    checklist_status: str
    entry_zone: Optional[str] = None
    invalidation: Optional[str] = None
    risk_reward: Optional[str] = None
    source_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    why_not_higher: Optional[str] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class RotationMemoItem(BaseModel):
    id: int
    signal_run_id: Optional[int] = None
    title: str
    summary: str
    themes: List[Dict[str, Any]] = Field(default_factory=list)
    generated_at: Optional[str] = None
    created_at: Optional[str] = None


class SignalRunResponse(BaseModel):
    id: int
    run_type: str
    status: str
    universe: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    diagnostics: List[str] = Field(default_factory=list)
    candidates: List[SignalCandidateItem] = Field(default_factory=list)
    rotation_memo: Optional[RotationMemoItem] = None


class LatestRotationMemoResponse(BaseModel):
    memo: Optional[RotationMemoItem] = None


class PriceHistoryBar(BaseModel):
    date: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    volume: float = 0


class PriceHistoryResponse(BaseModel):
    symbol: str
    asset_type: str
    asset_subtype: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    data_quality: Dict[str, Any] = Field(default_factory=dict)
    data_warnings: List[str] = Field(default_factory=list)
    market_cap: Optional[float] = None
    bars: List[PriceHistoryBar] = Field(default_factory=list)
    error: Optional[str] = None


class PositioningResponse(BaseModel):
    symbol: str
    asset_type: str
    as_of: str
    underlying_price: Optional[float] = None
    positioning_bias: str
    confidence: Dict[str, Any] = Field(default_factory=dict)
    gamma: Dict[str, Any] = Field(default_factory=dict)
    crowding: Dict[str, Any] = Field(default_factory=dict)
    short_pressure: Dict[str, Any] = Field(default_factory=dict)
    cot_macro_context: Dict[str, Any] = Field(default_factory=dict)
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    data_gaps: List[str] = Field(default_factory=list)
    what_to_watch: List[str] = Field(default_factory=list)
    methodology: List[str] = Field(default_factory=list)


class FreeDataSourceStatusItem(BaseModel):
    name: str
    category: str
    status: str
    quality: str
    cost: str
    latency: str
    coverage: str
    caveat: str
    last_checked_at: str
    detail: Optional[str] = None
    sample: Dict[str, Any] = Field(default_factory=dict)
    best_for: List[str] = Field(default_factory=list)
    limits: List[str] = Field(default_factory=list)
    refresh: str = ""
    decision_use: str = ""


class FreeDataStatusResponse(BaseModel):
    generated_at: str
    ok_count: int
    degraded_count: int
    summary: str
    cache_policy: Optional[str] = None
    cache: Dict[str, Any] = Field(default_factory=dict)
    sources: List[FreeDataSourceStatusItem] = Field(default_factory=list)


class FreeDataUniverseResponse(BaseModel):
    source: str
    generated_at: Optional[str] = None
    total: int
    stocks: int
    etfs: int
    symbols: List[str] = Field(default_factory=list)
    sample: List[Dict[str, Any]] = Field(default_factory=list)
    caveat: str
    cache: Dict[str, Any] = Field(default_factory=dict)


class FreeDataMacroResponse(BaseModel):
    source: str
    generated_at: str
    series: List[Dict[str, Any]] = Field(default_factory=list)
    caveat: str
    cache: Dict[str, Any] = Field(default_factory=dict)


class MarketRegimeResponse(BaseModel):
    generated_at: str
    as_of: Optional[str] = None
    regime: str
    score: float
    confidence: str
    summary: str
    components: List[Dict[str, Any]] = Field(default_factory=list)
    index_trends: List[Dict[str, Any]] = Field(default_factory=list)
    risk_ratios: List[Dict[str, Any]] = Field(default_factory=list)
    sector_breadth: Dict[str, Any] = Field(default_factory=dict)
    market_breadth: Dict[str, Any] = Field(default_factory=dict)
    macro: Dict[str, Any] = Field(default_factory=dict)
    volatility: Dict[str, Any] = Field(default_factory=dict)
    breadth_status: Dict[str, Any] = Field(default_factory=dict)
    data_sources: List[Dict[str, Any]] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)
    cache: Dict[str, Any] = Field(default_factory=dict)


class MarketBreadthRunRequest(BaseModel):
    universe: str = Field("us_etf_proxy", pattern="^(us_etf_proxy|us_stocks|us_etfs|us_listed)$")
    limit: Optional[int] = Field(None, ge=1, le=5000)
    min_price: Optional[float] = Field(None, ge=0)
    min_avg_dollar_volume: Optional[float] = Field(None, ge=0)


class MarketBreadthResponse(BaseModel):
    id: Optional[int] = None
    status: str
    source: Optional[str] = None
    generated_at: Optional[str] = None
    as_of: Optional[str] = None
    universe: str
    universe_label: Optional[str] = None
    universe_source: Optional[str] = None
    total_available: Optional[int] = None
    freshness: Optional[str] = None
    age_days: Optional[int] = None
    summary: Optional[str] = None
    symbols_requested: int = 0
    symbols_scanned: int = 0
    symbols_with_data: int = 0
    symbols_passing_liquidity: int = 0
    min_price: Optional[float] = None
    min_avg_dollar_volume: Optional[float] = None
    above_sma20_count: int = 0
    above_sma20_pct: Optional[float] = None
    above_sma50_count: int = 0
    above_sma50_pct: Optional[float] = None
    above_sma200_count: int = 0
    above_sma200_pct: Optional[float] = None
    new_high_52w_count: int = 0
    new_high_52w_pct: Optional[float] = None
    new_low_52w_count: int = 0
    new_low_52w_pct: Optional[float] = None
    advancers_count: int = 0
    decliners_count: int = 0
    up_volume: Optional[float] = None
    down_volume: Optional[float] = None
    source_counts: Dict[str, int] = Field(default_factory=dict)
    calculation_steps: List[Dict[str, Any]] = Field(default_factory=list)
    sample_constituents: List[Dict[str, Any]] = Field(default_factory=list)
    failures: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    cache: Dict[str, Any] = Field(default_factory=dict)


class FreeDataFundamentalsResponse(BaseModel):
    symbol: str
    source: str
    status: str
    cik: Optional[int] = None
    entity_name: Optional[str] = None
    generated_at: Optional[str] = None
    metrics: List[Dict[str, Any]] = Field(default_factory=list)
    caveat: str
    cache: Dict[str, Any] = Field(default_factory=dict)


class FreeDataShortVolumeResponse(BaseModel):
    symbol: str
    source: str
    status: str
    date: Optional[str] = None
    short_volume: Optional[float] = None
    short_exempt_volume: Optional[float] = None
    total_volume: Optional[float] = None
    short_volume_ratio: Optional[float] = None
    market: Optional[str] = None
    diagnostics: List[str] = Field(default_factory=list)
    caveat: str
    cache: Dict[str, Any] = Field(default_factory=dict)
