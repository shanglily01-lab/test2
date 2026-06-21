"""持仓顾问 tick 查询 — 只拉「到期」仓位，避免每轮从最早仓扫全表排队."""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from loguru import logger

# 与 gemini_position_advisor.HOLD_MIN_MINUTES 一致
HOLD_MIN_MINUTES = 15

# 每 5min tick 上限 (~3s/笔 → 约 2.5min，留足 scheduler 余量)
HOLD_ADVISOR_MAX_PER_TICK = 50
HOLD_REVIEW_INTERVAL_MINUTES = 15
HOLD_PROFIT_REVIEW_INTERVAL_MINUTES = 5

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

_DUE_SELECT = """
        SELECT fp.id, fp.account_id, fp.symbol, fp.position_side, fp.entry_price,
               fp.quantity, fp.leverage, fp.margin, fp.open_time, fp.source,
               TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) / 60.0 AS hold_hours,
               lr.last_hold_review,
               lr2.roi_pct AS last_roi_pct
        FROM futures_positions fp
        LEFT JOIN (
            SELECT position_id, MAX(created_at) AS last_hold_review
            FROM `{reviews_table}`
            WHERE review_type = 'hold' AND position_id IS NOT NULL
            GROUP BY position_id
        ) lr ON lr.position_id = fp.id
        LEFT JOIN `{reviews_table}` lr2
            ON lr2.position_id = fp.id
           AND lr2.review_type = 'hold'
           AND lr2.created_at = lr.last_hold_review
        WHERE fp.status = 'open'
          AND fp.account_id = 2
          AND TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) >= %s
          AND (
                lr.last_hold_review IS NULL
                OR TIMESTAMPDIFF(MINUTE, lr.last_hold_review, NOW()) >=
                   IF(IFNULL(lr2.roi_pct, 0) > 0, %s, %s)
          )
          {source_sql}
        ORDER BY lr.last_hold_review IS NULL DESC,
                 lr.last_hold_review ASC,
                 fp.open_time ASC
        LIMIT %s
"""


def _calc_margin_roi_pct(
    entry_price: float,
    current_price: float,
    side: str,
    leverage: int,
) -> float:
    if entry_price <= 0 or current_price <= 0:
        return 0.0
    side_u = (side or "").upper()
    if side_u == "LONG":
        price_chg = (current_price - entry_price) / entry_price * 100
    else:
        price_chg = (entry_price - current_price) / entry_price * 100
    return price_chg * max(int(leverage or 1), 1)


def fetch_due_hold_positions(
    conn,
    *,
    reviews_table: str,
    source_sql: str = "",
    source_params: Optional[Sequence[Any]] = None,
    hold_min_minutes: int = HOLD_MIN_MINUTES,
    review_interval_minutes: int = HOLD_REVIEW_INTERVAL_MINUTES,
    profit_review_interval_minutes: int = HOLD_PROFIT_REVIEW_INTERVAL_MINUTES,
    max_per_tick: int = HOLD_ADVISOR_MAX_PER_TICK,
) -> List[Dict]:
    """持仓满 hold_min；浮盈仓每 profit_interval 复审，其余每 review_interval."""
    extra_params: Tuple[Any, ...] = tuple(source_params or ())
    sql = _DUE_SELECT.format(reviews_table=reviews_table, source_sql=source_sql)
    params = (
        hold_min_minutes,
        profit_review_interval_minutes,
        review_interval_minutes,
        *extra_params,
        max_per_tick,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])
    except Exception as e:
        logger.error(f"[持仓顾问] 查到期仓位失败 table={reviews_table}: {e}")
        return []


def fetch_profit_flip_urgent_positions(
    conn,
    *,
    reviews_table: str,
    source_sql: str = "",
    source_params: Optional[Sequence[Any]] = None,
    hold_min_minutes: int = HOLD_MIN_MINUTES,
    exclude_ids: Optional[Set[int]] = None,
    get_price: Optional[Callable[[str], Optional[float]]] = None,
    max_per_tick: int = 20,
) -> List[Dict]:
    """
    上次审核浮盈、现已转亏 → 立即再审（不等 5/15min 间隔）。
    """
    if get_price is None:
        return []
    extra_params: Tuple[Any, ...] = tuple(source_params or ())
    sql = f"""
        SELECT fp.id, fp.account_id, fp.symbol, fp.position_side, fp.entry_price,
               fp.quantity, fp.leverage, fp.margin, fp.open_time, fp.source,
               TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) / 60.0 AS hold_hours,
               lr.last_hold_review,
               lr2.roi_pct AS last_roi_pct
        FROM futures_positions fp
        INNER JOIN (
            SELECT position_id, MAX(created_at) AS last_hold_review
            FROM `{reviews_table}`
            WHERE review_type = 'hold' AND position_id IS NOT NULL
            GROUP BY position_id
        ) lr ON lr.position_id = fp.id
        INNER JOIN `{reviews_table}` lr2
            ON lr2.position_id = fp.id
           AND lr2.review_type = 'hold'
           AND lr2.created_at = lr.last_hold_review
        WHERE fp.status = 'open'
          AND fp.account_id = 2
          AND TIMESTAMPDIFF(MINUTE, fp.open_time, NOW()) >= %s
          AND IFNULL(lr2.roi_pct, 0) > 0
          AND TIMESTAMPDIFF(MINUTE, lr.last_hold_review, NOW()) < %s
          {source_sql}
        ORDER BY lr.last_hold_review ASC
        LIMIT %s
    """
    params = (
        hold_min_minutes,
        HOLD_REVIEW_INTERVAL_MINUTES,
        *extra_params,
        max_per_tick,
    )
    skip = exclude_ids or set()
    out: List[Dict] = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = list(cur.fetchall() or [])
    except Exception as e:
        logger.error(f"[持仓顾问] 查浮盈转亏 urgent 失败 table={reviews_table}: {e}")
        return []

    for row in rows:
        pid = int(row["id"])
        if pid in skip:
            continue
        price = get_price(row["symbol"])
        if not price or price <= 0:
            continue
        roi = _calc_margin_roi_pct(
            float(row["entry_price"]),
            float(price),
            row["position_side"],
            int(row.get("leverage") or 5),
        )
        if roi < 0:
            row = dict(row)
            row["urgent_reason"] = "profit_to_loss_flip"
            out.append(row)
    return out


def fetch_all_due_hold_positions(
    conn,
    *,
    reviews_table: str,
    source_sql: str = "",
    source_params: Optional[Sequence[Any]] = None,
    get_price: Optional[Callable[[str], Optional[float]]] = None,
    hold_min_minutes: int = HOLD_MIN_MINUTES,
    max_per_tick: int = HOLD_ADVISOR_MAX_PER_TICK,
) -> List[Dict]:
    """常规到期 + 浮盈转亏 urgent 合并（去重）."""
    due = fetch_due_hold_positions(
        conn,
        reviews_table=reviews_table,
        source_sql=source_sql,
        source_params=source_params,
        hold_min_minutes=hold_min_minutes,
        max_per_tick=max_per_tick,
    )
    seen = {int(r["id"]) for r in due}
    urgent = fetch_profit_flip_urgent_positions(
        conn,
        reviews_table=reviews_table,
        source_sql=source_sql,
        source_params=source_params,
        hold_min_minutes=hold_min_minutes,
        exclude_ids=seen,
        get_price=get_price,
    )
    if urgent:
        logger.info(
            f"[持仓顾问] 浮盈转亏 urgent +{len(urgent)} 笔 table={reviews_table}"
        )
    return due + urgent
