# -*- coding: utf-8 -*-
"""Tests for the business-cycle phase classifier."""

from __future__ import annotations

import unittest
from typing import Dict, List

from src.services.macro_cycle_service import PHASE_PLAYBOOK, classify_cycle, derive_reading


def _series(series_id: str, values: List[float], *, monthly: bool = False) -> Dict:
    observations = []
    for index, value in enumerate(values):
        year = 2020 + (index // (12 if monthly else 365))
        month = (index // (1 if monthly else 31)) % 12 + 1
        day = 1 if monthly else index % 28 + 1
        observations.append({"date": f"{year}-{month:02d}-{day:02d}", "value": value})
    return {"id": series_id, "label": series_id, "status": "ok", "observations": observations}


def _reading(series_id: str, values: List[float], *, monthly: bool = False) -> Dict:
    return derive_reading(series_id, series_id, _series(series_id, values, monthly=monthly))


class DeriveReadingTestCase(unittest.TestCase):
    def test_inverted_curve_votes_late(self) -> None:
        reading = _reading("T10Y2Y", [-0.5] * 120)
        self.assertEqual(reading["votes"], {"late": 1.0})

    def test_sahm_trigger_detected(self) -> None:
        # 12 months at 3.5%, then a fast rise to 4.3% -> Sahm gap >= 0.5
        reading = _reading("UNRATE", [3.5] * 12 + [3.9, 4.1, 4.3], monthly=True)
        self.assertGreaterEqual(reading["sahm"], 0.5)
        self.assertEqual(reading["votes"], {"contraction": 2.0})

    def test_claims_surge_votes_contraction(self) -> None:
        reading = _reading("ICSA", [220_000.0] * 20 + [270_000.0] * 4)
        self.assertEqual(reading["votes"], {"contraction": 1.0})

    def test_fed_cutting_votes_early_or_contraction(self) -> None:
        reading = _reading("DFF", [5.25] * 60 + [4.5] * 60)
        self.assertEqual(reading.get("stance"), "cutting")
        self.assertIn("early", reading["votes"])

    def test_missing_series_yields_no_votes(self) -> None:
        reading = derive_reading("INDPRO", "INDPRO", {"status": "degraded", "observations": []})
        self.assertEqual(reading["votes"], {})
        self.assertIsNone(reading["value"])


class ClassifyCycleTestCase(unittest.TestCase):
    def test_sahm_hard_override_forces_contraction(self) -> None:
        readings = [
            _reading("UNRATE", [3.5] * 12 + [3.9, 4.1, 4.3], monthly=True),
            _reading("INDPRO", [100.0 + i * 0.3 for i in range(24)], monthly=True),  # growing
            _reading("BAMLH0A0HYM2", [3.0] * 120),  # tight spreads (mid vote)
        ]
        verdict = classify_cycle(readings)
        self.assertTrue(verdict["sahm_triggered"])
        self.assertEqual(verdict["phase"], "contraction")

    def test_mid_cycle_fixture(self) -> None:
        readings = [
            _reading("T10Y2Y", [0.8] * 120),                            # positive curve
            _reading("ICSA", [220_000.0] * 24),                          # stable claims
            _reading("UNRATE", [3.8] * 15, monthly=True),                # at lows
            _reading("INDPRO", [100 * (1.0015 ** i) for i in range(24)], monthly=True),  # ~1.8% yoy
            _reading("BAMLH0A0HYM2", [3.0] * 120),                       # tight credit
        ]
        verdict = classify_cycle(readings)
        self.assertIn(verdict["phase"], ("mid", "early"))
        self.assertFalse(verdict["sahm_triggered"])

    def test_late_cycle_fixture(self) -> None:
        readings = [
            _reading("T10Y2Y", [-0.6] * 120),                            # inverted
            _reading("PERMIT", [1500.0] * 12 + [1400.0] * 12, monthly=True),  # permits shrinking
            _reading("ICSA", [200_000.0] * 20 + [212_000.0] * 4),        # drifting up
            _reading("DFF", [5.3] * 120),                                # holding high
        ]
        verdict = classify_cycle(readings)
        self.assertEqual(verdict["phase"], "late")

    def test_no_data_returns_unknown_low_confidence(self) -> None:
        readings = [
            derive_reading(sid, sid, {"status": "degraded", "observations": []})
            for sid in ("T10Y2Y", "ICSA", "UNRATE", "INDPRO")
        ]
        verdict = classify_cycle(readings)
        self.assertEqual(verdict["phase"], "unknown")
        self.assertEqual(verdict["confidence"], "low")

    def test_few_indicators_lower_confidence(self) -> None:
        readings = [_reading("T10Y2Y", [0.8] * 120)]
        verdict = classify_cycle(readings)
        self.assertEqual(verdict["confidence"], "low")


class PlaybookTestCase(unittest.TestCase):
    def test_playbook_symbols_exist_in_rotation_universe(self) -> None:
        from src.services.rotation_service import ROTATION_GROUPS

        universe = {symbol for members in ROTATION_GROUPS.values() for symbol, _ in members}
        for phase, symbols in PHASE_PLAYBOOK.items():
            missing = set(symbols) - universe
            self.assertFalse(missing, f"{phase} playbook references unknown symbols: {missing}")


if __name__ == "__main__":
    unittest.main()
