"""探索/预测页 API 读库 — 走连接池 + 语句超时，禁止每次 new 连接."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Callable, Dict, TypeVar

from loguru import logger

_EXPLORE_READ_MAX_S = 8
_POS_CACHE: Dict[str, tuple] = {}
_POS_CACHE_TTL_S = 30
_POS_CACHE_LOCK = threading.Lock()

T = TypeVar("T")


def apply_explore_read_guard(cursor, *, max_seconds: int = _EXPLORE_READ_MAX_S) -> None:
    for stmt in (
        ("SET SESSION max_statement_time=%s", (max_seconds,)),
        ("SET SESSION max_execution_time=%s", (max_seconds * 1000,)),
    ):
        try:
            cursor.execute(stmt[0], stmt[1])
            return
        except Exception as e:
            logger.debug(f"[explore_db_guard] {stmt[0]} not applied: {e}")


@contextmanager
def explore_db_cursor(*, max_seconds: int = _EXPLORE_READ_MAX_S):
    """复用 API 连接池，避免探索页每次握手 + 占满独立连接."""
    import pymysql.cursors
    from app.database.connection_pool import get_api_connection

    conn = get_api_connection(acquire_timeout=3.0)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            apply_explore_read_guard(cur, max_seconds=max_seconds)
            yield cur
    finally:
        conn.close()


def explore_positions_cache_get(cache_key: str, loader: Callable[[], T]) -> T:
    now = time.time()
    with _POS_CACHE_LOCK:
        hit = _POS_CACHE.get(cache_key)
        if hit and now - hit[0] < _POS_CACHE_TTL_S:
            return hit[1]
    data = loader()
    with _POS_CACHE_LOCK:
        _POS_CACHE[cache_key] = (now, data)
    return data


def invalidate_explore_positions_cache(source: str = None) -> None:
    with _POS_CACHE_LOCK:
        if not source:
            _POS_CACHE.clear()
            return
        for key in list(_POS_CACHE.keys()):
            if key.startswith(f"{source}:"):
                del _POS_CACHE[key]
