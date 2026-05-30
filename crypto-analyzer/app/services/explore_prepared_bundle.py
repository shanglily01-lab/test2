"""
Gemini / DeepSeek 探索与战术策略 — 共用预计算数据包。

scheduler 每 15 分钟调用 rebuild_and_persist() 写入 data_cache.explore_prepared_snapshot；
各 worker 通过 get_explore_prepared_bundle() 只读，不再每轮 build/enrich。
"""
from __future__ import annotations

import copy
import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import pymysql
from loguru import logger

from app.services.data_cache_service import DATA_CACHE_DB, _get_conn
from app.services.gemini_explore_worker import (
    _build_global_context,
    _build_universe,
    _connect,
    _enrich_universe,
)

# 略大于 15min 调度周期，容忍抖动
DEFAULT_MAX_AGE_SEC = 16 * 60

_lock = threading.Lock()
_memo: Optional[Dict[str, Any]] = None


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def rebuild_and_persist() -> dict:
    """
    构建 universe + global_ctx 并写入 DB / 进程内缓存。
    由 data_cache.refresh_explore_shared_data() 调用。
    """
    t0 = time.time()
    stat = {"status": "ok", "symbol_count": 0, "elapsed_s": 0.0}
    try:
        with _connect() as conn:
            universe = _build_universe(conn)
            before = len(universe)
            _enrich_universe(conn, universe, trust_pool_narratives=True)
            after = len(universe)
            global_ctx = _build_global_context(conn)
        elapsed = time.time() - t0
        built_at = _utc_now_naive()
        _write_snapshot(universe, global_ctx, built_at, elapsed, "ok", None)
        _set_memo(universe, global_ctx, built_at.timestamp())
        stat["symbol_count"] = after
        stat["elapsed_s"] = round(elapsed, 1)
        stat["pruned"] = before - after
        logger.info(
            f"[探索预计算] 已刷新共用包 {after} sym "
            f"(enrich 剔除 {before - after}, {elapsed:.1f}s)"
        )
    except Exception as e:
        stat["status"] = "error"
        stat["error"] = str(e)[:500]
        logger.error(f"[探索预计算] 刷新失败: {e}", exc_info=True)
    return stat


def get_explore_prepared_bundle(
    conn,
    log_tag: str,
    *,
    max_age_sec: float = DEFAULT_MAX_AGE_SEC,
    allow_rebuild: bool = False,
) -> Tuple[dict, dict, bool]:
    """
    返回 (universe, global_ctx, from_shared).
    from_shared=True 表示来自 15min 预计算，非本轮现场汇集。
    """
    now = time.time()
    with _lock:
        if _memo and (now - _memo["ts"]) < max_age_sec:
            logger.info(
                f"[{log_tag}] 读共用探索包 (内存, {int(now - _memo['ts'])}s 前, "
                f"{len(_memo['universe'])} sym)"
            )
            return (
                copy.deepcopy(_memo["universe"]),
                copy.deepcopy(_memo["global_ctx"]),
                True,
            )

    loaded = _load_snapshot_from_db(max_age_sec)
    if loaded:
        universe, global_ctx, built_at = loaded
        _set_memo(universe, global_ctx, built_at.timestamp())
        logger.info(
            f"[{log_tag}] 读共用探索包 (DB, {len(universe)} sym)"
        )
        return copy.deepcopy(universe), copy.deepcopy(global_ctx), True

    if allow_rebuild:
        logger.warning(f"[{log_tag}] 共用探索包缺失或过期, 现场构建 (应检查 scheduler)")
        universe = _build_universe(conn)
        _enrich_universe(conn, universe, trust_pool_narratives=True)
        global_ctx = _build_global_context(conn)
        return universe, global_ctx, False

    logger.warning(
        f"[{log_tag}] 共用探索包不可用 (max_age={max_age_sec}s), 返回空 universe"
    )
    return {}, {}, False


def _set_memo(universe: dict, global_ctx: dict, ts: float) -> None:
    global _memo
    with _lock:
        _memo = {
            "ts": ts,
            "universe": copy.deepcopy(universe),
            "global_ctx": copy.deepcopy(global_ctx),
        }


def _write_snapshot(
    universe: dict,
    global_ctx: dict,
    built_at: datetime,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
) -> None:
    payload_u = json.dumps(universe, ensure_ascii=False, default=str)
    payload_g = json.dumps(global_ctx, ensure_ascii=False, default=str)
    conn = _get_conn(DATA_CACHE_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                REPLACE INTO explore_prepared_snapshot
                  (id, symbol_count, universe_json, global_ctx_json,
                   built_at, build_elapsed_s, status, error_msg)
                VALUES (1, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    len(universe),
                    payload_u,
                    payload_g,
                    built_at,
                    round(elapsed_s, 2),
                    status,
                    error_msg,
                ),
            )
    finally:
        conn.close()


def _load_snapshot_from_db(
    max_age_sec: float,
) -> Optional[Tuple[dict, dict, datetime]]:
    conn = _get_conn(DATA_CACHE_DB)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol_count, universe_json, global_ctx_json,
                       built_at, status
                FROM explore_prepared_snapshot
                WHERE id = 1 AND status = 'ok'
                """
            )
            row = cur.fetchone()
        if not row or not row.get("universe_json"):
            return None
        built_at = row["built_at"]
        if isinstance(built_at, datetime):
            age = (_utc_now_naive() - built_at).total_seconds()
        else:
            age = max_age_sec + 1
        if age > max_age_sec:
            return None
        universe = json.loads(row["universe_json"])
        global_ctx = json.loads(row["global_ctx_json"])
        if isinstance(global_ctx, str):
            global_ctx = json.loads(global_ctx)
        return universe, global_ctx, built_at
    except pymysql.Error as e:
        if e.args[0] == 1146:
            logger.warning("[探索预计算] 表 explore_prepared_snapshot 不存在, 请执行 migration 010")
        else:
            logger.warning(f"[探索预计算] 读 DB 失败: {e}")
        return None
    except Exception as e:
        logger.warning(f"[探索预计算] 解析快照失败: {e}")
        return None
    finally:
        conn.close()
