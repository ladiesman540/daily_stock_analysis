import apiClient from './index';

export interface ResearchMention {
  id: number;
  asset_symbol: string;
  asset_type: 'stock' | 'crypto';
  direction: string;
  time_horizon?: string | null;
  confidence?: number | null;
  catalyst_tags: string[];
  extracted_text?: string | null;
  created_at?: string | null;
}

export interface ResearchIdea {
  id: number;
  source_id?: number | null;
  source_type: string;
  external_id: string;
  url?: string | null;
  title?: string | null;
  content: string;
  published_at?: string | null;
  bookmarked_at?: string | null;
  created_at?: string | null;
  mentions: ResearchMention[];
}

export interface XStatus {
  configured: boolean;
  connected: boolean;
  account_count: number;
  last_sync_at?: string | null;
  accounts: Array<{
    id: number;
    provider_user_id: string;
    username?: string | null;
    display_name?: string | null;
    expires_at?: string | null;
    last_sync_at?: string | null;
  }>;
}

export interface SignalCandidate {
  id: number;
  symbol: string;
  asset_type: 'stock' | 'crypto';
  name?: string | null;
  candidate_score: number | null;
  checklist_status: 'pass' | 'watchlist' | 'reject' | string;
  entry_zone?: string | null;
  invalidation?: string | null;
  risk_reward?: string | null;
  source_evidence: Array<Record<string, unknown>>;
  why_not_higher?: string | null;
  metrics: Record<string, unknown>;
  created_at?: string | null;
}

export interface RotationMemo {
  id: number;
  signal_run_id?: number | null;
  title: string;
  summary: string;
  themes: Array<Record<string, unknown>>;
  generated_at?: string | null;
  created_at?: string | null;
}

export interface SignalRun {
  id: number;
  run_type: string;
  status: string;
  universe: string;
  started_at?: string | null;
  completed_at?: string | null;
  parameters: Record<string, unknown>;
  diagnostics: string[];
  candidates: SignalCandidate[];
  rotation_memo?: RotationMemo | null;
}

export interface SignalRunRequest {
  symbols?: string[];
  crypto_symbols?: string[];
  include_us?: boolean;
  include_crypto?: boolean;
  run_type?: string;
}

export interface PriceHistoryBar {
  date: string;
  open?: number | null;
  high?: number | null;
  low?: number | null;
  close?: number | null;
  volume: number;
}

export interface PriceHistoryResponse {
  symbol: string;
  asset_type: 'stock' | 'crypto';
  asset_subtype?: string | null;
  name?: string | null;
  source?: string | null;
  data_quality?: Record<string, unknown>;
  data_warnings?: string[];
  market_cap?: number | null;
  bars: PriceHistoryBar[];
  error?: string | null;
}

export interface PositioningResponse {
  symbol: string;
  asset_type: 'stock' | 'crypto';
  as_of: string;
  underlying_price?: number | null;
  positioning_bias: string;
  confidence: Record<string, unknown>;
  gamma: Record<string, unknown>;
  crowding: Record<string, unknown>;
  short_pressure: Record<string, unknown>;
  cot_macro_context: Record<string, unknown>;
  sources: Array<Record<string, unknown>>;
  data_gaps: string[];
  what_to_watch: string[];
  methodology: string[];
}

export interface FreeDataSourceStatus {
  name: string;
  category: string;
  status: string;
  quality: string;
  cost: string;
  latency: string;
  coverage: string;
  caveat: string;
  last_checked_at: string;
  detail?: string | null;
  sample: Record<string, unknown>;
  best_for: string[];
  limits: string[];
  refresh: string;
  decision_use: string;
}

export interface FreeDataStatusResponse {
  generated_at: string;
  ok_count: number;
  degraded_count: number;
  summary: string;
  cache_policy?: string | null;
  cache?: Record<string, unknown>;
  sources: FreeDataSourceStatus[];
}

export interface FreeDataUniverseResponse {
  source: string;
  generated_at?: string | null;
  total: number;
  stocks: number;
  etfs: number;
  symbols: string[];
  sample: Array<Record<string, unknown>>;
  caveat: string;
  cache?: Record<string, unknown>;
}

export interface FreeDataMacroResponse {
  source: string;
  generated_at: string;
  series: Array<Record<string, unknown>>;
  caveat: string;
  cache?: Record<string, unknown>;
}

