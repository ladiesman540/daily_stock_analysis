import type React from 'react';
import { useCallback, useEffect, useId, useState } from 'react';
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { stocksApi } from '../../api/stocks';
import { researchApi } from '../../api/research';
import { useSymbolDrawerStore } from '../../stores/symbolDrawerStore';
import { useSymbolNamesStore } from '../../stores/symbolNamesStore';
import { InfoTip } from '../common/InfoTip';
import { Button } from '../common/Button';
import {
  CHART_COLORS,
  CHART_TOOLTIP_LABEL_STYLE,
  CHART_TOOLTIP_STYLE,
} from '../charts/chartTheme';
import { cn } from '../../utils/cn';

type AddState = 'idle' | 'adding' | 'added' | 'error';

/** Stock names returned alongside history, kept for cached reopens. */
const symbolNames: Record<string, string> = {};

/**
 * Global symbol detail drawer (mounted once in the Shell). Desktop: right
 * slide-in panel; mobile: bottom sheet. Lazily fetches a 3-month close series
 * on open and caches it per symbol in the store. Fail-open: a fetch error
 * only blanks the chart area, never the drawer.
 */
export const SymbolDrawer: React.FC = () => {
  const { open, symbol, context, historyCache, closeDrawer, cacheHistory } = useSymbolDrawerStore();
  const titleId = useId();
  // Transient UI state keyed per symbol so switching symbols never needs a reset effect.
  const [addStates, setAddStates] = useState<Record<string, AddState>>({});
  const [historyFailed, setHistoryFailed] = useState<Record<string, boolean>>({});

  const series = symbol ? historyCache[symbol] : undefined;
  const storeName = useSymbolNamesStore((state) => (symbol ? state.names[symbol] : undefined));
  const addState: AddState = symbol ? (addStates[symbol] ?? 'idle') : 'idle';
  // Derived, fail-open status: cached series wins; otherwise loading until the fetch errors.
  const historyError = symbol ? historyFailed[symbol] === true && !series : false;

  // Resolve the display name on open (deduped/fail-open inside the store).
  useEffect(() => {
    if (open && symbol) void useSymbolNamesStore.getState().loadNames([symbol]);
  }, [open, symbol]);

  useEffect(() => {
    if (!open || !symbol) return;
    if (useSymbolDrawerStore.getState().historyCache[symbol]) return;

    let cancelled = false;
    stocksApi
      .getHistory(symbol, { days: 90 })
      .then((response) => {
        if (cancelled) return;
        const points = (response.data || [])
          .filter((bar) => bar && bar.date && Number.isFinite(bar.close))
          .map((bar) => ({ date: bar.date, close: bar.close }));
        if (response.stockName) {
          symbolNames[symbol] = response.stockName;
        }
        if (points.length > 0) {
          // API can return more bars than requested; clamp to ~3 months (63 trading days).
          cacheHistory(symbol, points.slice(-63));
        } else {
          setHistoryFailed((prev) => ({ ...prev, [symbol]: true }));
        }
      })
      .catch(() => {
        if (!cancelled) setHistoryFailed((prev) => ({ ...prev, [symbol]: true }));
      });

    return () => {
      cancelled = true;
    };
  }, [open, symbol, cacheHistory]);

  // Escape closes; body scroll locks while open.
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeDrawer();
    };
    document.addEventListener('keydown', handleKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [open, closeDrawer]);

  const addToWatchlist = useCallback(async () => {
    if (!symbol) return;
    setAddStates((prev) => ({ ...prev, [symbol]: 'adding' }));
    try {
      await researchApi.addWatchlistSymbol(symbol, 'Added from symbol drawer');
      setAddStates((prev) => ({ ...prev, [symbol]: 'added' }));
    } catch {
      setAddStates((prev) => ({ ...prev, [symbol]: 'error' }));
    }
  }, [symbol]);

  if (!open || !symbol) return null;

  const lastClose = series && series.length > 0 ? series[series.length - 1].close : null;
  const lastCloseDate = series && series.length > 0 ? series[series.length - 1].date : null;
  const prevClose = series && series.length > 1 ? series[series.length - 2].close : null;
  const dayChangePct =
    lastClose != null && prevClose != null && prevClose !== 0
      ? ((lastClose - prevClose) / prevClose) * 100
      : null;
  // Prefer the batch-resolved store name; fall back to the name shipped with history.
  const stockName = storeName || symbolNames[symbol];
  const metrics = context?.metrics ?? [];

  return (
    <div className="fixed inset-0 z-[100] overflow-hidden" role="presentation">
      <div
        className="absolute inset-0 bg-background/80 backdrop-blur-sm"
        onClick={closeDrawer}
      />

      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={cn(
          'absolute flex flex-col bg-card',
          'max-md:inset-x-0 max-md:bottom-0 max-md:h-[85vh] max-md:rounded-t-2xl max-md:border-t max-md:border-border/80 max-md:animate-slide-up',
          'md:inset-y-0 md:right-0 md:w-[420px] md:border-l md:border-border/80 md:animate-slide-in-right',
        )}
      >
        <div className="flex items-start justify-between border-b border-border/60 px-5 py-4">
          <div className="min-w-0">
            <span className="label-uppercase">Symbol</span>
            <h2 id={titleId} className="mt-1 font-mono text-lg font-semibold text-foreground">
              {symbol}
            </h2>
            {stockName ? <p className="truncate text-xs text-muted-text">{stockName}</p> : null}
          </div>
          <button
            type="button"
            onClick={closeDrawer}
            aria-label="Close symbol drawer"
            className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border/70 bg-card/80 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4">
          <div className="flex items-baseline gap-2">
            {lastClose != null ? (
              <>
                <span className="text-2xl font-semibold text-foreground">
                  {lastClose.toLocaleString('en-US', {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })}
                </span>
                {dayChangePct != null ? (
                  <span
                    className={cn(
                      'text-sm font-medium',
                      dayChangePct >= 0 ? 'text-success' : 'text-danger',
                    )}
                  >
                    {dayChangePct >= 0 ? '+' : ''}
                    {dayChangePct.toFixed(2)}% 1D
                  </span>
                ) : null}
                {lastCloseDate ? (
                  <span className="text-xs text-muted-text">· as of {lastCloseDate}</span>
                ) : null}
              </>
            ) : (
              <span className="text-sm text-muted-text">
                {historyError ? 'No cached price' : 'Loading price...'}
              </span>
            )}
          </div>

          <div className="h-44 w-full">
            {series && series.length > 1 ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 4 }}>
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fill: CHART_COLORS.muted }}
                    tickLine={false}
                    axisLine={false}
                    minTickGap={56}
                    tickFormatter={(value: string) => value.slice(5)}
                  />
                  <YAxis hide domain={['auto', 'auto']} />
                  <Tooltip
                    contentStyle={CHART_TOOLTIP_STYLE}
                    labelStyle={CHART_TOOLTIP_LABEL_STYLE}
                    formatter={(value) => [Number(value).toFixed(2), 'Close']}
                  />
                  <Line
                    type="monotone"
                    dataKey="close"
                    stroke={CHART_COLORS.primary}
                    strokeWidth={1.5}
                    dot={false}
                    isAnimationActive={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center rounded-xl border border-border/40 bg-elevated/40">
                <span className="text-xs text-muted-text">
                  {historyError ? `No cached history for ${symbol}` : 'Loading 3-month chart...'}
                </span>
              </div>
            )}
          </div>

          {metrics.length > 0 || context?.note ? (
            <div>
              <span className="label-uppercase">Why it's here</span>
              <div className="mt-2 space-y-1.5">
                {metrics.map((metric) => (
                  <div
                    key={`${metric.label}-${metric.value}`}
                    className="flex items-baseline justify-between gap-3 text-sm"
                  >
                    {metric.term ? (
                      <InfoTip term={metric.term}>
                        <span className="text-secondary-text">{metric.label}</span>
                      </InfoTip>
                    ) : (
                      <span className="text-secondary-text">{metric.label}</span>
                    )}
                    <span className="text-right font-medium text-foreground">{metric.value}</span>
                  </div>
                ))}
              </div>
              {context?.note ? (
                <p className="mt-2 text-xs leading-5 text-muted-text">{context.note}</p>
              ) : null}
            </div>
          ) : null}

          <div className="flex items-center justify-between gap-3 border-t border-border/60 pt-4">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void addToWatchlist()}
              disabled={addState === 'adding' || addState === 'added'}
            >
              {addState === 'added'
                ? 'Added'
                : addState === 'adding'
                  ? 'Adding...'
                  : addState === 'error'
                    ? 'Retry add'
                    : 'Add to watchlist'}
            </Button>
            <Link
              to={`/analyze?symbol=${encodeURIComponent(symbol)}`}
              onClick={closeDrawer}
              className="home-accent-link text-sm font-medium"
            >
              Full analysis →
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
};
