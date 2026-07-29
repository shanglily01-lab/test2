"""超级大脑策略 HTTP API — 概览 / 开关 / 持仓 / 手动跑一轮."""
from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from app.services.brain_config import (
    BRAIN_ACCOUNT_ID,
    BRAIN_ENABLED_KEY,
    BRAIN_HOLD_HOURS,
    BRAIN_LEVERAGE,
    BRAIN_LIMIT_TIMEOUT_MINUTES,
    BRAIN_MARGIN_USD,
    BRAIN_SCAN_INTERVAL_HOURS,
    BRAIN_SL_PCT,
    BRAIN_SOURCE,
    BRAIN_TICK_BATCH_SIZE,
    BRAIN_TICK_INTERVAL_SECONDS,
    BRAIN_TP_PCT,
    WIN_PROB_MIN,
)

router = APIRouter(prefix="/api/brain-swing", tags=["超级大脑策略"])

_run_lock = threading.Lock()


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


def _invalidate_settings_cache() -> None:
    from app.services.data_cache_service import invalidate_setting_cache
    from app.services.system_settings_loader import invalidate_loader_cache

    invalidate_setting_cache()
    invalidate_loader_cache()


def _upsert_setting(cur, key: str, value: str) -> None:
    cur.execute(
        "INSERT INTO system_settings (setting_key, setting_value) "
        "VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value=%s",
        (key, value, value),
    )


def _setting_enabled(cur, key: str, default: str = "1") -> bool:
    cur.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
        (key,),
    )
    row = cur.fetchone()
    if not row:
        return str(default).strip().lower() in ("1", "true", "yes", "on")
    val = row.get("setting_value") if isinstance(row, dict) else row[0]
    return str(val or default).strip().lower() in ("1", "true", "yes", "on")


