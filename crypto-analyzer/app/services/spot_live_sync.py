"""现货实盘同步 — 仅模拟现货开/平仓瞬间；打开开关不回填历史仓。

权威: docs/REQUIREMENTS_LOGIC_ZH.md §7.4
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from loguru import logger

from app.utils.config_loader import get_db_config

SPOT_LIVE_CLIENT_PREFIX = "spot_live_"


def live_client_order_id(spot_position_id: int) -> str:
    return f"{SPOT_LIVE_CLIENT_PREFIX}{int(spot_position_id)}"[:36]


def _active_spot_key() -> Optional[dict]:
    from app.services.api_key_service import APIKeyService

    keys = APIKeyService(get_db_config()).get_all_active_api_keys("binance")
    return keys[0] if keys else None


def _engine():
    from app.trading.binance_spot_engine import BinanceSpotEngine

    ak = _active_spot_key()
    if not ak:
        return None, None
    return BinanceSpotEngine(
        get_db_config(),
        api_key=ak["api_key"],
        api_secret=ak["api_secret"],
    ), ak


def sync_spot_live_buy(
    *,
    spot_position_id: int,
    symbol: str,
    source: str,
) -> bool:
    """模拟现货刚开仓的瞬间：开关开则市价买 500U。失败不影响模拟仓。"""
    from app.services.spot_paper_mirror import spot_quote_usd
    from app.services.trading_gates import is_spot_live_enabled

    if not is_spot_live_enabled():
        logger.info(f"[现货实盘] 开关关闭，跳过买入 {symbol} paper={spot_position_id}")
        return False
    quote = spot_quote_usd(live=True)
    if quote < 5:
        logger.warning(f"[现货实盘] 金额过小 {quote}U，跳过 {symbol}")
        return False
    try:
        engine, ak = _engine()
        if engine is None:
            logger.warning(f"[现货实盘] 无活跃 API key，跳过买入 {symbol}")
            return False
        result = engine.create_market_buy_order(
            account_id=ak["id"],
            symbol=symbol,
            quote_quantity=quote,
            source=source,
            client_order_id=live_client_order_id(spot_position_id),
        )
        if result.get("success"):
            logger.info(
                f"[现货实盘] 买入 {ak.get('account_name')} {symbol} {quote:.0f}U "
                f"paper={spot_position_id}"
            )
            return True
        logger.error(f"[现货实盘] 买入失败 {symbol}: {result.get('error')}")
        return False
    except Exception as e:
        logger.error(f"[现货实盘] 买入异常 {symbol}: {e}")
        return False


def sync_spot_live_sell(*, spot_position_id: int, symbol: str, quantity: float) -> bool:
    """模拟现货刚平仓的瞬间：有映射且开关开则市价卖。不按 symbol 模糊平手工仓。"""
    from app.services.trading_gates import is_spot_live_enabled

    if not is_spot_live_enabled():
        logger.info(f"[现货实盘] 开关关闭，跳过卖出 {symbol} paper={spot_position_id}")
        return False
    cid = live_client_order_id(spot_position_id)
    try:
        engine, ak = _engine()
        if engine is None:
            logger.warning(f"[现货实盘] 无活跃 API key，跳过卖出 {symbol}")
            return False
        live_qty = engine.take_open_live_qty(cid) or quantity
        if live_qty is None or float(live_qty) <= 0:
            logger.info(f"[现货实盘] 无映射实盘仓，跳过卖出 {symbol} paper={spot_position_id}")
            return False
        result = engine.create_market_sell_order(
            account_id=ak["id"],
            symbol=symbol,
            quantity=Decimal(str(live_qty)),
            source="spot_live",
            client_order_id=f"{cid}_s"[:36],
        )
        if result.get("success"):
            engine.mark_live_closed(cid)
            logger.info(
                f"[现货实盘] 卖出 {ak.get('account_name')} {symbol} qty={live_qty} "
                f"paper={spot_position_id}"
            )
            return True
        logger.error(f"[现货实盘] 卖出失败 {symbol}: {result.get('error')}")
        return False
    except Exception as e:
        logger.error(f"[现货实盘] 卖出异常 {symbol}: {e}")
        return False
