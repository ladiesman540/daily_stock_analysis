# -*- coding: utf-8 -*-
"""Positioning, options gamma, and crowding analysis for research workflows."""

from __future__ import annotations

import csv
import io
import math
import os
import zipfile
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple

import requests


CONTRACT_MULTIPLIER = 100
SECONDS_PER_YEAR = 365.0 * 24.0 * 60.0 * 60.0


@dataclass
class OptionContract:
    symbol: str
    expiration: str
    strike: float
    option_type: str
    open_interest: float = 0.0
    volume: float = 0.0
    gamma: Optional[float] = None
    delta: Optional[float] = None
    implied_volatility: Optional[float] = None
    provider: str = ""
    contract_symbol: Optional[str] = None


class OptionsProvider(Protocol):
    provider_name: str

    def fetch_chain(self, symbol: str) -> Dict[str, Any]:
        ...


class CompositeOptionsProvider:
    """Try configured options-chain providers in priority order."""

    provider_name = "composite"

    def __init__(self, providers: Optional[List[OptionsProvider]] = None):
        self.providers = providers or _default_options_providers()

    def fetch_chain(self, symbol: str) -> Dict[str, Any]:
        diagnostics: List[str] = []
        for provider in self.providers:
            try:
                payload = provider.fetch_chain(symbol)
            except Exception as exc:
                diagnostics.append(f"{provider.provider_name} failed: {exc}")
                continue
            diagnostics.extend(payload.get("diagnostics") or [])
            contracts = payload.get("contracts") or []
            if contracts:
                payload["diagnostics"] = diagnostics
                return payload
        return {
            "provider": "none",
            "contracts": [],
            "underlying_price": None,
            "diagnostics": diagnostics or ["No options chain provider returned contracts."],
        }


class PolygonOptionsProvider:
    """Massive/Polygon current option-chain snapshot provider."""

    provider_name = "massive"

    def fetch_chain(self, symbol: str) -> Dict[str, Any]:
        api_key = os.getenv("MASSIVE_API_KEY", "").strip() or os.getenv("POLYGON_API_KEY", "").strip()
        if not api_key:
            return {"contracts": [], "diagnostics": ["Massive/Polygon skipped: MASSIVE_API_KEY/POLYGON_API_KEY is not configured."]}

        base_url = os.getenv("MASSIVE_API_BASE_URL", "https://api.massive.com").rstrip("/")
        url = f"{base_url}/v3/snapshot/options/{symbol.upper()}"
        params = {
            "apiKey": api_key,
            "limit": 250,
            "sort": "expiration_date",
            "order": "asc",
        }
        contracts: List[OptionContract] = []
        underlying_price = None
        pages = 0
        next_url: Optional[str] = url
        next_params: Optional[Dict[str, Any]] = params
        while next_url and pages < 4:
            pages += 1
            response = requests.get(next_url, params=next_params, timeout=20)
            if response.status_code == 401:
                return {"contracts": [], "diagnostics": ["Massive/Polygon rejected the API key."]}
            if response.status_code == 403:
                return {"contracts": [], "diagnostics": ["Massive/Polygon plan does not include this options endpoint."]}
            if response.status_code == 429:
                return {"contracts": [], "diagnostics": ["Massive/Polygon rate limit reached."]}
            response.raise_for_status()
            payload = response.json()
            for row in payload.get("results") or []:
                details = row.get("details") or {}
                greeks = row.get("greeks") or {}
                day = row.get("day") or {}
                underlying = row.get("underlying_asset") or {}
                underlying_price = _first_number(underlying_price, underlying.get("price"))
                contract = _contract_from_parts(
                    symbol=symbol,
                    expiration=details.get("expiration_date"),
                    strike=details.get("strike_price"),
                    option_type=details.get("contract_type"),
                    open_interest=row.get("open_interest"),
                    volume=day.get("volume"),
                    gamma=greeks.get("gamma"),
                    delta=greeks.get("delta"),
                    implied_volatility=row.get("implied_volatility"),
                    provider=self.provider_name,
                    contract_symbol=details.get("ticker"),
                )
                if contract:
                    contracts.append(contract)
            next_url = payload.get("next_url")
            if next_url and "apiKey=" not in next_url:
                next_url = f"{next_url}{'&' if '?' in next_url else '?'}apiKey={api_key}"
            next_params = None

        return {
            "provider": self.provider_name,
            "contracts": contracts,
            "underlying_price": _float_or_none(underlying_price),
            "diagnostics": [],
        }