def _serialize_row(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        out = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                out[k] = v.isoformat(sep=" ", timespec="seconds")
            else:
                out[k] = v
        return out
    return row


@router.get("/overview")
def overview():
    """超级大脑策略页首屏：开关、参数、Big4、持仓/限价计数."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s, '1')",
                    (BRAIN_ENABLED_KEY,),
                )
                enabled = _setting_enabled(cur, BRAIN_ENABLED_KEY, "1")

                from app.services.brain_market_analyzer import evaluate_big4_gate

                big4 = evaluate_big4_gate(cur)

                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM futures_positions
                    WHERE account_id=%s AND UPPER(status)='OPEN'
                      AND (source=%s OR source LIKE 'brain_%%')
                    """,
                    (BRAIN_ACCOUNT_ID, BRAIN_SOURCE),
                )
                open_count = int((cur.fetchone() or {}).get("cnt", 0) or 0)

                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt FROM futures_positions
                    WHERE account_id=%s AND UPPER(status)='CLOSED'
                      AND (source=%s OR source LIKE 'brain_%%')
                      AND close_time >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)
                    """,
                    (BRAIN_ACCOUNT_ID, BRAIN_SOURCE),
                )
                closed_30d = int((cur.fetchone() or {}).get("cnt", 0) or 0)

                pending_limits = 0
                try:
                    cur.execute(
                        """
                        SELECT COUNT(*) AS cnt FROM futures_orders
                        WHERE account_id=%s AND status='PENDING' AND order_type='LIMIT'
                          AND (order_source=%s OR order_source LIKE 'brain_%%')
                        """,
                        (BRAIN_ACCOUNT_ID, BRAIN_SOURCE),
                    )
                    pending_limits = int((cur.fetchone() or {}).get("cnt", 0) or 0)
                except Exception:
                    pending_limits = 0
        finally:
            conn.close()

        return {
            "success": True,
            "data": {
                "source": BRAIN_SOURCE,
                "enabled": enabled,
                "live_sync": False,
                "comparison_period": True,
                "open_positions": open_count,
                "closed_positions_30d": closed_30d,
                "pending_limits": pending_limits,
                "big4": {
                    "ok": bool(big4.get("big4_ok")),
                    "bias": big4.get("bias") or big4.get("macro_bias") or "FLAT",
                    "reason": big4.get("reason") or "",
                },
                "params": {
                    "scan_mode": "round_robin",
                    "tick_batch_size": BRAIN_TICK_BATCH_SIZE,
                    "tick_interval_seconds": BRAIN_TICK_INTERVAL_SECONDS,
                    "scan_interval_hours": BRAIN_SCAN_INTERVAL_HOURS,
                    "poll_minutes": 30,
                    "hold_hours": BRAIN_HOLD_HOURS,
                    "leverage": BRAIN_LEVERAGE,
                    "margin_usd": BRAIN_MARGIN_USD,
                    "sl_pct": round(BRAIN_SL_PCT * 100, 2),
                    "tp_pct": round(BRAIN_TP_PCT * 100, 2),
                    "limit_timeout_minutes": BRAIN_LIMIT_TIMEOUT_MINUTES,
                    "win_prob_min": WIN_PROB_MIN,
                    "universe": "L0+L1",
                    "advisor": "DeepSeek 开仓确认 / 持仓可强制平",
                },
            },
        }
    except Exception as e:
        logger.error(f"[BRAIN API] /overview 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/live")
def live_status():
    """轮询直播状态：游标、本批结果、进度。"""
    try:
        from app.services.brain_strategy_orchestrator import get_brain_live_status
        data = get_brain_live_status()
        return {"success": True, "data": data}
    except Exception as e:
        logger.error(f"[BRAIN API] /live 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/toggle")
def toggle(request: ToggleRequest):
    try:
        val = "1" if request.enabled else "0"
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _upsert_setting(cur, BRAIN_ENABLED_KEY, val)
            conn.commit()
        finally:
            conn.close()
        _invalidate_settings_cache()
        return {"success": True, "enabled": request.enabled, "key": BRAIN_ENABLED_KEY}
    except Exception as e:
        logger.error(f"[BRAIN API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders")
def pending_orders(limit: int = 50):
    """BRAIN 限价挂单（未成交前在这里，不在持仓表）。"""
    try:
        limit = max(1, min(int(limit or 50), 200))
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, symbol, side, order_type, status, price, quantity,
                           executed_quantity, margin, order_source, entry_signal_type,
                           notes, created_at, updated_at
                    FROM futures_orders
                    WHERE account_id=%s
                      AND (order_source=%s OR order_source LIKE 'brain_%%')
                    ORDER BY
                      CASE WHEN status='PENDING' THEN 0 ELSE 1 END,
                      COALESCE(updated_at, created_at) DESC
                    LIMIT %s
                    """,
                    (BRAIN_ACCOUNT_ID, BRAIN_SOURCE, limit),
                )
                rows = [_serialize_row(r) for r in (cur.fetchall() or [])]
        finally:
            conn.close()
        pending_n = sum(1 for r in rows if str(r.get("status") or "").upper() == "PENDING")
        return {"success": True, "data": rows, "pending": pending_n}
    except Exception as e:
        logger.error(f"[BRAIN API] /orders 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
def positions(limit: int = 50):
    try:
        limit = max(1, min(int(limit or 50), 200))
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, symbol, position_side, entry_price, mark_price, quantity,
                           leverage, unrealized_pnl, source, status,
                           open_time, close_time, close_reason, created_at
                    FROM futures_positions
                    WHERE account_id=%s
                      AND (source=%s OR source LIKE 'brain_%%')
                    ORDER BY
                      CASE WHEN UPPER(status)='OPEN' THEN 0 ELSE 1 END,
                      COALESCE(open_time, created_at) DESC
                    LIMIT %s
                    """,
                    (BRAIN_ACCOUNT_ID, BRAIN_SOURCE, limit),
                )
                rows = [_serialize_row(r) for r in (cur.fetchall() or [])]
        finally:
            conn.close()
        return {"success": True, "data": rows}
    except Exception as e:
        logger.error(f"[BRAIN API] /positions 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/opportunities")
def opportunities(
    limit: int = 80,
    playbook: str = None,
    decision: str = None,
    scan_round_id: int = None,
):
    try:
        from app.services.brain_opportunity_store import list_opportunities

        conn = _connect()
        try:
            rows = list_opportunities(
                conn,
                limit=limit,
                playbook=playbook or None,
                decision=decision or None,
                scan_round_id=scan_round_id,
            )
            rows = [_serialize_row(r) for r in rows]
        finally:
            conn.close()
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"[BRAIN API] /opportunities 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/playbook-stats")
def playbook_stats_api(days: int = 30):
    try:
        from app.services.brain_opportunity_store import playbook_stats

        conn = _connect()
        try:
            rows = [_serialize_row(r) for r in playbook_stats(conn, days=days)]
        finally:
            conn.close()
        return {"success": True, "data": rows, "days": days}
    except Exception as e:
        logger.error(f"[BRAIN API] /playbook-stats 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run")
def run_now():
    """手动触发一轮 brain_swing（后台线程）。"""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一轮手动扫描仍在进行")

    def _job():
        try:
            from app.services.brain_strategy_orchestrator import run_brain_tick
            summary = run_brain_tick(triggered_by="web_manual")
            logger.info(f"[BRAIN API] 手动 tick 结束: {summary}")
        except Exception as e:
            logger.error(f"[BRAIN API] 手动 tick 异常: {e}", exc_info=True)
        finally:
            try:
                _run_lock.release()
            except Exception:
                pass

    threading.Thread(target=_job, name="brain-swing-manual", daemon=True).start()
    return {"success": True, "message": "已触发一批轮询分析（5币，发现机会立即下单）"}
