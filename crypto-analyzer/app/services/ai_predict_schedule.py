"""Gemini / DeepSeek 探索+预测 — 距上次成功 + max_hold_hours 调度.

调度周期: system_settings.max_hold_hours（2~8h，与持仓时长共用）。
规则: 距上次 status='ok' 的 asof_utc ≥ 周期小时数则 due；无成功记录则立即 due。
error/skipped 不推迟下一轮。认领 next_due = now + 周期（防短窗重复认领）；
若已超过周期仍未 ok，needs_run 覆盖 next_due，允许 5/10min 轮询重试。

调度器 5/10 分钟轮询 + worker 内 next_due 认领防重。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from loguru import logger

from app.services.system_settings_loader import get_max_hold_hours

PREDICT_SCHEDULE_POLL_MINUTES = 5

GEMINI_EXPLORE_NEXT_DUE_KEY = "gemini_explore_next_due_utc"
DEEPSEEK_EXPLORE_NEXT_DUE_KEY = "deepseek_explore_next_due_utc"
GEMINI_PREDICT_NEXT_DUE_KEY = "gemini_predict_next_due_utc"
DEEPSEEK_PREDICT_NEXT_DUE_KEY = "deepseek_predict_next_due_utc"

# 兼容旧校验/文档引用（错峰已不再参与 due；保留常量避免外部 import 崩）
GEMINI_DEEPSEEK_STAGGER_HOURS = 1
SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN = 15
STRATEGY_SCHEDULE_OFFSETS: Dict[str, int] = {
    "gemini_explore": 0,
    "gemini_predict": SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN,
    "deepseek_explore": GEMINI_DEEPSEEK_STAGGER_HOURS * 60,
    "deepseek_predict": GEMINI_DEEPSEEK_STAGGER_HOURS * 60
    + SAME_TEACHER_EXPLORE_PREDICT_GAP_MIN,
}

_AI_RUNS_TABLES: Dict[str, str] = {
    "gemini_explore": "gemini_explore_runs",
    "gemini_predict": "gemini_predict_runs",
    "deepseek_explore": "deepseek_explore_runs",
    "deepseek_predict": "deepseek_predict_runs",
}


def get_ai_round_interval_hours() -> int:
    """AI 探索/预测调度周期（小时），读 max_hold_hours。"""
    return get_max_hold_hours()


def get_ai_round_interval_seconds() -> int:
    return get_ai_round_interval_hours() * 3600


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


def next_due_after(
    after: Optional[datetime] = None,
    *,
    now: Optional[datetime] = None,
) -> datetime:
    """after（通常 last_ok 或认领时刻）起算下一轮到期时刻；after 为空则 now."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    base = after or now
    return base + timedelta(seconds=get_ai_round_interval_seconds())


def scheduled_slot_for_now(
    strategy_key: str,
    now: Optional[datetime] = None,
) -> datetime:
    """兼容旧 API：无墙钟槽，返回 now（调用方应改用 last_ok 间隔）."""
    if strategy_key not in STRATEGY_SCHEDULE_OFFSETS:
        raise KeyError(f"unknown strategy_key: {strategy_key}")
    return now or datetime.now(timezone.utc).replace(tzinfo=None)


def next_scheduled_slot(
    strategy_key: str,
    after: Optional[datetime] = None,
) -> datetime:
    """兼容旧 API：after + 周期（不再做墙钟对齐）."""
    if strategy_key not in STRATEGY_SCHEDULE_OFFSETS:
        raise KeyError(f"unknown strategy_key: {strategy_key}")
    return next_due_after(after)


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
    _ = strategy_key
    if manual:
        return True, "manual"

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    period_s = get_ai_round_interval_seconds()
    period_h = get_ai_round_interval_hours()

    with conn.cursor() as cur:
        last_ok = _last_ok_run_at(cur, runs_table)
        next_due = _read_setting_dt(cur, next_due_key)

        if last_ok:
            earliest = last_ok + timedelta(seconds=period_s)
            if now < earliest:
                remain_s = (earliest - now).total_seconds()
                return False, (
                    f"距上次成功不足 {period_h}h 剩余 {remain_s / 60:.0f}min "
                    f"(last_ok={last_ok.isoformat()}, "
                    f"下次={earliest.isoformat()} UTC)"
                )
            late_h = (now - earliest).total_seconds() / 3600
        else:
            # 无 ok：立即 due。若仅有未来 next_due（旧墙钟遗留）不挡补跑；
            # 进程内 running lock 防并发。
            late_h = None
            earliest = now
            _ = next_due

    if late_h is not None and late_h >= 0.5:
        return True, (
            f"逾期补跑 last_ok={(last_ok.isoformat() if last_ok else '无')} "
            f"已过期 {late_h:.1f}h (周期{period_h}h)"
        )
    if last_ok is None:
        return True, f"无成功记录, 立即执行 (周期{period_h}h)"
    return True, (
        f"间隔到期 last_ok={last_ok.isoformat()} "
        f"下次={earliest.isoformat()} UTC (周期{period_h}h)"
    )


