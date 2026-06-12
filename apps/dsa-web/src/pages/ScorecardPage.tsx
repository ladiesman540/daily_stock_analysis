import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Bar, BarChart, LabelList, ResponsiveContainer, XAxis, YAxis } from 'recharts';
import { researchApi, type ScorecardFlagRow, type ScorecardGroupStats, type ScorecardPopulationBlock, type ScorecardSummaryResponse } from '../api/research';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, AppPage, Badge, Button, Card, DiscoveryScreenBadges, InfoTip, Pagination, Select, TickerChip } from '../components/common';
import { DashboardPanelHeader, DashboardStateBlock } from '../components/dashboard';
import { CHART_COLORS } from '../components/charts/chartTheme';
import { discoveryScreensFromKeys } from '../utils/discoveryScreens';

const PAGE_SIZE = 20;

/** Screen-key -> human label, reusing the badge copy from discoveryScreens. */
const screenLabel = (key: string): string => discoveryScreensFromKeys([key])[0]?.label ?? key;

/** SCORE_BANDS order from the backend (scorecard_service.SCORE_BANDS). */
const SCORE_BAND_ORDER = ['90+', '80-90', '70-80', '60-70', '<60'];

const pct = (value?: number | null, digits = 0): string =>
  value == null ? '--' : `${(value * 100).toFixed(digits)}%`;

const signedPct = (value?: number | null, digits = 1): string =>
  value == null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;

const num = (value?: number | null, digits = 0): string =>
  value == null ? '--' : value.toFixed(digits);

const statusBadge = (status: string) => {
  switch (status) {
    case 'hit':
      return <Badge variant="success">hit</Badge>;
    case 'flop':
      return <Badge variant="danger">flop</Badge>;
    case 'expired':
      return (
        <InfoTip
          title="Expired"
          body="The 90-day window (plus a 7-day slack) ended without touching +20% or -20%. Counts against the batting average as a non-hit."
        >
          <Badge variant="warning">expired</Badge>
        </InfoTip>
      );
    default:
      return <Badge variant="default">{status}</Badge>;
  }
};

const SIMULATED_DISCLOSURE =
  "Simulated flags re-run today's screens on historical prices using today's stock universe. " +
  'Delisted losers are missing, so treat this as an optimistic ceiling.';

/** "Flags: 34% · Universe baseline: 18% · Edge +16pts" framing line. */
const edgeLine = (block?: ScorecardPopulationBlock | null): string | null => {
  const flagged = block?.flagged?.batting_average;
  const baseline = block?.baseline?.batting_average;
  if (flagged == null || baseline == null) return null;
  const edge = (block?.edge ?? flagged - baseline) * 100;
  return `Flags: ${pct(flagged)} · Universe baseline: ${pct(baseline)} · Edge ${edge >= 0 ? '+' : ''}${edge.toFixed(0)}pts`;
};

const Stat: React.FC<{ label: string; value: string; tone?: 'success' | 'danger' | 'default' }> = ({ label, value, tone = 'default' }) => (
  <div>
    <p className="text-xs text-secondary-text">{label}</p>
    <p className={`font-semibold ${tone === 'success' ? 'text-success' : tone === 'danger' ? 'text-danger' : 'text-foreground'}`}>{value}</p>
  </div>
);

