# -*- coding: utf-8 -*-
"""Shared test fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_research_market_data(monkeypatch):
    """Keep research market-data state hermetic per test.

    The production code keeps process-wide rate-limit budgets, crypto caches, and a
    DB-backed bar cache; without a reset, earlier tests exhaust the Massive budget or
    leak cached bars into later tests.
    """
    monkeypatch.setenv("RESEARCH_BAR_CACHE_ENABLED", "false")
    from src.services import research_market_data as rmd

    rmd._MASSIVE_LIMITER._calls.clear()
    rmd._YFINANCE_LIMITER._calls.clear()
    rmd._COINGECKO_COOLDOWN_UNTIL = 0.0
    with rmd._CRYPTO_CACHE_LOCK:
        rmd._CRYPTO_BARS_CACHE.clear()
        rmd._CRYPTO_UNIVERSE_CACHE.clear()
    rmd._EQUITY_PROFILE_CACHE.clear()
    yield
