# -*- coding: utf-8 -*-
"""Grinding leader scorecard for weekly research signals."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class CandidateScore:
    symbol: str
    asset_type: str
    name: str
    # None means "could not be scored" (e.g. missing data) — distinct from a real 0.
    candidate_score: Optional[float]
    checklist_status: str
    entry_zone: str
    invalidation: str
    risk_reward: str
    source_evidence: List[Dict[str, Any]] = field(default_factory=list)
    why_not_higher: str = ""
    metrics: Dict[str, Any] = field(default_factory=dict)


class GrindingLeaderScorer:
    """Deterministic gates for pre-parabolic grinding leader candidates."""

    def score(
        self,
        *,
        symbol: str,
        asset_type: str,
        bars: List[Dict[str, Any]],
        name: str = "",
        market_cap: Optional[float] = None,
        source_evidence: Optional[List[Dict[str, Any]]] = None,
        benchmark_perf_3m: Optional[float] = None,
    ) -> CandidateScore:
        cleaned_bars = _clean_bars(bars)
        evidence = source_evidence or []
        if len(cleaned_bars) < 200:
            # Missing data is not a verdict: score stays None so these rows never
            # pollute rankings as fake zeros.
            return CandidateScore(
                symbol=symbol.upper(),
                asset_type=asset_type,
                name=name or symbol.upper(),
                candidate_score=None,
                checklist_status="no_data",
                entry_zone="insufficient data",
                invalidation="insufficient data",
                risk_reward="not available",
                source_evidence=evidence,
                why_not_higher="Need at least 200 daily bars for SMA200 and 3-month trend gates.",
                metrics={"bars": len(cleaned_bars)},
            )

        closes = [float(row["close"]) for row in cleaned_bars]
        volumes = [float(row.get("volume") or 0.0) for row in cleaned_bars]
        current = closes[-1]
        sma20 = _sma(closes, 20)
        sma50 = _sma(closes, 50)
        sma200 = _sma(closes, 200)
        perf_1m = _perf(closes, 21)
        perf_3m = _perf(closes, 63)
        rsi14 = _rsi(closes, 14)
        avg_volume_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
        avg_dollar_volume_20 = avg_volume_20 * current
        recent_high_20 = max(closes[-20:])
        distance_from_sma20 = _pct(current, sma20)
        distance_from_20d_high = _pct(current, recent_high_20)
        vol_ratio_5_20 = _volume_ratio(volumes)
        market_cap_threshold = 250_000_000 if asset_type == "crypto" else 300_000_000
        market_cap_proxy_used = market_cap is None and avg_dollar_volume_20 >= 100_000_000

        gates = {
            "above_sma20": current > sma20,
            "above_sma50": current > sma50,
            "above_sma200": current > sma200,
            "perf_1m_5_to_15": 5 <= perf_1m <= 15,
            "perf_3m_20_plus": perf_3m >= 20,
            "rsi_preferred_55_to_70": 55 <= rsi14 <= 70,
            "rsi_not_extreme": rsi14 < 80,
            "liquid": avg_dollar_volume_20 >= (5_000_000 if asset_type == "crypto" else 10_000_000),
            "market_cap_ok": (market_cap or 0) >= market_cap_threshold or market_cap_proxy_used,
        }
        hard_gate_names = (
            "above_sma20",
            "above_sma50",
            "above_sma200",
            "perf_3m_20_plus",
            "rsi_not_extreme",
            "liquid",
            "market_cap_ok",
        )
        failed_hard = [name for name in hard_gate_names if not gates[name]]

        relative_strength_3m = (
            perf_3m - benchmark_perf_3m if benchmark_perf_3m is not None else None
        )

        # Continuous components with a theoretical max of ~96 so strong charts
        # spread out instead of all clipping to exactly 100.
        raw_score = 20.0
        raw_score += 6 if gates["above_sma20"] else -15
        raw_score += 6 if gates["above_sma50"] else -15
        raw_score += 8 if gates["above_sma200"] else -20
        raw_score += _perf_1m_credit(perf_1m)
        raw_score += _perf_3m_credit(perf_3m)
        raw_score += _rsi_credit(rsi14)
        raw_score += 4 if gates["liquid"] else -10
        raw_score += 4 if gates["market_cap_ok"] else -10
        raw_score += min(6, len(evidence) * 2)
        raw_score += _extension_credit(distance_from_sma20)
        raw_score -= 8 if vol_ratio_5_20 >= 3 else 0
        if relative_strength_3m is not None:
            raw_score += max(-5.0, min(10.0, relative_strength_3m * 0.25))
        raw_score = max(0, min(100, round(raw_score, 1)))

        checklist_status = "pass"
        if failed_hard:
            checklist_status = "reject"
        elif not gates["perf_1m_5_to_15"] or not gates["rsi_preferred_55_to_70"] or distance_from_sma20 > 10:
            checklist_status = "watchlist"

        setup_score, score_adjustments = _setup_score(
            raw_score=raw_score,
            checklist_status=checklist_status,
            failed_hard=failed_hard,
            gates=gates,
            distance_from_sma20=distance_from_sma20,
            vol_ratio_5_20=vol_ratio_5_20,
            evidence_count=len(evidence),
        )

        why = _why_not_higher(
            failed_hard=failed_hard,
            perf_1m=perf_1m,
            rsi14=rsi14,
            distance_from_sma20=distance_from_sma20,
            vol_ratio_5_20=vol_ratio_5_20,
            evidence_count=len(evidence),
        )
        support = min(sma20, current * 0.94)
        entry_low = max(sma20, current * 0.97)
        entry_high = min(current * 1.03, recent_high_20 * 1.01)
        upside_target = current * 1.18
        downside = max(0.01, current - support)
        upside = max(0.01, upside_target - current)
        rr = upside / downside

        metrics = {
            "current_price": round(current, 4),
            "sma20": round(sma20, 4),
            "sma50": round(sma50, 4),
            "sma200": round(sma200, 4),
            "perf_1m_pct": round(perf_1m, 2),
            "perf_3m_pct": round(perf_3m, 2),
            "rsi14": round(rsi14, 2),
            "avg_volume_20d": round(avg_volume_20, 2),
            "avg_dollar_volume_20d": round(avg_dollar_volume_20, 2),
            "market_cap": market_cap,
            "market_cap_proxy_used": market_cap_proxy_used,
            "benchmark_perf_3m_pct": round(benchmark_perf_3m, 2) if benchmark_perf_3m is not None else None,
            "relative_strength_3m_pct": round(relative_strength_3m, 2) if relative_strength_3m is not None else None,
            "distance_from_sma20_pct": round(distance_from_sma20, 2),
            "distance_from_20d_high_pct": round(distance_from_20d_high, 2),
            "volume_ratio_5d_vs_20d": round(vol_ratio_5_20, 2),
            "gates": gates,
            "raw_strength_score": raw_score,
            "setup_score": setup_score,
            "score_adjustments": score_adjustments,
        }

        return CandidateScore(
            symbol=symbol.upper(),
            asset_type=asset_type,
            name=name or symbol.upper(),
            candidate_score=setup_score,
            checklist_status=checklist_status,
            entry_zone=f"{entry_low:.2f} - {entry_high:.2f}",
            invalidation=f"Close below {support:.2f} or lose SMA20/SMA50 stack",
            risk_reward=f"{rr:.1f}:1 toward {upside_target:.2f}",
            source_evidence=evidence,
            why_not_higher=why,
            metrics=metrics,
        )


def _perf_1m_credit(perf_1m: float) -> float:
    """Triangular credit peaking (+10) at a 10% one-month grind; negative momentum costs points."""
    if perf_1m <= 0:
        return -8.0
    return 10.0 * max(0.0, 1.0 - abs(perf_1m - 10.0) / 10.0)


def _perf_3m_credit(perf_3m: float) -> float:
    """Continuous 3-month trend credit: scales 0..6 below the 20% gate, 6..10 above it."""
    if perf_3m < 0:
        return -15.0
    if perf_3m < 20:
        return perf_3m * 0.3
    return 6.0 + min(4.0, (perf_3m - 20.0) / 15.0)


def _rsi_credit(rsi14: float) -> float:
    """Triangular credit peaking (+8) at RSI 62; overheated RSI is penalized."""
    if rsi14 >= 80:
        return -12.0
    return 8.0 * max(0.0, 1.0 - abs(rsi14 - 62.0) / 15.0)


def _extension_credit(distance_from_sma20: float) -> float:
    """Continuous credit for sitting near the 20DMA, sliding into a penalty when stretched."""
    if distance_from_sma20 < 0:
        return 0.0
    if distance_from_sma20 <= 8:
        return 4.0 - distance_from_sma20 * 0.25
    return max(-8.0, 2.0 - (distance_from_sma20 - 8.0))


def _clean_bars(bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned = []
    for row in bars or []:
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if close <= 0 or math.isnan(close):
            continue
        cleaned.append({**row, "close": close})
    return cleaned


def _sma(values: List[float], period: int) -> float:
    return sum(values[-period:]) / period


def _perf(values: List[float], lookback: int) -> float:
    if len(values) <= lookback:
        return 0.0
    prior = values[-lookback - 1]
    return _pct(values[-1], prior)


def _pct(value: float, base: float) -> float:
    if not base:
        return 0.0
    return (value - base) / base * 100


def _volume_ratio(volumes: List[float]) -> float:
    if len(volumes) < 20:
        return 0.0
    avg5 = sum(volumes[-5:]) / 5
    avg20 = sum(volumes[-20:]) / 20
    return avg5 / avg20 if avg20 else 0.0


def _rsi(values: List[float], period: int = 14) -> float:
    if len(values) <= period:
        return 50.0
    gains = []
    losses = []
    deltas = [values[i] - values[i - 1] for i in range(1, len(values))]
    for delta in deltas[-period:]:
        if delta >= 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _setup_score(
    *,
    raw_score: float,
    checklist_status: str,
    failed_hard: List[str],
    gates: Dict[str, bool],
    distance_from_sma20: float,
    vol_ratio_5_20: float,
    evidence_count: int,
) -> tuple[float, List[Dict[str, Any]]]:
    """Convert raw chart strength into a current setup/actionability score."""

    score = raw_score
    adjustments: List[Dict[str, Any]] = []

    def apply(reason: str, points: float) -> None:
        nonlocal score
        if points == 0:
            return
        score -= points
        adjustments.append({"reason": reason, "points": -round(points, 1)})

    if checklist_status == "watchlist":
        capped = min(score, 89)
        if capped != score:
            adjustments.append({
                "reason": "capped because this is watchlist, not actionable yet",
                "points": round(capped - score, 1),
            })
            score = capped
    elif checklist_status == "reject":
        capped = min(score, 69)
        if capped != score:
            adjustments.append({
                "reason": "capped because this failed required gates",
                "points": round(capped - score, 1),
            })
            score = capped

    if failed_hard:
        apply(f"{len(failed_hard)} required gate{'s' if len(failed_hard) != 1 else ''} failed", 5 * len(failed_hard))
    if not gates["perf_1m_5_to_15"]:
        apply("1M move is outside the preferred 5-15% grinding band", 8)
    if not gates["rsi_preferred_55_to_70"]:
        apply("RSI is outside the preferred leadership zone", 6)
    if distance_from_sma20 > 10:
        apply("price is stretched more than 10% above the 20-day average", 8)
    if vol_ratio_5_20 >= 3:
        apply("short-term volume looks climactic", 8)
    if evidence_count == 0:
        apply("no catalyst/source evidence attached yet", 4)

    return max(0, min(100, round(score, 1))), adjustments


def _why_not_higher(
    *,
    failed_hard: List[str],
    perf_1m: float,
    rsi14: float,
    distance_from_sma20: float,
    vol_ratio_5_20: float,
    evidence_count: int,
) -> str:
    reasons = []
    if failed_hard:
        reasons.append("failed gates: " + ", ".join(failed_hard))
    if perf_1m < 5:
        reasons.append("one-month trend is not grinding strongly enough")
    elif perf_1m > 15:
        reasons.append("one-month move is already above the preferred grinding band")
    if rsi14 < 55:
        reasons.append("RSI is below preferred leadership zone")
    elif rsi14 > 70:
        reasons.append("RSI is above preferred zone and needs a cleaner reset")
    if distance_from_sma20 > 10:
        reasons.append("price is extended from SMA20")
    if vol_ratio_5_20 >= 3:
        reasons.append("short-term volume looks climactic")
    if evidence_count == 0:
        reasons.append("no recent bookmark/source evidence")
    return "; ".join(reasons) if reasons else "all primary gates are clean"
