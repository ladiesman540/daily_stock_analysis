# -*- coding: utf-8 -*-
"""Business-cycle phase composite from free FRED data, plus a crypto cycle gauge.

Each indicator derives a directional reading from full series history (not just the
latest value) and votes over {early, mid, late, contraction}. A Sahm-rule trigger
hard-overrides to contraction. The detected phase maps to a classic sector playbook,
which is compared against what the rotation engine actually shows leading — an
agreement/divergence flag, because divergence is itself a signal.

Missing or degraded series contribute no vote and lower confidence; nothing here
raises on data gaps (free-tier requirement).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from src.services.free_data_service import FreeDataService
from src.services.research_market_data import (
    ResearchMarketDataService,
    _coingecko_cooling_down,
    _coingecko_mark_rate_limited,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

PHASES = ("early", "mid", "late", "contraction")

# Classic cycle-phase sector playbook (symbols must exist in the rotation universe).
PHASE_PLAYBOOK: Dict[str, List[str]] = {
    "early": ["XLY", "XLF", "XLI", "IWM", "ITB", "KRE"],
    "mid": ["XLK", "SMH", "IGV", "XLC"],
    "late": ["XLE", "XLP", "XLV", "XLU", "XME"],
    "contraction": ["TLT", "IEF", "XLP", "XLV", "GLD", "QUAL"],
}

FRED_SERIES = [
    {"id": "T10Y2Y", "label": "10Y-2Y curve", "unit": "%"},
    {"id": "ICSA", "label": "Initial claims", "unit": "count"},
    {"id": "UNRATE", "label": "Unemployment rate", "unit": "%"},
    {"id": "INDPRO", "label": "Industrial production", "unit": "index"},
    {"id": "NFCI", "label": "Financial conditions (NFCI)", "unit": "index"},
    {"id": "PERMIT", "label": "Building permits", "unit": "count"},
    {"id": "DFF", "label": "Fed funds rate", "unit": "%"},
    {"id": "T10YIE", "label": "10Y inflation breakeven", "unit": "%"},
    {"id": "BAMLH0A0HYM2", "label": "High-yield spread", "unit": "%"},
]

_GLOBAL_CRYPTO_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


class MacroCycleService:
    def __init__(
        self,
        *,
        free_data: Optional[FreeDataService] = None,
        market_data: Optional[ResearchMarketDataService] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self.free_data = free_data or FreeDataService()
        self.market_data = market_data or ResearchMarketDataService()
        self.db = db or DatabaseManager.get_instance()

    def run_daily_snapshot(self) -> Dict[str, Any]:
        payload = self.build_snapshot()
        try:
            saved = self.db.save_macro_cycle_snapshot(payload)
            payload["persisted"] = {"as_of": saved.get("as_of"), "id": saved.get("id")}
        except Exception as exc:
            logger.warning("Macro cycle persist failed: %s", exc)
            payload["persisted"] = {"status": "failed", "error": str(exc)}
        return payload

    def build_snapshot(self) -> Dict[str, Any]:
        readings = self._indicator_readings()
        verdict = classify_cycle(readings)
        playbook = PHASE_PLAYBOOK.get(verdict["phase"], [])
        divergence, divergence_note = self._check_divergence(verdict["phase"], playbook)
        crypto = self._crypto_cycle()
        summary = _cycle_summary(verdict, readings, divergence, crypto)
        return {
            "as_of": date.today().isoformat(),
            "phase": verdict["phase"],
            "confidence": verdict["confidence"],
            "scores": verdict["scores"],
            "indicators": readings,
            "playbook": playbook,
            "divergence": divergence,
            "divergence_note": divergence_note,
            "crypto": crypto,
            "summary": summary,
            "generated_at": _now_iso(),
        }

    # ------------------------------------------------------------- indicators

    def _indicator_readings(self) -> List[Dict[str, Any]]:
        readings: List[Dict[str, Any]] = []
        for spec in FRED_SERIES:
            try:
                series = self.free_data.fred_series_history(
                    spec["id"], label=spec["label"], unit=spec["unit"]
                )
            except Exception as exc:
                series = {"id": spec["id"], "label": spec["label"], "status": "degraded", "detail": str(exc), "observations": []}
            readings.append(derive_reading(spec["id"], spec["label"], series))
        return readings

    # ------------------------------------------------------------- divergence

    def _check_divergence(self, phase: str, playbook: List[str]) -> Tuple[bool, str]:
        """Compare the phase playbook against the rotation engine's actual leaders."""
        if not playbook:
            return False, ""
        try:
            rotation = self.db.get_latest_sector_rotation_snapshot()
        except Exception as exc:
            logger.debug("Rotation snapshot read failed for divergence check: %s", exc)
            rotation = None
        constituents = (rotation or {}).get("constituents") or []
        ranked = [row for row in constituents if row.get("composite_rank") is not None]
        if len(ranked) < 8:
            return False, ""
        ranked.sort(key=lambda row: row["composite_rank"])
        top_quartile = {row["symbol"] for row in ranked[: max(3, len(ranked) // 4)]}
        overlap = len(top_quartile & set(playbook)) / min(len(playbook), len(top_quartile))
        if overlap >= 0.34:
            return False, (
                f"Tape leadership agrees with the {phase}-cycle playbook "
                f"({', '.join(sorted(top_quartile & set(playbook)))} leading)."
            )
        # Which phase does the tape actually look like?
        tape_phase = ""
        best_overlap = 0.0
        for candidate, symbols in PHASE_PLAYBOOK.items():
            cand_overlap = len(top_quartile & set(symbols)) / min(len(symbols), len(top_quartile))
            if cand_overlap > best_overlap:
                best_overlap = cand_overlap
                tape_phase = candidate
        note = (
            f"Cycle data says {phase}, but tape leadership does not match the {phase} playbook"
            + (f" — leaders look {tape_phase}-cycle" if tape_phase and tape_phase != phase else "")
            + ". Divergence is itself a signal: either the cycle read is early or the rally is narrow."
        )
        return True, note

    # ------------------------------------------------------------- crypto

    def _crypto_cycle(self) -> Dict[str, Any]:
        gauge: Dict[str, Any] = {"status": "ok", "inputs": 0}
        # BTC trend from cached bars
        try:
            btc = self.market_data.get_crypto_history("BTC")
            closes = [float(b["close"]) for b in btc.get("bars") or [] if b.get("close")]
            if len(closes) >= 200:
                sma50 = sum(closes[-50:]) / 50
                sma200 = sum(closes[-200:]) / 200
                gauge["btc_price"] = round(closes[-1], 2)
                gauge["btc_above_200dma"] = closes[-1] > sma200
                gauge["btc_golden_cross"] = sma50 > sma200
                gauge["inputs"] += 1
        except Exception as exc:
            logger.debug("BTC trend fetch failed: %s", exc)
        # ETH/BTC ratio trend
        try:
            eth = self.market_data.get_crypto_history("ETH")
            btc = self.market_data.get_crypto_history("BTC")
            eth_by_date = {str(b["date"])[:10]: float(b["close"]) for b in eth.get("bars") or [] if b.get("close")}
            ratio = [
                eth_by_date[str(b["date"])[:10]] / float(b["close"])
                for b in btc.get("bars") or []
                if b.get("close") and str(b["date"])[:10] in eth_by_date
            ]
            if len(ratio) > 63:
                gauge["eth_btc_21d_pct"] = round((ratio[-1] / ratio[-22] - 1) * 100, 2)
                gauge["eth_btc_63d_pct"] = round((ratio[-1] / ratio[-64] - 1) * 100, 2)
                gauge["inputs"] += 1
        except Exception as exc:
            logger.debug("ETH/BTC ratio failed: %s", exc)
        # Dominance + total mcap from CoinGecko /global (optional context)
        global_data = self._coingecko_global()
        if global_data:
            gauge["btc_dominance_pct"] = global_data.get("btc_dominance_pct")
            gauge["total_market_cap_usd"] = global_data.get("total_market_cap_usd")
            gauge["inputs"] += 1

        gauge["confidence"] = "high" if gauge["inputs"] >= 3 else "medium" if gauge["inputs"] == 2 else "low"
        above200 = gauge.get("btc_above_200dma")
        golden = gauge.get("btc_golden_cross")
        eth_trend = gauge.get("eth_btc_63d_pct")
        if above200 is False and golden is False:
            label = "bear"
            plain = "BTC below its 200DMA with a death cross — crypto cycle defensive."
        elif above200 and eth_trend is not None and eth_trend > 3:
            label = "alt_season_risk_on"
            plain = "BTC in an uptrend and ETH/BTC strengthening — risk appetite broadening into alts."
        elif above200:
            label = "btc_led_bull"
            plain = "BTC in an uptrend but leadership concentrated in BTC."
        else:
            label = "mixed"
            plain = "Crypto cycle signals are mixed or incomplete."
        gauge["gauge"] = label
        gauge["plain_english"] = plain
        return gauge

    def _coingecko_global(self) -> Optional[Dict[str, Any]]:
        ttl = _env_int("CYCLE_CRYPTO_GLOBAL_TTL_SECONDS", 3600)
        cached = _GLOBAL_CRYPTO_CACHE.get("global")
        if cached and time.monotonic() - cached[0] < ttl:
            return cached[1]
        if _coingecko_cooling_down():
            return None
        try:
            response = requests.get("https://api.coingecko.com/api/v3/global", timeout=12)
            if response.status_code == 429:
                _coingecko_mark_rate_limited(response.headers.get("Retry-After"))
                return None
            if response.status_code != 200:
                return None
            data = (response.json() or {}).get("data") or {}
            dominance = (data.get("market_cap_percentage") or {}).get("btc")
            total_mcap = (data.get("total_market_cap") or {}).get("usd")
            payload = {
                "btc_dominance_pct": round(float(dominance), 2) if dominance is not None else None,
                "total_market_cap_usd": float(total_mcap) if total_mcap is not None else None,
            }
            _GLOBAL_CRYPTO_CACHE["global"] = (time.monotonic(), payload)
            return payload
        except Exception as exc:
            logger.debug("CoinGecko /global failed: %s", exc)
            return None


# ----------------------------------------------------------------- pure logic


def derive_reading(series_id: str, label: str, series: Dict[str, Any]) -> Dict[str, Any]:
    """Turn raw observations into a directional reading + phase votes."""
    observations = series.get("observations") or []
    values = [obs["value"] for obs in observations]
    base = {
        "id": series_id,
        "label": label,
        "status": series.get("status") or "missing",
        "value": values[-1] if values else None,
        "as_of": observations[-1]["date"] if observations else None,
        "trend": None,
        "detail": series.get("detail"),
        "votes": {},
    }
    if not values:
        base["status"] = base["status"] if base["status"] != "ok" else "missing"
        return base

    if series_id == "T10Y2Y":
        level = values[-1]
        slope_63 = values[-1] - values[-64] if len(values) > 63 else None
        base["trend"] = f"{'+' if (slope_63 or 0) >= 0 else ''}{slope_63:.2f} over ~3m" if slope_63 is not None else None
        if level < -0.1:
            base["votes"] = {"late": 1.0}
            base["plain"] = "Curve inverted — classic late-cycle."
        elif level < 0.5 and slope_63 is not None and slope_63 > 0.2:
            # Re-steepening out of inversion historically precedes turns: the
            # bull case (cuts reviving the cycle) and the recession unwind both
            # start here, so split the vote.
            base["votes"] = {"early": 0.6, "contraction": 0.4}
            base["plain"] = "Curve re-steepening from inversion — transition zone."
        elif level >= 0.5:
            base["votes"] = {"early": 0.6, "mid": 0.4}
            base["plain"] = "Curve comfortably positive."
        else:
            base["votes"] = {"late": 0.5, "mid": 0.5}
            base["plain"] = "Curve flat."
    elif series_id == "ICSA":
        if len(values) >= 17:
            recent = sum(values[-4:]) / 4
            prior = sum(values[-17:-13]) / 4
            change = (recent - prior) / prior * 100 if prior else 0.0
            base["trend"] = f"4wk avg {change:+.1f}% vs 13w ago"
            if change >= 15:
                base["votes"] = {"contraction": 1.0}
                base["plain"] = "Claims surging — labor market cracking."
            elif change >= 5:
                base["votes"] = {"late": 1.0}
                base["plain"] = "Claims drifting up."
            elif change <= -5:
                base["votes"] = {"early": 1.0}
                base["plain"] = "Claims falling — labor improving."
            else:
                base["votes"] = {"mid": 1.0}
                base["plain"] = "Claims stable."
    elif series_id == "UNRATE":
        if len(values) >= 15:
            avg3 = sum(values[-3:]) / 3
            prior_lows = [sum(values[i - 3:i]) / 3 for i in range(len(values) - 12, len(values))]
            sahm = avg3 - min(prior_lows) if prior_lows else 0.0
            base["trend"] = f"Sahm gap {sahm:+.2f}"
            base["sahm"] = round(sahm, 2)
            if sahm >= 0.5:
                base["votes"] = {"contraction": 2.0}  # weighted: hard signal
                base["plain"] = "Sahm rule triggered — recession-level deterioration."
            elif sahm >= 0.2:
                base["votes"] = {"late": 1.0}
                base["plain"] = "Unemployment off its lows."
            else:
                base["votes"] = {"mid": 0.5, "early": 0.5}
                base["plain"] = "Unemployment near cycle lows."
    elif series_id == "INDPRO":
        if len(values) >= 13:
            yoy = (values[-1] / values[-13] - 1) * 100
            base["trend"] = f"{yoy:+.1f}% YoY"
            if yoy < -1:
                base["votes"] = {"contraction": 1.0}
                base["plain"] = "Industrial production contracting."
            elif yoy < 0.5:
                base["votes"] = {"late": 0.6, "early": 0.4}
                base["plain"] = "Output flat — turning point zone."
            elif yoy > 2.5:
                base["votes"] = {"early": 0.6, "mid": 0.4}
                base["plain"] = "Output accelerating."
            else:
                base["votes"] = {"mid": 1.0}
                base["plain"] = "Output growing steadily."
    elif series_id == "NFCI":
        level = values[-1]
        change_13w = values[-1] - values[-14] if len(values) > 13 else None
        base["trend"] = f"{change_13w:+.2f} over 13w" if change_13w is not None else None
        if level > 0:
            base["votes"] = {"contraction": 0.7, "late": 0.3}
            base["plain"] = "Financial conditions tight."
        elif change_13w is not None and change_13w > 0.1:
            base["votes"] = {"late": 1.0}
            base["plain"] = "Conditions tightening from loose."
        else:
            base["votes"] = {"mid": 0.5, "early": 0.5}
            base["plain"] = "Financial conditions loose."
    elif series_id == "PERMIT":
        if len(values) >= 13:
            yoy = (values[-1] / values[-13] - 1) * 100
            base["trend"] = f"{yoy:+.1f}% YoY"
            if yoy > 5:
                base["votes"] = {"early": 1.0}
                base["plain"] = "Housing permits expanding — early-cycle behavior."
            elif yoy < -10:
                base["votes"] = {"late": 0.6, "contraction": 0.4}
                base["plain"] = "Permits rolling over hard."
            elif yoy < 0:
                base["votes"] = {"late": 1.0}
                base["plain"] = "Permits shrinking."
            else:
                base["votes"] = {"mid": 1.0}
                base["plain"] = "Permits steady."
    elif series_id == "DFF":
        change_3m = values[-1] - values[-91] if len(values) > 90 else None
        if change_3m is not None:
            stance = "hiking" if change_3m > 0.10 else "cutting" if change_3m < -0.10 else "holding"
            base["trend"] = f"{stance} ({change_3m:+.2f} over 3m)"
            base["stance"] = stance
            if stance == "cutting":
                base["votes"] = {"early": 0.6, "contraction": 0.4}
                base["plain"] = "Fed cutting — either easing into recovery or responding to weakness."
            elif stance == "hiking":
                base["votes"] = {"mid": 0.5, "late": 0.5}
                base["plain"] = "Fed hiking."
            else:
                base["votes"] = {"mid": 0.4, "late": 0.6}
                base["plain"] = "Fed on hold."
    elif series_id == "T10YIE":
        change_63 = values[-1] - values[-64] if len(values) > 63 else None
        base["trend"] = f"{change_63:+.2f} over ~3m" if change_63 is not None else None
        if change_63 is not None:
            if change_63 > 0.15:
                base["votes"] = {"mid": 0.5, "late": 0.5}
                base["plain"] = "Inflation expectations rising."
            elif change_63 < -0.15:
                base["votes"] = {"contraction": 0.5, "early": 0.5}
                base["plain"] = "Inflation expectations falling — demand cooling."
            else:
                base["votes"] = {"mid": 1.0}
                base["plain"] = "Inflation expectations anchored."
    elif series_id == "BAMLH0A0HYM2":
        level = values[-1]
        change_63 = values[-1] - values[-64] if len(values) > 63 else None
        base["trend"] = f"{change_63:+.2f} over ~3m" if change_63 is not None else None
        if change_63 is not None and change_63 > 0.75:
            base["votes"] = {"contraction": 1.0}
            base["plain"] = "Credit spreads widening fast — stress building."
        elif level < 3.5:
            base["votes"] = {"mid": 0.7, "late": 0.3}
            base["plain"] = "Credit spreads tight — markets unworried."
        elif level > 5.5:
            base["votes"] = {"contraction": 0.7, "late": 0.3}
            base["plain"] = "Credit spreads elevated."
        else:
            base["votes"] = {"late": 1.0}
            base["plain"] = "Credit spreads middling."
    return base


def classify_cycle(readings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Vote aggregation with a Sahm hard override and availability-based confidence."""
    scores = {phase: 0.0 for phase in PHASES}
    available = 0
    sahm_triggered = False
    for reading in readings:
        votes = reading.get("votes") or {}
        if votes:
            available += 1
        for phase, weight in votes.items():
            if phase in scores:
                scores[phase] += weight
        if reading.get("id") == "UNRATE" and (reading.get("sahm") or 0) >= 0.5:
            sahm_triggered = True

    if sahm_triggered:
        phase = "contraction"
    elif available == 0:
        phase = "unknown"
    else:
        phase = max(scores, key=lambda key: scores[key])

    ordered = sorted(scores.values(), reverse=True)
    margin = (ordered[0] - ordered[1]) if len(ordered) > 1 else 0.0
    if available >= 7 and margin >= 1.0:
        confidence = "high"
    elif available >= 5 and margin >= 0.5:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "phase": phase,
        "confidence": confidence,
        "scores": {key: round(value, 2) for key, value in scores.items()},
        "available_indicators": available,
        "sahm_triggered": sahm_triggered,
    }


def _cycle_summary(
    verdict: Dict[str, Any],
    readings: List[Dict[str, Any]],
    divergence: bool,
    crypto: Dict[str, Any],
) -> str:
    highlights = [
        f"{reading['label']}: {reading.get('plain')}"
        for reading in readings
        if reading.get("plain")
    ][:3]
    parts = [
        f"Cycle phase: {verdict['phase']} (confidence {verdict['confidence']}, "
        f"{verdict['available_indicators']}/9 indicators).",
        *highlights,
    ]
    if divergence:
        parts.append("⚠️ Tape leadership diverges from the phase playbook.")
    if crypto.get("gauge"):
        parts.append(f"Crypto: {crypto['gauge']}.")
    return " ".join(parts)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