class TradierOptionsProvider:
    """Tradier option-chain provider with Greeks when enabled on the account."""

    provider_name = "tradier"

    def fetch_chain(self, symbol: str) -> Dict[str, Any]:
        token = os.getenv("TRADIER_ACCESS_TOKEN", "").strip() or os.getenv("TRADIER_BEARER_TOKEN", "").strip()
        if not token:
            return {"contracts": [], "diagnostics": ["Tradier skipped: TRADIER_ACCESS_TOKEN is not configured."]}

        base_url = os.getenv("TRADIER_BASE_URL", "https://api.tradier.com/v1").rstrip("/")
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        quote_payload = requests.get(
            f"{base_url}/markets/quotes",
            headers=headers,
            params={"symbols": symbol.upper()},
            timeout=15,
        )
        quote_payload.raise_for_status()
        quote = (quote_payload.json().get("quotes") or {}).get("quote") or {}
        if isinstance(quote, list):
            quote = quote[0] if quote else {}
        underlying_price = _first_number(quote.get("last"), quote.get("close"), quote.get("bid"))

        expirations_response = requests.get(
            f"{base_url}/markets/options/expirations",
            headers=headers,
            params={"symbol": symbol.upper(), "includeAllRoots": "true", "strikes": "false"},
            timeout=15,
        )
        expirations_response.raise_for_status()
        dates = ((expirations_response.json().get("expirations") or {}).get("date") or [])
        if isinstance(dates, str):
            dates = [dates]

        contracts: List[OptionContract] = []
        diagnostics: List[str] = []
        for expiration in dates[:4]:
            chain_response = requests.get(
                f"{base_url}/markets/options/chains",
                headers=headers,
                params={"symbol": symbol.upper(), "expiration": expiration, "greeks": "true"},
                timeout=20,
            )
            if chain_response.status_code == 429:
                diagnostics.append("Tradier rate limit reached before all expirations were loaded.")
                break
            chain_response.raise_for_status()
            options = ((chain_response.json().get("options") or {}).get("option") or [])
            if isinstance(options, dict):
                options = [options]
            for row in options:
                greeks = row.get("greeks") or {}
                contract = _contract_from_parts(
                    symbol=symbol,
                    expiration=row.get("expiration_date") or expiration,
                    strike=row.get("strike"),
                    option_type=row.get("option_type"),
                    open_interest=row.get("open_interest"),
                    volume=row.get("volume"),
                    gamma=greeks.get("gamma"),
                    delta=greeks.get("delta"),
                    implied_volatility=_first_number(greeks.get("mid_iv"), greeks.get("smv_vol"), row.get("iv")),
                    provider=self.provider_name,
                    contract_symbol=row.get("symbol"),
                )
                if contract:
                    contracts.append(contract)

        return {
            "provider": self.provider_name,
            "contracts": contracts,
            "underlying_price": _float_or_none(underlying_price),
            "diagnostics": diagnostics,
        }


