# -*- coding: utf-8 -*-
"""Tests for the grinding leader scorecard."""

from __future__ import annotations

import math
import unittest

from src.services.research_scoring import GrindingLeaderScorer


def _grinding_leader_bars(*, volume: float = 1_000_000.0, market_cap: float = 5_000_000_000.0):
    closes = []
    for idx in range(260):
        base = 60 * (1.003 ** idx)
        if idx >= 197:
            base = closes[196] * (1 + 0.22 * ((idx - 196) / 63))
        if idx >= 239:
            base = closes[238] * (1 + 0.10 * ((idx - 238) / 21))
        closes.append(base * (1 + 0.012 * math.sin(idx * 1.7)))

    return [
        {
            "date": f"2025-01-{(idx % 28) + 1:02d}",
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": volume,
            "market_cap": market_cap,
        }
        for idx, close in enumerate(closes)
    ]


def _soft_fail_watchlist_bars():
    bars = _grinding_leader_bars()
    start = len(bars) - 10
    for idx in range(start, len(bars)):
        progress = (idx - start + 1) / (len(bars) - start)
        bars[idx]["close"] *= 1 - (0.04 * progress)
        bars[idx]["open"] = bars[idx]["close"] * 0.99
        bars[idx]["high"] = bars[idx]["close"] * 1.01
        bars[idx]["low"] = bars[idx]["close"] * 0.98
    return bars


class GrindingLeaderScoringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.scorer = GrindingLeaderScorer()

    def test_grinding_leader_candidate_passes_primary_gates(self) -> None:
        score = self.scorer.score(
            symbol="NVDA",
            asset_type="stock",
            bars=_grinding_leader_bars(),
            market_cap=5_000_000_000,
            source_evidence=[{"source": "test", "catalyst_tags": ["ai"]}],
        )

        self.assertEqual(score.checklist_status, "pass")
        self.assertGreaterEqual(score.candidate_score, 65)
        self.assertLess(score.candidate_score, 100)
        self.assertTrue(score.metrics["gates"]["above_sma20"])
        self.assertTrue(score.metrics["gates"]["above_sma50"])
        self.assertTrue(score.metrics["gates"]["above_sma200"])
        self.assertGreaterEqual(score.metrics["perf_1m_pct"], 5)
        self.assertLessEqual(score.metrics["perf_1m_pct"], 15)
        self.assertGreaterEqual(score.metrics["perf_3m_pct"], 20)
        self.assertGreaterEqual(score.metrics["rsi14"], 55)
        self.assertLessEqual(score.metrics["rsi14"], 70)

    def test_insufficient_history_is_no_data_not_zero(self) -> None:
        score = self.scorer.score(
            symbol="APP",
            asset_type="stock",
            bars=_grinding_leader_bars()[:120],
            market_cap=5_000_000_000,
        )

        self.assertEqual(score.checklist_status, "no_data")
        self.assertIsNone(score.candidate_score)
        self.assertIn("200 daily bars", score.why_not_higher)

    def test_liquidity_gate_rejects_thin_names(self) -> None:
        score = self.scorer.score(
            symbol="THIN",
            asset_type="stock",
            bars=_grinding_leader_bars(volume=10_000),
            market_cap=5_000_000_000,
        )

        self.assertEqual(score.checklist_status, "reject")
        self.assertFalse(score.metrics["gates"]["liquid"])
        self.assertIn("liquid", score.why_not_higher)

    def test_missing_market_cap_can_use_dollar_volume_proxy(self) -> None:
        score = self.scorer.score(
            symbol="LIQUID",
            asset_type="stock",
            bars=_grinding_leader_bars(volume=3_000_000),
            market_cap=None,
        )

        self.assertTrue(score.metrics["gates"]["market_cap_ok"])
        self.assertTrue(score.metrics["market_cap_proxy_used"])

    def test_watchlist_score_is_capped_below_clean_actionable_score(self) -> None:
        score = self.scorer.score(
            symbol="HOT",
            asset_type="stock",
            bars=_soft_fail_watchlist_bars(),
            market_cap=5_000_000_000,
        )

        self.assertEqual(score.checklist_status, "watchlist")
        self.assertLess(score.candidate_score, 90)
        self.assertGreaterEqual(score.metrics["raw_strength_score"], score.candidate_score)
        self.assertTrue(score.metrics["score_adjustments"])

    def test_archetype_charts_get_distinct_scores(self) -> None:
        """The old scorer collapsed to 0.0/100.0 walls; archetypes must spread out."""

        def _bars_from(closes):
            return [
                {"date": f"2025-01-{(idx % 28) + 1:02d}", "close": close, "volume": 5_000_000.0}
                for idx, close in enumerate(closes)
            ]

        leader = [60 * (1.003 ** idx) * (1 + 0.012 * math.sin(idx * 1.7)) for idx in range(260)]
        downtrend = [200 * (0.998 ** idx) * (1 + 0.01 * math.sin(idx * 1.7)) for idx in range(260)]
        choppy = [100 * (1 + 0.08 * math.sin(idx / 7)) for idx in range(260)]
        parabolic = [100 + 0.05 * idx for idx in range(240)]
        parabolic += [parabolic[-1] * (1.06 ** (idx + 1)) for idx in range(20)]
        recovering = [150 * (0.9985 ** idx) * (1 + 0.01 * math.sin(idx * 1.7)) for idx in range(200)]
        recovering += [recovering[-1] * (1.0028 ** (idx + 1)) * (1 + 0.008 * math.sin(idx * 1.7)) for idx in range(60)]

        leader_score, downtrend_score, choppy_score, parabolic_score, recovering_score = [
            self.scorer.score(symbol=f"T{i}", asset_type="stock", bars=_bars_from(closes), market_cap=5_000_000_000).candidate_score
            for i, closes in enumerate([leader, downtrend, choppy, parabolic, recovering])
        ]
        scores = [leader_score, downtrend_score, choppy_score, parabolic_score, recovering_score]

        self.assertTrue(all(score is not None for score in scores))
        # Healthy-but-different charts must not collapse onto one saturation wall.
        self.assertEqual(
            len({leader_score, parabolic_score, recovering_score}), 3, f"saturated scores: {scores}"
        )
        self.assertTrue(all(score < 100 for score in scores), f"clipped at 100: {scores}")
        self.assertGreater(leader_score, downtrend_score)
        self.assertGreater(leader_score, choppy_score)
        self.assertGreater(leader_score, parabolic_score)

    def test_relative_strength_separates_identical_charts(self) -> None:
        bars = _grinding_leader_bars()
        strong = self.scorer.score(
            symbol="RS1", asset_type="stock", bars=bars, market_cap=5_000_000_000, benchmark_perf_3m=0.0
        )
        weak = self.scorer.score(
            symbol="RS2", asset_type="stock", bars=bars, market_cap=5_000_000_000, benchmark_perf_3m=60.0
        )
        self.assertGreater(strong.candidate_score, weak.candidate_score)


if __name__ == "__main__":
    unittest.main()
