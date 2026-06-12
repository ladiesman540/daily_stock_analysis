import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, ChevronRight, RefreshCcw } from 'lucide-react';
import { AppPage, Badge, Button, Card, DiscoveryScreenBadges, InfoTip, InlineAlert, Input, TickerChip } from '../components/common';
import { discoveryScreens } from '../utils/discoveryScreens';
import { DashboardPanelHeader, DashboardStateBlock } from '../components/dashboard';
import { EquityCurve, MiniTrend, RegimeGauge, RRGScatter } from '../components/charts';
import {
  researchApi,
  type DailyBriefHeadline,
  type DailyBriefResponse,
  type RotationConstituent,
  type RotationLeaderEntry,
} from '../api/research';
import { portfolioApi } from '../api/portfolio';
import type { PortfolioEquityPoint, PortfolioSnapshotResponse } from '../types/portfolio';
import type { SymbolHistoryPoint } from '../stores/symbolDrawerStore';
import { useAgentChatStore } from '../stores/agentChatStore';
import { useSymbolNamesStore } from '../stores/symbolNamesStore';
import { cn } from '../utils/cn';

type BadgeVariant = 'default' | 'success' | 'warning' | 'danger' | 'info';

const ROTATION_HORIZONS = ['1D', '1M'] as const;

const QUADRANTS: Array<{ key: string; label: string; variant: BadgeVariant }> = [
  { key: 'leading', label: 'Leading', variant: 'success' },
  { key: 'improving', label: 'Improving', variant: 'info' },
  { key: 'weakening', label: 'Weakening', variant: 'warning' },
  { key: 'lagging', label: 'Lagging', variant: 'danger' },
];

const CYCLE_STAGES = [
  { key: 'early', label: 'Early', shortLabel: 'Early' },
  { key: 'mid', label: 'Mid', shortLabel: 'Mid' },
  { key: 'late', label: 'Late', shortLabel: 'Late' },
  { key: 'contraction', label: 'Contraction', shortLabel: 'Contr.' },
] as const;

const regimeVariant = (regime?: string | null): BadgeVariant => {
  const value = (regime || '').toLowerCase();
  if (value.includes('risk-on') || value.includes('bull')) return 'success';
  if (value.includes('risk-off') || value.includes('bear')) return 'danger';
  if (value.includes('neutral')) return 'info';
  return 'default';
};

const statusVariant = (status: string): BadgeVariant => {
  if (status === 'pass') return 'success';
  if (status === 'watchlist') return 'info';
  return 'default';
};

const money = (value?: number | null, currency = 'USD'): string =>
  value == null ? 'N/A' : `${currency} ${value.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

const signedMoney = (value?: number | null, currency = 'USD'): string =>
  value == null ? 'N/A' : `${value >= 0 ? '+' : '-'}${currency} ${Math.abs(value).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;

const rankMarker = (entry: { rank_change?: number | null; entered_top3_1m?: boolean }): string => {
  if (entry.entered_top3_1m) return '(new)';
  const change = entry.rank_change;
  if (change == null || change === 0) return '';
  return change > 0 ? `↑${change}` : `↓${Math.abs(change)}`;
};

const constituentMarker = (constituent: RotationConstituent): string => {
  const change = constituent.rank_change?.['1M'];
  if (change == null || change === 0) return '';
  return change > 0 ? ` ↑${change}` : ` ↓${Math.abs(change)}`;
};

const signedPct = (value?: number | null, digits = 2): string =>
  value == null ? 'N/A' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;

const impactChipVariant = (label?: string | null): BadgeVariant => {
  if (label === 'market_moving') return 'danger';
  if (label === 'sector') return 'warning';
  return 'default';
};

const impactChipText = (label?: string | null): string => {
  if (label === 'market_moving') return 'Market-moving';
  if (label === 'sector') return 'Sector';
  if (label === 'stock_specific') return 'Stock-specific';
  return label || 'Stock-specific';
};

const headlineAge = (item: DailyBriefHeadline): string => {
  const stamp = item.published_at || item.fetched_at;
  if (!stamp) return '';
  const ms = Date.now() - new Date(stamp).getTime();
  if (!Number.isFinite(ms) || ms < 0) return '';
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return '<1h ago';
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days > 7) return stamp.slice(0, 10);
  return `${days}d ago`;
};

