"""
BinanceDataHub HTTP 接口 (内部用)
================================

设计目标:
    BinanceDataHub 是单一的币安 REST 抓取源, 只在 app/main.py 进程内运行.
    其他独立进程 (smart_trader_service /
    multi_strategy 等) 通过本路由暴露的 HTTP 端点访问 hub 数据, 而不是各自
    init 一个 hub - 否则会出现多个进程并发打币安, 违背 "单源" 原则.

数据流:
    业务代码调 get_global_data_hub() -> 本进程无 hub 则返回 HubHttpProxy ->
    HubHttpProxy 内部 HTTP 跳到本路由 -> 命中 main 进程的 hub 单例 -> 返回数据

端点全部挂在 /api/datahub/ 前缀下, 仅供本机进程访问 (localhost:9020),
不对外开放. 外部访问 /api/futures/* 已有的接口.

序列化约定:
    - Decimal -> str (避免浮点精度问题)
    - datetime -> ISO8601 字符串
    - None -> null
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from loguru import logger

router = APIRouter(prefix="/api/datahub", tags=["datahub"])


def _hub_or_503():
    """获取本进程 hub 单例, 没有则 503. 调用方应该挂在 app/main.py 启动后才有响应."""
    from app.services.binance_data_hub import get_global_data_hub
    # 这里直接读单例, 不走 fallback (路由层就是 hub 所在进程, 没有 hub 是真异常)
    from app.services import binance_data_hub as _m
    hub = _m._global_hub
    if hub is None:
        raise HTTPException(status_code=503, detail="BinanceDataHub not initialized in this process")
    return hub


def _dec_to_str(v: Optional[Decimal]) -> Optional[str]:
    return str(v) if v is not None else None


# -----------------------------------------------------------------------------
# 价格 / 批量价格
# -----------------------------------------------------------------------------

@router.get("/price/{symbol:path}")
async def datahub_get_price(
    symbol: str,
    max_age_seconds: int = Query(90, ge=1, le=3600),
    allow_rest: bool = Query(True),
):
    """单 symbol 取价 (异步, 同 hub.get_price)."""
    hub = _hub_or_503()
    sym = symbol.replace("%2F", "/")
    p = await hub.get_price(sym, max_age_seconds=max_age_seconds, allow_rest_fallback=allow_rest)
    return {"symbol": sym, "price": _dec_to_str(p)}


@router.get("/trade-price/{symbol:path}")
def datahub_get_trade_price(
    symbol: str,
    max_age_seconds: int = Query(90, ge=1, le=3600),
    allow_rest: bool = Query(True),
    allow_db: bool = Query(True),
):
    """Synchronous futures trade price: mark/ticker first, optional DB fallback."""
    hub = _hub_or_503()
    sym = symbol.replace("%2F", "/")
    p = hub.get_trade_price_sync(
        sym,
        max_age_seconds=max_age_seconds,
        allow_rest_fallback=allow_rest,
        allow_db_fallback=allow_db,
    )
    return {"symbol": sym, "price": _dec_to_str(p)}


@router.post("/prices/batch")
async def datahub_get_prices_batch(
    symbols: List[str] = Body(..., embed=True),
    max_age_seconds: int = Body(90),
):
    """批量价格, 全部命中缓存 = 0 REST."""
    hub = _hub_or_503()
    out = await hub.get_prices_batch(symbols, max_age_seconds=max_age_seconds)
    return {"prices": {k: str(v) for k, v in out.items()}}


# -----------------------------------------------------------------------------
# K 线
# -----------------------------------------------------------------------------

@router.get("/klines/{symbol:path}")
async def datahub_get_klines(
    symbol: str,
    interval: str = Query("5m"),
    limit: int = Query(1, ge=1, le=1500),
    allow_rest: bool = Query(True),
):
    """K 线 (DB 优先, REST 兜底)."""
    hub = _hub_or_503()
    sym = symbol.replace("%2F", "/")
    rows = await hub.get_klines(sym, interval=interval, limit=limit, allow_rest_fallback=allow_rest)
    out: List[Dict[str, Any]] = []
    for r in rows or []:
        out.append({
            "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
            "volume": r.get("volume", 0),
            "open_time": r["open_time"].isoformat() if r.get("open_time") else None,
            "close_time": r["close_time"].isoformat() if r.get("close_time") else None,
        })
    return {"klines": out}


# -----------------------------------------------------------------------------
# 全市场 ticker / premiumIndex 快照 (供批量场景)
# -----------------------------------------------------------------------------

@router.get("/ticker_map")
async def datahub_get_ticker_map(market: str = Query("futures")):
    hub = _hub_or_503()
    m = hub.get_full_ticker_map(market=market)
    return {"market": market, "prices": {k: str(v) for k, v in m.items()}}


@router.get("/premium_map")
async def datahub_get_premium_map(market: str = Query("futures")):
    hub = _hub_or_503()
    m = hub.get_premium_index_map(market=market)
    return {"market": market, "mark_prices": {k: str(v) for k, v in m.items()}}


@router.get("/funding_rate/{symbol:path}")
async def datahub_get_funding_rate(symbol: str):
    hub = _hub_or_503()
    sym = symbol.replace("%2F", "/")
    r = hub.get_funding_rate_sync(sym)
    return {"symbol": sym, "funding_rate": _dec_to_str(r)}


# -----------------------------------------------------------------------------
# 通用 REST 转发 (供 binance_futures_collector / price_collector 用)
# 限定: 端点 path 必须以 / 开头, 不接受完整 URL. 防止任意 URL 转发.
# -----------------------------------------------------------------------------

_ALLOWED_PREFIXES_FAPI = ("/fapi/", "/futures/data/")
_ALLOWED_PREFIXES_SPOT = ("/api/v3/",)


def _validate_path(path: str, allowed_prefixes: tuple) -> None:
    if not path.startswith(allowed_prefixes):
        raise HTTPException(
            status_code=400,
            detail=f"path '{path}' 不在允许的前缀范围 {allowed_prefixes}",
        )


@router.post("/rest/fapi/get")
async def datahub_fapi_get(
    path: str = Body(...),
    params: Optional[dict] = Body(None),
    timeout: float = Body(8.0),
):
    """通用 fapi GET 转发. 经过 hub 的熔断 + 令牌桶限速."""
    _validate_path(path, _ALLOWED_PREFIXES_FAPI)
    hub = _hub_or_503()
    data = await hub.fapi_request_get(path, params, timeout=timeout)
    return {"data": data}


@router.post("/rest/spot/get")
async def datahub_spot_get(
    path: str = Body(...),
    params: Optional[dict] = Body(None),
    timeout: float = Body(8.0),
):
    """通用现货 GET 转发."""
    _validate_path(path, _ALLOWED_PREFIXES_SPOT)
    hub = _hub_or_503()
    data = await hub.spot_request_get(path, params, timeout=timeout)
    return {"data": data}


# -----------------------------------------------------------------------------
# 健康检查
# -----------------------------------------------------------------------------

@router.get("/health")
async def datahub_health():
    """对外暴露 hub 状态, 供 HubHttpProxy 启动时探活."""
    from app.services import binance_data_hub as _m
    hub = _m._global_hub
    if hub is None:
        return {"ok": False, "reason": "hub_not_initialized"}
    return {
        "ok": True,
        "ticker_cache_size": len(hub._ticker_cache),
        "premium_cache_size": len(hub._premium_cache),
        "stat_rest_calls": hub._stat_rest_calls,
        "stat_rejected_ban": hub._stat_rest_rejected_by_ban,
        "stat_rejected_bucket": hub._stat_rest_rejected_by_bucket,
    }
