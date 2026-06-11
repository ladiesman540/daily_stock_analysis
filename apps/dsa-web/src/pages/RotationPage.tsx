import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bitcoin, CheckCircle2, Compass, RefreshCcw, RotateCw } from 'lucide-react';
import { AppPage, Badge, Button, Card, DataFreshnessBadge, DecisionExplanation as DecisionExplanationPanel, EmptyState, InlineAlert, StatCard } from '../components/common';
import type { DecisionExplanationData } from '../components/common';
import {
  researchApi,
  type CycleSnapshotResponse,
  type RotationConstituent,
  type RotationHorizon,
  type RotationMemo,
  type RotationSnapshotResponse,
} from '../api/research';

const HORIZONS: RotationHorizon[] = ['1D', '1W', '1M', '3M', '6M', '12M'];

const GROUP_LABELS: Record<string, string> = {
  all: 'All',
  index_style: 'Index & style',
  sectors: 'Sectors',
  industries: 'Industries & themes',
  bonds_intl_cmd: 'Bonds, intl, commodities',
};

const QUADRANTS: Array<{ key: string; label: string; variant: 'success' | 'info' | 'warning' | 'danger'; blurb: string }> = [
  { key: 'leading', label: 'Leading', variant: 'success', blurb: 'Strong vs SPY and still strengthening.' },
  { key: 'improving', label: 'Improving', variant: 'info', blurb: 'Still weak vs SPY but momentum turning up — where new leadership comes from.' },
  { key: 'weakening', label: 'Weakening', variant: 'warning', blurb: 'Still strong vs SPY but losing momentum — ageing leadership.' },
  { key: 'lagging', label: 'Lagging', variant: 'danger', blurb: 'Weak and getting weaker.' },
];

const ROTATION_RULE_EXPLANATION: DecisionExplanationData = {
  title: 'How this page decides',
  decision: 'Locate the cycle: what is hot/not per timeframe, and where we are in the bigger picture',
  plainEnglish:
    'Every liquid sector/industry ETF is ranked by relative strength against SPY over daily, weekly, monthly, quarterly, semiannual, and annual windows. RRG quadrants show whether leadership is fresh or ageing. The macro card places it all inside the business cycle.',
  evidence: [
    { label: 'Leaderboard', value: 'RS rank per timeframe', source: 'Returns vs SPY from cached daily bars.' },
    { label: 'Quadrants', value: 'RS trend × RS momentum', source: 'RRG-style: leading / weakening / lagging / improving.' },
    { label: 'Cycle phase', value: 'FRED composite', source: 'Curve, claims, Sahm gap, output, financial conditions, permits, Fed stance, breakevens, credit.' },
  ],
  math: [
    { formula: 'rel = (1 + etf_return) / (1 + spy_return) - 1, ranked per horizon', result: 'RS rank (1 = strongest)' },
    { formula: 'rs_ratio = 100 × RS / SMA63(RS); rs_momentum = ratio now vs 21d ago', result: 'RRG quadrant' },
  ],
  confidence: {
    level: 'medium',
    reason: 'Rotation is descriptive, not predictive. Agreement between timeframes, quadrants, and the cycle phase raises conviction; divergence is a warning.',
  },
  whatWouldChange: [
    'A regime break (see Home) can reset leadership quickly.',
    'Cycle phase changes shift which sectors historically lead.',
    'Newly-hot groups (rank jumps) matter more than long-standing leaders.',
  ],
  guardrails: [
    'A leading group is a hunting ground, not a trade. Use Signals for entries.',
    'When the tape diverges from the cycle playbook, trust the tape for timing and the cycle for risk.',
  ],
};

const fmtPct = (value: number | null | undefined): string =>
  typeof value === 'number' && Number.isFinite(value) ? `${value > 0 ? '+' : ''}${value.toFixed(1)}%` : 'n/a';

const pctTone = (value: number | null | undefined): string =>
  typeof value === 'number' ? (value > 0 ? 'text-success' : value < 0 ? 'text-danger' : 'text-secondary-text') : 'text-secondary-text';

const RankChange: React.FC<{ change?: number | null; isNew?: boolean }> = ({ change, isNew }) => {
  if (isNew) return <Badge variant="info">new top-3</Badge>;
  if (change == null) return <span className="text-secondary-text">—</span>;
  if (change > 0) return <span className="text-success">↑{change}</span>;
  if (change < 0) return <span className="text-danger">↓{Math.abs(change)}</span>;
  return <span className="text-secondary-text">＝</span>;
};

