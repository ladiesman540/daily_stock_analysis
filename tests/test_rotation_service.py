# -*- coding: utf-8 -*-
"""Tests for the multi-timeframe sector rotation engine."""

from __future__ import annotations

import unittest
from typing import Dict, List
from unittest.mock import MagicMock

from src.services.free_data_service import BREADTH_ETF_PROXY_SYMBOLS
from src.services.rotation_service import (
    HORIZONS,
    ROTATION_GROUPS,
    RotationService,
    composite_score,
    horizon_return,
    relative_return,
    rrg_quadrant,
)


def _bars(daily_pct: float, *, days: int = 320, start: float = 100.0) -> List[Dict]:
    closes = []
    price = start
    for _ in range(days):
        price *= 1 + daily_pct
        closes.append(price)
    return [
        {"date": f"{2025 + (i // 252)}-{(i // 21) % 12 + 1:02d}-{i % 21 + 1:02d}", "close": close}
        for i, close in enumerate(closes)
    ]


class UniverseSyncTestCase(unittest.TestCase):
    def test_rotation_universe_matches_breadth_universe(self) -> None:
        rotation_symbols = {symbol for members in ROTATION_GROUPS.values() for symbol, _ in members}
        self.assertEqual(rotation_symbols, set(BREADTH_ETF_PROXY_SYMBOLS))


class RotationMathTestCase(unittest.TestCase):
    def test_horizon_return_and_relative(self) -> None:
        closes = [100.0, 110.0]
        self.assertAlmostEqual(horizon_return(closes, 1), 0.10)
        self.assertIsNone(horizon_return(closes, 5))
        self.assertAlmostEqual(relative_return(0.10, 0.0), 0.10)
        self.assertAlmostEqual(relative_return(0.10, 0.10), 0.0)
        self.assertIsNone(relative_return(None, 0.1))

    def test_composite_renormalizes_over_available_horizons(self) -> None:
        full = composite_score({"1W": 1.0, "1M": 1.0, "3M": 1.0, "6M": 1.0, "12M": 1.0})
        partial = composite_score({"1M": 1.0, "3M": 1.0})
        self.assertEqual(full, 100.0)
        self.assertEqual(partial, 100.0)
        self.assertIsNone(composite_score({}))

    def test_rrg_quadrants(self) -> None:
        rising = [1.0 * (1.002 ** i) for i in range(120)]            # strong and strengthening
        fading = [1.0 * (1.004 ** i) for i in range(90)]
        fading += [fading[-1] * (0.999 ** (i + 1)) for i in range(30)]  # strong but rolling over
        falling = [1.0 * (0.998 ** i) for i in range(120)]           # weak and weakening
        turning = [1.0 * (0.996 ** i) for i in range(100)]
        turning += [turning[-1] * (1.0015 ** (i + 1)) for i in range(20)]  # weak but improving

        self.assertEqual(rrg_quadrant(rising)[2], "leading")
        self.assertEqual(rrg_quadrant(falling)[2], "lagging")
        self.assertEqual(rrg_quadrant(turning)[2], "improving")
        ratio, momentum, quadrant = rrg_quadrant(fading)
        self.assertLess(momentum, 100)
        self.assertIn(quadrant, ("weakening", "lagging"))

    def test_rrg_insufficient_history_returns_none(self) -> None:
        self.assertEqual(rrg_quadrant([1.0] * 30), (None, None, None))


class RotationSnapshotTestCase(unittest.TestCase):
    """End-to-end snapshot with mocked market data and DB."""

    def _service(self, histories: Dict[str, List[Dict]], previous: Dict | None = None) -> RotationService:
        market_data = MagicMock()
        market_data.get_us_equity_history.side_effect = lambda symbol, days=320: {
            "symbol": symbol,
            "bars": histories.get(symbol, []),
        }
        db = MagicMock()
        db.get_previous_sector_rotation_snapshot.return_value = previous
        return RotationService(market_data=market_data, db=db)

    def _histories(self) -> Dict[str, List[Dict]]:
        histories = {symbol: _bars(0.0005) for members in ROTATION_GROUPS.values() for symbol, _ in members}
        histories["SPY"] = _bars(0.0005)
        histories["SMH"] = _bars(0.004)   # clear leader
        histories["TAN"] = _bars(-0.003)  # clear laggard
        return histories

    def test_leader_and_laggard_rank_correctly(self) -> None:
        service = self._service(self._histories())
        snapshot = service.build_snapshot()
        by_symbol = {row["symbol"]: row for row in snapshot["constituents"]}
        self.assertEqual(by_symbol["SMH"]["composite_rank"], 1)
        ranked = [row for row in snapshot["constituents"] if row["composite_rank"] is not None]
        self.assertEqual(by_symbol["TAN"]["composite_rank"], len(ranked))
        for horizon in HORIZONS:
            self.assertEqual(by_symbol["SMH"]["ranks"][horizon], 1)
        self.assertNotIn("SPY", [row["symbol"] for row in ranked])
        self.assertEqual(by_symbol["SMH"]["quadrant"], "leading")
        self.assertEqual(by_symbol["TAN"]["quadrant"], "lagging")

    def test_first_run_has_null_rank_changes(self) -> None:
        service = self._service(self._histories(), previous=None)
        snapshot = service.build_snapshot()
        row = snapshot["constituents"][0]
        self.assertTrue(all(value is None for value in row["rank_change"].values()))
        self.assertFalse(row["entered_top3_1m"])

    def test_rank_change_detects_new_top3(self) -> None:
        previous = {
            "constituents": [
                {"symbol": symbol, "ranks": {"1M": index + 1}}
                for index, symbol in enumerate(["GLD", "SLV", "USO", "SMH", "TAN"])
            ]
        }
        service = self._service(self._histories(), previous=previous)
        snapshot = service.build_snapshot()
        by_symbol = {row["symbol"]: row for row in snapshot["constituents"]}
        # SMH moves from prev rank 4 to rank 1 -> entered top-3, positive change.
        self.assertTrue(by_symbol["SMH"]["entered_top3_1m"])
        self.assertEqual(by_symbol["SMH"]["rank_change"]["1M"], 3)
        # GLD was top-3 before; with flat bars it falls out.
        self.assertTrue(by_symbol["GLD"]["exited_top3_1m"])

    def test_missing_symbol_degrades_to_warning(self) -> None:
        histories = self._histories()
        histories["URA"] = []
        service = self._service(histories)
        snapshot = service.build_snapshot()
        self.assertTrue(any("URA" in warning for warning in snapshot["warnings"]))
        self.assertNotIn("URA", [row["symbol"] for row in snapshot["constituents"]])

    def test_leaders_block_built_per_horizon(self) -> None:
        service = self._service(self._histories())
        snapshot = service.build_snapshot()
        for horizon in ("1D", "1W", "1M", "3M"):
            block = snapshot["leaders"][horizon]
            self.assertEqual(block["leaders"][0]["symbol"], "SMH")
            self.assertEqual(block["laggards"][0]["symbol"], "TAN")


if __name__ == "__main__":
    unittest.main()
