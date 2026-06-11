import type React from 'react';
import { Sparkline } from '../charts/Sparkline';
import {
  useSymbolDrawerStore,
  type SymbolDrawerContext,
  type SymbolHistoryPoint,
} from '../../stores/symbolDrawerStore';
import { useSymbolNamesStore } from '../../stores/symbolNamesStore';
import { cn } from '../../utils/cn';

interface TickerChipProps {
  symbol: string;
  /** Carried into the symbol drawer ("Why it's here" section). */
  context?: SymbolDrawerContext;
  /** Optional 30d closes rendered as a tiny inline sparkline. */
  sparkline?: SymbolHistoryPoint[];
  /** Optional signed % return, colored green/red. */
  change?: number;
  /** Render the resolved company/ETF name (muted, truncated) next to the symbol. */
  showName?: boolean;
  className?: string;
}

/**
 * Clickable ticker pill: mono symbol, optional sparkline and signed return.
 * Click (or Enter/Space, it's a button) opens the symbol drawer. When the
 * symbol-names store knows the name it is always exposed as a hover title.
 */
export const TickerChip: React.FC<TickerChipProps> = ({
  symbol,
  context,
  sparkline,
  change,
  showName = false,
  className,
}) => {
  const openDrawer = useSymbolDrawerStore((state) => state.openDrawer);
  const name = useSymbolNamesStore((state) => state.names[symbol]);

  return (
    <button
      type="button"
      onClick={() => openDrawer(symbol, context)}
      aria-label={`Open ${symbol} details`}
      title={name || undefined}
      className={cn(
        'inline-flex min-h-9 items-center gap-1.5 rounded-full border border-border/55 bg-elevated/75 px-2 py-0.5',
        'transition-colors hover:border-[var(--border-hover)] hover:bg-hover',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40',
        className,
      )}
    >
      <span className="font-mono text-xs font-semibold text-foreground">{symbol}</span>
      {showName && name ? (
        <span className="max-w-[100px] truncate text-[11px] text-muted-text sm:max-w-[140px]">{name}</span>
      ) : null}
      {sparkline && sparkline.length >= 2 ? <Sparkline data={sparkline} /> : null}
      {change != null && Number.isFinite(change) ? (
        <span
          className={cn('text-[11px] font-medium', change >= 0 ? 'text-success' : 'text-danger')}
        >
          {change >= 0 ? '+' : ''}
          {change.toFixed(2)}%
        </span>
      ) : null}
    </button>
  );
};
