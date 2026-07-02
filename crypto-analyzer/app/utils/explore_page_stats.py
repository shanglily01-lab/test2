"""探索/预测页统计 — 只读 position_stats_snapshot，禁止 API 回退扫 futures_positions."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from loguru import logger

_MEM_CACHE: Dict[str, tuple] = {}
_MEM_TTL_S = 120


def _empty_counts() -> Dict[str, int]:
    return {"open_positions": 0, "closed_positions_30d": 0}


def _empty_stats(days: int = 30) -> Dict[str, Any]:
    return {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "win_rate": 0.0,
        "total_realized_pnl": 0.0,
        "avg_realized_pnl": 0.0,
        "floating_pnl": 0.0,
        "total_pnl": 0.0,
        "days": days,
        "stats_from_cache": False,
        "stats_stale": True,
    }


def _snapshot_stats(source: str, account_id: int = 2) -> Optional[Dict[str, Any]]:
    cache_key = f"{source}:{account_id}"
    now = time.time()
    cached = _MEM_CACHE.get(cache_key)
    if cached and now - cached[0] < _MEM_TTL_S:
        return cached[1]

    try:
        from app.services.data_cache_service import get_position_stats

        row = get_position_stats(source, account_id)
        if not row:
            return None
        closed_30d = int(row.get("closed_30d") or 0)
        wins = int(row.get("wins_30d") or 0)
        losses = int(row.get("losses_30d") or 0)
        total_pnl = float(row.get("pnl_30d") or 0)
        payload = {
            "open_count": int(row.get("open_count") or 0),
            "closed_30d": closed_30d,
            "total_trades": closed_30d,
            "wins": wins,
            "losses": losses,
            "breakeven": max(0, closed_30d - wins - losses),
            "win_rate": float(row.get("win_rate_30d") or 0),
            "total_realized_pnl": round(total_pnl, 2),
            "avg_realized_pnl": round(total_pnl / closed_30d, 2) if closed_30d else 0.0,
            "floating_pnl": round(float(row.get("floating_pnl") or 0), 2),
            "updated_at": row.get("updated_at"),
            "from_cache": True,
        }
        _MEM_CACHE[cache_key] = (now, payload)
        return payload
    except Exception as e:
        logger.warning(f"[explore_page_stats] snapshot read failed {source}: {e}")
        return None


def status_counts(source: str, account_id: int = 2) -> Dict[str, int]:
    """status 卡片用的 open / closed_30d；仅读快照，不回退 COUNT 主表."""
    snap = _snapshot_stats(source, account_id)
    if snap:
        return {
            "open_positions": int(snap.get("open_count") or 0),
            "closed_positions_30d": int(snap.get("closed_30d") or 0),
        }
    logger.warning(
        f"[explore_page_stats] snapshot miss for {source} — "
        "returning zeros (run refresh_position_stats / POST /api/data-cache/refresh/position-stats)"
    )
    return _empty_counts()


def explore_stats_payload(
    source: str,
    days: int = 30,
    account_id: int = 2,
) -> Dict[str, Any]:
    """/stats 返回体；days=30 时只读快照，不回退 futures_positions 聚合."""
    if days != 30:
        logger.warning(f"[explore_page_stats] days={days} not supported on explore page; use review API")
        base = _empty_stats(days)
        base["stats_stale"] = True
        return base

    snap = _snapshot_stats(source, account_id)
    if not snap:
        logger.warning(f"[explore_page_stats] stats snapshot miss for {source}")
        return _empty_stats(days)

    floating = float(snap.get("floating_pnl") or 0)
    realized = float(snap.get("total_realized_pnl") or 0)
    return {
        "total_trades": snap.get("total_trades", 0),
        "wins": snap.get("wins", 0),
        "losses": snap.get("losses", 0),
        "breakeven": snap.get("breakeven", 0),
        "win_rate": snap.get("win_rate", 0.0),
        "total_realized_pnl": snap.get("total_realized_pnl", 0.0),
        "avg_realized_pnl": snap.get("avg_realized_pnl", 0.0),
        "floating_pnl": round(floating, 2),
        "total_pnl": round(realized + floating, 2),
        "days": days,
        "stats_from_cache": True,
        "stats_stale": False,
        "snapshot_updated_at": (
            snap["updated_at"].isoformat()
            if snap.get("updated_at") and hasattr(snap["updated_at"], "isoformat")
            else snap.get("updated_at")
        ),
    }


def invalidate_snapshot_mem_cache(source: str = None, account_id: int = 2) -> None:
    """refresh_position_stats 成功后可选调用."""
    if source:
        _MEM_CACHE.pop(f"{source}:{account_id}", None)
    else:
        _MEM_CACHE.clear()
