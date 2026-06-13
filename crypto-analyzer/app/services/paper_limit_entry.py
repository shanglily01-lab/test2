"""模拟盘限价开仓 — 偏移与开关由 system_settings 控制，30 分钟有效。"""
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

# 默认限价偏移（百分比点数，0.5 = 0.5%）
DEFAULT_PAPER_LIMIT_LONG_OFFSET_PCT = 0.5
DEFAULT_PAPER_LIMIT_SHORT_OFFSET_PCT = 0.5
PAPER_LIMIT_OFFSET_MIN_PCT = 0.1
PAPER_LIMIT_OFFSET_MAX_PCT = 1.0

# 向后兼容：模块级常量回退默认值
PAPER_LIMIT_LONG_OFFSET = Decimal(str(DEFAULT_PAPER_LIMIT_LONG_OFFSET_PCT / 100))
PAPER_LIMIT_SHORT_OFFSET = Decimal(str(DEFAULT_PAPER_LIMIT_SHORT_OFFSET_PCT / 100))

# 挂单有效期（分钟）
PAPER_LIMIT_TIMEOUT_MINUTES = 30

# 挂单创建后至少等待 N 秒才允许成交（避免秒成交看起来像市价单）
PAPER_LIMIT_MIN_FILL_AGE_SEC = 60

# 超时处理：expire=放弃(取消) | convert_market=转市价成交
PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE = "expire"
PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET = "convert_market"
DEFAULT_PAPER_LIMIT_TIMEOUT_ACTION = PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE


def _clamp_offset_pct(pct: float) -> float:
    return max(PAPER_LIMIT_OFFSET_MIN_PCT, min(PAPER_LIMIT_OFFSET_MAX_PCT, pct))


def is_paper_limit_entry_enabled() -> bool:
    """模拟盘限价开仓总开关 (system_settings.paper_limit_entry_enabled，默认开)。"""
    from app.services.data_cache_service import get_setting
    val = get_setting("paper_limit_entry_enabled", "1")
    return str(val).strip().lower() in ("1", "true", "yes")


