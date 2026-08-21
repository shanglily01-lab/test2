"""合约自选手动开仓 — 限价挂单或市价立刻成交（走 fill_paper_limit_order）。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.paper_limit_entry import create_paper_limit_order
from app.services.watchlist_config import (
    WATCHLIST_ACCOUNT_ID,
    WATCHLIST_DEFAULT_MARGIN_USD,
    WATCHLIST_LEVERAGE,
    WATCHLIST_LIMIT_TIMEOUT_MINUTES,
    WATCHLIST_SL_PCT,
    WATCHLIST_SOURCE,
    WATCHLIST_TP_PCT,
)
from app.utils.futures_symbol import futures_symbol_rating_canonical


def _fail(msg: str) -> Tuple[None, str]:
    return None, msg


def place_watchlist_order(
    conn,
    *,
    symbol: str,
    side: str,
    order_type: str,
    limit_price: Optional[float] = None,
    leverage: Optional[int] = None,
    margin: Optional[float] = None,
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    返回 (payload, error)。市价：挂单后立刻按实时 ticker 成交；
    限价：PENDING，executor 用实时价触价。
    """
    from app.services.paper_open_gate import gate_simulated_open
    from app.services.trading_gates import get_paper_margin_usd, is_live_trading_enabled
    from app.utils.futures_price import get_futures_limit_trigger_price

    symbol = futures_symbol_rating_canonical(symbol)
    side_u = (side or "").upper()
    if side_u not in ("LONG", "SHORT"):
        return _fail("方向须为 LONG 或 SHORT")
    kind = (order_type or "limit").strip().lower()
    if kind not in ("market", "limit"):
        return _fail("类型须为 market 或 limit")

    lev = int(leverage or WATCHLIST_LEVERAGE)
    if lev < 1 or lev > 125:
        return _fail("杠杆须在 1–125")
    sl = float(sl_pct if sl_pct is not None else WATCHLIST_SL_PCT)
    tp = float(tp_pct if tp_pct is not None else WATCHLIST_TP_PCT)
    if sl <= 0 or sl > 50 or tp <= 0 or tp > 80:
        return _fail("止损/止盈百分比不合法")

    price = get_futures_limit_trigger_price(
        conn, symbol, max_age_seconds=30, log_tag="watchlist",
    )
    if not price or price <= 0:
        return _fail("无法获取实时合约价")

    user_limit = None
    if kind == "limit":
        try:
            user_limit = float(limit_price)
        except (TypeError, ValueError):
            user_limit = 0.0
        if user_limit <= 0:
            return _fail("限价单必须填写委托价")

    paper_margin = float(margin) if margin and float(margin) > 0 else None
    if paper_margin is None:
        paper_margin = float(get_paper_margin_usd(symbol, conn) or WATCHLIST_DEFAULT_MARGIN_USD)
    if paper_margin < 10 or paper_margin > 50000:
        return _fail("保证金须在 10–50000U")

    allowed, gate_reason = gate_simulated_open(
        symbol, side_u, float(price), WATCHLIST_SOURCE,
        catalyst="manual_watchlist",
        leverage=lev,
        sl_pct=sl,
        tp_pct=tp,
        hold_hours=WATCHLIST_LIMIT_TIMEOUT_MINUTES / 60.0,
        account_id=WATCHLIST_ACCOUNT_ID,
        conn=conn,
    )
    if not allowed:
        return _fail(str(gate_reason or "闸门拒绝"))

    fail: List[str] = []
    db_id = create_paper_limit_order(
        conn,
        symbol=symbol,
        side=side_u,
        ref_price=float(price),
        source=WATCHLIST_SOURCE,
        leverage=lev,
        margin=paper_margin,
        stop_loss_pct=sl,
        take_profit_pct=tp,
        entry_signal_type="watchlist_manual",
        entry_reason=f"自选·{'市价' if kind == 'market' else '限价'}{side_u}",
        account_id=WATCHLIST_ACCOUNT_ID,
        timeout_minutes=WATCHLIST_LIMIT_TIMEOUT_MINUTES,
        skip_open_advisor=True,
        failure_reason=fail,
        explicit_limit_price=user_limit,
        min_fill_age_sec=0,
    )
    if not db_id:
        return _fail(fail[0] if fail else "挂单失败")

    order_row = _load_order_by_id(conn, int(db_id))
    if not order_row:
        return _fail("挂单已写入但读回失败")

    live_on = bool(is_live_trading_enabled())
    payload: Dict[str, Any] = {
        "order_db_id": int(db_id),
        "order_id": order_row.get("order_id"),
        "symbol": symbol,
        "side": side_u,
        "order_type": kind,
        "ref_price": float(price),
        "limit_price": float(order_row.get("price") or 0),
        "live_trading_enabled": live_on,
        "status": order_row.get("status"),
        "filled": False,
        "position_id": None,
        "live_sync": None,
    }

    if kind != "market":
        payload["message"] = (
            f"限价已挂 {payload['limit_price']}，实时价触价后成交。"
            f"{'实盘开则成交瞬间同步（仅 L0）' if live_on else '当前实盘关，仅模拟'}"
        )
        return payload, ""

    from app.trading.futures_trading_engine import FuturesTradingEngine
    from app.utils.config_loader import get_db_config

    engine = FuturesTradingEngine(get_db_config())
    result = engine.fill_paper_limit_order(order_row, at_market=True)
    if not result.get("success"):
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE futures_orders
                    SET status='EXPIRED', cancellation_reason=%s,
                        canceled_at=NOW(), updated_at=NOW()
                    WHERE order_id=%s AND status IN ('PENDING','FILLING')
                    """,
                    ("watchlist_market_fill_failed", order_row.get("order_id")),
                )
        except Exception as e:
            logger.warning(f"[自选] 市价失败后过期挂单失败: {e}")
        return _fail(result.get("message") or "市价成交失败")
    payload["filled"] = True
    payload["status"] = "FILLED"
    payload["position_id"] = result.get("position_id")
    payload["fill_price"] = result.get("fill_price") or result.get("entry_price")
    payload["live_sync"] = result.get("live_sync")
    payload["message"] = (
        f"市价已成交 @{payload.get('fill_price')}。"
        f"{'实盘开则已按闸门同步（仅 L0）' if live_on else '当前实盘关，仅模拟'}"
    )
    logger.info(
        f"[自选] 市价成交 {symbol} {side_u} pos={payload['position_id']} "
        f"live={payload['live_sync']}"
    )
    return payload, ""


def cancel_watchlist_order(conn, order_id: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """撤销自选未成交限价单。尚未成交，按 INV-01 此时还没有实盘挂单。"""
    oid = (order_id or "").strip()
    if not oid:
        return _fail("缺少订单号")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, order_id, symbol, side, status
            FROM futures_orders
            WHERE order_id=%s AND account_id=%s AND order_source=%s
            LIMIT 1
            """,
            (oid, WATCHLIST_ACCOUNT_ID, WATCHLIST_SOURCE),
        )
        row = cur.fetchone()
        if not row:
            return _fail("订单不存在或不属于自选")
        st = str(row.get("status") or "").upper()
        if st not in ("PENDING", "FILLING"):
            return _fail(f"状态 {st} 不可撤")
        cur.execute(
            """
            UPDATE futures_orders
            SET status='CANCELLED', cancellation_reason=%s,
                canceled_at=NOW(), updated_at=NOW()
            WHERE order_id=%s AND account_id=%s AND order_source=%s
              AND status IN ('PENDING','FILLING')
            """,
            ("watchlist_manual_cancel", oid, WATCHLIST_ACCOUNT_ID, WATCHLIST_SOURCE),
        )
        n = int(cur.rowcount or 0)
    if n != 1:
        return _fail("撤单失败，订单可能已成交")
    try:
        conn.commit()
    except Exception:
        pass
    logger.info(f"[自选] 撤单 {row.get('symbol')} {oid}")
    return {
        "order_id": oid,
        "symbol": row.get("symbol"),
        "status": "CANCELLED",
        "message": f"{row.get('symbol')} 已撤单",
    }, ""


def _load_order_by_id(conn, db_id: int) -> Optional[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM futures_orders WHERE id=%s LIMIT 1", (db_id,))
        return cur.fetchone()


def list_watchlist_orders(conn, limit: int = 40) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, order_id, symbol, side, order_type, price, quantity,
                   status, avg_fill_price, fill_time, created_at,
                   live_sync_status, stop_loss_price, take_profit_price,
                   leverage, margin, position_id
            FROM futures_orders
            WHERE account_id=%s AND order_source=%s
            ORDER BY id DESC
            LIMIT %s
            """,
            (WATCHLIST_ACCOUNT_ID, WATCHLIST_SOURCE, int(limit)),
        )
        return list(cur.fetchall() or [])


def list_watchlist_positions(conn) -> List[Dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, position_side, leverage, quantity, margin,
                   entry_price, mark_price, unrealized_pnl, unrealized_pnl_pct,
                   stop_loss_price, take_profit_price, open_time, status, source
            FROM futures_positions
            WHERE account_id=%s AND source=%s AND LOWER(status)='open'
            ORDER BY id DESC
            """,
            (WATCHLIST_ACCOUNT_ID, WATCHLIST_SOURCE),
        )
        return list(cur.fetchall() or [])
