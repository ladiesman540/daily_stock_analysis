# OWNER.md — how I actually run this fork

This is a hardened fork of [daily_stock_analysis](https://github.com/ZhuLinsen/daily_stock_analysis)
re-pointed at **US stocks**: free-tier data, a paper portfolio that trades the system's own
recommendations, daily signal scans over a watchlist, and a local web dashboard. The upstream
project supports far more (A-shares, 11 notification channels, Docker, GitHub Actions, bots) —
all of that still works, but none of it is needed day to day.

**The three commands that matter:**

```bash
./dsa doctor      # is everything wired up? (LLM, data keys, notifications, scheduling)
./dsa snapshot    # the daily run: breadth → regime → rotation → cycle → signals → discovery → analysis (watchlist) → news (impact scoring) → portfolio → backtest → notify (digest)
./dsa dashboard   # web UI at http://127.0.0.1:8000
```

Run `./dsa` with no arguments for the full command list. The first command to run after any
config change is `./dsa doctor` — it tells you what the install will actually do and why.

## The daily routine

The dashboard home page (`/`) is **Today** — the morning read in order: regime → cycle →
rotation/RRG → top signals → paper-portfolio mini card, plus three discovery surfaces the
snapshot fills in. The old single-stock analyzer moved to `/analyze`.

- **New ideas** (`discovery` step): screens a liquidity-gated US universe (price ≥ $5,
  20-day dollar volume ≥ $20M, ~500 symbols) for 52-week highs, RS-vs-SPY leaders, volume
  surges and sector tailwind — watchlist and ETFs excluded. Top 5 on Today, full ranking
  on Signals.
- **What's holding up** (same step, zero extra fetches): on a red SPY day
  (1D return ≤ `DOWN_DAY_THRESHOLD_PCT`, default -0.75%) lists the sectors and stocks that
  closed green or beat SPY by ≥ 1 point.
- **Market-moving news** (`news` step): one batched LLM call scores today's persisted
  headlines (market_moving / sector / stock_specific / noise); the top 3 show on Today
  and in the digest.

`./dsa snapshot` runs all of it (see the step list above; `--steps discovery,news` reruns
just these). `./dsa doctor` reports the age of the latest discovery/down-day snapshots and
today's scored-headline count under "Snapshot outputs".

One-time setup: `python3 scripts/scorecard_bootstrap.py` (~10-15 min, resumable) backfills a
simulated scorecard history by replaying the discovery screens over past dates — clearly
labeled simulated, never merged with real hit rates (`--dry-run` previews the dates first).

## My configuration

The entire active config is **17 variables** in `.env` (template: `.env.owner.example`).
Everything else in the 634-line `.env.example` uses defaults.

| Variable | What it does |
| --- | --- |
| `OPENAI_CODEX_AUTH_ENABLED` | Use the local Codex/ChatGPT sign-in for LLM calls — no OpenAI API key needed |
| `OPENAI_CODEX_AUTH_PATH` | Path to the Codex auth file (default `~/.codex/auth.json`) |
| `OPENAI_CODEX_CLI_PATH` | Path to the `codex` CLI (used to refresh the token) |
| `AGENT_REASONING_MODEL` | Model for heavy analysis/agent reasoning |
| `AGENT_LITELLM_MODEL` | Agent default model (used when reasoning model is unset) |
| `AGENT_DATA_MODEL` | Cheaper model for data-gathering steps |
| `ALPHA_VANTAGE_API_KEY` | Free-tier US fundamentals/quotes |
| `POLYGON_API_KEY` | Polygon market data (free tier) |
| `MASSIVE_API_KEY` | Massive market data for US bars |
| `MASSIVE_API_BASE_URL` | Massive endpoint |
| `POSITIONING_OPTIONS_PROVIDER_PRIORITY` | Which options-data providers the positioning service tries, in order |
| `POSITIONING_USE_YFINANCE_OPTIONS` | Allow yfinance options chains as a positioning source |
| `RESEARCH_US_INCLUDE_ETFS` | Include ETFs in US signal scans (default true) |
| `RESEARCH_US_WATCHLIST` | Comma-separated US symbols that drive the daily signal scan |
| `X_CLIENT_ID` / `X_CLIENT_SECRET` / `X_REDIRECT_URI` | X (Twitter) OAuth for social sentiment in research |

Note: `STOCK_LIST` (upstream's A-share entry point) is **intentionally unset** — this fork's
daily flow is driven by `RESEARCH_US_WATCHLIST` instead. The doctor knows this and won't
flag it.

## How the LLM is wired

The app has three LLM config layers, tried in order; the first one that produces models wins:

1. `LITELLM_CONFIG` (a litellm YAML file) — not used here
2. `LLM_CHANNELS` (env-var channel definitions) — not used here
3. **Legacy provider keys / Codex sign-in — ACTIVE in this fork**

With `OPENAI_CODEX_AUTH_ENABLED=true`, OpenAI calls authenticate with the token in
`~/.codex/auth.json` (read backend-only via `src/services/codex_auth_bridge.py`; tokens
never land in `.env` or the browser). If LLM calls start failing, the token has probably
expired: run `codex login`, then `./dsa doctor` to confirm. `./dsa doctor` always prints
which layer is active and which models resolved.

## Scheduling (launchd, macOS)

```bash
./dsa schedule install        # daily snapshot, weekdays 15:00 local (after US close)
./dsa schedule install --all  # + intraday alert check + always-on web UI
./dsa schedule status
./dsa logs snapshot           # tail the run log
```

launchd (not cron, not the in-app `--schedule` flag) because `StartCalendarInterval` runs
missed jobs at next wake — a closed laptop lid doesn't silently skip a day. Details, caveats,
and manual install steps: [docs/macos-scheduling.md](docs/macos-scheduling.md).

Each snapshot run prints a per-step JSON summary at the end of the log. A failed step shows
`"status": "failed"` with the error; the notify step shows per-channel send results.

## Where data lives

| What | Where |
| --- | --- |
| SQLite database (analyses, snapshots, paper portfolio) | `data/stock_analysis.db` (override: `DATABASE_PATH`) |
| Run logs from launchd | `logs/launchd_*.log` (gitignored) |
| Web UI build artifacts | `static/` (auto-built from `apps/dsa-web/` on first `./dsa dashboard`) |
| Saved reports | `reports/` |

## Adding Telegram (when ready)

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → copy the token.
2. Message your new bot once, then get your chat id from `https://api.telegram.org/bot<TOKEN>/getUpdates`.
3. Add to `.env`:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
# TELEGRAM_MESSAGE_THREAD_ID=...   # only for forum-style group topics
```

4. `./dsa doctor` should now show Telegram under Notifications; `./dsa snapshot --steps notify`
   sends a real digest as a test. Until a channel is configured, digests are only written to the log.

## Escape hatches

- Full CLI (29 flags: backtests, market review, scheduling, serve modes): `python main.py --help`
- Upstream English docs: [docs/INDEX_EN.md](docs/INDEX_EN.md)
- One-off analysis of any symbols: `./dsa analyze AAPL,COHR`
