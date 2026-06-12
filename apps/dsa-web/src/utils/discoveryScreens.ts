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
  if (item.quiet_accumulation) {
    screens.push({ key: 'quiet', label: 'Quiet accumulation', term: 'screen_quiet_accumulation' });
  }
  if (item.beaten_down_reversal) {
    screens.push({ key: 'reversal', label: 'Reversal setup', term: 'screen_beaten_down_reversal' });
  }
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

/** Same badges from a stored screens key-list (scorecard rows persist keys, not booleans). */
export const discoveryScreensFromKeys = (keys?: string[] | null): DiscoveryScreenBadge[] =>
  discoveryScreens({
    symbol: '',
    near_52w_high: keys?.includes('near_52w_high'),
    rs_top_decile: keys?.includes('rs_top_decile'),
    unusual_volume: keys?.includes('unusual_volume'),
    quiet_accumulation: keys?.includes('quiet_accumulation'),
    beaten_down_reversal: keys?.includes('beaten_down_reversal'),
    sector_tailwind: keys?.includes('sector_tailwind'),
  });
