"""TOP50 + 白名单/黑名单评级 — 4h 动态刷新（scheduler 重启后仍可靠）。

- schedule.every(4).hours 在进程重启后会重新计时
- 15min 轮询 + system_settings.rating_refresh_next_due_utc 保证至少每 4h 刷新一次
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config

RATING_REFRESH_INTERVAL_HOURS = 4
RATING_REFRESH_INTERVAL_SECONDS = RATING_REFRESH_INTERVAL_HOURS * 3600
RATING_REFRESH_POLL_MINUTES = 15

RATING_REFRESH_NEXT_DUE_KEY = "rating_refresh_next_due_utc"
RATING_REFRESH_LAST_OK_KEY = "rating_refresh_last_ok_utc"


def _db_config() -> dict:
    return {**get_db_config()}


def _parse_utc_naive(value: str) -> Optional[datetime]:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _read_setting_dt(cur, key: str) -> Optional[datetime]:
    cur.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
        (key,),
    )
    row = cur.fetchone()
    if not row:
        return None
    val = row.get("setting_value") if isinstance(row, dict) else row[0]
    return _parse_utc_naive(val or "")


def _write_setting(cur, key: str, value: str, description: str, updated_by: str) -> None:
    cur.execute(
        """
        INSERT INTO system_settings
          (setting_key, setting_value, description, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE
          setting_value = VALUES(setting_value),
          description = VALUES(description),
          updated_by = VALUES(updated_by),
          updated_at = NOW()
        """,
        (key, value, description, updated_by),
    )


def rating_round_is_due(
    conn,
    *,
    now: Optional[datetime] = None,
    manual: bool = False,
) -> Tuple[bool, str]:
    """是否到了该刷新 TOP50 + 评级的时间。"""
    if manual:
        return True, "manual"

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        last_ok = _read_setting_dt(cur, RATING_REFRESH_LAST_OK_KEY)
        if last_ok:
            elapsed_s = (now - last_ok).total_seconds()
            if elapsed_s >= RATING_REFRESH_INTERVAL_SECONDS:
                return True, (
                    f"逾期补跑 距上次成功 {elapsed_s / 3600:.2f}h >= "
                    f"{RATING_REFRESH_INTERVAL_HOURS}h (last_ok={last_ok.isoformat()})"
                )

        next_due = _read_setting_dt(cur, RATING_REFRESH_NEXT_DUE_KEY)
        if next_due and now < next_due:
            remain_s = (next_due - now).total_seconds()
            return False, (
                f"未到点 剩余 {remain_s / 3600:.2f}h (next_due={next_due.isoformat()})"
            )

        if last_ok:
            elapsed_s = (now - last_ok).total_seconds()
            if elapsed_s < RATING_REFRESH_INTERVAL_SECONDS:
                return False, (
                    f"距上次成功 {elapsed_s / 3600:.2f}h < {RATING_REFRESH_INTERVAL_HOURS}h "
                    f"(last_ok={last_ok.isoformat()})"
                )

    return True, "due"


def rating_claim_next_slot(
    conn,
    *,
    now: Optional[datetime] = None,
    triggered_by: str = "scheduler",
) -> datetime:
    """认领下一 4h 窗口，防止 15min 轮询重复触发。"""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    next_due = now + timedelta(seconds=RATING_REFRESH_INTERVAL_SECONDS)
    value = next_due.strftime("%Y-%m-%dT%H:%M:%S")
    with conn.cursor() as cur:
        _write_setting(
            cur,
            RATING_REFRESH_NEXT_DUE_KEY,
            value,
            f"TOP50+评级下一轮最早执行 UTC ({RATING_REFRESH_INTERVAL_HOURS}h 周期)",
            triggered_by,
        )
    logger.info(
        f"[评级刷新] 已认领 {RATING_REFRESH_INTERVAL_HOURS}h 窗口, "
        f"next_due_utc={value} triggered_by={triggered_by}"
    )
    return next_due


def rating_mark_ok(conn, *, triggered_by: str = "scheduler") -> None:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    value = now.strftime("%Y-%m-%dT%H:%M:%S")
    with conn.cursor() as cur:
        _write_setting(
            cur,
            RATING_REFRESH_LAST_OK_KEY,
            value,
            "TOP50+评级上次成功完成 UTC",
            triggered_by,
        )
    logger.info(f"[评级刷新] 记录 last_ok_utc={value} triggered_by={triggered_by}")


def run_rating_refresh_if_due(
    *,
    manual: bool = False,
    triggered_by: str = "scheduler",
) -> bool:
    """
    若到点则刷新 TOP50 + trading_symbol_rating。
    返回 True 表示本轮已执行且成功完成。
    """
    conn = None
    try:
        conn = pymysql.connect(**_db_config(), autocommit=True, connect_timeout=10)
        due, reason = rating_round_is_due(conn, manual=manual)
        if not due:
            logger.info(f"[评级刷新] 跳过: {reason} triggered_by={triggered_by}")
            return False

        logger.info(f"[评级刷新] 开始: {reason} triggered_by={triggered_by}")
        rating_claim_next_slot(conn, triggered_by=triggered_by)
    finally:
        if conn:
            conn.close()

    try:
        from update_top_performers import update_top_performing_symbols

        result = update_top_performing_symbols(account_id=2, top_n=50)
    except Exception as e:
        logger.error(f"[评级刷新] 失败 triggered_by={triggered_by}: {e}")
        import traceback

        logger.error(traceback.format_exc())
        return False

    if result != "ok":
        logger.info(f"[评级刷新] 未写入完成 status={result} triggered_by={triggered_by}")
        return False

    conn2 = None
    try:
        conn2 = pymysql.connect(**_db_config(), autocommit=True, connect_timeout=10)
        rating_mark_ok(conn2, triggered_by=triggered_by)
    finally:
        if conn2:
            conn2.close()

    logger.info(f"[评级刷新] 完成 triggered_by={triggered_by}")
    return True
