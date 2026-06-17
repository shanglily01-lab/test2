#!/usr/bin/env python3
"""服务器验证：Binance 实盘 API 中文 symbol 签名 (-1022) 与 SL/TP 无 reduceOnly (-1106).

不下真实单，仅调用 /fapi/v1/order/test 与 set_leverage / set_margin_type（与 PaperSync 路径一致）。

用法:
  cd crypto-analyzer
  python scripts/validate_binance_live_sign.py
  python scripts/validate_binance_live_sign.py --symbol 龙虾/USDT
  python scripts/validate_binance_live_sign.py --symbol 币安人生/USDT --leverage 5
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

from app.services.api_key_service import APIKeyService
from app.trading.binance_futures_engine import BinanceFuturesEngine
from app.utils.config_loader import get_db_config

ASCII_CONTROL = "BTC/USDT"
DEFAULT_UNICODE = "龙虾/USDT"


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"OK: {msg}")


def _warn(msg: str) -> None:
    print(f"WARN: {msg}")


def _load_active_api_key() -> Tuple[str, str]:
    """与 PaperSync / UserTradingEngineManager 一致：经 APIKeyService 解密读取。"""
    db_config = get_db_config()
    env = dotenv_values(str(ROOT / ".env"))
    enc = (env.get("API_KEY_ENCRYPTION_KEY") or env.get("JWT_SECRET_KEY") or "").strip()
    svc = APIKeyService(db_config, encryption_key=enc or None)
    keys = svc.get_all_active_api_keys("binance")
    if not keys:
        _fail("无可用 Binance API Key (user_api_keys.status=active)")
    row = keys[0]
    api_key = (row.get("api_key") or "").strip()
    api_secret = (row.get("api_secret") or "").strip()
    if len(api_key) < 10 or len(api_secret) < 10:
        _fail(
            "API Key 解密后无效（长度过短）。"
            "请确认 .env 中 JWT_SECRET_KEY / API_KEY_ENCRYPTION_KEY 与 Web 服务一致"
        )
    return api_key, api_secret


def _api_error(result: Any) -> str | None:
    if not isinstance(result, dict):
        return f"非 dict 响应: {result!r}"
    if result.get("success") is False:
        code = result.get("code", "?")
        return f"[{code}] {result.get('error') or result.get('msg')}"
    code = result.get("code")
    if isinstance(code, int) and code < 0:
        return f"[{code}] {result.get('msg', result)}"
    return None


def _legacy_signature(api_secret: str, params: dict) -> str:
    """修复前签名：未 URL 编码（中文 symbol 会 -1022）。"""
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return hmac.new(
        api_secret.encode("utf-8"),
        query_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _check_signature_encoding(api_secret: str, symbol: str) -> None:
    binance_symbol = symbol.replace("/", "").upper()
    if binance_symbol.isascii():
        _warn(f"{symbol} 为 ASCII，跳过 legacy/new 签名差异检查")
        return
    sample = {
        "symbol": binance_symbol,
        "leverage": 5,
        "timestamp": 1700000000000,
        "recvWindow": 5000,
    }
    legacy = _legacy_signature(api_secret, sample)
    modern = hmac.new(
        api_secret.encode("utf-8"),
        urlencode(list(sample.items())).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if legacy == modern:
        _fail(f"{symbol} legacy/modern 签名相同，无法验证 URL 编码修复")
    encoded = urlencode(list(sample.items()))
    if binance_symbol in encoded:
        _fail("modern 签名字符串仍含未编码中文")
    _ok(f"签名编码: legacy≠modern，modern 使用 URL 编码 ({encoded[:60]}...)")


def _order_test_market(engine: BinanceFuturesEngine, symbol: str, leverage: int) -> None:
    binance_symbol = engine._convert_symbol(symbol)
    px = engine.get_current_price(symbol)
    if not px or px <= 0:
        _fail(f"{symbol} 无法获取市价")
    raw_qty = Decimal("1500") / Decimal(str(px))
    qty = engine._round_quantity(raw_qty, symbol)
    info = engine._symbol_info_cache.get(binance_symbol, {})
    min_qty = info.get("min_qty", Decimal("0.001"))
    if qty <= 0 or qty < min_qty:
        _fail(f"{symbol} 计算测试数量无效 raw={raw_qty} rounded={qty} min_qty={min_qty}")

    lev = engine.set_leverage(symbol, leverage)
    if lev.get("success") is False:
        _fail(f"{symbol} set_leverage: {lev.get('error')} [{lev.get('code')}]")
    margin = engine.set_margin_type(symbol, "ISOLATED")
    if margin.get("success") is False and margin.get("code") != -4046:
        _fail(f"{symbol} set_margin_type: {margin.get('error')} [{margin.get('code')}]")

    params = {
        "symbol": binance_symbol,
        "side": "BUY",
        "positionSide": "LONG",
        "type": "MARKET",
        "quantity": str(qty),
    }
    result = engine._request("POST", "/fapi/v1/order/test", params)
    err = _api_error(result)
    if err:
        _fail(f"{symbol} order/test MARKET: {err}")
    _ok(f"{symbol} order/test MARKET qty={qty} (≈1500U notional)")


def _order_test_stop(engine: BinanceFuturesEngine, symbol: str) -> None:
    """与 _place_stop_loss 相同参数（无 reduceOnly）。"""
    binance_symbol = engine._convert_symbol(symbol)
    px = float(engine.get_current_price(symbol))
    if px <= 0:
        _fail(f"{symbol} 无法获取市价 (SL test)")
    qty = engine._round_quantity(Decimal("1"), symbol)
    if qty <= 0:
        qty = Decimal("1")
    stop_price = engine._round_price(Decimal(str(px * 0.97)), symbol)

    params = {
        "symbol": binance_symbol,
        "side": "SELL",
        "positionSide": "LONG",
        "type": "STOP_MARKET",
        "stopPrice": str(stop_price),
        "quantity": str(qty),
        "workingType": "MARK_PRICE",
        "timeInForce": "GTE_GTC",
    }
    if "reduceOnly" in params or "reduceonly" in params:
        _fail("SL 参数仍含 reduceOnly")

    result = engine._request("POST", "/fapi/v1/order/test", params)
    err = _api_error(result)
    if err:
        if "-1106" in err and "reduceonly" in err.lower():
            _fail(f"{symbol} SL order/test 仍触发 -1106 reduceOnly: {err}")
        _fail(f"{symbol} SL order/test: {err}")
    _ok(f"{symbol} SL order/test stop={stop_price} (无 reduceOnly)")


def _order_test_take_profit(engine: BinanceFuturesEngine, symbol: str) -> None:
    binance_symbol = engine._convert_symbol(symbol)
    px = float(engine.get_current_price(symbol))
    qty = engine._round_quantity(Decimal("1"), symbol)
    if qty <= 0:
        qty = Decimal("1")
    tp_price = engine._round_price(Decimal(str(px * 1.05)), symbol)

    params = {
        "symbol": binance_symbol,
        "side": "SELL",
        "positionSide": "LONG",
        "type": "TAKE_PROFIT_MARKET",
        "stopPrice": str(tp_price),
        "quantity": str(qty),
        "workingType": "MARK_PRICE",
        "timeInForce": "GTE_GTC",
    }
    result = engine._request("POST", "/fapi/v1/order/test", params)
    err = _api_error(result)
    if err:
        if "-1106" in err and "reduceonly" in err.lower():
            _fail(f"{symbol} TP order/test 仍触发 -1106 reduceOnly: {err}")
        _fail(f"{symbol} TP order/test: {err}")
    _ok(f"{symbol} TP order/test stop={tp_price} (无 reduceOnly)")


def _inspect_engine_sl_tp_helpers(engine: BinanceFuturesEngine, symbol: str) -> None:
    """确认引擎 helper 构建的参数不含 reduceOnly。"""
    import inspect

    src = inspect.getsource(engine._place_stop_loss)
    if "reduceOnly" in src or "reduceonly" in src:
        _fail("_place_stop_loss 源码仍含 reduceOnly")
    src_tp = inspect.getsource(engine._place_take_profit)
    if "reduceOnly" in src_tp or "reduceonly" in src_tp:
        _fail("_place_take_profit 源码仍含 reduceOnly")
    _ok("_place_stop_loss / _place_take_profit 源码无 reduceOnly")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Binance live sign + SL/TP params")
    parser.add_argument("--symbol", default=DEFAULT_UNICODE, help="中文/Unicode 测试对")
    parser.add_argument("--leverage", type=int, default=5, help="set_leverage 测试杠杆")
    parser.add_argument("--skip-ascii", action="store_true", help="跳过 BTC 对照组")
    args = parser.parse_args()

    targets: List[str] = []
    if not args.skip_ascii:
        targets.append(ASCII_CONTROL)
    targets.append(args.symbol)

    print("=== validate_binance_live_sign ===")
    api_key, api_secret = _load_active_api_key()
    _ok("已加载 active Binance API Key")

    db_config = get_db_config()
    engine = BinanceFuturesEngine(db_config, api_key=api_key, api_secret=api_secret)

    _check_signature_encoding(api_secret, args.symbol)
    _inspect_engine_sl_tp_helpers(engine, args.symbol)

    for sym in targets:
        print(f"--- {sym} ---")
        _order_test_market(engine, sym, args.leverage)
        _order_test_stop(engine, sym)
        _order_test_take_profit(engine, sym)

    print("ALL PASSED")


if __name__ == "__main__":
    main()