def explore_round_is_due(
    conn,
    *,
    strategy_key: str,
    runs_table: str,
    next_due_key: str,
    now: Optional[datetime] = None,
    manual: bool = False,
    log_tag: str = "Explore",
    interval_hours: Optional[float] = None,
) -> Tuple[bool, str]:
    """主探索：距上次 ok ≥ max_hold_hours（interval_hours 保留兼容，忽略）."""
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
    """主预测：距上次 ok ≥ max_hold_hours."""
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
    _ = strategy_key
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    next_due = next_due_after(now, now=now)
    value = next_due.strftime("%Y-%m-%dT%H:%M:%S")
    period_h = get_ai_round_interval_hours()
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
                f"{log_tag} 下一轮 UTC (距认领 +{period_h}h)",
            ),
        )
    logger.info(
        f"[{log_tag}] 已认领下一轮, next_due_utc={value} (周期={period_h}h)"
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


_AI_NEXT_DUE_KEYS: Dict[str, str] = {
    "gemini_explore": GEMINI_EXPLORE_NEXT_DUE_KEY,
    "gemini_predict": GEMINI_PREDICT_NEXT_DUE_KEY,
    "deepseek_explore": DEEPSEEK_EXPLORE_NEXT_DUE_KEY,
    "deepseek_predict": DEEPSEEK_PREDICT_NEXT_DUE_KEY,
}


def realign_stale_next_due_slots(
    conn,
    *,
    now: Optional[datetime] = None,
    grace_seconds: int = 90,
) -> int:
    """部署或 max_hold_hours 变更后：next_due 对齐为 last_ok+周期（无 ok 则 now）."""
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    period_s = get_ai_round_interval_seconds()
    threshold = period_s / 2 + grace_seconds
    fixed = 0
    period_h = get_ai_round_interval_hours()

    with conn.cursor() as cur:
        for strategy_key, next_due_key in _AI_NEXT_DUE_KEYS.items():
            stored = _read_setting_dt(cur, next_due_key)
            if stored is None:
                continue
            runs_table = _AI_RUNS_TABLES.get(strategy_key)
            last_ok = _last_ok_run_at(cur, runs_table) if runs_table else None
            canonical = next_due_after(last_ok, now=now) if last_ok else now
            # 已逾期：校正到 now，避免墙钟遗留的未来 next_due 挡补跑
            if last_ok and (now - last_ok).total_seconds() >= period_s:
                canonical = now
            drift_s = abs((stored - canonical).total_seconds())
            if drift_s <= threshold:
                continue
            value = canonical.strftime("%Y-%m-%dT%H:%M:%S")
            cur.execute(
                """
                INSERT INTO system_settings
                  (setting_key, setting_value, description, updated_by, updated_at)
                VALUES (%s, %s, %s, 'ai_predict_schedule_realign', NOW())
                ON DUPLICATE KEY UPDATE
                  setting_value = VALUES(setting_value),
                  updated_by = 'ai_predict_schedule_realign',
                  updated_at = NOW()
                """,
                (
                    next_due_key,
                    value,
                    f"{strategy_key} 下一轮 UTC (last_ok+{period_h}h 间隔调度)",
                ),
            )
            logger.info(
                f"[AI调度校正] {strategy_key}: {stored.isoformat()} → {value} "
                f"(drift={drift_s / 60:.0f}min, 周期={period_h}h)"
            )
            fixed += 1
    return fixed
