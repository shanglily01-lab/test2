"""
DeepSeek 探索 HTTP API.

端点:
- GET  /api/deepseek-explore/bootstrap              首屏单请求 (status+runs+open+stats)
- GET  /api/deepseek-explore/status                 返回 kill switch + 最近一轮元数据
- POST /api/deepseek-explore/toggle                 切换 kill switch (写 system_settings)
- GET  /api/deepseek-explore/runs?limit=20          最近 N 轮运行记录
- GET  /api/deepseek-explore/verdicts?run_id=X      某轮的所有 verdicts
- GET  /api/deepseek-explore/positions?status=open  当前 OPEN / 历史 CLOSED 仓位
- POST /api/deepseek-explore/run-now                手动触发一轮 (调试用,后台线程跑)
"""
from __future__ import annotations

import threading
from typing import Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from app.utils.futures_symbol import futures_symbol_rating_canonical
from app.utils.position_display import canonicalize_symbol_fields
from app.utils.explore_db_guard import explore_db_cursor, explore_positions_cache_get
from app.utils.explore_page_stats import explore_stats_payload, status_counts
from app.utils.explore_list_queries import (
    fetch_closed_positions,
    fetch_open_positions,
    fetch_runs_list,
)


router = APIRouter(prefix="/api/deepseek-explore", tags=["DeepSeek探索"])