/** Tiny diverging bar for vs-SPY outperformance (pp), centered at 0, clamped at ±5pp. */
const DeltaBar: React.FC<{ value: number }> = ({ value }) => {
  const widthPct = Math.min(Math.abs(value) / 5, 1) * 50;
  return (
    <span aria-hidden className="relative inline-block h-1.5 w-14 shrink-0 rounded-full bg-border/40">
      <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
      <span
        className={cn('absolute inset-y-0 rounded-full', value >= 0 ? 'bg-success' : 'bg-danger')}
        style={value >= 0 ? { left: '50%', width: `${widthPct}%` } : { right: '50%', width: `${widthPct}%` }}
      />
    </span>
  );
};

interface FreshnessBadgeProps {
  asOf?: string | null;
  ageDays?: number | null;
  freshness?: string | null;
}

/** Per-card "as of / N days old" badge — amber when the brief flags the data stale. */
const FreshnessBadge: React.FC<FreshnessBadgeProps> = ({ asOf, ageDays, freshness }) => {
  if (!asOf) {
    return <Badge variant="default">No data</Badge>;
  }
  const age = ageDays != null && ageDays > 0 ? ` · ${ageDays}d old` : '';
  return (
    <Badge variant={freshness === 'stale' ? 'warning' : 'success'}>
      as of {asOf.slice(0, 10)}{age}
    </Badge>
  );
};

interface BriefCardProps {
  eyebrow: string;
  title: string;
  /** When set, the card title links to its detail page (chevron affordance). */
  to?: string;
  loading: boolean;
  section?: { status: string; as_of?: string | null; age_days?: number | null; freshness?: string | null } | null;
  missingText: string;
  children: React.ReactNode;
}

const BriefCard: React.FC<BriefCardProps> = ({ eyebrow, title, to, loading, section, missingText, children }) => (
  <Card padding="md">
    <DashboardPanelHeader
      eyebrow={eyebrow}
      title={to ? (
        <Link to={to} className="inline-flex items-center gap-1 transition-colors hover:text-primary">
          {title}
          <ChevronRight aria-hidden className="h-4 w-4 text-muted-text" />
        </Link>
      ) : title}
      actions={!loading && section?.status === 'completed' ? (
        <FreshnessBadge asOf={section.as_of} ageDays={section.age_days} freshness={section.freshness} />
      ) : null}
    />
    {loading ? (
      <DashboardStateBlock title="Loading..." loading compact />
    ) : !section || section.status !== 'completed' ? (
      <DashboardStateBlock title="No snapshot yet" description={missingText} compact />
    ) : (
      children
    )}
  </Card>
);

