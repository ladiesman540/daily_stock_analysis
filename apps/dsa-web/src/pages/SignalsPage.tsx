import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { CheckCircle2, ChevronDown, ChevronUp, DatabaseZap, RefreshCcw, SearchCheck, SlidersHorizontal, TrendingUp } from 'lucide-react';
import { AppPage, Badge, Button, Card, DataFreshnessBadge, DecisionExplanation as DecisionExplanationPanel, DiscoveryScreenBadges, EmptyState, InlineAlert, TickerChip } from '../components/common';
import type { DecisionExplanationData } from '../components/common';
import { researchApi, type DiscoverySnapshotResponse, type PriceHistoryResponse, type SignalCandidate, type SignalRun } from '../api/research';
import { useSymbolNamesStore } from '../stores/symbolNamesStore';
import { discoveryScreens } from '../utils/discoveryScreens';

const statusVariant = (status: string): 'success' | 'warning' | 'danger' | 'default' => {
  if (status === 'pass') return 'success';
  if (status === 'watchlist') return 'warning';
  if (status === 'reject') return 'danger';
  return 'default';
};

const formatScore = (score: number | null | undefined): string => (
  typeof score === 'number' && Number.isFinite(score) ? score.toFixed(1) : 'n/a'
);

const formatMetric = (value: unknown, suffix = '') => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `${value.toFixed(Math.abs(value) >= 100 ? 0 : 2)}${suffix}`;
  }
  return 'N/A';
};

const formatSource = (source?: string | null): string => {
  if (source === 'massive') return 'Massive / Polygon';
  if (source === 'yfinance') return 'Yahoo Finance via yfinance';
  if (source === 'coingecko') return 'CoinGecko';
  return source || 'unknown source';
};

const asRecord = (value: unknown): Record<string, unknown> => (
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
);

const asString = (value: unknown): string => (typeof value === 'string' ? value : '');
const asStringArray = (value: unknown): string[] | null => (
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : null
);

const DEFAULT_STOCK_SCAN_SYMBOLS = [
  'AAPL', 'NVDA', 'AMD', 'AVGO', 'ARM', 'PLTR', 'APP', 'TSLA', 'META', 'MSFT', 'COIN', 'MSTR',
  'HOOD', 'CRWD', 'NET', 'RKLB', 'IONQ', 'SMCI', 'COHR', 'SOFI', 'RBLX', 'NFLX', 'GOOGL', 'AMZN',
  'SPY', 'QQQ', 'IWM', 'DIA', 'SMH', 'SOXX', 'XLK', 'XLC', 'XLY', 'XLF', 'XLI', 'XLE', 'XLV', 'XBI', 'ARKK',
];

const SCAN_DEFAULT_ENV_VARS = [
  'RESEARCH_US_SYMBOLS',
  'RESEARCH_US_WATCHLIST',
  'RESEARCH_FINVIZ_CSV_PATH',
  'RESEARCH_US_INCLUDE_ETFS',
];

const parseSymbols = (value: string): string[] => {
  const seen = new Set<string>();
  return value
    .split(/[\s,;]+/)
    .map((item) => item.trim().replace(/^\$/, '').toUpperCase())
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item)) return false;
      seen.add(item);
      return true;
    });
};

const qualityVariant = (tier: string): 'success' | 'warning' | 'default' | 'info' => {
  if (tier.includes('primary') || tier.includes('official')) return 'success';
  if (tier.includes('fallback') || tier.includes('unknown')) return 'warning';
  return 'info';
};

const qualityLabel = (quality: Record<string, unknown>): string => {
  const tier = asString(quality.tier);
  if (tier.includes('primary')) return 'Primary data';
  if (tier.includes('fallback')) return 'Fallback data';
  if (tier.includes('spot')) return 'Spot data';
  return 'Data quality unknown';
};

const formatPrice = (value: number): string => {
  if (Math.abs(value) >= 1000) return value.toFixed(0);
  if (Math.abs(value) >= 100) return value.toFixed(1);
  return value.toFixed(2);
};

const formatAdjustment = (item: Record<string, unknown>): string => {
  const reason = asString(item.reason) || 'setup penalty';
  const points = typeof item.points === 'number' && Number.isFinite(item.points) ? item.points : null;
  return points === null ? reason : `${reason} (${points.toFixed(1)})`;
};

const friendlyReason = (reason: string): string => {
  const cleaned = reason.trim();
  const lower = cleaned.toLowerCase();
  if (!cleaned) return '';
  if (lower.includes('one-month move is already above the preferred grinding band')) {
    return 'It may already be moving too fast for the ideal grinding-leader setup.';
  }
  if (lower.includes('one-month trend is not grinding strongly enough')) {
    return 'The 1-month trend is not strong enough yet.';
  }
  if (lower.includes('rsi is above preferred zone')) {
    return 'Momentum is hot. A cleaner reset would reduce chase risk.';
  }
  if (lower.includes('rsi is below preferred leadership zone')) {
    return 'Momentum is not strong enough for the preferred leadership zone.';
  }
  if (lower.includes('price is extended from sma20')) {
    return 'Price is stretched above the short-term average, so entry risk is worse.';
  }
  if (lower.includes('no recent bookmark/source evidence')) {
    return 'No saved source, bookmark, or catalyst evidence is supporting it yet.';
  }
  if (lower.startsWith('failed gates:')) {
    return `Failed checklist gates: ${cleaned.replace(/^failed gates:\s*/i, '').replaceAll('_', ' ')}.`;
  }
  if (lower.includes('need at least 200 daily bars')) {
    return 'The app does not have enough price history to judge the full trend setup.';
  }
  return cleaned;
};

