"""Gemini / DeepSeek / GPT 探索+预测 — 统一固定时刻 2h 调度.

锚点: 北京时间 21:30 (= UTC 13:30), 每 2h 一轮.
Gemini / GPT / DeepSeek 错开 30 分钟; 同教师探索/预测再错开 15 分钟:
  gemini_explore +0,   gemini_predict +15,
  gpt_explore +30,     gpt_predict +45,
  deepseek_explore +60, deepseek_predict +75  (北京 22:30 / 22:45)

调度器 5/10 分钟轮询 + worker 内 next_due 认领防重。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from loguru import logger

from app.services.ai_explore_prompt import EXPLORE_MIN_INTERVAL_HOURS

PREDICT_ROUND_INTERVAL_HOURS = 2
PREDICT_ROUND_INTERVAL_SECONDS = PREDICT_ROUND_INTERVAL_HOURS * 3600
PREDICT_SCHEDULE_POLL_MINUTES = 5

# 固定锚点 UTC 13:30 = 北京时间 21:30
SCHEDULE_ANCHOR_HOUR_UTC = 13
SCHEDULE_ANCHOR_MINUTE_UTC = 30
GEMINI_DEEPSEEK_STAGGER_HOURS = 1
SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN = 15
_SCHEDULE_ANCHOR_BASE = datetime(2024, 1, 1, SCHEDULE_ANCHOR_HOUR_UTC, SCHEDULE_ANCHOR_MINUTE_UTC)

GEMINI_EXPLORE_NEXT_DUE_KEY = "gemini_explore_next_due_utc"
DEEPSEEK_EXPLORE_NEXT_DUE_KEY = "deepseek_explore_next_due_utc"
GPT_EXPLORE_NEXT_DUE_KEY = "gpt_explore_next_due_utc"
GEMINI_PREDICT_NEXT_DUE_KEY = "gemini_predict_next_due_utc"
DEEPSEEK_PREDICT_NEXT_DUE_KEY = "deepseek_predict_next_due_utc"
GPT_PREDICT_NEXT_DUE_KEY = "gpt_predict_next_due_utc"

_GEMINI_OFFSET = 0
_DEEPSEEK_OFFSET = GEMINI_DEEPSEEK_STAGGER_HOURS * 60
_GPT_OFFSET = GEMINI_DEEPSEEK_STAGGER_HOURS * 30  # 30min after Gemini, 30min before DeepSeek

STRATEGY_SCHEDULE_OFFSETS: Dict[str, int] = {
    "gemini_explore": _GEMINI_OFFSET,
    "gemini_predict": _GEMINI_OFFSET + SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN,
    "gpt_explore": _GPT_OFFSET,
    "gpt_predict": _GPT_OFFSET + SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN,
    "deepseek_explore": _DEEPSEEK_OFFSET,
    "deepseek_predict": _DEEPSEEK_OFFSET + SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN,
}


def _offset_label(offset_min: int) -> str:
    if offset_min <= 0:
        return "+0min"
    if offset_min % 60 == 0:
        h = offset_min // 60
        return f"+{h}h"
    h, m = divmod(offset_min, 60)
    if h:
        return f"+{h}h{m}min"
    return f"+{offset_min}min"


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


def _last_ok_run_at(cur, runs_table: str) -> Optional[datetime]:
    """仅 status=ok 参与周期（error/skipped 不推迟下一轮）."""
    cur.execute(
        f"SELECT MAX(asof_utc) AS last_at FROM `{runs_table}` WHERE status='ok'"
    )
    row = cur.fetchone()
    last_at = (row or {}).get("last_at")
    if last_at is None:
        return None
    if getattr(last_at, "tzinfo", None) is not None:
        return last_at.astimezone(timezone.utc).replace(tzinfo=None)
    return last_at


def _period_base_for_now(now: datetime) -> datetime:
    """当前 2h 周期起点 (共享锚点 13:30 UTC, 不含策略 offset)."""
    if now < _SCHEDULE_ANCHOR_BASE:
        return _SCHEDULE_ANCHOR_BASE
    elapsed_s = (now - _SCHEDULE_ANCHOR_BASE).total_seconds()
    period_s = PREDICT_ROUND_INTERVAL_SECONDS
    n = int(elapsed_s // period_s)
    return _SCHEDULE_ANCHOR_BASE + timedelta(seconds=period_s * n)


def _slot_in_period(period_base: datetime, strategy_key: str) -> datetime:
    offset_min = STRATEGY_SCHEDULE_OFFSETS[strategy_key]
    return period_base + timedelta(minutes=offset_min)


def scheduled_slot_for_now(
    strategy_key: str,
    now: Optional[datetime] = None,
) -> datetime:
    """当前 2h 周期内该策略的固定槽位时刻 (UTC naive)."""
    if strategy_key not in STRATEGY_SCHEDULE_OFFSETS:
        raise KeyError(f"unknown strategy_key: {strategy_key}")

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    return _slot_in_period(_period_base_for_now(now), strategy_key)


def next_scheduled_slot(
    strategy_key: str,
    after: Optional[datetime] = None,
) -> datetime:
    """after 之后的下一个固定槽位."""
    after = after or datetime.now(timezone.utc).replace(tzinfo=None)
    period_base = _period_base_for_now(after)
    slot_this_period = _slot_in_period(period_base, strategy_key)
    if after < slot_this_period:
        return slot_this_period
    next_base = period_base + timedelta(seconds=PREDICT_ROUND_INTERVAL_SECONDS)
    return _slot_in_period(next_base, strategy_key)


def _ai_round_is_due(
    conn,
    *,
    strategy_key: str,
    runs_table: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    manual: bool = False,
    log_tag: str = "AI",
) -> Tuple[bool, str]:
    if manual:
        return True, "manual"

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    slot = scheduled_slot_for_now(strategy_key, now)

    if now < slot:
        remain_s = (slot - now).total_seconds()
        return False, (
            f"未到点 剩余 {remain_s / 60:.0f}min "
            f"(slot={slot.isoformat()} UTC, 锚点21:30北京)"
        )

    with conn.cursor() as cur:
        last_ok = _last_ok_run_at(cur, runs_table)
        needs_run = (not last_ok) or (last_ok < slot)

        next_due = _read_setting_dt(cur, next_due_key)
        if next_due and now < next_due and not needs_run:
            remain_s = (next_due - now).total_seconds()
            return False, (
                f"已认领 剩余 {remain_s / 3600:.2f}h "
                f"(next_due={next_due.isoformat()})"
            )

        if last_ok and last_ok >= slot:
            next_slot = slot + timedelta(seconds=PREDICT_ROUND_INTERVAL_SECONDS)
            if now < next_slot:
                remain_s = (next_slot - now).total_seconds()
                return False, (
                    f"本槽已完成 last_ok={last_ok.isoformat()} "
                    f"下次 {next_slot.isoformat()} UTC "
                    f"(剩余 {remain_s / 60:.0f}min)"
                )

    late_h = (now - slot).total_seconds() / 3600
    if late_h >= 0.5:
        return True, (
            f"逾期补跑 slot={slot.isoformat()} UTC 迟到 {late_h:.1f}h"
        )
    return True, f"固定槽 due slot={slot.isoformat()} UTC (锚点21:30北京)"


def explore_round_is_due(
    conn,
    *,
    strategy_key: str,
    runs_table: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    manual: bool = False,
    log_tag: str = "Explore",
    interval_hours: float = EXPLORE_MIN_INTERVAL_HOURS,
) -> Tuple[bool, str]:
    """主探索固定时刻 2h 防重 (interval_hours 保留兼容, 实际以锚点槽位为准)."""
    _ = interval_hours
    return _ai_round_is_due(
        conn,
        strategy_key=strategy_key,
        runs_table=runs_table,
        next_due_key=next_due_key,
        now=now,
        manual=manual,
        log_tag=log_tag,
    )


def predict_round_is_due(
    conn,
    *,
    strategy_key: str,
    runs_table: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    manual: bool = False,
    log_tag: str = "Predict",
) -> Tuple[bool, str]:
    """预测固定时刻 2h 防重."""
    return _ai_round_is_due(
        conn,
        strategy_key=strategy_key,
        runs_table=runs_table,
        next_due_key=next_due_key,
        now=now,
        manual=manual,
        log_tag=log_tag,
    )


def _claim_next_slot(
    conn,
    *,
    strategy_key: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    log_tag: str = "AI",
) -> datetime:
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    next_due = next_scheduled_slot(strategy_key, now)
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
                (
                    f"{log_tag} 下一轮固定槽 UTC "
                    f"(锚点21:30北京 {_offset_label(STRATEGY_SCHEDULE_OFFSETS[strategy_key])})"
                ),
            ),
        )
    logger.info(
        f"[{log_tag}] 已认领固定槽, next_due_utc={value} "
        f"(offset={_offset_label(STRATEGY_SCHEDULE_OFFSETS[strategy_key])})"
    )
    return next_due


def explore_claim_next_slot(
    conn,
    *,
    strategy_key: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    log_tag: str = "Explore",
) -> datetime:
    return _claim_next_slot(
        conn,
        strategy_key=strategy_key,
        next_due_key=next_due_key,
        now=now,
        log_tag=log_tag,
    )


def predict_claim_next_slot(
    conn,
    *,
    strategy_key: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    log_tag: str = "Predict",
) -> datetime:
    return _claim_next_slot(
        conn,
        strategy_key=strategy_key,
        next_due_key=next_due_key,
        now=now,
        log_tag=log_tag,
    )
