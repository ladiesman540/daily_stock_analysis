import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, BarChart3, BookOpenCheck, Brain, CheckCircle2, ChevronDown, ChevronUp, Crosshair, DatabaseZap, Gauge, Layers, Search, ShieldAlert, TrendingUp } from 'lucide-react';
import { Link } from 'react-router-dom';
import { AppPage, Badge, Button, Card, DataFreshnessBadge, DecisionExplanation as DecisionExplanationPanel, EmptyState, InlineAlert, Input } from '../components/common';
import type { DecisionExplanationData } from '../components/common';
import { researchApi, type MarketBreadthResponse, type MarketRegimeResponse, type PositioningResponse } from '../api/research';

type AssetType = 'stock' | 'crypto';
type ExplanationTone = 'info' | 'success' | 'warning' | 'danger';

type DecisionExplanation = {
  title: string;
  decision: string;
  tone: ExplanationTone;
  plainEnglish: string;
  why: string[];
  howToUse: string;
};

const asNumber = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return null;
};

const asString = (value: unknown): string | null => {
  if (typeof value === 'string' && value.trim()) return value;
  return null;
};

const formatNumber = (value: unknown, digits = 2): string => {
  const parsed = asNumber(value);
  if (parsed === null) return 'N/A';
  if (Math.abs(parsed) >= 1_000_000_000) return `${(parsed / 1_000_000_000).toFixed(2)}B`;
  if (Math.abs(parsed) >= 1_000_000) return `${(parsed / 1_000_000).toFixed(2)}M`;
  if (Math.abs(parsed) >= 10_000) return parsed.toFixed(0);
  return parsed.toFixed(digits);
};

const formatPercent = (value: unknown): string => {
  const parsed = asNumber(value);
  return parsed === null ? 'N/A' : `${parsed.toFixed(1)}%`;
};

const formatMaybePercent = (value: unknown): string => {
  const parsed = asNumber(value);
  return parsed === null ? 'N/A' : `${parsed.toFixed(1)}%`;
};

const formatCurrency = (value: unknown): string => {
  const parsed = asNumber(value);
  return parsed === null ? 'N/A' : `$${formatNumber(parsed, 0)}`;
};

const formatCountWithFallback = (value: unknown, fallback: string): string => {
  const parsed = asNumber(value);
  return parsed === null ? fallback : formatNumber(parsed, 0);
};

const scoreBand = (score: number | null): string => {
  if (score === null) return 'unknown';
  if (score >= 80) return 'strong';
  if (score >= 60) return 'okay';
  if (score >= 40) return 'mixed';
  return 'weak';
};

const scoreRead = (score: number | null): string => {
  const band = scoreBand(score);
  const label = band.charAt(0).toUpperCase() + band.slice(1);
  return `${label}: ${score === null ? 'N/A' : formatNumber(score, 1)}/100`;
};

const componentLabel = (label: unknown): string => {
  const normalized = (asString(label) || '').toLowerCase();
  if (normalized.includes('index')) return 'Major indexes';
  if (normalized.includes('breadth')) return 'Stock participation';
  if (normalized.includes('rally')) return 'Rally participation';
  if (normalized.includes('macro')) return 'Macro backdrop';
  return asString(label) || 'Market check';
};

const componentPlainEnglish = (
  label: unknown,
  score: number | null,
  marketBreadth: Partial<MarketBreadthResponse>,
  sectorBreadth: Record<string, unknown>,
): string => {
  const normalized = (asString(label) || '').toLowerCase();
  const band = scoreBand(score);
  const prefix = band.charAt(0).toUpperCase() + band.slice(1);

  if (normalized.includes('index')) {
    return `${prefix}: SPY, QQQ, and IWM are being checked against their 50-day and 200-day moving averages.`;
  }
  if (normalized.includes('breadth')) {
    if (marketBreadth.status === 'completed') {
      return `${prefix}: ${formatMaybePercent(marketBreadth.above_sma50_pct)} of liquid stocks are above their 50-day average and ${formatMaybePercent(marketBreadth.above_sma200_pct)} are above their 200-day average.`;
    }
    return `${prefix}: using sector ETF breadth because the full stock breadth cache is not available. Sector ETF 50-day breadth is ${formatMaybePercent(sectorBreadth.above_50_pct)}.`;
  }
  if (normalized.includes('rally')) {
    return `${prefix}: this asks whether the rally is broad, not just carried by a few mega-cap winners.`;
  }
  if (normalized.includes('macro')) {
    return `${prefix}: rates, credit, and the dollar are background pressure. They should size your aggression, not create a trade by themselves.`;
  }
  return asString(label) || 'Market check';
};

const ratioMeaning = (pair: string): string => {
  const meanings: Record<string, string> = {
    'IWM/SPY': 'Small caps are lagging large caps',
    'RSP/SPY': 'The average stock is lagging the mega-cap index',
    'HYG/TLT': 'Credit risk appetite is not strong enough',
    'QQQ/SPY': 'Growth and tech are not leading clearly',
  };
  return meanings[pair] || pair;
};

const readableRegimeWarning = (warning: string): string => {
  if (warning.includes('RESEARCH_BREADTH_SYMBOL_LIMIT') || warning.includes('capped free-tier universe')) {
    return 'The breadth scan is capped in free-data mode, so it may not include every listed US stock.';
  }
  return warning;
};

const buildMarketRegimeExplanation = (
  regime: MarketRegimeResponse,
  marketBreadth: Partial<MarketBreadthResponse>,
  sectorBreadth: Record<string, unknown>,
  riskRatios: Array<Record<string, unknown>>,
): DecisionExplanationData => {
  const componentEvidence = (regime.components || []).slice(0, 4).map((item) => ({
    label: componentLabel(item.label),
    value: scoreRead(asNumber(item.score)),
    source: componentPlainEnglish(item.label, asNumber(item.score), marketBreadth, sectorBreadth),
  }));
  const weights = (regime.components || [])
    .filter((item) => asNumber(item.score) !== null && asNumber(item.weight) !== null)
    .map((item) => {
      const weight = asNumber(item.weight) || 0;
      return `${componentLabel(item.label)} ${formatNumber(item.score, 1)} x ${formatNumber(weight * 100, 0)}%`;
    })
    .join(' + ');
  const weakRatios = riskRatios
    .filter((item) => !item.risk_on)
    .map((item) => asString(item.pair) || asString(item.label))
    .filter((item): item is string => Boolean(item))
    .slice(0, 3);
  const weakRatioMeanings = weakRatios.map(ratioMeaning);
  const breadthWarnings = Array.isArray(marketBreadth.warnings) ? marketBreadth.warnings.slice(0, 2) : [];
  const breadthSource = marketBreadth.status === 'completed'
    ? 'Daily breadth cache'
    : 'Sector ETF proxy';
  const regimeMeaning = regime.regime === 'Risk-On'
    ? 'Risk-on means the market backdrop is helping momentum trades. You can be more open to breakouts, while still requiring clean entries.'
    : regime.regime === 'Risk-Off'
      ? 'Risk-off means the backdrop is hostile. Breakouts are more likely to fail, so cash, smaller size, or only the cleanest setups make more sense.'
      : 'Neutral means the backdrop is usable but not easy. Strong setups can work, but the app should not treat every strong stock as automatically buyable.';

  return {
    title: `Why the market backdrop is ${regime.regime}`,
    decision: `${regime.regime}: ${formatNumber(regime.score, 1)}/100`,
    plainEnglish: regimeMeaning,
    evidence: [
      ...componentEvidence,
      {
        label: 'Stocks counted',
        value: marketBreadth.status === 'completed'
          ? `${formatNumber(marketBreadth.symbols_passing_liquidity, 0)} stocks counted`
          : `${formatMaybePercent(sectorBreadth.above_50_pct)} sector ETFs above 50DMA`,
        source: breadthSource,
      },
      {
        label: 'What is holding it back',
        value: weakRatioMeanings.length ? weakRatioMeanings.join('; ') : 'No major rally-participation drag',
        source: weakRatios.length ? `Checks behind this: ${weakRatios.join(', ')}` : 'Risk-ratio checks',
      },
    ],
    math: [
      {
        formula: 'Final score',
        result: `${weights || 'available checks'} = ${formatNumber(regime.score, 1)}/100`,
      },
      {
        formula: 'Stock participation score',
        result: marketBreadth.status === 'completed'
          ? `55% weight on stocks above the 50-day average plus 45% weight on stocks above the 200-day average. Here: ${formatMaybePercent(marketBreadth.above_sma50_pct)} and ${formatMaybePercent(marketBreadth.above_sma200_pct)}.`
          : 'ETF proxy used until the full stock cache is available.',
      },
    ],
    confidence: {
      level: regime.confidence,
      reason: 'Confidence is about source coverage and freshness. It is not a promise that the market will keep moving this way.',
    },
    warnings: [
      ...breadthWarnings,
      ...regime.caveats.slice(0, 2),
    ].map(readableRegimeWarning),
    whatWouldChange: [
      'If cached breadth drops below 50% above the 50DMA, the tape gets weaker.',
      'If small caps, equal-weight stocks, and credit start helping the rally, rally health improves.',
      'If SPY, QQQ, or IWM lose the 200DMA, the index trend score weakens fast.',
    ],
    guardrails: [
      'This is a backdrop filter, not a buy signal.',
      'A strong regime still needs a clean setup, entry zone, and invalidation level.',
    ],
  };
};

