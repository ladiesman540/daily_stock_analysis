import type React from 'react';
import { Badge } from '../common';
import type { HistoryItem } from '../../types/analysis';
import { getSentimentColor } from '../../types/analysis';
import { formatDateTime } from '../../utils/format';
import { truncateStockName, isStockNameTruncated } from '../../utils/stockName';

interface HistoryListItemProps {
  item: HistoryItem;
  isViewing: boolean; // Indicates if this report is currently being viewed in the right panel
  isChecked: boolean; // Indicates if the checkbox is checked for bulk operations
  isDeleting: boolean;
  onToggleChecked: (recordId: number) => void;
  onClick: (recordId: number) => void;
}

const getOperationBadgeLabel = (advice?: string) => {
  const normalized = advice?.trim();
  if (!normalized) {
    return 'Sentiment';
  }
  const lower = normalized.toLowerCase();
  if (lower.includes('trim') || lower.includes('reduce')) {
    return 'Trim';
  }
  if (lower.includes('sell')) {
    return 'Sell';
  }
  if (lower.includes('watch') || lower.includes('wait') || lower.includes('hold')) {
    return 'Watch';
  }
  if (lower.includes('buy') || lower.includes('accumulate')) {
    return 'Buy';
  }
  if (normalized.includes('减仓')) {
    return 'Trim';
  }
  if (normalized.includes('卖')) {
    return 'Sell';
  }
  if (normalized.includes('观望') || normalized.includes('等待')) {
    return 'Watch';
  }
  if (normalized.includes('买') || normalized.includes('布局')) {
    return 'Buy';
  }
  if (/[\u3400-\u9fff]/.test(normalized)) {
    return 'Advice';
  }
  return normalized.split(/[，。；、\s]/)[0] || 'Advice';
};

export const HistoryListItem: React.FC<HistoryListItemProps> = ({
  item,
  isViewing,
  isChecked,
  isDeleting,
  onToggleChecked,
  onClick,
}) => {
  const sentimentColor = item.sentimentScore !== undefined ? getSentimentColor(item.sentimentScore) : null;
  const stockName = item.stockName || item.stockCode;
  const isTruncated = isStockNameTruncated(stockName);

  return (
    <div className="group flex items-start gap-2">
      <div className="pt-4">
        <input
          type="checkbox"
          checked={isChecked}
          onChange={() => onToggleChecked(item.id)}
          disabled={isDeleting}
          className="h-3.5 w-3.5 cursor-pointer rounded border-border bg-card accent-primary focus:ring-primary/30 disabled:opacity-50"
        />
      </div>
      <button
        type="button"
        onClick={() => onClick(item.id)}
        className={`home-history-item flex-1 text-left p-3 group/item ${
          isViewing ? 'home-history-item-selected' : ''
        }`}
      >
        <div className="relative z-10 flex items-start gap-2.5">
          {sentimentColor && (
            <div
              className="mt-0.5 h-10 w-1 rounded-full flex-shrink-0"
              style={{
                backgroundColor: sentimentColor,
                boxShadow: `0 0 0 3px ${sentimentColor}12`,
              }}
            />
          )}
          <div className="flex-1 min-w-0">
            <div className="flex min-w-0 items-start justify-between gap-2">
              <span
                title={isTruncated ? stockName : undefined}
                className="block min-w-0 truncate text-sm font-semibold tracking-tight text-foreground"
              >
                {truncateStockName(stockName)}
              </span>
              {sentimentColor && (
                <Badge
                  variant="default"
                  size="sm"
                  className="home-history-sentiment-badge shrink-0 whitespace-nowrap shadow-none text-[10px] font-semibold leading-none"
                  style={{
                    color: sentimentColor,
                    borderColor: `${sentimentColor}28`,
                    backgroundColor: `${sentimentColor}0f`,
                  }}
                >
                  {item.sentimentScore}
                </Badge>
              )}
            </div>
            <div className="mt-1.5 flex min-w-0 flex-wrap items-center gap-1.5">
              <span className="rounded-md border border-border/70 bg-card px-1.5 py-0.5 font-mono text-[11px] text-secondary-text">
                {item.stockCode}
              </span>
              <span className="min-w-0 text-[11px] text-muted-text">
                {formatDateTime(item.createdAt)}
              </span>
            </div>
            {sentimentColor ? (
              <div className="mt-2 text-[11px] font-medium text-secondary-text">
                {getOperationBadgeLabel(item.operationAdvice)}
              </div>
            ) : null}
            {isTruncated ? (
              <div className="mt-1 hidden rounded-md bg-card px-2 py-1 text-[11px] text-secondary-text shadow-soft-card group-hover/item:block">
                {stockName}
              </div>
            ) : null}
          </div>
        </div>
      </button>
    </div>
  );
};
