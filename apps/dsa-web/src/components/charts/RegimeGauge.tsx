import type React from 'react';
import { Cell, Pie, PieChart } from 'recharts';
import { cn } from '../../utils/cn';
import { CHART_COLORS } from './chartTheme';

interface RegimeGaugeProps {
  /** Regime score 0-100; null/undefined renders an empty state. */
  score?: number | null;
  width?: number;
  className?: string;
}

/** Zone arcs: red < 40, amber 40-60, green > 60 (matches the regime labels). */
const ZONES = [
  { value: 40, color: CHART_COLORS.danger },
  { value: 20, color: CHART_COLORS.warning },
  { value: 40, color: CHART_COLORS.success },
];

const zoneTextClass = (score: number): string => {
  if (score < 40) return 'text-danger';
  if (score <= 60) return 'text-warning';
  return 'text-success';
};

/**
 * Semicircular 0-100 gauge with red/amber/green zone arcs and a needle at the
 * current regime score.
 */
export const RegimeGauge: React.FC<RegimeGaugeProps> = ({ score, width = 180, className }) => {
  const height = width / 2 + 12;
  const cx = width / 2;
  const cy = width / 2; // semicircle baseline
  const outerRadius = width / 2 - 6;
  const innerRadius = outerRadius - 14;

  const hasScore = score != null && Number.isFinite(score);
  const clamped = hasScore ? Math.max(0, Math.min(100, score)) : 0;
  // 0 points left (180deg), 100 points right (0deg).
  const angle = (180 - clamped * 1.8) * (Math.PI / 180);
  const needleLength = innerRadius - 4;
  const needleX = cx + needleLength * Math.cos(angle);
  const needleY = cy - needleLength * Math.sin(angle);

  return (
    <div className={cn('relative', className)} style={{ width, height }}>
      <PieChart width={width} height={height} margin={{ top: 0, right: 0, bottom: 0, left: 0 }}>
        <Pie
          data={ZONES}
          dataKey="value"
          cx={cx}
          cy={cy}
          startAngle={180}
          endAngle={0}
          innerRadius={innerRadius}
          outerRadius={outerRadius}
          stroke="none"
          isAnimationActive={false}
        >
          {ZONES.map((zone) => (
            <Cell key={zone.color} fill={zone.color} fillOpacity={0.85} />
          ))}
        </Pie>
      </PieChart>

      {hasScore ? (
        <svg
          className="pointer-events-none absolute inset-0"
          width={width}
          height={height}
          aria-hidden
        >
          <line
            x1={cx}
            y1={cy}
            x2={needleX}
            y2={needleY}
            stroke={CHART_COLORS.foreground}
            strokeWidth={2}
            strokeLinecap="round"
          />
          <circle cx={cx} cy={cy} r={4} fill={CHART_COLORS.foreground} />
        </svg>
      ) : null}

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center">
        {hasScore ? (
          <span className={cn('text-xl font-bold leading-none', zoneTextClass(clamped))}>
            {Math.round(clamped)}
          </span>
        ) : (
          <span className="text-xl font-bold leading-none text-muted-text">--</span>
        )}
      </div>
    </div>
  );
};
