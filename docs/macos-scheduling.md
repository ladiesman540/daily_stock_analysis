# macOS Scheduling (launchd)

The dashboard is only useful if its data refreshes without anyone remembering to run it.
On a laptop, the in-app `--schedule` mode is unreliable: it only fires if the process is
alive and the Mac is awake at poll time. launchd's `StartCalendarInterval` instead runs
missed jobs at the next wake, so a closed lid never silently skips a day.

## Jobs

| Plist | What it does | When |
| --- | --- | --- |
| `scripts/launchd/com.dsa.daily-snapshot.plist` | Full snapshot: breadth → regime → rotation → cycle → signals → watchlist LLM analysis → paper portfolio → backtest → digest | Weekdays 15:00 local (after US close; adjust to your timezone) |
| `scripts/launchd/com.dsa.intraday-check.plist` | Cheap regime + paper-position check, alert-only (regime flip, VIX stress, stops/targets) | ~Every 90 min during US market hours |
| `scripts/launchd/com.dsa.serve.plist` | Keeps the web UI (`main.py --serve-only`) running across reboots | Always (KeepAlive) |

The snapshot script itself is `scripts/daily_snapshot.py`; each step is isolated, and you
can run any subset manually, e.g.:

```bash
.venv312/bin/python scripts/daily_snapshot.py --steps breadth,regime
.venv312/bin/python scripts/daily_snapshot.py --steps regime,portfolio,notify --alerts-only
```

## Install

1. Edit the plists if your repo path, venv path, or timezone differ. The schedule times
   are **local time**: pick the hour that corresponds to ~17:00 US Eastern.
2. Copy and load:

```bash
cp scripts/launchd/com.dsa.daily-snapshot.plist ~/Library/LaunchAgents/
cp scripts/launchd/com.dsa.intraday-check.plist ~/Library/LaunchAgents/   # optional
cp scripts/launchd/com.dsa.serve.plist ~/Library/LaunchAgents/            # optional

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dsa.daily-snapshot.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dsa.intraday-check.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.dsa.serve.plist
```

3. Verify:

```bash
launchctl list | grep com.dsa
launchctl kickstart gui/$(id -u)/com.dsa.daily-snapshot   # force a run now
tail -f logs/launchd_daily_snapshot.log
```

## Uninstall

```bash
launchctl bootout gui/$(id -u)/com.dsa.daily-snapshot
launchctl bootout gui/$(id -u)/com.dsa.intraday-check
launchctl bootout gui/$(id -u)/com.dsa.serve
rm ~/Library/LaunchAgents/com.dsa.*.plist
```

## Notifications

The digest/alerts go through the existing multi-channel `NotificationService`. The
simplest channel is Telegram: create a bot with @BotFather, then set in `.env`:

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Without any channel configured, the digest is written to the log only.

## Caveats

- launchd reads the plist at bootstrap time; re-run `bootout` + `bootstrap` after edits.
- `StartCalendarInterval` coalesces missed runs into ONE catch-up run at wake — fine here
  because `daily_snapshot.py` is idempotent (one row per as-of date).
- Timezone is whatever the Mac is set to; the plists do not adjust for DST differences
  between your timezone and US Eastern (worst case the job runs an hour after close).
