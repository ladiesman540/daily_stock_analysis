#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run the daily US market breadth cache job."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import setup_env  # noqa: E402
from src.services.free_data_service import FreeDataService  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the DSA market breadth cache.")
    parser.add_argument("--universe", choices=["us_stocks", "us_etfs", "us_listed"], default="us_stocks")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-price", type=float, default=None)
    parser.add_argument("--min-avg-dollar-volume", type=float, default=None)
    args = parser.parse_args()

    setup_env()
    payload = FreeDataService(cache_enabled=False).run_market_breadth_cache(
        universe=args.universe,
        limit=args.limit,
        min_price=args.min_price,
        min_avg_dollar_volume=args.min_avg_dollar_volume,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
