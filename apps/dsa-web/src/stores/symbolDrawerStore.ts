import { create } from 'zustand';

export type SymbolDrawerSource = 'signal' | 'discovery' | 'down_day' | 'rotation' | 'generic';

export interface SymbolDrawerMetric {
  label: string;
  value: string;
  /** Optional glossary key — wraps the label in an InfoTip. */
  term?: string;
}

export interface SymbolDrawerContext {
  source: SymbolDrawerSource;
  metrics?: SymbolDrawerMetric[];
  note?: string;
}

export interface SymbolHistoryPoint {
  date: string;
  close: number;
}

interface SymbolDrawerState {
  open: boolean;
  symbol: string | null;
  context: SymbolDrawerContext | null;
  /** Per-session cache of fetched close series, keyed by symbol. */
  historyCache: Record<string, SymbolHistoryPoint[]>;
  openDrawer: (symbol: string, context?: SymbolDrawerContext) => void;
  closeDrawer: () => void;
  cacheHistory: (symbol: string, points: SymbolHistoryPoint[]) => void;
}

export const useSymbolDrawerStore = create<SymbolDrawerState>((set) => ({
  open: false,
  symbol: null,
  context: null,
  historyCache: {},
  openDrawer: (symbol, context) =>
    set({ open: true, symbol, context: context ?? { source: 'generic' } }),
  closeDrawer: () => set({ open: false }),
  cacheHistory: (symbol, points) =>
    set((state) => ({ historyCache: { ...state.historyCache, [symbol]: points } })),
}));
