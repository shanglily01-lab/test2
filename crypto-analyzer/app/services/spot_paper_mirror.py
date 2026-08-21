"""REQ-SPOT：模拟现货镜像 — BRAIN A1 / DeepSeek LONG，仅 L0，实盘默认关。

权威: docs/REQUIREMENTS_LOGIC_ZH.md §7.4
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional, Tuple

from loguru import logger

from app.services.paper_limit_entry import PAPER_ACCOUNT_ID, is_paper_futures_account
from app.utils.config_loader import get_db_config
from app.utils.position_time import utc_now_naive

SPOT_ACCOUNT_ID = 3
SPOT_QUOTE_USD = 500.0
SPOT_LIVE_QUOTE_USD = 500.0  # 日后开实现货时每笔也是 500U
SPOT_MAX_POSITIONS = 5
SPOT_MIRROR_PLAYBOOKS = frozenset({"A1"})
DEEPSEEK_LONG_SOURCES = frozenset({"deepseek_explore", "deepseek_predict"})
SPOT_SOURCE_BRAIN = "spot_brain"
SPOT_SOURCE_DS_EXPLORE = "spot_deepseek_explore"
SPOT_SOURCE_DS_PREDICT = "spot_deepseek_predict"
SPOT_SOURCES = frozenset({
    SPOT_SOURCE_BRAIN,
    SPOT_SOURCE_DS_EXPLORE,
    SPOT_SOURCE_DS_PREDICT,
})


def _normalize(source: str) -> str:
    return (source or "").strip().lower()


def spot_source_for(futures_source: str) -> str:
    from app.services.brain_config import is_brain_source

    src = _normalize(futures_source)
    if is_brain_source(src):
        return SPOT_SOURCE_BRAIN
    if src == "deepseek_explore":
        return SPOT_SOURCE_DS_EXPLORE
    if src == "deepseek_predict":
        return SPOT_SOURCE_DS_PREDICT
    return "spot_mirror"


def is_spot_mirror_eligible(
    *,
    source: str,
    side: str,
    playbook: Optional[str] = None,
) -> Tuple[bool, str]:
    """纯逻辑：现货是否跟这笔合约成交。不查库。"""
    side_u = (side or "").strip().upper()
    if side_u != "LONG":
        return False, "spot_long_only"
    src = _normalize(source)
    from app.services.brain_config import is_brain_source

    if is_brain_source(src):
        pb = (playbook or "").strip().upper()
        if pb in SPOT_MIRROR_PLAYBOOKS:
            return True, ""
        return False, f"brain_playbook_not_spot:{pb or 'none'}"
    if src in DEEPSEEK_LONG_SOURCES:
        return True, ""
    return False, f"source_not_spot:{src or 'none'}"


def is_spot_trading_enabled(cursor=None) -> bool:
    """现货模拟镜像 kill switch；缺省关闭。"""
    from app.services.trading_gates import is_spot_trading_enabled as _enabled

    return _enabled(cursor)


def is_spot_live_enabled(cursor=None) -> bool:
    """现货实盘同步；缺省关闭。打开不得回填历史仓。"""
    from app.services.trading_gates import is_spot_live_enabled as _enabled

    return _enabled(cursor)


def spot_quote_usd(*, live: bool = False) -> float:
    """单笔现货金额（USDT）。模拟与实盘均为 500U。"""
    return float(SPOT_LIVE_QUOTE_USD if live else SPOT_QUOTE_USD)


def _playbook_of(entry_signal_type: Optional[str], entry_reason: Optional[str]) -> Optional[str]:
    from app.services.strategy_display_names import extract_playbook_code

    return extract_playbook_code(entry_signal_type, entry_reason)


def _get_conn():
    import pymysql
    import pymysql.cursors

    return pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _is_l0_symbol(symbol: str, cursor=None) -> Tuple[bool, str]:
    from app.services.trading_gates import (
        check_symbol_trading_forbidden,
        get_symbol_rating_info,
    )

    rating_level, _, rating_locked = get_symbol_rating_info(symbol, cursor)
    forbidden, reason = check_symbol_trading_forbidden(
        symbol, cursor, rating_level=rating_level, rating_locked=rating_locked,
    )
    if forbidden:
        return False, reason or "spot_forbidden"
    if rating_level == 0:
        return True, ""
    return False, f"spot_requires_L0:level={rating_level}"


def maybe_mirror_spot_from_paper_fill(
    *,
    account_id: int,
    symbol: str,
    side: str,
    source: str,
    entry_price: float,
    entry_signal_type: Optional[str] = None,
    entry_reason: Optional[str] = None,
    stop_loss_price: Optional[float] = None,
    take_profit_price: Optional[float] = None,
    planned_close_time: Any = None,
    futures_position_id: Optional[int] = None,
) -> Optional[int]:
    """合约模拟成交后尝试开一笔现货模拟仓。失败不影响合约仓。"""
    if not is_paper_futures_account(account_id):
        return None
    pb = _playbook_of(entry_signal_type, entry_reason)
    ok, why = is_spot_mirror_eligible(source=source, side=side, playbook=pb)
    if not ok:
        logger.debug(f"[现货镜像] 跳过 {symbol} {side} source={source}: {why}")
        return None
    if not is_spot_trading_enabled():
        logger.debug(f"[现货镜像] 开关关闭，跳过 {symbol}")
        return None
    price = float(entry_price or 0)
    if price <= 0:
        return None

    conn = None
    try:
        conn = _get_conn()
        cur = conn.cursor()
        l0_ok, l0_reason = _is_l0_symbol(symbol, cur)
        if not l0_ok:
            logger.info(f"[现货镜像] 拒绝 {symbol}: {l0_reason}")
            return None

        cur.execute(
            "SELECT COUNT(*) AS cnt FROM spot_positions "
            "WHERE status='open' AND account_id=%s",
            (SPOT_ACCOUNT_ID,),
        )
        row = cur.fetchone() or {}
        if int(row.get("cnt") or 0) >= SPOT_MAX_POSITIONS:
            logger.info(f"[现货镜像] 持仓已满 {SPOT_MAX_POSITIONS}，跳过 {symbol}")
            return None

        cur.execute(
            "SELECT id FROM spot_positions "
            "WHERE symbol=%s AND status='open' AND account_id=%s LIMIT 1",
            (symbol, SPOT_ACCOUNT_ID),
        )
        if cur.fetchone():
            logger.info(f"[现货镜像] 已有同币仓，跳过 {symbol}")
            return None

        quote = spot_quote_usd(live=False)
        qty = round(quote / price, 8)
        if qty <= 0:
            return None
        sl = float(stop_loss_price) if stop_loss_price else round(price * 0.97, 8)
        tp = float(take_profit_price) if take_profit_price else round(price * 1.05, 8)
        sl_pct = abs(price - sl) / price * 100.0 if price else 3.0
        tp_pct = abs(tp - price) / price * 100.0 if price else 5.0
        close_at = planned_close_time
        if close_at is None:
            close_at = utc_now_naive() + timedelta(hours=24)
        spot_src = spot_source_for(source)
        reason = (
            f"mirror {source} {pb or 'LONG'} futures_pid={futures_position_id or '-'} "
            f"| {(entry_reason or '')[:120]}"
        )[:200]

        cur.execute(
            """
            INSERT INTO spot_positions
                (account_id, symbol, direction, quantity, notional_value, cost_basis,
                 entry_price, mark_price,
                 take_profit_price, stop_loss_price, take_profit_pct, stop_loss_pct,
                 status, source, entry_reason, open_time, planned_close_time,
                 unrealized_pnl, realized_pnl)
            VALUES (%s,%s,'long',%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,
                    'open',%s,%s,%s,%s,0,0)
            """,
            (
                SPOT_ACCOUNT_ID, symbol, qty, round(quote, 2), round(quote, 2),
                price, price, tp, sl, tp_pct, sl_pct,
                spot_src, reason, utc_now_naive(), close_at,
            ),
        )
        spot_id = cur.lastrowid
        logger.info(
            f"[现货镜像] 开仓 {symbol} id={spot_id} src={spot_src} "
            f"@ {price:.6g} {quote:.0f}U futures_pid={futures_position_id}"
        )
        if spot_id:
            try:
                from app.services.spot_live_sync import sync_spot_live_buy
                sync_spot_live_buy(
                    spot_position_id=int(spot_id),
                    symbol=symbol,
                    source=spot_src,
                )
            except Exception as live_ex:
                logger.warning(f"[现货镜像] 实盘同步失败 {symbol}: {live_ex}")
        return int(spot_id) if spot_id else None
    except Exception as e:
        logger.warning(f"[现货镜像] 写入失败 {symbol}: {e}")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
