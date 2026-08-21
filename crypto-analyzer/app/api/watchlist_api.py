"""合约自选 HTTP API — 列表 / 加减币 / 手动开仓."""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from app.services.watchlist_config import (
    WATCHLIST_DEFAULT_MARGIN_USD,
    WATCHLIST_LEVERAGE,
    WATCHLIST_PRICE_REFRESH_SECONDS,
    WATCHLIST_SL_PCT,
    WATCHLIST_SOURCE,
    WATCHLIST_TP_PCT,
)

router = APIRouter(prefix="/api/watchlist", tags=["合约自选"])


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


def _serialize(row: Any) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        out = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                out[k] = v.isoformat(sep=" ", timespec="seconds")
            elif hasattr(v, "__float__") and k not in ("id", "leverage"):
                try:
                    out[k] = float(v)
                except Exception:
                    out[k] = v
            else:
                out[k] = v
        return out
    return row


class AddSymbolBody(BaseModel):
    symbol: str = Field(..., min_length=3, max_length=32)


class PlaceOrderBody(BaseModel):
    symbol: str
    side: str
    order_type: str = "limit"
    limit_price: Optional[float] = None
    leverage: Optional[int] = None
    margin: Optional[float] = None
    sl_pct: Optional[float] = None
    tp_pct: Optional[float] = None


def _allowed_universe(conn) -> List[str]:
    from app.services.market_cap_universe import load_config_yaml_symbols_ranked
    from app.services.securities_filter import is_security
    from app.utils.futures_symbol import futures_symbol_rating_canonical

    out: List[str] = []
    seen = set()
    for raw in load_config_yaml_symbols_ranked():
        canon = futures_symbol_rating_canonical(raw)
        if not canon or canon in seen or is_security(canon):
            continue
        seen.add(canon)
        out.append(canon)
    return out


@router.get("")
def watchlist_overview():
    from app.services.trading_gates import (
        get_symbol_rating_info,
        is_live_trading_enabled,
        check_live_symbol_allowed,
    )
    from app.services.watchlist_store import list_watchlist_symbols
    from app.utils.futures_symbol import futures_symbol_clean
    from app.utils.futures_price import build_futures_limit_trigger_price_map

    conn = _connect()
    try:
        rows = list_watchlist_symbols(conn)
        symbols = [r["symbol"] for r in rows]
        prices = build_futures_limit_trigger_price_map(
            conn, symbols, max_age_seconds=60, log_tag="watchlist",
        )
        change_map = {}
        if symbols:
            keys = []
            for s in symbols:
                keys.append(s)
                c = futures_symbol_clean(s)
                if c not in keys:
                    keys.append(c)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT symbol, current_price, change_24h FROM price_stats_24h "
                    "WHERE symbol IN (" + ",".join(["%s"] * len(keys)) + ")",
                    keys,
                )
                for r in cur.fetchall() or []:
                    change_map[r["symbol"]] = r
                    change_map[futures_symbol_clean(r["symbol"])] = r
        live_on = bool(is_live_trading_enabled())
        items = []
        for row in rows:
            sym = row["symbol"]
            clean = futures_symbol_clean(sym)
            px = prices.get(sym) or prices.get(clean)
            ch = change_map.get(sym) or change_map.get(clean) or {}
            rating_level, _, locked = get_symbol_rating_info(sym, conn)
            live_ok, live_why = check_live_symbol_allowed(sym, conn) if live_on else (False, "live_trading_enabled=0")
            items.append({
                "id": row.get("id"),
                "symbol": sym,
                "price": px,
                "change_24h": float(ch.get("change_24h") or 0) if ch else None,
                "rating_level": rating_level,
                "rating_locked": bool(locked),
                "live_sync_ok": bool(live_ok),
                "live_sync_reason": "" if live_ok else str(live_why or ""),
            })
        return {
            "ok": True,
            "source": WATCHLIST_SOURCE,
            "live_trading_enabled": live_on,
            "refresh_seconds": WATCHLIST_PRICE_REFRESH_SECONDS,
            "defaults": {
                "leverage": WATCHLIST_LEVERAGE,
                "margin": WATCHLIST_DEFAULT_MARGIN_USD,
                "sl_pct": WATCHLIST_SL_PCT,
                "tp_pct": WATCHLIST_TP_PCT,
            },
            "items": items,
        }
    except Exception as e:
        logger.error(f"[自选] overview 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.get("/universe")
def watchlist_universe(q: str = ""):
    conn = _connect()
    try:
        universe = _allowed_universe(conn)
        needle = (q or "").strip().upper().replace("/", "")
        if needle:
            universe = [s for s in universe if needle in s.upper().replace("/", "")]
        return {"ok": True, "symbols": universe[:80], "total": len(universe)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.post("")
def watchlist_add(body: AddSymbolBody):
    from app.services.securities_filter import is_security
    from app.services.watchlist_store import add_watchlist_symbol
    from app.utils.futures_symbol import futures_symbol_rating_canonical

    conn = _connect()
    try:
        canon = futures_symbol_rating_canonical(body.symbol)
        if not canon.endswith("/USDT"):
            raise HTTPException(status_code=400, detail="只支持 U 本位永续，如 BTC/USDT")
        if is_security(canon):
            raise HTTPException(status_code=400, detail="该交易对被证券过滤拦截")
        universe = set(_allowed_universe(conn))
        if canon not in universe:
            raise HTTPException(status_code=400, detail="不在 config.yaml 市值池内")
        result = add_watchlist_symbol(conn, canon)
        return {"ok": True, **result}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"[自选] 添加失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.delete("/{symbol:path}")
def watchlist_remove(symbol: str):
    from app.services.watchlist_store import remove_watchlist_symbol

    conn = _connect()
    try:
        removed = remove_watchlist_symbol(conn, symbol)
        return {"ok": True, "removed": removed}
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.post("/order")
def watchlist_order(body: PlaceOrderBody):
    from app.services.watchlist_orders import place_watchlist_order

    conn = _connect()
    try:
        payload, err = place_watchlist_order(
            conn,
            symbol=body.symbol,
            side=body.side,
            order_type=body.order_type,
            limit_price=body.limit_price,
            leverage=body.leverage,
            margin=body.margin,
            sl_pct=body.sl_pct,
            tp_pct=body.tp_pct,
        )
        if err:
            raise HTTPException(status_code=400, detail=err)
        return {"ok": True, **(payload or {})}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[自选] 下单失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@router.get("/orders")
def watchlist_orders(limit: int = 40):
    from app.services.watchlist_orders import list_watchlist_orders, list_watchlist_positions

    conn = _connect()
    try:
        return {
            "ok": True,
            "orders": [_serialize(r) for r in list_watchlist_orders(conn, limit=limit)],
            "positions": [_serialize(r) for r in list_watchlist_positions(conn)],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass
