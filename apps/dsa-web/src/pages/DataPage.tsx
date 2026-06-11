import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, DatabaseZap, RefreshCcw, ShieldAlert, Sigma, TableProperties } from 'lucide-react';
import { AppPage, Badge, Button, Card, DecisionExplanation as DecisionExplanationPanel, EmptyState, InlineAlert, Input } from '../components/common';
import type { DecisionExplanationData } from '../components/common';
import {
  researchApi,
  type FreeDataFundamentalsResponse,
  type FreeDataMacroResponse,
  type FreeDataShortVolumeResponse,
  type FreeDataSourceStatus,
  type FreeDataStatusResponse,
  type FreeDataUniverseResponse,
} from '../api/research';

const statusVariant = (status: string): 'success' | 'warning' | 'danger' | 'default' | 'info' => {
  if (status === 'ok' || status === 'configured') return 'success';
  if (status === 'fallback') return 'warning';
  if (status === 'missing' || status === 'degraded') return 'danger';
  return 'default';
};

const qualityVariant = (quality: string): 'success' | 'warning' | 'danger' | 'default' | 'info' => {
  if (quality === 'official' || quality === 'primary') return 'success';
  if (quality.includes('fallback') || quality.includes('unofficial')) return 'warning';
  if (quality === 'missing') return 'danger';
  return 'info';
};

const asNumber = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null);
const asString = (value: unknown): string => (typeof value === 'string' ? value : '');

const DATA_PLAN = [
  {
    title: 'Price and leaders',
    text: 'Use Massive/Polygon first for US stock and ETF daily bars. Fall back only when the page says fallback data.',
  },
  {
    title: 'Fundamental acceleration',
    text: 'Use SEC companyfacts for revenue, profit, EPS, and filing-backed confirmation after the chart already looks strong.',
  },
  {
    title: 'Positioning reality',
    text: 'Use FINRA, COT, and options chains as caution layers. Free options gamma is context, not a decisive flow read.',
  },
  {
    title: 'Catalysts and sources',
    text: 'Use Alpha Vantage news and X bookmarks to explain why a move may matter, then score sources by forward returns.',
  },
];

const DATA_RULE_EXPLANATION: DecisionExplanationData = {
  title: 'How this page decides',
  decision: 'Trust primary/official data first',
  plainEnglish: 'The app should never treat all data sources equally. Official and primary feeds get more trust. Fallback, estimated, delayed, or missing data lowers confidence everywhere that source is used.',
  evidence: [
    { label: 'Primary', value: 'Massive/Polygon when available', source: 'Used for daily bars and market data.' },
    { label: 'Official', value: 'SEC, FRED, FINRA, CFTC, Nasdaq', source: 'Used for filings, macro, short-volume, positioning, and universe definitions.' },
    { label: 'Fallback', value: 'yfinance/public APIs', source: 'Useful but confidence must be capped.' },
  ],
  math: [
    { formula: 'confidence = source quality + freshness + coverage - missing/fallback penalties', result: 'used across analysis pages' },
  ],
  confidence: {
    level: 'medium',
    reason: 'Before live status loads, assume mixed confidence. After refresh, source-specific warnings decide the real confidence.',
  },
  whatWouldChange: [
    'More primary feeds increase confidence.',
    'Rate limits, stale data, or fallback-only data decrease confidence.',
    'Missing options data means gamma/flow should be treated as context, not truth.',
  ],
  guardrails: [
    'No signal should hide its source.',
    'No fallback source should be presented as institutional-grade data.',
  ],
};