const riskVariant = (value: unknown): 'success' | 'warning' | 'danger' | 'default' => {
  if (value === 'low' || value === 'high') return value === 'low' ? 'success' : 'danger';
  if (value === 'medium') return 'warning';
  return 'default';
};

const sourceVariant = (value: unknown): 'success' | 'warning' | 'default' => {
  if (value === 'ok' || value === 'available') return 'success';
  if (value === 'missing' || value === 'not_built_yet' || value === 'degraded') return 'warning';
  return 'default';
};

const regimeVariant = (value: unknown): 'success' | 'warning' | 'danger' | 'default' => {
  if (value === 'Risk-On') return 'success';
  if (value === 'Risk-Off') return 'danger';
  if (value === 'Neutral') return 'warning';
  return 'default';
};

const LOOKOUT_ITEMS = [
  {
    label: 'Gamma flip',
    detail: 'Spot above flip can dampen moves; below flip can make momentum expand fast.',
  },
  {
    label: 'Call wall',
    detail: 'Treat as possible resistance until price accepts above it with volume.',
  },
  {
    label: 'Put wall',
    detail: 'Treat as possible support; losing it can make downside hedging more reflexive.',
  },
  {
    label: 'Put/call crowding',
    detail: 'Low put/call OI means bullish crowding; high ratios mean fear or hedging pressure.',
  },
  {
    label: 'Short pressure',
    detail: 'High short float plus upside breakout can fuel squeezes; weak volume can trap longs.',
  },
  {
    label: 'Data quality',
    detail: 'Trust paid/official sources more. Treat yfinance-only gamma as useful but lower conviction.',
  },
];

const compactList = (items: Array<string | null | undefined>): string[] => items.filter((item): item is string => Boolean(item));

const readableRegime = (value: unknown): string => (asString(value) || 'unknown').replace(/[_-]/g, ' ');

const buildBiasExplanation = (
  analysis: PositioningResponse,
  gamma: Record<string, unknown>,
  crowding: Record<string, unknown>,
  shortInterest: Record<string, unknown>,
  cot: Record<string, unknown>,
): DecisionExplanation => {
  const bias = analysis.positioning_bias;
  const putCallOi = asNumber(gamma.put_call_oi_ratio);
  const shortFloat = asNumber(shortInterest.short_percent_float);
  const cotNet = asNumber(cot.noncommercial_net_oi_pct);
  const riskLevel = asString(crowding.risk_level) || 'unknown';
  const regime = readableRegime(gamma.gamma_regime);

  const explanations: Record<string, string> = {
    'squeeze-prone but crowded': 'Short interest is elevated and options positioning leans bullish. That can fuel a fast upside move, but it also means a lot of people may already be leaning the same way.',
    'trend-prone / unstable': 'Options positioning can amplify price moves instead of calming them down. Breakouts and breakdowns can travel farther than expected.',
    'balanced positive-gamma': 'Options positioning looks less one-sided and market-maker hedging may absorb some movement. This can make price action more range-bound unless a fresh catalyst appears.',
    'bullish but call-crowded': 'Options open interest is tilted toward calls. That shows bullish appetite, but it can also mean the easy bullish trade is already crowded.',
    'macro headwind': 'The mapped macro futures market is positioned against the asset. That does not kill the trade, but it is a reason to demand cleaner confirmation.',
    'neutral / data-dependent': 'The app does not see a strong enough positioning edge. The setup needs more confirmation from price, volume, catalysts, or better data coverage.',
  };

  return {
    title: 'Bias',
    decision: readableRegime(bias),
    tone: bias.includes('crowded') || bias.includes('headwind') || bias.includes('unstable') ? 'warning' : bias.includes('balanced') ? 'success' : 'info',
    plainEnglish: explanations[bias] || explanations['neutral / data-dependent'],
    why: compactList([
      `Gamma regime: ${regime}`,
      putCallOi !== null ? `Put/call open interest: ${putCallOi.toFixed(2)}` : null,
      shortFloat !== null ? `Short float: ${shortFloat.toFixed(1)}%` : null,
      cotNet !== null ? `Macro COT net positioning: ${cotNet.toFixed(1)}% of open interest` : null,
      `Crowding risk: ${riskLevel}`,
    ]),
    howToUse: 'Use bias as a setup filter, not a trade command. A bullish read still needs a clean entry, invalidation level, and room before the trade gets crowded.',
  };
};

const buildGammaExplanation = (
  analysis: PositioningResponse,
  gamma: Record<string, unknown>,
): DecisionExplanation => {
  const regime = asString(gamma.gamma_regime) || 'unknown';
  const spot = asNumber(analysis.underlying_price);
  const flip = asNumber(gamma.gamma_flip_level);
  const netGex = asNumber(gamma.net_dollar_gamma_1pct);
  const callWall = (gamma.call_wall ?? {}) as Record<string, unknown>;
  const putWall = (gamma.put_wall ?? {}) as Record<string, unknown>;

  const plainEnglish = regime === 'positive_gamma'
    ? 'Positive gamma means options hedging may dampen price moves. In plain terms, the stock can chop or pin near big options levels unless a catalyst forces it away.'
    : regime === 'negative_gamma'
      ? 'Negative gamma means options hedging may chase price moves. In plain terms, a move can feed on itself and get faster in either direction.'
      : 'The app does not have enough usable gamma data to describe the options regime with confidence.';

  return {
    title: 'Gamma Regime',
    decision: readableRegime(regime),
    tone: regime === 'negative_gamma' ? 'warning' : regime === 'positive_gamma' ? 'success' : 'info',
    plainEnglish,
    why: compactList([
      netGex !== null ? `Net GEX per 1% move: ${formatNumber(netGex)}` : null,
      flip !== null ? `Gamma flip: ${formatNumber(flip)}` : null,
      spot !== null && flip !== null ? `Spot is ${spot >= flip ? 'above' : 'below'} the flip` : null,
      asNumber(callWall.strike) !== null ? `Call wall: ${formatNumber(callWall.strike)}` : null,
      asNumber(putWall.strike) !== null ? `Put wall: ${formatNumber(putWall.strike)}` : null,
    ]),
    howToUse: 'Watch what price does around the flip, call wall, and put wall. These are not magic levels; they are areas where hedging pressure can change.',
  };
};

