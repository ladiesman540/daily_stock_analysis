#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configuration doctor: explain what this install will actually do, and why.

Read-only. Answers the questions that are otherwise buried in src/config.py:
which LLM config layer won, which data sources are usable, whether
notifications will go anywhere, whether the dashboard can be served, whether
launchd scheduling is installed, and how fresh the daily snapshot outputs
(discovery / down-day RS / news scoring) are.

Usage:
  python scripts/doctor.py            # instant, offline
  python scripts/doctor.py --live     # also run `codex login status` + a yfinance fetch
  python scripts/doctor.py --json     # machine-readable output

Exit code: 0 when no [FAIL] items, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402

OK = "ok"
INFO = "info"
WARN = "warn"
FAIL = "FAIL"

_MARKERS = {OK: "[ok]  ", INFO: "[info]", WARN: "[warn]", FAIL: "[FAIL]"}


class Report:
    """Collects (level, message, hint) items per section and renders them."""

    def __init__(self) -> None:
        self.sections: dict[str, list[dict]] = {}

    def add(self, section: str, level: str, message: str, hint: str = "") -> None:
        self.sections.setdefault(section, []).append(
            {"level": level, "message": message, "hint": hint}
        )

    @property
    def has_fail(self) -> bool:
        return any(
            item["level"] == FAIL
            for items in self.sections.values()
            for item in items
        )

    def render(self) -> str:
        lines: list[str] = []
        for section, items in self.sections.items():
            lines.append(f"== {section} ==")
            for item in items:
                lines.append(f"  {_MARKERS[item['level']]} {item['message']}")
                if item["hint"]:
                    lines.append(f"         fix: {item['hint']}")
            lines.append("")
        verdict = "1 or more problems need attention" if self.has_fail else "no blocking problems"
        lines.append(f"Doctor verdict: {verdict}.")
        return "\n".join(lines)

    def to_json(self) -> dict:
        return {"ok": not self.has_fail, "sections": self.sections}


def _env_file_path() -> Path:
    import os

    env_file = os.getenv("ENV_FILE")
    return Path(env_file) if env_file else ROOT / ".env"


def check_env(report: Report) -> None:
    from src.config import Config

    env_path = _env_file_path()
    if env_path.exists():
        var_count = len(re.findall(r"^[A-Z_]+=", env_path.read_text(encoding="utf-8"), re.M))
        report.add("Environment", OK, f"loaded {env_path} ({var_count} variables set)")
    else:
        report.add(
            "Environment", WARN, f"no .env file at {env_path} — running on defaults only",
            hint="cp .env.owner.example .env and fill in your keys",
        )
    overrides = getattr(Config, "_BOOTSTRAP_RUNTIME_ENV_OVERRIDES", frozenset())
    if overrides:
        report.add(
            "Environment", INFO,
            f"{len(overrides)} variables came from the shell, not .env: "
            + ", ".join(sorted(overrides)[:6]),
        )


def check_llm(report: Report, config, live: bool) -> None:
    from src.config import (
        get_effective_agent_data_model,
        get_effective_agent_primary_model,
    )

    source = getattr(config, "llm_models_source", "legacy_env")
    ladder = [
        ("litellm_config", "1. LITELLM_CONFIG yaml file"),
        ("llm_channels", "2. LLM_CHANNELS env var"),
        ("legacy_env", "3. legacy provider keys / Codex sign-in"),
    ]
    for key, label in ladder:
        marker = "  <-- ACTIVE" if key == source else ""
        report.add("LLM", INFO, f"{label}{marker}")

    primary = get_effective_agent_primary_model(config)
    data_model = get_effective_agent_data_model(config)
    report.add("LLM", OK if primary else WARN,
               f"effective reasoning model: {primary or '(none resolved)'}")
    if data_model and data_model != primary:
        report.add("LLM", OK, f"effective data-gathering model: {data_model}")

    codex_enabled = bool(getattr(config, "openai_codex_auth_enabled", False))
    if codex_enabled:
        from src.services.codex_auth_bridge import get_codex_auth_status

        status = get_codex_auth_status(
            auth_path=getattr(config, "openai_codex_auth_path", None),
            cli_path=getattr(config, "openai_codex_cli_path", None),
            run_cli_status=live,
        )
        if status["logged_in"]:
            expires = status.get("token_expires_at")
            ttl = ""
            if expires:
                hours = max(0, int((expires - time.time()) / 3600))
                ttl = f" (token refreshes; current one expires in ~{hours}h)"
            report.add("LLM", OK, f"Codex/ChatGPT sign-in active{ttl}")
        elif status["token_expired"]:
            report.add("LLM", FAIL, "Codex sign-in token is EXPIRED — LLM calls will fail",
                       hint="run `codex login` (or open the Codex app and sign in)")
        elif not status["auth_file_exists"]:
            report.add("LLM", FAIL, f"Codex auth file missing: {status['auth_path']}",
                       hint="run `codex login`, or disable OPENAI_CODEX_AUTH_ENABLED")
        else:
            report.add("LLM", FAIL, "Codex auth file present but no usable token",
                       hint="run `codex login`")
        if live and status.get("status_message"):
            report.add("LLM", INFO, f"codex login status: {status['status_message']}")
    elif not getattr(config, "llm_model_list", []) and not primary:
        report.add("LLM", FAIL, "no usable LLM configured at any layer",
                   hint="set OPENAI_CODEX_AUTH_ENABLED=true, or an API key (see OWNER.md)")