const formatNumber = (value: unknown, digits = 2): string => {
  const parsed = asNumber(value);
  if (parsed === null) return 'N/A';
  if (Math.abs(parsed) >= 1_000_000_000) return `${(parsed / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(parsed) >= 1_000_000) return `${(parsed / 1_000_000).toFixed(2)}M`;
  if (Math.abs(parsed) >= 10_000) return parsed.toFixed(0);
  return parsed.toFixed(digits);
};

const formatValue = (value: unknown, unit?: string): string => {
  const parsed = asNumber(value);
  if (parsed === null) return 'N/A';
  if (unit === 'USD') return `$${formatNumber(parsed, 0)}`;
  if (unit === 'shares') return formatNumber(parsed, 0);
  if (unit === 'USD/shares') return `$${parsed.toFixed(2)}`;
  if (unit === '%') return `${parsed.toFixed(2)}%`;
  return formatNumber(parsed, 2);
};

const cacheText = (cache?: Record<string, unknown>): string => {
  const status = asString(cache?.status);
  const ttl = asNumber(cache?.ttl_seconds);
  if (!status) return 'Cache status N/A';
  return ttl ? `Cache ${status}, ${Math.round(ttl / 60)} min TTL` : `Cache ${status}`;
};

const buildDataStackExplanation = (
  status: FreeDataStatusResponse,
  officialCount: number,
  fallbackCount: number,
): DecisionExplanationData => ({
  title: 'Why the app trusts or discounts data',
  decision: `${status.ok_count} usable sources, ${status.degraded_count} need caution`,
  plainEnglish: status.summary,
  evidence: [
    { label: 'Official feeds', value: String(officialCount), source: 'SEC, FRED, Nasdaq Trader, FINRA, CFTC where available.' },
    { label: 'Fallback feeds', value: String(fallbackCount), source: 'Useful, but confidence should be capped.' },
    { label: 'Cache policy', value: cacheText(status.cache), source: status.cache_policy || 'Short cache prevents burning free API limits.' },
    { label: 'Decision rule', value: 'Primary beats fallback', source: 'A signal should disclose when it relies on unofficial or delayed data.' },
  ],
  math: [
    {
      formula: 'source_health = usable sources - degraded/missing sources',
      result: `${status.ok_count} usable / ${status.degraded_count} caution`,
    },
  ],
  confidence: {
    level: fallbackCount > 0 ? 'medium' : 'high',
    reason: 'Confidence is based on source quality and coverage. It is separate from whether a stock trade will work.',
  },
  whatWouldChange: [
    'Adding paid/current options data would improve positioning confidence.',
    'If Massive/Polygon rate limits, price-derived signals should show fallback warnings.',
    'If an official source is missing, the app should downgrade confidence instead of hiding the gap.',
  ],
  guardrails: [
    'Do not treat free options gamma as real dealer flow.',
    'Read source warnings before trusting any score.',
  ],
});

const DataPage: React.FC = () => {
  const [symbol, setSymbol] = useState('AAPL');
  const [status, setStatus] = useState<FreeDataStatusResponse | null>(null);
  const [universe, setUniverse] = useState<FreeDataUniverseResponse | null>(null);
  const [macro, setMacro] = useState<FreeDataMacroResponse | null>(null);
  const [fundamentals, setFundamentals] = useState<FreeDataFundamentalsResponse | null>(null);
  const [shortVolume, setShortVolume] = useState<FreeDataShortVolumeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (nextSymbol = symbol) => {
    const cleaned = nextSymbol.trim().toUpperCase();
    if (!cleaned) return;
    setLoading(true);
    setError(null);
    try {
      const [statusPayload, universePayload, macroPayload, fundamentalsPayload, shortVolumePayload] = await Promise.all([
        researchApi.getFreeDataStatus(),
        researchApi.getFreeDataUniverse(5000),
        researchApi.getFreeDataMacro(),
        researchApi.getFreeDataFundamentals(cleaned),
        researchApi.getFreeDataShortVolume(cleaned),
      ]);
      setStatus(statusPayload);
      setUniverse(universePayload);
      setMacro(macroPayload);
      setFundamentals(fundamentalsPayload);
      setShortVolume(shortVolumePayload);
      setSymbol(cleaned);
    } catch {
      setError('Free data refresh failed. Check network access and provider limits.');
    } finally {
      setLoading(false);
    }
  }, [symbol]);

  useEffect(() => {
    document.title = 'Free Data - DSA';
    void load('AAPL');
  }, []);

  const officialCount = useMemo(() => (status?.sources || []).filter((source) => source.quality === 'official').length, [status?.sources]);
  const fallbackCount = useMemo(() => (status?.sources || []).filter((source) => source.status === 'fallback' || source.quality.includes('unofficial')).length, [status?.sources]);

  return (
    <AppPage>
      <div data-testid="free-data-page" className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="label-uppercase">Inputs</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Free Data</h1>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            label="Symbol"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            className="h-10 w-32"
            onKeyDown={(event) => {
              if (event.key === 'Enter') void load();
            }}
          />
          <Button onClick={() => void load()} isLoading={loading} loadingText="Checking">
            <RefreshCcw className="h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      {error ? <InlineAlert variant="danger" title="Data error" message={error} className="mb-4" /> : null}

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric icon={<DatabaseZap className="h-5 w-5" />} label="Live sources" value={status ? String(status.ok_count) : '...'} />
        <Metric icon={<ShieldAlert className="h-5 w-5" />} label="Needs caution" value={status ? String(status.degraded_count) : '...'} tone="warning" />
        <Metric icon={<CheckCircle2 className="h-5 w-5" />} label="Official feeds" value={status ? String(officialCount) : '...'} tone="success" />
        <Metric icon={<AlertTriangle className="h-5 w-5" />} label="Fallback feeds" value={status ? String(fallbackCount) : '...'} tone="warning" />
      </div>

      {status ? (
        <InlineAlert
          variant="info"
          title="Free stack reality"
          message={(
            <div className="space-y-1">
              <p>{status.summary}</p>
              {status.cache_policy ? <p>{status.cache_policy}</p> : null}
              <p className="text-xs opacity-80">{cacheText(status.cache)}</p>
            </div>
          )}
          className="mb-4"
        />
      ) : null}

      {status ? (
        <DecisionExplanationPanel
          data={buildDataStackExplanation(status, officialCount, fallbackCount)}
          compact
          className="mb-4"
        />
      ) : (
        <DecisionExplanationPanel data={DATA_RULE_EXPLANATION} compact className="mb-4" />
      )}

      <Card title="How The Free Stack Gets You Data" subtitle="Practical plan" padding="md" className="mb-4 rounded-lg">
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          {DATA_PLAN.map((item) => (
            <RealityItem
              key={item.title}
              icon={<CheckCircle2 className="h-5 w-5" />}
              title={item.title}
              text={item.text}
            />
          ))}
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[1.15fr_0.85fr]">
        <Card title="Source Coverage" subtitle="Status" padding="md" className="rounded-lg">
          {!status && loading ? (
            <div className="py-8 text-sm text-secondary-text">Checking free sources...</div>
          ) : status ? (
            <div className="grid gap-2 md:grid-cols-2">
              {status.sources.map((source) => (
                <SourceRow key={source.name} source={source} />
              ))}
            </div>
          ) : (
            <EmptyState icon={<DatabaseZap className="h-6 w-6" />} title="No source status" description="Refresh data." />
          )}
        </Card>

        <Card title="US Universe" subtitle="Nasdaq Trader" padding="md" className="rounded-lg">
          {universe ? (
            <div>
              <div className="grid grid-cols-3 gap-2">
                <Info label="Total" value={formatNumber(universe.total, 0)} />
                <Info label="Stocks" value={formatNumber(universe.stocks, 0)} />
                <Info label="ETFs" value={formatNumber(universe.etfs, 0)} />
              </div>
              <p className="mt-3 text-xs leading-5 text-secondary-text">{universe.caveat}</p>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {universe.symbols.slice(0, 24).map((item) => <Badge key={item} variant="info">{item}</Badge>)}
              </div>
            </div>
          ) : (
            <div className="py-8 text-sm text-secondary-text">Loading universe...</div>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card title="Macro Snapshot" subtitle="FRED" padding="md" className="rounded-lg">
          {macro ? (
            <>
              <div className="grid gap-2 md:grid-cols-2">
                {macro.series.map((item) => (
                  <Info
                    key={asString(item.id)}
                    label={asString(item.label)}
                    value={formatValue(item.value, asString(item.unit))}
                    detail={asString(item.date) || asString(item.detail)}
                  />
                ))}
              </div>
              <p className="mt-3 text-xs leading-5 text-secondary-text">{macro.caveat}</p>
            </>
          ) : (
            <div className="py-8 text-sm text-secondary-text">Loading macro...</div>
          )}
        </Card>

        <Card title={`${fundamentals?.symbol || symbol} SEC Fundamentals`} subtitle="Companyfacts" padding="md" className="rounded-lg">
          {fundamentals ? (
            <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-sm font-semibold text-foreground">{fundamentals.entity_name || fundamentals.symbol}</p>
                  <p className="text-xs text-secondary-text">CIK {fundamentals.cik || 'N/A'}</p>
                </div>
                <Badge variant={statusVariant(fundamentals.status)}>{fundamentals.status}</Badge>
              </div>
              {fundamentals.metrics.length ? (
                <div className="grid gap-2 md:grid-cols-2">
                  {fundamentals.metrics.map((metric) => (
                    <Info
                      key={asString(metric.key)}
                      label={asString(metric.label)}
                      value={formatValue(metric.value, asString(metric.unit))}
                      detail={asNumber(metric.growth_pct) !== null ? `${asNumber(metric.growth_pct)?.toFixed(2)}% comparable change` : asString(metric.end)}
                    />
                  ))}
                </div>
              ) : (
                <p className="text-sm text-secondary-text">No SEC metrics found for this symbol.</p>
              )}
              <p className="mt-3 text-xs leading-5 text-secondary-text">{fundamentals.caveat}</p>
            </>
          ) : (
            <div className="py-8 text-sm text-secondary-text">Loading SEC facts...</div>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Card title={`${shortVolume?.symbol || symbol} Short-Sale Volume`} subtitle="FINRA" padding="md" className="rounded-lg">
          {shortVolume ? (
            <>
              <div className="grid gap-2 md:grid-cols-2">
                <Info label="Short Volume" value={formatNumber(shortVolume.short_volume, 0)} />
                <Info label="Total Volume" value={formatNumber(shortVolume.total_volume, 0)} />
                <Info label="Short Volume Ratio" value={shortVolume.short_volume_ratio === null || shortVolume.short_volume_ratio === undefined ? 'N/A' : `${shortVolume.short_volume_ratio.toFixed(2)}%`} />
                <Info label="Date" value={shortVolume.date || 'N/A'} />
              </div>
              <p className="mt-3 text-xs leading-5 text-secondary-text">{shortVolume.caveat}</p>
              {shortVolume.diagnostics.length ? (
                <div className="mt-3 rounded-lg border border-warning/25 bg-warning/10 px-3 py-2 text-xs leading-5 text-warning">
                  {shortVolume.diagnostics.slice(0, 3).join(' | ')}
                </div>
              ) : null}
            </>
          ) : (
            <div className="py-8 text-sm text-secondary-text">Loading FINRA short-volume...</div>
          )}
        </Card>

        <Card title="Free Options Reality" subtitle="Gamma and flow" padding="md" className="rounded-lg">
          <div className="grid gap-3 md:grid-cols-3">
            <RealityItem
              icon={<Sigma className="h-5 w-5" />}
              title="Gamma"
              text="Free path is estimated/delayed unless options snapshots are included in your Massive plan."
            />
            <RealityItem
              icon={<Activity className="h-5 w-5" />}
              title="Flow"
              text="Real intraday dealer flow needs trade direction and quote context. Free APIs rarely provide that cleanly."
            />
            <RealityItem
              icon={<TableProperties className="h-5 w-5" />}
              title="Decision Rule"
              text="When options source is yfinance, treat positioning as context and keep confidence capped."
            />
          </div>
        </Card>
      </div>
    </AppPage>
  );
};

const Metric: React.FC<{ icon: React.ReactNode; label: string; value: string; tone?: 'success' | 'warning' }> = ({ icon, label, value, tone }) => (
  <Card padding="sm" className="rounded-lg">
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="text-xs uppercase tracking-normal text-secondary-text">{label}</p>
        <p className={tone === 'success' ? 'mt-2 text-2xl font-semibold text-success' : tone === 'warning' ? 'mt-2 text-2xl font-semibold text-warning' : 'mt-2 text-2xl font-semibold text-foreground'}>
          {value}
        </p>
      </div>
      <div className="rounded-lg border border-cyan/20 bg-cyan/10 p-2 text-cyan">{icon}</div>
    </div>
  </Card>
);

const SourceRow: React.FC<{ source: FreeDataSourceStatus }> = ({ source }) => (
  <div data-testid="source-row" className="rounded-lg border border-border/60 bg-elevated/45 px-3 py-3">
    <div className="flex items-start justify-between gap-2">
      <div>
        <p className="text-sm font-semibold text-foreground">{source.name}</p>
        <p className="mt-1 text-xs text-secondary-text">{source.category}</p>
      </div>
      <div className="flex flex-col items-end gap-1">
        <Badge variant={statusVariant(source.status)}>{source.status}</Badge>
        <Badge variant={qualityVariant(source.quality)}>{source.quality.replaceAll('_', ' ')}</Badge>
      </div>
    </div>
    <p className="mt-3 text-xs leading-5 text-secondary-text">{source.coverage}</p>
    {source.decision_use ? (
      <div className="mt-2 rounded-lg border border-cyan/20 bg-cyan/10 px-2.5 py-2 text-xs leading-5 text-cyan">
        {source.decision_use}
      </div>
    ) : null}
    {source.best_for?.length ? (
      <div className="mt-2 flex flex-wrap gap-1.5">
        {source.best_for.slice(0, 4).map((item) => <Badge key={item} variant="info">{item}</Badge>)}
      </div>
    ) : null}
    {source.limits?.length ? (
      <div className="mt-2 space-y-1 text-xs leading-5 text-secondary-text">
        {source.limits.slice(0, 3).map((item) => <p key={item}>Limit: {item}</p>)}
      </div>
    ) : null}
    <p className="mt-2 text-xs leading-5 text-warning">{source.caveat}</p>
    {source.refresh ? <p className="mt-2 text-[11px] text-muted-foreground">{source.refresh}</p> : null}
    {source.detail ? <p className="mt-2 text-[11px] text-muted-foreground">{source.detail}</p> : null}
  </div>
);

const Info: React.FC<{ label: string; value: string; detail?: string }> = ({ label, value, detail }) => (
  <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
    <p className="text-[11px] uppercase tracking-normal text-secondary-text">{label}</p>
    <p className="mt-1 break-words text-sm font-semibold text-foreground">{value}</p>
    {detail ? <p className="mt-1 break-words text-[11px] text-muted-foreground">{detail}</p> : null}
  </div>
);

const RealityItem: React.FC<{ icon: React.ReactNode; title: string; text: string }> = ({ icon, title, text }) => (
  <div className="rounded-lg border border-border/60 bg-elevated/45 px-3 py-3">
    <div className="flex items-center gap-2 text-cyan">
      {icon}
      <p className="text-sm font-semibold text-foreground">{title}</p>
    </div>
    <p className="mt-2 text-xs leading-5 text-secondary-text">{text}</p>
  </div>
);

export default DataPage;
