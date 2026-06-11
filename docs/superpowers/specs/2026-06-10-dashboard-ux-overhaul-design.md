# Dashboard UX Overhaul — Design (Approach A: Enhance in Place)

Date: 2026-06-10. Owner feedback driving this: "not user friendly — either overexplains or doesn't explain at all, no way to get more details, hardly any charts."

Owner decisions (locked):
- **Explanations:** keep the terse layout; every term/number gets a tappable tooltip (definition + how it's computed). No two-mode toggle, no prose-first rewrite.
- **Drill-down:** BOTH a symbol drawer (tap any ticker → details) AND card headers linking to their detail pages.
- **Charts:** chart-first — every Today card leads with a visual. recharts (already a dependency).

Constraints: keep the card-stack structure and existing API shapes (additive only); digest output untouched; repo rules (surgical, fail-open, English UI, no commits without owner OK); mobile 390px stays overflow-free.

## Shared building blocks (new, apps/dsa-web/src)

1. **`components/common/InfoTip.tsx`** — dotted-underline term or small ⓘ; tap (mobile) / hover (desktop) opens a popover: 1–2 plain-English sentences + optional "Computed from: …" line. Content keyed into **`utils/glossary.ts`** — a single map `{key: {title, body, computedFrom?}}` (~18 entries: regime score, VIX, VIX3M/term structure, breadth/50DMA, cycle phases, RRG + rs_ratio/rs_momentum + 4 quadrants, RS percentile, 52w-high screen, unusual volume, ADV, entry zone, checklist status, impact labels, down-day rule, paper portfolio). Popover closes on tap-out/Esc; only one open at a time.
2. **`components/common/TickerChip.tsx`** — clickable ticker pill (optional inline sparkline + return); used in Signals/Ideas/Down-day/Rotation rows and playbook lists. Click → opens SymbolDrawer.
3. **`components/symbol/SymbolDrawer.tsx`** + zustand `stores/symbolDrawerStore.ts` — right slide-in (desktop) / bottom sheet (mobile ~85vh). Opened with `{symbol, context}` where context carries whatever the row knows (signal score breakdown, discovery screens passed, down-day vs-SPY delta, rotation returns). Contents: header (symbol, last close, 1D change), 3-month close line chart from existing `GET /api/v1/stocks/{symbol}/history`, "Why it's here" section rendered from context (each metric with InfoTip), actions: Add to watchlist (existing POST), "Full analysis →" link to `/analyze`. Fail-open: chart area shows a quiet "no cached history" state on fetch failure.
4. **`components/charts/`** — thin recharts wrappers themed once: `Sparkline` (30d closes, no axes), `RegimeGauge` (semicircle 0–100, red/amber/green zones, needle at score), `MiniTrend` (small line w/ dots, used for regime 14d + breadth 30d), `RRGScatter` (x=rs_ratio, y=rs_momentum, quadrant-shaded background at 100/100 crosshair, dots labeled by symbol, click dot → drawer), `EquityCurve` (area, 90d).

## Backend (additive, research/portfolio routers, DB-only, fail-open)

- **`GET /api/v1/research/free-data/sparklines?symbols=A,B,C&days=30`** — closes from the `stock_daily` cache ONLY (no network). Returns `{series: {SYM: [{date, close}, …]}}`; unknown/uncached symbols silently omitted; symbols capped at 60; days capped at 90. One DB read per symbol via existing range getter.
- **Brief `regime` payload** — add `history`: the 14-day list already queried in `_collect_regime`, slimmed to `[{as_of, score, vix}]`, plus `breadth_trend`: last 30 `market_breadth_daily` rows slimmed to `[{as_of, pct_above_50dma}]` (new small storage getter, cloned from existing history getters). Renderers unchanged → digest byte-identical.
- **`GET /api/v1/portfolio/equity-history?days=90`** — reads `PortfolioDailySnapshot` for the paper account (reuse however the existing portfolio endpoints resolve the account); returns `[{date, equity, currency}]`. If the table has no rows yet, returns `[]` (UI hides the curve).

## Per-card design (Today page, same order)

1. **Regime** — leads with `RegimeGauge` + 14d `MiniTrend`; VIX/VIX3M and breadth stay as stats, each wrapped in InfoTip; breadth gains its 30d `MiniTrend` when `breadth_trend` non-empty.
2. **Cycle** — phase chip + horizontal 4-segment stage bar (early/mid/late/contraction, marker on current); playbook ETFs become TickerChips; InfoTips on phase + playbook.
3. **Rotation** — leads with `RRGScatter` of the sector group's constituents; hot/not rows become TickerChips with returns; InfoTips on RRG/quadrant terms. Header → `/rotation`.
4. **Signals** — each row: TickerChip + `Sparkline` + score + entry zone (InfoTips on score/checklist/entry). Header → `/signals`.
5. **New ideas** — rows: TickerChip + `Sparkline` + reason + small badges for screens passed (InfoTip each); Add button unchanged. Header → `/signals` (discovery section).
6. **Down day** — SPY stat + holding-up rows as TickerChips with a tiny diverging vs-SPY delta bar; sector list unchanged (incl. unavailable state); Ask-why footer unchanged. InfoTip on the trigger rule.
7. **Headlines** — layout unchanged; InfoTips on impact labels.
8. **Portfolio** — equity + day change + `EquityCurve` when history exists (else current layout); header → `/portfolio`.
9. **Ask** — unchanged.

SignalsPage discovery section rows also get TickerChip + screens-passed badges (drawer works there too). Other pages: out of scope this pass.

## Data flow

TodayPage keeps its single brief fetch + portfolio fetch; adds ONE `sparklines` batch call (symbols = signals ∪ ideas ∪ down-day rows, deduped) and one `equity-history` call. Drawer fetches `/stocks/{symbol}/history` lazily on open (cached per symbol in the store for the session).

## Error handling

Every new fetch is independent and fail-open: missing sparkline → chip renders without sparkline; gauge/scatter guard null fields; drawer shows quiet empty states. No new fetch can blank a card (existing allSettled pattern preserved).

## Testing / verification

- pytest: sparklines endpoint (cache-only, cap, unknown symbols omitted, no network), regime history/breadth_trend in brief (and digest byte-identical), equity-history endpoint (empty + seeded).
- `tsc -b` + `npm run build` clean.
- Independent Playwright evaluator at 1280px + 390px: tooltips open/close, drawer opens with chart + why-section from each card type, RRG scatter renders & dots clickable, no horizontal overflow, console clean.

## Execution phases

P1 backend (endpoint + brief additions + tests) → P2 frontend foundation (glossary, InfoTip, TickerChip, drawer + store, chart kit) → P3 chart-first cards on Today → P4 SignalsPage chips + mobile polish → P5 evaluator browser pass + fixes.
