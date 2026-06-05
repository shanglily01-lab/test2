"""
Gemini 市场情绪 + 川普分析 HTTP API.

端点:
- GET  /api/gemini-sentiment/status       返回 kill switch + 最近一轮数据
- POST /api/gemini-sentiment/toggle       切换 kill switch
- GET  /api/gemini-sentiment/runs?limit=20 最近 N 轮运行记录
- POST /api/gemini-sentiment/run-now      手动触发一轮
"""
from __future__ import annotations

import threading
from typing import Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from loguru import logger


router = APIRouter(prefix="/api/gemini-sentiment", tags=["Gemini情绪分析"])


def _get_db_config():
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


# ============================================================
# 状态 + 开关
# ============================================================
@router.get("/status")
async def status():
    """返回当前 kill switch + 最近一轮分析数据."""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT setting_value FROM system_settings "
                    "WHERE setting_key='gemini_sentiment_enabled' LIMIT 1"
                )
                row = cur.fetchone()
                enabled_raw = str((row or {}).get('setting_value', '1')).strip().lower()
                enabled = enabled_raw in ('1', 'true', 'yes', 'on')

                cur.execute(
                    "SELECT id, asof_utc, model, elapsed_s, status, error_msg, "
                    "       triggered_by, "
                    "       market_sentiment_label, market_sentiment_score, "
                    "       market_direction_verdict, "
                    "       LEFT(sentiment_summary_zh, 200) AS sentiment_summary_short, "
                    "       trump_impact_label, trump_impact_score, "
                    "       LEFT(trump_analysis_zh, 200) AS trump_analysis_short, "
                    "       trump_key_topics, trump_market_impact, "
                    "       created_at "
                    "FROM gemini_sentiment_runs ORDER BY id DESC LIMIT 1"
                )
                last_run = cur.fetchone()

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM gemini_sentiment_runs "
                    "WHERE created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR)"
                )
                runs_24h = int((cur.fetchone() or {}).get('cnt', 0) or 0)
        finally:
            conn.close()

        return {
            "success": True,
            "data": {
                "enabled": enabled,
                "last_run": last_run,
                "runs_24h": runs_24h,
                "interval_hours": 8,
            },
        }
    except Exception as e:
        logger.error(f"[Gemini情绪 API] /status 失败: {e}")
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
                    "VALUES ('gemini_sentiment_enabled', %s) "
                    "ON DUPLICATE KEY UPDATE setting_value = %s",
                    (val, val),
                )
        finally:
            conn.close()
        logger.info(f"[Gemini情绪 API] kill switch -> {val}")
        return {"success": True, "enabled": request.enabled}
    except Exception as e:
        logger.error(f"[Gemini情绪 API] /toggle 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 运行记录
# ============================================================
@router.get("/runs")
async def list_runs(limit: int = Query(20, ge=1, le=100)):
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, asof_utc, model, elapsed_s, status, error_msg, triggered_by, "
                    "       market_sentiment_label, market_sentiment_score, "
                    "       market_direction_verdict, "
                    "       LEFT(sentiment_summary_zh, 200) AS sentiment_summary_short, "
                    "       trump_impact_label, trump_impact_score, "
                    "       LEFT(trump_analysis_zh, 200) AS trump_analysis_short, "
                    "       trump_key_topics, trump_market_impact, "
                    "       created_at "
                    "FROM gemini_sentiment_runs "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                runs = cur.fetchall()
        finally:
            conn.close()

        for r in runs:
            if r.get('market_sentiment_score') is not None:
                r['market_sentiment_score'] = float(r['market_sentiment_score'])
            if r.get('trump_impact_score') is not None:
                r['trump_impact_score'] = float(r['trump_impact_score'])

        return {"success": True, "data": runs, "count": len(runs)}
    except Exception as e:
        logger.error(f"[Gemini情绪 API] /runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/detail")
async def get_detail(run_id: int = Query(..., ge=1)):
    """获取某一轮的完整分析原文。"""
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, asof_utc, model, elapsed_s, status, error_msg, triggered_by, "
                    "       sentiment_summary_zh, market_sentiment_label, market_sentiment_score, "
                    "       market_direction_verdict, "
                    "       trump_analysis_zh, trump_impact_label, trump_impact_score, "
                    "       trump_key_topics, trump_market_impact, "
                    "       created_at "
                    "FROM gemini_sentiment_runs "
                    "WHERE id=%s",
                    (run_id,),
                )
                row = cur.fetchone()
        finally:
            conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="记录不存在")

        if row.get('market_sentiment_score') is not None:
            row['market_sentiment_score'] = float(row['market_sentiment_score'])
        if row.get('trump_impact_score') is not None:
            row['trump_impact_score'] = float(row['trump_impact_score'])

        return {"success": True, "data": row}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Gemini情绪 API] /detail 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 手动触发
# ============================================================
_run_lock = threading.Lock()


@router.post("/run-now")
async def run_now():
    if not _run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一轮还在跑, 请稍后再试")

    def _run_in_background(t: float):
        try:
            from app.services.gemini_sentiment_analyzer import run_sentiment_round
            run_sentiment_round(triggered_by='manual')
        except Exception as e:
            logger.error(f"[Gemini情绪 API] 后台跑一轮异常: {e}", exc_info=True)
        finally:
            try:
                _run_lock.release()
            except Exception:
                pass

    import time
    t = threading.Thread(
        target=_run_in_background,
        args=(time.time(),),
        name='gemini_sentiment_manual',
        daemon=True,
    )
    t.start()
    return {"success": True, "message": "已在后台启动一轮, 看日志和 /runs 接口"}