/** Shared metric body for the real headline strip and the simulated block. */
const PopulationMetrics: React.FC<{ block: ScorecardPopulationBlock; windowDays: number; hitPct: number }> = ({ block, windowDays, hitPct }) => {
  const flagged = block.flagged;
  const decided = flagged.decided ?? 0;
  if (!decided) {
    return (
      <p className="text-sm text-secondary-text">
        {flagged.open ?? 0} flags open — outcomes mature over the next {windowDays} days.
      </p>
    );
  }
  const line = edgeLine(block);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <InfoTip term="hit_rate">
          <span className="text-4xl font-semibold tabular-nums text-foreground">{pct(flagged.batting_average)}</span>
        </InfoTip>
        <span className="text-sm text-secondary-text">
          of decided flags hit +{hitPct.toFixed(0)}% within {windowDays} days ({flagged.hits} of {decided})
        </span>
      </div>
      {line ? <p className="text-sm text-foreground">{line}</p> : null}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Decided" value={String(decided)} />
        <Stat label="Still open" value={String(flagged.open ?? 0)} />
        <Stat label="Avg days to hit" value={num(flagged.avg_days_to_hit, 1)} />
        <Stat
          label="Avg max gain (decided)"
          value={signedPct(flagged.avg_max_gain_pct)}
          tone={flagged.avg_max_gain_pct != null && flagged.avg_max_gain_pct >= 0 ? 'success' : 'danger'}
        />
      </div>
    </div>
  );
};

/** Compact breakdown table shared by the score-band and by-month views. */
const GroupTable: React.FC<{ title: string; rows: Array<[string, ScorecardGroupStats]> }> = ({ title, rows }) => (
  <div>
    <p className="label-uppercase mb-2">{title}</p>
    {rows.length ? (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[320px] text-left text-sm">
          <thead>
            <tr className="border-b border-border/60 text-xs uppercase tracking-normal text-secondary-text">
              <th className="py-2 pr-3">{title === 'By month' ? 'Month' : 'Score band'}</th>
              <th className="py-2 pr-3">Flags</th>
              <th className="py-2 pr-3">Decided</th>
              <th className="py-2">Hit rate</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, stats]) => (
              <tr key={key} className="border-b border-border/40">
                <td className="py-2 pr-3 font-medium text-foreground">{key}</td>
                <td className="py-2 pr-3 text-secondary-text">{stats.total}</td>
                <td className="py-2 pr-3 text-secondary-text">{stats.decided}</td>
                <td className="py-2 tabular-nums text-foreground">
                  {stats.batting_average != null ? `${pct(stats.batting_average)} (${stats.hit}/${stats.decided})` : '--'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <p className="text-xs text-secondary-text">No decided flags yet.</p>
    )}
  </div>
);