const quadrantVariant = (quadrant?: string | null): 'success' | 'info' | 'warning' | 'danger' | 'default' => {
  const match = QUADRANTS.find((item) => item.key === quadrant);
  return match ? match.variant : 'default';
};

const RotationPage: React.FC = () => {
  const [rotation, setRotation] = useState<RotationSnapshotResponse | null>(null);
  const [cycle, setCycle] = useState<CycleSnapshotResponse | null>(null);
  const [memo, setMemo] = useState<RotationMemo | null>(null);
  const [rotationError, setRotationError] = useState<string | null>(null);
  const [cycleError, setCycleError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [horizon, setHorizon] = useState<RotationHorizon>('1M');
  const [group, setGroup] = useState<string>('all');

  const loadAll = useCallback(async () => {
    researchApi.getRotationSnapshot().then(setRotation).catch(() => setRotationError('Failed to load the rotation snapshot.'));
    researchApi.getCycleSnapshot().then(setCycle).catch(() => setCycleError('Failed to load the cycle snapshot.'));
    researchApi.getLatestRotationMemo().then(setMemo).catch(() => undefined);
  }, []);

  useEffect(() => {
    document.title = 'Rotation - DSA';
    void loadAll();
  }, [loadAll]);

  const handleRecompute = useCallback(async () => {
    setLoading(true);
    setRotationError(null);
    setCycleError(null);
    try {
      setRotation(await researchApi.runRotationSnapshot());
      setCycle(await researchApi.runCycleSnapshot());
    } catch {
      setRotationError('Recompute failed. Check server logs.');
    } finally {
      setLoading(false);
    }
  }, []);

  const constituents = useMemo(() => rotation?.constituents ?? [], [rotation]);

  const tableRows = useMemo(() => {
    const benchmark = rotation?.benchmark || 'SPY';
    const rows = constituents.filter(
      (row) => row.symbol !== benchmark && (group === 'all' || row.group === group),
    );
    return rows.sort((a, b) => {
      const rankA = a.ranks?.[horizon] ?? Number.MAX_SAFE_INTEGER;
      const rankB = b.ranks?.[horizon] ?? Number.MAX_SAFE_INTEGER;
      return rankA - rankB;
    });
  }, [constituents, group, horizon, rotation?.benchmark]);

  const quadrantGroups = useMemo(() => {
    const result: Record<string, RotationConstituent[]> = { leading: [], improving: [], weakening: [], lagging: [] };
    for (const row of constituents) {
      if (row.quadrant && result[row.quadrant]) result[row.quadrant].push(row);
    }
    for (const key of Object.keys(result)) {
      result[key].sort((a, b) => (a.composite_rank ?? 999) - (b.composite_rank ?? 999));
    }
    return result;
  }, [constituents]);

  const hasRotation = rotation?.status === 'completed' && constituents.length > 0;
  const hasCycle = cycle?.status === 'completed';
  const crypto = (cycle?.crypto ?? {}) as Record<string, unknown>;

  return (
    <AppPage>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-uppercase">Research</p>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <h1 className="text-2xl font-semibold text-foreground">Rotation & Cycle</h1>
            <DataFreshnessBadge asOf={rotation?.as_of} />
          </div>
        </div>
        <Button onClick={handleRecompute} isLoading={loading} loadingText="Computing">
          <RefreshCcw className="h-4 w-4" />
          Recompute
        </Button>
      </div>

      {rotationError ? <InlineAlert variant="danger" title="Rotation error" message={rotationError} className="mb-4" /> : null}

      <Card title="How To Read This Page" subtitle="Plain English" padding="md" className="mb-4 rounded-lg">
        <div className="grid gap-3 md:grid-cols-3">
          <GuidePoint title="Hot / Not" text="Each timeframe ranks every group by relative strength against SPY. What leads the week is often not what leads the quarter — the disagreement is the information." />
          <GuidePoint title="Quadrants" text="Leading groups are strong and strengthening. Improving groups are where new leadership is born. Weakening leaders are ageing." />
          <GuidePoint title="Cycle" text="The macro card says where we are in the business cycle and which sectors historically lead from here. When the tape disagrees, that divergence is itself a signal." />
        </div>
      </Card>

      <DecisionExplanationPanel data={ROTATION_RULE_EXPLANATION} compact className="mb-4" />

      {!hasRotation ? (
        <EmptyState
          icon={<RotateCw className="h-6 w-6" />}
          title="No rotation snapshot yet"
          description={rotation?.summary || 'No sector data yet — it arrives automatically with the nightly update (6:00 PM), or press Recompute to build it now.'}
          className="mb-4"
        />
      ) : (
        <div className="space-y-4">
          <Card padding="md" className="rounded-lg" title="Hot / Not Leaderboard" subtitle={`Relative strength vs ${rotation?.benchmark || 'SPY'} — ${rotation?.symbols_ranked ?? 0} ETFs ranked`}>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {HORIZONS.map((item) => (
                <Button key={item} size="sm" variant={horizon === item ? 'primary' : 'secondary'} onClick={() => setHorizon(item)}>
                  {item}
                </Button>
              ))}
              <span className="mx-1 h-5 w-px bg-border/60" />
              {Object.entries(GROUP_LABELS).map(([key, label]) => (
                <Button key={key} size="sm" variant={group === key ? 'outline' : 'ghost'} onClick={() => setGroup(key)}>
                  {label}
                </Button>
              ))}
            </div>
            <p className="backtest-table-scroll-hint mb-2">Scroll horizontally on small screens</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border/60 text-xs uppercase tracking-normal text-secondary-text">
                    <th className="py-2 pr-3">#</th>
                    <th className="py-2 pr-3">ETF</th>
                    <th className="py-2 pr-3">Group</th>
                    <th className="py-2 pr-3">{horizon} return</th>
                    <th className="py-2 pr-3">vs SPY</th>
                    <th className="py-2 pr-3">Rank Δ</th>
                    <th className="py-2 pr-3">Composite</th>
                    <th className="py-2">Quadrant</th>
                  </tr>
                </thead>
                <tbody>
                  {tableRows.map((row) => (
                    <tr key={row.symbol} className="border-b border-border/40">
                      <td className="py-2 pr-3 text-secondary-text">{row.ranks?.[horizon] ?? '—'}</td>
                      <td className="py-2 pr-3">
                        <span className="font-semibold text-foreground">{row.symbol}</span>
                        <span className="ml-2 text-xs text-secondary-text">{row.label}</span>
                      </td>
                      <td className="py-2 pr-3"><Badge variant="default">{GROUP_LABELS[row.group] || row.group}</Badge></td>
                      <td className={`py-2 pr-3 font-medium ${pctTone(row.returns?.[horizon])}`}>{fmtPct(row.returns?.[horizon])}</td>
                      <td className={`py-2 pr-3 ${pctTone(row.rel?.[horizon])}`}>{fmtPct(row.rel?.[horizon])}</td>
                      <td className="py-2 pr-3"><RankChange change={row.rank_change?.[horizon]} isNew={horizon === '1M' && row.entered_top3_1m} /></td>
                      <td className="py-2 pr-3 text-secondary-text">{row.composite_rank ?? '—'}</td>
                      <td className="py-2">
                        {row.quadrant ? <Badge variant={quadrantVariant(row.quadrant)}>{row.quadrant}</Badge> : <span className="text-secondary-text">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <section>
            <h3 className="mb-3 text-sm font-semibold uppercase tracking-normal text-secondary-text">RRG Quadrants (vs SPY)</h3>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              {QUADRANTS.map((quadrant) => (
                <Card key={quadrant.key} padding="sm" className="rounded-lg">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <Badge variant={quadrant.variant}>{quadrant.label}</Badge>
                    <span className="text-xs text-secondary-text">{quadrantGroups[quadrant.key]?.length ?? 0}</span>
                  </div>
                  <p className="mb-2 text-xs leading-5 text-secondary-text">{quadrant.blurb}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {(quadrantGroups[quadrant.key] ?? []).map((row) => (
                      <Badge key={row.symbol} variant="default">{row.symbol}</Badge>
                    ))}
                  </div>
                </Card>
              ))}
            </div>
          </section>
        </div>
      )}

      <div className="mt-4 space-y-4">
        {cycleError ? <InlineAlert variant="danger" title="Cycle error" message={cycleError} /> : null}
        {hasCycle ? (
          <Card padding="md" className="rounded-lg" title="Business Cycle" subtitle={`As of ${cycle?.as_of || 'n/a'} — free FRED data, rule-based`}>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Compass className="h-4 w-4 text-cyan" />
              <Badge variant={cycle?.phase === 'contraction' ? 'danger' : cycle?.phase === 'late' ? 'warning' : 'success'}>
                {(cycle?.phase || 'unknown').toUpperCase()}
              </Badge>
              <Badge variant="info">{cycle?.confidence || 'n/a'} confidence</Badge>
              {(cycle?.playbook ?? []).length > 0 ? (
                <span className="text-xs text-secondary-text">
                  Playbook: {(cycle?.playbook ?? []).join(', ')}
                </span>
              ) : null}
            </div>
            {cycle?.divergence ? (
              <InlineAlert variant="warning" title="Tape vs cycle divergence" message={cycle?.divergence_note || 'Tape leadership does not match the phase playbook.'} className="mb-3" />
            ) : null}
            <p className="backtest-table-scroll-hint mb-2">Scroll horizontally on small screens</p>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[640px] text-left text-sm">
                <thead>
                  <tr className="border-b border-border/60 text-xs uppercase tracking-normal text-secondary-text">
                    <th className="py-2 pr-3">Indicator</th>
                    <th className="py-2 pr-3">Latest</th>
                    <th className="py-2 pr-3">Trend</th>
                    <th className="py-2">Reading</th>
                  </tr>
                </thead>
                <tbody>
                  {(cycle?.indicators ?? []).map((indicator) => (
                    <tr key={indicator.id} className="border-b border-border/40">
                      <td className="py-2 pr-3 font-medium text-foreground">{indicator.label}</td>
                      <td className="py-2 pr-3 text-secondary-text">
                        {indicator.status === 'ok' && indicator.value != null ? indicator.value : 'n/a'}
                      </td>
                      <td className="py-2 pr-3 text-secondary-text">{indicator.trend || '—'}</td>
                      <td className="py-2 text-secondary-text">{indicator.plain || (indicator.status !== 'ok' ? `(${indicator.status})` : '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ) : null}

        {hasCycle && crypto.gauge ? (
          <Card padding="md" className="rounded-lg" title="Crypto Cycle" subtitle="BTC trend + ETH/BTC + dominance">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <Bitcoin className="h-4 w-4 text-warning" />
              <Badge variant={crypto.gauge === 'bear' ? 'danger' : crypto.gauge === 'alt_season_risk_on' ? 'success' : 'info'}>
                {String(crypto.gauge).replaceAll('_', ' ')}
              </Badge>
              <span className="text-xs text-secondary-text">{String(crypto.plain_english || '')}</span>
            </div>
            <div className="grid grid-cols-2 gap-2 sm:gap-3 md:grid-cols-4">
              <StatCard label="BTC > 200DMA" value={crypto.btc_above_200dma == null ? 'n/a' : crypto.btc_above_200dma ? 'Yes' : 'No'} />
              <StatCard label="Golden cross" value={crypto.btc_golden_cross == null ? 'n/a' : crypto.btc_golden_cross ? 'Yes' : 'No'} />
              <StatCard label="ETH/BTC 63d" value={typeof crypto.eth_btc_63d_pct === 'number' ? fmtPct(crypto.eth_btc_63d_pct) : 'n/a'} />
              <StatCard label="BTC dominance" value={typeof crypto.btc_dominance_pct === 'number' ? `${crypto.btc_dominance_pct}%` : 'n/a'} />
            </div>
          </Card>
        ) : null}

        {memo ? (
          <Card padding="lg" className="rounded-lg" title="Signal-Scan Memo" subtitle="Themes from the latest stock/crypto scan">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              {memo.signal_run_id ? <Badge variant="info">Run #{memo.signal_run_id}</Badge> : null}
              {memo.generated_at ? <span className="text-xs text-secondary-text">{new Date(memo.generated_at).toLocaleString()}</span> : null}
            </div>
            <p className="max-w-4xl text-sm leading-6 text-foreground">{memo.summary}</p>
            {memo.themes.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {memo.themes.map((theme, index) => (
                  <Badge key={`${String(theme.theme)}-${index}`} variant="history">
                    {String(theme.theme || 'untagged')} ({String(theme.mentions ?? 0)})
                  </Badge>
                ))}
              </div>
            ) : null}
          </Card>
        ) : null}
      </div>
    </AppPage>
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

export default RotationPage;
