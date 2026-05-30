"""
战术探索统一调度 — 每策略 4h 必跑 + 15min 轮询 + DB next_due (重启不丢).

10 任务首次初始化 next_due 按 24min 错峰; 之后每轮认领 now+4h.
同一 poll 若多个任务 overdue, 先跑 next_due 最早的一个, 避免 LLM 并发风暴.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional, Tuple

from loguru import logger

from app.services.ai_predict_schedule import _read_setting_dt
from app.services.ai_tactical_explore_schedule import (
    TACTICAL_BLOCK_FIRST_OFFSET_MIN,
    TACTICAL_POLL_MINUTES,
    TACTICAL_ROUND_INTERVAL_HOURS,
    TACTICAL_SLOT_STEP_MINUTES,
    ensure_tactical_next_due,
    tactical_next_due_key,
    tactical_round_is_due,
)
from app.services.deepseek_reversal_explore_worker import run_deepseek_reversal_explore_round
from app.services.gemini_explore_worker import _connect
from app.services.gemini_reversal_explore_worker import run_gemini_reversal_explore_round
from app.services.tactical_explore_workers import (
    run_deepseek_chase_explore_round,
    run_deepseek_dump_explore_round,
    run_deepseek_pullback_explore_round,
    run_deepseek_rebound_explore_round,
    run_gemini_chase_explore_round,
    run_gemini_dump_explore_round,
    run_gemini_pullback_explore_round,
    run_gemini_rebound_explore_round,
)


@dataclass(frozen=True)
class TacticalScheduleJob:
    label: str
    run_fn: Callable[[str], Optional[int]]
    slot_index: int  # 0..9, 仅用于首次 next_due 错峰
    source: str
    runs_table: str

    @property
    def next_due_key(self) -> str:
        return tactical_next_due_key(self.source)

    @property
    def stagger_offset_min(self) -> int:
        return TACTICAL_BLOCK_FIRST_OFFSET_MIN + self.slot_index * TACTICAL_SLOT_STEP_MINUTES


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


TACTICAL_SCHEDULE_JOBS: List[TacticalScheduleJob] = [
    TacticalScheduleJob("Gemini顶空底多", run_gemini_reversal_explore_round, 0,
                        "gemini_reversal", "gemini_reversal_explore_runs"),
    TacticalScheduleJob("Gemini回调做多", run_gemini_pullback_explore_round, 1,
                        "gemini_pullback", "gemini_pullback_explore_runs"),
    TacticalScheduleJob("Gemini反弹做空", run_gemini_rebound_explore_round, 2,
                        "gemini_rebound", "gemini_rebound_explore_runs"),
    TacticalScheduleJob("Gemini追涨做多", run_gemini_chase_explore_round, 3,
                        "gemini_chase", "gemini_chase_explore_runs"),
    TacticalScheduleJob("Gemini杀跌做空", run_gemini_dump_explore_round, 4,
                        "gemini_dump", "gemini_dump_explore_runs"),
    TacticalScheduleJob("DeepSeek顶空底多", run_deepseek_reversal_explore_round, 5,
                        "deepseek_reversal", "deepseek_reversal_explore_runs"),
    TacticalScheduleJob("DeepSeek回调做多", run_deepseek_pullback_explore_round, 6,
                        "deepseek_pullback", "deepseek_pullback_explore_runs"),
    TacticalScheduleJob("DeepSeek反弹做空", run_deepseek_rebound_explore_round, 7,
                        "deepseek_rebound", "deepseek_rebound_explore_runs"),
    TacticalScheduleJob("DeepSeek追涨做多", run_deepseek_chase_explore_round, 8,
                        "deepseek_chase", "deepseek_chase_explore_runs"),
    TacticalScheduleJob("DeepSeek杀跌做空", run_deepseek_dump_explore_round, 9,
                        "deepseek_dump", "deepseek_dump_explore_runs"),
]


def _collect_due_jobs(now: datetime) -> List[Tuple[TacticalScheduleJob, str, Optional[datetime]]]:
    """返回 (job, reason, next_due_dt) 列表."""
    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[战术调度] DB 连接失败: {e}")
        return []

    due: List[Tuple[TacticalScheduleJob, str, Optional[datetime]]] = []
    try:
        with conn.cursor() as cur:
            for job in TACTICAL_SCHEDULE_JOBS:
                ensure_tactical_next_due(
                    conn,
                    source=job.source,
                    runs_table=job.runs_table,
                    slot_index=job.slot_index,
                    log_tag=job.label,
                    now=now,
                )
                ok, reason = tactical_round_is_due(
                    conn,
                    runs_table=job.runs_table,
                    next_due_key=job.next_due_key,
                    now=now,
                    manual=False,
                    log_tag=job.label,
                )
                if not ok:
                    continue
                nd = _read_setting_dt(cur, job.next_due_key)
                due.append((job, reason, nd))
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return due


def run_tactical_explore_poll(triggered_by: str = "scheduler") -> None:
    """
    15min 轮询：到点 (next_due) 的任务启动 worker.
    多任务同时 overdue 时本轮只启动 next_due 最早的一个.
    """
    now = _utc_now_naive()
    due_jobs = _collect_due_jobs(now)
    if not due_jobs:
        return

    due_jobs.sort(key=lambda x: x[2] or now)
    job, reason, nd = due_jobs[0]
    if len(due_jobs) > 1:
        logger.info(
            f"[战术调度] {len(due_jobs)} 个任务到点, 本轮先跑 {job.label}, "
            f"其余 {len(due_jobs) - 1} 个下轮 poll 补跑"
        )
    else:
        logger.info(f"[战术调度] 认领 {job.label} ({reason}, next_due={nd})")

    def _wrapper(j: TacticalScheduleJob = job) -> None:
        try:
            j.run_fn(triggered_by=triggered_by)
        except Exception as e:
            logger.error(f"[战术调度] {j.label} 异常: {e}", exc_info=True)

    threading.Thread(
        target=_wrapper,
        daemon=True,
        name=f"Tactical_{job.label}",
    ).start()


def format_schedule_plan() -> str:
    """可打印的调度表（文档/日志用）."""
    lines = [
        f"战术探索: 每策略 {TACTICAL_ROUND_INTERVAL_HOURS}h 必跑, "
        f"每 {TACTICAL_POLL_MINUTES}min 轮询, next_due 存 system_settings",
        f"首次错峰: 槽步进 {TACTICAL_SLOT_STEP_MINUTES}min, 首槽 +{TACTICAL_BLOCK_FIRST_OFFSET_MIN}min",
    ]
    for j in TACTICAL_SCHEDULE_JOBS:
        lines.append(
            f"  [{j.slot_index}] {j.label}: source={j.source}, "
            f"next_due_key={j.next_due_key}, 错峰 +{j.stagger_offset_min}min"
        )
    return "\n".join(lines)
