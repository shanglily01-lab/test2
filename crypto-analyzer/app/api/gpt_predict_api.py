"""
GPT 预测 HTTP API.

端点:
- GET  /api/gpt-predict/status                  返回 kill switch + 最近一轮元数据
- POST /api/gpt-predict/toggle                  切换 kill switch
- GET  /api/gpt-predict/runs?limit=20           最近 N 轮运行记录
- GET  /api/gpt-predict/verdicts?run_id=X       某轮的所有 verdicts
- POST /api/gpt-predict/run-now                 手动触发一轮
- GET  /api/gpt-predict/positions?status=open   当前 OPEN / 历史 CLOSED 仓位
- GET  /api/gpt-predict/positions/live          实时盈亏
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


router = APIRouter(prefix="/api/gpt-predict", tags=["GPT预测"])


def _get_db_config():
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


def _get_price_hub():
    try:
        from app.services.binance_data_hub import get_global_data_hub
        return get_global_data_hub()
    except Exception:
        return None


def _live_price(symbol: str, hub) -> Optional[float]:
    try:
        from app.utils.futures_price import get_futures_trade_price

        conn = _connect()
        try:
            return get_futures_trade_price(conn, symbol, log_tag="GPT预测展示")
        finally:
            conn.close()
    except Exception:
        pass
    if hub is not None:
        try:
            lp = hub.get_trade_price_sync(symbol, max_age_seconds=90)
            if lp is not None and lp > 0:
                return float(lp)
        except Exception:
            pass
    return None


def _build_live_positions(positions):
    """用实时价现场算浮盈, 不用 DB 里可能过期的 unrealized_pnl."""
    if not positions:
        return [], {
            "total_unrealized_pnl": 0.0,
            "total_margin_used": 0.0,
            "total_unrealized_pnl_pct": 0.0,
            "open_count": 0,
        }

    hub = _get_price_hub()
    result = []
    for p in positions:
        symbol = futures_symbol_rating_canonical(p['symbol'])
        side = p['position_side']
        entry = float(p['entry_price'])
        qty = float(p['quantity'])
        margin = float(p['margin'])
        live_price = _live_price(symbol, hub)

        unrealized_pnl = None
        unrealized_pnl_pct = None
        if live_price is not None and live_price > 0:
            if side == 'LONG':
                unrealized_pnl = (live_price - entry) * qty
            else:
                unrealized_pnl = (entry - live_price) * qty
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


_OPEN_POSITIONS_SQL = (
    "SELECT id, symbol, position_side, leverage, quantity, "
    "       entry_price, margin, "
    "       stop_loss_price, take_profit_price, "
    "       open_time, planned_close_time, entry_reason "
    "FROM futures_positions "
    "WHERE source='gpt_predict' AND status='open' AND account_id=2 "
    "ORDER BY open_time DESC"
)


# ============================================================
# 状态 + 开关
# ============================================================
@router.get("/status")
async def status():
    """返回当前 kill switch + 最近一轮 + 当前 OPEN 持仓数."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT setting_value FROM system_settings "
                    "WHERE setting_key='gpt_predict_enabled' LIMIT 1"
                )
                row = cur.fetchone()
                enabled_raw = str((row or {}).get('setting_value', '0')).strip().lower()
                enabled = enabled_raw in ('1', 'true', 'yes', 'on')

                cur.execute(
                    "SELECT id, asof_utc, model, symbol_count, predictions_made, "
                    "       orders_opened, elapsed_s, status, error_msg, "
                    "       triggered_by, LEFT(summary_zh, 200) AS summary_short, created_at "
                    "FROM gpt_predict_runs ORDER BY id DESC LIMIT 1"
                )
                last_run = cur.fetchone()

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source='gpt_predict' AND status='open' AND account_id=2"
                )
                open_count = int((cur.fetchone() or {}).get('cnt', 0) or 0)

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source='gpt_predict' AND status='closed' AND account_id=2 "
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
                "max_positions": None,
                "params": {
                    "margin_usd": 500,
                    "leverage": 5,
                    "hold_hours": 4,
                    "sl_pct": 5,
                    "tp_pct": 10,
                    "confidence_threshold": 0.6,
                    "top_n": 50,
                },
            },
        }
    except Exception as e:
        logger.error(f"[GPT预测 API] /status 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/toggle")
async def toggle(request: ToggleRequest):
    try:
        val = '1' if request.enabled else '0'
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO system_settings (setting_key, setting_value) "
                    "VALUES ('gpt_predict_enabled', %s) "
                    "ON DUPLICATE KEY UPDATE setting_value = %s",
                    (val, val),
                )
        finally:
            conn.close()
        logger.info(f"[GPT预测 API] kill switch -> {val}")
        return {"success": True, "enabled": request.enabled}
    except Exception as e:
        logger.error(f"[GPT预测 API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 运行记录
# ============================================================
@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=200)):
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, asof_utc, model, symbol_count, predictions_made, "
                    "       orders_opened, elapsed_s, status, error_msg, triggered_by, "
                    "       LEFT(summary_zh, 200) AS summary_short, created_at, "
                    "       (prompt_text IS NOT NULL AND prompt_text != '') AS has_prompt, "
                    "       (raw_response IS NOT NULL AND raw_response != '') AS has_raw "
                    "FROM gpt_predict_runs "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                runs = cur.fetchall()
        finally:
            conn.close()
        return {"success": True, "data": runs, "count": len(runs)}
    except Exception as e:
        logger.error(f"[GPT预测 API] /runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{run_id}/detail")
