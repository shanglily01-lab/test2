"""
Gemini 探索 HTTP API.

端点:
- GET  /api/gemini-explore/status                 返回 kill switch + 最近一轮元数据
- POST /api/gemini-explore/toggle                 切换 kill switch (写 system_settings)
- GET  /api/gemini-explore/runs?limit=20          最近 N 轮运行记录
- GET  /api/gemini-explore/verdicts?run_id=X      某轮的所有 verdicts
- GET  /api/gemini-explore/positions?status=open  当前 OPEN / 历史 CLOSED 仓位
- POST /api/gemini-explore/run-now                手动触发一轮 (调试用,后台线程跑)

只读 + 1 个开关 + 1 个调试触发, 不带手动平仓接口 (按 plan 用户决定).
"""
from __future__ import annotations

import threading
from typing import Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger


router = APIRouter(prefix="/api/gemini-explore", tags=["Gemini探索"])


def _get_db_config():
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    return pymysql.connect(
        **_get_db_config(),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ============================================================
# 状态 + 开关
# ============================================================
@router.get("/status")
async def status():
    """返回当前 kill switch 状态 + 最近一轮元数据 + 当前 OPEN 持仓数."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT setting_value FROM system_settings "
                    "WHERE setting_key='gemini_explore_enabled' LIMIT 1"
                )
                row = cur.fetchone()
                enabled_raw = str((row or {}).get('setting_value', '0')).strip().lower()
                enabled = enabled_raw in ('1', 'true', 'yes', 'on')

                cur.execute(
                    "SELECT id, asof_utc, model, universe_size, trades_opened, "
                    "       elapsed_s, status, error_msg, triggered_by, created_at "
                    "FROM gemini_explore_runs ORDER BY id DESC LIMIT 1"
                )
                last_run = cur.fetchone()

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source='gemini_explore' AND status='open' AND account_id=2"
                )
                open_count = int((cur.fetchone() or {}).get('cnt', 0) or 0)

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source='gemini_explore' AND status='closed' AND account_id=2 "
                    "AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)"
                )
                closed_30d = int((cur.fetchone() or {}).get('cnt', 0) or 0)
        finally:
            conn.close()

        return {
            "success": True,
            "data": {
                "enabled": enabled,
                "last_run": last_run,
                "open_positions": open_count,
                "closed_positions_30d": closed_30d,
                "max_positions": 20,
                "params": {
                    "margin_usd": 500,
                    "leverage": 5,
                    "hold_hours": 6,
                    "sl_pct": 3,
                    "tp_pct": 8,
                    "confidence_threshold": 0.6,
                },
            },
        }
    except Exception as e:
        logger.error(f"[Gemini探索 API] /status 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/toggle")
async def toggle(request: ToggleRequest):
    """切换 kill switch. 不会立刻触发一轮 Gemini, 只改 system_settings."""
    try:
        val = '1' if request.enabled else '0'
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO system_settings (setting_key, setting_value) "
                    "VALUES ('gemini_explore_enabled', %s) "
                    "ON DUPLICATE KEY UPDATE setting_value = %s",
                    (val, val),
                )
        finally:
            conn.close()
        logger.info(f"[Gemini探索 API] kill switch -> {val}")
        return {"success": True, "enabled": request.enabled}
    except Exception as e:
        logger.error(f"[Gemini探索 API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 运行记录
# ============================================================
@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=200)):
    """最近 N 轮运行记录."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, asof_utc, model, universe_size, trades_opened, "
                    "       elapsed_s, status, error_msg, triggered_by, "
                    "       LEFT(summary_zh, 200) AS summary_short, created_at "
                    "FROM gemini_explore_runs "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                runs = cur.fetchall()
        finally:
            conn.close()
        return {"success": True, "data": runs, "count": len(runs)}
    except Exception as e:
        logger.error(f"[Gemini探索 API] /runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
async def list_verdicts(run_id: int = Query(..., ge=1)):
    """某轮的所有 verdicts (含 skip 的, 用户能看到 Gemini 完整发现)."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, run_id, symbol, category, confidence, "
                    "       catalyst, data_signal, risk_note, "
                    "       action_taken, position_id, skip_reason, created_at "
                    "FROM gemini_explore_verdicts "
                    "WHERE run_id=%s "
                    "ORDER BY "
                    "  CASE action_taken "
                    "    WHEN 'opened' THEN 0 "
                    "    WHEN 'skipped_big4' THEN 1 "
                    "    WHEN 'skipped_dedup' THEN 2 "
                    "    WHEN 'skipped_max_positions' THEN 3 "
                    "    WHEN 'skipped_confidence' THEN 4 "
                    "    ELSE 5 END, "
                    "  confidence DESC",
                    (run_id,),
                )
                verdicts = cur.fetchall()
        finally:
            conn.close()
        # decimal 转 float 方便前端
        for v in verdicts:
            if v.get('confidence') is not None:
                v['confidence'] = float(v['confidence'])
        return {"success": True, "data": verdicts, "count": len(verdicts)}
    except Exception as e:
        logger.error(f"[Gemini探索 API] /verdicts 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 持仓查询
# ============================================================
@router.get("/positions")
async def list_positions(
    status: str = Query('open', pattern='^(open|closed)$'),
    limit: int = Query(50, ge=1, le=500),
):
    """gemini_explore source 的当前 OPEN / 历史 CLOSED 仓位."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                if status == 'open':
                    cur.execute(
                        "SELECT id, symbol, position_side, leverage, quantity, "
                        "       entry_price, mark_price, "
                        "       stop_loss_price, take_profit_price, "
                        "       stop_loss_pct, take_profit_pct, "
                        "       margin, unrealized_pnl, unrealized_pnl_pct, "
                        "       open_time, planned_close_time, "
                        "       entry_reason, source "
                        "FROM futures_positions "
                        "WHERE source='gemini_explore' AND status='open' AND account_id=2 "
                        "ORDER BY open_time DESC "
                        "LIMIT %s",
                        (limit,),
                    )
                else:
                    cur.execute(
                        "SELECT id, symbol, position_side, leverage, quantity, "
                        "       entry_price, mark_price, "
                        "       stop_loss_price, take_profit_price, "
                        "       margin, realized_pnl, "
                        "       open_time, close_time, "
                        "       entry_reason, notes, source "
                        "FROM futures_positions "
                        "WHERE source='gemini_explore' AND status='closed' AND account_id=2 "
                        "  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
                        "ORDER BY close_time DESC "
                        "LIMIT %s",
                        (limit,),
                    )
                rows = cur.fetchall()
        finally:
            conn.close()

        # decimal/datetime 转字符串
        for r in rows:
            for k, v in list(r.items()):
                if hasattr(v, 'isoformat'):
                    r[k] = v.isoformat()
                elif hasattr(v, '__float__') and not isinstance(v, (int, float)):
                    try:
                        r[k] = float(v)
                    except Exception:
                        pass
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"[Gemini探索 API] /positions 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 实时价格 + 实时盈亏 (前端高频轮询用)
# ============================================================
@router.get("/positions/live")
async def list_positions_live():
    """返回所有 OPEN 仓位的实时盈亏.

    与 /positions?status=open 不同, 这里每个仓位的 mark_price 是
    **实时从 BinanceDataHub WS markPrice 拿** (秒级), 然后用最新价
    现场算 unrealized_pnl / pnl_pct.

    适合前端 3-5 秒轮询, 不要更高频 (Hub 内部 WS 推送频率本身就 1-2s).
    """
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, symbol, position_side, leverage, quantity, "
                    "       entry_price, margin, "
                    "       stop_loss_price, take_profit_price, "
                    "       open_time, planned_close_time, entry_reason "
                    "FROM futures_positions "
                    "WHERE source='gemini_explore' AND status='open' AND account_id=2 "
                    "ORDER BY open_time DESC"
                )
                positions = cur.fetchall()
        finally:
            conn.close()

        if not positions:
            return {"success": True, "data": [], "count": 0}

        # 拿 Hub 实时价
        try:
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
        except Exception as e:
            logger.warning(f"[Gemini探索 API] /positions/live Hub 获取失败: {e}")
            hub = None

        from decimal import Decimal
        result = []
        for p in positions:
            symbol = p['symbol']
            side = p['position_side']
            entry = float(p['entry_price'])
            qty = float(p['quantity'])
            margin = float(p['margin'])

            # 实时价
            live_price = None
            if hub is not None:
                try:
                    lp = hub.get_price_sync(symbol, max_age_seconds=90)
                    if lp is not None and lp > 0:
                        live_price = float(lp)
                except Exception as e:
                    logger.debug(f"[Gemini探索 API] hub 取 {symbol} 实时价失败: {e}")

            # 兜底: 5m close
            if live_price is None:
                try:
                    conn2 = _connect()
                    with conn2.cursor() as cur2:
                        cur2.execute(
                            "SELECT close_price FROM kline_data "
                            "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                            "ORDER BY open_time DESC LIMIT 1",
                            (symbol,)
                        )
                        row = cur2.fetchone()
                        if row and row.get('close_price'):
                            live_price = float(row['close_price'])
                    conn2.close()
                except Exception:
                    pass

            # 算实时盈亏
            unrealized_pnl = None
            unrealized_pnl_pct = None
            if live_price is not None and live_price > 0:
                if side == 'LONG':
                    unrealized_pnl = (live_price - entry) * qty
                else:
                    unrealized_pnl = (entry - live_price) * qty
                # ROI on margin (跟 UI 显示一致, 含杠杆)
                unrealized_pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0

            row = {
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
            }
            result.append(row)

        # 汇总
        total_pnl = sum((r['unrealized_pnl'] or 0) for r in result)
        total_margin = sum(r['margin'] for r in result)
        total_pnl_pct = (total_pnl / total_margin * 100) if total_margin > 0 else 0

        return {
            "success": True,
            "data": result,
            "count": len(result),
            "summary": {
                "total_unrealized_pnl": round(total_pnl, 2),
                "total_margin_used": round(total_margin, 2),
                "total_unrealized_pnl_pct": round(total_pnl_pct, 2),
                "open_count": len(result),
            },
        }
    except Exception as e:
        logger.error(f"[Gemini探索 API] /positions/live 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 手动触发 (调试用)
# ============================================================
_run_lock = threading.Lock()


def _run_in_background(triggered_by: str):
    try:
        from app.services.gemini_explore_worker import run_explore_round
        run_explore_round(triggered_by=triggered_by)
    except Exception as e:
        logger.error(f"[Gemini探索 API] 后台跑一轮异常: {e}", exc_info=True)
    finally:
        try:
            _run_lock.release()
        except Exception:
            pass


@router.post("/run-now")
async def run_now():
    """手动触发一轮 (后台线程跑, 立即返回). 单实例锁, 已在跑时返回 409."""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一轮还在跑, 请稍后再试")

    t = threading.Thread(
        target=_run_in_background,
        args=('manual',),
        name='gemini_explore_manual',
        daemon=True,
    )
    t.start()
    return {"success": True, "message": "已在后台启动一轮, 看日志和 /runs 接口"}
