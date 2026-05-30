"""战术探索 (10 策略) — 统一 4h 调度防重 (保证每 4h 至少一轮, 重启不丢).

与 Gemini/DeepSeek 预测相同模式:
  - system_settings.{tactical_{source}_next_due_utc} 持久化下一窗口
  - scheduler 每 15min 轮询 + worker 认领 next_due
  - 距上次 runs 表任意一轮 < 4h 时跳过 (manual 不受限)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from loguru import logger

from app.services.ai_predict_schedule import (
    PREDICT_ROUND_INTERVAL_HOURS,
    PREDICT_ROUND_INTERVAL_SECONDS,
    _last_run_at,
    _read_setting_dt,
    predict_claim_next_slot,
    predict_round_is_due,
)

TACTICAL_ROUND_INTERVAL_HOURS = PREDICT_ROUND_INTERVAL_HOURS
TACTICAL_POLL_MINUTES = 15

# 错峰: 10 任务 × 24min = 4h (仅用于首次初始化 next_due)
TACTICAL_SLOT_STEP_MINUTES = 24
TACTICAL_BLOCK_FIRST_OFFSET_MIN = 12


def tactical_next_due_key(source: str) -> str:
    return f"tactical_{source}_next_due_utc"


def ensure_tactical_next_due(
    conn,
    *,
    source: str,
    runs_table: str,
    slot_index: int,
    log_tag: str,
    now: Optional[datetime] = None,
) -> None:
    """首次部署/无 next_due 时写入: 基于上次 asof_utc+4h 或按槽位错峰."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    key = tactical_next_due_key(source)
    with conn.cursor() as cur:
        if _read_setting_dt(cur, key):
            return

        last_at = _last_run_at(cur, runs_table)
        if last_at:
            next_due = last_at + timedelta(seconds=PREDICT_ROUND_INTERVAL_SECONDS)
            if next_due < now:
                next_due = now
        else:
            offset_min = TACTICAL_BLOCK_FIRST_OFFSET_MIN + slot_index * TACTICAL_SLOT_STEP_MINUTES
            next_due = now + timedelta(minutes=offset_min)

        value = next_due.strftime("%Y-%m-%dT%H:%M:%S")
        cur.execute(
            """
            INSERT INTO system_settings
              (setting_key, setting_value, description, updated_by, updated_at)
            VALUES (%s, %s, %s, 'ai_tactical_explore_schedule', NOW())
            ON DUPLICATE KEY UPDATE
              setting_value = VALUES(setting_value),
              updated_by = 'ai_tactical_explore_schedule',
              updated_at = NOW()
            """,
            (
                key,
                value,
                f"{log_tag} 下一轮最早执行 UTC ({TACTICAL_ROUND_INTERVAL_HOURS}h 周期)",
            ),
        )
    conn.commit()
    logger.info(f"[{log_tag}] 初始化 next_due_utc={value}")


def tactical_round_is_due(
    conn,
    *,
    runs_table: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    manual: bool = False,
    log_tag: str = "Tactical",
) -> Tuple[bool, str]:
    return predict_round_is_due(
        conn,
        runs_table=runs_table,
        next_due_key=next_due_key,
        now=now,
        manual=manual,
        log_tag=log_tag,
    )


def tactical_claim_next_slot(
    conn,
    *,
    next_due_key: str,
    now: Optional[datetime] = None,
    log_tag: str = "Tactical",
) -> datetime:
    return predict_claim_next_slot(
        conn,
        next_due_key=next_due_key,
        now=now,
        log_tag=log_tag,
    )
