import type React from 'react';
import {
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { useSymbolDrawerStore } from '../../stores/symbolDrawerStore';
import { useSymbolNamesStore } from '../../stores/symbolNamesStore';
import {
  CHART_COLORS,
  CHART_TOOLTIP_LABEL_STYLE,
  CHART_TOOLTIP_STYLE,
  QUADRANT_COLORS,
} from './chartTheme';

export interface RRGPoint {
  symbol: string;
  rsRatio: number;
  rsMomentum: number;
  quadrant?: string | null;
}

interface RRGScatterProps {
  points: RRGPoint[];
  className?: string;
}

const quadrantOf = (point: RRGPoint): string => {
  if (point.quadrant) return point.quadrant.toLowerCase();
  if (point.rsRatio >= 100) return point.rsMomentum >= 100 ? 'leading' : 'weakening';
  return point.rsMomentum >= 100 ? 'improving' : 'lagging';
};

const QUADRANT_TITLES: Record<string, string> = {
  leading: 'Leading',
  weakening: 'Weakening',
  lagging: 'Lagging',
  improving: 'Improving',
};

interface DotShapeProps {
  cx?: number;
  cy?: number;
  payload?: RRGPoint;
  onSelect: (point: RRGPoint) => void;
}

/** Colored dot with a small mono symbol label; click opens the symbol drawer. */
const RRGDot: React.FC<DotShapeProps> = ({ cx, cy, payload, onSelect }) => {
  if (cx == null || cy == null || !payload) return null;
  const color = QUADRANT_COLORS[quadrantOf(payload)] ?? CHART_COLORS.muted;
  return (
    <g
      onClick={() => onSelect(payload)}
      className="cursor-pointer"
      role="button"
      aria-label={`Open ${payload.symbol} details`}
    >
      <circle cx={cx} cy={cy} r={9} fill="transparent" />
      <circle cx={cx} cy={cy} r={4} fill={color} stroke="hsl(var(--card))" strokeWidth={1} />
      <text
        x={cx}
        y={cy - 7}
        textAnchor="middle"
        fontSize={10}
        fontFamily="var(--font-mono)"
        fill={CHART_COLORS.foreground}
      >
        {payload.symbol}
      </text>
    </g>
  );
};

/**
 * RRG-style quadrant scatter: x = RS-Ratio, y = RS-Momentum, crosshair at
 * 100/100, quadrants faintly tinted and labeled. Clicking a dot opens the
 * symbol drawer with the rotation context.
 */
export const RRGScatter: React.FC<RRGScatterProps> = ({ points, className }) => {
  const openDrawer = useSymbolDrawerStore((state) => state.openDrawer);
  const names = useSymbolNamesStore((state) => state.names);

  const valid = (points || []).filter(
    (point) => Number.isFinite(point.rsRatio) && Number.isFinite(point.rsMomentum),
  );
  if (valid.length === 0) {
    return (
      <div className={className ?? 'h-64 w-full'}>
        <div className="flex h-full items-center justify-center text-xs text-muted-text">
          No rotation data to plot.
        </div>
      </div>
    );
  }

  const xs = valid.map((point) => point.rsRatio);
  const ys = valid.map((point) => point.rsMomentum);
  const padX = Math.max(0.5, (Math.max(...xs, 100) - Math.min(...xs, 100)) * 0.15);
  const padY = Math.max(0.5, (Math.max(...ys, 100) - Math.min(...ys, 100)) * 0.15);
  const xMin = Math.min(...xs, 100) - padX;
  const xMax = Math.max(...xs, 100) + padX;
  const yMin = Math.min(...ys, 100) - padY;
  const yMax = Math.max(...ys, 100) + padY;

  const handleSelect = (point: RRGPoint) => {
    const quadrant = quadrantOf(point);
    openDrawer(point.symbol, {
      source: 'rotation',
      metrics: [
        { label: 'RS-Ratio', value: point.rsRatio.toFixed(1), term: 'rs_ratio' },
        { label: 'RS-Momentum', value: point.rsMomentum.toFixed(1), term: 'rs_momentum' },
        {
          label: 'Quadrant',
          value: QUADRANT_TITLES[quadrant] ?? quadrant,
          term: `quadrant_${quadrant}`,
        },
      ],
    });
  };

  const quadrantLabel = (
    text: string,
    position: 'insideTopRight' | 'insideBottomRight' | 'insideBottomLeft' | 'insideTopLeft',
    color: string,
  ) => ({
    value: text,
    position,
    fill: color,
    fontSize: 10,
    opacity: 0.9,
  });

  return (
    <div className={className ?? 'h-64 w-full'}>
      <ResponsiveContainer width="100%" height="100%">
        <ScatterChart margin={{ top: 12, right: 12, bottom: 4, left: -22 }}>
          <XAxis
            type="number"
            dataKey="rsRatio"
            name="RS-Ratio"
            domain={[xMin, xMax]}
            tickCount={5}
            tick={{ fontSize: 10, fill: CHART_COLORS.muted }}
            tickFormatter={(value: number) => value.toFixed(0)}
            tickLine={false}
            axisLine={{ stroke: CHART_COLORS.border }}
          />
          <YAxis
            type="number"
            dataKey="rsMomentum"
            name="RS-Momentum"
            domain={[yMin, yMax]}
            tickCount={5}
            tick={{ fontSize: 10, fill: CHART_COLORS.muted }}
            tickFormatter={(value: number) => value.toFixed(0)}
            tickLine={false}
            axisLine={{ stroke: CHART_COLORS.border }}
          />

          <ReferenceArea
            x1={100}
            x2={xMax}
            y1={100}
            y2={yMax}
            fill={QUADRANT_COLORS.leading}
            fillOpacity={0.05}
            stroke="none"
            label={quadrantLabel('Leading', 'insideTopRight', QUADRANT_COLORS.leading)}
          />
          <ReferenceArea
            x1={100}
            x2={xMax}
            y1={yMin}
            y2={100}
            fill={QUADRANT_COLORS.weakening}
            fillOpacity={0.05}
            stroke="none"
            label={quadrantLabel('Weakening', 'insideBottomRight', QUADRANT_COLORS.weakening)}
          />
          <ReferenceArea
            x1={xMin}
            x2={100}
            y1={yMin}
            y2={100}
            fill={QUADRANT_COLORS.lagging}
            fillOpacity={0.05}
            stroke="none"
            label={quadrantLabel('Lagging', 'insideBottomLeft', QUADRANT_COLORS.lagging)}
          />
          <ReferenceArea
            x1={xMin}
            x2={100}
            y1={100}
            y2={yMax}
            fill={QUADRANT_COLORS.improving}
            fillOpacity={0.05}
            stroke="none"
            label={quadrantLabel('Improving', 'insideTopLeft', QUADRANT_COLORS.improving)}
          />

          <ReferenceLine x={100} stroke={CHART_COLORS.border} />
          <ReferenceLine y={100} stroke={CHART_COLORS.border} />

          <Tooltip
            cursor={{ strokeDasharray: '3 3', stroke: CHART_COLORS.border }}
            contentStyle={CHART_TOOLTIP_STYLE}
            labelStyle={CHART_TOOLTIP_LABEL_STYLE}
            formatter={(value, name) => [Number(value).toFixed(2), String(name)]}
            labelFormatter={(_, payload) => {
              const point = payload?.[0]?.payload as RRGPoint | undefined;
              if (!point?.symbol) return '';
              const name = names[point.symbol];
              return name ? `${point.symbol} · ${name}` : point.symbol;
            }}
          />

          <Scatter
            data={valid}
            isAnimationActive={false}
            shape={(props: unknown) => (
              <RRGDot {...(props as Omit<DotShapeProps, 'onSelect'>)} onSelect={handleSelect} />
            )}
          />
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
};
