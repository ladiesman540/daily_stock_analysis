# -*- coding: utf-8 -*-
"""Watchlist service — thin layer over the watchlist_symbols DB table.

首次调用 get_symbols() 时若表为空，会将 RESEARCH_US_WATCHLIST 环境变量中的
逗号分隔符号一次性导入数据库（source='env_seed'），后续不再读取该环境变量。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _parse_env_watchlist() -> List[str]:
    raw = os.getenv("RESEARCH_US_WATCHLIST", "")
    symbols = [s.strip().upper() for s in re.split(r"[,\s]+", raw) if s.strip()]
    # dedup while preserving order
    seen: set[str] = set()
    result: List[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            result.append(s)
    return result


class WatchlistService:
    """管理美股自选股列表，持久化于 watchlist_symbols 表。"""

    def get_symbols(self) -> List[str]:
        """Return uppercase watchlist symbols (stable order: created_at, symbol).

        If the table is empty, seeds from RESEARCH_US_WATCHLIST env var first
        (source='env_seed').  Subsequent calls always read from the DB.
        """
        from src.storage import DatabaseManager

        db = DatabaseManager.get_instance()
        rows = db.get_watchlist_symbols()

        if not rows:
            env_symbols = _parse_env_watchlist()
            if env_symbols:
                logger.info(
                    "watchlist_symbols 表为空，从 RESEARCH_US_WATCHLIST 导入 %d 个符号",
                    len(env_symbols),
                )
                for sym in env_symbols:
                    db.add_watchlist_symbol(sym, source="env_seed")
                rows = db.get_watchlist_symbols()
            else:
                logger.debug("watchlist_symbols 表为空，RESEARCH_US_WATCHLIST 也未配置")

        return [row["symbol"] for row in rows]

    def add(
        self,
        symbol: str,
        note: Optional[str] = None,
        source: str = "manual",
    ) -> Dict[str, Any]:
        """Add a symbol to the watchlist (idempotent)."""
        from src.storage import DatabaseManager

        return DatabaseManager.get_instance().add_watchlist_symbol(
            symbol, note=note, source=source
        )

    def remove(self, symbol: str) -> bool:
        """Remove a symbol from the watchlist. Returns True if a row was deleted."""
        from src.storage import DatabaseManager

        return DatabaseManager.get_instance().remove_watchlist_symbol(symbol)

    def get_rows(self) -> List[Dict[str, Any]]:
        """Return full row dicts of watchlist symbols (with seeding if empty).

        Calls get_symbols() first to ensure seeding, then returns full rows
        via DatabaseManager.get_watchlist_symbols().
        """
        from src.storage import DatabaseManager

        self.get_symbols()  # Intentionally triggers seeding if table is empty
        return DatabaseManager.get_instance().get_watchlist_symbols()
