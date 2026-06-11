import type { DiscoveryConstituent } from '../api/research';

export interface DiscoveryScreenBadge {
  key: string;
  label: string;
  /** Glossary key for the InfoTip; explicit copy used when none fits. */
  term?: string;
  title?: string;
  body?: string;
}

/** Screens this discovery idea passed, rendered as tiny InfoTip'd badges. */
export const discoveryScreens = (item: DiscoveryConstituent): DiscoveryScreenBadge[] => {
  const screens: DiscoveryScreenBadge[] = [];
  if (item.near_52w_high) screens.push({ key: '52w', label: '52w high', term: 'screen_52w_high' });
  if (item.rs_top_decile) screens.push({ key: 'rs', label: 'RS top 10%', term: 'rs_percentile' });
  if (item.unusual_volume) screens.push({ key: 'vol', label: 'Volume surge', term: 'screen_unusual_volume' });
  if (item.sector_tailwind) {
    screens.push({
      key: 'sector',
      label: 'Sector tailwind',
      title: 'Sector tailwind',
      body: 'The stock\'s most-correlated sector ETF is itself gaining strength, so the move has group-level support behind it.',
    });
  }
  return screens;
};