const reasonList = (candidate: SignalCandidate): string[] => {
  const raw = candidate.why_not_higher || '';
  const parts = raw.split(';').map(friendlyReason).filter(Boolean);
  return parts.length ? parts : ['No extra rejection reason was reported.'];
};

const statusExplanation = (candidate: SignalCandidate): string => {
  if (candidate.checklist_status === 'pass') {
    return 'Passed means the candidate cleared the main trend, liquidity, and risk gates. It is worth deeper work, not an automatic buy.';
  }
  if (candidate.checklist_status === 'watchlist') {
    return 'Watchlist means the idea has some strength, but one or more gates need improvement before it becomes actionable.';
  }
  if (candidate.checklist_status === 'reject') {
    return 'Rejected means the setup failed an important gate. It can still move, but the app does not think the risk/reward is clean enough right now.';
  }
  return 'This status is the app’s checklist result. Use it to decide whether the idea deserves more attention.';
};

const nextStep = (candidate: SignalCandidate): string => {
  if (candidate.checklist_status === 'pass') {
    return 'Next step: open Positioning, check crowding/gamma, then decide whether the entry zone and invalidation make sense.';
  }
  if (candidate.checklist_status === 'watchlist') {
    return 'Next step: do not chase. Look for a reset, catalyst, or cleaner confirmation before treating it as actionable.';
  }
  return 'Next step: ignore it for now unless the failed gates improve or your outside thesis is strong enough to override the system.';
};

const SIGNALS_RULE_EXPLANATION: DecisionExplanationData = {
  title: 'How this page decides',
  decision: 'Strength first, actionability second',
  plainEnglish: 'Signals first measures raw chart strength, then converts it into a setup score by penalizing bad timing, failed gates, missing source evidence, and stretched entries.',
  evidence: [
    { label: 'Trend', value: '1M and 3M strength', source: 'Daily bars from Massive/Polygon or fallback data.' },
    { label: 'Heat', value: 'RSI and extension', source: 'Keeps the app from chasing already-stretched moves.' },
    { label: 'Catalyst', value: 'News/bookmarks/source evidence', source: 'Used after price passes the first screen.' },
    { label: 'Risk', value: 'Entry, invalidation, data gaps', source: 'Used to decide pass/watchlist/reject.' },
  ],
  math: [
    { formula: 'Raw strength score', result: 'how strong the chart is before penalties' },
    { formula: 'Setup score', result: 'raw strength minus timing, data, catalyst, and checklist penalties' },
  ],
  confidence: {
    level: 'medium',
    reason: 'Confidence depends on whether the candidate has primary price data, enough history, and source/catalyst evidence.',
  },
  whatWouldChange: [
    'A pullback into the entry zone can improve a watchlist name.',
    'A new catalyst or bookmark from a proven source can improve the score.',
    'Overextension, weak RSI, or missing data can downgrade the status.',
  ],
  guardrails: [
    'Pass means investigate now, not buy now.',
    'Watchlist means wait for better evidence or timing.',
  ],
};

const buildSignalExplanation = (
  candidate: SignalCandidate,
  quality: Record<string, unknown>,
  warnings: string[],
  reasons: string[],
  evidenceCount: number,
): DecisionExplanationData => {
  const metrics = candidate.metrics || {};
  const status = candidate.checklist_status;
  const confidenceLevel = asString(quality.tier).includes('primary') ? 'high' : asString(quality.tier).includes('fallback') ? 'medium' : 'unknown';
  const rawStrength = typeof metrics.raw_strength_score === 'number' ? metrics.raw_strength_score : candidate.candidate_score ?? null;
  const adjustments = Array.isArray(metrics.score_adjustments)
    ? metrics.score_adjustments.map(asRecord).map(formatAdjustment)
    : [];
  return {
    title: 'Why this signal says that',
    decision: `${candidate.symbol}: ${status} (setup ${formatScore(candidate.candidate_score)})`,
    plainEnglish: statusExplanation(candidate),
    evidence: [
      { label: '1M move', value: formatMetric(metrics.perf_1m_pct, '%'), source: 'Grinding leader band prefers roughly 5-15%.' },
      { label: '3M move', value: formatMetric(metrics.perf_3m_pct, '%'), source: 'Leadership filter wants sustained strength.' },
      { label: 'RSI heat', value: formatMetric(metrics.rsi14), source: 'Preferred zone is strong but not wildly overheated.' },
      { label: 'Data source', value: formatSource(asString(quality.source) || asString(metrics.data_source)), source: asString(quality.last_bar_date) ? `Last bar ${asString(quality.last_bar_date)}` : 'Freshness unknown' },
      { label: 'Source evidence', value: String(evidenceCount), source: 'Bookmarks/news/catalyst evidence attached to this idea.' },
    ],
    math: [
      {
        formula: 'Raw strength score',
        result: typeof rawStrength === 'number' ? rawStrength.toFixed(1) : 'n/a',
      },
      {
        formula: 'Setup score after penalties',
        result: adjustments.length
          ? `${formatScore(candidate.candidate_score)} after: ${adjustments.slice(0, 4).join('; ')}`
          : `${formatScore(candidate.candidate_score)} with no setup penalties`,
      },
    ],
    confidence: {
      level: confidenceLevel,
      reason: qualityLabel(quality) + '. Confidence is data quality, not prediction certainty.',
    },
    warnings: warnings.slice(0, 3),
    whatWouldChange: [
      'A cleaner pullback closer to the entry zone can improve the setup.',
      'Fresh catalyst/source evidence can improve conviction.',
      'Overextension, weak RSI, or a broken moving-average trend can downgrade it.',
    ],
    guardrails: [
      'Pass means investigate now, not buy now.',
      'Watchlist means wait for a better entry or better evidence.',
      ...adjustments.slice(0, 2),
      ...reasons.slice(0, 2),
    ],
  };
};

