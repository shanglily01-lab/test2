"""探索/预测页 run detail 查询 — 按 field 只拉单列，减轻页面卡顿."""
from __future__ import annotations

from typing import Any, Dict, Optional

_RUN_TABLES = frozenset({
    "gemini_explore_runs",
    "deepseek_explore_runs",
    "gemini_predict_runs",
    "deepseek_predict_runs",
})


def fetch_run_detail_row(
    cursor,
    table: str,
    run_id: int,
    field: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if table not in _RUN_TABLES:
        raise ValueError(f"unsupported run table: {table}")
    f = (field or "").strip().lower()
    if f == "prompt":
        cols = "id, summary_zh, prompt_text"
    elif f == "raw":
        cols = "id, summary_zh, raw_response"
    else:
        cols = "id, prompt_text, raw_response, summary_zh"
    cursor.execute(f"SELECT {cols} FROM {table} WHERE id=%s", (run_id,))
    return cursor.fetchone()
