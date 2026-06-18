"""持仓顾问 tick 查询 — 只拉「到期」仓位，避免每轮从最早仓扫全表排队."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from loguru import logger

# 与 gemini_position_advisor.HOLD_MIN_MINUTES 一致
HOLD_MIN_MINUTES = 15

# 每 15min tick 上限 (~3s/笔 → 约 2.5min，留足 scheduler 余量)
HOLD_ADVISOR_MAX_PER_TICK = 50
HOLD_REVIEW_INTERVAL_MINUTES = 15

DEEPSEEK_HOLD_SOURCE_SQL = (
    "AND LOWER(fp.source) NOT IN ("
    "'gemini_explore', 'gemini_predict', "
    "'gemini_midline_long', 'gemini_midline_short', "
    "'deepseek_midline_long', 'deepseek_midline_short'"
    ")"
)
GEMINI_HOLD_SOURCE_SQL = (
    "AND LOWER(fp.source) IN ('gemini_explore', 'gemini_predict')"
)


def fetch_due_hold_positions(
    conn,
    *,
    reviews_table: str,
    source_sql: str = "",
    source_params: Optional[Sequence[Any]] = None,
    hold_min_minutes: int = HOLD_MIN_MINUTES,
    review_interval_minutes: int = HOLD_REVIEW_INTERVAL_MINUTES,
    max_per_tick: int = HOLD_ADVISOR_MAX_PER_TICK,
) -> List[Dict]:
    """持仓满 hold_min 且距上次 hold 审核 ≥ review_interval（或从未审核）."""
    extra_params: Tuple[Any, ...] = tuple(source_params or ())
    sql = f"""
        SELECT fp.id, fp.account_id, fp.symbol, fp.position_side, fp.entry_price,
               fp.quantity, fp.leverage, fp.margin, fp.open_time, fp.source,
               TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) / 60.0 AS hold_hours,
               lr.last_hold_review
        FROM futures_positions fp
        LEFT JOIN (
            SELECT position_id, MAX(created_at) AS last_hold_review
            FROM `{reviews_table}`
            WHERE review_type = 'hold' AND position_id IS NOT NULL
            GROUP BY position_id
        ) lr ON lr.position_id = fp.id
        WHERE fp.status = 'open'
          AND fp.account_id = 2
          AND TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) >= %s
          AND (
                lr.last_hold_review IS NULL
                OR TIMESTAMPDIFF(MINUTE, lr.last_hold_review, NOW()) >= %s
          )
          {source_sql}
        ORDER BY lr.last_hold_review IS NULL DESC,
                 lr.last_hold_review ASC,
                 fp.open_time ASC
        LIMIT %s
    """
    params = (hold_min_minutes, review_interval_minutes, *extra_params, max_per_tick)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])
    except Exception as e:
        logger.error(f"[持仓顾问] 查到期仓位失败 table={reviews_table}: {e}")
        return []