export interface MarketRegimeResponse {
  generated_at: string;
  as_of?: string | null;
  regime: string;
  score: number;
  confidence: string;
  summary: string;
  components: Array<Record<string, unknown>>;
  index_trends: Array<Record<string, unknown>>;
  risk_ratios: Array<Record<string, unknown>>;
  sector_breadth: Record<string, unknown>;
  market_breadth: Record<string, unknown>;
  macro: Record<string, unknown>;
  breadth_status: Record<string, unknown>;
  data_sources: Array<Record<string, unknown>>;
  caveats: string[];
  cache?: Record<string, unknown>;
}

export interface MarketBreadthResponse {
  id?: number | null;
  status: string;
  source?: string | null;
  generated_at?: string | null;
  as_of?: string | null;
  universe: string;
  universe_label?: string | null;
  universe_source?: string | null;
  total_available?: number | null;
  freshness?: string | null;
  age_days?: number | null;
  summary?: string | null;
  symbols_requested: number;
  symbols_scanned: number;
  symbols_with_data: number;
  symbols_passing_liquidity: number;
  min_price?: number | null;
  min_avg_dollar_volume?: number | null;
  above_sma20_count: number;
  above_sma20_pct?: number | null;
  above_sma50_count: number;
  above_sma50_pct?: number | null;
  above_sma200_count: number;
  above_sma200_pct?: number | null;
  new_high_52w_count: number;
  new_high_52w_pct?: number | null;
  new_low_52w_count: number;
  new_low_52w_pct?: number | null;
  advancers_count: number;
  decliners_count: number;
  up_volume?: number | null;
  down_volume?: number | null;
  source_counts: Record<string, number>;
  calculation_steps: Array<Record<string, unknown>>;
  sample_constituents: Array<Record<string, unknown>>;
  failures: Array<Record<string, unknown>>;
  warnings: string[];
  cache?: Record<string, unknown>;
}

export interface FreeDataFundamentalsResponse {
  symbol: string;
  source: string;
  status: string;
  cik?: number | null;
  entity_name?: string | null;
  generated_at?: string | null;
  metrics: Array<Record<string, unknown>>;
  caveat: string;
  cache?: Record<string, unknown>;
}

export interface FreeDataShortVolumeResponse {
  symbol: string;
  source: string;
  status: string;
  date?: string | null;
  short_volume?: number | null;
  short_exempt_volume?: number | null;
  total_volume?: number | null;
  short_volume_ratio?: number | null;
  market?: string | null;
  diagnostics: string[];
  caveat: string;
  cache?: Record<string, unknown>;
}

export interface WatchlistSymbol {
  symbol: string;
  note?: string | null;
  source: string;
  created_at?: string | null;
}

export interface WatchlistResponse {
  items: WatchlistSymbol[];
}

export type RotationHorizon = '1D' | '1W' | '1M' | '3M' | '6M' | '12M';

export interface RotationConstituent {
  symbol: string;
  label: string;
  group: string;
  last_close?: number | null;
  bar_as_of?: string | null;
  returns: Partial<Record<RotationHorizon, number | null>>;
  rel: Partial<Record<RotationHorizon, number | null>>;
  ranks: Partial<Record<RotationHorizon, number>>;
  composite_score?: number | null;
  composite_rank?: number | null;
  rs_ratio?: number | null;
  rs_momentum?: number | null;
  quadrant?: 'leading' | 'weakening' | 'lagging' | 'improving' | null;
  rank_change?: Partial<Record<RotationHorizon, number | null>>;
  entered_top3_1m?: boolean;
  exited_top3_1m?: boolean;
}

export interface RotationLeaderEntry {
  symbol: string;
  return_pct?: number | null;
  rank_change?: number | null;
  entered_top3_1m?: boolean;
}

export interface RotationSnapshotResponse {
  status: 'completed' | 'missing' | string;
  as_of?: string | null;
  benchmark?: string;
  universe?: string;
  symbols_total?: number;
  symbols_ranked?: number;
  constituents?: RotationConstituent[];
  leaders?: Record<string, { leaders: RotationLeaderEntry[]; laggards: RotationLeaderEntry[] }>;
  warnings?: string[];
  summary?: string;
  generated_at?: string | null;
}

