import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useLiveStockQuote } from '../../../hooks/useLiveStockQuote';
import { ReportOverview } from '../ReportOverview';

vi.mock('../../../hooks/useLiveStockQuote', async () => {
  const actual = await vi.importActual<typeof import('../../../hooks/useLiveStockQuote')>(
    '../../../hooks/useLiveStockQuote',
  );
  return {
    ...actual,
    useLiveStockQuote: vi.fn(),
  };
});

const baseMeta = {
  queryId: 'q-1',
  stockCode: '600519',
  stockName: '贵州茅台',
  reportType: 'detailed' as const,
  reportLanguage: 'zh' as const,
  createdAt: '2026-03-21T08:00:00Z',
};

const baseSummary = {
  analysisSummary: '趋势维持强势',
  operationAdvice: '继续观察买点',
  trendPrediction: '短线震荡偏强',
  sentimentScore: 78,
};

const liveQuoteState = {
  quote: {
    stockCode: '600519',
    stockName: 'Kweichow Moutai',
    currentPrice: 188.42,
    changePercent: 2.35,
    source: 'fallback',
  },
  isLoading: false,
  error: null,
  lastCheckedAt: new Date('2026-03-21T09:00:00Z'),
  refresh: vi.fn(),
};

describe('ReportOverview', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(useLiveStockQuote).mockReturnValue(liveQuoteState);
  });

  it('shows the latest quote and ignores saved snapshot pricing', () => {
    render(
      <ReportOverview
        meta={{
          ...baseMeta,
          currentPrice: 100,
          changePct: -8.4,
        }}
        summary={baseSummary}
      />,
    );

    expect(screen.getByText('Latest quote')).toBeInTheDocument();
    expect(screen.getByText('188.42')).toBeInTheDocument();
    expect(screen.getByText('+2.35%')).toBeInTheDocument();
    expect(screen.queryByText('100.00')).not.toBeInTheDocument();
    expect(screen.queryByText('-8.40%')).not.toBeInTheDocument();
  });

  it('shows unavailable instead of a saved snapshot when live quote is missing', () => {
    vi.mocked(useLiveStockQuote).mockReturnValue({
      quote: null,
      isLoading: false,
      error: 'Latest quote unavailable',
      lastCheckedAt: new Date('2026-03-21T09:00:00Z'),
      refresh: vi.fn(),
    });

    render(
      <ReportOverview
        meta={{
          ...baseMeta,
          currentPrice: 100,
          changePct: -8.4,
        }}
        summary={baseSummary}
      />,
    );

    expect(screen.getByText('Latest price unavailable')).toBeInTheDocument();
    expect(screen.queryByText('100.00')).not.toBeInTheDocument();
    expect(screen.queryByText('-8.40%')).not.toBeInTheDocument();
  });

  it('renders related boards with leading and lagging markers', () => {
    render(
      <ReportOverview
        meta={baseMeta}
        summary={baseSummary}
        details={{
          belongBoards: [
            { name: ' 白酒 ', type: '行业' },
            { name: '消费', type: '概念' },
            { name: '新能源' },
          ],
          sectorRankings: {
            top: [{ name: '白酒', changePct: 2.31 }],
            bottom: [{ name: '消费', changePct: -1.2 }],
          },
        }}
      />,
    );

    expect(screen.getByText('Related Boards')).toBeInTheDocument();
    expect(screen.getByText('白酒')).toBeInTheDocument();
    expect(screen.getByText('行业')).toBeInTheDocument();
    expect(screen.getByText('Leading')).toBeInTheDocument();
    expect(screen.getByText('+2.31%')).toBeInTheDocument();
    expect(screen.getByText('Lagging')).toBeInTheDocument();
    expect(screen.getByText('-1.20%')).toBeInTheDocument();
    expect(screen.queryByText('Neutral')).not.toBeInTheDocument();
  });

  it('shows board list when rankings are unavailable', () => {
    render(
      <ReportOverview
        meta={baseMeta}
        summary={baseSummary}
        details={{
          belongBoards: [{ name: '半导体', type: '行业' }],
        }}
      />,
    );

    expect(screen.getByText('Related Boards')).toBeInTheDocument();
    expect(screen.getByText('半导体')).toBeInTheDocument();
    expect(screen.queryByText('Neutral')).not.toBeInTheDocument();
    expect(screen.queryByText('Leading')).not.toBeInTheDocument();
    expect(screen.queryByText('Lagging')).not.toBeInTheDocument();
  });

  it('hides related boards section when no boards are available', () => {
    render(<ReportOverview meta={baseMeta} summary={baseSummary} details={{ belongBoards: [] }} />);

    expect(screen.queryByText('Related Boards')).not.toBeInTheDocument();
  });

  it('fails open on malformed ranking payloads', () => {
    render(
      <ReportOverview
        meta={baseMeta}
        summary={baseSummary}
        details={{
          belongBoards: [{ name: ' 白酒 ' }],
          sectorRankings: {
            top: {} as unknown as never[],
            bottom: [{ name: '白酒', changePct: '-2.5%' as unknown as number }],
          },
        }}
      />,
    );

    expect(screen.getByText('Related Boards')).toBeInTheDocument();
    expect(screen.getByText('白酒')).toBeInTheDocument();
    expect(screen.getByText('Lagging')).toBeInTheDocument();
    expect(screen.getByText('-2.50%')).toBeInTheDocument();
  });
});