async def get_run_detail(run_id: int):
    """返回某轮的完整 prompt 与模型原始 JSON 响应."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, prompt_text, raw_response, summary_zh "
                    "FROM gpt_predict_runs WHERE id=%s",
                    (run_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="run not found")
        return {"success": True, "data": row}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GPT预测 API] /runs/{run_id}/detail 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
async def list_verdicts(run_id: int = Query(..., ge=1)):
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT v.id, v.run_id, v.symbol, v.category, v.confidence, "
                    "       v.catalyst, v.data_signal, v.risk_note, "
                    "       v.price_at_pred, v.action_taken, v.position_id, "
                    "       v.skip_reason, v.created_at "
                    "FROM gpt_predict_verdicts v "
                    "WHERE v.run_id=%s "
                    "ORDER BY "
                    "  CASE v.action_taken "
                    "    WHEN 'opened' THEN 0 "
                    "    WHEN 'skipped_big4' THEN 1 "
                    "    WHEN 'skipped_dedup' THEN 2 "
                    "    WHEN 'skipped_max_positions' THEN 3 "
                    "    WHEN 'skipped_confidence' THEN 4 "
                    "    ELSE 5 END, "
                    "  v.confidence DESC",
                    (run_id,),
                )
                verdicts = cur.fetchall()
        finally:
            conn.close()
        for v in verdicts:
            if v.get('confidence') is not None:
                v['confidence'] = float(v['confidence'])
            if v.get('price_at_pred') is not None:
                v['price_at_pred'] = float(v['price_at_pred'])
        canonicalize_symbol_fields(verdicts)
        return {"success": True, "data": verdicts, "count": len(verdicts)}
    except Exception as e:
        logger.error(f"[GPT预测 API] /verdicts 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 持仓查询
# ============================================================
@router.get("/positions")
async def list_positions(
    status: str = Query('open', pattern='^(open|closed)$'),
    limit: int = Query(50, ge=1, le=500),
):
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
                        "WHERE source='gpt_predict' AND status='open' AND account_id=2 "
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
                        "WHERE source='gpt_predict' AND status='closed' AND account_id=2 "
                        "  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
                        "ORDER BY close_time DESC "
                        "LIMIT %s",
                        (limit,),
                    )
                rows = cur.fetchall()
        finally:
            conn.close()

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
        logger.error(f"[GPT预测 API] /positions 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 实时价格 + 盈亏
# ============================================================
@router.get("/positions/live")
async def list_positions_live():
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(_OPEN_POSITIONS_SQL)
                positions = cur.fetchall()
        finally:
            conn.close()

        result, summary = _build_live_positions(positions)
        return {
            "success": True,
            "data": result,
            "count": len(result),
            "summary": summary,
        }
    except Exception as e:
        logger.error(f"[GPT预测 API] /positions/live 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 盈亏统计
# ============================================================
@router.get("/stats")
async def stats(days: int = Query(30, ge=1, le=365)):
    """返回 GPT 预测的累计盈亏统计."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                from app.utils.pnl_stats import PNL_COUNT_SELECT, parse_pnl_counts

                cur.execute(
                    f"""
                    SELECT
                        {PNL_COUNT_SELECT},
                        COALESCE(SUM(realized_pnl), 0) AS total_pnl,
                        COALESCE(AVG(realized_pnl), 0) AS avg_pnl
                    FROM futures_positions
                    WHERE source='gpt_predict' AND status='closed' AND account_id=2
                      AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    """,
                    (days,),
                )
                row = cur.fetchone()
                counts = parse_pnl_counts(row)
                total = counts["total_trades"]
                wins = counts["wins"]
                losses = counts["losses"]
                breakeven = counts["breakeven"]

                cur.execute(_OPEN_POSITIONS_SQL)
                open_positions = cur.fetchall()
        finally:
            conn.close()

        _, live_summary = _build_live_positions(open_positions)
        floating_pnl = float(live_summary['total_unrealized_pnl'])

        total_pnl = float(row['total_pnl'] or 0)
        avg_pnl = float(row['avg_pnl'] or 0)

        return {
            "success": True,
            "data": {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "breakeven": breakeven,
                "win_rate": counts["win_rate"],
                "total_realized_pnl": round(total_pnl, 2),
                "avg_realized_pnl": round(avg_pnl, 2),
                "floating_pnl": round(floating_pnl, 2),
                "total_pnl": round(total_pnl + floating_pnl, 2),
                "days": days,
            },
        }
    except Exception as e:
        logger.error(f"[GPT预测 API] /stats 失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 手动触发
# ============================================================
_run_lock = threading.Lock()


def _run_in_background(triggered_by: str):
    try:
        from app.services.gpt_predictor import run_predict_round
        run_predict_round(triggered_by=triggered_by)
    except Exception as e:
        logger.error(f"[GPT预测 API] 后台跑一轮异常: {e}", exc_info=True)
    finally:
        try:
            _run_lock.release()
        except Exception:
            pass


@router.post("/run-now")
async def run_now():
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一轮还在跑, 请稍后再试")

    t = threading.Thread(
        target=_run_in_background,
        args=('manual',),
        name='gpt_predict_manual',
        daemon=True,
    )
    t.start()
    return {"success": True, "message": "已在后台启动一轮, 看日志和 /runs 接口"}