export interface DiscoveryConstituent {
  symbol: string;
  name?: string | null;
  close?: number | null;
  avg_dollar_volume_20d?: number | null;
  pct_of_52w_high?: number | null;
  near_52w_high?: boolean;
  perf_1m_pct?: number | null;
  perf_3m_pct?: number | null;
  rs_1m_pct?: number | null;
  rs_3m_pct?: number | null;
  rs_top_decile?: boolean;
  volume_ratio?: number | null;
  unusual_volume?: boolean;
  quiet_accum_volume_ratio?: number | null;
  quiet_accumulation?: boolean;
  beaten_down_reversal?: boolean;
  sector_symbol?: string | null;
  sector_tailwind?: boolean;
  composite_score?: number | null;
  rank?: number | null;
  reason?: string | null;
  candidate_score?: number | null;
  checklist_status?: string | null;
  entry_zone?: string | null;
}

export interface DiscoverySnapshotResponse {
  status: 'completed' | 'missing' | string;
  as_of?: string | null;
  age_days?: number | null;
  freshness?: 'fresh' | 'stale' | 'unknown' | string;
  universe_size?: number;
  qualified_size?: number;
  constituents?: DiscoveryConstituent[];
  warnings?: string[];
  summary?: string;
  generated_at?: string | null;
}

export interface DownDaySector {
  symbol: string;
  label?: string | null;
  return_1d_pct?: number | null;
}

export interface DownDayStock {
  symbol: string;
  source?: 'watchlist' | 'discovery' | string;
  return_1d_pct?: number | null;
  rs_vs_spy_pp?: number | null;
  reason?: string | null;
}

export interface DownDayRsResponse {
  status: 'completed' | 'missing' | string;
  as_of?: string | null;
  age_days?: number | null;
  freshness?: 'fresh' | 'stale' | 'unknown' | string;
  benchmark?: string;
  spy_return_pct?: number | null;
  threshold_pct?: number | null;
  triggered?: boolean;
  last_down_day?: string | null;
  is_latest_session?: boolean;
  sectors_holding_up?: DownDaySector[];
  stocks_holding_up?: DownDayStock[];
  stocks_scanned?: number;
  stocks_missing?: number;
  warnings?: string[];
  summary?: string;
  generated_at?: string | null;
}

/** Per-population scorecard stats (real and simulated are NEVER merged). */
export interface ScorecardPopulationStats {
  flags: number;
  hits: number;
  flops: number;
  expired: number;
  open: number;
  decided: number;
  batting_average: number | null;
  avg_days_to_hit: number | null;
  avg_max_gain_pct: number | null;
}

/** Per-group stats for by_screen / by_score_band / by_month breakdowns. */
export interface ScorecardGroupStats {
  total: number;
  open: number;
  hit: number;
  flop: number;
  expired: number;
  decided: number;
  batting_average: number | null;
}

export interface ScorecardPopulationBlock {
  /** Rows that passed >=1 screen or made the scored top-40 — the headline. */
  flagged: ScorecardPopulationStats;
  /** ALL liquidity-qualified rows — the control group the flags must beat. */
  baseline: ScorecardPopulationStats;
  /** flagged.batting_average - baseline.batting_average (null when either is null). */
  edge: number | null;
  by_screen: Record<string, ScorecardGroupStats>;
  by_score_band: Record<string, ScorecardGroupStats>;
  by_month: Record<string, ScorecardGroupStats>;
}

export interface ScorecardSummaryResponse {
  status: 'completed' | 'missing' | string;
  /** Only set when status is missing. */
  summary?: string;
  days?: number;
  window_days?: number;
  hit_threshold_pct?: number;
  flop_threshold_pct?: number;
  real?: ScorecardPopulationBlock;
  simulated?: ScorecardPopulationBlock | null;
  caveats?: string[];
  generated_at?: string;
}