const buildCrowdingExplanation = (
  crowding: Record<string, unknown>,
  gamma: Record<string, unknown>,
  shortInterest: Record<string, unknown>,
): DecisionExplanation => {
  const score = asNumber(crowding.crowding_risk_score);
  const level = asString(crowding.risk_level) || 'unknown';
  const rawFlags = crowding.flags;
  const flags = Array.isArray(rawFlags) ? rawFlags as Array<Record<string, unknown>> : [];
  const shortFloat = asNumber(shortInterest.short_percent_float);
  const putCallOi = asNumber(gamma.put_call_oi_ratio);

  const plainEnglish = level === 'high'
    ? 'High crowding means the trade may already be busy. It can still work, but reversals get nastier because too many people may need to exit at once.'
    : level === 'medium'
      ? 'Medium crowding means there are some signs the trade is getting popular or hedged heavily. You should be more selective with entries.'
      : level === 'low'
        ? 'Low crowding means the app is not seeing many positioning stress signals. That does not make the trade safe, but it means the setup is less obviously packed.'
        : 'The app does not have enough crowding data to make a clean read.';

  return {
    title: 'Crowding Risk',
    decision: score !== null ? `${score.toFixed(1)}/100 ${level}` : level,
    tone: riskVariant(level) === 'danger' ? 'danger' : riskVariant(level) === 'warning' ? 'warning' : riskVariant(level) === 'success' ? 'success' : 'info',
    plainEnglish,
    why: compactList([
      flags.length > 0 ? `${flags.length} crowding flag${flags.length === 1 ? '' : 's'} fired` : 'No major crowding flags fired',
      putCallOi !== null ? `Put/call open interest: ${putCallOi.toFixed(2)}` : null,
      shortFloat !== null ? `Short float: ${shortFloat.toFixed(1)}%` : null,
      ...flags.slice(0, 3).map((flag) => asString(flag.label)),
    ]),
    howToUse: 'When crowding is high, size smaller, demand better confirmation, and know the exit before entry. Low crowding is permission to investigate, not permission to gamble.',
  };
};

const buildConfidenceExplanation = (
  analysis: PositioningResponse,
  confidence: Record<string, unknown>,
  gamma: Record<string, unknown>,
): DecisionExplanation => {
  const score = asNumber(confidence.score);
  const label = asString(confidence.label) || 'unknown';
  const okSources = (analysis.sources || []).filter((source) => asString(source.status) === 'ok').length;
  const missingSources = (analysis.sources || []).filter((source) => asString(source.status) === 'missing').length;
  const contractsWithGamma = asNumber(gamma.contracts_with_gamma);

  return {
    title: 'Confidence',
    decision: score !== null ? `${score}/100 ${label}` : label,
    tone: label === 'high' ? 'success' : label === 'medium' ? 'warning' : 'info',
    plainEnglish: 'Confidence is about data quality, not prediction certainty. A high confidence score means the app had more usable sources; it does not mean the trade will work.',
    why: compactList([
      `${okSources} source group${okSources === 1 ? '' : 's'} usable`,
      missingSources > 0 ? `${missingSources} source group${missingSources === 1 ? '' : 's'} missing` : null,
      contractsWithGamma !== null ? `${formatNumber(contractsWithGamma, 0)} option contracts had gamma data` : null,
      analysis.data_gaps.length > 0 ? `${analysis.data_gaps.length} data gap${analysis.data_gaps.length === 1 ? '' : 's'} reported` : null,
    ]),
    howToUse: 'Trust the read more when confidence is high. When confidence is low, treat the page as a checklist of missing evidence rather than a signal.',
  };
};

const buildDecisionExplanations = (
  analysis: PositioningResponse,
  gamma: Record<string, unknown>,
  crowding: Record<string, unknown>,
  confidence: Record<string, unknown>,
  shortInterest: Record<string, unknown>,
  cot: Record<string, unknown>,
): DecisionExplanation[] => [
  buildBiasExplanation(analysis, gamma, crowding, shortInterest, cot),
  buildGammaExplanation(analysis, gamma),
  buildCrowdingExplanation(crowding, gamma, shortInterest),
  buildConfidenceExplanation(analysis, confidence, gamma),
];

