"""探索/预测页列表查询 — 轻量 SQL，禁止 information_schema 探测."""
from __future__ import annotations

from typing import Dict, List

from app.utils.explore_sql import (
    CLOSED_POSITIONS_LIST_SQL,
    OPEN_POSITIONS_LIST_SQL,
    predict_runs_list_sql,
    runs_list_sql,
)

# True=用 has_prompt 列；若库未跑 migration 023 则自动降级一次
_RUN_FLAG_COLS: Dict[str, bool] = {}


def _exec_runs_list(cursor, table: str, limit: int, predict: bool = False) -> List[dict]:
    use_flags = _RUN_FLAG_COLS.get(table, True)
    builder = predict_runs_list_sql if predict else runs_list_sql
    sql = builder(table, has_flag_columns=use_flags)
    try:
        cursor.execute(sql, (limit,))
        return cursor.fetchall() or []
    except Exception as e:
        err = str(e).lower()
        if use_flags and ("has_prompt" in err or "1054" in err or "unknown column" in err):
            _RUN_FLAG_COLS[table] = False
            sql = builder(table, has_flag_columns=False)
            cursor.execute(sql, (limit,))
            return cursor.fetchall() or []
        raise


def fetch_runs_list(cursor, table: str, limit: int) -> List[dict]:
    return _exec_runs_list(cursor, table, limit, predict=False)


def fetch_predict_runs_list(cursor, table: str, limit: int) -> List[dict]:
    return _exec_runs_list(cursor, table, limit, predict=True)


def fetch_open_positions(cursor, source: str, limit: int = 200) -> List[dict]:
    cursor.execute(OPEN_POSITIONS_LIST_SQL, (source, limit))
    return cursor.fetchall() or []


def fetch_closed_positions(cursor, source: str, limit: int = 50) -> List[dict]:
    cursor.execute(CLOSED_POSITIONS_LIST_SQL, (source, min(limit, 50)))
    return cursor.fetchall() or []
