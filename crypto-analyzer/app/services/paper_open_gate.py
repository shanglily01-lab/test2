"""模拟开仓前顾问闸门 — 统一 DeepSeek 开仓顾问."""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from loguru import logger

from app.services.deepseek_position_advisor import DEEPSEEK_PER_CALL_DELAY_S
from app.services.open_advisor_routing import resolve_open_advisors
from app.services.securities_filter import is_security

_open_gate_lock = threading.Lock()
_open_gate_waiting = 0

_PROVIDER_DELAY = {
    "deepseek": DEEPSEEK_PER_CALL_DELAY_S,
}


def gate_simulated_open(
    symbol: str,
    side: str,
    price: float,
    source: str,
    catalyst: str = "",
    leverage: int = 5,
    sl_pct: Optional[float] = None,
    tp_pct: Optional[float] = None,
    hold_hours: Optional[float] = None,
    account_id: int = 2,
    conn=None,
) -> Tuple[bool, str]:
    """
    开仓前审核。返回 (允许开仓, 原因).
    当前统一走 DeepSeek 开仓顾问；Gemini 相关主路由已下线，仅保留历史 source 兼容。
    顾问关闭时放行；顾问/API 异常时保守拒绝，避免故障静默开仓。
    """
    global _open_gate_waiting
    if is_security(symbol):
        reason = f"non_crypto_symbol_blocked:{symbol}"
        logger.info(
            f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {reason}"
        )
        return False, reason

    from app.services.midline_swing_config import is_midline_source
    if is_midline_source(source):
        try:
            from app.services.trading_gates import check_simulated_symbol_allowed
            allowed, reason = check_simulated_symbol_allowed(symbol, conn)
            if not allowed:
                logger.info(
                    f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {reason}"
                )
                return False, reason
        except Exception as e:
            logger.warning(f"[开仓闸门] {symbol} 基础闸门异常: {e}")
            return False, "symbol_gate_error"
        return True, "midline_skip_advisor"

    try:
        from app.services.trading_gates import (
            check_simulated_symbol_allowed,
            check_symbol_loss_cooldown,
            has_open_futures_position_same_side,
        )
        allowed, reason = check_simulated_symbol_allowed(symbol, conn)
        if not allowed:
            logger.info(
                f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {reason}"
            )
            return False, reason
        symbol_allowed, symbol_reason = check_symbol_loss_cooldown(
            symbol, conn, source=source,
        )
        if not symbol_allowed:
            logger.info(
                f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {symbol_reason}"
            )
            return False, symbol_reason
        duplicate, duplicate_reason = has_open_futures_position_same_side(
            conn, symbol, side, account_id=account_id,
        )
        if duplicate:
            logger.info(
                f"[开仓闸门] 拒绝重复开仓 {symbol} {side} source={source}: {duplicate_reason}"
            )
            return False, duplicate_reason
    except Exception as e:
        logger.warning(f"[开仓闸门] {symbol} 基础币种闸门异常，拒绝开仓: {e}")
        return False, "symbol_gate_error"

    try:
        providers = resolve_open_advisors(source)
    except Exception as e:
        logger.warning(f"[开仓顾问] {symbol} 解析顾问路由异常, 拒绝开仓: {e}")
        return False, "advisor_route_error_reject"

    with _open_gate_lock:
        _open_gate_waiting += 1
        queue_ahead = _open_gate_waiting - 1
        if queue_ahead > 0:
            logger.info(
                f"[开仓顾问] {symbol} source={source} 排队中(前方约{queue_ahead}笔), 等待审查"
            )
        try:
            last_reason = "approved"
            for provider in providers:
                if provider == "deepseek":
                    from app.services.deepseek_position_advisor import get_deepseek_advisor
                    allowed, reason = get_deepseek_advisor().review_open(
                        symbol=symbol,
                        side=side,
                        price=price,
                        source=source,
                        catalyst=catalyst,
                        leverage=leverage,
                        sl_pct=sl_pct,
                        tp_pct=tp_pct,
                        hold_hours=hold_hours,
                        conn=conn,
                    )
                else:
                    # Gemini 开仓顾问已下线；未知 provider 跳过
                    continue
                if not allowed:
                    msg = f"{provider}: {reason}"
                    logger.info(
                        f"[开仓顾问] 拒绝开仓 {symbol} {side} source={source}: {msg}"
                    )
                    return False, msg
                last_reason = reason
            return True, last_reason
        except Exception as e:
            logger.warning(f"[开仓顾问] {symbol} 审核异常, 拒绝开仓: {e}")
            return False, "advisor_error_reject"
        finally:
            _open_gate_waiting = max(0, _open_gate_waiting - 1)
            delay = max(_PROVIDER_DELAY.get(p, 0) for p in providers)
            if delay > 0:
                time.sleep(delay)
