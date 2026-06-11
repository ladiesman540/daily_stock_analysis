# -*- coding: utf-8 -*-
"""Research text extraction helpers for the signal analyst module."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, List


_CASHTAG_RE = re.compile(r"(?<![A-Z0-9_])\$([A-Za-z][A-Za-z0-9.]{0,14})\b")
_UPPER_TOKEN_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}\b")

_FALSE_POSITIVES = {
    "A",
    "AI",  # too ambiguous without cashtag or explicit watchlist context
    "ALL",
    "ATH",
    "BE",
    "CEO",
    "CFO",
    "CPI",
    "DNA",
    "ETF",
    "EPS",
    "FOMC",
    "GDP",
    "IPO",
    "LOL",
    "MACD",
    "MA",
    "NYSE",
    "NASDAQ",
    "OEM",
    "PE",
    "RSI",
    "SEC",
    "USA",
    "USD",
    "USDT",
    "YOY",
}

_CRYPTO_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "BNB",
    "XRP",
    "DOGE",
    "ADA",
    "AVAX",
    "LINK",
    "TON",
    "TRX",
    "DOT",
    "MATIC",
    "POL",
    "SUI",
    "APT",
    "ARB",
    "OP",
    "TIA",
    "SEI",
    "INJ",
    "NEAR",
    "ATOM",
    "LTC",
    "BCH",
    "UNI",
    "AAVE",
    "MKR",
    "PENDLE",
    "WIF",
    "PEPE",
}

_CRYPTO_WORD_ALIASES = {
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
}

_BULLISH_WORDS = {
    "accumulate",
    "accumulation",
    "breakout",
    "bullish",
    "buy",
    "calls",
    "higher",
    "leader",
    "long",
    "moon",
    "parabolic",
    "rip",
    "send",
    "squeeze",
    "strong",
}

_BEARISH_WORDS = {
    "avoid",
    "bearish",
    "breakdown",
    "dump",
    "fade",
    "puts",
    "rug",
    "sell",
    "short",
    "weak",
}

_CATALYST_KEYWORDS = {
    "ai": ("ai", "artificial intelligence", "gpu", "datacenter"),
    "robotics": ("robot", "robotics", "automation"),
    "defense": ("defense", "missile", "drone", "aerospace"),
    "earnings": ("earnings", "eps", "revenue", "guidance"),
    "partnership": ("partnership", "partner", "deal", "contract"),
    "crypto": ("bitcoin", "ethereum", "solana", "crypto", "token", "etf"),
    "rates": ("fed", "rates", "yield", "cpi", "inflation"),
}


@dataclass(frozen=True)
class AssetMentionDraft:
    """A normalized mention extracted from user research."""

    symbol: str
    asset_type: str
    direction: str = "neutral"
    time_horizon: str = "unknown"
    confidence: float = 0.5
    catalyst_tags: List[str] = field(default_factory=list)
    extracted_text: str = ""


def extract_asset_mentions(text: str, *, extra_crypto_symbols: Iterable[str] | None = None) -> List[AssetMentionDraft]:
    """Extract likely stock cashtags and crypto symbols from a research note."""

    raw_text = text or ""
    if not raw_text.strip():
        return []

    crypto_symbols = set(_CRYPTO_SYMBOLS)
    crypto_symbols.update(symbol.upper().strip() for symbol in extra_crypto_symbols or [] if symbol)

    symbols: dict[str, str] = {}
    for match in _CASHTAG_RE.finditer(raw_text):
        symbol = _clean_symbol(match.group(1))
        if not symbol or symbol in _FALSE_POSITIVES:
            continue
        symbols[symbol] = "crypto" if symbol in crypto_symbols else "stock"

    lowered = raw_text.lower()
    for word, symbol in _CRYPTO_WORD_ALIASES.items():
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            symbols.setdefault(symbol, "crypto")

    for match in _UPPER_TOKEN_RE.finditer(raw_text):
        symbol = _clean_symbol(match.group(0))
        if symbol in crypto_symbols:
            symbols.setdefault(symbol, "crypto")

    direction = infer_direction(raw_text)
    horizon = infer_time_horizon(raw_text)
    catalyst_tags = infer_catalyst_tags(raw_text)

    mentions = [
        AssetMentionDraft(
            symbol=symbol,
            asset_type=asset_type,
            direction=direction,
            time_horizon=horizon,
            confidence=_infer_confidence(raw_text, asset_type=asset_type),
            catalyst_tags=catalyst_tags,
            extracted_text=_compact_excerpt(raw_text, symbol),
        )
        for symbol, asset_type in sorted(symbols.items())
    ]
    return mentions


def infer_direction(text: str) -> str:
    lowered = (text or "").lower()
    bullish = sum(1 for word in _BULLISH_WORDS if re.search(rf"\b{re.escape(word)}\b", lowered))
    bearish = sum(1 for word in _BEARISH_WORDS if re.search(rf"\b{re.escape(word)}\b", lowered))
    if bullish > bearish:
        return "bullish"
    if bearish > bullish:
        return "bearish"
    return "neutral"


def infer_time_horizon(text: str) -> str:
    lowered = (text or "").lower()
    if any(word in lowered for word in ("today", "intraday", "scalp", "0dte")):
        return "intraday"
    if any(word in lowered for word in ("week", "swing", "1-2 weeks", "two weeks")):
        return "swing"
    if any(word in lowered for word in ("month", "quarter", "q1", "q2", "q3", "q4")):
        return "position"
    return "unknown"


def infer_catalyst_tags(text: str) -> List[str]:
    lowered = (text or "").lower()
    tags = []
    for tag, needles in _CATALYST_KEYWORDS.items():
        if any(needle in lowered for needle in needles):
            tags.append(tag)
    return tags


def _infer_confidence(text: str, *, asset_type: str) -> float:
    confidence = 0.55 if asset_type == "stock" else 0.6
    lowered = (text or "").lower()
    if any(word in lowered for word in ("maybe", "watching", "possible", "if")):
        confidence -= 0.1
    if any(word in lowered for word in ("high conviction", "strong", "confirmed", "breakout")):
        confidence += 0.15
    return max(0.1, min(0.95, round(confidence, 2)))


def _clean_symbol(raw: str) -> str:
    symbol = (raw or "").strip().upper().replace(".", "-")
    if not symbol or len(symbol) > 15:
        return ""
    if symbol in _FALSE_POSITIVES:
        return ""
    return symbol


def _compact_excerpt(text: str, symbol: str) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= 280:
        return compact
    idx = compact.upper().find(symbol.upper())
    if idx == -1:
        return compact[:277] + "..."
    start = max(0, idx - 110)
    end = min(len(compact), idx + 170)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return prefix + compact[start:end] + suffix
