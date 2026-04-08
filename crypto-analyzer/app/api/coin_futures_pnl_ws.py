"""
币本位持仓：WebSocket 实时推送盈亏（Binance markPrice 流 + 与 batch 一致的 USDT/币本位 映射）
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any, Dict, List, Optional, Set, Tuple
import time
from urllib.parse import quote

import pymysql
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger

try:
    import websockets as ws_client
except ImportError:
    ws_client = None

from app.trading.dapi_coin_margined_price import (
    build_dapi_usd_perp_fmt_map,
    canonical_coin_usd_display,
    fapi_usdt_perp_symbol_for_coin_usd,
    get_all_dapi_ticker_prices,
)


def _sync_load_positions(account_id: int, mysql_config: dict) -> List[Dict[str, Any]]:
    conn = pymysql.connect(
        host=mysql_config.get("host", "localhost"),
        port=mysql_config.get("port", 3306),
        user=mysql_config.get("user", "root"),
        password=mysql_config.get("password", ""),
        database=mysql_config.get("database", "binance-data"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, symbol, position_side,
                   COALESCE(avg_entry_price, entry_price) AS entry_price,
                   quantity, margin, leverage
            FROM futures_positions
            WHERE account_id = %s AND status = 'open'
            ORDER BY open_time DESC
            """,
            (account_id,),
        )
        rows = cur.fetchall()
        cur.close()
        out = []
        for r in rows:
            x = dict(r)
            for k, v in list(x.items()):
                if isinstance(v, Decimal):
                    x[k] = float(v)
            out.append(x)
        return out
    finally:
        conn.close()


def _build_stream_plan(
    position_symbols: List[str],
) -> Tuple[List[str], List[str], Dict[str, str]]:
    """
    返回 (fstream 流名列表, dstream 流名列表, Binance 消息 s 字段 -> 内部 symbol 如 OP/USD)
    """
    rows = get_all_dapi_ticker_prices()
    dapi_fmt = build_dapi_usd_perp_fmt_map(rows)

    f_streams: Set[str] = set()
    d_streams: Set[str] = set()
    evt_to_internal: Dict[str, str] = {}

    for sym in position_symbols:
        canon = canonical_coin_usd_display(sym)
        if canon and canon in dapi_fmt:
            base = canon.split("/")[0]
            d_streams.add(f"{base.lower()}usd_perp@markPrice@1s")
            evt_to_internal[f"{base.upper()}USD_PERP"] = sym
        else:
            usdt = fapi_usdt_perp_symbol_for_coin_usd(sym)
            if usdt:
                f_streams.add(f"{usdt.lower()}@markPrice@1s")
                evt_to_internal[usdt] = sym

    return list(f_streams), list(d_streams), evt_to_internal


def _compute_items(
    positions: List[Dict[str, Any]], prices: Dict[str, float]
) -> Tuple[List[Dict[str, Any]], float]:
    """根据当前价映射计算每条持仓的浮盈与合计。"""
    items: List[Dict[str, Any]] = []
    total = 0.0
    for p in positions:
        sym = p["symbol"]
        price = prices.get(sym)
        if price is None:
            continue
        entry = float(p["entry_price"])
        qty = float(p["quantity"])
        margin = float(p.get("margin") or 0)
        side = p.get("position_side") or "LONG"
        if side == "LONG":
            pnl = (price - entry) * qty
        else:
            pnl = (entry - price) * qty
        pct = (pnl / margin * 100.0) if margin > 0 else 0.0
        total += pnl
        items.append(
            {
                "position_id": int(p["id"]),
                "symbol": sym,
                "current_price": float(price),
                "unrealized_pnl": float(pnl),
                "unrealized_pnl_pct": float(pct),
            }
        )
    return items, total


def _parse_mark_price(msg: str) -> Optional[Tuple[str, float]]:
    """
    解析 Binance combined stream 或单流消息，返回 (s 大写, price)。
    """
    try:
        j = json.loads(msg)
    except json.JSONDecodeError:
        return None
    payload = j.get("data", j)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    if payload.get("e") != "markPriceUpdate":
        return None
    s = payload.get("s")
    p = payload.get("p")
    if not s or p is None:
        return None
    return str(s).upper(), float(p)


def attach_coin_futures_pnl_ws(router: APIRouter, mysql_config: dict) -> None:
    """
    注册 WebSocket: GET ws://.../api/coin-futures/ws/positions-pnl?account_id=3
    """

    @router.websocket("/ws/positions-pnl")
    async def positions_pnl_ws(
        websocket: WebSocket, account_id: int = Query(3, ge=1)
    ):
        if ws_client is None:
            await websocket.close(code=1011)
            return

        await websocket.accept()

        loop = asyncio.get_event_loop()
        try:
            positions = await loop.run_in_executor(
                None, _sync_load_positions, account_id, mysql_config
            )
        except Exception as e:
            logger.warning(f"WS 持仓加载失败: {e}")
            await websocket.send_json({"type": "error", "message": str(e)})
            await websocket.close()
            return

        if not positions:
            await websocket.send_json({"type": "empty", "positions": []})
            await websocket.close()
            return

        syms = [p["symbol"] for p in positions]
        f_streams, d_streams, evt_to_internal = _build_stream_plan(syms)

        prices: Dict[str, float] = {}

        def apply_binance_symbol(binance_s: str, price: float) -> None:
            internal = evt_to_internal.get(binance_s)
            if internal:
                prices[internal] = price

        send_lock = asyncio.Lock()
        last_send = 0.0
        min_interval = 0.08  # 约 12 次/秒，减轻前端压力

        async def maybe_send() -> None:
            nonlocal last_send
            items, total = _compute_items(positions, prices)
            if not items:
                return
            async with send_lock:
                now = time.monotonic()
                if now - last_send < min_interval:
                    return
                last_send = now
                await websocket.send_json(
                    {
                        "type": "tick",
                        "prices": {k: float(v) for k, v in prices.items()},
                        "items": items,
                        "total_unrealized_pnl": float(total),
                    }
                )

        async def pump_from_url(url: str) -> None:
            try:
                async with ws_client.connect(
                    url, ping_interval=20, ping_timeout=20
                ) as ws:
                    async for raw in ws:
                        parsed = _parse_mark_price(raw)
                        if not parsed:
                            continue
                        bsym, px = parsed
                        apply_binance_symbol(bsym, px)
                        try:
                            await maybe_send()
                        except Exception as send_err:
                            logger.debug(f"WS 推送失败: {send_err}")
                            return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"Binance WS 连接结束: {e}")

        urls: List[str] = []
        if f_streams:
            sp = "/".join(sorted(f_streams))
            urls.append(
                f"wss://fstream.binance.com/stream?streams={quote(sp, safe='/')}"
            )
        if d_streams:
            sp = "/".join(sorted(d_streams))
            urls.append(
                f"wss://dstream.binance.com/stream?streams={quote(sp, safe='/')}"
            )

        if not urls:
            await websocket.send_json(
                {"type": "error", "message": "无法订阅任何行情流（请检查交易对）"}
            )
            await websocket.close()
            return

        await websocket.send_json(
            {
                "type": "ready",
                "account_id": account_id,
                "fstream_streams": len(f_streams),
                "dstream_streams": len(d_streams),
            }
        )

        pump_tasks: List[asyncio.Task] = [
            asyncio.create_task(pump_from_url(u)) for u in urls
        ]

        async def wait_client_disconnect() -> None:
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                return

        try:
            await wait_client_disconnect()
        finally:
            for t in pump_tasks:
                t.cancel()
            await asyncio.gather(*pump_tasks, return_exceptions=True)