def _get_db_config():
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _to_float(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_live_positions(positions):
    """用持仓表里的最新快照展示浮盈, 避免页面请求逐仓阻塞行情源."""
    if not positions:
        return [], {
            "total_unrealized_pnl": 0.0,
            "total_margin_used": 0.0,
            "total_unrealized_pnl_pct": 0.0,
            "open_count": 0,
        }

    result = []
    for p in positions:
        symbol = futures_symbol_rating_canonical(p['symbol'])
        side = p['position_side']
        entry = float(p['entry_price'] or 0)
        qty = float(p['quantity'] or 0)
        margin = float(p['margin'] or 0)
        live_price = _to_float(p.get('mark_price'))
        unrealized_pnl = _to_float(p.get('unrealized_pnl'))
        unrealized_pnl_pct = _to_float(p.get('unrealized_pnl_pct'))

        if unrealized_pnl is None and live_price is not None and live_price > 0:
            if side == 'LONG':
                unrealized_pnl = (live_price - entry) * qty
            else:
                unrealized_pnl = (entry - live_price) * qty
        if unrealized_pnl_pct is None and unrealized_pnl is not None:
            unrealized_pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0

        result.append({
            'id': p['id'],
            'symbol': symbol,
            'position_side': side,
            'leverage': p['leverage'],
            'quantity': qty,
            'entry_price': entry,
            'mark_price': live_price,
            'margin': margin,
            'stop_loss_price': float(p['stop_loss_price']) if p['stop_loss_price'] else None,
            'take_profit_price': float(p['take_profit_price']) if p['take_profit_price'] else None,
            'unrealized_pnl': round(unrealized_pnl, 4) if unrealized_pnl is not None else None,
            'unrealized_pnl_pct': round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
            'open_time': p['open_time'].isoformat() if p['open_time'] else None,
            'planned_close_time': p['planned_close_time'].isoformat() if p['planned_close_time'] else None,
            'entry_reason': p['entry_reason'],
        })

    total_pnl = sum((r['unrealized_pnl'] or 0) for r in result)
    total_margin = sum(r['margin'] for r in result)
    total_pnl_pct = (total_pnl / total_margin * 100) if total_margin > 0 else 0
    summary = {
        "total_unrealized_pnl": round(total_pnl, 2),
        "total_margin_used": round(total_margin, 2),
        "total_unrealized_pnl_pct": round(total_pnl_pct, 2),
        "open_count": len(result),
    }
    return result, summary




# ============================================================
# 首屏 bootstrap（单连接，避免并行打满 API 池）
# ============================================================
@router.get("/bootstrap")
def bootstrap():
    """首屏一次返回 status + runs + open + stats."""
    try:
        from app.utils.explore_bootstrap import explore_bootstrap_payload

        data = explore_bootstrap_payload(
            source="deepseek_explore",
            enabled_key="deepseek_explore_enabled",
            runs_table="deepseek_explore_runs",
            leverage=5,
            confidence_threshold=0.5,
        )
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /bootstrap 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 状态 + 开关
# ============================================================
@router.get("/status")
def status():
    """返回当前 kill switch 状态 + 最近一轮元数据 + 当前 OPEN 持仓数."""
    try:
        with explore_db_cursor() as cur:
            cur.execute(
                "SELECT setting_value FROM system_settings "
                "WHERE setting_key='deepseek_explore_enabled' LIMIT 1"
            )
            row = cur.fetchone()
            enabled_raw = str((row or {}).get('setting_value', '0')).strip().lower()
            enabled = enabled_raw in ('1', 'true', 'yes', 'on')

            cur.execute(
                "SELECT id, asof_utc, model, universe_size, trades_opened, "
                "       elapsed_s, status, error_msg, triggered_by, created_at "
                "FROM deepseek_explore_runs ORDER BY id DESC LIMIT 1"
            )
            last_run = cur.fetchone()

        counts = status_counts("deepseek_explore")
        open_count = counts["open_positions"]
        closed_30d = counts["closed_positions_30d"]

        from app.services.system_settings_loader import get_strategy_open_params
        _params = get_strategy_open_params()
        return {
            "success": True,
            "data": {
                "enabled": enabled,
                "last_run": last_run,
                "open_positions": open_count,
                "closed_positions_30d": closed_30d,
                "max_positions": _params["max_positions"],
                "params": {
                    "margin_usd": 1000,
                    "leverage": 5,
                    "hold_hours": _params["hold_hours"],
                    "sl_pct": _params["sl_pct"],
                    "tp_pct": _params["tp_pct"],
                    "confidence_threshold": 0.5,
                    "entry_grace_min": 30,
                },
            },
        }
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /status 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/toggle")
def toggle(request: ToggleRequest):
    """切换 kill switch. 只改 system_settings."""
    try:
        val = '1' if request.enabled else '0'
        with explore_db_cursor() as cur:
                cur.execute(
                    "INSERT INTO system_settings (setting_key, setting_value) "
                    "VALUES ('deepseek_explore_enabled', %s) "
                    "ON DUPLICATE KEY UPDATE setting_value = %s",
                    (val, val),
                )
        logger.info(f"[DeepSeek探索 API] kill switch -> {val}")
        return {"success": True, "enabled": request.enabled}
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 运行记录
# ============================================================
@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200)):
    """最近 N 轮运行记录."""
    try:
        with explore_db_cursor() as cur:
            runs = fetch_runs_list(cur, "deepseek_explore_runs", limit)
        return {"success": True, "data": runs, "count": len(runs)}
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/detail")
def get_run_detail(
    run_id: int,
    field: Optional[str] = Query(None, pattern="^(prompt|raw)$"),
):
    """返回某轮 prompt 或原始 JSON；field=prompt|raw 时只拉单列，减轻页面卡顿."""
    try:
        from app.utils.explore_api_helpers import fetch_run_detail_row

        with explore_db_cursor() as cur:
                row = fetch_run_detail_row(cur, "deepseek_explore_runs", run_id, field)
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        return {"success": True, "data": row}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /runs/{run_id}/detail 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
def list_verdicts(run_id: int = Query(..., ge=1)):
    """某轮已开仓 verdicts (未开仓的不返回)."""
    try:
        with explore_db_cursor() as cur:
                cur.execute(
                    "SELECT id, run_id, symbol, category, confidence, "
                    "       catalyst, data_signal, risk_note, "
                    "       action_taken, position_id, skip_reason, created_at "
                    "FROM deepseek_explore_verdicts "
                    "WHERE run_id=%s AND action_taken='opened' "
                    "ORDER BY confidence DESC, id ASC",
                    (run_id,),
                )
                verdicts = cur.fetchall()
        for v in verdicts:
            if v.get('confidence') is not None:
                v['confidence'] = float(v['confidence'])
        canonicalize_symbol_fields(verdicts)
        return {"success": True, "data": verdicts, "count": len(verdicts)}
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /verdicts 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 持仓查询
# ============================================================
@router.get("/positions")
def list_positions(
    status: str = Query('open', pattern='^(open|closed)$'),
    limit: int = Query(50, ge=1, le=500),
):
    """deepseek_explore source 的当前 OPEN / 历史 CLOSED 仓位."""
    try:
        with explore_db_cursor() as cur:
            if status == 'open':
                rows = fetch_open_positions(cur, "deepseek_explore", limit)
            else:
                rows = fetch_closed_positions(cur, "deepseek_explore", limit)

        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, 'isoformat'):
                    r[k] = v.isoformat()
                elif hasattr(v, '__float__') and not isinstance(v, (int, float)):
                    try:
                        r[k] = float(v)
                    except Exception:
                        pass
        if status == 'closed':
            from app.utils.position_display import enrich_closed_position_rows
            enrich_closed_position_rows(rows)
        canonicalize_symbol_fields(rows)
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /positions 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 持仓盈亏快照
# ============================================================
@router.get("/positions/live")
def list_positions_live():
    """返回所有 OPEN 仓位的盈亏快照, 不在页面请求内逐仓阻塞行情源."""
    try:
        def _fetch():
            with explore_db_cursor() as cur:
                return fetch_open_positions(cur, "deepseek_explore", 200)

        positions = explore_positions_cache_get("deepseek_explore:open:live", _fetch)
        result, summary = _build_live_positions(positions)
        return {
            "success": True,
            "data": result,
            "count": len(result),
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /positions/live 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 盈亏统计
# ============================================================
@router.get("/stats")
def stats(days: int = Query(30, ge=1, le=365)):
    """返回 DeepSeek 探索的累计盈亏统计."""
    try:
        data = explore_stats_payload("deepseek_explore", days=days)
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] /stats 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 手动触发
# ============================================================
_run_lock = threading.Lock()


def _run_in_background(triggered_by: str):
    try:
        from app.services.deepseek_explore_worker import run_explore_round
        run_explore_round(triggered_by=triggered_by)
    except Exception as e:
        logger.error(f"[DeepSeek探索 API] 后台跑一轮异常: {e}", exc_info=True)
    finally:
        try:
            _run_lock.release()
        except Exception:
            pass


@router.post("/run-now")
def run_now():
    """手动触发一轮 (后台线程跑, 立即返回)."""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一轮还在跑, 请稍后再试")

    t = threading.Thread(
        target=_run_in_background,
        args=('manual',),
        name='deepseek_explore_manual',
        daemon=True,
    )
    t.start()
    return {"success": True, "message": "已在后台启动一轮, 看日志和 /runs 接口"}
