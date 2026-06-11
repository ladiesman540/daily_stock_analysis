import type React from 'react';
import { Line, LineChart } from 'recharts';
import { CHART_COLORS } from './chartTheme';

interface SparklinePoint {
  date: string;
  close: number;
}

interface SparklineProps {
  data: SparklinePoint[];
  width?: number;
  height?: number;
  /** Override the stroke; defaults to green/red based on first vs last close. */
  stroke?: string;
}

/**
 * Tiny inline close-price line (no axes, no tooltip). Colors green when the
 * series ends above where it started, red otherwise.
 */
export const Sparkline: React.FC<SparklineProps> = ({ data, width = 40, height = 16, stroke }) => {
  if (!data || data.length < 2) return null;

  const first = data[0].close;
  const last = data[data.length - 1].close;
  const color = stroke ?? (last >= first ? CHART_COLORS.success : CHART_COLORS.danger);

  return (
    <LineChart
      width={width}
      height={height}
      data={data}
      margin={{ top: 1, right: 1, bottom: 1, left: 1 }}
      aria-hidden
    >
      <Line
        type="monotone"
        dataKey="close"
        stroke={color}
        strokeWidth={1.25}
        dot={false}
        isAnimationActive={false}
      />
    </LineChart>
  );
};