const ScorecardPage: React.FC = () => {
  const [summary, setSummary] = useState<ScorecardSummaryResponse | null>(null);
  const [loadingSummary, setLoadingSummary] = useState(true);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);

  const [flagRows, setFlagRows] = useState<ScorecardFlagRow[]>([]);
  const [totalFlags, setTotalFlags] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [loadingFlags, setLoadingFlags] = useState(false);
  const [statusFilter, setStatusFilter] = useState('all');
  const [populationFilter, setPopulationFilter] = useState('real');

  const [breakdownSource, setBreakdownSource] = useState<'real' | 'simulated'>('real');
  const [bootstrapState, setBootstrapState] = useState<'idle' | 'starting' | 'started' | 'already_running' | 'error'>('idle');

  useEffect(() => {
    document.title = 'Scorecard - DSA';
  }, []);

  useEffect(() => {
    let cancelled = false;
    const loadSummary = async () => {
      setLoadingSummary(true);
      try {
        const data = await researchApi.getScorecard();
        if (cancelled) return;
        setSummary(data);
        setPageError(null);
        // Real flags need ~90 days to mature; until then the simulated
        // backtest is the only population with decided outcomes.
        if (!(data.real?.flagged?.decided ?? 0) && (data.simulated?.flagged?.decided ?? 0)) {
          setBreakdownSource('simulated');
        }
      } catch (err) {
        if (!cancelled) setPageError(getParsedApiError(err));
      } finally {
        if (!cancelled) setLoadingSummary(false);
      }
    };
    void loadSummary();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadFlags = useCallback(async (page: number, status: string, population: string) => {
    setLoadingFlags(true);
    try {
      const response = await researchApi.getScorecardFlags({
        status: status === 'all' ? undefined : (status as 'open' | 'hit' | 'flop' | 'expired'),
        simulated: population === 'all' ? undefined : population === 'simulated',
        limit: PAGE_SIZE,
        offset: (page - 1) * PAGE_SIZE,
      });
      setFlagRows(response.rows || []);
      setTotalFlags(response.total || 0);
      setCurrentPage(page);
    } catch (err) {
      setPageError(getParsedApiError(err));
    } finally {
      setLoadingFlags(false);
    }
  }, []);

  useEffect(() => {
    void loadFlags(1, statusFilter, populationFilter);
  }, [loadFlags, statusFilter, populationFilter]);

  const runBootstrap = useCallback(async () => {
    setBootstrapState('starting');
    try {
      const response = await researchApi.runScorecardBootstrap();
      setBootstrapState(response.status === 'already_running' ? 'already_running' : 'started');
    } catch {
      setBootstrapState('error');
    }
  }, []);

  const windowDays = summary?.window_days ?? 90;
  const hitPct = summary?.hit_threshold_pct ?? 20;
  const realBlock = summary?.real ?? null;
  const hasRealRows = (realBlock?.baseline?.flags ?? 0) > 0;
  const simulated = summary?.simulated ?? null;
  const breakdownBlock = breakdownSource === 'simulated' && simulated ? simulated : summary?.real;
  const showBreakdownToggle = Boolean(simulated && hasRealRows);

  const screenBars = Object.entries(breakdownBlock?.by_screen || {}).map(([key, stats]) => ({
    name: screenLabel(key),
    pct: stats.batting_average != null ? Math.round(stats.batting_average * 1000) / 10 : 0,
    label:
      stats.batting_average != null
        ? `${(stats.batting_average * 100).toFixed(0)}% (n=${stats.decided})`
        : `-- (n=${stats.decided})`,
  }));
  const bandRows = Object.entries(breakdownBlock?.by_score_band || {}).sort(
    ([a], [b]) => SCORE_BAND_ORDER.indexOf(a) - SCORE_BAND_ORDER.indexOf(b),
  );
  const monthRows = Object.entries(breakdownBlock?.by_month || {}).sort(([a], [b]) => (a < b ? 1 : -1));

  const totalPages = Math.ceil(totalFlags / PAGE_SIZE);

  return (
    <AppPage>
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
        <div>
          <p className="label-uppercase">Trust gate</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Scorecard</h1>
          <p className="mt-1 text-sm text-secondary-text">
            Of everything the discovery screens flagged, how much actually went on to gain +{hitPct.toFixed(0)}%
            within {windowDays} days?
          </p>
        </div>

        {pageError ? <ApiErrorAlert error={pageError} /> : null}

        {/* Headline strip — REAL flags only. */}
        <Card padding="md">
          <DashboardPanelHeader eyebrow="Real flags" title="Hit rate" />
          {loadingSummary ? (
            <DashboardStateBlock title="Loading..." loading compact />
          ) : !hasRealRows || !realBlock ? (
            <DashboardStateBlock
              title="No graded flags yet"
              description="Flags are graded automatically with the nightly update once discovery snapshots exist."
              compact
            />
          ) : (
            <PopulationMetrics block={realBlock} windowDays={windowDays} hitPct={hitPct} />
          )}
        </Card>

        {/* SIMULATED block — visually distinct, never merged with real stats. */}
        {loadingSummary ? null : simulated ? (
          <Card padding="md" className="border border-warning/30">
            <DashboardPanelHeader
              eyebrow={<span className="text-warning">Simulated — backtested</span>}
              title="Backtested hit rate"
            />
            <div className="space-y-3">
              <PopulationMetrics block={simulated} windowDays={windowDays} hitPct={hitPct} />
              <p className="text-xs text-secondary-text">{SIMULATED_DISCLOSURE}</p>
            </div>
          </Card>
        ) : (
          <Card padding="md" className="border border-dashed border-border/70">
            <DashboardPanelHeader
              eyebrow={<span className="text-warning">Simulated — backtested</span>}
              title="No simulated history yet"
            />
            <div className="space-y-3 text-sm text-secondary-text">
              <p>
                Real flags need ~{windowDays} days to mature. The one-time bootstrap replays today&apos;s
                discovery screens over past prices to estimate the hit rate now. {SIMULATED_DISCLOSURE}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button
                  onClick={() => void runBootstrap()}
                  isLoading={bootstrapState === 'starting'}
                  loadingText="Starting..."
                  disabled={bootstrapState === 'started' || bootstrapState === 'already_running'}
                >
                  Run bootstrap
                </Button>
                {bootstrapState === 'started' ? (
                  <span className="text-xs text-success">Bootstrap started — takes ~10-15 min; refresh later.</span>
                ) : bootstrapState === 'already_running' ? (
                  <span className="text-xs text-warning">A bootstrap is already running — refresh in a few minutes.</span>
                ) : bootstrapState === 'error' ? (
                  <span className="text-xs text-danger">Bootstrap failed to start — check the server logs and retry.</span>
                ) : (
                  <span className="text-xs text-muted-text">Takes ~10-15 min; refresh later.</span>
                )}
              </div>
            </div>
          </Card>
        )}

        {/* Breakdowns: hit rate by screen, score band, month. */}
        {breakdownBlock ? (
          <Card padding="md">
            <DashboardPanelHeader
              eyebrow={breakdownSource === 'simulated' ? <span className="text-warning">Simulated — backtested</span> : 'Real flags'}
              title="Where the hits come from"
              actions={showBreakdownToggle ? (
                <div className="flex items-center gap-1.5">
                  <Button size="sm" variant={breakdownSource === 'real' ? 'primary' : 'ghost'} onClick={() => setBreakdownSource('real')}>
                    Real
                  </Button>
                  <Button size="sm" variant={breakdownSource === 'simulated' ? 'primary' : 'ghost'} onClick={() => setBreakdownSource('simulated')}>
                    Simulated
                  </Button>
                </div>
              ) : null}
            />
            <div className="space-y-5">
              <div>
                <p className="label-uppercase mb-2">Hit rate by screen</p>
                {screenBars.length ? (
                  <ResponsiveContainer width="100%" height={Math.max(96, screenBars.length * 44)}>
                    <BarChart data={screenBars} layout="vertical" margin={{ top: 0, right: 76, bottom: 0, left: 0 }}>
                      <XAxis type="number" domain={[0, 100]} hide />
                      <YAxis
                        type="category"
                        dataKey="name"
                        width={110}
                        tick={{ fill: CHART_COLORS.muted, fontSize: 12 }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <Bar dataKey="pct" fill={CHART_COLORS.primary} radius={[0, 6, 6, 0]} barSize={18} background={{ fill: CHART_COLORS.border, radius: 6 }}>
                        <LabelList dataKey="label" position="right" fill={CHART_COLORS.foreground} fontSize={11} />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                ) : (
                  <p className="text-xs text-secondary-text">No flags passed a screen yet.</p>
                )}
              </div>
              <div className="grid gap-5 md:grid-cols-2">
                <GroupTable title="By composite score band" rows={bandRows} />
                <div>
                  <GroupTable title="By month" rows={monthRows} />
                  <p className="mt-2 text-xs text-muted-text">
                    Recent months skew high until their flags mature — fast winners decide first; flops and expiries
                    take longer.
                  </p>
                </div>
              </div>
            </div>
          </Card>
        ) : null}

        {/* Recent flags table. */}
        <Card padding="md">
          <DashboardPanelHeader
            eyebrow="Flag log"
            title="Recent flags"
            actions={<span className="text-xs text-secondary-text">{totalFlags} total</span>}
          />
          <p className="mb-3 text-xs text-muted-text">
            The log includes baseline rows — liquidity-qualified names that passed no screen. They form the base rate
            the flags must beat.
          </p>
          <div className="mb-3 grid grid-cols-2 gap-2 sm:max-w-md">
            <Select
              label="Status"
              value={statusFilter}
              onChange={(value) => setStatusFilter(value)}
              options={[
                { value: 'all', label: 'All statuses' },
                { value: 'open', label: 'Open' },
                { value: 'hit', label: 'Hit' },
                { value: 'flop', label: 'Flop' },
                { value: 'expired', label: 'Expired' },
              ]}
            />
            <Select
              label="Population"
              value={populationFilter}
              onChange={(value) => setPopulationFilter(value)}
              options={[
                { value: 'real', label: 'Real' },
                { value: 'simulated', label: 'Simulated' },
                { value: 'all', label: 'Real + simulated' },
              ]}
            />
          </div>
          {loadingFlags ? (
            <DashboardStateBlock title="Loading..." loading compact />
          ) : flagRows.length === 0 ? (
            <DashboardStateBlock title="No flags match these filters." compact />
          ) : (
            <>
              <p className="backtest-table-scroll-hint mb-2">Scroll horizontally on small screens</p>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-left text-sm">
                  <thead>
                    <tr className="border-b border-border/60 text-xs uppercase tracking-normal text-secondary-text">
                      <th className="py-2 pr-3">Flagged</th>
                      <th className="py-2 pr-3">Symbol</th>
                      <th className="py-2 pr-3">Screens</th>
                      <th className="py-2 pr-3">Status</th>
                      <th className="py-2 pr-3">Entry</th>
                      <th className="py-2 pr-3">Days to hit</th>
                      <th className="py-2 pr-3">Max gain</th>
                      <th className="py-2">Return</th>
                    </tr>
                  </thead>
                  <tbody>
                    {flagRows.map((row) => {
                      const screens = discoveryScreensFromKeys(row.screens);
                      const isBaseline = !row.screens?.length && row.candidate_score == null;
                      return (
                        <tr key={row.id} className="border-b border-border/40">
                          <td className="py-2 pr-3 whitespace-nowrap text-secondary-text">{row.as_of || '--'}</td>
                          <td className="py-2 pr-3">
                            <span className="inline-flex items-center gap-1.5">
                              <TickerChip
                                symbol={row.symbol}
                                context={{
                                  source: 'discovery',
                                  metrics: [
                                    { label: 'Flagged', value: row.as_of || '--' },
                                    { label: 'Status', value: row.status, term: 'hit_rate' },
                                    { label: 'Entry close', value: row.entry_close != null ? row.entry_close.toFixed(2) : '--' },
                                    { label: 'Max gain since', value: signedPct(row.max_gain_pct) },
                                  ],
                                }}
                              />
                              {row.simulated ? <Badge variant="warning">sim</Badge> : null}
                            </span>
                          </td>
                          <td className="py-2 pr-3">
                            <span className="inline-flex flex-wrap items-center gap-1">
                              {screens.length ? (
                                <DiscoveryScreenBadges screens={screens} />
                              ) : isBaseline ? (
                                <Badge variant="default" className="text-muted-text">baseline</Badge>
                              ) : (
                                <span className="text-xs text-muted-text">--</span>
                              )}
                            </span>
                          </td>
                          <td className="py-2 pr-3">{statusBadge(row.status)}</td>
                          <td className="py-2 pr-3 tabular-nums text-secondary-text">
                            {row.entry_close != null ? row.entry_close.toFixed(2) : '--'}
                          </td>
                          <td className="py-2 pr-3 tabular-nums text-secondary-text">{row.days_to_hit ?? '--'}</td>
                          <td className={`py-2 pr-3 tabular-nums ${row.max_gain_pct != null && row.max_gain_pct >= 0 ? 'text-success' : 'text-secondary-text'}`}>
                            {signedPct(row.max_gain_pct)}
                          </td>
                          <td className={`py-2 tabular-nums ${row.current_return_pct == null ? 'text-secondary-text' : row.current_return_pct >= 0 ? 'text-success' : 'text-danger'}`}>
                            {signedPct(row.current_return_pct)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <Pagination
                currentPage={currentPage}
                totalPages={totalPages}
                onPageChange={(page) => void loadFlags(page, statusFilter, populationFilter)}
                className="mt-4"
              />
            </>
          )}
        </Card>
      </div>
    </AppPage>
  );
};

export default ScorecardPage;
