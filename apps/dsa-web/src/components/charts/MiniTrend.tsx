import type React from 'react';
import { Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_TOOLTIP_LABEL_STYLE,
  CHART_TOOLTIP_STYLE,
} from './chartTheme';

interface MiniTrendPoint {
  date: string;
  value: number;
}

interface MiniTrendProps {
  data: MiniTrendPoint[];
  /** Label shown in the tooltip for the series (e.g. "Score"). */
  name?: string;
  stroke?: string;
  /** Optional horizontal reference line (e.g. 50 for breadth). */
  referenceValue?: number;
  /** Formats tooltip values. */
  valueFormatter?: (value: number) => string;
  className?: string;
}

/**
 * Small full-width trend line (h-12) with a hover tooltip and an optional
 * reference line. Axes are hidden to keep it quiet.
 */
export const MiniTrend: React.FC<MiniTrendProps> = ({
  data,
  name = 'Value',
  stroke = CHART_COLORS.primary,
  referenceValue,
  valueFormatter,
  className,
}) => {
  if (!data || data.length < 2) return null;

  return (
    <div className={className ?? 'h-12 w-full'}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 2, left: 4 }}>
          <XAxis dataKey="date" hide />
          <YAxis hide domain={['auto', 'auto']} />
          {referenceValue != null ? (
            <ReferenceLine y={referenceValue} stroke={CHART_COLORS.border} strokeDasharray="3 3" />
          ) : null}
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            formatter={(value) => [
              valueFormatter ? valueFormatter(Number(value)) : `${Number(value).toFixed(1)}`,
              name,
            ]}
          />
          <Line
            type="monotone"
            dataKey="value"
            name={name}
            stroke={stroke}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};
