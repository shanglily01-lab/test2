"""探索/预测页列表查询 — 统一优化后的 SQL."""
from __future__ import annotations

from typing import Dict, List

from app.utils.explore_sql import (
    CLOSED_POSITIONS_LIST_SQL,
    OPEN_POSITIONS_LIST_SQL,
    predict_runs_list_sql,
    runs_list_sql,
)

_RUN_FLAG_COLS: Dict[str, bool] = {}


def _table_has_prompt_flags(cursor, table: str) -> bool:
    if table in _RUN_FLAG_COLS:
        return _RUN_FLAG_COLS[table]
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = %s AND column_name = 'has_prompt'
        """,
        (table,),
    )
    ok = int((cursor.fetchone() or {}).get("cnt") or 0) > 0
    _RUN_FLAG_COLS[table] = ok
    return ok


def fetch_runs_list(cursor, table: str, limit: int) -> List[dict]:
    sql = runs_list_sql(table, has_flag_columns=_table_has_prompt_flags(cursor, table))
    cursor.execute(sql, (limit,))
    return cursor.fetchall() or []


def fetch_predict_runs_list(cursor, table: str, limit: int) -> List[dict]:
    sql = predict_runs_list_sql(table, has_flag_columns=_table_has_prompt_flags(cursor, table))
    cursor.execute(sql, (limit,))
    return cursor.fetchall() or []


def fetch_open_positions(cursor, source: str, limit: int = 200) -> List[dict]:
    cursor.execute(OPEN_POSITIONS_LIST_SQL, (source, limit))
    return cursor.fetchall() or []


def fetch_closed_positions(cursor, source: str, limit: int = 80) -> List[dict]:
    cursor.execute(CLOSED_POSITIONS_LIST_SQL, (source, limit))
    return cursor.fetchall() or []
