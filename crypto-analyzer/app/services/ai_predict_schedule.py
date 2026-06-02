"""Gemini / DeepSeek 预测 — 统一 4h 调度防重 (保证每 4h 至少执行一轮).

调度器每 5 分钟轮询 + worker 内秒级防重 + system_settings 认领下一窗口,
避免 schedule.every(4).hours 在进程重启后计时清零导致长期不跑。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from loguru import logger

PREDICT_ROUND_INTERVAL_HOURS = 4
PREDICT_ROUND_INTERVAL_SECONDS = PREDICT_ROUND_INTERVAL_HOURS * 3600
PREDICT_SCHEDULE_POLL_MINUTES = 5

GEMINI_PREDICT_NEXT_DUE_KEY = "gemini_predict_next_due_utc"
DEEPSEEK_PREDICT_NEXT_DUE_KEY = "deepseek_predict_next_due_utc"
GPT_PREDICT_NEXT_DUE_KEY = "gpt_predict_next_due_utc"


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
    return _parse_utc_naive(row.get("setting_value") or "")


def _last_run_at(cur, runs_table: str) -> Optional[datetime]:
    cur.execute(f"SELECT MAX(asof_utc) AS last_at FROM `{runs_table}`")
    row = cur.fetchone()
    last_at = (row or {}).get("last_at")
    if last_at is None:
        return None
    if getattr(last_at, "tzinfo", None) is not None:
        return last_at.astimezone(timezone.utc).replace(tzinfo=None)
    return last_at


def predict_round_is_due(
    conn,
    *,
    runs_table: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    manual: bool = False,
    log_tag: str = "Predict",
) -> Tuple[bool, str]:
    """是否到了该跑下一轮的时间 (manual 始终 True)."""
    if manual:
        return True, "manual"

    now = now or datetime.now()
    with conn.cursor() as cur:
        next_due = _read_setting_dt(cur, next_due_key)
        if next_due and now < next_due:
            remain_s = (next_due - now).total_seconds()
            return False, f"未到点 剩余 {remain_s / 3600:.2f}h (next_due={next_due.isoformat()})"

        last_at = _last_run_at(cur, runs_table)
        if last_at:
            elapsed_s = (now - last_at).total_seconds()
            if elapsed_s < PREDICT_ROUND_INTERVAL_SECONDS:
                return False, (
                    f"距上次执行 {elapsed_s / 3600:.2f}h < {PREDICT_ROUND_INTERVAL_HOURS}h "
                    f"(last_at={last_at.isoformat()})"
                )

    return True, "due"


def predict_claim_next_slot(
    conn,
    *,
    next_due_key: str,
    now: Optional[datetime] = None,
    log_tag: str = "Predict",
) -> datetime:
    """认领下一 4h 窗口 (本轮开始后写入, 防止 5min 轮询重复触发)."""
    now = now or datetime.now()
    next_due = now + timedelta(seconds=PREDICT_ROUND_INTERVAL_SECONDS)
    value = next_due.strftime("%Y-%m-%dT%H:%M:%S")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO system_settings
              (setting_key, setting_value, description, updated_by, updated_at)
            VALUES (%s, %s, %s, 'ai_predict_schedule', NOW())
            ON DUPLICATE KEY UPDATE
              setting_value = VALUES(setting_value),
              updated_by = 'ai_predict_schedule',
              updated_at = NOW()
            """,
            (
                next_due_key,
                value,
                f"{log_tag} 下一轮最早执行 UTC ({PREDICT_ROUND_INTERVAL_HOURS}h 周期)",
            ),
        )
    logger.info(f"[{log_tag}] 已认领 {PREDICT_ROUND_INTERVAL_HOURS}h 窗口, next_due_utc={value}")
    return next_due