export interface ScorecardFlagRow {
  id: number;
  as_of: string | null;
  symbol: string;
  entry_close: number | null;
  composite_score: number | null;
  candidate_score: number | null;
  /** Screen keys the flag passed, e.g. ["near_52w_high", "rs_top_decile"]. */
  screens: string[];
  simulated: boolean;
  status: 'open' | 'hit' | 'flop' | 'expired' | string;
  hit_date: string | null;
  days_to_hit: number | null;
  max_gain_pct: number | null;
  current_return_pct: number | null;
  last_eval_bar_date: string | null;
  window_days: number | null;
  hit_threshold_pct: number | null;
  flop_threshold_pct: number | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface ScorecardFlagsResponse {
  days: number;
  limit: number;
  offset: number;
  total: number;
  rows: ScorecardFlagRow[];
}

export interface CycleIndicator {
  id: string;
  label: string;
  status: string;
  value?: number | null;
  as_of?: string | null;
  trend?: string | null;
  plain?: string | null;
  detail?: string | null;
}

export interface CycleSnapshotResponse {
  status: 'completed' | 'missing' | string;
  as_of?: string | null;
  phase?: string;
  confidence?: string;
  scores?: Record<string, number>;
  indicators?: CycleIndicator[];
  playbook?: string[];
  divergence?: boolean;
  divergence_note?: string | null;
  crypto?: Record<string, unknown>;
  summary?: string;
  generated_at?: string | null;
}

export interface DailyBriefFreshness {
  status: 'completed' | 'missing' | string;
  as_of?: string | null;
  age_days?: number | null;
  freshness?: 'fresh' | 'stale' | 'unknown' | string;
}

export interface DailyBriefRegimeSnapshot {
  as_of?: string | null;
  regime?: string | null;
  score?: number | null;
  confidence?: string | null;
  vix?: number | null;
  vix3m?: number | null;
  term_inverted?: boolean;
  breadth_above_50dma_pct?: number | null;
}

export interface DailyBriefRegimeHistoryPoint {
  as_of?: string | null;
  score?: number | null;
  vix?: number | null;
}

export interface DailyBriefBreadthTrendPoint {
  as_of?: string | null;
  pct_above_50dma?: number | null;
}

export interface DailyBriefSignalItem {
  symbol: string;
  asset_type?: string | null;
  name?: string | null;
  candidate_score: number | null;
  checklist_status: string;
  entry_zone?: string | null;
}

export interface DailyBriefHeadline {
  id: number;
  title: string;
  source?: string | null;
  code?: string | null;
  url?: string | null;
  published_at?: string | null;
  fetched_at?: string | null;
  impact_score?: number | null;
  impact_label?: 'market_moving' | 'sector' | 'stock_specific' | string | null;
  impact_reason?: string | null;
}

export interface DailyBriefResponse {
  regime: DailyBriefFreshness & {
    latest?: DailyBriefRegimeSnapshot | null;
    previous?: DailyBriefRegimeSnapshot | null;
    history?: DailyBriefRegimeHistoryPoint[];
    breadth_trend?: DailyBriefBreadthTrendPoint[];
  };
  rotation: DailyBriefFreshness & Omit<RotationSnapshotResponse, 'status'>;
  cycle: DailyBriefFreshness & Omit<CycleSnapshotResponse, 'status'>;
  signals: DailyBriefFreshness & {
    run_id?: number;
    top?: DailyBriefSignalItem[];
    unscored_count?: number;
  };
  discovery: DailyBriefFreshness & {
    universe_size?: number;
    qualified_size?: number;
    top?: DiscoveryConstituent[];
    summary?: string;
  };
  down_day_rs: DailyBriefFreshness & Omit<DownDayRsResponse, 'status'>;
  headlines: DailyBriefFreshness & {
    top?: DailyBriefHeadline[];
  };
  scorecard: DailyBriefFreshness & {
    window_days?: number;
    hit_threshold_pct?: number;
    real?: DailyBriefScorecardPopulation;
    simulated?: DailyBriefScorecardPopulation | null;
  };
}

/** Slim per-population scorecard stats carried by the daily brief. */
export interface DailyBriefScorecardPopulation {
  flagged: {
    flags?: number | null;
    open?: number | null;
    hits?: number | null;
    decided?: number | null;
    batting_average?: number | null;
    avg_days_to_hit?: number | null;
    avg_max_gain_pct?: number | null;
  };
  baseline: {
    decided?: number | null;
    batting_average?: number | null;
  };
  edge?: number | null;
}

export interface SparklinePoint {
  date: string;
  close: number;
}

export interface SparklinesResponse {
  days: number;
  series: Record<string, SparklinePoint[]>;
}

export interface SymbolNamesResponse {
  /** Symbol → display name (company / ETF label). Unknown symbols are omitted. */
  names: Record<string, string>;
}

export const researchApi = {
  async getDailyBrief(): Promise<DailyBriefResponse> {
    const response = await apiClient.get<DailyBriefResponse>('/api/v1/research/free-data/daily-brief');
    return response.data;
  },
  async getSparklines(symbols: string[], days = 30): Promise<SparklinesResponse> {
    const response = await apiClient.get<SparklinesResponse>('/api/v1/research/free-data/sparklines', {
      params: { symbols: symbols.join(','), days },
    });
    return response.data;
  },
  async getSymbolNames(symbols: string[]): Promise<SymbolNamesResponse> {
    const response = await apiClient.get<SymbolNamesResponse>('/api/v1/research/free-data/symbol-names', {
      params: { symbols: symbols.join(',') },
    });
    return response.data;
  },
  async getRotationSnapshot(): Promise<RotationSnapshotResponse> {
    const response = await apiClient.get<RotationSnapshotResponse>('/api/v1/research/free-data/rotation/latest');
    return response.data;
  },
  async runRotationSnapshot(): Promise<RotationSnapshotResponse> {
    const response = await apiClient.post<RotationSnapshotResponse>('/api/v1/research/free-data/rotation/run', undefined, {
      timeout: 240000,
    });
    return response.data;
  },
  async getDiscoverySnapshot(): Promise<DiscoverySnapshotResponse> {
    const response = await apiClient.get<DiscoverySnapshotResponse>('/api/v1/research/free-data/discovery/latest');
    return response.data;
  },
  async runDiscoverySnapshot(): Promise<DiscoverySnapshotResponse> {
    // A cold full-universe scan can take ~10 minutes behind free-tier rate limits.
    const response = await apiClient.post<DiscoverySnapshotResponse>('/api/v1/research/free-data/discovery/run', undefined, {
      timeout: 900000,
    });
    return response.data;
  },
  async getDownDayRs(): Promise<DownDayRsResponse> {
    const response = await apiClient.get<DownDayRsResponse>('/api/v1/research/free-data/down-day-rs/latest');
    return response.data;
  },
  async getCycleSnapshot(): Promise<CycleSnapshotResponse> {
    const response = await apiClient.get<CycleSnapshotResponse>('/api/v1/research/free-data/cycle/latest');
    return response.data;
  },
  async runCycleSnapshot(): Promise<CycleSnapshotResponse> {
    const response = await apiClient.post<CycleSnapshotResponse>('/api/v1/research/free-data/cycle/run', undefined, {
      timeout: 240000,
    });
    return response.data;
  },
  async getScorecard(params?: { days?: number; include_simulated?: boolean }): Promise<ScorecardSummaryResponse> {
    const response = await apiClient.get<ScorecardSummaryResponse>('/api/v1/research/scorecard', { params });
    return response.data;
  },
  async getScorecardFlags(params?: {
    status?: 'open' | 'hit' | 'flop' | 'expired';
    simulated?: boolean;
    screen?: string;
    days?: number;
    limit?: number;
    offset?: number;
  }): Promise<ScorecardFlagsResponse> {
    const response = await apiClient.get<ScorecardFlagsResponse>('/api/v1/research/scorecard/flags', { params });
    return response.data;
  },
  async runScorecardBootstrap(): Promise<{ status: 'started' | 'already_running' | string }> {
    const response = await apiClient.post<{ status: string }>('/api/v1/research/scorecard/bootstrap');
    return response.data;
  },
  async getXStatus(): Promise<XStatus> {
    const response = await apiClient.get<XStatus>('/api/v1/research/x/status');
    return response.data;
  },
  async startXOAuth(): Promise<{ configured: boolean; auth_url: string; state?: string; message?: string }> {
    const response = await apiClient.post('/api/v1/research/x/oauth/start');
    return response.data;
  },
  async syncXBookmarks(maxPages = 3): Promise<{ success: boolean; status: string; imported: number; mentions: number; pages: number }> {
    const response = await apiClient.post('/api/v1/research/x/sync', { max_pages: maxPages });
    return response.data;
  },
  async listIdeas(params?: { limit?: number; asset_type?: 'stock' | 'crypto'; symbol?: string }): Promise<ResearchIdea[]> {
    const response = await apiClient.get<{ items: ResearchIdea[] }>('/api/v1/research/ideas', { params });
    return response.data.items;
  },
  async createIdea(payload: { content: string; title?: string; source_type?: string }): Promise<ResearchIdea> {
    const response = await apiClient.post<ResearchIdea>('/api/v1/research/ideas', payload);
    return response.data;
  },
  async createSignalRun(payload: SignalRunRequest = {}): Promise<SignalRun> {
    const response = await apiClient.post<SignalRun>('/api/v1/research/signal-runs', payload, {
      timeout: 180000,
    });
    return response.data;
  },
  async getSignalRun(runId: number): Promise<SignalRun> {
    const response = await apiClient.get<SignalRun>(`/api/v1/research/signal-runs/${runId}`);
    return response.data;
  },
  async getLatestRotationMemo(): Promise<RotationMemo | null> {
    const response = await apiClient.get<{ memo: RotationMemo | null }>('/api/v1/research/rotation-memos/latest');
    return response.data.memo;
  },
  async getPriceHistory(symbol: string, assetType: 'stock' | 'crypto' = 'stock', days = 260): Promise<PriceHistoryResponse> {
    const response = await apiClient.get<PriceHistoryResponse>(`/api/v1/research/price-history/${encodeURIComponent(symbol)}`, {
      params: { asset_type: assetType, days },
      timeout: 30000,
    });
    return response.data;
  },
  async getPositioning(symbol: string, assetType: 'stock' | 'crypto' = 'stock'): Promise<PositioningResponse> {
    const response = await apiClient.get<PositioningResponse>(`/api/v1/research/positioning/${encodeURIComponent(symbol)}`, {
      params: { asset_type: assetType },
      timeout: 90000,
    });
    return response.data;
  },
  async getFreeDataStatus(): Promise<FreeDataStatusResponse> {
    const response = await apiClient.get<FreeDataStatusResponse>('/api/v1/research/free-data/status', { timeout: 90000 });
    return response.data;
  },
  async getFreeDataUniverse(limit = 5000): Promise<FreeDataUniverseResponse> {
    const response = await apiClient.get<FreeDataUniverseResponse>('/api/v1/research/free-data/universe', {
      params: { limit },
      timeout: 60000,
    });
    return response.data;
  },
  async getFreeDataMacro(): Promise<FreeDataMacroResponse> {
    const response = await apiClient.get<FreeDataMacroResponse>('/api/v1/research/free-data/macro', { timeout: 60000 });
    return response.data;
  },
  async getMarketRegime(): Promise<MarketRegimeResponse> {
    const response = await apiClient.get<MarketRegimeResponse>('/api/v1/research/free-data/market-regime', { timeout: 90000 });
    return response.data;
  },
  async getLatestMarketBreadth(universe = 'us_stocks'): Promise<MarketBreadthResponse> {
    const response = await apiClient.get<MarketBreadthResponse>('/api/v1/research/free-data/market-breadth/latest', {
      params: { universe },
      timeout: 30000,
    });
    return response.data;
  },
  async runMarketBreadthCache(payload: { universe?: string; limit?: number; min_price?: number; min_avg_dollar_volume?: number } = {}): Promise<MarketBreadthResponse> {
    const response = await apiClient.post<MarketBreadthResponse>('/api/v1/research/free-data/market-breadth/run', payload, {
      timeout: 240000,
    });
    return response.data;
  },
  async getFreeDataFundamentals(symbol: string): Promise<FreeDataFundamentalsResponse> {
    const response = await apiClient.get<FreeDataFundamentalsResponse>(`/api/v1/research/free-data/fundamentals/${encodeURIComponent(symbol)}`, { timeout: 60000 });
    return response.data;
  },
  async getFreeDataShortVolume(symbol: string): Promise<FreeDataShortVolumeResponse> {
    const response = await apiClient.get<FreeDataShortVolumeResponse>(`/api/v1/research/free-data/short-volume/${encodeURIComponent(symbol)}`, { timeout: 60000 });
    return response.data;
  },
  async getWatchlist(): Promise<WatchlistSymbol[]> {
    const response = await apiClient.get<WatchlistResponse>('/api/v1/research/watchlist');
    return response.data.items;
  },
  async addWatchlistSymbol(symbol: string, note?: string): Promise<WatchlistSymbol> {
    const response = await apiClient.post<WatchlistSymbol>('/api/v1/research/watchlist', { symbol, note });
    return response.data;
  },
  async removeWatchlistSymbol(symbol: string): Promise<{ symbol: string; removed: boolean }> {
    const response = await apiClient.delete<{ symbol: string; removed: boolean }>(`/api/v1/research/watchlist/${encodeURIComponent(symbol)}`);
    return response.data;
  },
};