class YFinanceOptionsProvider:
    """Free delayed options fallback using yfinance."""

    provider_name = "yfinance"

    def fetch_chain(self, symbol: str) -> Dict[str, Any]:
        if os.getenv("POSITIONING_USE_YFINANCE_OPTIONS", "true").strip().lower() in {"0", "false", "no"}:
            return {"contracts": [], "diagnostics": ["yfinance options fallback is disabled."]}
        try:
            import yfinance as yf
        except ModuleNotFoundError:
            return {"contracts": [], "diagnostics": ["yfinance is not installed."]}

        ticker = yf.Ticker(symbol.upper())
        underlying_price = self._latest_price(ticker)
        expirations = list(getattr(ticker, "options", []) or [])
        contracts: List[OptionContract] = []
        diagnostics = ["Using delayed Yahoo/yfinance options data; Greeks are estimated from IV."]
        for expiration in expirations[:4]:
            try:
                chain = ticker.option_chain(expiration)
            except Exception as exc:
                diagnostics.append(f"yfinance skipped {expiration}: {exc}")
                continue
            for option_type, frame in (("call", chain.calls), ("put", chain.puts)):
                if frame is None:
                    continue
                for _, row in frame.iterrows():
                    iv = _float_or_none(row.get("impliedVolatility"))
                    strike = _float_or_none(row.get("strike"))
                    gamma = None
                    if underlying_price and strike and iv:
                        gamma = _black_scholes_gamma(underlying_price, strike, iv, expiration)
                    contract = _contract_from_parts(
                        symbol=symbol,
                        expiration=expiration,
                        strike=strike,
                        option_type=option_type,
                        open_interest=row.get("openInterest"),
                        volume=row.get("volume"),
                        gamma=gamma,
                        delta=None,
                        implied_volatility=iv,
                        provider=self.provider_name,
                        contract_symbol=row.get("contractSymbol"),
                    )
                    if contract:
                        contracts.append(contract)

        return {
            "provider": self.provider_name,
            "contracts": contracts,
            "underlying_price": _float_or_none(underlying_price),
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _latest_price(ticker: Any) -> Optional[float]:
        try:
            info = ticker.fast_info or {}
            price = _first_number(
                _get_attr_or_item(info, "last_price"),
                _get_attr_or_item(info, "lastPrice"),
                _get_attr_or_item(info, "regular_market_price"),
            )
            if price:
                return _float_or_none(price)
        except Exception:
            pass
        try:
            history = ticker.history(period="5d", interval="1d", auto_adjust=False)
            if history is not None and not history.empty:
                return _float_or_none(history["Close"].dropna().iloc[-1])
        except Exception:
            return None
        return None


class ShortPressureProvider:
    """Short-interest and FTD pressure from low-cost public sources."""

    provider_name = "short_pressure"

    def fetch(self, symbol: str) -> Dict[str, Any]:
        diagnostics: List[str] = []
        yfinance_data = self._fetch_yfinance_short_interest(symbol, diagnostics)
        ftd_data = self._fetch_sec_ftd(symbol, diagnostics)
        return {
            "provider": self.provider_name,
            "short_interest": yfinance_data,
            "fails_to_deliver": ftd_data,
            "diagnostics": diagnostics,
        }

    def _fetch_yfinance_short_interest(self, symbol: str, diagnostics: List[str]) -> Dict[str, Any]:
        try:
            import yfinance as yf
        except ModuleNotFoundError:
            diagnostics.append("Short interest skipped: yfinance is not installed.")
            return {}
        try:
            info = yf.Ticker(symbol.upper()).info or {}
        except Exception as exc:
            diagnostics.append(f"Short interest fetch failed: {exc}")
            return {}

        report_ts = _float_or_none(info.get("dateShortInterest"))
        report_date = None
        if report_ts:
            try:
                report_date = datetime.utcfromtimestamp(report_ts).date().isoformat()
            except Exception:
                report_date = None
        return {
            "source": "Yahoo Finance via yfinance",
            "short_percent_float": _percent_value(info.get("shortPercentOfFloat")),
            "short_ratio_days_to_cover": _float_or_none(info.get("shortRatio")),
            "shares_short": _float_or_none(info.get("sharesShort")),
            "shares_short_prior_month": _float_or_none(info.get("sharesShortPriorMonth")),
            "report_date": report_date,
        }

    def _fetch_sec_ftd(self, symbol: str, diagnostics: List[str]) -> Dict[str, Any]:
        if os.getenv("POSITIONING_SEC_FTD_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
            diagnostics.append("SEC FTD skipped: POSITIONING_SEC_FTD_ENABLED is false.")
            return {}
        headers = {"User-Agent": os.getenv("SEC_USER_AGENT", "daily-stock-analysis/1.0 contact@example.com")}
        for year_month, half in _recent_sec_ftd_files():
            url = f"https://www.sec.gov/files/data/fails-deliver-data/cnsfails{year_month}{half}.zip"
            try:
                response = requests.get(url, headers=headers, timeout=15)
            except Exception as exc:
                diagnostics.append(f"SEC FTD fetch failed for {year_month}{half}: {exc}")
                continue
            if response.status_code == 404:
                continue
            if response.status_code in {403, 429}:
                diagnostics.append(f"SEC FTD blocked or rate limited with status {response.status_code}.")
                return {}
            if response.status_code != 200:
                diagnostics.append(f"SEC FTD returned status {response.status_code} for {year_month}{half}.")
                continue
            try:
                parsed = _parse_sec_ftd_zip(response.content, symbol)
            except Exception as exc:
                diagnostics.append(f"SEC FTD parse failed for {year_month}{half}: {exc}")
                continue
            if parsed:
                parsed["source"] = "SEC fails-to-deliver"
                parsed["file"] = f"cnsfails{year_month}{half}.zip"
                return parsed
        diagnostics.append("SEC FTD data unavailable for the recent files checked.")
        return {}


class CFTCCotProvider:
    """CFTC COT macro context via the public reporting API."""

    provider_name = "cftc_cot"

    def fetch(self, symbol: str, asset_type: str = "stock") -> Dict[str, Any]:
        if os.getenv("POSITIONING_CFTC_ENABLED", "true").strip().lower() in {"0", "false", "no"}:
            return {"diagnostics": ["CFTC COT skipped: POSITIONING_CFTC_ENABLED is false."]}
        keywords = _cot_keywords(symbol, asset_type)
        if not keywords:
            return {
                "diagnostics": [
                    "CFTC COT is macro futures context only; no useful contract mapping for this symbol."
                ]
            }
        try:
            response = requests.get(
                "https://publicreporting.cftc.gov/resource/srt6-5q2f.json",
                params={"$limit": 5000, "$order": "report_date_as_yyyy_mm_dd DESC"},
                timeout=20,
            )
            if response.status_code in {403, 429}:
                return {"diagnostics": [f"CFTC public reporting returned status {response.status_code}."]}
            response.raise_for_status()
            rows = response.json() or []
        except Exception as exc:
            return {"diagnostics": [f"CFTC COT fetch failed: {exc}"]}

        match = _find_cot_record(rows, keywords)
        if not match:
            return {"diagnostics": [f"CFTC COT had no match for: {', '.join(keywords)}."]}
        open_interest = _number_from_keys(match, ["open_interest_all", "open_interest"])
        noncomm_long = _number_from_keys(
            match,
            ["noncommercial_positions_long_all", "noncomm_positions_long_all", "noncommercial_long_all"],
        )
        noncomm_short = _number_from_keys(
            match,
            ["noncommercial_positions_short_all", "noncomm_positions_short_all", "noncommercial_short_all"],
        )
        net = None
        net_oi_pct = None
        if noncomm_long is not None and noncomm_short is not None:
            net = noncomm_long - noncomm_short
            if open_interest:
                net_oi_pct = net / open_interest * 100
        return {
            "source": "CFTC public reporting",
            "market": match.get("market_and_exchange_names") or match.get("market_and_exchange_name"),
            "report_date": match.get("report_date_as_yyyy_mm_dd"),
            "open_interest": open_interest,
            "noncommercial_long": noncomm_long,
            "noncommercial_short": noncomm_short,
            "noncommercial_net": net,
            "noncommercial_net_oi_pct": net_oi_pct,
            "diagnostics": [],
        }


class PositioningService:
    """Compute a decision-support positioning dashboard for one symbol."""

    def __init__(
        self,
        *,
        options_provider: Optional[OptionsProvider] = None,
        short_provider: Optional[ShortPressureProvider] = None,
        cot_provider: Optional[CFTCCotProvider] = None,
    ):
        self.options_provider = options_provider or CompositeOptionsProvider()
        self.short_provider = short_provider or ShortPressureProvider()
        self.cot_provider = cot_provider or CFTCCotProvider()

    def analyze(self, symbol: str, *, asset_type: str = "stock") -> Dict[str, Any]:
        normalized = _normalize_symbol(symbol)
        if not normalized:
            raise ValueError("symbol is required")
        asset_type = (asset_type or "stock").strip().lower()
        if asset_type not in {"stock", "crypto"}:
            raise ValueError("asset_type must be stock or crypto")

        diagnostics: List[str] = []
        sources: List[Dict[str, Any]] = []
        chain_payload = {"contracts": [], "underlying_price": None, "provider": "none", "diagnostics": []}
        if asset_type == "stock":
            chain_payload = self.options_provider.fetch_chain(normalized)
            diagnostics.extend(chain_payload.get("diagnostics") or [])
            if chain_payload.get("provider") != "none":
                sources.append(
                    {
                        "name": str(chain_payload.get("provider") or "options").title(),
                        "category": "options_chain",
                        "status": "ok" if chain_payload.get("contracts") else "missing",
                    }
                )
        else:
            diagnostics.append("Equity option gamma is not available for spot crypto symbols in this MVP.")

        contracts = list(chain_payload.get("contracts") or [])
        underlying_price = _float_or_none(chain_payload.get("underlying_price"))
        gamma = _build_gamma_summary(contracts, underlying_price)
        if gamma["contract_count"] == 0:
            diagnostics.append("Gamma map unavailable: no option contracts were returned.")
        elif gamma["contracts_with_gamma"] == 0:
            diagnostics.append("Gamma map incomplete: contracts did not include Greeks or enough IV to estimate gamma.")

        short_payload = self.short_provider.fetch(normalized) if asset_type == "stock" else {
            "short_interest": {},
            "fails_to_deliver": {},
            "diagnostics": ["Short-interest and SEC FTD are equity-only in this MVP."],
        }
        diagnostics.extend(short_payload.get("diagnostics") or [])
        sources.append(
            {
                "name": "Short interest / SEC FTD",
                "category": "short_pressure",
                "status": "ok" if _has_meaningful_values(
                    short_payload.get("short_interest"),
                    short_payload.get("fails_to_deliver"),
                ) else "missing",
            }
        )

        cot_payload = self.cot_provider.fetch(normalized, asset_type=asset_type)
        diagnostics.extend(cot_payload.get("diagnostics") or [])
        sources.append(
            {
                "name": "CFTC COT",
                "category": "macro_positioning",
                "status": "ok" if cot_payload.get("market") else "missing",
            }
        )

        crowding = _build_crowding_summary(gamma, short_payload, cot_payload)
        confidence = _confidence_score(gamma, short_payload, cot_payload)
        bias = _positioning_bias(gamma, crowding, short_payload, cot_payload)
        what_to_watch = _what_to_watch(gamma, crowding, short_payload, cot_payload)

        return {
            "symbol": normalized,
            "asset_type": asset_type,
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "underlying_price": underlying_price,
            "positioning_bias": bias,
            "confidence": confidence,
            "gamma": gamma,
            "crowding": crowding,
            "short_pressure": {
                "short_interest": short_payload.get("short_interest") or {},
                "fails_to_deliver": short_payload.get("fails_to_deliver") or {},
            },
            "cot_macro_context": {k: v for k, v in cot_payload.items() if k != "diagnostics"},
            "sources": sources,
            "data_gaps": _dedupe(diagnostics),
            "what_to_watch": what_to_watch,
            "methodology": [
                "Gamma exposure is OI-implied. Calls are treated as positive gamma and puts as negative gamma; this is not a direct dealer-position feed.",
                "True intraday dealer flow requires trade direction plus quote context at execution time.",
                "FINRA daily short-sale volume is not the same thing as short interest; this MVP separates short interest, FTD, and options crowding.",
            ],
        }


def _build_gamma_summary(contracts: List[OptionContract], underlying_price: Optional[float]) -> Dict[str, Any]:
    calls = [item for item in contracts if item.option_type == "call"]
    puts = [item for item in contracts if item.option_type == "put"]
    total_call_oi = sum(max(item.open_interest, 0.0) for item in calls)
    total_put_oi = sum(max(item.open_interest, 0.0) for item in puts)
    total_call_volume = sum(max(item.volume, 0.0) for item in calls)
    total_put_volume = sum(max(item.volume, 0.0) for item in puts)
    rows: List[Dict[str, Any]] = []
    contracts_with_gamma = 0
    if underlying_price:
        for item in contracts:
            gamma = item.gamma
            if gamma is None and item.implied_volatility:
                gamma = _black_scholes_gamma(underlying_price, item.strike, item.implied_volatility, item.expiration)
            if gamma is None or not item.open_interest:
                continue
            contracts_with_gamma += 1
            sign = 1 if item.option_type == "call" else -1
            dollar_gamma_1pct = sign * gamma * item.open_interest * CONTRACT_MULTIPLIER * (underlying_price ** 2) * 0.01
            rows.append({**asdict(item), "signed_dollar_gamma_1pct": dollar_gamma_1pct})

    net_gex = sum(row["signed_dollar_gamma_1pct"] for row in rows)
    call_wall = _wall(rows, "call", positive=True)
    put_wall = _wall(rows, "put", positive=False)
    max_pain = _max_pain(contracts)
    gamma_flip = _gamma_flip(contracts, underlying_price)
    unusual = _unusual_options_activity(contracts)

    return {
        "provider_contracts": sorted({item.provider for item in contracts if item.provider}),
        "contract_count": len(contracts),
        "contracts_with_gamma": contracts_with_gamma,
        "underlying_price": underlying_price,
        "net_dollar_gamma_1pct": _round_or_none(net_gex, 2),
        "gamma_regime": "positive_gamma" if net_gex > 0 else "negative_gamma" if net_gex < 0 else "unknown",
        "gamma_flip_level": _round_or_none(gamma_flip, 2),
        "call_wall": call_wall,
        "put_wall": put_wall,
        "max_pain": _round_or_none(max_pain, 2),
        "put_call_oi_ratio": _ratio(total_put_oi, total_call_oi),
        "put_call_volume_ratio": _ratio(total_put_volume, total_call_volume),
        "total_call_open_interest": _round_or_none(total_call_oi, 0),
        "total_put_open_interest": _round_or_none(total_put_oi, 0),
        "unusual_activity": unusual,
        "sign_convention": "OI-implied: call gamma positive, put gamma negative; dealer sign is not directly observed.",
    }


def _build_crowding_summary(
    gamma: Dict[str, Any],
    short_payload: Dict[str, Any],
    cot_payload: Dict[str, Any],
) -> Dict[str, Any]:
    flags: List[Dict[str, Any]] = []
    score = 25.0
    put_call_oi = _float_or_none(gamma.get("put_call_oi_ratio"))
    if put_call_oi is not None:
        if put_call_oi < 0.55:
            score += 20
            flags.append({"severity": "warning", "label": "Call-side crowding", "detail": "Put/call open-interest ratio is low."})
        elif put_call_oi > 1.5:
            score += 15
            flags.append({"severity": "info", "label": "Heavy put positioning", "detail": "Put/call open-interest ratio is high."})
    if gamma.get("gamma_regime") == "negative_gamma":
        score += 15
        flags.append({"severity": "warning", "label": "Negative gamma regime", "detail": "Moves can extend faster below/above key levels."})
    unusual = gamma.get("unusual_activity") or []
    if unusual:
        score += min(20, 4 * len(unusual))
        flags.append({"severity": "info", "label": "Unusual option volume", "detail": f"{len(unusual)} contracts have volume elevated versus open interest."})

    short_interest = (short_payload.get("short_interest") or {})
    short_float = _float_or_none(short_interest.get("short_percent_float"))
    if short_float is not None:
        if short_float >= 15:
            score += 20
            flags.append({"severity": "warning", "label": "High short interest", "detail": f"{short_float:.1f}% of float reported short."})
        elif short_float >= 8:
            score += 10
            flags.append({"severity": "info", "label": "Moderate short interest", "detail": f"{short_float:.1f}% of float reported short."})

    ftd = (short_payload.get("fails_to_deliver") or {})
    ftd_notional = _float_or_none(ftd.get("latest_notional"))
    if ftd_notional and ftd_notional >= 25_000_000:
        score += 15
        flags.append({"severity": "warning", "label": "FTD pressure", "detail": "Recent SEC fails-to-deliver notional is elevated."})

    cot_net = _float_or_none(cot_payload.get("noncommercial_net_oi_pct"))
    if cot_net is not None and abs(cot_net) >= 25:
        score += 8
        flags.append({"severity": "info", "label": "Macro futures positioning stretched", "detail": f"Non-commercial net is {cot_net:.1f}% of open interest."})

    score = max(0.0, min(100.0, score))
    return {
        "crowding_risk_score": round(score, 1),
        "risk_level": "high" if score >= 70 else "medium" if score >= 45 else "low",
        "flags": flags,
    }


def _confidence_score(gamma: Dict[str, Any], short_payload: Dict[str, Any], cot_payload: Dict[str, Any]) -> Dict[str, Any]:
    score = 0
    providers = {str(item).lower() for item in (gamma.get("provider_contracts") or [])}
    if gamma.get("contracts_with_gamma"):
        if providers & {"massive", "polygon", "tradier"}:
            score += 45
        elif providers == {"yfinance"} or "yfinance" in providers:
            score += 25
        else:
            score += 30
    elif gamma.get("contract_count"):
        score += 15
    short_interest = short_payload.get("short_interest") or {}
    if short_interest:
        source = str(short_interest.get("source") or "").lower()
        score += 12 if "yfinance" in source or "yahoo" in source else 20
    if short_payload.get("fails_to_deliver"):
        score += 15
    if cot_payload.get("market"):
        score += 10
    score = max(0, min(100, score))
    return {
        "score": score,
        "label": "high" if score >= 75 else "medium" if score >= 45 else "low",
    }


def _positioning_bias(
    gamma: Dict[str, Any],
    crowding: Dict[str, Any],
    short_payload: Dict[str, Any],
    cot_payload: Dict[str, Any],
) -> str:
    regime = gamma.get("gamma_regime")
    put_call = _float_or_none(gamma.get("put_call_oi_ratio"))
    short_float = _float_or_none((short_payload.get("short_interest") or {}).get("short_percent_float"))
    cot_net = _float_or_none(cot_payload.get("noncommercial_net_oi_pct"))
    risk_level = crowding.get("risk_level")

    if short_float and short_float >= 15 and put_call is not None and put_call < 0.8:
        return "squeeze-prone but crowded"
    if regime == "negative_gamma" and risk_level in {"medium", "high"}:
        return "trend-prone / unstable"
    if regime == "positive_gamma" and put_call is not None and 0.6 <= put_call <= 1.4:
        return "balanced positive-gamma"
    if put_call is not None and put_call < 0.55:
        return "bullish but call-crowded"
    if cot_net is not None and cot_net < -20:
        return "macro headwind"
    return "neutral / data-dependent"


def _what_to_watch(
    gamma: Dict[str, Any],
    crowding: Dict[str, Any],
    short_payload: Dict[str, Any],
    cot_payload: Dict[str, Any],
) -> List[str]:
    items: List[str] = []
    flip = gamma.get("gamma_flip_level")
    call_wall = gamma.get("call_wall") or {}
    put_wall = gamma.get("put_wall") or {}
    max_pain = gamma.get("max_pain")
    if flip:
        items.append(f"Watch the gamma flip near {flip}: above it, moves may dampen; below it, momentum can expand.")
    if call_wall.get("strike"):
        items.append(f"Watch the call wall near {call_wall['strike']}: it can act like resistance until dealers are forced to re-hedge.")
    if put_wall.get("strike"):
        items.append(f"Watch the put wall near {put_wall['strike']}: losing it can make downside hedging more reflexive.")
    if max_pain:
        items.append(f"Into expiration, compare spot versus max pain near {max_pain}; pin risk rises when price stalls near large OI strikes.")
    for flag in crowding.get("flags") or []:
        label = flag.get("label")
        detail = flag.get("detail")
        if label and detail:
            items.append(f"{label}: {detail}")
    short_float = _float_or_none((short_payload.get("short_interest") or {}).get("short_percent_float"))
    if short_float and short_float >= 8:
        items.append("If price breaks higher while short interest is elevated, watch for squeeze follow-through; if volume fades, treat it as crowded risk.")
    cot_net = _float_or_none(cot_payload.get("noncommercial_net_oi_pct"))
    if cot_net is not None:
        items.append(f"Macro COT context: non-commercial net positioning is {cot_net:.1f}% of futures open interest in the mapped market.")
    if not items:
        items.append("Main thing to watch is data coverage: this symbol needs options-chain and short-positioning sources before the signal is useful.")
    return items[:8]


def _default_options_providers() -> List[OptionsProvider]:
    providers: Dict[str, OptionsProvider] = {
        "polygon": PolygonOptionsProvider(),
        "tradier": TradierOptionsProvider(),
        "yfinance": YFinanceOptionsProvider(),
    }
    priority = os.getenv("POSITIONING_OPTIONS_PROVIDER_PRIORITY", "polygon,tradier,yfinance")
    result = []
    for name in [item.strip().lower() for item in priority.split(",") if item.strip()]:
        provider = providers.get(name)
        if provider:
            result.append(provider)
    return result or [PolygonOptionsProvider(), TradierOptionsProvider(), YFinanceOptionsProvider()]


def _contract_from_parts(**kwargs: Any) -> Optional[OptionContract]:
    expiration = str(kwargs.get("expiration") or "").strip()
    strike = _float_or_none(kwargs.get("strike"))
    option_type = str(kwargs.get("option_type") or "").strip().lower()
    if option_type in {"calls", "c"}:
        option_type = "call"
    if option_type in {"puts", "p"}:
        option_type = "put"
    if not expiration or strike is None or option_type not in {"call", "put"}:
        return None
    return OptionContract(
        symbol=_normalize_symbol(str(kwargs.get("symbol") or "")),
        expiration=expiration[:10],
        strike=float(strike),
        option_type=option_type,
        open_interest=max(_float_or_none(kwargs.get("open_interest")) or 0.0, 0.0),
        volume=max(_float_or_none(kwargs.get("volume")) or 0.0, 0.0),
        gamma=_float_or_none(kwargs.get("gamma")),
        delta=_float_or_none(kwargs.get("delta")),
        implied_volatility=_normalize_iv(kwargs.get("implied_volatility")),
        provider=str(kwargs.get("provider") or ""),
        contract_symbol=kwargs.get("contract_symbol"),
    )


def _black_scholes_gamma(spot: float, strike: float, iv: float, expiration: str) -> Optional[float]:
    spot = _float_or_none(spot) or 0.0
    strike = _float_or_none(strike) or 0.0
    iv = _normalize_iv(iv) or 0.0
    tte = _time_to_expiry(expiration)
    if spot <= 0 or strike <= 0 or iv <= 0 or tte <= 0:
        return None
    d1 = (math.log(spot / strike) + (0.045 + 0.5 * iv * iv) * tte) / (iv * math.sqrt(tte))
    return math.exp(-0.5 * d1 * d1) / (spot * iv * math.sqrt(tte) * math.sqrt(2 * math.pi))


def _time_to_expiry(expiration: str) -> float:
    try:
        expiry = datetime.fromisoformat(str(expiration)[:10]).replace(hour=21, minute=0, second=0)
    except ValueError:
        return 0.0
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seconds = max((expiry - now).total_seconds(), 0.0)
    return seconds / SECONDS_PER_YEAR


def _wall(rows: List[Dict[str, Any]], option_type: str, *, positive: bool) -> Optional[Dict[str, Any]]:
    selected = [row for row in rows if row.get("option_type") == option_type]
    if not selected:
        return None
    if positive:
        row = max(selected, key=lambda item: item.get("signed_dollar_gamma_1pct") or 0.0)
    else:
        row = min(selected, key=lambda item: item.get("signed_dollar_gamma_1pct") or 0.0)
    return {
        "strike": _round_or_none(row.get("strike"), 2),
        "expiration": row.get("expiration"),
        "signed_dollar_gamma_1pct": _round_or_none(row.get("signed_dollar_gamma_1pct"), 2),
        "open_interest": _round_or_none(row.get("open_interest"), 0),
    }


def _gamma_flip(contracts: List[OptionContract], underlying_price: Optional[float]) -> Optional[float]:
    if not underlying_price or not contracts:
        return None
    grid = [underlying_price * (0.75 + idx * 0.01) for idx in range(51)]
    values: List[Tuple[float, float]] = []
    for spot in grid:
        total = 0.0
        used = 0
        for item in contracts:
            gamma = item.gamma
            if item.implied_volatility:
                gamma = _black_scholes_gamma(spot, item.strike, item.implied_volatility, item.expiration)
            if gamma is None or not item.open_interest:
                continue
            sign = 1 if item.option_type == "call" else -1
            total += sign * gamma * item.open_interest * CONTRACT_MULTIPLIER * (spot ** 2) * 0.01
            used += 1
        if used:
            values.append((spot, total))
    for (prev_spot, prev_value), (spot, value) in zip(values, values[1:]):
        if prev_value == 0:
            return prev_spot
        if (prev_value < 0 < value) or (prev_value > 0 > value):
            distance = abs(prev_value) + abs(value)
            if distance <= 0:
                return spot
            return prev_spot + (spot - prev_spot) * abs(prev_value) / distance
    if values:
        nearest = min(values, key=lambda item: abs(item[1]))
        if abs(nearest[1]) < max(abs(value) for _, value in values) * 0.12:
            return nearest[0]
    return None


def _max_pain(contracts: List[OptionContract]) -> Optional[float]:
    strikes = sorted({item.strike for item in contracts if item.open_interest > 0})
    if not strikes:
        return None
    best_strike = None
    best_payout = None
    for candidate in strikes:
        payout = 0.0
        for item in contracts:
            if item.option_type == "call":
                payout += max(candidate - item.strike, 0.0) * item.open_interest * CONTRACT_MULTIPLIER
            else:
                payout += max(item.strike - candidate, 0.0) * item.open_interest * CONTRACT_MULTIPLIER
        if best_payout is None or payout < best_payout:
            best_payout = payout
            best_strike = candidate
    return best_strike


def _unusual_options_activity(contracts: List[OptionContract]) -> List[Dict[str, Any]]:
    rows = []
    for item in contracts:
        if item.volume <= 0:
            continue
        denominator = item.open_interest if item.open_interest > 0 else 1.0
        ratio = item.volume / denominator
        if item.volume >= 500 and ratio >= 0.5:
            rows.append(
                {
                    "contract_symbol": item.contract_symbol,
                    "expiration": item.expiration,
                    "strike": item.strike,
                    "option_type": item.option_type,
                    "volume": _round_or_none(item.volume, 0),
                    "open_interest": _round_or_none(item.open_interest, 0),
                    "volume_to_oi": round(ratio, 2),
                }
            )
    rows.sort(key=lambda item: (item["volume_to_oi"], item["volume"]), reverse=True)
    return rows[:8]


def _parse_sec_ftd_zip(content: bytes, symbol: str) -> Dict[str, Any]:
    normalized = _normalize_symbol(symbol)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [name for name in archive.namelist() if name.lower().endswith(".txt")]
        if not names:
            return {}
        raw = archive.read(names[0]).decode("latin-1", errors="replace")
    reader = csv.DictReader(io.StringIO(raw), delimiter="|")
    rows = []
    for row in reader:
        row_symbol = _normalize_symbol(row.get("SYMBOL") or row.get("Symbol") or "")
        if row_symbol != normalized:
            continue
        quantity = _float_or_none(row.get("QUANTITY (FAILS)") or row.get("QUANTITY_FAILS"))
        price = _float_or_none(row.get("PRICE"))
        settlement = row.get("SETTLEMENT DATE") or row.get("SETTLEMENT_DATE")
        if quantity is None:
            continue
        rows.append(
            {
                "settlement_date": settlement,
                "quantity": quantity,
                "price": price,
                "notional": quantity * price if price else None,
            }
        )
    if not rows:
        return {}
    rows.sort(key=lambda item: item.get("settlement_date") or "")
    latest = rows[-1]
    recent = rows[-10:]
    return {
        "latest_settlement_date": latest.get("settlement_date"),
        "latest_quantity": _round_or_none(latest.get("quantity"), 0),
        "latest_price": _round_or_none(latest.get("price"), 2),
        "latest_notional": _round_or_none(latest.get("notional"), 2),
        "ten_record_average_quantity": _round_or_none(sum(item.get("quantity") or 0 for item in recent) / len(recent), 0),
    }


def _recent_sec_ftd_files() -> Iterable[Tuple[str, str]]:
    today = date.today().replace(day=15)
    for offset in range(0, 8):
        month = today.month - offset
        year = today.year
        while month <= 0:
            month += 12
            year -= 1
        year_month = f"{year}{month:02d}"
        yield year_month, "b"
        yield year_month, "a"


def _cot_keywords(symbol: str, asset_type: str) -> List[str]:
    normalized = _normalize_symbol(symbol)
    if asset_type == "crypto" or normalized in {"BTC", "BITO", "IBIT", "MSTR", "COIN"}:
        return ["BITCOIN"]
    if normalized in {"QQQ", "TQQQ", "SQQQ", "NVDA", "AMD", "MSFT", "AAPL", "META", "AVGO", "TSLA"}:
        return ["NASDAQ", "NQ"]
    if normalized in {"SPY", "VOO", "IVV", "SPX", "ES"}:
        return ["S&P", "E-MINI"]
    if normalized in {"IWM", "RUT"}:
        return ["RUSSELL"]
    if normalized in {"GLD", "GDX", "GOLD"}:
        return ["GOLD"]
    if normalized in {"SLV", "SILVER"}:
        return ["SILVER"]
    if normalized in {"USO", "XLE", "OIH", "WTI"}:
        return ["CRUDE", "OIL"]
    return ["NASDAQ"] if len(normalized) <= 5 else []


def _find_cot_record(rows: List[Dict[str, Any]], keywords: List[str]) -> Optional[Dict[str, Any]]:
    uppercase_keywords = [keyword.upper() for keyword in keywords]
    for row in rows:
        name = str(row.get("market_and_exchange_names") or row.get("market_and_exchange_name") or "").upper()
        if all(keyword in name for keyword in uppercase_keywords):
            return row
    for row in rows:
        name = str(row.get("market_and_exchange_names") or row.get("market_and_exchange_name") or "").upper()
        if any(keyword in name for keyword in uppercase_keywords):
            return row
    return None


def _normalize_symbol(symbol: str) -> str:
    return "".join(ch for ch in str(symbol or "").upper().strip() if ch.isalnum() or ch in {".", "-"}).replace("$", "")


def _float_or_none(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        parsed = float(value)
        if math.isnan(parsed) or math.isinf(parsed):
            return None
        return parsed
    except (TypeError, ValueError):
        return None


def _first_number(*values: Any) -> Optional[float]:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _normalize_iv(value: Any) -> Optional[float]:
    iv = _float_or_none(value)
    if iv is None or iv <= 0:
        return None
    if iv > 5:
        iv = iv / 100.0
    if iv > 0 and iv < 0.01:
        iv = iv * 100.0
    return iv if 0.01 <= iv <= 5 else None


def _percent_value(value: Any) -> Optional[float]:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return parsed * 100 if 0 <= parsed <= 1 else parsed


def _ratio(numerator: float, denominator: float) -> Optional[float]:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 3)


def _round_or_none(value: Any, digits: int) -> Optional[float]:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return round(parsed, digits)


def _number_from_keys(row: Dict[str, Any], keys: List[str]) -> Optional[float]:
    for key in keys:
        parsed = _float_or_none(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _get_attr_or_item(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _dedupe(items: Iterable[str]) -> List[str]:
    result = []
    for item in items:
        cleaned = str(item or "").strip()
        if cleaned and cleaned not in result:
            result.append(cleaned)
    return result


def _has_meaningful_values(*payloads: Any) -> bool:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for key, value in payload.items():
            if key in {"source", "file"}:
                continue
            if value not in (None, "", [], {}):
                return True
    return False
