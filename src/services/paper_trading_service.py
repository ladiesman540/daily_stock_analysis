# -*- coding: utf-8 -*-
"""Paper portfolio that mirrors the system's own US-stock recommendations.

Every analysis that ships entry/stop/target prices becomes a paper position, and a
daily grading pass closes positions through their stop or target. This is the
feedback loop that tells the user whether the system's calls actually work.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from src.services.portfolio_service import PortfolioConflictError, PortfolioService
from src.services.research_market_data import ResearchMarketDataService
from src.storage import AnalysisHistory, DatabaseManager

logger = logging.getLogger(__name__)

PAPER_ACCOUNT_NAME = "Paper — System Signals"

_US_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_ENTRY_ON_NEXT_CLOSE = ("buy", "add", "买入", "加仓")
_ENTRY_ON_PULLBACK = ("watch", "wait", "观望")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


class PaperTradingService:
    """Open/close paper trades from analysis_history rows via the real PortfolioService."""

    def __init__(
        self,
        *,
        portfolio: Optional[PortfolioService] = None,
        market_data: Optional[ResearchMarketDataService] = None,
        db: Optional[DatabaseManager] = None,
    ):
        self.portfolio = portfolio or PortfolioService()
        self.market_data = market_data or ResearchMarketDataService()
        self.db = db or DatabaseManager.get_instance()
        self.position_notional = _env_float("PAPER_POSITION_NOTIONAL", 10_000.0)
        self.entry_window_days = _env_int("PAPER_ENTRY_WINDOW_DAYS", 10)
        self.max_hold_days = _env_int("PAPER_MAX_HOLD_DAYS", 60)
        self.lookback_days = _env_int("PAPER_ANALYSIS_LOOKBACK_DAYS", 30)

    def run_daily(self) -> Dict[str, Any]:
        """Idempotent daily pass: mirror new recommendations, grade open positions, snapshot."""
        account_id = self._ensure_account()
        trades = self._paper_trades(account_id)
        opened = self._open_positions_from_analyses(account_id, trades)
        trades = self._paper_trades(account_id) if opened else trades
        closed = self._grade_open_positions(account_id, trades)
        snapshot = self._snapshot(account_id)
        result = {
            "account_id": account_id,
            "opened": opened,
            "closed": closed,
            "open_positions": self._open_entries(self._paper_trades(account_id)),
            "snapshot": snapshot,
        }
        logger.info(
            "Paper trading daily pass: %d opened, %d closed, %d open",
            len(opened),
            len(closed),
            len(result["open_positions"]),
        )
        return result

    def _ensure_account(self) -> int:
        for account in self.portfolio.list_accounts():
            if account.get("name") == PAPER_ACCOUNT_NAME:
                return int(account["id"])
        account = self.portfolio.create_account(
            name=PAPER_ACCOUNT_NAME,
            broker="paper",
            market="us",
            base_currency="USD",
        )
        account_id = int(account["id"])
        initial_cash = _env_float("PAPER_INITIAL_CASH", 100_000.0)
        try:
            self.portfolio.record_cash_ledger(
                account_id=account_id,
                event_date=date.today(),
                direction="in",
                amount=initial_cash,
                note="Paper account initial funding",
            )
        except Exception as exc:
            logger.warning("Paper account funding failed: %s", exc)
        return account_id

    def _paper_trades(self, account_id: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        page = 1
        while page <= 50:  # paging API caps page_size at 100
            payload = self.portfolio.list_trade_events(account_id=account_id, page=page, page_size=100)
            batch = payload.get("items") or []
            items.extend(batch)
            if len(items) >= int(payload.get("total") or 0) or not batch:
                break
            page += 1
        return [row for row in items if str(row.get("trade_uid") or "").startswith("paper-analysis-")]

    @staticmethod
    def _open_entries(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        exits = {
            row["trade_uid"].replace("-exit", "")
            for row in trades
            if str(row.get("trade_uid") or "").endswith("-exit")
        }
        return [
            row
            for row in trades
            if str(row.get("trade_uid") or "").endswith("-entry")
            and row["trade_uid"].replace("-entry", "") not in exits
        ]

    def _recent_actionable_analyses(self) -> List[Dict[str, Any]]:
        cutoff = datetime.now() - timedelta(days=max(1, self.lookback_days))
        with self.db.get_session() as session:
            rows = session.execute(
                select(AnalysisHistory)
                .where(
                    AnalysisHistory.created_at >= cutoff,
                    AnalysisHistory.ideal_buy.isnot(None),
                    AnalysisHistory.stop_loss.isnot(None),
                    AnalysisHistory.take_profit.isnot(None),
                )
                .order_by(AnalysisHistory.created_at)
            ).scalars().all()
            return [
                {
                    "id": int(row.id),
                    "code": str(row.code or "").upper(),
                    "name": row.name,
                    "operation_advice": str(row.operation_advice or ""),
                    "ideal_buy": float(row.ideal_buy),
                    "stop_loss": float(row.stop_loss),
                    "take_profit": float(row.take_profit),
                    "created_at": row.created_at,
                }
                for row in rows
                if _US_SYMBOL_RE.match(str(row.code or "").upper())
            ]

    def _open_positions_from_analyses(
        self, account_id: int, trades: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        known_uids = {str(row.get("trade_uid") or "") for row in trades}
        opened: List[Dict[str, Any]] = []
        for analysis in self._recent_actionable_analyses():
            entry_uid = f"paper-analysis-{analysis['id']}-entry"
            if entry_uid in known_uids:
                continue
            fill = self._resolve_entry_fill(analysis)
            if fill is None:
                continue
            fill_date, fill_price = fill
            quantity = round(self.position_notional / fill_price, 4)
            if quantity <= 0:
                continue
            try:
                self.portfolio.record_trade(
                    account_id=account_id,
                    symbol=analysis["code"],
                    trade_date=fill_date,
                    side="buy",
                    quantity=quantity,
                    price=round(fill_price, 4),
                    trade_uid=entry_uid,
                    note=(
                        f"Paper entry from analysis #{analysis['id']} "
                        f"({analysis['operation_advice']}); stop {analysis['stop_loss']}, "
                        f"target {analysis['take_profit']}"
                    ),
                )
            except PortfolioConflictError:
                continue
            except Exception as exc:
                logger.warning("Paper entry failed for %s: %s", analysis["code"], exc)
                continue
            opened.append(
                {
                    "symbol": analysis["code"],
                    "analysis_id": analysis["id"],
                    "fill_date": fill_date.isoformat(),
                    "price": round(fill_price, 4),
                    "quantity": quantity,
                }
            )
        return opened

    def _resolve_entry_fill(self, analysis: Dict[str, Any]) -> Optional[tuple]:
        """Entry rule: Buy/Add fills at the next close; Watch/Wait fills only if price
        trades back into the ideal_buy zone within the entry window."""
        advice = analysis["operation_advice"].lower()
        analysis_date = analysis["created_at"].date()
        bars = self._bars_after(analysis["code"], analysis_date)
        if not bars:
            return None
        if any(token in advice for token in _ENTRY_ON_NEXT_CLOSE):
            first = bars[0]
            return self._bar_date(first), float(first["close"])
        if any(token in advice for token in _ENTRY_ON_PULLBACK):
            limit_price = analysis["ideal_buy"]
            for bar in bars[: self.entry_window_days]:
                low = bar.get("low")
                if low is None:
                    continue
                if float(low) <= limit_price:
                    open_price = bar.get("open")
                    fill = min(limit_price, float(open_price)) if open_price else limit_price
                    return self._bar_date(bar), float(fill)
            return None
        return None

    def _grade_open_positions(
        self, account_id: int, trades: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        closed: List[Dict[str, Any]] = []
        analyses = {item["id"]: item for item in self._recent_actionable_analyses_all()}
        for entry in self._open_entries(trades):
            uid = str(entry["trade_uid"])
            try:
                analysis_id = int(uid.replace("paper-analysis-", "").replace("-entry", ""))
            except ValueError:
                continue
            analysis = analyses.get(analysis_id)
            if analysis is None:
                continue
            entry_date = date.fromisoformat(entry["trade_date"])
            bars = self._bars_after(analysis["code"], entry_date)
            exit_fill = self._resolve_exit_fill(
                bars,
                stop=analysis["stop_loss"],
                target=analysis["take_profit"],
                entry_date=entry_date,
            )
            if exit_fill is None:
                continue
            exit_date, exit_price, reason = exit_fill
            try:
                self.portfolio.record_trade(
                    account_id=account_id,
                    symbol=analysis["code"],
                    trade_date=exit_date,
                    side="sell",
                    quantity=float(entry["quantity"]),
                    price=round(exit_price, 4),
                    trade_uid=uid.replace("-entry", "-exit"),
                    note=f"Paper exit ({reason}) for analysis #{analysis_id}",
                )
            except PortfolioConflictError:
                continue
            except Exception as exc:
                logger.warning("Paper exit failed for %s: %s", analysis["code"], exc)
                continue
            pnl_pct = (exit_price - float(entry["price"])) / float(entry["price"]) * 100
            closed.append(
                {
                    "symbol": analysis["code"],
                    "analysis_id": analysis_id,
                    "reason": reason,
                    "exit_date": exit_date.isoformat(),
                    "entry_price": float(entry["price"]),
                    "exit_price": round(exit_price, 4),
                    "pnl_pct": round(pnl_pct, 2),
                }
            )
        return closed

    def _resolve_exit_fill(
        self,
        bars: List[Dict[str, Any]],
        *,
        stop: float,
        target: float,
        entry_date: date,
    ) -> Optional[tuple]:
        for index, bar in enumerate(bars):
            low = bar.get("low")
            high = bar.get("high")
            open_price = bar.get("open")
            close = bar.get("close")
            # Conservative: if both stop and target are inside one bar, the stop wins.
            if low is not None and float(low) <= stop:
                fill = min(stop, float(open_price)) if open_price else stop
                return self._bar_date(bar), float(fill), "stopped"
            if high is not None and float(high) >= target:
                fill = max(target, float(open_price)) if open_price else target
                return self._bar_date(bar), float(fill), "target"
            if index + 1 >= self.max_hold_days and close is not None:
                return self._bar_date(bar), float(close), "time_stop"
        return None

    def _recent_actionable_analyses_all(self) -> List[Dict[str, Any]]:
        """Same shape as _recent_actionable_analyses but unbounded by the entry lookback,
        so old open positions can still be graded."""
        with self.db.get_session() as session:
            rows = session.execute(
                select(AnalysisHistory)
                .where(
                    AnalysisHistory.ideal_buy.isnot(None),
                    AnalysisHistory.stop_loss.isnot(None),
                    AnalysisHistory.take_profit.isnot(None),
                )
                .order_by(AnalysisHistory.created_at)
            ).scalars().all()
            return [
                {
                    "id": int(row.id),
                    "code": str(row.code or "").upper(),
                    "name": row.name,
                    "operation_advice": str(row.operation_advice or ""),
                    "ideal_buy": float(row.ideal_buy),
                    "stop_loss": float(row.stop_loss),
                    "take_profit": float(row.take_profit),
                    "created_at": row.created_at,
                }
                for row in rows
                if _US_SYMBOL_RE.match(str(row.code or "").upper())
            ]

    def _bars_after(self, symbol: str, after: date) -> List[Dict[str, Any]]:
        try:
            payload = self.market_data.get_us_equity_history(symbol)
        except Exception as exc:
            logger.warning("Paper trading bars fetch failed for %s: %s", symbol, exc)
            return []
        bars = []
        for bar in payload.get("bars") or []:
            bar_date = str(bar.get("date") or "")[:10]
            if not bar_date or bar.get("close") is None:
                continue
            try:
                parsed = date.fromisoformat(bar_date)
            except ValueError:
                continue
            if parsed > after:
                bars.append(bar)
        return bars

    @staticmethod
    def _bar_date(bar: Dict[str, Any]) -> date:
        return date.fromisoformat(str(bar["date"])[:10])

    def _snapshot(self, account_id: int) -> Dict[str, Any]:
        try:
            snapshot = self.portfolio.get_portfolio_snapshot(account_id=account_id)
            account = (snapshot.get("accounts") or [{}])[0]
            return {
                "as_of": snapshot.get("as_of"),
                "total_equity": account.get("total_equity"),
                "total_cash": account.get("total_cash"),
                "unrealized_pnl": account.get("unrealized_pnl"),
                "realized_pnl": account.get("realized_pnl"),
            }
        except Exception as exc:
            logger.warning("Paper portfolio snapshot failed: %s", exc)
            return {"status": "failed", "error": str(exc)}
