"""
data_cache API 路由

提供缓存刷新的手动触发接口和缓存查询接口。
"""
from __future__ import annotations

from fastapi import APIRouter
from loguru import logger

from app.services.data_cache_service import (
    refresh_market_snapshot,
    refresh_market_movers,
    refresh_candidate_pool,
    refresh_position_stats,
    sync_settings_cache,
    get_market_snapshot,
    get_market_movers,
    get_candidate_pool,
    get_position_stats,
    invalidate_setting_cache,
)

router = APIRouter(prefix="/api/data-cache", tags=["data_cache"])


@router.post("/refresh/market-snapshot")
async def api_refresh_market_snapshot():
    """手动刷新市场概览快照."""
    result = refresh_market_snapshot()
    return {"status": "ok", "result": result}


@router.post("/refresh/market-movers")
async def api_refresh_market_movers():
    """手动刷新市场异动快照."""
    result = refresh_market_movers()
    return {"status": "ok", "result": result}


@router.post("/refresh/candidate-pool")
async def api_refresh_candidate_pool():
    """手动刷新候选交易对池."""
    result = refresh_candidate_pool()
    return {"status": "ok", "result": result}


@router.post("/refresh/position-stats")
async def api_refresh_position_stats():
    """手动刷新持仓统计快照."""
    result = refresh_position_stats()
    return {"status": "ok", "result": result}


@router.post("/refresh/settings-cache")
async def api_refresh_settings_cache():
    """手动刷新系统设置缓存."""
    result = sync_settings_cache()
    invalidate_setting_cache()
    return {"status": "ok", "result": result}


@router.get("/market-snapshot")
async def api_get_market_snapshot():
    """获取市场概览快照."""
    data = get_market_snapshot()
    if data:
        # 转为可序列化 dict
        return {"status": "ok", "data": {k: _safe_val(v) for k, v in data.items()}}
    return {"status": "error", "message": "快照数据不存在"}


@router.get("/market-movers")
async def api_get_market_movers(category: str = None, limit: int = 20):
    """获取市场异动快照."""
    data = get_market_movers(category=category, limit=limit)
    return {"status": "ok", "data": data}


@router.get("/candidate-pool")
async def api_get_candidate_pool(limit: int = 50):
    """获取候选交易对池."""
    data = get_candidate_pool(limit=limit)
    return {"status": "ok", "count": len(data), "data": data}


@router.get("/position-stats")
async def api_get_position_stats(source: str = "gemini_explore"):
    """获取持仓统计快照."""
    data = get_position_stats(source)
    if data:
        return {"status": "ok", "data": {k: _safe_val(v) for k, v in data.items()}}
    return {"status": "error", "message": f"持仓统计 {source} 不存在"}


def _safe_val(v):
    if isinstance(v, (int, float)):
        return v
    try:
        if isinstance(v, bytes):
            v = v.decode("utf-8", errors="replace")
        return str(v)
    except Exception:
        return str(v)