def check_data_sources(report: Report, config, live: bool) -> None:
    import os

    for var, label in [
        ("ALPHA_VANTAGE_API_KEY", "Alpha Vantage"),
        ("POLYGON_API_KEY", "Polygon"),
        ("MASSIVE_API_KEY", "Massive"),
    ]:
        if (os.getenv(var) or "").strip():
            report.add("Data sources", OK, f"{label} key set")
        else:
            report.add("Data sources", WARN, f"{label} key not set ({var}) — fallbacks will cover it")

    try:
        import yfinance  # noqa: F401
        report.add("Data sources", OK, "yfinance importable (primary free US source)")
    except Exception as exc:
        report.add("Data sources", FAIL, f"yfinance import failed: {exc}",
                   hint="pip install -r requirements.txt inside .venv312")

    try:
        from src.services.watchlist_service import WatchlistService
        from src.storage import DatabaseManager

        db_symbols = WatchlistService().get_symbols()
        env_raw = (os.getenv("RESEARCH_US_WATCHLIST") or "").strip()
        seed_note = " (seeded from env)" if env_raw and not db_symbols else ""
        if db_symbols:
            report.add(
                "Data sources", OK,
                f"Watchlist: {len(db_symbols)} symbols in DB ({', '.join(db_symbols[:5])}...)"
                if len(db_symbols) > 5 else
                f"Watchlist: {len(db_symbols)} symbols in DB ({', '.join(db_symbols)}){seed_note}",
            )
        else:
            env_hint = "" if not env_raw else " (RESEARCH_US_WATCHLIST will seed on first run)"
            report.add(
                "Data sources", WARN,
                f"Watchlist: empty — signal scans have no universe{env_hint}",
                hint="Add symbols via the Settings UI or set RESEARCH_US_WATCHLIST=AAPL,MSFT,... in .env",
            )
    except Exception as exc:
        report.add("Data sources", WARN, f"Watchlist DB check failed: {exc}")

    if live:
        try:
            import yfinance

            hist = yfinance.Ticker("SPY").history(period="5d")
            if hist is None or hist.empty:
                raise RuntimeError("empty response")
            report.add("Data sources", OK, "live check: fetched SPY daily bars via yfinance")
        except Exception as exc:
            report.add("Data sources", FAIL, f"live check: yfinance SPY fetch failed: {exc}",
                       hint="check network / Yahoo availability")


def check_notifications(report: Report) -> None:
    from src.notification import NotificationService

    service = NotificationService()
    channels = service.get_available_channels()
    if channels:
        report.add("Notifications", OK,
                   f"{len(channels)} channel(s) configured: {service.get_channel_names()}")
    else:
        report.add(
            "Notifications", WARN,
            "no notification channel configured — the daily digest is only written to the log",
            hint="set TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID in .env (see OWNER.md, 'Telegram')",
        )


def check_dashboard(report: Report) -> None:
    from src.webui_frontend import _manual_build_command, _resolve_artifact_index

    frontend_dir = ROOT / "apps" / "dsa-web"
    for tool in ("node", "npm"):
        if shutil.which(tool):
            report.add("Dashboard", OK, f"{tool} found")
        else:
            report.add("Dashboard", WARN, f"{tool} not found — web UI cannot auto-build",
                       hint="brew install node")
    artifact = _resolve_artifact_index(frontend_dir)
    if artifact.exists():
        report.add("Dashboard", OK, f"frontend build artifact present: {artifact}")
    else:
        report.add("Dashboard", WARN, "frontend not built yet — first `./dsa dashboard` will npm-build it",
                   hint=_manual_build_command(frontend_dir))


def check_scheduling(report: Report) -> None:
    agents_dir = Path.home() / "Library" / "LaunchAgents"
    installed = sorted(p.name for p in agents_dir.glob("com.dsa.*.plist"))
    if installed:
        report.add("Scheduling", OK, f"launchd plists installed: {', '.join(installed)}")
        try:
            listed = subprocess.run(
                ["launchctl", "list"], capture_output=True, text=True, timeout=10, check=False,
            ).stdout
            loaded = [name[:-len(".plist")] for name in installed if name[:-len(".plist")] in listed]
            if loaded:
                report.add("Scheduling", OK, f"loaded in launchd: {', '.join(loaded)}")
            else:
                report.add("Scheduling", WARN, "plists installed but not loaded in launchd",
                           hint="./dsa schedule install")
        except Exception as exc:
            report.add("Scheduling", INFO, f"could not query launchctl: {exc}")
    else:
        report.add("Scheduling", WARN, "no launchd jobs installed — snapshots only run when you run them",
                   hint="./dsa schedule install")

    log_path = ROOT / "logs" / "launchd_daily_snapshot.log"
    if log_path.exists():
        hours = (time.time() - log_path.stat().st_mtime) / 3600
        level = OK if hours < 30 else WARN
        report.add("Scheduling", level, f"last snapshot log activity: {hours:.0f}h ago")
    else:
        report.add("Scheduling", INFO, "snapshot has never run via launchd (no log file yet)")


