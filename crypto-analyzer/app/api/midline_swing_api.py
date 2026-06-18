"""中线做多/做空 HTTP API（Gemini / DeepSeek 探索页共用）."""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from app.services.midline_swing_config import (
    MIDLINE_HOLD_DAYS,
    MIDLINE_KILL_SWITCH,
    MIDLINE_LEVERAGE,
    MIDLINE_LIMIT_OFFSET_PCT,
    MIDLINE_LIMIT_TIMEOUT_MINUTES,
    MIDLINE_MARGIN_USD,
    MIDLINE_SOURCES,
    is_midline_source,
)
from app.utils.futures_symbol import futures_symbol_rating_canonical


router = APIRouter(prefix="/api/midline-swing", tags=["中线策略"])


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


def _validate_source(source: str) -> str:
    s = (source or "").strip().lower()
    if s not in MIDLINE_SOURCES:
        raise HTTPException(status_code=400, detail=f"invalid source: {source}")
    return s


@router.get("/status")
def status(source: str = Query(...)):
    source = _validate_source(source)
    kill_key = MIDLINE_KILL_SWITCH[source]
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
                    (kill_key,),
                )
                row = cur.fetchone()
                enabled_raw = str((row or {}).get("setting_value", "0")).strip().lower()
                enabled = enabled_raw in ("1", "true", "yes", "on")

                cur.execute(
                    """
                    SELECT id, asof_utc, universe_size, signals_found, orders_placed,
                           elapsed_s, status, error_msg, triggered_by, summary_zh, created_at
                    FROM midline_swing_runs WHERE source=%s ORDER BY id DESC LIMIT 1
                    """,
                    (source,),
                )
                last_run = cur.fetchone()

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source=%s AND status='open' AND account_id=2",
                    (source,),
                )
                open_count = int((cur.fetchone() or {}).get("cnt", 0) or 0)

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source=%s AND status='closed' AND account_id=2 "
                    "AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)",
                    (source,),
                )
                closed_30d = int((cur.fetchone() or {}).get("cnt", 0) or 0)
        finally:
            conn.close()

        profile = "long" if source.endswith("_long") else "short"
        return {
            "success": True,
            "data": {
                "source": source,
                "enabled": enabled,
                "last_run": last_run,
                "open_positions": open_count,
                "closed_positions_30d": closed_30d,
                "params": {
                    "margin_usd": MIDLINE_MARGIN_USD,
                    "leverage": MIDLINE_LEVERAGE,
                    "hold_days": MIDLINE_HOLD_DAYS,
                    "limit_offset_pct": MIDLINE_LIMIT_OFFSET_PCT,
                    "limit_timeout_minutes": MIDLINE_LIMIT_TIMEOUT_MINUTES,
                    "interval_hours": 6,
                    "profile": profile,
                },
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[中线 API] /status 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ToggleRequest(BaseModel):
    enabled: bool


@router.post("/toggle")
def toggle(source: str = Query(...), request: ToggleRequest = ...):
    source = _validate_source(source)
    kill_key = MIDLINE_KILL_SWITCH[source]
    try:
        val = "1" if request.enabled else "0"
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO system_settings (setting_key, setting_value) "
                    "VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value=%s",
                    (kill_key, val, val),
                )
        finally:
            conn.close()
        return {"success": True, "enabled": request.enabled, "source": source}
    except Exception as e:
        logger.error(f"[中线 API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
def list_runs(source: str = Query(...), limit: int = Query(20, ge=1, le=200)):
    source = _validate_source(source)
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, source, asof_utc, universe_size, signals_found, orders_placed,
                           elapsed_s, status, error_msg, triggered_by,
                           LEFT(summary_zh, 200) AS summary_short, created_at
                    FROM midline_swing_runs
                    WHERE source=%s ORDER BY id DESC LIMIT %s
                    """,
                    (source, limit),
                )
                runs = cur.fetchall()
        finally:
            conn.close()
        return {"success": True, "data": runs, "count": len(runs)}
    except Exception as e:
        logger.error(f"[中线 API] /runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
def list_verdicts(source: str = Query(...), run_id: int = Query(..., ge=1)):
    source = _validate_source(source)
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, run_id, source, symbol, side, score, signal_detail,
                           action_taken, order_id, position_id, skip_reason, created_at
                    FROM midline_swing_verdicts
                    WHERE run_id=%s AND source=%s
                    ORDER BY score DESC, id ASC
                    """,
                    (run_id, source),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        return {"success": True, "data": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"[中线 API] /verdicts 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
def list_positions(
    source: str = Query(...),
    status: str = Query("open"),
    limit: int = Query(200, ge=1, le=500),
):
    source = _validate_source(source)
    st = status.strip().lower()
    if st not in ("open", "closed"):
        raise HTTPException(status_code=400, detail="status must be open or closed")
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                if st == "open":
                    cur.execute(
                        """
                        SELECT id, symbol, position_side, leverage, quantity, entry_price,
                               mark_price, margin, unrealized_pnl, unrealized_pnl_pct,
                               stop_loss_price, take_profit_price, liquidation_price,
                               open_time, planned_close_time, entry_reason
                        FROM futures_positions
                        WHERE source=%s AND status='open' AND account_id=2
                        ORDER BY open_time DESC LIMIT %s
                        """,
                        (source, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, symbol, position_side, leverage, quantity, entry_price,
                               mark_price, margin, realized_pnl, close_time, open_time,
                               close_reason, entry_reason
                        FROM futures_positions
                        WHERE source=%s AND status='closed' AND account_id=2
                        AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                        ORDER BY close_time DESC LIMIT %s
                        """,
                        (source, limit),
                    )
                rows = cur.fetchall()
        finally:
            conn.close()
        for r in rows:
            r["symbol"] = futures_symbol_rating_canonical(r.get("symbol"))
        return {"success": True, "data": rows, "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[中线 API] /positions 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-now")
def run_now(source: str = Query(...)):
    source = _validate_source(source)
    teacher = "gemini" if source.startswith("gemini_") else "deepseek"
    profile = "long" if source.endswith("_long") else "short"

    def _bg():
        try:
            from app.services.midline_explore_worker import run_midline_round
            run_midline_round(teacher, profile, triggered_by="manual")
        except Exception as e:
            logger.error(f"[中线 API] manual run 失败 {source}: {e}", exc_info=True)

    threading.Thread(target=_bg, daemon=True, name=f"MidlineManual-{source}").start()
    return {"success": True, "message": f"已后台触发 {source}"}
