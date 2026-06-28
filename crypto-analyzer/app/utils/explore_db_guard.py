"""探索页 API 读库保护 — 限制单条 SELECT 最长执行时间，避免拖死连接池."""
from __future__ import annotations

from loguru import logger

# MariaDB 10.5+ / MySQL 5.7.8+
_EXPLORE_READ_MAX_S = 8


def apply_explore_read_guard(cursor, *, max_seconds: int = _EXPLORE_READ_MAX_S) -> None:
    """Best-effort per-session query timeout for explore page reads."""
    for stmt in (
        ("SET SESSION max_statement_time=%s", (max_seconds,)),
        ("SET SESSION max_execution_time=%s", (max_seconds * 1000,)),
    ):
        try:
            cursor.execute(stmt[0], stmt[1])
            return
        except Exception as e:
            logger.debug(f"[explore_db_guard] {stmt[0]} not applied: {e}")