const TodayPage: React.FC = () => {
  const navigate = useNavigate();
  const [brief, setBrief] = useState<DailyBriefResponse | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioSnapshotResponse | null>(null);
  const [portfolioError, setPortfolioError] = useState(false);
  const [equityHistory, setEquityHistory] = useState<PortfolioEquityPoint[]>([]);
  const [sparklines, setSparklines] = useState<Record<string, SymbolHistoryPoint[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [watchlistAdds, setWatchlistAdds] = useState<Record<string, 'adding' | 'added' | 'error'>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [briefResult, portfolioResult, equityResult] = await Promise.allSettled([
        researchApi.getDailyBrief(),
        portfolioApi.getSnapshot(),
        portfolioApi.getEquityHistory({ days: 90 }),
      ]);
      if (briefResult.status === 'fulfilled') {
        setBrief(briefResult.value);
      } else {
        setError('Daily brief failed to load. Check that the API server is running.');
      }
      if (portfolioResult.status === 'fulfilled') {
        setPortfolio(portfolioResult.value);
        setPortfolioError(false);
      } else {
        setPortfolioError(true);
      }
      if (equityResult.status === 'fulfilled') {
        setEquityHistory(equityResult.value.points || []);
      }
      // One batch sparkline call for every ticker chip on the page (fail-open:
      // a sparkline failure must never affect the cards).
      if (briefResult.status === 'fulfilled') {
        const value = briefResult.value;
        const symbols = Array.from(new Set(
          [
            ...(value.signals?.top || []).slice(0, 5).map((item) => item.symbol),
            ...(value.discovery?.top || []).slice(0, 5).map((item) => item.symbol),
            ...(value.down_day_rs?.stocks_holding_up || []).slice(0, 8).map((item) => item.symbol),
          ].filter(Boolean),
        ));
        // One batch name lookup for every symbol on the page (fail-open inside
        // the store): signal/idea/down-day rows, rotation hot/not, playbook,
        // and the RRG constituents.
        const nameSymbols = Array.from(new Set(
          [
            ...symbols,
            ...(value.down_day_rs?.sectors_holding_up || []).map((item) => item.symbol),
            ...Object.values(value.rotation?.leaders || {}).flatMap((block) => [
              ...(block.leaders || []).map((entry) => entry.symbol),
              ...(block.laggards || []).map((entry) => entry.symbol),
            ]),
            ...(value.cycle?.playbook || []),
            ...(value.rotation?.constituents || []).map((item) => item.symbol),
          ].filter(Boolean),
        ));
        if (nameSymbols.length) {
          void useSymbolNamesStore.getState().loadNames(nameSymbols);
        }
        if (symbols.length) {
          try {
            const response = await researchApi.getSparklines(symbols, 30);
            setSparklines(response.series || {});
          } catch {
            // Chips render without sparklines.
          }
        }
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    document.title = 'Today - DSA';
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // An already-open tab (especially a restored mobile tab) never refetches on
  // its own; reload the brief whenever the tab regains focus, throttled so
  // rapid tab-switching doesn't hammer the API.
  useEffect(() => {
    let lastAutoLoad = Date.now();
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      if (Date.now() - lastAutoLoad < 60_000) return;
      lastAutoLoad = Date.now();
      void load();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [load]);

  const addToWatchlist = useCallback(async (symbol: string) => {
    setWatchlistAdds((prev) => ({ ...prev, [symbol]: 'adding' }));
    try {
      await researchApi.addWatchlistSymbol(symbol, 'Added from discovery');
      setWatchlistAdds((prev) => ({ ...prev, [symbol]: 'added' }));
    } catch {
      setWatchlistAdds((prev) => ({ ...prev, [symbol]: 'error' }));
    }
  }, []);

  const askAboutToday = useCallback(() => {
    const prompt = question.trim();
    if (!prompt) return;
    useAgentChatStore.getState().setPendingPrompt(prompt);
    navigate('/chat');
  }, [navigate, question]);

  const askWhyHoldingUp = useCallback(() => {
    const section = brief?.down_day_rs;
    if (!section) return;
    const symbols = (section.stocks_holding_up || []).slice(0, 3).map((item) => item.symbol);
    const names = symbols.length
      ? symbols.join(', ')
      : (section.sectors_holding_up || []).slice(0, 3).map((item) => item.symbol).join(', ');
    if (!names) return;
    const spy = section.spy_return_pct != null ? `${section.spy_return_pct.toFixed(2)}%` : 'down';
    useAgentChatStore.getState().setPendingPrompt(`Why did ${names} hold up on today's ${spy} SPY day?`);
    navigate('/chat');
  }, [brief, navigate]);

  const regime = brief?.regime;
  const latest = regime?.latest;
  const previous = regime?.previous;
  const scoreDelta = latest?.score != null && previous?.score != null ? latest.score - previous.score : null;
  const cycle = brief?.cycle;
  const rotation = brief?.rotation;
  const signals = brief?.signals;
  const discovery = brief?.discovery;
  const downDay = brief?.down_day_rs;
  const headlines = brief?.headlines;
  const scorecard = brief?.scorecard;
  const scorecardFlagged = scorecard?.real?.flagged;
  const scorecardBaseline = scorecard?.real?.baseline;
  const scorecardDecided = scorecardFlagged?.decided ?? 0;
  const scorecardWindow = scorecard?.window_days ?? 90;
  const scorecardHitPct = scorecard?.hit_threshold_pct ?? 20;
  const scorecardSim = scorecard?.simulated?.flagged;
  // Prominent only when the snapshot covers the latest market session AND it was a down day.
  const downDayActive = Boolean(
    downDay?.status === 'completed' && downDay.triggered && downDay.is_latest_session,
  );
  const downDayHasNames = Boolean(
    (downDay?.stocks_holding_up || []).length || (downDay?.sectors_holding_up || []).length,
  );
  const constituents = rotation?.constituents || [];
  const phase = cycle?.phase ? cycle.phase.charAt(0).toUpperCase() + cycle.phase.slice(1) : 'Unknown';
  const phaseKey = (cycle?.phase || '').toLowerCase();

  const regimeTrend = (regime?.history || [])
    .filter((point) => point.as_of && point.score != null)
    .map((point) => ({ date: point.as_of as string, value: point.score as number }));
  const breadthTrend = (regime?.breadth_trend || [])
    .filter((point) => point.as_of && point.pct_above_50dma != null)
    .map((point) => ({ date: point.as_of as string, value: point.pct_above_50dma as number }));
  const sectorRrgPoints = constituents
    .filter((item) => item.group === 'sectors' && item.rs_ratio != null && item.rs_momentum != null)
    .map((item) => ({
      symbol: item.symbol,
      rsRatio: item.rs_ratio as number,
      rsMomentum: item.rs_momentum as number,
      quadrant: item.quadrant,
    }));

  const leaderChip = (entry: RotationLeaderEntry, horizon: string, kind: string) => {
    const marker = rankMarker(entry);
    return (
      <span key={`${kind}-${entry.symbol}`} className="inline-flex items-center gap-1">
        <TickerChip
          symbol={entry.symbol}
          change={entry.return_pct ?? undefined}
          context={{
            source: 'rotation',
            metrics: [{ label: `${horizon} return`, value: signedPct(entry.return_pct, 1) }],
          }}
        />
        {marker ? <span className="text-[11px] text-muted-text">{marker}</span> : null}
      </span>
    );
  };

  return (
    <AppPage>
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4" data-testid="today-page">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <p className="label-uppercase">Morning routine</p>
            <h1 className="mt-1 text-2xl font-semibold text-foreground">Today</h1>
          </div>
          <Button variant="secondary" onClick={() => void load()} isLoading={loading} loadingText="Loading">
            <RefreshCcw className="h-4 w-4" />
            Refresh
          </Button>
        </div>

        {error ? <InlineAlert variant="danger" title="Brief error" message={error} /> : null}

        <BriefCard
          eyebrow="1 · Regime"
          title="Market regime"
          loading={loading}
          section={regime}
          missingText="No market data yet — it arrives automatically with the nightly update (6:00 PM)."
        >
          <div className="space-y-3 text-sm text-foreground">
            <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
              <RegimeGauge score={latest?.score} width={150} />
              {regimeTrend.length >= 2 ? (
                <div className="min-w-[140px] max-w-full flex-1">
                  <p className="text-[11px] text-muted-text">14-day score trend</p>
                  <MiniTrend data={regimeTrend} name="Score" className="h-12 w-full" />
                </div>
              ) : (
                <p className="text-[11px] text-muted-text">Trend appears after 2+ daily snapshots.</p>
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={regimeVariant(latest?.regime)} size="md">{latest?.regime || 'Unknown'}</Badge>
              <InfoTip term="regime_score">
                <span className="font-semibold">{latest?.score != null ? `${latest.score}/100` : 'N/A'}</span>
              </InfoTip>
              {scoreDelta != null ? (
                <span className={scoreDelta >= 0 ? 'text-success text-xs' : 'text-danger text-xs'}>
                  {scoreDelta >= 0 ? '+' : ''}{scoreDelta.toFixed(1)} vs {previous?.as_of}
                </span>
              ) : null}
              <span className="text-xs text-secondary-text">confidence {latest?.confidence || 'n/a'}</span>
            </div>
            {latest?.vix != null ? (
              <p>
                <InfoTip term="vix">
                  <span>
                    VIX {latest.vix}
                    {latest.vix3m != null ? ` / VIX3M ${latest.vix3m}` : ''}
                  </span>
                </InfoTip>
                {' '}
                <InfoTip term="vix_term_structure">
                  <span className={latest.term_inverted ? 'text-warning' : 'text-secondary-text'}>
                    (term structure {latest.term_inverted ? 'inverted' : 'normal'})
                  </span>
                </InfoTip>
              </p>
            ) : null}
            {latest?.breadth_above_50dma_pct != null ? (
              <div className="space-y-1">
                <p>
                  <InfoTip term="breadth_50dma">
                    <span>Breadth: {latest.breadth_above_50dma_pct}% of tracked ETFs above their 50DMA</span>
                  </InfoTip>
                </p>
                {breadthTrend.length >= 2 ? (
                  <MiniTrend
                    data={breadthTrend}
                    name="% above 50DMA"
                    referenceValue={50}
                    valueFormatter={(value) => `${value.toFixed(0)}%`}
                    className="h-10 w-full"
                  />
                ) : (
                  <p className="text-[11px] text-muted-text">Trend appears after 2+ daily snapshots.</p>
                )}
              </div>
            ) : null}
            {previous?.regime && previous.regime !== latest?.regime ? (
              <p className="text-warning">Regime changed from {previous.regime} on {previous.as_of}</p>
            ) : null}
          </div>
        </BriefCard>

        <BriefCard
          eyebrow="2 · Cycle"
          title="Macro cycle"
          loading={loading}
          section={cycle}
          missingText="No cycle read yet — it arrives automatically with the nightly update (6:00 PM)."
        >
          <div className="space-y-3 text-sm text-foreground">
            <div className="flex flex-wrap items-center gap-2">
              <InfoTip term="cycle_phase">
                <Badge variant="info" size="md">{phase} cycle</Badge>
              </InfoTip>
              <span className="text-xs text-secondary-text">confidence {cycle?.confidence || 'n/a'}</span>
            </div>
            <div className="flex gap-1" role="img" aria-label={`Cycle stage: ${phase}`}>
              {CYCLE_STAGES.map((stage) => (
                <div key={stage.key} className="min-w-0 flex-1">
                  <div
                    className={cn(
                      'h-1.5 rounded-full',
                      stage.key === phaseKey ? 'bg-primary' : 'bg-border/60',
                    )}
                  />
                  <p
                    className={cn(
                      'mt-1 truncate text-center text-[11px]',
                      stage.key === phaseKey ? 'font-semibold text-foreground' : 'text-muted-text',
                    )}
                  >
                    <span className="sm:hidden">{stage.shortLabel}</span>
                    <span className="hidden sm:inline">{stage.label}</span>
                  </p>
                </div>
              ))}
            </div>
            {(cycle?.playbook || []).length ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-secondary-text">{phase}-cycle playbook:</span>
                {(cycle?.playbook || []).map((symbol) => (
                  <TickerChip key={symbol} symbol={symbol} context={{ source: 'generic' }} />
                ))}
              </div>
            ) : null}
            {cycle?.divergence ? (
              <p className="text-warning">{cycle.divergence_note || 'Tape leadership diverges from the phase playbook.'}</p>
            ) : null}
          </div>
        </BriefCard>

        <BriefCard
          eyebrow="3 · Rotation"
          title="Sector rotation"
          to="/rotation"
          loading={loading}
          section={rotation}
          missingText="No sector rankings yet — they arrive automatically with the nightly update (6:00 PM)."
        >
          <div className="space-y-3 text-sm">
            {sectorRrgPoints.length ? (
              <RRGScatter points={sectorRrgPoints} className="h-64 w-full" />
            ) : null}
            <div className="space-y-1.5">
              {ROTATION_HORIZONS.map((horizon) => {
                const block = rotation?.leaders?.[horizon];
                const tops = block?.leaders || [];
                const bottoms = block?.laggards || [];
                if (!tops.length) return null;
                return (
                  <div key={horizon} className="flex flex-wrap items-center gap-1.5">
                    <span className="shrink-0 text-xs font-semibold text-foreground">{horizon}</span>
                    <span className="text-xs text-success">hot:</span>
                    {tops.map((entry) => leaderChip(entry, horizon, 'hot'))}
                    <span className="text-xs text-danger">not:</span>
                    {bottoms.map((entry) => leaderChip(entry, horizon, 'not'))}
                  </div>
                );
              })}
            </div>
            {constituents.length ? (
              <div className="space-y-1.5 border-t border-border/60 pt-3">
                <p className="label-uppercase">
                  <InfoTip term="rrg"><span>RRG quadrants</span></InfoTip> (1M rank moves)
                </p>
                {QUADRANTS.map(({ key, label, variant }) => {
                  const members = constituents
                    .filter((item) => item.quadrant === key)
                    .sort((a, b) => (a.composite_rank ?? 999) - (b.composite_rank ?? 999))
                    .slice(0, 5);
                  if (!members.length) return null;
                  return (
                    <div key={key} className="flex flex-wrap items-center gap-2">
                      <InfoTip term={`quadrant_${key}`}>
                        <Badge variant={variant}>{label}</Badge>
                      </InfoTip>
                      <span className="text-foreground">
                        {members.map((item) => `${item.symbol}${constituentMarker(item)}`).join(', ')}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : null}
          </div>
        </BriefCard>

        <BriefCard
          eyebrow="4 · Signals"
          title="Top signals"
          to="/signals"
          loading={loading}
          section={signals}
          missingText="No signal scan persisted yet. Run a signal scan from the Signals page."
        >
          {(signals?.top || []).length ? (
            <div className="space-y-2 text-sm">
              {(signals?.top || []).map((item, index) => (
                <div key={item.symbol} className="flex flex-wrap items-center gap-2">
                  <span className="w-6 shrink-0 text-xs text-secondary-text">#{index + 1}</span>
                  <TickerChip
                    symbol={item.symbol}
                    sparkline={sparklines[item.symbol]}
                    showName
                    context={{
                      source: 'signal',
                      metrics: [
                        {
                          label: 'Score',
                          value: item.candidate_score != null ? item.candidate_score.toFixed(0) : 'N/A',
                        },
                        { label: 'Checklist', value: item.checklist_status, term: 'checklist_status' },
                        ...(item.entry_zone
                          ? [{ label: 'Entry zone', value: item.entry_zone, term: 'entry_zone' }]
                          : []),
                      ],
                    }}
                  />
                  <span className="text-foreground">{item.candidate_score != null ? item.candidate_score.toFixed(0) : 'N/A'}</span>
                  <InfoTip term="checklist_status">
                    <Badge variant={statusVariant(item.checklist_status)}>{item.checklist_status}</Badge>
                  </InfoTip>
                  {item.entry_zone ? (
                    <InfoTip term="entry_zone">
                      <span className="text-xs text-secondary-text">entry {item.entry_zone}</span>
                    </InfoTip>
                  ) : null}
                </div>
              ))}
              {signals?.unscored_count ? (
                <p className="text-xs text-secondary-text">{signals.unscored_count} symbols unscored (data unavailable)</p>
              ) : null}
            </div>
          ) : (
            <DashboardStateBlock title="No scored candidates in the latest scan." compact />
          )}
        </BriefCard>

        <BriefCard
          eyebrow="5 · New ideas"
          title="Discovery ideas"
          to="/signals"
          loading={loading}
          section={discovery}
          missingText="No new ideas yet — they arrive automatically with the nightly update (6:00 PM)."
        >
          {(discovery?.top || []).length ? (
            <div className="space-y-2 text-sm">
              {(discovery?.top || []).slice(0, 5).map((item, index) => {
                const screens = discoveryScreens(item);
                return (
                  <div key={item.symbol} className="flex flex-wrap items-center gap-2">
                    <span className="w-6 shrink-0 text-xs text-secondary-text">#{index + 1}</span>
                    <TickerChip
                      symbol={item.symbol}
                      sparkline={sparklines[item.symbol]}
                      showName
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
                    {item.candidate_score != null ? (
                      <span className="text-foreground">{item.candidate_score.toFixed(0)}</span>
                    ) : null}
                    {item.checklist_status ? (
                      <Badge variant={statusVariant(item.checklist_status)}>{item.checklist_status}</Badge>
                    ) : null}
                    <DiscoveryScreenBadges screens={screens} />
                    <span className="min-w-0 flex-1 basis-full text-xs text-secondary-text sm:basis-auto">
                      {item.reason || ''}
                    </span>
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => void addToWatchlist(item.symbol)}
                      disabled={watchlistAdds[item.symbol] === 'adding' || watchlistAdds[item.symbol] === 'added'}
                    >
                      {watchlistAdds[item.symbol] === 'added'
                        ? 'Added'
                        : watchlistAdds[item.symbol] === 'adding'
                          ? 'Adding...'
                          : watchlistAdds[item.symbol] === 'error'
                            ? 'Retry add'
                            : 'Add to watchlist'}
                    </Button>
                  </div>
                );
              })}
            </div>
          ) : (
            <DashboardStateBlock title="No new ideas passed the discovery screens today." compact />
          )}
        </BriefCard>

        <BriefCard
          eyebrow="6 · Down day"
          title="What's holding up"
          loading={loading}
          section={downDay}
          missingText="No down-day data yet — it arrives automatically with the nightly update (6:00 PM)."
        >
          {downDayActive ? (
            <div className="space-y-2 text-sm">
              <p className="text-foreground">
                SPY <span className="font-semibold text-danger">{signedPct(downDay?.spy_return_pct)}</span> —
                {' '}
                <InfoTip term="down_day_rule">
                  <span>names green on the day or beating SPY by ≥1pp:</span>
                </InfoTip>
              </p>
              {(downDay?.sectors_holding_up || []).length ? (
                <p className="text-foreground">
                  <span className="font-semibold">Sectors:</span>{' '}
                  {(downDay?.sectors_holding_up || [])
                    .map((item) => `${item.symbol}${item.return_1d_pct != null ? ` ${signedPct(item.return_1d_pct, 1)}` : ''}`)
                    .join(', ')}
                </p>
              ) : (
                <p className="text-secondary-text">Sector breakdown unavailable for this session.</p>
              )}
              {(downDay?.stocks_holding_up || []).length ? (
                <div className="space-y-1.5">
                  {(downDay?.stocks_holding_up || []).slice(0, 8).map((item) => (
                    <div key={item.symbol} className="flex flex-wrap items-center gap-2">
                      <TickerChip
                        symbol={item.symbol}
                        sparkline={sparklines[item.symbol]}
                        showName
                        context={{
                          source: 'down_day',
                          metrics: [
                            { label: 'Day return', value: signedPct(item.return_1d_pct) },
                            ...(item.rs_vs_spy_pp != null
                              ? [{
                                  label: 'vs SPY',
                                  value: `${item.rs_vs_spy_pp >= 0 ? '+' : ''}${item.rs_vs_spy_pp.toFixed(1)}pp`,
                                  term: 'down_day_rule',
                                }]
                              : []),
                          ],
                        }}
                      />
                      <span className={item.return_1d_pct != null && item.return_1d_pct >= 0 ? 'text-success' : 'text-danger'}>
                        {signedPct(item.return_1d_pct)}
                      </span>
                      {item.rs_vs_spy_pp != null ? (
                        <>
                          <DeltaBar value={item.rs_vs_spy_pp} />
                          <span className="text-xs text-secondary-text">
                            {item.rs_vs_spy_pp >= 0 ? '+' : ''}{item.rs_vs_spy_pp.toFixed(1)}pp vs SPY
                          </span>
                        </>
                      ) : null}
                      {item.source ? (
                        <Badge variant={item.source === 'watchlist' ? 'info' : 'default'}>{item.source}</Badge>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-secondary-text">No tracked stocks held up — broad selloff.</p>
              )}
              {downDayHasNames ? (
                <button
                  type="button"
                  className="inline-flex items-center gap-1 text-xs text-secondary-text transition-colors hover:text-foreground"
                  onClick={askWhyHoldingUp}
                >
                  Ask why
                  <ArrowRight className="h-3.5 w-3.5" />
                </button>
              ) : null}
            </div>
          ) : (
            <p className="text-sm text-secondary-text">
              {downDay?.last_down_day
                ? `Last down day was ${downDay.last_down_day}.`
                : 'No down day in the recent SPY history.'}
            </p>
          )}
        </BriefCard>

        <BriefCard
          eyebrow="7 · Headlines"
          title="Market-moving news"
          loading={loading}
          section={headlines}
          missingText="No scored headlines today — news is rated automatically with the nightly update (6:00 PM)."
        >
          {(headlines?.top || []).length ? (
            <div className="space-y-2 text-sm">
              {(headlines?.top || []).map((item) => (
                <div key={item.id} className="flex flex-wrap items-center gap-2">
                  <InfoTip term="impact_label">
                    <Badge variant={impactChipVariant(item.impact_label)}>{impactChipText(item.impact_label)}</Badge>
                  </InfoTip>
                  <span className="min-w-0 flex-1 text-foreground">{item.title}</span>
                  <span className="shrink-0 text-xs text-secondary-text">
                    {[item.source, headlineAge(item)].filter(Boolean).join(' · ')}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <DashboardStateBlock title="No scored headlines today." compact />
          )}
        </BriefCard>

        <Card padding="md">
          <DashboardPanelHeader
            eyebrow="8 · Portfolio"
            title={
              <span className="inline-flex items-center gap-1.5">
                <Link to="/portfolio" className="inline-flex items-center gap-1 transition-colors hover:text-primary">
                  Paper portfolio
                  <ChevronRight aria-hidden className="h-4 w-4 text-muted-text" />
                </Link>
                <InfoTip term="paper_portfolio" icon />
              </span>
            }
            actions={!loading && portfolio ? <FreshnessBadge asOf={portfolio.asOf} /> : null}
          />
          {loading ? (
            <DashboardStateBlock title="Loading..." loading compact />
          ) : portfolioError || !portfolio ? (
            <DashboardStateBlock
              title="Portfolio unavailable"
              description="Snapshot failed to load. Open the Portfolio page for details."
              compact
            />
          ) : portfolio.accountCount === 0 ? (
            <DashboardStateBlock
              title="No portfolio accounts yet"
              description="Create an account on the Portfolio page to track the paper book here."
              compact
            />
          ) : (
            <div className="space-y-2 text-sm text-foreground">
              {equityHistory.length >= 2 ? (
                <EquityCurve
                  data={equityHistory.map((point) => ({ date: point.date, equity: point.equity }))}
                  currency={equityHistory[0]?.currency || portfolio.currency}
                  className="h-36 w-full"
                />
              ) : null}
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                <div>
                  <p className="text-xs text-secondary-text">Equity</p>
                  <p className="font-semibold">{money(portfolio.totalEquity, portfolio.currency)}</p>
                </div>
                <div>
                  <p className="text-xs text-secondary-text">Cash</p>
                  <p className="font-semibold">{money(portfolio.totalCash, portfolio.currency)}</p>
                </div>
                <div>
                  <p className="text-xs text-secondary-text">Unrealized</p>
                  <p className={portfolio.unrealizedPnl >= 0 ? 'font-semibold text-success' : 'font-semibold text-danger'}>
                    {signedMoney(portfolio.unrealizedPnl, portfolio.currency)}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-secondary-text">Realized</p>
                  <p className={portfolio.realizedPnl >= 0 ? 'font-semibold text-success' : 'font-semibold text-danger'}>
                    {signedMoney(portfolio.realizedPnl, portfolio.currency)}
                  </p>
                </div>
              </div>
              <button
                type="button"
                className="inline-flex items-center gap-1 text-xs text-secondary-text transition-colors hover:text-foreground"
                onClick={() => navigate('/portfolio')}
              >
                Open portfolio
                <ArrowRight className="h-3.5 w-3.5" />
              </button>
            </div>
          )}
        </Card>

        <BriefCard
          eyebrow="9 · Scorecard"
          title="Is the system any good?"
          to="/scorecard"
          loading={loading}
          section={scorecard}
          missingText="No graded flags yet — the scorecard fills in automatically with the nightly update (6:00 PM)."
        >
          <div className="space-y-2 text-sm">
            {scorecardDecided > 0 ? (
              <>
                <p className="text-foreground">
                  <InfoTip term="hit_rate">
                    <span className="text-2xl font-semibold tabular-nums">
                      {scorecardFlagged?.batting_average != null
                        ? `${(scorecardFlagged.batting_average * 100).toFixed(0)}%`
                        : 'N/A'}
                    </span>
                  </InfoTip>
                  {' '}
                  <span className="text-secondary-text">
                    of decided flags hit +{scorecardHitPct.toFixed(0)}% within {scorecardWindow} days
                    {' '}({scorecardFlagged?.hits ?? 0} of {scorecardDecided}; {scorecardFlagged?.open ?? 0} still open)
                  </span>
                </p>
                {scorecardFlagged?.batting_average != null && scorecardBaseline?.batting_average != null ? (
                  <p className="text-xs text-secondary-text">
                    Flags {(scorecardFlagged.batting_average * 100).toFixed(0)}%
                    {' · '}Universe baseline {(scorecardBaseline.batting_average * 100).toFixed(0)}%
                    {' · '}Edge {(scorecard?.real?.edge ?? 0) >= 0 ? '+' : ''}
                    {((scorecard?.real?.edge ?? 0) * 100).toFixed(0)}pts
                  </p>
                ) : null}
              </>
            ) : (
              <>
                <p className="text-secondary-text">
                  {scorecardFlagged?.open ?? 0} flags open — outcomes mature over the next {scorecardWindow} days.
                </p>
                {scorecardSim?.batting_average != null ? (
                  <p className="text-xs text-secondary-text">
                    Simulated backtest: {(scorecardSim.batting_average * 100).toFixed(0)}% — an optimistic ceiling
                    (delisted losers are missing).
                  </p>
                ) : null}
              </>
            )}
          </div>
        </BriefCard>

        <Card padding="md">
          <DashboardPanelHeader eyebrow="10 · Ask" title="Ask about today" />
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-0 flex-1">
              <Input
                label="Question"
                placeholder="e.g. What changed in the regime since yesterday?"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                className="h-10 w-full"
                onKeyDown={(event) => {
                  if (event.key === 'Enter') askAboutToday();
                }}
              />
            </div>
            <Button onClick={askAboutToday} disabled={!question.trim()}>
              Ask
              <ArrowRight className="h-4 w-4" />
            </Button>
          </div>
        </Card>
      </div>
    </AppPage>
  );
};

export default TodayPage;