const PositioningPage: React.FC = () => {
  const [symbol, setSymbol] = useState('NVDA');
  const [assetType, setAssetType] = useState<AssetType>('stock');
  const [analysis, setAnalysis] = useState<PositioningResponse | null>(null);
  const [marketRegime, setMarketRegime] = useState<MarketRegimeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [regimeLoading, setRegimeLoading] = useState(false);
  const [breadthRunning, setBreadthRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [regimeError, setRegimeError] = useState<string | null>(null);
  const [breadthError, setBreadthError] = useState<string | null>(null);
  const [showRegimeWork, setShowRegimeWork] = useState(false);
  const [showBreadthWork, setShowBreadthWork] = useState(false);
  const [showMarketInputs, setShowMarketInputs] = useState(false);
  const [showPositioningWork, setShowPositioningWork] = useState(false);
  const [showPositioningDetails, setShowPositioningDetails] = useState(false);
  const [showLearning, setShowLearning] = useState(false);

  const loadPositioning = useCallback(async (nextSymbol = symbol, nextAssetType = assetType) => {
    const cleaned = nextSymbol.trim().toUpperCase();
    if (!cleaned) return;
    setLoading(true);
    setError(null);
    try {
      const payload = await researchApi.getPositioning(cleaned, nextAssetType);
      setAnalysis(payload);
      setSymbol(payload.symbol);
    } catch {
      setError('Positioning scan failed. Check provider settings and try again.');
    } finally {
      setLoading(false);
    }
  }, [assetType, symbol]);

  const loadMarketRegime = useCallback(async () => {
    setRegimeLoading(true);
    setRegimeError(null);
    try {
      setMarketRegime(await researchApi.getMarketRegime());
    } catch {
      setRegimeError('Market regime could not load. The page still works, but the tape filter is unavailable.');
    } finally {
      setRegimeLoading(false);
    }
  }, []);

  const runDailyBreadthCache = useCallback(async () => {
    setBreadthRunning(true);
    setBreadthError(null);
    try {
      await researchApi.runMarketBreadthCache({ universe: 'us_stocks' });
      setMarketRegime(await researchApi.getMarketRegime());
    } catch {
      setBreadthError('Daily breadth cache failed. Check market-data limits, then try again with a smaller RESEARCH_BREADTH_SYMBOL_LIMIT.');
    } finally {
      setBreadthRunning(false);
    }
  }, []);

  useEffect(() => {
    document.title = 'Positioning - DSA';
    void loadPositioning('NVDA', 'stock');
    void loadMarketRegime();
  }, []);

  const gamma = analysis?.gamma ?? {};
  const crowding = analysis?.crowding ?? {};
  const confidence = analysis?.confidence ?? {};
  const shortInterest = (analysis?.short_pressure?.short_interest ?? {}) as Record<string, unknown>;
  const ftd = (analysis?.short_pressure?.fails_to_deliver ?? {}) as Record<string, unknown>;
  const cot = analysis?.cot_macro_context ?? {};

  const callWall = (gamma.call_wall ?? {}) as Record<string, unknown>;
  const putWall = (gamma.put_wall ?? {}) as Record<string, unknown>;
  const flags = useMemo(() => {
    const raw = crowding.flags;
    return Array.isArray(raw) ? raw as Array<Record<string, unknown>> : [];
  }, [crowding.flags]);
  const unusual = useMemo(() => {
    const raw = gamma.unusual_activity;
    return Array.isArray(raw) ? raw as Array<Record<string, unknown>> : [];
  }, [gamma.unusual_activity]);
  const decisionExplanations = analysis ? buildDecisionExplanations(analysis, gamma, crowding, confidence, shortInterest, cot) : [];

  return (
    <AppPage>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="label-uppercase">Research</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Positioning</h1>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <Input
            label="Symbol"
            value={symbol}
            onChange={(event) => setSymbol(event.target.value.toUpperCase())}
            className="h-10 w-32"
            onKeyDown={(event) => {
              if (event.key === 'Enter') void loadPositioning();
            }}
          />
          <div className="flex h-10 overflow-hidden rounded-lg border border-border/70 bg-card">
            {(['stock', 'crypto'] as const).map((item) => (
              <button
                key={item}
                type="button"
                className={assetType === item ? 'px-3 text-sm font-medium text-cyan' : 'px-3 text-sm text-secondary-text hover:text-foreground'}
                onClick={() => setAssetType(item)}
              >
                {item}
              </button>
            ))}
          </div>
          <Button onClick={() => void loadPositioning()} isLoading={loading} loadingText="Scanning">
            <Search className="h-4 w-4" />
            Scan
          </Button>
        </div>
      </div>

      {error ? <InlineAlert variant="danger" title="Positioning error" message={error} className="mb-4" /> : null}

      <div className="space-y-4">
        {regimeError ? <InlineAlert variant="warning" title="Market regime unavailable" message={regimeError} /> : null}
        {breadthError ? <InlineAlert variant="warning" title="Breadth cache failed" message={breadthError} /> : null}

        <MarketRegimePanel
          regime={marketRegime}
          loading={regimeLoading}
          onRefresh={() => void loadMarketRegime()}
          onRunBreadth={() => void runDailyBreadthCache()}
          breadthRunning={breadthRunning}
          showRegimeWork={showRegimeWork}
          onToggleRegimeWork={() => setShowRegimeWork((value) => !value)}
          showBreadthWork={showBreadthWork}
          onToggleBreadthWork={() => setShowBreadthWork((value) => !value)}
          showMarketInputs={showMarketInputs}
          onToggleMarketInputs={() => setShowMarketInputs((value) => !value)}
        />

        {loading && !analysis ? (
          <InlineAlert
            variant="info"
            title="Live scan running"
            message="Loading options chain, short interest, SEC fails-to-deliver, and macro positioning. First scan can take 10-20 seconds."
          />
        ) : null}

        {!analysis && !loading ? (
          <EmptyState icon={<Crosshair className="h-6 w-6" />} title="No positioning scan" description="Run a symbol scan." />
        ) : null}

        {analysis ? (
          <>
          <div className="grid gap-3 md:grid-cols-4">
            <SummaryCard icon={<Crosshair className="h-5 w-5" />} label="Bias" value={readableRegime(analysis.positioning_bias)} />
            <SummaryCard icon={<Gauge className="h-5 w-5" />} label="Confidence" value={`${asNumber(confidence.score) ?? 0}/100`} badge={asString(confidence.label) || undefined} />
            <SummaryCard icon={<Activity className="h-5 w-5" />} label="Gamma Regime" value={readableRegime(gamma.gamma_regime)} />
            <SummaryCard icon={<ShieldAlert className="h-5 w-5" />} label="Crowding Risk" value={`${formatNumber(crowding.crowding_risk_score, 1)}/100`} badge={asString(crowding.risk_level) || undefined} badgeVariant={riskVariant(crowding.risk_level)} />
          </div>

          <PositioningReadPanel
            analysis={analysis}
            gamma={gamma}
            callWall={callWall}
            putWall={putWall}
            explanations={decisionExplanations}
            showWork={showPositioningWork}
            onToggleWork={() => setShowPositioningWork((value) => !value)}
            showDetails={showPositioningDetails}
            onToggleDetails={() => setShowPositioningDetails((value) => !value)}
          />

          <section>
            {showPositioningWork ? (
              <>
                <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
                  <div>
                    <p className="label-uppercase">Show Work</p>
                    <h2 className="mt-1 text-lg font-semibold text-foreground">Full reasoning</h2>
                  </div>
                  <p className="max-w-2xl text-sm leading-6 text-secondary-text">
                    These are explanations of the positioning read. They are not buy or sell instructions.
                  </p>
                </div>
                <div className="grid gap-3 xl:grid-cols-2">
                  {decisionExplanations.map((item) => (
                    <ReasonCard key={item.title} explanation={item} />
                  ))}
                </div>
              </>
            ) : null}
          </section>

          {showPositioningDetails ? (
          <>
          <div className="grid gap-4 xl:grid-cols-[1.25fr_0.75fr]">
            <Card title={`${analysis.symbol} Gamma Map`} subtitle={analysis.as_of} padding="md" className="rounded-lg">
              <div className="grid gap-2 sm:grid-cols-3">
                <Info label="Spot" value={formatNumber(analysis.underlying_price)} />
                <Info label="Net GEX / 1%" value={formatNumber(gamma.net_dollar_gamma_1pct)} />
                <Info label="Gamma Flip" value={formatNumber(gamma.gamma_flip_level)} />
                <Info label="Call Wall" value={formatNumber(callWall.strike)} detail={asString(callWall.expiration) || undefined} />
                <Info label="Put Wall" value={formatNumber(putWall.strike)} detail={asString(putWall.expiration) || undefined} />
                <Info label="Max Pain" value={formatNumber(gamma.max_pain)} />
                <Info label="Put/Call OI" value={formatNumber(gamma.put_call_oi_ratio, 3)} />
                <Info label="Put/Call Volume" value={formatNumber(gamma.put_call_volume_ratio, 3)} />
                <Info label="Contracts With Gamma" value={`${formatNumber(gamma.contracts_with_gamma, 0)} / ${formatNumber(gamma.contract_count, 0)}`} />
              </div>
              <p className="mt-3 text-xs leading-relaxed text-secondary-text">{asString(gamma.sign_convention)}</p>
            </Card>

            <Card title="Source Coverage" padding="md" className="rounded-lg">
              <div className="space-y-2">
                {(analysis.sources || []).map((source, index) => (
                  <div key={`${source.name}-${index}`} className="flex items-center justify-between gap-3 rounded-lg border border-border/60 px-3 py-2">
                    <div>
                      <p className="text-sm font-medium text-foreground">{asString(source.name) || 'Source'}</p>
                      <p className="text-xs text-secondary-text">{asString(source.category) || 'data'}</p>
                    </div>
                    <Badge variant={sourceVariant(source.status)}>{asString(source.status) || 'unknown'}</Badge>
                  </div>
                ))}
              </div>
            </Card>
          </div>

          <div className="grid gap-4 xl:grid-cols-3">
            <Card title="Short Pressure" padding="md" className="rounded-lg">
              <div className="space-y-2">
                <Info label="Short Float" value={formatPercent(shortInterest.short_percent_float)} />
                <Info label="Days To Cover" value={formatNumber(shortInterest.short_ratio_days_to_cover, 2)} />
                <Info label="Shares Short" value={formatNumber(shortInterest.shares_short, 0)} />
                <Info label="Latest FTD Notional" value={formatNumber(ftd.latest_notional, 2)} />
              </div>
            </Card>

            <Card title="Macro COT" padding="md" className="rounded-lg">
              <div className="space-y-2">
                <Info label="Mapped Market" value={asString(cot.market) || 'N/A'} />
                <Info label="Report Date" value={asString(cot.report_date) || 'N/A'} />
                <Info label="Non-Commercial Net" value={formatNumber(cot.noncommercial_net, 0)} />
                <Info label="Net / OI" value={formatPercent(cot.noncommercial_net_oi_pct)} />
              </div>
            </Card>

            <Card title="Crowding Flags" padding="md" className="rounded-lg">
              {flags.length === 0 ? (
                <p className="text-sm text-secondary-text">No crowding flags.</p>
              ) : (
                <div className="space-y-2">
                  {flags.map((flag, index) => (
                    <div key={index} className="rounded-lg border border-border/60 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <AlertTriangle className="h-4 w-4 text-warning" />
                        <p className="text-sm font-medium text-foreground">{asString(flag.label) || 'Flag'}</p>
                      </div>
                      <p className="mt-1 text-xs text-secondary-text">{asString(flag.detail) || ''}</p>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
          </>
          ) : null}

          {showPositioningDetails ? (
          <div className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
            <Card title="What To Watch" padding="md" className="rounded-lg">
              <div className="space-y-2">
                {(analysis.what_to_watch || []).map((item, index) => (
                  <div key={index} className="flex gap-2 rounded-lg border border-border/60 px-3 py-2 text-sm text-foreground">
                    <Crosshair className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            </Card>

            <Card title="Unusual Options" padding="md" className="rounded-lg">
              {unusual.length === 0 ? (
                <p className="text-sm text-secondary-text">No elevated volume-to-open-interest contracts.</p>
              ) : (
                <>
                  <div className="space-y-2 md:hidden">
                    {unusual.map((item, index) => (
                      <div key={index} className="rounded-lg border border-border/60 px-3 py-2">
                        <p className="break-all text-sm font-medium text-foreground">{asString(item.contract_symbol) || '-'}</p>
                        <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-secondary-text">
                          <span>{asString(item.option_type) || '-'}</span>
                          <span>{formatNumber(item.strike)}</span>
                          <span>{asString(item.expiration) || '-'}</span>
                          <span>{formatNumber(item.volume_to_oi, 2)} vol/OI</span>
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="hidden overflow-x-auto md:block">
                    <table className="w-full min-w-[560px] text-left text-sm">
                      <thead className="text-xs uppercase text-secondary-text">
                        <tr>
                          <th className="pb-2">Contract</th>
                          <th className="pb-2">Type</th>
                          <th className="pb-2">Strike</th>
                          <th className="pb-2">Expiration</th>
                          <th className="pb-2">Volume/OI</th>
                        </tr>
                      </thead>
                      <tbody>
                        {unusual.map((item, index) => (
                          <tr key={index} className="border-t border-border/50">
                            <td className="py-2 text-foreground">{asString(item.contract_symbol) || '-'}</td>
                            <td className="py-2 text-secondary-text">{asString(item.option_type) || '-'}</td>
                            <td className="py-2 text-secondary-text">{formatNumber(item.strike)}</td>
                            <td className="py-2 text-secondary-text">{asString(item.expiration) || '-'}</td>
                            <td className="py-2 text-secondary-text">{formatNumber(item.volume_to_oi, 2)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </Card>
          </div>
          ) : null}

          {analysis.data_gaps.length > 0 ? (
            <InlineAlert
              variant="warning"
              title="Data gaps"
              message={(
                <ul className="space-y-1">
                  {analysis.data_gaps.slice(0, showPositioningDetails ? 8 : 3).map((item) => <li key={item}>{item}</li>)}
                </ul>
              )}
              action={<DatabaseZap className="h-5 w-5" />}
            />
          ) : null}

          <CollapsibleCard
            title="Learning And Methodology"
            subtitle="Definitions, checklist, and calculation notes"
            open={showLearning}
            onToggle={() => setShowLearning((value) => !value)}
          >
            <div className="space-y-4">
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {LOOKOUT_ITEMS.map((item) => (
                  <LookoutItem key={item.label} label={item.label} detail={item.detail} />
                ))}
              </div>

              <div className="grid gap-5 xl:grid-cols-[0.95fr_1.05fr]">
                <div>
                  <div className="mb-3 flex items-center gap-2">
                    <BookOpenCheck className="h-5 w-5 text-cyan" />
                    <h3 className="text-base font-semibold text-foreground">Simple workflow</h3>
                  </div>
                  <ol className="space-y-2 text-sm leading-6 text-secondary-text">
                    <li><span className="font-medium text-foreground">1.</span> Check the plain-English bias first.</li>
                    <li><span className="font-medium text-foreground">2.</span> Look at crowding risk before getting excited.</li>
                    <li><span className="font-medium text-foreground">3.</span> Mark the gamma flip, call wall, and put wall as levels to watch.</li>
                    <li><span className="font-medium text-foreground">4.</span> Read data gaps before trusting the signal.</li>
                    <li><span className="font-medium text-foreground">5.</span> Only then decide entry, invalidation, and position size.</li>
                  </ol>
                  <Link to="/guide" className="mt-4 inline-flex items-center gap-2 text-sm font-medium text-cyan hover:text-cyan/80">
                    Open the full guide
                  </Link>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <DefinitionItem term="Bias" definition="The app's best summary of current positioning. It is a lens, not a trade order." />
                  <DefinitionItem term="Gamma regime" definition="Whether options hedging is more likely to calm moves or amplify them." />
                  <DefinitionItem term="Crowding risk" definition="How packed or fragile the trade may be based on options, shorts, and positioning." />
                  <DefinitionItem term="Confidence" definition="How much usable data the app had. It is not certainty that the trade wins." />
                  <DefinitionItem term="Call wall" definition="A big call-options strike that can act like resistance until price breaks through cleanly." />
                  <DefinitionItem term="Put wall" definition="A big put-options strike that can act like support until it breaks." />
                </div>
              </div>

              <div className="space-y-2 rounded-lg border border-border/60 bg-elevated/45 p-3">
                {(analysis.methodology || []).map((item) => (
                  <div key={item} className="flex gap-2 text-sm text-secondary-text">
                    <Layers className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            </div>
          </CollapsibleCard>
          </>
        ) : null}
      </div>
    </AppPage>
  );
};

const MarketRegimePanel: React.FC<{
  regime: MarketRegimeResponse | null;
  loading: boolean;
  onRefresh: () => void;
  onRunBreadth: () => void;
  breadthRunning: boolean;
  showRegimeWork: boolean;
  onToggleRegimeWork: () => void;
  showBreadthWork: boolean;
  onToggleBreadthWork: () => void;
  showMarketInputs: boolean;
  onToggleMarketInputs: () => void;
}> = ({
  regime,
  loading,
  onRefresh,
  onRunBreadth,
  breadthRunning,
  showRegimeWork,
  onToggleRegimeWork,
  showBreadthWork,
  onToggleBreadthWork,
  showMarketInputs,
  onToggleMarketInputs,
}) => {
  const indexTrends = regime?.index_trends ?? [];
  const riskRatios = regime?.risk_ratios ?? [];
  const sectorBreadth = regime?.sector_breadth ?? {};
  const marketBreadth = (regime?.market_breadth ?? {}) as Partial<MarketBreadthResponse>;
  const breadthStatus = regime?.breadth_status ?? {};
  const dataSources = regime?.data_sources ?? [];
  const haveNow = Array.isArray(breadthStatus.have_now) ? breadthStatus.have_now as string[] : [];
  const missing = Array.isArray(breadthStatus.missing_for_full_breadth) ? breadthStatus.missing_for_full_breadth as string[] : [];
  const explanation = regime ? buildMarketRegimeExplanation(regime, marketBreadth, sectorBreadth, riskRatios) : null;

  return (
    <Card title="Market Regime" subtitle="Tape filter and data map" padding="md" className="rounded-lg">
      {loading && !regime ? (
        <div className="flex items-center gap-3 rounded-lg border border-border/60 bg-elevated/45 px-3 py-3 text-sm text-secondary-text">
          <div className="home-spinner h-4 w-4 animate-spin border-2" />
          Calculating ETF trend, risk ratios, sector breadth proxy, and macro backdrop...
        </div>
      ) : null}

      {regime ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="max-w-2xl">
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <Badge variant={regimeVariant(regime.regime)}>{regime.regime}</Badge>
                <Badge variant="info">{regime.confidence} confidence</Badge>
                <DataFreshnessBadge asOf={regime.as_of} />
              </div>
              <p className="text-sm leading-6 text-foreground">{regime.summary}</p>
              <p className="mt-2 text-xs leading-5 text-secondary-text">
                This is the market backdrop for momentum trades. It answers whether the tape is helping breakouts, mixed, or fragile.
              </p>
            </div>
            <div className="flex min-w-[11rem] flex-col items-start gap-2 rounded-lg border border-border/60 bg-elevated/45 px-4 py-3">
              <span className="text-xs uppercase tracking-normal text-secondary-text">Regime score</span>
              <span className="text-3xl font-semibold text-foreground">{formatNumber(regime.score, 1)}</span>
              <Button variant="secondary" size="xsm" onClick={onRefresh} isLoading={loading} loadingText="Refreshing">
                Refresh
              </Button>
            </div>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" size="xsm" onClick={onToggleRegimeWork}>
              {showRegimeWork ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              {showRegimeWork ? 'Hide regime work' : 'Show regime work'}
            </Button>
            <Button variant="ghost" size="xsm" onClick={onToggleMarketInputs}>
              {showMarketInputs ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              {showMarketInputs ? 'Hide market inputs' : 'Show market inputs'}
            </Button>
            <Button variant="ghost" size="xsm" onClick={onToggleBreadthWork}>
              {showBreadthWork ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
              {showBreadthWork ? 'Hide breadth cache' : 'Show breadth cache'}
            </Button>
          </div>

          {showRegimeWork && explanation ? <DecisionExplanationPanel data={explanation} compact /> : null}

          {showBreadthWork ? (
            <MarketBreadthAudit
              breadth={marketBreadth}
              onRun={onRunBreadth}
              running={breadthRunning}
              expanded={showBreadthWork}
              onToggleExpanded={onToggleBreadthWork}
            />
          ) : null}

          {showMarketInputs ? (
          <>
          <div className="grid gap-4 xl:grid-cols-[0.95fr_1.05fr]">
            <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
              <div className="mb-3 flex items-center gap-2">
                <TrendingUp className="h-4 w-4 text-cyan" />
                <h3 className="text-sm font-semibold text-foreground">Index and breadth proxy</h3>
              </div>
              <div className="grid gap-2 sm:grid-cols-3">
                {indexTrends.map((item) => (
                  <div key={asString(item.symbol) || JSON.stringify(item)} className="rounded-lg border border-border/50 bg-card/60 px-3 py-2">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-foreground">{asString(item.symbol) || '-'}</p>
                      <Badge variant={item.above_sma200 ? 'success' : 'warning'}>{item.above_sma200 ? 'above 200DMA' : 'below 200DMA'}</Badge>
                    </div>
                    <p className="text-xs text-secondary-text">Close {formatNumber(item.close)} / 50DMA {formatNumber(item.sma50)}</p>
                  </div>
                ))}
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <Info label="Sector ETFs above 50DMA" value={formatMaybePercent(sectorBreadth.above_50_pct)} />
                <Info label="Sector ETFs above 200DMA" value={formatMaybePercent(sectorBreadth.above_200_pct)} />
              </div>
            </div>

            <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
              <div className="mb-3 flex items-center gap-2">
                <BarChart3 className="h-4 w-4 text-cyan" />
                <h3 className="text-sm font-semibold text-foreground">Rally health checks</h3>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                {riskRatios.map((item) => (
                  <div key={asString(item.pair) || JSON.stringify(item)} className="rounded-lg border border-border/50 bg-card/60 px-3 py-2">
                    <div className="mb-1 flex items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-foreground">{asString(item.pair) || '-'}</p>
                      <Badge variant={item.risk_on ? 'success' : 'warning'}>{item.risk_on ? 'helping rally' : 'not helping rally'}</Badge>
                    </div>
                    <p className="text-xs leading-5 text-secondary-text">{asString(item.plain_english) || ''}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
              <div className="mb-3 flex items-center gap-2">
                <DatabaseZap className="h-4 w-4 text-cyan" />
                <h3 className="text-sm font-semibold text-foreground">Do we have the data?</h3>
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div>
                  <p className="mb-2 text-xs uppercase tracking-normal text-secondary-text">Have now</p>
                  <div className="space-y-1.5">
                    {haveNow.map((item) => (
                      <div key={item} className="flex gap-2 text-sm text-foreground">
                        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                        <span>{item}</span>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <p className="mb-2 text-xs uppercase tracking-normal text-secondary-text">Still missing</p>
                  <div className="space-y-1.5">
                    {missing.map((item) => (
                      <div key={item} className="flex gap-2 text-sm text-secondary-text">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                        <span>{item}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
              <p className="mt-3 text-xs leading-5 text-secondary-text">
                {asString(breadthStatus.plain_english) || 'The app can use the proxy now and add full breadth after the cache job exists.'}
              </p>
            </div>

            <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
              <div className="mb-3 flex items-center gap-2">
                <Layers className="h-4 w-4 text-cyan" />
                <h3 className="text-sm font-semibold text-foreground">Where it comes from</h3>
              </div>
              <div className="space-y-2">
                {dataSources.map((source) => (
                  <div key={asString(source.name) || JSON.stringify(source)} className="rounded-lg border border-border/50 bg-card/60 px-3 py-2">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="text-sm font-semibold text-foreground">{asString(source.name) || 'Data source'}</p>
                      <Badge variant={sourceVariant(source.status)}>{asString(source.status) || 'unknown'}</Badge>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-secondary-text">{asString(source.used_for) || ''}</p>
                    <p className="mt-1 text-[11px] leading-5 text-muted-text">{asString(source.caveat) || ''}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
          </>
          ) : null}
        </div>
      ) : !loading ? (
        <p className="text-sm text-secondary-text">Market regime has not loaded yet.</p>
      ) : null}
    </Card>
  );
};

const MarketBreadthAudit: React.FC<{
  breadth: Partial<MarketBreadthResponse>;
  onRun: () => void;
  running: boolean;
  expanded: boolean;
  onToggleExpanded: () => void;
}> = ({ breadth, onRun, running, expanded, onToggleExpanded }) => {
  const status = asString(breadth.status) || 'missing';
  const completed = status === 'completed';
  const universeSource = asString(breadth.universe_source)
    || (completed ? 'Nasdaq Trader universe plus default liquid names/watchlist' : 'not built yet');
  const steps = Array.isArray(breadth.calculation_steps) ? breadth.calculation_steps : [];
  const constituents = Array.isArray(breadth.sample_constituents) ? breadth.sample_constituents.slice(0, 12) : [];
  const failures = Array.isArray(breadth.failures) ? breadth.failures.slice(0, 5) : [];
  const warnings = Array.isArray(breadth.warnings) ? breadth.warnings : [];
  const sourceCounts = breadth.source_counts && typeof breadth.source_counts === 'object' ? Object.entries(breadth.source_counts) : [];

  return (
    <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">Daily stock breadth cache</h3>
            <Badge variant={completed ? 'success' : 'warning'}>{completed ? 'cached' : status}</Badge>
            {breadth.freshness ? <Badge variant={breadth.freshness === 'fresh' ? 'success' : 'warning'}>{breadth.freshness}</Badge> : null}
          </div>
          <p className="text-sm leading-6 text-secondary-text">
            {asString(breadth.summary) || 'No daily breadth cache has been built yet. Run it after market close to show stock-level breadth instead of only ETF proxies.'}
          </p>
          <p className="mt-1 text-xs leading-5 text-muted-text">
            Source universe: {universeSource}.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="ghost" size="xsm" onClick={onToggleExpanded}>
            {expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            {expanded ? 'Hide breadth work' : 'Show breadth work'}
          </Button>
          <Button variant="secondary" size="xsm" onClick={onRun} isLoading={running} loadingText="Running cache">
            <DatabaseZap className="h-4 w-4" />
            Run Daily Cache
          </Button>
        </div>
      </div>

      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
        <Info label="As Of" value={asString(breadth.as_of) || 'N/A'} detail={breadth.generated_at ? `Generated ${breadth.generated_at}` : undefined} />
        <Info label="Passed Gates" value={formatNumber(breadth.symbols_passing_liquidity, 0)} detail={`Price >= ${formatCurrency(breadth.min_price)}, ADV >= ${formatCurrency(breadth.min_avg_dollar_volume)}`} />
        <Info label="Above 50DMA" value={formatMaybePercent(breadth.above_sma50_pct)} detail={`${formatNumber(breadth.above_sma50_count, 0)} symbols`} />
        <Info label="Above 200DMA" value={formatMaybePercent(breadth.above_sma200_pct)} detail={`${formatNumber(breadth.above_sma200_count, 0)} symbols`} />
        {expanded ? (
          <>
            <Info label="Universe Scanned" value={`${formatNumber(breadth.symbols_scanned, 0)} / ${formatNumber(breadth.symbols_requested, 0)}`} detail={`${formatCountWithFallback(breadth.total_available, 'capped universe')} available before cap`} />
            <Info label="Data Coverage" value={formatNumber(breadth.symbols_with_data, 0)} detail="Symbols with usable daily bars" />
            <Info label="Above 20DMA" value={formatMaybePercent(breadth.above_sma20_pct)} detail={`${formatNumber(breadth.above_sma20_count, 0)} symbols`} />
            <Info label="52W High / Low" value={`${formatMaybePercent(breadth.new_high_52w_pct)} / ${formatMaybePercent(breadth.new_low_52w_pct)}`} detail={`${formatNumber(breadth.new_high_52w_count, 0)} highs, ${formatNumber(breadth.new_low_52w_count, 0)} lows`} />
          </>
        ) : null}
      </div>

      {expanded ? (
      <>
      <div className="mt-3 grid gap-3 xl:grid-cols-[1.15fr_0.85fr]">
        <div>
          <div className="mb-2 flex items-center gap-2">
            <Layers className="h-4 w-4 text-cyan" />
            <p className="text-xs uppercase tracking-normal text-secondary-text">How the answer was built</p>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            {steps.length > 0 ? steps.map((step) => (
              <div key={asString(step.step) || JSON.stringify(step)} className="rounded-lg border border-border/50 bg-card/60 px-3 py-2">
                <p className="text-sm font-semibold text-foreground">{asString(step.step) || 'Step'}</p>
                <p className="mt-1 text-xs leading-5 text-secondary-text">{asString(step.plain_english) || ''}</p>
              </div>
            )) : (
              <div className="rounded-lg border border-border/50 bg-card/60 px-3 py-2 text-sm text-secondary-text">
                Run the cache to show the exact calculation steps.
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <div>
            <p className="mb-2 text-xs uppercase tracking-normal text-secondary-text">Provider counts</p>
            <div className="flex flex-wrap gap-2">
              {sourceCounts.length > 0 ? sourceCounts.map(([source, count]) => (
                <Badge key={source} variant={source === 'massive' ? 'success' : source === 'missing' ? 'warning' : 'info'}>
                  {source}: {count}
                </Badge>
              )) : <span className="text-sm text-secondary-text">No provider counts yet.</span>}
            </div>
          </div>
          {warnings.length > 0 ? (
            <div>
              <p className="mb-2 text-xs uppercase tracking-normal text-secondary-text">Warnings</p>
              <div className="space-y-1.5">
                {warnings.slice(0, 4).map((warning) => (
                  <div key={warning} className="flex gap-2 text-sm text-secondary-text">
                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                    <span>{warning}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="mt-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs uppercase tracking-normal text-secondary-text">Sample symbols counted in breadth</p>
          <span className="text-xs text-muted-text">Showing first {constituents.length || 0} passed symbols</span>
        </div>
        {constituents.length > 0 ? (
          <>
            <div className="space-y-2 md:hidden">
              {constituents.map((item) => (
                <div key={asString(item.symbol) || JSON.stringify(item)} className="rounded-lg border border-border/50 bg-card/60 px-3 py-2">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <p className="text-sm font-semibold text-foreground">{asString(item.symbol) || '-'}</p>
                    <Badge variant={item.above_sma200 ? 'success' : 'warning'}>{item.above_sma200 ? 'above 200DMA' : 'below 200DMA'}</Badge>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-xs text-secondary-text">
                    <span>Close {formatNumber(item.close)}</span>
                    <span>50DMA {formatNumber(item.sma50)}</span>
                    <span>200DMA {formatNumber(item.sma200)}</span>
                    <span>ADV {formatCurrency(item.avg_dollar_volume_20d)}</span>
                  </div>
                </div>
              ))}
            </div>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="text-xs uppercase text-secondary-text">
                  <tr>
                    <th className="pb-2">Symbol</th>
                    <th className="pb-2">Close</th>
                    <th className="pb-2">50DMA</th>
                    <th className="pb-2">200DMA</th>
                    <th className="pb-2">20D $Vol</th>
                    <th className="pb-2">Trend</th>
                    <th className="pb-2">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {constituents.map((item) => (
                    <tr key={asString(item.symbol) || JSON.stringify(item)} className="border-t border-border/50">
                      <td className="py-2 font-medium text-foreground">{asString(item.symbol) || '-'}</td>
                      <td className="py-2 text-secondary-text">{formatNumber(item.close)}</td>
                      <td className="py-2 text-secondary-text">{formatNumber(item.sma50)}</td>
                      <td className="py-2 text-secondary-text">{formatNumber(item.sma200)}</td>
                      <td className="py-2 text-secondary-text">{formatCurrency(item.avg_dollar_volume_20d)}</td>
                      <td className="py-2">
                        <Badge variant={item.above_sma50 && item.above_sma200 ? 'success' : 'warning'}>
                          {item.above_sma50 && item.above_sma200 ? 'above 50/200' : 'weaker'}
                        </Badge>
                      </td>
                      <td className="py-2 text-secondary-text">{asString(item.source) || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="rounded-lg border border-border/50 bg-card/60 px-3 py-2 text-sm text-secondary-text">
            No counted symbols yet. Run the cache after market close.
          </p>
        )}
      </div>

      {failures.length > 0 ? (
        <div className="mt-3 rounded-lg border border-border/50 bg-card/60 px-3 py-2">
          <p className="mb-2 text-xs uppercase tracking-normal text-secondary-text">Recent missing symbols</p>
          <div className="space-y-1 text-xs text-secondary-text">
            {failures.map((item) => (
              <p key={`${asString(item.symbol) || 'symbol'}-${asString(item.reason) || 'reason'}`}>
                <span className="font-medium text-foreground">{asString(item.symbol) || '-'}</span>: {asString(item.reason) || 'missing data'}
              </p>
            ))}
          </div>
        </div>
      ) : null}
      </>
      ) : null}
    </div>
  );
};

const SummaryCard: React.FC<{
  icon: React.ReactNode;
  label: string;
  value: string;
  badge?: string;
  badgeVariant?: 'success' | 'warning' | 'danger' | 'default' | 'info';
}> = ({ icon, label, value, badge, badgeVariant = 'info' }) => (
  <Card padding="sm" className="rounded-lg">
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="text-xs uppercase tracking-normal text-secondary-text">{label}</p>
        <p className="mt-2 break-words text-lg font-semibold text-foreground">{value}</p>
      </div>
      <div className="rounded-lg border border-cyan/20 bg-cyan/10 p-2 text-cyan">{icon}</div>
    </div>
    {badge ? <Badge variant={badgeVariant} className="mt-3">{badge}</Badge> : null}
  </Card>
);

const PositioningReadPanel: React.FC<{
  analysis: PositioningResponse;
  gamma: Record<string, unknown>;
  callWall: Record<string, unknown>;
  putWall: Record<string, unknown>;
  explanations: DecisionExplanation[];
  showWork: boolean;
  onToggleWork: () => void;
  showDetails: boolean;
  onToggleDetails: () => void;
}> = ({ analysis, gamma, callWall, putWall, explanations, showWork, onToggleWork, showDetails, onToggleDetails }) => {
  const bias = explanations[0];
  const gammaRead = explanations[1];
  const crowdingRead = explanations[2];
  const confidenceRead = explanations[3];
  const topReasons = (bias?.why || []).slice(0, 4);
  const topWatch = (analysis.what_to_watch || []).slice(0, 3);

  return (
    <Card padding="md" className="rounded-lg">
      <div className="grid gap-5 xl:grid-cols-[1.2fr_0.8fr]">
        <div>
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Badge variant={bias?.tone === 'danger' ? 'danger' : bias?.tone === 'warning' ? 'warning' : bias?.tone === 'success' ? 'success' : 'info'}>
              {analysis.symbol}
            </Badge>
            <Badge variant={riskVariant((analysis.crowding ?? {}).risk_level)}>
              {asString((analysis.crowding ?? {}).risk_level) || 'unknown'} crowding
            </Badge>
            <span className="text-xs text-secondary-text">{analysis.as_of}</span>
          </div>
          <p className="label-uppercase">Main Read</p>
          <h2 className="mt-1 text-xl font-semibold leading-tight text-foreground">{bias?.decision || analysis.positioning_bias}</h2>
          <p className="mt-3 max-w-3xl text-sm leading-6 text-foreground">
            {bias?.plainEnglish || 'The app does not have enough positioning evidence to make a clean read.'}
          </p>
          {topReasons.length > 0 ? (
            <div className="mt-4 grid gap-2 md:grid-cols-2">
              {topReasons.map((item) => (
                <div key={item} className="flex gap-2 rounded-lg border border-border/60 bg-elevated/45 px-3 py-2 text-sm text-secondary-text">
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
                  <span>{item}</span>
                </div>
              ))}
            </div>
          ) : null}
        </div>

        <div className="space-y-3">
          <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
            <p className="label-uppercase">Key Levels</p>
            <div className="mt-3 grid grid-cols-2 gap-2">
              <Info label="Spot" value={formatNumber(analysis.underlying_price)} />
              <Info label="Gamma Flip" value={formatNumber(gamma.gamma_flip_level)} />
              <Info label="Call Wall" value={formatNumber(callWall.strike)} />
              <Info label="Put Wall" value={formatNumber(putWall.strike)} />
            </div>
          </div>
          <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
            <p className="label-uppercase">Quick Checks</p>
            <div className="mt-3 space-y-2 text-sm text-secondary-text">
              <p><span className="font-medium text-foreground">Gamma:</span> {gammaRead?.decision || readableRegime(gamma.gamma_regime)}</p>
              <p><span className="font-medium text-foreground">Crowding:</span> {crowdingRead?.decision || 'unknown'}</p>
              <p><span className="font-medium text-foreground">Data:</span> {confidenceRead?.decision || 'unknown confidence'}</p>
            </div>
          </div>
        </div>
      </div>

      {topWatch.length > 0 ? (
        <div className="mt-4 rounded-lg border border-border/60 bg-card/70 p-3">
          <p className="label-uppercase">Watch Next</p>
          <div className="mt-2 grid gap-2 lg:grid-cols-3">
            {topWatch.map((item, index) => (
              <div key={`${item}-${index}`} className="flex gap-2 text-sm leading-6 text-secondary-text">
                <Crosshair className="mt-1 h-4 w-4 shrink-0 text-cyan" />
                <span>{item}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant="secondary" size="xsm" onClick={onToggleWork}>
          {showWork ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {showWork ? 'Hide full reasoning' : 'Show full reasoning'}
        </Button>
        <Button variant="ghost" size="xsm" onClick={onToggleDetails}>
          {showDetails ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          {showDetails ? 'Hide data panels' : 'Show data panels'}
        </Button>
      </div>
    </Card>
  );
};

const CollapsibleCard: React.FC<{
  title: string;
  subtitle: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}> = ({ title, subtitle, open, onToggle, children }) => (
  <Card padding="md" className="rounded-lg">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div>
        <p className="label-uppercase">{subtitle}</p>
        <h3 className="mt-1 text-lg font-semibold leading-tight text-foreground">{title}</h3>
      </div>
      <Button variant="secondary" size="xsm" onClick={onToggle}>
        {open ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        {open ? 'Hide' : 'Show'}
      </Button>
    </div>
    {open ? <div className="mt-4">{children}</div> : null}
  </Card>
);

const toneStyles: Record<ExplanationTone, string> = {
  info: 'border-cyan/25 bg-cyan/10 text-cyan',
  success: 'border-success/25 bg-success/10 text-success',
  warning: 'border-warning/25 bg-warning/10 text-warning',
  danger: 'border-danger/25 bg-danger/10 text-danger',
};

const ReasonCard: React.FC<{ explanation: DecisionExplanation }> = ({ explanation }) => (
  <Card padding="md" className="rounded-lg">
    <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
      <div className="flex items-center gap-2">
        <Brain className="h-5 w-5 text-cyan" />
        <div>
          <p className="text-xs uppercase tracking-normal text-secondary-text">{explanation.title}</p>
          <h3 className="mt-1 text-base font-semibold text-foreground">{explanation.decision}</h3>
        </div>
      </div>
      <Badge variant={explanation.tone === 'danger' ? 'danger' : explanation.tone === 'warning' ? 'warning' : explanation.tone === 'success' ? 'success' : 'info'}>
        reasoned
      </Badge>
    </div>
    <p className="text-sm leading-6 text-foreground">{explanation.plainEnglish}</p>
    <div className="mt-3 space-y-2">
      {explanation.why.map((item) => (
        <div key={item} className="flex gap-2 text-sm text-secondary-text">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
          <span>{item}</span>
        </div>
      ))}
    </div>
    <div className={`mt-4 rounded-lg border px-3 py-2 text-sm leading-6 ${toneStyles[explanation.tone]}`}>
      {explanation.howToUse}
    </div>
  </Card>
);

const LookoutItem: React.FC<{ label: string; detail: string }> = ({ label, detail }) => (
  <div className="flex gap-2 rounded-lg border border-border/60 bg-elevated/45 px-3 py-3">
    <Crosshair className="mt-0.5 h-4 w-4 shrink-0 text-cyan" />
    <div>
      <p className="text-sm font-medium text-foreground">{label}</p>
      <p className="mt-1 text-xs leading-relaxed text-secondary-text">{detail}</p>
    </div>
  </div>
);

const DefinitionItem: React.FC<{ term: string; definition: string }> = ({ term, definition }) => (
  <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
    <p className="text-sm font-semibold text-foreground">{term}</p>
    <p className="mt-1 text-xs leading-5 text-secondary-text">{definition}</p>
  </div>
);

const Info: React.FC<{ label: string; value: string; detail?: string }> = ({ label, value, detail }) => (
  <div className="rounded-lg border border-border/60 bg-elevated/45 p-3">
    <p className="text-[11px] uppercase tracking-normal text-secondary-text">{label}</p>
    <p className="mt-1 break-words text-sm font-medium text-foreground">{value}</p>
    {detail ? <p className="mt-1 text-xs text-muted-foreground">{detail}</p> : null}
  </div>
);

export default PositioningPage;
