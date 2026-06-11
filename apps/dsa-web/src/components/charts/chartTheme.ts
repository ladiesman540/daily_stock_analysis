import type React from 'react';

/**
 * Theme-aware chart colors. These resolve through the CSS variables defined in
 * index.css (light + .dark blocks), so charts follow dark mode without
 * hardcoded hex values.
 */
export const CHART_COLORS = {
  primary: 'hsl(var(--primary))',
  success: 'hsl(var(--color-success))',
  warning: 'hsl(var(--color-warning))',
  danger: 'hsl(var(--color-danger))',
  muted: 'hsl(var(--muted-text))',
  border: 'hsl(var(--border))',
  foreground: 'hsl(var(--foreground))',
} as const;

/** Quadrant colors matching the Badge variants used for rotation chips. */
export const QUADRANT_COLORS: Record<string, string> = {
  leading: CHART_COLORS.success,
  improving: CHART_COLORS.primary,
  weakening: CHART_COLORS.warning,
  lagging: CHART_COLORS.danger,
};

/** Recharts <Tooltip contentStyle> aligned with the app's card/popover styling. */
export const CHART_TOOLTIP_STYLE: React.CSSProperties = {
  backgroundColor: 'hsl(var(--card))',
  border: '1px solid hsl(var(--border))',
  borderRadius: 12,
  fontSize: 12,
  color: 'hsl(var(--foreground))',
  boxShadow: 'var(--shadow-soft-card)',
};

export const CHART_TOOLTIP_LABEL_STYLE: React.CSSProperties = {
  color: 'hsl(var(--muted-text))',
  fontSize: 11,
};
