import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { stocksApi, type StockQuote } from '../api/stocks';

type UseLiveStockQuoteOptions = {
  enabled?: boolean;
  intervalMs?: number;
};

export type UseLiveStockQuoteResult = {
  quote: StockQuote | null;
  isLoading: boolean;
  error: string | null;
  lastCheckedAt: Date | null;
  refresh: () => Promise<void>;
};

const DEFAULT_INTERVAL_MS = 30_000;

export const isUsableLiveQuote = (quote: StockQuote | null | undefined): quote is StockQuote => (
  typeof quote?.currentPrice === 'number'
  && Number.isFinite(quote.currentPrice)
  && quote.currentPrice > 0
  && quote.source !== 'placeholder'
);

const errorMessage = (error: unknown): string => {
  if (error instanceof Error && error.message.trim()) {
    return error.message;
  }
  return 'Latest quote unavailable';
};

export function useLiveStockQuote(
  stockCode?: string,
  options: UseLiveStockQuoteOptions = {},
): UseLiveStockQuoteResult {
  const { enabled = true, intervalMs = DEFAULT_INTERVAL_MS } = options;
  const normalizedCode = useMemo(() => stockCode?.trim().toUpperCase() || '', [stockCode]);
  const [quote, setQuote] = useState<StockQuote | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null);
  const requestIdRef = useRef(0);
  const quoteRef = useRef<StockQuote | null>(null);

  useEffect(() => {
    quoteRef.current = quote;
  }, [quote]);

  const refresh = useCallback(async () => {
    if (!enabled || !normalizedCode) {
      return;
    }

    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setIsLoading(quoteRef.current === null);
    setError(null);

    try {
      const nextQuote = await stocksApi.getQuote(normalizedCode);
      if (requestIdRef.current !== requestId) {
        return;
      }
      if (!isUsableLiveQuote(nextQuote)) {
        throw new Error('Latest quote unavailable');
      }
      setQuote(nextQuote);
      setLastCheckedAt(new Date());
    } catch (quoteError) {
      if (requestIdRef.current !== requestId) {
        return;
      }
      setError(errorMessage(quoteError));
      setLastCheckedAt(new Date());
    } finally {
      if (requestIdRef.current === requestId) {
        setIsLoading(false);
      }
    }
  }, [enabled, normalizedCode]);

  useEffect(() => {
    requestIdRef.current += 1;
    quoteRef.current = null;
    setQuote(null);
    setError(null);
    setLastCheckedAt(null);

    if (!enabled || !normalizedCode) {
      setIsLoading(false);
      return;
    }

    void refresh();
    const intervalId = window.setInterval(() => {
      void refresh();
    }, intervalMs);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refresh();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, [enabled, intervalMs, normalizedCode, refresh]);

  return {
    quote,
    isLoading,
    error,
    lastCheckedAt,
    refresh,
  };
}
