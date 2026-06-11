# -*- coding: utf-8 -*-
"""Tests for research asset extraction."""

from __future__ import annotations

import unittest

from src.services.research_extraction import extract_asset_mentions


class ResearchExtractionTestCase(unittest.TestCase):
    def test_extracts_cashtags_and_crypto_words(self) -> None:
        text = "$NVDA grinding leader, Bitcoin breakout, long $SOL for the next few weeks."
        mentions = extract_asset_mentions(text)
        by_symbol = {item.symbol: item for item in mentions}

        self.assertEqual(by_symbol["NVDA"].asset_type, "stock")
        self.assertEqual(by_symbol["BTC"].asset_type, "crypto")
        self.assertEqual(by_symbol["SOL"].asset_type, "crypto")
        self.assertEqual(by_symbol["NVDA"].direction, "bullish")
        self.assertEqual(by_symbol["SOL"].time_horizon, "swing")

    def test_avoids_common_uppercase_false_positives(self) -> None:
        text = "CPI and FOMC are hot. RSI is high. Watching $AMD, not AI as a ticker."
        symbols = {item.symbol for item in extract_asset_mentions(text)}

        self.assertIn("AMD", symbols)
        self.assertNotIn("CPI", symbols)
        self.assertNotIn("FOMC", symbols)
        self.assertNotIn("RSI", symbols)
        self.assertNotIn("AI", symbols)

    def test_bearish_direction_from_post_language(self) -> None:
        mentions = extract_asset_mentions("Short $TSLA on breakdown, puts look cleaner.")
        self.assertEqual(mentions[0].symbol, "TSLA")
        self.assertEqual(mentions[0].direction, "bearish")


if __name__ == "__main__":
    unittest.main()
