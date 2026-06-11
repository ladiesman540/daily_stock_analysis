import { create } from 'zustand';
import { researchApi } from '../api/research';

interface SymbolNamesState {
  /** Resolved symbol → display name (company / ETF label). */
  names: Record<string, string>;
  /**
   * Fetch names for any not-yet-requested symbols in one batched call and
   * merge the result. Fail-open: a failed batch is forgotten so a later call
   * can retry; unknown symbols stay marked so they are never re-requested.
   */
  loadNames: (symbols: string[]) => Promise<void>;
}

/** Symbols already resolved, in flight, or known-unresolvable this session. */
const requested = new Set<string>();

export const useSymbolNamesStore = create<SymbolNamesState>((set) => ({
  names: {},
  loadNames: async (symbols) => {
    const pending = Array.from(
      new Set(symbols.map((symbol) => symbol.trim().toUpperCase()).filter(Boolean)),
    ).filter((symbol) => !requested.has(symbol));
    if (pending.length === 0) return;
    pending.forEach((symbol) => requested.add(symbol));
    try {
      const response = await researchApi.getSymbolNames(pending);
      const incoming = response.names || {};
      if (Object.keys(incoming).length > 0) {
        set((state) => ({ names: { ...state.names, ...incoming } }));
      }
    } catch {
      // Chips simply render without names; allow a retry on the next call.
      pending.forEach((symbol) => requested.delete(symbol));
    }
  },
}));
