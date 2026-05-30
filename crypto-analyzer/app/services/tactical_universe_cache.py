"""兼容层 — 战术探索改读 explore_prepared_bundle."""
from __future__ import annotations

from app.services.explore_prepared_bundle import (
    get_explore_prepared_bundle,
    rebuild_and_persist,
)

# 供测试脚本 invalidate
def invalidate() -> None:
    import app.services.explore_prepared_bundle as mod
    mod._memo = None


def warm(conn, log_tag: str = "战术调度") -> int:
    """调度侧已由 refresh_explore_shared_data 预计算；此处仅加载."""
    universe, _, _ = get_explore_prepared_bundle(
        conn, log_tag, allow_rebuild=False,
    )
    if not universe:
        stat = rebuild_and_persist()
        return int(stat.get("symbol_count") or 0)
    return len(universe)


def get_prepared_universe(conn, log_tag: str):
    allow = False
    return get_explore_prepared_bundle(
        conn, log_tag, allow_rebuild=allow,
    )
