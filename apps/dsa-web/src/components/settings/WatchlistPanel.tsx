import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { researchApi, type WatchlistSymbol } from '../../api/research';
import { Button } from '../common/Button';

interface WatchlistPanelProps {
  disabled?: boolean;
}

export const WatchlistPanel: React.FC<WatchlistPanelProps> = ({ disabled = false }) => {
  const [symbols, setSymbols] = useState<WatchlistSymbol[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [symbolInput, setSymbolInput] = useState('');
  const [noteInput, setNoteInput] = useState('');
  const [isAdding, setIsAdding] = useState(false);
  const [removingSymbol, setRemovingSymbol] = useState<string | null>(null);
  const symbolRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const items = await researchApi.getWatchlist();
      setSymbols(items);
    } catch {
      setError('Failed to load watchlist.');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    const sym = symbolInput.trim().toUpperCase();
    if (!sym) return;
    setIsAdding(true);
    setError(null);
    try {
      const added = await researchApi.addWatchlistSymbol(sym, noteInput.trim() || undefined);
      setSymbols((prev) => {
        if (prev.some((s) => s.symbol === added.symbol)) return prev;
        return [...prev, added];
      });
      setSymbolInput('');
      setNoteInput('');
      symbolRef.current?.focus();
    } catch {
      setError(`Failed to add ${sym}.`);
    } finally {
      setIsAdding(false);
    }
  };

  const handleRemove = async (symbol: string) => {
    setRemovingSymbol(symbol);
    setError(null);
    try {
      await researchApi.removeWatchlistSymbol(symbol);
      setSymbols((prev) => prev.filter((s) => s.symbol !== symbol));
    } catch {
      setError(`Failed to remove ${symbol}.`);
    } finally {
      setRemovingSymbol(null);
    }
  };

  const isDisabled = disabled || isLoading || isAdding;

  return (
    <div className="space-y-4">
      {/* Add form */}
      <form onSubmit={(e) => { void handleAdd(e); }} className="flex flex-wrap items-end gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted-text uppercase tracking-wider">Symbol</label>
          <input
            ref={symbolRef}
            type="text"
            value={symbolInput}
            onChange={(e) => setSymbolInput(e.target.value.toUpperCase())}
            placeholder="AAPL"
            maxLength={16}
            disabled={isDisabled}
            className="input-surface input-focus-glow h-9 rounded-xl border bg-transparent px-3 text-sm w-28 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-xs font-medium text-muted-text uppercase tracking-wider">Note (optional)</label>
          <input
            type="text"
            value={noteInput}
            onChange={(e) => setNoteInput(e.target.value)}
            placeholder="e.g. AI sector leader"
            maxLength={256}
            disabled={isDisabled}
            className="input-surface input-focus-glow h-9 rounded-xl border bg-transparent px-3 text-sm w-52 focus:outline-none disabled:cursor-not-allowed disabled:opacity-60"
          />
        </div>
        <Button
          type="submit"
          variant="settings-primary"
          size="sm"
          disabled={isDisabled || !symbolInput.trim()}
          isLoading={isAdding}
          loadingText="Adding..."
        >
          Add
        </Button>
      </form>

      {/* Error message */}
      {error ? (
        <p className="text-xs text-danger">{error}</p>
      ) : null}

      {/* Symbol list */}
      {isLoading ? (
        <p className="text-xs text-muted-text">Loading...</p>
      ) : symbols.length === 0 ? (
        <p className="text-xs text-muted-text">No symbols in watchlist. Add one above.</p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {symbols.map((item) => (
            <div
              key={item.symbol}
              title={[item.note, `source: ${item.source}`].filter(Boolean).join(' — ')}
              className="flex items-center gap-1.5 rounded-xl border settings-border bg-background/40 px-3 py-1.5"
            >
              <span className="font-mono text-sm text-foreground">{item.symbol}</span>
              {item.note ? (
                <span className="text-xs text-muted-text max-w-[120px] truncate">{item.note}</span>
              ) : null}
              <button
                type="button"
                onClick={() => { void handleRemove(item.symbol); }}
                disabled={disabled || removingSymbol === item.symbol}
                className="-my-2 -mr-2 ml-0.5 inline-flex h-9 w-9 items-center justify-center text-muted-text hover:text-danger disabled:opacity-40 transition-colors"
                aria-label={`Remove ${item.symbol}`}
              >
                {removingSymbol === item.symbol ? (
                  <span className="text-xs">…</span>
                ) : (
                  <span className="text-xs leading-none">✕</span>
                )}
              </button>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs leading-6 text-muted-text">
        Symbols here are used by signal scans, market breadth, and analysis jobs.
        The <code className="text-xs">RESEARCH_US_WATCHLIST</code> env var seeds this list on first run.
      </p>
    </div>
  );
};
