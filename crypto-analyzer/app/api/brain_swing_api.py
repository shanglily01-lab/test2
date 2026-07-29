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


@router.post("/run")
def run_now():
    """手动触发一轮 brain_swing（后台线程）。"""
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一轮手动扫描仍在进行")

    def _job():
        try:
            from app.services.brain_strategy_orchestrator import run_brain_round
            summary = run_brain_round(triggered_by="web_manual")
            logger.info(f"[BRAIN API] 手动一轮结束: {summary}")
        except Exception as e:
            logger.error(f"[BRAIN API] 手动一轮异常: {e}", exc_info=True)
        finally:
            try:
                _run_lock.release()
            except Exception:
                pass

    threading.Thread(target=_job, name="brain-swing-manual", daemon=True).start()
    return {"success": True, "message": "已启动一轮超级大脑扫描"}