const SignalsPage: React.FC = () => {
  const [run, setRun] = useState<SignalRun | null>(null);
  const [statusFilter, setStatusFilter] = useState<'all' | 'pass' | 'watchlist' | 'reject'>('watchlist');
  const [assetFilter, setAssetFilter] = useState<'all' | 'stock' | 'crypto'>('stock');
  const [scanSymbols, setScanSymbols] = useState(DEFAULT_STOCK_SCAN_SYMBOLS.join(', '));
  const [includeStocks, setIncludeStocks] = useState(true);
  const [includeCrypto, setIncludeCrypto] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [discovery, setDiscovery] = useState<DiscoverySnapshotResponse | null>(null);
  const [showScanSettings, setShowScanSettings] = useState(false);

  const hydrateScanControls = useCallback((nextRun: SignalRun) => {
    const params = nextRun.parameters || {};
    const symbols = asStringArray(params.symbols);
    if (symbols?.length) {
      setScanSymbols(symbols.join(', '));
    } else {
      setScanSymbols(DEFAULT_STOCK_SCAN_SYMBOLS.join(', '));
    }
    if (typeof params.include_us === 'boolean') setIncludeStocks(params.include_us);
    if (typeof params.include_crypto === 'boolean') setIncludeCrypto(params.include_crypto);
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      const memo = await researchApi.getLatestRotationMemo();
      if (memo?.signal_run_id) {
        const latestRun = await researchApi.getSignalRun(memo.signal_run_id);
        setRun(latestRun);
        hydrateScanControls(latestRun);
      }
    } catch {
      setError('Failed to load signals.');
    }
  }, [hydrateScanControls]);

  useEffect(() => {
    document.title = 'Signals - DSA';
    void loadLatest();
  }, [loadLatest]);

  useEffect(() => {
    researchApi.getDiscoverySnapshot()
      .then((snapshot) => {
        setDiscovery(snapshot);
        const symbols = (snapshot?.constituents || [])
          .slice(0, DISCOVERY_ROW_LIMIT)
          .map((item) => item.symbol)
          .filter(Boolean);
        if (symbols.length) void useSymbolNamesStore.getState().loadNames(symbols);
      })
      .catch(() => setDiscovery(null));
  }, []);

  // Resolve display names for the candidate chips (title attr / drawer reuse).
  useEffect(() => {
    const symbols = (run?.candidates || []).map((candidate) => candidate.symbol).filter(Boolean);
    if (symbols.length) void useSymbolNamesStore.getState().loadNames(symbols);
  }, [run?.candidates]);

  const handleRun = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const symbols = parseSymbols(scanSymbols);
      const nextRun = await researchApi.createSignalRun({
        symbols: includeStocks && symbols.length ? symbols : undefined,
        include_us: includeStocks,
        include_crypto: includeCrypto,
        run_type: includeStocks && symbols.length ? 'custom' : 'weekly',
      });
      setRun(nextRun);
      hydrateScanControls(nextRun);
    } catch {
      setError('Scan failed. Check data sources and try again.');
    } finally {
      setLoading(false);
    }
  }, [includeCrypto, includeStocks, scanSymbols]);

  const filtered = useMemo(() => {
    const candidates = run?.candidates ?? [];
    return candidates.filter((candidate) => {
      if (statusFilter !== 'all' && candidate.checklist_status !== statusFilter) return false;
      if (assetFilter !== 'all' && candidate.asset_type !== assetFilter) return false;
      return true;
    });
  }, [assetFilter, run?.candidates, statusFilter]);

  const stats = useMemo(() => {
    const candidates = run?.candidates ?? [];
    return {
      total: candidates.length,
      pass: candidates.filter((item) => item.checklist_status === 'pass').length,
      watchlist: candidates.filter((item) => item.checklist_status === 'watchlist').length,
      crypto: candidates.filter((item) => item.asset_type === 'crypto').length,
    };
  }, [run?.candidates]);

  const dataAsOf = useMemo(() => {
    const dates = (run?.candidates ?? [])
      .map((candidate) => asString(asRecord(asRecord(candidate.metrics).data_quality).last_bar_date))
      .filter(Boolean)
      .sort();
    return dates.length ? dates[dates.length - 1] : run?.completed_at || null;
  }, [run]);

  const hasCandidates = (run?.candidates?.length ?? 0) > 0;
  const hasDiscovery = discovery?.status === 'completed';
  const showHeroEmpty = !hasCandidates && !hasDiscovery;

  return (
    <AppPage>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-uppercase">Research</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold text-foreground">Signals</h1>
            <DataFreshnessBadge asOf={dataAsOf} />
          </div>
        </div>
        <Button onClick={handleRun} isLoading={loading} loadingText="Scanning" disabled={!includeStocks && !includeCrypto}>
          <RefreshCcw className="h-4 w-4" />
          Run scan
        </Button>
      </div>

      {error ? <InlineAlert variant="danger" title="Research error" message={error} className="mb-4" /> : null}

      {showHeroEmpty ? (
        <Card padding="md" className="mb-4 rounded-lg">
          <EmptyState
            icon={<TrendingUp className="h-6 w-6" />}
            title="No signals yet"
            description="This page scans stocks and crypto for strong setups and ranks the best ideas. Your first signals arrive automatically with the nightly update (6:00 PM) — or run a scan now."
            action={(
              <Button onClick={handleRun} isLoading={loading} loadingText="Scanning" disabled={!includeStocks && !includeCrypto}>
                <RefreshCcw className="h-4 w-4" />
                Run a scan now
              </Button>
            )}
          />
        </Card>
      ) : (
        <>
          <div className="mb-3 grid grid-cols-2 gap-2 sm:gap-3 md:grid-cols-4">
            <MetricCard label="Candidates" value={stats.total} />
            <MetricCard label="Passed" value={stats.pass} tone="success" />
            <MetricCard label="Watchlist" value={stats.watchlist} tone="warning" />
            <MetricCard label="Crypto" value={stats.crypto} tone="info" />
          </div>

          <div className="mb-4 flex flex-wrap items-center gap-2 rounded-2xl border border-border/60 bg-elevated/35 p-2.5">
            <SlidersHorizontal className="h-4 w-4 text-secondary-text" />
            {(['watchlist', 'pass', 'reject', 'all'] as const).map((item) => (
              <Button
                key={item}
                variant={statusFilter === item ? 'primary' : 'secondary'}
                size="sm"
                onClick={() => setStatusFilter(item)}
              >
                {item}
              </Button>
            ))}
            {(['stock', 'crypto', 'all'] as const).map((item) => (
              <Button
                key={item}
                variant={assetFilter === item ? 'outline' : 'ghost'}
                size="sm"
                onClick={() => setAssetFilter(item)}
              >
                {item}
              </Button>
            ))}
          </div>

          {filtered.length === 0 ? (
            <EmptyState
              icon={<TrendingUp className="h-6 w-6" />}
              title="No candidates"
              description={hasCandidates
                ? 'Nothing matches the current filters — try a different status or asset filter above.'
                : 'Signals arrive automatically with the nightly update (6:00 PM), or run a scan now with the Run scan button above.'}
            />
          ) : (
            <div className="grid gap-3 xl:grid-cols-2">
              {filtered.map((candidate) => (
                <CandidateCard key={candidate.id} candidate={candidate} />
              ))}
            </div>
          )}

          <DiscoverySection discovery={discovery} />
        </>
      )}

      <div className="mt-5">
        <Button variant="secondary" size="sm" onClick={() => setShowScanSettings((value) => !value)}>
          {showScanSettings ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {showScanSettings ? 'Hide scan settings' : 'Scan settings'}
        </Button>
      </div>

      {showScanSettings ? (
        <div className="mt-3">
          <ScanSetupPanel
            run={run}
            scanSymbols={scanSymbols}
            onScanSymbolsChange={setScanSymbols}
            includeStocks={includeStocks}
            onIncludeStocksChange={setIncludeStocks}
            includeCrypto={includeCrypto}
            onIncludeCryptoChange={setIncludeCrypto}
            onUseDefaults={() => setScanSymbols(DEFAULT_STOCK_SCAN_SYMBOLS.join(', '))}
            onClear={() => setScanSymbols('')}
            onRun={handleRun}
            loading={loading}
          />
        </div>
      ) : null}

      <DecisionExplanationPanel data={SIGNALS_RULE_EXPLANATION} compact className="mt-5" />

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="What This Page Is" subtitle="Signal machine" padding="md" className="rounded-lg">
          <div className="grid gap-3">
            <GuidePoint
              title="It ranks ideas"
              text="This is the weekly screener for stocks and crypto. It tries to find strong names, then tells you why they are pass, watchlist, or reject."
            />
            <GuidePoint
              title="Default view"
              text="You are seeing stock watchlist names first because they are the highest-signal review queue. Use all or reject only when you want the full audit trail."
            />
            <GuidePoint
              title="Your job"
              text="Start with the top cards, read what held each one back, then check Positioning before risking money."
            />
          </div>
        </Card>

        <Card title="How To Read Signals" subtitle="Plain English" padding="md" className="rounded-lg">
          <div className="grid gap-3">
            <GuidePoint
              title="Score"
              text="The visible score is setup quality right now. Raw strength can be high while setup score drops because the name is too extended, too hot, or missing catalyst evidence."
            />
            <GuidePoint
              title="Status"
              text="Pass means investigate now. Watchlist means wait for better confirmation. Reject means the setup is too flawed for the current rules."
            />
            <GuidePoint
              title="Why not higher"
              text="This is the important part. It tells you what is wrong with the setup, such as overextension, weak momentum, no catalyst, or missing data."
            />
          </div>
        </Card>
      </div>
    </AppPage>
  );
};