def get_paper_limit_long_offset_pct() -> float:
    """做多限价偏移百分比点数 (0.1~1.0)。"""
    from app.services.data_cache_service import get_setting
    raw = get_setting("paper_limit_long_offset_pct", str(DEFAULT_PAPER_LIMIT_LONG_OFFSET_PCT))
    try:
        return _clamp_offset_pct(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_PAPER_LIMIT_LONG_OFFSET_PCT


def get_paper_limit_short_offset_pct() -> float:
    """做空限价偏移百分比点数 (0.1~1.0)。"""
    from app.services.data_cache_service import get_setting
    raw = get_setting("paper_limit_short_offset_pct", str(DEFAULT_PAPER_LIMIT_SHORT_OFFSET_PCT))
    try:
        return _clamp_offset_pct(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_PAPER_LIMIT_SHORT_OFFSET_PCT


def get_paper_limit_long_offset() -> Decimal:
    return Decimal(str(get_paper_limit_long_offset_pct() / 100))


def get_paper_limit_short_offset() -> Decimal:
    return Decimal(str(get_paper_limit_short_offset_pct() / 100))


def get_paper_limit_timeout_action() -> str:
    """限价单超时：expire=放弃 | convert_market=转市价 (system_settings.paper_limit_timeout_action)。"""
    from app.services.data_cache_service import get_setting

    raw = str(
        get_setting("paper_limit_timeout_action", DEFAULT_PAPER_LIMIT_TIMEOUT_ACTION)
    ).strip().lower()
    if raw in ("convert_market", "market", "convert"):
        return PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET
    return PAPER_LIMIT_TIMEOUT_ACTION_EXPIRE


def calc_paper_limit_price(side: str, ref_price: float) -> float:
    """根据参考价与系统设置计算模拟盘限价。"""
    px = Decimal(str(ref_price))
    if side.upper() == "LONG":
        off = get_paper_limit_long_offset()
        return float((px * (Decimal("1") - off)).quantize(Decimal("0.00000001")))
    off = get_paper_limit_short_offset()
    return float((px * (Decimal("1") + off)).quantize(Decimal("0.00000001")))


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

    if not is_paper_limit_entry_enabled():
        return _open_paper_market_position(
            conn,
            symbol=symbol,
            side=side,
            ref_price=ref_price,
            source=source,
            leverage=leverage,
            margin=margin,
            quantity=quantity,
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            entry_signal_type=entry_signal_type,
            entry_reason=entry_reason,
            entry_score=entry_score,
            max_hold_minutes=max_hold_minutes,
            planned_close_time=planned_close_time,
            signal_id=signal_id,
            strategy_id=strategy_id,
            account_id=account_id,
        )

    if has_pending_paper_limit_order(conn, symbol, side, source, account_id):
        logger.info(f"[限价开仓] 跳过 {symbol} {side} source={source}: 已有挂单")
        return None

    long_off = get_paper_limit_long_offset()
    short_off = get_paper_limit_short_offset()

    # 用最新市价重算限价，确保做多限价低于市价、做空限价高于市价
    market_ref = float(ref_price)
    try:
        from app.utils.futures_price import get_futures_trade_price
        fresh = get_futures_trade_price(
            conn, symbol, max_age_seconds=30, log_tag="paper_limit_entry", require_fresh=True,
        )
        if fresh and fresh > 0:
            market_ref = float(fresh)
        else:
            logger.warning(
                f"[限价开仓] {symbol} {side} 无新鲜市价，跳过限价单 source={source}"
            )
            return None
    except Exception as e:
        logger.warning(f"[限价开仓] {symbol} 刷新市价失败，跳过限价单: {e}")
        return None

    caller_ref = float(ref_price)
    if caller_ref > 0 and abs(market_ref - caller_ref) / caller_ref > 0.15:
        logger.warning(
            f"[限价开仓] {symbol} 调用方参考价={caller_ref} 与市价={market_ref} "
            f"偏离>15%，以市价为准"
        )

    limit_price = calc_paper_limit_price(side, market_ref)
    if side == "LONG" and limit_price >= market_ref:
        limit_price = float(Decimal(str(market_ref)) * (Decimal("1") - long_off))
    elif side == "SHORT" and limit_price <= market_ref:
        limit_price = float(Decimal(str(market_ref)) * (Decimal("1") + short_off))
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
        "ref_price": market_ref,
        "min_fill_age_sec": PAPER_LIMIT_MIN_FILL_AGE_SEC,
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
            f"(市价 {market_ref:.6g}, 多−{get_paper_limit_long_offset_pct():g}%/空+{get_paper_limit_short_offset_pct():g}%, "
            f"有效期 {timeout_minutes}min, 最早成交 {PAPER_LIMIT_MIN_FILL_AGE_SEC}s 后) "
            f"SL={sl_price} TP={tp_price} qty={quantity} source={source} order_db_id={db_id}"
        )
        return db_id
    except Exception as e:
        logger.error(f"[限价开仓] 创建失败 {symbol} {side}: {e}")
        return None


def _open_paper_market_position(
    conn,
    *,
    symbol: str,
    side: str,
    ref_price: float,
    source: str,
    leverage: int,
    margin: float,
    quantity: Optional[float],
    stop_loss_pct: Optional[float],
    take_profit_pct: Optional[float],
    stop_loss_price: Optional[float],
    take_profit_price: Optional[float],
    entry_signal_type: str,
    entry_reason: str,
    entry_score: Optional[float],
    max_hold_minutes: Optional[int],
    planned_close_time: Optional[datetime],
    signal_id: Optional[int],
    strategy_id: Optional[int],
    account_id: int,
) -> Optional[int]:
    """限价开关关闭时，模拟盘改市价立即开仓。"""
    side = side.upper()
    market_ref = float(ref_price)
    try:
        from app.utils.futures_price import get_futures_trade_price
        fresh = get_futures_trade_price(
            conn, symbol, max_age_seconds=30, log_tag="paper_market_entry", require_fresh=False,
        )
        if fresh and fresh > 0:
            market_ref = float(fresh)
    except Exception as e:
        logger.debug(f"[市价开仓] {symbol} 刷新市价失败，用参考价: {e}")

    if quantity is None or quantity <= 0:
        notional = margin * leverage
        quantity = round(notional / market_ref, 6) if market_ref > 0 else 0
    if quantity <= 0:
        logger.error(f"[市价开仓] {symbol} {side} 数量非正")
        return None

    try:
        from app.utils.config_loader import get_db_config
        from app.trading.futures_trading_engine import FuturesTradingEngine

        engine = FuturesTradingEngine(get_db_config())
        result = engine.open_position(
            account_id=account_id,
            symbol=symbol,
            position_side=side,
            quantity=Decimal(str(quantity)),
            leverage=leverage,
            limit_price=None,
            stop_loss_pct=Decimal(str(stop_loss_pct)) if stop_loss_pct is not None else None,
            take_profit_pct=Decimal(str(take_profit_pct)) if take_profit_pct is not None else None,
            stop_loss_price=Decimal(str(stop_loss_price)) if stop_loss_price is not None else None,
            take_profit_price=Decimal(str(take_profit_price)) if take_profit_price is not None else None,
            source=source,
            signal_id=signal_id,
            strategy_id=strategy_id,
            entry_signal_type=entry_signal_type or source,
            entry_reason=entry_reason,
            entry_score=entry_score,
            max_hold_minutes=max_hold_minutes,
            planned_close_time=planned_close_time,
        )
        if not result.get("success"):
            logger.warning(
                f"[市价开仓] {symbol} {side} source={source} 失败: {result.get('message')}"
            )
            return None
        pos_id = result.get("position_id")
        logger.info(
            f"[市价开仓] {symbol} {side} @ {market_ref:.6g} "
            f"qty={quantity} source={source} position_id={pos_id} (限价开关已关)"
        )
        return int(pos_id) if pos_id else None
    except Exception as e:
        logger.error(f"[市价开仓] {symbol} {side} 异常: {e}")
        return None


def parse_order_notes(notes: Optional[str]) -> Dict[str, Any]:
    if not notes:
        return {}
    try:
        data = json.loads(notes)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
