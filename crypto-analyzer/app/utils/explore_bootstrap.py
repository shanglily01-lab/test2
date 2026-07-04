"""探索页首屏 bootstrap — 单连接返回 status/runs/open/stats，避免并行打满 API 连接池."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.utils.explore_db_guard import explore_db_cursor
from app.utils.explore_list_queries import fetch_open_positions, fetch_runs_list
from app.utils.explore_page_stats import explore_stats_payload, status_counts
from app.utils.futures_symbol import futures_symbol_rating_canonical
from app.utils.position_display import canonicalize_symbol_fields


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_live_positions(positions: List[dict]) -> Tuple[List[dict], Dict[str, Any]]:
    """用持仓表快照展示浮盈，不在页面请求内逐仓阻塞行情源."""
    if not positions:
        return [], {
            "total_unrealized_pnl": 0.0,
            "total_margin_used": 0.0,
            "total_unrealized_pnl_pct": 0.0,
            "open_count": 0,
        }

    result = []
    for p in positions:
        symbol = futures_symbol_rating_canonical(p["symbol"])
        side = p["position_side"]
        entry = float(p["entry_price"] or 0)
        qty = float(p["quantity"] or 0)
        margin = float(p["margin"] or 0)
        live_price = _to_float(p.get("mark_price"))
        unrealized_pnl = _to_float(p.get("unrealized_pnl"))
        unrealized_pnl_pct = _to_float(p.get("unrealized_pnl_pct"))

        if unrealized_pnl is None and live_price is not None and live_price > 0:
            if side == "LONG":
                unrealized_pnl = (live_price - entry) * qty
            else:
                unrealized_pnl = (entry - live_price) * qty
        if unrealized_pnl_pct is None and unrealized_pnl is not None:
            unrealized_pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0

        result.append({
            "id": p["id"],
            "symbol": symbol,
            "position_side": side,
            "leverage": p["leverage"],
            "quantity": qty,
            "entry_price": entry,
            "mark_price": live_price,
            "margin": margin,
            "stop_loss_price": float(p["stop_loss_price"]) if p["stop_loss_price"] else None,
            "take_profit_price": float(p["take_profit_price"]) if p["take_profit_price"] else None,
            "unrealized_pnl": round(unrealized_pnl, 4) if unrealized_pnl is not None else None,
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
            "open_time": p["open_time"].isoformat() if p.get("open_time") and hasattr(p["open_time"], "isoformat") else p.get("open_time"),
            "planned_close_time": (
                p["planned_close_time"].isoformat()
                if p.get("planned_close_time") and hasattr(p["planned_close_time"], "isoformat")
                else p.get("planned_close_time")
            ),
            "entry_reason": p.get("entry_reason"),
        })

    total_pnl = sum((r["unrealized_pnl"] or 0) for r in result)
    total_margin = sum(r["margin"] for r in result)
    total_pnl_pct = (total_pnl / total_margin * 100) if total_margin > 0 else 0
    summary = {
        "total_unrealized_pnl": round(total_pnl, 2),
        "total_margin_used": round(total_margin, 2),
        "total_unrealized_pnl_pct": round(total_pnl_pct, 2),
        "open_count": len(result),
    }
    return result, summary


def _serialize_run(row: Optional[dict]) -> Optional[dict]:
    if not row:
        return None
    out = dict(row)
    for k, v in list(out.items()):
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
            try:
                out[k] = float(v)
            except Exception:
                pass
    return out


def explore_bootstrap_payload(
    *,
    source: str,
    enabled_key: str,
    runs_table: str,
    leverage: int,
    confidence_threshold: float,
    margin_usd: int = 500,
    entry_grace_min: int = 30,
    runs_limit: int = 20,
) -> Dict[str, Any]:
    """单次游标读完首屏数据（status + runs + open），stats 走 data_cache 快照."""
    from app.services.system_settings_loader import get_strategy_open_params

    with explore_db_cursor() as cur:
        cur.execute(
            "SELECT setting_value FROM system_settings "
            "WHERE setting_key=%s LIMIT 1",
            (enabled_key,),
        )
        row = cur.fetchone()
        enabled_raw = str((row or {}).get("setting_value", "0")).strip().lower()
        enabled = enabled_raw in ("1", "true", "yes", "on")

        cur.execute(
            f"SELECT id, asof_utc, model, universe_size, trades_opened, "
            f"       elapsed_s, status, error_msg, triggered_by, created_at "
            f"FROM `{runs_table}` ORDER BY id DESC LIMIT 1"
        )
        last_run = _serialize_run(cur.fetchone())

        runs = fetch_runs_list(cur, runs_table, runs_limit)
        for r in runs:
            for k, v in list(r.items()):
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
                elif hasattr(v, "__float__") and not isinstance(v, (int, float, bool)):
                    try:
                        r[k] = float(v)
                    except Exception:
                        pass

        open_rows = fetch_open_positions(cur, source, 200)

    counts = status_counts(source)
    stats = explore_stats_payload(source, days=30)
    live, open_summary = build_live_positions(open_rows)
    canonicalize_symbol_fields(live)

    _params = get_strategy_open_params()
    status_data = {
        "enabled": enabled,
        "last_run": last_run,
        "open_positions": counts["open_positions"],
        "closed_positions_30d": counts["closed_positions_30d"],
        "max_positions": _params["max_positions"],
        "params": {
            "margin_usd": margin_usd,
            "leverage": leverage,
            "hold_hours": _params["hold_hours"],
            "sl_pct": _params["sl_pct"],
            "tp_pct": _params["tp_pct"],
            "confidence_threshold": confidence_threshold,
            "entry_grace_min": entry_grace_min,
        },
    }
    return {
        "status": status_data,
        "runs": runs,
        "open_positions": live,
        "open_summary": open_summary,
        "stats": stats,
    }