const MetricCard: React.FC<{ label: string; value: number; tone?: 'success' | 'warning' | 'info' }> = ({ label, value, tone }) => (
  <Card padding="sm" className="rounded-lg">
    <p className="text-xs uppercase tracking-normal text-secondary-text">{label}</p>
    <p className={tone === 'success' ? 'mt-2 text-2xl font-semibold text-success' : tone === 'warning' ? 'mt-2 text-2xl font-semibold text-warning' : tone === 'info' ? 'mt-2 text-2xl font-semibold text-cyan' : 'mt-2 text-2xl font-semibold text-foreground'}>
      {value}
    </p>
  </Card>
);

const ScanSetupPanel: React.FC<{
  run: SignalRun | null;
  scanSymbols: string;
  onScanSymbolsChange: (value: string) => void;
  includeStocks: boolean;
  onIncludeStocksChange: (value: boolean) => void;
  includeCrypto: boolean;
  onIncludeCryptoChange: (value: boolean) => void;
  onUseDefaults: () => void;
  onClear: () => void;
  onRun: () => void;
  loading: boolean;
}> = ({
  run,
  scanSymbols,
  onScanSymbolsChange,
  includeStocks,
  onIncludeStocksChange,
  includeCrypto,
  onIncludeCryptoChange,
  onUseDefaults,
  onClear,
  onRun,
  loading,
}) => {
  const symbols = useMemo(() => parseSymbols(scanSymbols), [scanSymbols]);
  const latestRawSymbols = run?.parameters?.symbols;
  const latestSymbols = asStringArray(latestRawSymbols);
  const latestMode = latestSymbols && latestSymbols.length
    ? `${latestSymbols.length} custom stock/ETF symbols`
    : 'Default stock/ETF universe';

  return (
    <Card title="Scan Setup" subtitle="Find and edit candidates" padding="md" className="mb-4 rounded-lg">
      <div className="grid gap-4 xl:grid-cols-[1.1fr_0.9fr]">
        <div>
          <p className="text-sm leading-6 text-foreground">
            Candidates come from this scan list. Edit the symbols below, choose whether to include crypto, then run the scan again.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border/60 bg-elevated/45 px-3 py-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={includeStocks}
                onChange={(event) => onIncludeStocksChange(event.target.checked)}
                className="h-4 w-4"
              />
              Stocks / ETFs
            </label>
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border/60 bg-elevated/45 px-3 py-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={includeCrypto}
                onChange={(event) => onIncludeCryptoChange(event.target.checked)}
                className="h-4 w-4"
              />
              Crypto
            </label>
          </div>
          <label className="mt-3 block">
            <span className="text-xs uppercase tracking-normal text-secondary-text">Stocks / ETFs to scan</span>
            <textarea
              value={scanSymbols}
              onChange={(event) => onScanSymbolsChange(event.target.value)}
              rows={4}
              className="mt-2 w-full resize-y rounded-lg border border-border/70 bg-card px-3 py-2 text-sm leading-6 text-foreground outline-none transition focus:border-cyan/40 focus:ring-4 focus:ring-cyan/10"
              placeholder="Example: NVDA, AMD, COHR, SMH, SOXX"
            />
          </label>
          <p className="mt-2 text-xs leading-5 text-secondary-text">
            Empty box means use the backend default universe. To scan no stocks, turn Stocks / ETFs off.
          </p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="secondary" size="sm" onClick={onUseDefaults}>Use default list</Button>
            <Button variant="ghost" size="sm" onClick={onClear}>Clear symbols</Button>
            <Button onClick={onRun} isLoading={loading} loadingText="Scanning" disabled={!includeStocks && !includeCrypto}>
              <RefreshCcw className="h-4 w-4" />
              Run this scan
            </Button>
          </div>
        </div>

        <div className="grid gap-2">
          <Info label="Current stock/ETF list" value={includeStocks ? `${symbols.length || 'default'} symbols` : 'off'} />
          <Info label="Crypto universe" value={includeCrypto ? 'Top liquid crypto list' : 'off'} />
          <Info label="Latest run" value={run ? `#${run.id} - ${latestMode}` : 'No run loaded'} />
          <Info label="Finished" value={run?.completed_at || 'Not run yet'} />
          <div className="rounded-lg border border-border/60 bg-elevated/45 p-3 text-xs leading-5 text-secondary-text">
            <p>Temporary edits apply to the next run. Permanent defaults live in backend environment variables:</p>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {SCAN_DEFAULT_ENV_VARS.map((name) => (
                <code key={name} className="rounded-md border border-border/60 bg-card px-1.5 py-0.5 text-[11px] text-foreground">
                  {name}
                </code>
              ))}
            </div>
          </div>
        </div>
      </div>
    </Card>
  );
};

