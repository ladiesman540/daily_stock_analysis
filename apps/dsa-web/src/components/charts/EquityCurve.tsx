import type React from 'react';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  CHART_COLORS,
  CHART_TOOLTIP_LABEL_STYLE,
  CHART_TOOLTIP_STYLE,
} from './chartTheme';

interface EquityPoint {
  date: string;
  equity: number;
}

interface EquityCurveProps {
  data: EquityPoint[];
  currency?: string;
  className?: string;
}

/**
 * Portfolio equity area chart with a currency-formatted tooltip. Axes stay
 * quiet: sparse date ticks, no Y axis.
 */
export const EquityCurve: React.FC<EquityCurveProps> = ({ data, currency = 'USD', className }) => {
  if (!data || data.length < 2) return null;

  const formatMoney = (value: number): string =>
    new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency,
      maximumFractionDigits: 0,
    }).format(value);

  return (
    <div className={className ?? 'h-40 w-full'}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 6, right: 6, bottom: 0, left: 6 }}>
          <defs>
            <linearGradient id="equity-curve-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CHART_COLORS.primary} stopOpacity={0.25} />
              <stop offset="100%" stopColor={CHART_COLORS.primary} stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="date"
            tick={{ fontSize: 10, fill: CHART_COLORS.muted }}
            tickLine={false}
            axisLine={false}
            minTickGap={48}
            tickFormatter={(value: string) => value.slice(5)}
          />
          <YAxis hide domain={['auto', 'auto']} />
          <Tooltip
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            formatter={(value) => [formatMoney(Number(value)), 'Equity']}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke={CHART_COLORS.primary}
            strokeWidth={1.5}
            fill="url(#equity-curve-fill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};
