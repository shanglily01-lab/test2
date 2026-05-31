"""Big4 综合行情 HTTP API — Gemini / DeepSeek 工厂."""
from __future__ import annotations

import threading
from typing import Callable

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger

from app.services.big4_comprehensive_analyzer import INTERVAL_HOURS, PROVIDER_CONFIG


def _get_db_config():
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    return pymysql.connect(
        **_get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def create_big4_analysis_router(provider: str, prefix: str, tag: str) -> APIRouter:
    provider = provider.lower()
    cfg = PROVIDER_CONFIG[provider]
    router = APIRouter(prefix=prefix, tags=[tag])
    _run_lock = threading.Lock()

    @router.get("/status")
    async def status():
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    if not _table_exists(cur, "big4_analysis_runs"):
                        return {
                            "success": False,
                            "message": "请执行 migration 012_big4_analysis_runs.sql",
                        }
                    cur.execute(
                        "SELECT setting_value FROM system_settings "
                        "WHERE setting_key=%s LIMIT 1",
                        (cfg["setting_key"],),
                    )
                    row = cur.fetchone()
                    enabled_raw = str((row or {}).get("setting_value", cfg["default_enabled"])).strip().lower()
                    enabled = enabled_raw in ("1", "true", "yes", "on")

                    cur.execute(
                        "SELECT id, asof_utc, model, elapsed_s, status, error_msg, triggered_by, "
                        "       big4_quant_signal, overall_label, overall_score, "
                        "       direction_verdict, "
                        "       LEFT(analysis_summary_zh, 200) AS analysis_summary_short, "
                        "       created_at "
                        "FROM big4_analysis_runs WHERE provider=%s ORDER BY id DESC LIMIT 1",
                        (provider,),
                    )
                    last_run = cur.fetchone()

                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM big4_analysis_runs "
                        "WHERE provider=%s AND created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)",
                        (provider,),
                    )
                    runs_24h = int((cur.fetchone() or {}).get("cnt", 0) or 0)
            finally:
                conn.close()

            if last_run and last_run.get("overall_score") is not None:
                last_run["overall_score"] = float(last_run["overall_score"])

            return {
                "success": True,
                "data": {
                    "enabled": enabled,
                    "last_run": last_run,
                    "runs_24h": runs_24h,
                    "interval_hours": INTERVAL_HOURS,
                },
            }
        except Exception as e:
            logger.error(f"[Big4分析 API/{provider}] /status 失败: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    class ToggleRequest(BaseModel):
        enabled: bool

    @router.post("/toggle")
    async def toggle(request: ToggleRequest):
        try:
            val = "1" if request.enabled else "0"
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO system_settings (setting_key, setting_value) "
                        "VALUES (%s, %s) ON DUPLICATE KEY UPDATE setting_value=%s",
                        (cfg["setting_key"], val, val),
                    )
            finally:
                conn.close()
            return {"success": True, "enabled": request.enabled}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/runs")
    async def list_runs(limit: int = Query(20, ge=1, le=100)):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, asof_utc, model, elapsed_s, status, error_msg, triggered_by, "
                        "       big4_quant_signal, overall_label, overall_score, "
                        "       direction_verdict, "
                        "       LEFT(analysis_summary_zh, 200) AS analysis_summary_short, "
                        "       created_at "
                        "FROM big4_analysis_runs WHERE provider=%s "
                        "ORDER BY id DESC LIMIT %s",
                        (provider, limit),
                    )
                    runs = cur.fetchall()
            finally:
                conn.close()
            for r in runs:
                if r.get("overall_score") is not None:
                    r["overall_score"] = float(r["overall_score"])
            return {"success": True, "data": runs, "count": len(runs)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/detail")
    async def get_detail(run_id: int = Query(..., ge=1)):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, asof_utc, model, elapsed_s, status, error_msg, triggered_by, "
                        "       big4_quant_signal, overall_label, overall_score, "
                        "       direction_verdict, analysis_summary_zh, per_coin_json, "
                        "       created_at "
                        "FROM big4_analysis_runs WHERE provider=%s AND id=%s",
                        (provider, run_id),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            if not row:
                raise HTTPException(status_code=404, detail="记录不存在")
            if row.get("overall_score") is not None:
                row["overall_score"] = float(row["overall_score"])
            return {"success": True, "data": row}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/run-now")
    async def run_now():
        if not _run_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="上一轮还在跑, 请稍后再试")

        def _bg():
            try:
                from app.services.big4_comprehensive_analyzer import run_big4_analysis_round
                run_big4_analysis_round(provider, triggered_by="manual")
            except Exception as e:
                logger.error(f"[Big4分析 API/{provider}] 手动跑异常: {e}", exc_info=True)
            finally:
                try:
                    _run_lock.release()
                except Exception:
                    pass

        threading.Thread(
            target=_bg, daemon=True, name=f"big4_analysis_{provider}_manual"
        ).start()
        return {"success": True, "message": "已在后台启动一轮"}

    return router


gemini_big4_router = create_big4_analysis_router(
    "gemini", "/api/gemini-big4-analysis", "Gemini Big4综合行情"
)
deepseek_big4_router = create_big4_analysis_router(
    "deepseek", "/api/deepseek-big4-analysis", "DeepSeek Big4综合行情"
)
