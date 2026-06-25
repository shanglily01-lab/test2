"""探索/预测页统计 — 优先读 position_stats_snapshot，避免每次打开页面扫 futures_positions."""
from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger


def _snapshot_stats(source: str, account_id: int = 2) -> Optional[Dict[str, Any]]:
    try:
        from app.services.data_cache_service import get_position_stats

        row = get_position_stats(source, account_id)
        if not row:
            return None
        closed_30d = int(row.get("closed_30d") or 0)
        wins = int(row.get("wins_30d") or 0)
        losses = int(row.get("losses_30d") or 0)
        total_pnl = float(row.get("pnl_30d") or 0)
        return {
            "open_count": int(row.get("open_count") or 0),
            "closed_30d": closed_30d,
            "total_trades": closed_30d,
            "wins": wins,
            "losses": losses,
            "breakeven": max(0, closed_30d - wins - losses),
            "win_rate": float(row.get("win_rate_30d") or 0),
            "total_realized_pnl": round(total_pnl, 2),
            "avg_realized_pnl": round(total_pnl / closed_30d, 2) if closed_30d else 0.0,
            "from_cache": True,
        }
    except Exception as e:
        logger.debug(f"[explore_page_stats] snapshot miss {source}: {e}")
        return None


def _live_closed_stats(cur, source: str, days: int, account_id: int = 2) -> Dict[str, Any]:
    from app.utils.pnl_stats import PNL_COUNT_SELECT, parse_pnl_counts

    cur.execute(
        f"""
        SELECT
            {PNL_COUNT_SELECT},
            COALESCE(SUM(realized_pnl), 0) AS total_pnl,
            COALESCE(AVG(realized_pnl), 0) AS avg_pnl
        FROM futures_positions
        WHERE source=%s AND status='closed' AND account_id=%s
          AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
        """,
        (source, account_id, days),
    )
    row = cur.fetchone() or {}
    counts = parse_pnl_counts(row)
    total_pnl = float(row.get("total_pnl") or 0)
    avg_pnl = float(row.get("avg_pnl") or 0)
    return {
        "total_trades": counts["total_trades"],
        "wins": counts["wins"],
        "losses": counts["losses"],
        "breakeven": counts["breakeven"],
        "win_rate": counts["win_rate"],
        "total_realized_pnl": round(total_pnl, 2),
        "avg_realized_pnl": round(avg_pnl, 2),
        "from_cache": False,
    }


def _live_open_floating(cur, source: str, account_id: int = 2) -> float:
    cur.execute(
        """
        SELECT COALESCE(SUM(unrealized_pnl), 0) AS floating_pnl
        FROM futures_positions
        WHERE source=%s AND status='open' AND account_id=%s
        """,
        (source, account_id),
    )
    row = cur.fetchone() or {}
    return float(row.get("floating_pnl") or 0)


def _live_open_count(cur, source: str, account_id: int = 2) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt FROM futures_positions
        WHERE source=%s AND status='open' AND account_id=%s
        """,
        (source, account_id),
    )
    return int((cur.fetchone() or {}).get("cnt") or 0)


def _live_closed_count_30d(cur, source: str, account_id: int = 2) -> int:
    cur.execute(
        """
        SELECT COUNT(*) AS cnt FROM futures_positions
        WHERE source=%s AND status='closed' AND account_id=%s
          AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
        """,
        (source, account_id),
    )
    return int((cur.fetchone() or {}).get("cnt") or 0)


def status_counts(source: str, cur=None, account_id: int = 2) -> Dict[str, int]:
    """status 卡片用的 open / closed_30d；优先快照，避免双 COUNT 扫主表."""
    snap = _snapshot_stats(source, account_id)
    if snap:
        return {
            "open_positions": int(snap.get("open_count") or 0),
            "closed_positions_30d": int(snap.get("closed_30d") or 0),
        }
    if cur is None:
        raise ValueError("status_counts fallback requires cursor")
    return {
        "open_positions": _live_open_count(cur, source, account_id),
        "closed_positions_30d": _live_closed_count_30d(cur, source, account_id),
    }


def explore_stats_payload(
    source: str,
    days: int = 30,
    cur=None,
    account_id: int = 2,
) -> Dict[str, Any]:
    """/stats 返回体；days=30 时读快照，自定义天数才回退实时聚合."""
    closed: Dict[str, Any]
    if days == 30:
        snap = _snapshot_stats(source, account_id)
        if snap:
            closed = snap
        elif cur is None:
            closed = {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "win_rate": 0.0,
                "total_realized_pnl": 0.0,
                "avg_realized_pnl": 0.0,
                "from_cache": False,
            }
        else:
            closed = _live_closed_stats(cur, source, days, account_id)
    else:
        if cur is None:
            raise ValueError("explore_stats_payload live fallback requires cursor")
        closed = _live_closed_stats(cur, source, days, account_id)

    floating = 0.0
    if cur is not None:
        floating = _live_open_floating(cur, source, account_id)

    total_pnl = float(closed.get("total_realized_pnl") or 0)
    return {
        "total_trades": closed.get("total_trades", 0),
        "wins": closed.get("wins", 0),
        "losses": closed.get("losses", 0),
        "breakeven": closed.get("breakeven", 0),
        "win_rate": closed.get("win_rate", 0.0),
        "total_realized_pnl": closed.get("total_realized_pnl", 0.0),
        "avg_realized_pnl": closed.get("avg_realized_pnl", 0.0),
        "floating_pnl": round(floating, 2),
        "total_pnl": round(total_pnl + floating, 2),
        "days": days,
        "stats_from_cache": bool(closed.get("from_cache")),
    }
