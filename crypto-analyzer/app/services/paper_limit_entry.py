"""模拟盘限价开仓 — 做多 -1%、做空 +1%，30 分钟有效。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, Optional

from loguru import logger

from app.utils.futures_symbol import futures_symbol_rating_canonical

# 模拟盘默认账户
PAPER_ACCOUNT_ID = 2

# 限价偏移：做多低于市价 1%，做空高于市价 1%
PAPER_LIMIT_LONG_OFFSET = Decimal("0.01")
PAPER_LIMIT_SHORT_OFFSET = Decimal("0.01")

# 挂单有效期（分钟）
PAPER_LIMIT_TIMEOUT_MINUTES = 30


def calc_paper_limit_price(side: str, ref_price: float) -> float:
    """根据参考价计算模拟盘限价。"""
    px = Decimal(str(ref_price))
    if side.upper() == "LONG":
        return float((px * (Decimal("1") - PAPER_LIMIT_LONG_OFFSET)).quantize(Decimal("0.00000001")))
    return float((px * (Decimal("1") + PAPER_LIMIT_SHORT_OFFSET)).quantize(Decimal("0.00000001")))


def _calc_sl_tp(side: str, limit_price: float, sl_pct: Optional[float], tp_pct: Optional[float],
                sl_price: Optional[float], tp_price: Optional[float]) -> tuple:
    if sl_price is not None and tp_price is not None:
        return sl_price, tp_price
    lp = Decimal(str(limit_price))
    sl = tp = None
    if sl_pct is not None:
        sp = Decimal(str(sl_pct))
        sl = float(lp * (Decimal("1") - sp / 100) if side.upper() == "LONG" else lp * (Decimal("1") + sp / 100))
    if tp_pct is not None:
        tpv = Decimal(str(tp_pct))
        tp = float(lp * (Decimal("1") + tpv / 100) if side.upper() == "LONG" else lp * (Decimal("1") - tpv / 100))
    return sl, tp


def has_pending_paper_limit_order(
    conn, symbol: str, side: str, source: str, account_id: int = PAPER_ACCOUNT_ID,
) -> bool:
    """同 symbol + side + source 是否已有未成交限价开仓单。"""
    symbol = futures_symbol_rating_canonical(symbol)
    order_side = f"OPEN_{side.upper()}"
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM futures_orders
                WHERE account_id=%s AND symbol=%s AND side=%s
                  AND order_source=%s AND status='PENDING' AND order_type='LIMIT'
                LIMIT 1
                """,
                (account_id, symbol, order_side, source),
            )
            return cur.fetchone() is not None
    except Exception as e:
        logger.warning(f"[限价开仓] 检查挂单失败 {symbol} {side}: {e}")
        return False


def create_paper_limit_order(
    conn,
    *,
    symbol: str,
    side: str,
    ref_price: float,
    source: str,
    leverage: int,
    margin: float,
    quantity: Optional[float] = None,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    entry_signal_type: str = "",
    entry_reason: str = "",
    entry_score: Optional[float] = None,
    signal_components: Optional[Dict] = None,
    max_hold_minutes: Optional[int] = None,
    planned_close_time: Optional[datetime] = None,
    signal_id: Optional[int] = None,
    strategy_id: Optional[int] = None,
    account_id: int = PAPER_ACCOUNT_ID,
    timeout_minutes: int = PAPER_LIMIT_TIMEOUT_MINUTES,
) -> Optional[int]:
    """
    创建模拟盘限价开仓单（不直接成交）。

    Returns:
        futures_orders.id，失败返回 None。
    """
    symbol = futures_symbol_rating_canonical(symbol)
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        logger.error(f"[限价开仓] 无效方向 {side}")
        return None

    if has_pending_paper_limit_order(conn, symbol, side, source, account_id):
        logger.info(f"[限价开仓] 跳过 {symbol} {side} source={source}: 已有挂单")
        return None

    limit_price = calc_paper_limit_price(side, ref_price)
    sl_price, tp_price = _calc_sl_tp(
        side, limit_price, stop_loss_pct, take_profit_pct, stop_loss_price, take_profit_price,
    )

    if quantity is None or quantity <= 0:
        notional = margin * leverage
        quantity = round(notional / limit_price, 6)
    else:
        notional = round(quantity * limit_price, 2)

    if quantity <= 0:
        logger.error(f"[限价开仓] {symbol} {side} 数量非正")
        return None

    margin_required = notional / leverage
    fee = notional * 0.0004
    order_id = f"FUT-{uuid.uuid4().hex[:16].upper()}"
    order_side = f"OPEN_{side}"

    meta: Dict[str, Any] = {
        "timeout_minutes": timeout_minutes,
        "ref_price": ref_price,
        "margin": margin_required,
        "max_hold_minutes": max_hold_minutes,
        "entry_score": entry_score,
        "entry_reason": entry_reason,
        "signal_components": signal_components,
    }
    if planned_close_time:
        meta["planned_close_time"] = planned_close_time.strftime("%Y-%m-%d %H:%M:%S")

    notes = json.dumps(meta, ensure_ascii=False)

    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO futures_orders (
                    account_id, order_id, symbol,
                    side, order_type, leverage,
                    price, quantity, executed_quantity,
                    margin, total_value, executed_value,
                    fee, fee_rate, status,
                    stop_loss_price, take_profit_price,
                    order_source, entry_signal_type, signal_id, strategy_id,
                    notes, created_at
                ) VALUES (
                    %s, %s, %s,
                    %s, 'LIMIT', %s,
                    %s, %s, 0,
                    %s, %s, 0,
                    %s, %s, 'PENDING',
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    account_id, order_id, symbol,
                    order_side, leverage,
                    limit_price, quantity,
                    margin_required, notional,
                    fee, 0.0004,
                    sl_price, tp_price,
                    source, entry_signal_type or source, signal_id, strategy_id,
                    notes, datetime.now(),
                ),
            )
            db_id = cur.lastrowid

        logger.info(
            f"[限价开仓] 挂单 {symbol} {side} @ {limit_price:.6g} "
            f"(参考价 {ref_price:.6g}, 有效期 {timeout_minutes}min) "
            f"SL={sl_price} TP={tp_price} qty={quantity} source={source} id={db_id}"
        )
        return db_id
    except Exception as e:
        logger.error(f"[限价开仓] 创建失败 {symbol} {side}: {e}")
        return None


def parse_order_notes(notes: Optional[str]) -> Dict[str, Any]:
    if not notes:
        return {}
    try:
        data = json.loads(notes)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
