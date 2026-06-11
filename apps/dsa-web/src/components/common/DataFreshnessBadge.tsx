import React, { useState } from 'react';
import { Badge } from './Badge';

interface DataFreshnessBadgeProps {
  /** ISO date (YYYY-MM-DD or full ISO timestamp) of the data snapshot. */
  asOf?: string | null;
  /** Age in calendar days beyond which data is flagged stale. Default 2. */
  staleAfterDays?: number;
  className?: string;
}

const dayMs = 24 * 60 * 60 * 1000;

/**
 * Shows how old the underlying data snapshot is so a frozen dashboard can never
 * masquerade as a live one. Green = fresh, amber = aging, red = stale.
 */
export const DataFreshnessBadge: React.FC<DataFreshnessBadgeProps> = ({
  asOf,
  staleAfterDays = 2,
  className,
}) => {
  // Captured once per mount; age precision of "since page load" is plenty here.
  const [now] = useState(() => Date.now());
  if (!asOf) {
    return (
      <Badge variant="danger" className={className}>
        No data snapshot
      </Badge>
    );
  }
  const parsed = new Date(asOf.length <= 10 ? `${asOf}T00:00:00` : asOf);
  if (Number.isNaN(parsed.getTime())) {
    return (
      <Badge variant="warning" className={className}>
        Data as of {asOf}
      </Badge>
    );
  }
  const ageDays = Math.max(0, Math.floor((now - parsed.getTime()) / dayMs));
  const label = asOf.slice(0, 10);
  if (ageDays > staleAfterDays + 2) {
    return (
      <Badge variant="danger" className={className}>
        STALE — data from {label} ({ageDays}d old)
      </Badge>
    );
  }
  if (ageDays > staleAfterDays) {
    return (
      <Badge variant="warning" className={className}>
        Data as of {label} ({ageDays}d old)
      </Badge>
    );
  }
  return (
    <Badge variant="success" className={className}>
      Data as of {label}
    </Badge>
  );
};
