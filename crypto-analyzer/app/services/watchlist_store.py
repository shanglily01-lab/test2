"""用户合约自选交易对落库。"""
from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from app.services.watchlist_config import WATCHLIST_MAX_SYMBOLS
from app.utils.futures_symbol import futures_symbol_rating_canonical


def ensure_watchlist_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_watchlist_symbols (
                id INT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(32) NOT NULL,
                sort_order INT NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uk_watchlist_symbol (symbol)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )


def list_watchlist_symbols(conn) -> List[Dict[str, Any]]:
    ensure_watchlist_table(conn)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, sort_order, created_at
            FROM user_watchlist_symbols
            ORDER BY sort_order ASC, id ASC
            """
        )
        rows = cur.fetchall() or []
    return list(rows)


def add_watchlist_symbol(conn, symbol: str) -> Dict[str, Any]:
    ensure_watchlist_table(conn)
    canon = futures_symbol_rating_canonical(symbol)
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM user_watchlist_symbols")
        row = cur.fetchone() or {}
        n = int(row.get("cnt") or 0)
        if n >= WATCHLIST_MAX_SYMBOLS:
            raise ValueError(f"自选最多 {WATCHLIST_MAX_SYMBOLS} 个")
        cur.execute(
            "SELECT id FROM user_watchlist_symbols WHERE symbol=%s LIMIT 1",
            (canon,),
        )
        if cur.fetchone():
            return {"symbol": canon, "added": False, "reason": "already"}
        cur.execute("SELECT COALESCE(MAX(sort_order), 0) AS mx FROM user_watchlist_symbols")
        mx = int((cur.fetchone() or {}).get("mx") or 0)
        cur.execute(
            "INSERT INTO user_watchlist_symbols (symbol, sort_order) VALUES (%s, %s)",
            (canon, mx + 1),
        )
    logger.info(f"[自选] 添加 {canon}")
    return {"symbol": canon, "added": True}


def remove_watchlist_symbol(conn, symbol: str) -> bool:
    ensure_watchlist_table(conn)
    canon = futures_symbol_rating_canonical(symbol)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM user_watchlist_symbols WHERE symbol=%s", (canon,))
        return int(cur.rowcount or 0) > 0