const CandidateCard: React.FC<{ candidate: SignalCandidate }> = ({ candidate }) => {
  const metrics = candidate.metrics || {};
  const quality = asRecord(metrics.data_quality);
  const warnings = Array.isArray(metrics.data_warnings) ? metrics.data_warnings.filter((item): item is string => typeof item === 'string') : [];
  const evidenceCount = candidate.source_evidence?.length ?? 0;
  const reasons = reasonList(candidate);
  const explanation = buildSignalExplanation(candidate, quality, warnings, reasons, evidenceCount);
  const [history, setHistory] = useState<PriceHistoryResponse | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setHistory(null);
    setHistoryError(null);
    researchApi.getPriceHistory(candidate.symbol, candidate.asset_type, 260)
      .then((payload) => {
        if (!cancelled) setHistory(payload);
      })
      .catch(() => {
        if (!cancelled) setHistoryError('Chart data unavailable.');
      });
    return () => {
      cancelled = true;
    };
  }, [candidate.asset_type, candidate.symbol]);

  return (
    <Card padding="md" className="rounded-lg">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2>
              <TickerChip
                symbol={candidate.symbol}
                context={{
                  source: 'signal',
                  metrics: [
                    { label: 'Score', value: formatScore(candidate.candidate_score) },
                    { label: 'Checklist', value: candidate.checklist_status, term: 'checklist_status' },
                    ...(candidate.entry_zone
                      ? [{ label: 'Entry zone', value: candidate.entry_zone, term: 'entry_zone' }]
                      : []),
                  ],
                }}
              />
            </h2>
            <Badge variant="info">{candidate.asset_type}</Badge>
            <Badge variant={statusVariant(candidate.checklist_status)}>{candidate.checklist_status}</Badge>
          </div>
          <p className="mt-1 text-sm text-secondary-text">{candidate.name || candidate.symbol}</p>
        </div>
        <div className="text-right">
          <p className="text-xs uppercase tracking-normal text-secondary-text">Setup score</p>
          <p className="text-2xl font-semibold text-cyan">{formatScore(candidate.candidate_score)}</p>
          <Badge variant={qualityVariant(asString(quality.tier))} className="mt-1">{qualityLabel(quality)}</Badge>
        </div>
      </div>

      <PriceChart history={history} error={historyError} />

      <div className="mb-4 rounded-lg border border-border/60 bg-elevated/45 px-3 py-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <DatabaseZap className="h-4 w-4 text-cyan" />
            <p className="text-sm font-medium text-foreground">{formatSource(asString(quality.source) || asString(metrics.data_source))}</p>
          </div>
          <p className="text-xs text-secondary-text">
            {asString(quality.last_bar_date) ? `Last bar ${asString(quality.last_bar_date)}` : 'No freshness timestamp'}
          </p>
        </div>
        {warnings.length > 0 ? (
          <div className="mt-2 space-y-1 text-xs leading-5 text-warning">
            {warnings.slice(0, 2).map((warning) => <p key={warning}>{warning}</p>)}
          </div>
        ) : null}
      </div>

      <div className="grid gap-2 text-sm sm:grid-cols-3">
        <Info label="1M move" value={formatMetric(metrics.perf_1m_pct, '%')} />
        <Info label="3M move" value={formatMetric(metrics.perf_3m_pct, '%')} />
        <Info label="RSI heat" value={formatMetric(metrics.rsi14)} />
        <Info label="Raw strength" value={formatMetric(metrics.raw_strength_score)} />
        <Info label="Entry zone" value={candidate.entry_zone || 'N/A'} />
        <Info label="Invalidation" value={candidate.invalidation || 'N/A'} />
        <Info label="Risk/reward" value={candidate.risk_reward || 'N/A'} />
      </div>

      <div className="mt-4 border-t border-border/60 pt-3">
        <DecisionExplanationPanel data={explanation} compact />
        <div className="mt-3 rounded-lg border border-border/60 bg-elevated/45 px-3 py-2">
          <div className="mb-2 flex items-center gap-2">
            <SearchCheck className="h-4 w-4 text-cyan" />
            <p className="text-sm font-medium text-foreground">What held it back</p>
          </div>
          <div className="space-y-2">
            {reasons.map((reason) => (
              <div key={reason} className="flex gap-2 text-sm leading-6 text-secondary-text">
                <CheckCircle2 className="mt-1 h-3.5 w-3.5 shrink-0 text-cyan" />
                <span>{reason}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="mt-3 rounded-lg border border-warning/25 bg-warning/10 px-3 py-2 text-sm leading-6 text-warning">
          {nextStep(candidate)}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{evidenceCount} source evidence item{evidenceCount === 1 ? '' : 's'}</p>
      </div>
    </Card>
  );
};

const DISCOVERY_ROW_LIMIT = 40;

const DiscoverySection: React.FC<{ discovery: DiscoverySnapshotResponse | null }> = ({ discovery }) => {
  const [adds, setAdds] = useState<Record<string, 'adding' | 'added' | 'error'>>({});
  const names = useSymbolNamesStore((state) => state.names);
  const rows = (discovery?.constituents || []).slice(0, DISCOVERY_ROW_LIMIT);

  const addToWatchlist = useCallback(async (symbol: string) => {
    setAdds((prev) => ({ ...prev, [symbol]: 'adding' }));
    try {
      await researchApi.addWatchlistSymbol(symbol, 'Added from discovery');
      setAdds((prev) => ({ ...prev, [symbol]: 'added' }));
    } catch {
      setAdds((prev) => ({ ...prev, [symbol]: 'error' }));
    }
  }, []);

  return (
    <Card
      title="Discovered Ideas"
      subtitle={
        discovery?.status === 'completed'
          ? `New non-watchlist names as of ${discovery.as_of || 'latest run'} — ${discovery.qualified_size ?? rows.length} of ${discovery.universe_size ?? '?'} scanned stocks qualified`
          : 'Daily liquidity-gated screen: 52-week highs, RS vs SPY, unusual volume, sector tailwind'
      }
      padding="md"
      className="mt-5 rounded-lg"
    >
      {discovery?.status !== 'completed' ? (
        <EmptyState
          icon={<TrendingUp className="h-6 w-6" />}
          title="No discovered ideas yet"
          description="New ideas arrive automatically with the nightly update (6:00 PM)."
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={<TrendingUp className="h-6 w-6" />}
          title="No qualified ideas"
          description="No stocks passed the liquidity gate and screens in the latest discovery run."
        />
      ) : (
        <div className="divide-y divide-border/60">
          {rows.map((item) => {
            const screens = discoveryScreens(item);
            const displayName = names[item.symbol] || item.name || '';
            return (
            <div key={item.symbol} className="flex flex-wrap items-center gap-2 py-2.5">
              <span className="w-8 shrink-0 text-xs text-secondary-text">#{item.rank ?? '-'}</span>
              <div className="w-24 shrink-0">
                <TickerChip
                  symbol={item.symbol}
                  context={{
                    source: 'discovery',
                    metrics: [
                      ...(item.composite_score != null
                        ? [{ label: 'Composite score', value: item.composite_score.toFixed(0) }]
                        : []),
                      ...(item.candidate_score != null
                        ? [{ label: 'Candidate score', value: item.candidate_score.toFixed(0) }]
                        : []),
                      ...screens.map((screen) => ({
                        label: screen.label,
                        value: 'Passed',
                        term: screen.term,
                      })),
                    ],
                    note: item.reason || undefined,
                  }}
                />
                {displayName && displayName.toUpperCase() !== item.symbol.toUpperCase() ? (
                  <p className="truncate text-[11px] text-secondary-text">{displayName}</p>
                ) : null}
              </div>
              <span className="w-12 shrink-0 text-sm text-cyan">
                {item.composite_score != null ? item.composite_score.toFixed(0) : 'n/a'}
              </span>
              {item.checklist_status ? (
                <Badge variant={statusVariant(item.checklist_status)}>{item.checklist_status}</Badge>
              ) : null}
              <DiscoveryScreenBadges screens={screens} />
              <span className="min-w-0 flex-1 basis-full text-xs leading-5 text-secondary-text lg:basis-auto">
                {item.reason || ''}
                {item.entry_zone ? ` · entry ${item.entry_zone}` : ''}
              </span>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void addToWatchlist(item.symbol)}
                disabled={adds[item.symbol] === 'adding' || adds[item.symbol] === 'added'}
              >
                {adds[item.symbol] === 'added'
                  ? 'Added'
                  : adds[item.symbol] === 'adding'
                    ? 'Adding...'
                    : adds[item.symbol] === 'error'
                      ? 'Retry add'
                      : 'Add to watchlist'}
              </Button>
            </div>
            );
          })}
          {(discovery.constituents || []).length > DISCOVERY_ROW_LIMIT ? (
            <p className="pt-2 text-xs text-secondary-text">
              Showing the top {DISCOVERY_ROW_LIMIT} of {(discovery.constituents || []).length} qualified names.
            </p>
          ) : null}
        </div>
      )}
    </Card>
  );
};

const GuidePoint: React.FC<{ title: string; text: string }> = ({ title, text }) => (
  <div className="flex gap-2 rounded-lg border border-border/60 bg-elevated/45 px-3 py-3">
    <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
    <div>
      <p className="text-sm font-semibold text-foreground">{title}</p>
      <p className="mt-1 text-xs leading-5 text-secondary-text">{text}</p>
    </div>
  </div>
);

const PriceChart: React.FC<{ history: PriceHistoryResponse | null; error: string | null }> = ({ history, error }) => {
  const bars = useMemo(() => {
    const raw = history?.bars || [];
    return raw
      .filter((bar) => typeof bar.close === 'number' && Number.isFinite(bar.close))
      .slice(-126) as Array<{ date: string; close: number; volume: number }>;
  }, [history?.bars]);

  const chart = useMemo(() => buildChart(bars), [bars]);

  if (error || history?.error) {
    return (
      <div className="mb-4 rounded-lg border border-warning/25 bg-warning/10 px-3 py-3 text-sm text-warning">
        {error || history?.error}
      </div>
    );
  }

  if (!history || bars.length < 2 || !chart) {
    return (
      <div className="mb-4 flex h-36 items-center justify-center rounded-lg border border-border/60 bg-elevated/35 text-sm text-secondary-text">
        Loading price chart...
      </div>
    );
  }

  const first = bars[0].close;
  const last = bars[bars.length - 1].close;
  const change = ((last - first) / first) * 100;
  const lineColor = change >= 0 ? 'hsl(var(--success))' : 'hsl(var(--danger))';
  const chartQuality = asRecord(history.data_quality);

  return (
    <div className="mb-4 rounded-lg border border-border/60 bg-elevated/35 p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-xs uppercase tracking-normal text-secondary-text">6-month chart</p>
          <p className="mt-1 text-sm font-medium text-foreground">
            {formatPrice(first)} to {formatPrice(last)}
          </p>
        </div>
        <div className="text-right">
          <Badge variant={change >= 0 ? 'success' : 'danger'}>{change >= 0 ? '+' : ''}{change.toFixed(1)}%</Badge>
          <p className="mt-1 text-[11px] text-muted-foreground">{formatSource(history.source)} · {qualityLabel(chartQuality)}</p>
        </div>
      </div>
      <svg viewBox="0 0 320 120" className="h-36 w-full overflow-visible" role="img" aria-label={`${history.symbol} 6-month price chart`}>
        <line x1="0" y1="106" x2="320" y2="106" stroke="hsl(var(--border))" strokeWidth="1" />
        <line x1="0" y1="14" x2="320" y2="14" stroke="hsl(var(--border))" strokeWidth="1" opacity="0.35" />
        <path d={chart.closePath} fill="none" stroke={lineColor} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {chart.sma20Path ? <path d={chart.sma20Path} fill="none" stroke="hsl(var(--cyan))" strokeWidth="1.4" strokeOpacity="0.9" strokeDasharray="4 4" /> : null}
        {chart.sma50Path ? <path d={chart.sma50Path} fill="none" stroke="hsl(var(--warning))" strokeWidth="1.2" strokeOpacity="0.85" strokeDasharray="2 4" /> : null}
      </svg>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
        <span className="inline-flex items-center gap-1"><span className="h-2 w-4 rounded-full" style={{ backgroundColor: lineColor }} />Close</span>
        <span className="inline-flex items-center gap-1"><span className="h-0.5 w-4 bg-cyan" />SMA20</span>
        <span className="inline-flex items-center gap-1"><span className="h-0.5 w-4 bg-warning" />SMA50</span>
      </div>
    </div>
  );
};

const buildChart = (bars: Array<{ date: string; close: number }>) => {
  if (bars.length < 2) return null;
  const width = 320;
  const height = 120;
  const padX = 4;
  const padY = 12;
  const closes = bars.map((bar) => bar.close);
  const sma20 = rollingAverage(closes, 20);
  const sma50 = rollingAverage(closes, 50);
  const allValues = [...closes, ...sma20.filter(Boolean) as number[], ...sma50.filter(Boolean) as number[]];
  const min = Math.min(...allValues);
  const max = Math.max(...allValues);
  const range = Math.max(0.0001, max - min);
  const x = (index: number) => padX + (index / Math.max(1, bars.length - 1)) * (width - padX * 2);
  const y = (value: number) => height - padY - ((value - min) / range) * (height - padY * 2);
  const pathFrom = (values: Array<number | null>) => values
    .map((value, index) => {
      if (value === null) return '';
      return `${index === values.findIndex((item) => item !== null) ? 'M' : 'L'} ${x(index).toFixed(2)} ${y(value).toFixed(2)}`;
    })
    .filter(Boolean)
    .join(' ');

  return {
    closePath: pathFrom(closes),
    sma20Path: pathFrom(sma20),
    sma50Path: pathFrom(sma50),
  };
};

const rollingAverage = (values: number[], period: number): Array<number | null> => values.map((_, index) => {
  if (index + 1 < period) return null;
  const slice = values.slice(index + 1 - period, index + 1);
  return slice.reduce((sum, value) => sum + value, 0) / period;
});

const Info: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="rounded-lg border border-border/60 bg-elevated/50 p-3">
    <p className="text-[11px] uppercase tracking-normal text-secondary-text">{label}</p>
    <p className="mt-1 break-words text-sm font-medium text-foreground">{value}</p>
  </div>
);

export default SignalsPage;
