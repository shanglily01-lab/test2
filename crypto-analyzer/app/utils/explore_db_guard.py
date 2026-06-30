"""探索/预测页 API 读库保护 — 短超时独立连接 + 8s 语句上限，避免拖死连接池."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional, TypeVar

from loguru import logger

# MariaDB 10.5+ / MySQL 5.7.8+
_EXPLORE_READ_MAX_S = 8
_EXPLORE_CONNECT_KW = {
    "connect_timeout": 5,
    "read_timeout": 10,
    "write_timeout": 10,
}

_POS_CACHE: Dict[str, tuple] = {}
_POS_CACHE_TTL_S = 5
_POS_CACHE_LOCK = threading.Lock()

T = TypeVar("T")


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


@contextmanager
def explore_db_cursor(*, max_seconds: int = _EXPLORE_READ_MAX_S):
    """
    探索页只读专用连接：不走 API 连接池，10s 读超时 + max_statement_time，
    慢查询失败快退，不把全站连接池占满。
    """
    import pymysql
    import pymysql.cursors

    from app.utils.config_loader import get_db_config

    cfg = dict(get_db_config())
    conn = pymysql.connect(
        **cfg,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        **_EXPLORE_CONNECT_KW,
    )
    try:
        with conn.cursor() as cur:
            apply_explore_read_guard(cur, max_seconds=max_seconds)
            yield cur
    finally:
        try:
            conn.close()
        except Exception:
            pass


def explore_positions_cache_get(cache_key: str, loader: Callable[[], T]) -> T:
    """OPEN/CLOSED 列表短缓存，减轻 15s 轮询 + 首屏并发对 futures_positions 的压力."""
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
