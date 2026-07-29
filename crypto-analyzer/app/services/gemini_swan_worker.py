"""Gemini 红黑天鹅 worker — 已下线兼容壳.

调度已停；kill switch gemini_swan_enabled 强制关闭。
共用工具请 import explore_universe_utils / gemini_llm_config。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from app.services.explore_universe_utils import (  # noqa: F401
    EXCLUDE_BASES,
    MIN_QUOTE_VOLUME,
    STABLECOINS,
    TOP_FUNDING,
    TOP_MOVER,
    _base_of,
    _is_excluded,
    _merge_universe,
    _read_setting,
)
from app.services.gemini_llm_config import (  # noqa: F401
    GEMINI_API_KEY,
    GEMINI_MODEL,
    GEMINI_TIMEOUT_S,
)

ROUND_INTERVAL_S = 60
DEFAULT_ROUNDS = 3


def run_swan_round(
    force_rounds: Optional[int] = None,
    triggered_by: str = "scheduler",
) -> Optional[int]:
    """已下线：不再调用 Gemini / 写 swan 表。"""
    logger.info(
        f"[swan] 已下线，跳过 run_swan_round (triggered_by={triggered_by})"
    )
    return None


__all__ = [
    "EXCLUDE_BASES",
    "STABLECOINS",
    "MIN_QUOTE_VOLUME",
    "TOP_MOVER",
    "TOP_FUNDING",
    "ROUND_INTERVAL_S",
    "DEFAULT_ROUNDS",
    "GEMINI_MODEL",
    "GEMINI_API_KEY",
    "GEMINI_TIMEOUT_S",
    "_base_of",
    "_is_excluded",
    "_merge_universe",
    "_read_setting",
    "run_swan_round",
]


if __name__ == "__main__":
    rid = run_swan_round(triggered_by="manual")
    print(f"run_id={rid}")
