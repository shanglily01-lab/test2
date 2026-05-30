"""
战术探索统一调度 — 4h 周期、24min 错峰槽位、15min 轮询认领。

与 gemini/deepseek 主探索 (2h) 错开：战术槽位从每 4h 块的 +12min 起，每 24min 一个，
同一时刻最多 1 个战术任务进入 due 窗口，避免 LLM/DB 并发卡顿。
各 worker 仍有 per-source 锁 + min_interval_hours 防重。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, List, Optional

from loguru import logger

from app.services.deepseek_reversal_explore_worker import run_deepseek_reversal_explore_round
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

# 与 scheduler 中 schedule.every(15).minutes 一致
TACTICAL_POLL_MINUTES = 15
TACTICAL_CYCLE_HOURS = 4.0
# 槽位宽度 = 轮询间隔，保证每槽至少被 poll 命中一次
SLOT_WINDOW_MINUTES = TACTICAL_POLL_MINUTES
# 10 个任务 × 24min = 240min = 4h
SLOT_STEP_MINUTES = 24
# 块内首槽偏移，避开整点 gemini/deepseek 主探索 (2h 常在 :00/:02 附近)
BLOCK_FIRST_OFFSET_MIN = 12


@dataclass(frozen=True)
class TacticalScheduleJob:
    label: str
    run_fn: Callable[[str], Optional[int]]
    slot_index: int  # 0..9 within 4h block
    interval_hours: float = TACTICAL_CYCLE_HOURS

    @property
    def offset_min(self) -> int:
        return (BLOCK_FIRST_OFFSET_MIN + self.slot_index * SLOT_STEP_MINUTES) % int(
            TACTICAL_CYCLE_HOURS * 60
        )


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def minutes_into_cycle(now: datetime, cycle_hours: float = TACTICAL_CYCLE_HOURS) -> float:
    cycle_sec = cycle_hours * 3600.0
    return (now.timestamp() % cycle_sec) / 60.0


def is_job_due(job: TacticalScheduleJob, now: Optional[datetime] = None) -> bool:
    """当前是否处于该任务的认领窗口 [offset, offset + SLOT_WINDOW)."""
    now = now or _utc_now_naive()
    pos = minutes_into_cycle(now, job.interval_hours)
    start = float(job.offset_min)
    end = start + SLOT_WINDOW_MINUTES
    return start <= pos < end


TACTICAL_SCHEDULE_JOBS: List[TacticalScheduleJob] = [
    TacticalScheduleJob("Gemini顶空底多", run_gemini_reversal_explore_round, 0),
    TacticalScheduleJob("Gemini回调做多", run_gemini_pullback_explore_round, 1),
    TacticalScheduleJob("Gemini反弹做空", run_gemini_rebound_explore_round, 2),
    TacticalScheduleJob("Gemini追涨做多", run_gemini_chase_explore_round, 3),
    TacticalScheduleJob("Gemini杀跌做空", run_gemini_dump_explore_round, 4),
    TacticalScheduleJob("DeepSeek顶空底多", run_deepseek_reversal_explore_round, 5),
    TacticalScheduleJob("DeepSeek回调做多", run_deepseek_pullback_explore_round, 6),
    TacticalScheduleJob("DeepSeek反弹做空", run_deepseek_rebound_explore_round, 7),
    TacticalScheduleJob("DeepSeek追涨做多", run_deepseek_chase_explore_round, 8),
    TacticalScheduleJob("DeepSeek杀跌做空", run_deepseek_dump_explore_round, 9),
]


def run_tactical_explore_poll(triggered_by: str = "scheduler") -> None:
    """
    15min 轮询：认领当前槽位到期的任务，每个 due 任务独立后台线程。
    同一 poll 内若多个 due（不应发生），仍逐个 Thread 启动，间隔由槽位设计保证通常为 1 个。
    """
    now = _utc_now_naive()
    due_jobs = [j for j in TACTICAL_SCHEDULE_JOBS if is_job_due(j, now)]
    if not due_jobs:
        return

    for job in due_jobs:
        logger.info(
            f"[战术调度] 认领 {job.label} "
            f"(槽位 offset={job.offset_min}min, 块内={minutes_into_cycle(now):.0f}min)"
        )

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
        f"战术探索: {TACTICAL_CYCLE_HOURS}h 周期, 每 {TACTICAL_POLL_MINUTES}min 轮询, "
        f"槽宽 {SLOT_WINDOW_MINUTES}min, 步进 {SLOT_STEP_MINUTES}min",
    ]
    for j in TACTICAL_SCHEDULE_JOBS:
        lines.append(
            f"  [{j.slot_index}] {j.label}: 块内 {j.offset_min:03d}-"
            f"{j.offset_min + SLOT_WINDOW_MINUTES - 1:03d} min (UTC)"
        )
    return "\n".join(lines)
