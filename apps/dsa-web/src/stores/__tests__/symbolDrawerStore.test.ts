import { beforeEach, describe, expect, it } from 'vitest';
import { useSymbolDrawerStore } from '../symbolDrawerStore';

describe('symbolDrawerStore', () => {
  beforeEach(() => {
    useSymbolDrawerStore.setState({
      open: false,
      symbol: null,
      context: null,
      historyCache: {},
    });
  });

  it('starts closed with no symbol', () => {
    const state = useSymbolDrawerStore.getState();
    expect(state.open).toBe(false);
    expect(state.symbol).toBeNull();
    expect(state.context).toBeNull();
  });

  it('openDrawer sets the symbol and context', () => {
    useSymbolDrawerStore.getState().openDrawer('NVDA', {
      source: 'signal',
      metrics: [{ label: 'Score', value: '82', term: 'checklist_status' }],
      note: 'from test',
    });

    const state = useSymbolDrawerStore.getState();
    expect(state.open).toBe(true);
    expect(state.symbol).toBe('NVDA');
    expect(state.context?.source).toBe('signal');
    expect(state.context?.metrics).toHaveLength(1);
  });

  it('openDrawer defaults to a generic context', () => {
    useSymbolDrawerStore.getState().openDrawer('AAPL');
    expect(useSymbolDrawerStore.getState().context).toEqual({ source: 'generic' });
  });

  it('closeDrawer closes without clearing the history cache', () => {
    const store = useSymbolDrawerStore.getState();
    store.openDrawer('SPY');
    store.cacheHistory('SPY', [{ date: '2026-06-09', close: 600.5 }]);
    useSymbolDrawerStore.getState().closeDrawer();

    const state = useSymbolDrawerStore.getState();
    expect(state.open).toBe(false);
    expect(state.historyCache.SPY).toHaveLength(1);
  });

  it('cacheHistory stores series per symbol', () => {
    const store = useSymbolDrawerStore.getState();
    store.cacheHistory('SPY', [{ date: '2026-06-09', close: 600.5 }]);
    store.cacheHistory('QQQ', [{ date: '2026-06-09', close: 520.1 }]);

    const { historyCache } = useSymbolDrawerStore.getState();
    expect(Object.keys(historyCache).sort()).toEqual(['QQQ', 'SPY']);
    expect(historyCache.QQQ[0].close).toBe(520.1);
  });
});
