"""模拟开仓前顾问闸门 — Gemini / DeepSeek 按 source 路由，其他策略双审."""
from __future__ import annotations

import threading
import time
from typing import Optional, Tuple

from loguru import logger

from app.services.gemini_position_advisor import GEMINI_PER_CALL_DELAY_S
from app.services.deepseek_position_advisor import DEEPSEEK_PER_CALL_DELAY_S
from app.services.open_advisor_routing import resolve_open_advisors
from app.services.securities_filter import is_security
from app.utils.futures_symbol import futures_symbol_clean, sql_rating_symbol_clean

_open_gate_lock = threading.Lock()
_open_gate_waiting = 0

_PROVIDER_DELAY = {
    "gemini": GEMINI_PER_CALL_DELAY_S,
    "deepseek": DEEPSEEK_PER_CALL_DELAY_S,
}

RECENT_STOP_LOSS_COOLDOWN_HOURS = 4
RECENT_LOSS_COOLDOWN_HOURS = 24
RECENT_LOSS_TRADE_LIMIT = 2
RECENT_LOSS_PNL_LIMIT = -80.0


def _check_recent_loss_cooldown(
    symbol: str,
    conn=None,
    account_id: int = 2,
) -> Tuple[bool, str]:
    """
    短周期亏损冷却:
    - 近4小时同币有止损，禁止新开仓；
    - 近24小时同币亏损>=2笔或净亏<=-80U，禁止新开仓。
    """
    clean = futures_symbol_clean(symbol)
    if not clean:
        return True, ""

    own_conn = conn is None
    if own_conn:
        import pymysql
        import pymysql.cursors
        from app.utils.config_loader import get_db_config
        conn = pymysql.connect(
            **get_db_config(),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    cur = None
    try:
        cur = conn.cursor()
        clean_expr = sql_rating_symbol_clean("symbol")
        cur.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM futures_positions
            WHERE account_id=%s
              AND status='closed'
              AND close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
              AND {clean_expr} = %s
              AND (
                notes = '止损'
                OR notes LIKE '%%止损%%'
                OR notes LIKE '%%code:SL%%'
              )
            """,
            (account_id, RECENT_STOP_LOSS_COOLDOWN_HOURS, clean),
        )
        row = cur.fetchone() or {}
        stop_n = int((row.get("n") if isinstance(row, dict) else row[0]) or 0)
        if stop_n > 0:
            return False, f"近{RECENT_STOP_LOSS_COOLDOWN_HOURS}小时同币已止损{stop_n}笔，冷却禁止开仓"

        cur.execute(
            f"""
            SELECT
              COUNT(*) AS total_n,
              SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS loss_n,
              COALESCE(SUM(realized_pnl), 0) AS net_pnl
            FROM futures_positions
            WHERE account_id=%s
              AND status='closed'
              AND close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
              AND realized_pnl IS NOT NULL
              AND {clean_expr} = %s
            """,
            (account_id, RECENT_LOSS_COOLDOWN_HOURS, clean),
        )
        row = cur.fetchone() or {}
        loss_n = int((row.get("loss_n") if isinstance(row, dict) else row[1]) or 0)
        net_pnl = float((row.get("net_pnl") if isinstance(row, dict) else row[2]) or 0)
        if loss_n >= RECENT_LOSS_TRADE_LIMIT:
            return False, f"近{RECENT_LOSS_COOLDOWN_HOURS}小时同币亏损{loss_n}笔，冷却禁止开仓"
        if net_pnl <= RECENT_LOSS_PNL_LIMIT:
            return False, f"近{RECENT_LOSS_COOLDOWN_HOURS}小时同币净亏{net_pnl:.2f}U，冷却禁止开仓"
        return True, ""
    finally:
        if cur is not None:
            try:
                cur.close()
            except Exception:
                pass
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


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
    conn=None,
) -> Tuple[bool, str]:
    """
    开仓前审核。返回 (允许开仓, 原因).
    gemini_explore/gemini_predict 由 Gemini 审核，其余 source 由 DeepSeek 审核。
    顾问关闭时放行；顾问/API 异常时保守拒绝，避免故障静默开仓。
    """
    global _open_gate_waiting
    if is_security(symbol):
        reason = f"non_crypto_symbol_blocked:{symbol}"
        logger.info(
            f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {reason}"
        )
        return False, reason

    try:
        from app.services.trading_gates import check_simulated_symbol_allowed
        allowed, reason = check_simulated_symbol_allowed(symbol, conn)
        if not allowed:
            logger.info(
                f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {reason}"
            )
            return False, reason
    except Exception as e:
        logger.warning(f"[开仓闸门] {symbol} 基础币种闸门异常，拒绝开仓: {e}")
        return False, "symbol_gate_error"

    try:
        allowed, reason = _check_recent_loss_cooldown(symbol, conn)
        if not allowed:
            logger.info(
                f"[开仓闸门] 拒绝开仓 {symbol} {side} source={source}: {reason}"
            )
            return False, reason
    except Exception as e:
        logger.warning(f"[开仓闸门] {symbol} 短周期亏损冷却检查异常，拒绝开仓: {e}")
        return False, "recent_loss_cooldown_error"

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
                if provider == "gemini":
                    from app.services.gemini_position_advisor import get_open_advisor
                    allowed, reason = get_open_advisor().review_open(
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
                elif provider == "deepseek":
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
