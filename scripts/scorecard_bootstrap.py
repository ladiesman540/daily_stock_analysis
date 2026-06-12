#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-time retroactive scorecard bootstrap (simulated history).

Replays the discovery screens over past SPY trading dates (every ~5th session
covering --weeks back, strictly before the earliest real discovery snapshot)
and grades each simulated flag forward; rows are stored with simulated=True
and never merged with real stats. The deep fetch writes through to the
stock_daily cache, so interrupted runs can simply be re-run.

--dry-run prints the sampled as-of dates WITHOUT fetching anything: trading
dates come from cached SPY bars only (no network by design).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap simulated scorecard history from past discovery replays.")
    parser.add_argument("--weeks", type=int, default=None,
                        help="Trading weeks of history to simulate (default: SCORECARD_BOOTSTRAP_WEEKS or 39)")
    parser.add_argument("--deep-days", type=int, default=None,
                        help="Calendar days of bars to fetch per symbol (default: SCORECARD_BOOTSTRAP_DEEP_DAYS or 600)")
    parser.add_argument("--symbol-limit", type=int, default=None,
                        help="Cap the discovery universe to the first N symbols (default: no cap)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the sampled as-of dates from cached SPY bars only; no network, no writes")
    args = parser.parse_args()

    setup_env()
    from src.services.scorecard_service import ScorecardService  # noqa: E402  (after env setup)

    service = ScorecardService()
    if args.dry_run:
        payload = service.preview_bootstrap_dates(weeks=args.weeks)
    else:
        payload = service.run_bootstrap(
            weeks=args.weeks,
            deep_days=args.deep_days,
            symbol_limit=args.symbol_limit,
        )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
