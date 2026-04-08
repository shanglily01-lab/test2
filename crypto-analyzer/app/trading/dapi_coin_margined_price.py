"""
币本位 dapi：全市场 GET /dapi/v1/ticker/price（无 symbol）与单交易对请求等价数据源。
部分环境下单 symbol 请求异常时，与服务器上 curl 无参列表一致，可作回退。

Binance 返回的 symbol/ps 均为无斜杠格式，例如：
{"symbol":"TRXUSD_PERP","ps":"TRXUSD","price":"0.31609",...}
内部展示用 TRX/USD 时，需与 TRXUSD_PERP / ps=TRXUSD 对齐。
"""
import re
import time
from decimal import Decimal
from typing import Any, List, Optional

_CACHE_TS: float = 0.0
_CACHE_ROWS: Optional[List[Any]] = None
TTL_SEC = 2.0


def get_all_dapi_ticker_prices() -> Optional[List[Any]]:
    global _CACHE_TS, _CACHE_ROWS
    now = time.time()
    if _CACHE_ROWS is not None and (now - _CACHE_TS) < TTL_SEC:
        return _CACHE_ROWS
    import requests

    try:
        r = requests.get("https://dapi.binance.com/dapi/v1/ticker/price", timeout=4)
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list):
            return None
        _CACHE_TS = now
        _CACHE_ROWS = data
        return data
    except Exception:
        return None


def to_dapi_perp_symbol(symbol: str) -> Optional[str]:
    """
    将内部或前端多种写法统一为 dapi 永续符号（无斜杠），如 TRXUSD_PERP。
    支持：TRX/USD、TRXUSD、TRXUSD_PERP、trx-usd
    """
    if not symbol or not isinstance(symbol, str):
        return None
    s = symbol.strip().upper().replace("-", "/")
    if s.endswith("/USD"):
        base = s.split("/")[0].strip()
        if base and re.match(r"^[A-Z0-9]+$", base):
            return f"{base}USD_PERP"
    if s.endswith("USD_PERP"):
        return s
    # TRXUSD（无斜杠、无 _PERP），且排除 U 本位 USDT
    if s.endswith("USD") and not s.endswith("USDT") and len(s) > 3:
        base = s[:-3]
        if base and re.match(r"^[A-Z0-9]+$", base):
            return f"{base}USD_PERP"
    return None


def canonical_coin_usd_display(symbol: str) -> Optional[str]:
    """统一为 BASE/USD，便于与 dapi 映射表、库内 symbol 对齐。"""
    api = to_dapi_perp_symbol(symbol)
    if not api or not api.endswith("USD_PERP"):
        return None
    base = api[:-8]
    return f"{base}/USD" if base else None


def fapi_usdt_perp_symbol_for_coin_usd(symbol: str) -> Optional[str]:
    """
    内部展示为币本位 BASE/USD 时，对应 U 本位永续在 fapi 上的符号（如 APT/USD → APTUSDT）。

    Binance 币本位 dapi 仅部分币种有 USD_PERP；未挂牌时批量行情需用同基底 USDT 永续作参考价，
    与 coin_futures_trading_engine 中 dapi 失败后的现货回退一致（U 本位与现货同量级）。
    """
    canon = canonical_coin_usd_display(symbol)
    if not canon:
        return None
    base = canon.split("/")[0].strip()
    if not base or not re.match(r"^[A-Z0-9]+$", base):
        return None
    return f"{base}USDT"


def spot_usdt_pair_slash_for_coin_usd(symbol: str) -> Optional[str]:
    """K 线库中常见写法 BASE/USDT，供币本位无行情时回退查询。"""
    canon = canonical_coin_usd_display(symbol)
    if not canon:
        return None
    base = canon.split("/")[0].strip()
    if not base:
        return None
    return f"{base}/USDT"


def _row_price(item: dict) -> Optional[Decimal]:
    p = item.get("price")
    if p is None:
        return None
    try:
        dec = Decimal(str(p))
        if dec > 0:
            return dec
    except Exception:
        pass
    return None


def find_perp_price(rows: Optional[List[Any]], api_sym: str) -> Optional[Decimal]:
    """
    在全量 ticker 列表中匹配永续价格。
    优先 symbol == api_sym；否则用无斜杠的 ps（如 TRXUSD）对齐 TRXUSD_PERP。
    """
    if not rows or not api_sym:
        return None
    # 1) 精确匹配 symbol（Binance 返回无斜杠，如 TRXUSD_PERP）
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("symbol") != api_sym:
            continue
        dec = _row_price(item)
        if dec is not None:
            return dec

    # 2) 用 ps 匹配：api_sym 为 TRXUSD_PERP -> ps 为 TRXUSD
    ps_want = api_sym.replace("_PERP", "") if api_sym.endswith("_PERP") else None
    if not ps_want:
        return None
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("ps") != ps_want:
            continue
        sym = str(item.get("symbol") or "")
        # 优先永续；同 ps 下可能有交割合约，取 _PERP
        if sym.endswith("USD_PERP"):
            dec = _row_price(item)
            if dec is not None:
                return dec
    for item in rows:
        if not isinstance(item, dict):
            continue
        if item.get("ps") != ps_want:
            continue
        dec = _row_price(item)
        if dec is not None:
            return dec
    return None