def check_database(report: Report, config) -> None:
    db_path = Path(getattr(config, "database_path", "./data/stock_analysis.db"))
    if not db_path.is_absolute():
        db_path = ROOT / db_path
    if db_path.exists():
        size_mb = db_path.stat().st_size / (1024 * 1024)
        report.add("Database", OK, f"{db_path} ({size_mb:.1f} MB)")
    else:
        report.add("Database", INFO, f"{db_path} does not exist yet — created on first run")


def check_snapshot_outputs(report: Report) -> None:
    """Freshness of the discovery/news snapshot steps. DB reads only; fail-open."""
    from datetime import date, datetime, time

    try:
        from src.storage import DatabaseManager

        db = DatabaseManager()
    except Exception as exc:
        report.add("Snapshot outputs", WARN, f"snapshot DB check failed: {exc}")
        return

    for label, getter, step in (
        ("discovery snapshot", "get_latest_discovery_snapshot", "discovery"),
        ("down-day RS snapshot", "get_latest_down_day_rs_snapshot", "discovery"),
    ):
        try:
            snap = getattr(db, getter)()
        except Exception as exc:
            report.add("Snapshot outputs", WARN, f"{label} check failed: {exc}")
            continue
        as_of_raw = (snap or {}).get("as_of")
        if not as_of_raw:
            report.add("Snapshot outputs", INFO, f"{label}: never run (no rows yet)",
                       hint=f"./dsa snapshot --steps {step}")
            continue
        age_days = (date.today() - date.fromisoformat(as_of_raw)).days
        level = OK if age_days <= 3 else WARN  # <= 3 covers a weekend gap
        report.add("Snapshot outputs", level, f"latest {label}: as_of {as_of_raw} ({age_days}d old)",
                   hint="" if level == OK else f"./dsa snapshot --steps {step}")

    try:
        from sqlalchemy import func, select

        from src.storage import NewsIntel

        # Naive local midnight — impact_scored_at is written as naive datetime.now().
        midnight = datetime.combine(date.today(), time())
        with db.get_session() as session:
            scored = session.execute(
                select(func.count()).select_from(NewsIntel)
                .where(NewsIntel.impact_scored_at >= midnight)
            ).scalar() or 0
        if scored:
            report.add("Snapshot outputs", OK, f"news impact: {scored} headline(s) scored today")
        else:
            report.add("Snapshot outputs", INFO,
                       "news impact: no headlines scored today (the `news` snapshot step does this)")
    except Exception as exc:
        report.add("Snapshot outputs", WARN, f"news impact check failed: {exc}")


def classify_issue(issue, watchlist_nonempty: bool) -> str:
    """Map a ConfigIssue severity to a doctor level, downgrading STOCK_LIST.

    This fork's daily flow is watchlist-driven (DB table); STOCK_LIST is the
    upstream A-share entry point and intentionally unset here.
    """
    if issue.field == "STOCK_LIST" and watchlist_nonempty:
        return INFO
    return {"error": FAIL, "warning": WARN, "info": INFO}.get(issue.severity, INFO)


def check_structured_issues(report: Report, config) -> None:
    try:
        from src.services.watchlist_service import WatchlistService
        watchlist_nonempty = bool(WatchlistService().get_symbols())
    except Exception:
        import os
        watchlist_nonempty = bool((os.getenv("RESEARCH_US_WATCHLIST") or "").strip())
    issues = config.validate_structured()
    if not issues:
        report.add("Config validation", OK, "validate_structured() reported no issues")
        return
    for issue in issues:
        level = classify_issue(issue, watchlist_nonempty)
        suffix = " (expected in this fork — watchlist-driven, not STOCK_LIST-driven)" \
            if level == INFO and issue.field == "STOCK_LIST" else ""
        report.add("Config validation", level, f"{issue.field or '(general)'}: {issue.message}{suffix}")


def run_all(live: bool = False) -> Report:
    from src.config import Config

    config = Config.get_instance()
    report = Report()
    check_env(report)
    check_llm(report, config, live)
    check_data_sources(report, config, live)
    check_notifications(report)
    check_dashboard(report)
    check_scheduling(report)
    check_database(report, config)
    check_snapshot_outputs(report)
    check_structured_issues(report, config)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose this install's configuration.")
    parser.add_argument("--live", action="store_true",
                        help="also run `codex login status` and a yfinance SPY fetch")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    setup_env()
    logging.disable(logging.WARNING)  # doctor reports state itself; keep service init logs quiet
    report = run_all(live=args.live)
    if args.json:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        print(report.render())
    return 1 if report.has_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
