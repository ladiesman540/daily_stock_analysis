# Cycle Stack: Sector Rotation, Business Cycle, Crypto Cycle

The cycle stack answers the cycles-investor question — "where is the meta cycle?" — on
three layers, all from free data:

1. **Sector rotation** (weeks–months): what's hot/not per timeframe, and whether
   leadership is fresh or ageing.
2. **Business cycle** (quarters–years): early / mid / late / contraction, from FRED.
3. **Crypto cycle**: BTC trend, ETH/BTC, dominance.

Everything is computed by `scripts/daily_snapshot.py` (steps `rotation`, `cycle`),
persisted one row per day (`sector_rotation_daily`, `macro_cycle_daily`), surfaced on
the **Rotation & Cycle** page and in the Telegram digest.

## Sector rotation (`src/services/rotation_service.py`)

- **Universe**: the same ~51 liquid ETFs as the breadth job (`BREADTH_ETF_PROXY_SYMBOLS`),
  grouped into index/style, sectors, industries/themes, bonds/intl/commodities.
  Bars come from the local daily-bar cache — no extra API cost.
- **Horizons**: 1D, 1W (5 bars), 1M (21), 3M (63), 6M (126), 12M (252).
  1D is display-only (excluded from the composite — single-day moves are noise).
- **Relative strength**: `rel = (1 + etf_ret) / (1 + spy_ret) - 1`, ranked per horizon
  (1 = strongest). Composite = weighted mean of per-horizon percentiles
  (1W 10%, 1M 30%, 3M 30%, 6M 20%, 12M 10%), renormalized when a horizon is missing.
- **RRG quadrants** from the date-aligned RS line `rs = etf_close / spy_close`:
  - `rs_ratio = 100 × rs_now / SMA63(rs)` — above 100 = relatively strong
  - `rs_momentum = 100 × rs_ratio_now / rs_ratio_21d_ago` — above 100 = strengthening
  - leading (≥100, ≥100) · weakening (≥100, <100) · improving (<100, >100) ·
    lagging (<100, ≤100). A momentum of exactly 100 (constant-rate trend) is treated
    as continuation, not a turn.
- **Rank-change**: every snapshot diffs ranks against the previous persisted snapshot;
  `entered_top3_1m` / `exited_top3_1m` drive the "new" badges and Telegram alerts.
  The first run has no previous snapshot — all changes null, no alerts.

## Business cycle (`src/services/macro_cycle_service.py`)

Nine FRED series, each reduced to a directional reading that votes over
{early, mid, late, contraction}:

| Series | Reading |
| --- | --- |
| `T10Y2Y` | curve level + 63d slope (inversion → late; re-steepening from inversion → transition) |
| `ICSA` | 4-wk avg claims vs 13 weeks ago (surge → contraction) |
| `UNRATE` | Sahm-style gap: 3m avg − 12m low. **≥ 0.50 hard-overrides the phase to contraction** |
| `INDPRO` | YoY output growth |
| `NFCI` | financial conditions level + 13w change |
| `PERMIT` | housing permits YoY (classic lead indicator) |
| `DFF` | 3m change → Fed hiking / holding / cutting |
| `T10YIE` | 10y breakeven 63d change |
| `BAMLH0A0HYM2` | HY spread level + 63d change (fast widening → contraction) |

Phase = argmax of votes; confidence = high/medium/low from indicator availability ×
margin between the top two phases. Missing/degraded series contribute no vote and only
lower confidence — the classifier never crashes on data gaps.

**Playbook + divergence**: each phase maps to the classic sector playbook
(early: XLY/XLF/XLI/IWM/ITB/KRE · mid: XLK/SMH/IGV/XLC · late: XLE/XLP/XLV/XLU/XME ·
contraction: TLT/IEF/XLP/XLV/GLD/QUAL). The detected phase is compared against the
rotation engine's actual top-quartile leaders; a mismatch sets `divergence=true` with a
plain-English note. Divergence is itself a signal: either the cycle read is early or the
rally is narrow.

FRED fetching uses `FreeDataService.fred_series_history()` — full series CSV limited
server-side via `cosd`, `Connection: close` per request (FRED stalls rapid keep-alive
reuse), one retry on timeout, cached 6h.

## Crypto cycle

- BTC vs SMA200 + 50/200 cross and ETH/BTC 21d/63d trend from the cached crypto bars
  (Binance → Kraken → CoinGecko chain).
- BTC dominance + total market cap from CoinGecko `/global` (cooldown-aware, cached
  ~1h, optional — the gauge degrades to bars-only when unavailable).
- Gauge: `bear` (BTC < 200DMA with death cross) · `btc_led_bull` (uptrend, BTC-led) ·
  `alt_season_risk_on` (uptrend + ETH/BTC strengthening) · `mixed`.

## Alerts

`DailyDigestService.send_threshold_alerts()` fires (once per condition per day, state in
`data/alert_state.json`):

- cycle phase change (e.g. `mid → late`)
- a symbol entering/exiting the 1M top-3 rotation ranks

## Degradation behavior

| Failure | Effect |
| --- | --- |
| A FRED series times out | indicator shows `degraded`, no vote, confidence drops |
| CoinGecko /global rate-limited | crypto gauge loses dominance context only |
| An ETF has no bars | excluded with a warning; ranks renormalize |
| Benchmark (SPY) bars missing | rotation snapshot skipped with an explicit warning |
| No previous snapshot | rank changes null; no rotation alerts |
