/**
 * Plain-English glossary for dashboard terms. Each entry backs an InfoTip popover.
 * Definitions are grounded in what the backend actually computes:
 * - regime score: src/services/free_data_service.py (weighted composite, VIX3M cap)
 * - rotation: src/services/rotation_service.py (rs_ratio = 100*RS/SMA63, momentum vs 21d ago)
 * - discovery/down-day: src/services/discovery_service.py (98% of 252-bar high, 2x volume, $20M ADV, SPY -0.75%)
 * - signals: src/services/research_scoring.py (checklist gates, entry zone)
 * - cycle: src/services/macro_cycle_service.py (FRED votes over early/mid/late/contraction)
 */
export interface GlossaryEntry {
  title: string;
  body: string;
  computedFrom?: string;
}

export const GLOSSARY: Record<string, GlossaryEntry> = {
  regime_score: {
    title: 'Regime score',
    body: 'A 0-100 health check of the overall US stock market. Higher means conditions favor being invested; below 40 leans defensive.',
    computedFrom:
      'Composite of index trend, breadth, rally health, volatility and macro (35/20/20/15/10 weights). An inverted VIX curve caps it at 45.',
  },
  vix: {
    title: 'VIX',
    body: "The market's expectation of how choppy the S&P 500 will be over the next 30 days. High readings (roughly above 25) signal fear; low readings signal calm.",
  },
  vix_term_structure: {
    title: 'VIX term structure',
    body: "Compares the 30-day VIX with its 3-month cousin (VIX3M). Normally the 3-month is higher; when near-term VIX rises above it ('inverted'), traders are bracing for trouble right now.",
    computedFrom: 'VIX vs VIX3M. Inversion caps the regime score below Risk-On.',
  },
  breadth_50dma: {
    title: 'Breadth (% above 50-day average)',
    body: 'The share of stocks trading above their own 50-day average price. A high percentage means the rally has broad participation; a low one means a few big names are carrying the market.',
    computedFrom: 'Percent of liquid US stocks (or a sector-ETF proxy) above their 50-day moving average, from the daily breadth cache.',
  },
  cycle_phase: {
    title: 'Business-cycle phase',
    body: 'Where the economy sits in its cycle: early (recovery just starting), mid (steady growth), late (overheating, rates biting) or contraction (shrinking). Each phase historically favors different sectors, which drives the playbook.',
    computedFrom: 'Free FRED macro series each vote over early/mid/late/contraction; a Sahm-rule unemployment trigger forces contraction.',
  },
  rrg: {
    title: 'Relative Rotation Graph (RRG)',
    body: 'A four-quadrant map of how each sector is doing versus the S&P 500. Position shows relative strength, movement shows whether that strength is rising or fading - sectors tend to rotate clockwise through the quadrants.',
  },
  rs_ratio: {
    title: 'RS-Ratio',
    body: 'Relative strength versus the S&P 500. Above 100 means the symbol has been outperforming the index lately; below 100 means lagging it.',
    computedFrom: '100 x (price ratio vs SPY) / its own 63-day average.',
  },
  rs_momentum: {
    title: 'RS-Momentum',
    body: 'Whether that relative strength is improving or fading. Above 100 the symbol is gaining ground on the index; below 100 it is losing steam.',
    computedFrom: '100 x RS-Ratio now / RS-Ratio 21 trading days ago.',
  },
  quadrant_leading: {
    title: 'Leading',
    body: 'Stronger than the index and still gaining ground (RS-Ratio and RS-Momentum both above 100). The current winners.',
  },
  quadrant_weakening: {
    title: 'Weakening',
    body: 'Still stronger than the index but losing steam (RS-Ratio above 100, RS-Momentum below). Often where leaders cool off before falling behind.',
  },
  quadrant_lagging: {
    title: 'Lagging',
    body: 'Weaker than the index and still losing ground (RS-Ratio and RS-Momentum both below 100). The current losers.',
  },
  quadrant_improving: {
    title: 'Improving',
    body: 'Still weaker than the index but picking up speed (RS-Ratio below 100, RS-Momentum above). Where new leaders often emerge.',
  },
  rs_percentile: {
    title: 'RS percentile',
    body: "Where this symbol's strength versus the market ranks against the other names scanned - 90 means it beat 90% of them.",
    computedFrom: 'Rank of return relative to SPY across the scanned universe.',
  },
  screen_52w_high: {
    title: '52-week-high screen',
    body: 'Flags stocks closing within 2% of their highest price of the past year. Strength near highs tends to continue because there are no trapped sellers waiting overhead.',
    computedFrom: 'Close >= 98% of the highest close over the last 252 trading days.',
  },
  screen_unusual_volume: {
    title: 'Unusual volume screen',
    body: 'Flags stocks that rose on far heavier trading than usual - a sign that big buyers showed up.',
    computedFrom: 'An up day with volume >= 2x the prior 20-day average.',
  },
  screen_quiet_accumulation: {
    title: 'Quiet accumulation screen',
    body: 'Flags stocks trading on sustained heavier-than-usual volume while the price barely moves - the footprint of a big buyer absorbing shares without chasing. A watch flag only; it does not affect the composite rank.',
    computedFrom: 'Average volume over the last 5 sessions >= 1.5x the prior 20-session average, with the close within 3% of where it was 5 sessions ago (needs 26+ bars).',
  },
  screen_beaten_down_reversal: {
    title: 'Beaten-down reversal screen',
    body: 'Flags stocks down 40% or more from their 52-week high that are starting to turn: beating the market over the past month on above-average volume. A watch flag only; it does not affect the composite rank.',
    computedFrom: 'Close <= 60% of the 252-day high, 1-month return above SPY (1M RS > 0), and latest volume >= 1.25x the prior 20-day average.',
  },
  adv: {
    title: 'ADV (average dollar volume)',
    body: 'How much money changes hands in the stock on a typical day. The scans require at least $20M so a position can be bought and sold without moving the price.',
    computedFrom: '20-day average of price x volume; $20M minimum gate.',
  },
  entry_zone: {
    title: 'Entry zone',
    body: 'A suggested buy range near the current price, anchored between the 20-day average price and just above the recent high. Chasing far above it worsens your risk/reward.',
    computedFrom: 'From max(20-day avg, price -3%) up to min(price +3%, 20-day high +1%).',
  },
  checklist_status: {
    title: 'Checklist status',
    body: "Pass means the setup clears every hard quality gate (trend, liquidity, size). Watchlist means it is close but something is off - too extended, too hot or too cold. Reject means a hard gate failed.",
  },
  impact_label: {
    title: 'Impact label',
    body: 'How widely a headline matters: Market-moving affects the whole market, Sector affects one industry, Stock-specific affects a single name.',
    computedFrom: 'LLM-scored daily for cached headlines.',
  },
  down_day_rule: {
    title: 'Down-day rule',
    body: 'On days the S&P 500 (SPY) drops 0.75% or more, this lists what held up anyway. Strength on red days often marks future leaders.',
    computedFrom: 'Trigger: SPY <= -0.75% on the day. Holding up: green on the day, or beat SPY by at least 1 percentage point.',
  },
  paper_portfolio: {
    title: 'Paper portfolio',
    body: 'A simulated portfolio that tracks trades with pretend money. It follows real market prices so you can judge the strategy without risking anything.',
  },
  hit_rate: {
    title: 'Hit rate (batting average)',
    body: 'Of everything the discovery screens flagged, the share that went on to gain +20% within 90 days. Flags still inside their window stay "open" and are not counted either way.',
    computedFrom:
      'hits / (hits + flops + expired). Hit: a close >= +20% within 90 calendar days of the flag; flop: a close <= -20% first; expired: the window (plus a 7-day slack) ended touching neither.',
  },
};
