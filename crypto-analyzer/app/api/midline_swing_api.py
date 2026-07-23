"""中线策略 v2 HTTP API — 机会分析 + 参数 + 手动扫描."""
from __future__ import annotations

import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from app.services.midline_swing_config import (
    MIDLINE_HOLD_HOURS,
    MIDLINE_KILL_SWITCH,
    MIDLINE_LEVERAGE,
    MIDLINE_MARGIN_USD,
    MIDLINE_SETTING_INTERVAL_HOURS,
    MIDLINE_SETTING_LIMIT_LONG_OFFSET_PCT,
    MIDLINE_SETTING_LIMIT_SHORT_OFFSET_PCT,
    MIDLINE_SL_PCT,
    MIDLINE_SOURCES,
    MIDLINE_TP_PCT,
    _clamp_midline_interval_hours,
    _clamp_midline_limit_offset_pct,
    get_midline_runtime_params,
    profile_for_source,
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


def _ensure_settings(cur) -> None:
    for key in MIDLINE_KILL_SWITCH.values():
        cur.execute(
            "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s, '0')",
            (key,),
        )
    cur.execute(
        "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s, %s)",
        (MIDLINE_SETTING_INTERVAL_HOURS, "4"),
    )
    cur.execute(
        "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s, %s)",
        (MIDLINE_SETTING_LIMIT_LONG_OFFSET_PCT, "1.0"),
    )
    cur.execute(
        "INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES (%s, %s)",
        (MIDLINE_SETTING_LIMIT_SHORT_OFFSET_PCT, "1.0"),
    )


@router.get("/status")
def status(source: str = Query(...)):
    source = _validate_source(source)
    kill_key = MIDLINE_KILL_SWITCH[source]
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _ensure_settings(cur)
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

        profile = profile_for_source(source)
        runtime = get_midline_runtime_params()
        return {
            "success": True,
            "data": {
                "source": source,
                "enabled": enabled,
                "last_run": last_run,
                "open_positions": open_count,
                "closed_positions_30d": closed_30d,
                "live_sync": False,
                "params": {
                    "margin_usd": MIDLINE_MARGIN_USD,
                    "leverage": MIDLINE_LEVERAGE,
                    "hold_hours": MIDLINE_HOLD_HOURS,
                    "limit_long_offset_pct": runtime["limit_long_offset_pct"],
                    "limit_short_offset_pct": runtime["limit_short_offset_pct"],
                    "limit_offset_pct": runtime["limit_offset_pct"],
                    "limit_timeout_minutes": runtime["limit_timeout_minutes"],
                    "sl_pct": MIDLINE_SL_PCT,
                    "tp_pct": MIDLINE_TP_PCT,
                    "interval_hours": runtime["interval_hours"],
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


class MidlineParamsRequest(BaseModel):
    interval_hours: Optional[int] = None
    limit_long_offset_pct: Optional[float] = None
    limit_short_offset_pct: Optional[float] = None


def _invalidate_midline_settings_cache() -> None:
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


@router.get("/params")
def get_params():
    try:
        runtime = get_midline_runtime_params()
        return {
            "success": True,
            "data": {
                **runtime,
                "margin_usd": MIDLINE_MARGIN_USD,
                "leverage": MIDLINE_LEVERAGE,
                "hold_hours": MIDLINE_HOLD_HOURS,
                "sl_pct": MIDLINE_SL_PCT,
                "tp_pct": MIDLINE_TP_PCT,
                "sources": sorted(MIDLINE_SOURCES),
                "live_sync": False,
            },
        }
    except Exception as e:
        logger.error(f"[中线 API] /params GET 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/params")
def update_params(request: MidlineParamsRequest):
    updates: dict[str, str] = {}
    if request.interval_hours is not None:
        updates[MIDLINE_SETTING_INTERVAL_HOURS] = str(
            _clamp_midline_interval_hours(request.interval_hours)
        )
    if request.limit_long_offset_pct is not None:
        updates[MIDLINE_SETTING_LIMIT_LONG_OFFSET_PCT] = str(
            _clamp_midline_limit_offset_pct(request.limit_long_offset_pct)
        )
    if request.limit_short_offset_pct is not None:
        updates[MIDLINE_SETTING_LIMIT_SHORT_OFFSET_PCT] = str(
            _clamp_midline_limit_offset_pct(request.limit_short_offset_pct)
        )
    if not updates:
        raise HTTPException(status_code=400, detail="至少提供一个可更新字段")

    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                for key, val in updates.items():
                    _upsert_setting(cur, key, val)
            conn.commit()
        finally:
            conn.close()
        _invalidate_midline_settings_cache()
        runtime = get_midline_runtime_params()
        return {"success": True, "data": runtime, "updated": list(updates.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[中线 API] /params POST 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/toggle")
def toggle(source: str = Query(...), request: ToggleRequest = ...):
    source = _validate_source(source)
    kill_key = MIDLINE_KILL_SWITCH[source]
    try:
        val = "1" if request.enabled else "0"
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _upsert_setting(cur, kill_key, val)
        finally:
            conn.close()
        _invalidate_midline_settings_cache()
        return {"success": True, "enabled": request.enabled, "source": source}
    except Exception as e:
        logger.error(f"[中线 API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/overview")
def overview():
    """中线策略页首屏：两路 status + 共用参数."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                _ensure_settings(cur)
                data = {}
                for source in sorted(MIDLINE_SOURCES):
                    kill_key = MIDLINE_KILL_SWITCH[source]
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
                               elapsed_s, status, summary_zh, triggered_by, created_at
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
                    data[source] = {
                        "enabled": enabled,
                        "last_run": last_run,
                        "open_positions": open_count,
                    }
        finally:
            conn.close()
        return {
            "success": True,
            "data": {
                "sources": data,
                "params": get_midline_runtime_params(),
                "hold_hours": MIDLINE_HOLD_HOURS,
                "sl_pct": MIDLINE_SL_PCT,
                "tp_pct": MIDLINE_TP_PCT,
                "margin_usd": MIDLINE_MARGIN_USD,
                "leverage": MIDLINE_LEVERAGE,
                "live_sync": False,
            },
        }
    except Exception as e:
        logger.error(f"[中线 API] /overview 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
def list_runs(
    source: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=200),
):
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                if source:
                    source = _validate_source(source)
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
                else:
                    cur.execute(
                        """
                        SELECT id, source, asof_utc, universe_size, signals_found, orders_placed,
                               elapsed_s, status, error_msg, triggered_by,
                               LEFT(summary_zh, 200) AS summary_short, created_at
                        FROM midline_swing_runs
                        WHERE source IN ('midline_long', 'midline_short')
                        ORDER BY id DESC LIMIT %s
                        """,
                        (limit,),
                    )
                runs = cur.fetchall()
        finally:
            conn.close()
        return {"success": True, "data": runs, "count": len(runs)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[中线 API] /runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
def list_verdicts(
    run_id: int = Query(..., ge=1),
    source: Optional[str] = Query(None),
    only_passed: bool = Query(False),
    limit: int = Query(300, ge=1, le=1000),
):
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                sql = """
                    SELECT id, run_id, source, symbol, side, score, signal_detail,
                           action_taken, order_id, position_id, skip_reason, created_at
                    FROM midline_swing_verdicts
                    WHERE run_id=%s
                """
                params: list = [run_id]
                if source:
                    source = _validate_source(source)
                    sql += " AND source=%s"
                    params.append(source)
                if only_passed:
                    sql += " AND action_taken='limit_placed'"
                sql += " ORDER BY (action_taken='limit_placed') DESC, score DESC, id ASC LIMIT %s"
                params.append(limit)
                cur.execute(sql, params)
                rows = cur.fetchall()
        finally:
            conn.close()
        for r in rows:
            r["symbol"] = futures_symbol_rating_canonical(r.get("symbol"))
        return {"success": True, "data": rows, "count": len(rows)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[中线 API] /verdicts 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
def list_positions(
    source: Optional[str] = Query(None),
    status: str = Query("open"),
    limit: int = Query(200, ge=1, le=500),
):
    st = status.strip().lower()
    if st not in ("open", "closed"):
        raise HTTPException(status_code=400, detail="status must be open or closed")
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                if source:
                    source = _validate_source(source)
                    src_filter = "source=%s"
                    src_params: list = [source]
                else:
                    src_filter = "source IN ('midline_long', 'midline_short')"
                    src_params = []
                if st == "open":
                    cur.execute(
                        f"""
                        SELECT id, symbol, position_side, leverage, quantity, entry_price,
                               mark_price, margin, unrealized_pnl, unrealized_pnl_pct,
                               stop_loss_price, take_profit_price, liquidation_price,
                               open_time, planned_close_time, entry_reason, source
                        FROM futures_positions
                        WHERE {src_filter} AND status='open' AND account_id=2
                        ORDER BY open_time DESC LIMIT %s
                        """,
                        (*src_params, limit),
                    )
                else:
                    cur.execute(
                        f"""
                        SELECT id, symbol, position_side, leverage, quantity, entry_price,
                               mark_price, margin, realized_pnl, close_time, open_time,
                               close_reason, entry_reason, source
                        FROM futures_positions
                        WHERE {src_filter} AND status='closed' AND account_id=2
                        AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                        ORDER BY close_time DESC LIMIT %s
                        """,
                        (*src_params, limit),
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
def run_now(source: Optional[str] = Query(None), profile: Optional[str] = Query(None)):
    """手动触发一轮。source=midline_long|short；缺省则两路都跑."""
    targets: list[str] = []
    if source:
        targets = [_validate_source(source)]
    elif profile:
        p = profile.strip().lower()
        if p not in ("long", "short"):
            raise HTTPException(status_code=400, detail="profile must be long or short")
        targets = [f"midline_{p}"]
    else:
        targets = ["midline_long", "midline_short"]

    def _bg(src: str):
        try:
            from app.services.midline_explore_worker import run_midline_round
            run_midline_round(source=src, triggered_by="manual")
        except Exception as e:
            logger.error(f"[中线 API] manual run 失败 {src}: {e}", exc_info=True)

    for src in targets:
        threading.Thread(target=_bg, args=(src,), daemon=True, name=f"MidlineManual-{src}").start()
    return {"success": True, "message": f"已后台触发: {', '.join(targets)}"}
