#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only replay of PaperSync pre-send checks for BSB (or --symbol).

Run on the server, from the project root:

    cd /home/ec2-user/crypto-analyzer
    python3 scripts/test_bsb_live_sync.py
    python3 scripts/test_bsb_live_sync.py --symbol DOGE
    python3 scripts/test_bsb_live_sync.py --order-id 115040

Does NOT place, cancel, or retry any Binance order.
Does NOT change live_sync_status.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql
from app.services.paper_limit_sync_service import decide_live_sync_at_paper_fill
from app.services.trading_gates import (
    check_live_open_allowed,
    get_live_margin_ratio,
    is_live_trading_enabled,
)
from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_clean


def _ok(name: str, ok: bool, detail: str) -> None:
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {name}: {detail}")


def _http_json(url: str, timeout: float = 5.0) -> tuple[int, dict | str]:
    req = Request(url, headers={"User-Agent": "test_bsb_live_sync"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw[:300]
    except Exception as e:
        return 0, str(e)


def _binance_ticker(raw: str) -> tuple[float | None, str]:
    t0 = time.time()
    code, body = _http_json(
        f"https://fapi.binance.com/fapi/v1/ticker/price?symbol={raw}",
        timeout=8,
    )
    ms = int((time.time() - t0) * 1000)
    if isinstance(body, dict) and body.get("price"):
        return float(body["price"]), f"http={code} {ms}ms px={body['price']}"
    return None, f"http={code} {ms}ms body={body!r}"[:240]


def _binance_filters(raw: str) -> dict:
    code, body = _http_json(
        "https://fapi.binance.com/fapi/v1/exchangeInfo",
        timeout=20,
    )
    if not isinstance(body, dict):
        return {"error": f"exchangeInfo http={code} {body}"}
    for item in body.get("symbols") or []:
        if item.get("symbol") == raw:
            filters = {f["filterType"]: f for f in item.get("filters") or []}
            return {
                "status": item.get("status"),
                "qtyPrec": item.get("quantityPrecision"),
                "LOT_SIZE": filters.get("LOT_SIZE"),
                "MARKET_LOT_SIZE": filters.get("MARKET_LOT_SIZE"),
                "MIN_NOTIONAL": filters.get("MIN_NOTIONAL"),
            }
    return {"error": f"{raw} not in exchangeInfo"}


def _round_qty(qty: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        step = Decimal("0.001")
    return (qty / step).quantize(Decimal("1"), rounding=ROUND_DOWN) * step


def _load_order(cur, symbol: str, order_id: int | None) -> dict | None:
    if order_id:
        cur.execute(
            """
            SELECT fo.id, fo.order_id, fo.symbol, fo.side, fo.order_source,
                   fo.avg_fill_price, fo.quantity, fo.leverage,
                   fo.stop_loss_price, fo.take_profit_price, fo.fill_time,
                   fo.live_sync_status, fo.position_id, fta.user_id
            FROM futures_orders fo
            JOIN futures_trading_accounts fta ON fta.id = fo.account_id
            WHERE fo.id=%s
            """,
            (order_id,),
        )
        return cur.fetchone()
    clean = futures_symbol_clean(symbol)
    cur.execute(
        """
        SELECT fo.id, fo.order_id, fo.symbol, fo.side, fo.order_source,
               fo.avg_fill_price, fo.quantity, fo.leverage,
               fo.stop_loss_price, fo.take_profit_price, fo.fill_time,
               fo.live_sync_status, fo.position_id, fta.user_id
        FROM futures_orders fo
        JOIN futures_trading_accounts fta ON fta.id = fo.account_id
        WHERE fo.account_id=2
          AND fo.side IN ('OPEN_LONG','OPEN_SHORT')
          AND REPLACE(REPLACE(fo.symbol,'/',''),'USDT','') = REPLACE(%s,'USDT','')
        ORDER BY fo.id DESC
        LIMIT 1
        """,
        (clean,),
    )
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        """
        SELECT fo.id, fo.order_id, fo.symbol, fo.side, fo.order_source,
               fo.avg_fill_price, fo.quantity, fo.leverage,
               fo.stop_loss_price, fo.take_profit_price, fo.fill_time,
               fo.live_sync_status, fo.position_id, fta.user_id
        FROM futures_orders fo
        JOIN futures_trading_accounts fta ON fta.id = fo.account_id
        WHERE fo.account_id=2
          AND fo.side IN ('OPEN_LONG','OPEN_SHORT')
          AND fo.symbol LIKE %s
        ORDER BY fo.id DESC
        LIMIT 1
        """,
        (f"%{symbol}%",),
    )
    return cur.fetchone()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only BSB live-sync test")
    parser.add_argument("--symbol", default="BSB/USDT")
    parser.add_argument("--order-id", type=int, default=0)
    parser.add_argument("--api-base", default="http://127.0.0.1:9020")
    args = parser.parse_args()
    symbol_arg = args.symbol if "/" in args.symbol else f"{args.symbol}/USDT"

    print("=== test_bsb_live_sync (read-only, no order) ===")
    print(f"api_base={args.api_base}")

    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    cur = conn.cursor()
    order = _load_order(cur, symbol_arg, args.order_id or None)
    if not order:
        print("no paper open order found")
        conn.close()
        return 1

    symbol = order["symbol"]
    source = order["order_source"]
    raw = futures_symbol_clean(symbol)
    side = "LONG" if "LONG" in str(order["side"]) else "SHORT"
    paper_fill = float(order["avg_fill_price"] or 0)
    paper_sl = float(order["stop_loss_price"] or 0)
    paper_tp = float(order["take_profit_price"] or 0)
    print(
        f"order id={order['id']} {order['order_id']} {symbol} {order['side']} "
        f"src={source} fill={paper_fill} sl={paper_sl} tp={paper_tp} "
        f"live_sync={order['live_sync_status']} fill_time={order['fill_time']}"
    )

    live_on = is_live_trading_enabled()
    _ok("live_trading_enabled", live_on, str(int(live_on)))

    allowed, why = check_live_open_allowed(symbol, source, cur)
    _ok("check_live_open_allowed", allowed, why or "allowed")

    dec, dec_why = decide_live_sync_at_paper_fill(symbol, source, cur)
    _ok(
        "decide_live_sync_at_paper_fill",
        dec is None,
        f"status={dec!r} reason={dec_why or 'NULL=queue sync'}",
    )

    ratio = get_live_margin_ratio(symbol, cur)
    _ok("L0 margin_ratio", ratio >= 1.0, str(ratio))

    cur.execute(
        """
        SELECT user_id, status, max_position_value, max_leverage
        FROM user_api_keys WHERE user_id=%s AND status='active' LIMIT 1
        """,
        (order["user_id"],),
    )
    key = cur.fetchone()
    _ok("api_key", bool(key), str(key))
    base_margin = float((key or {}).get("max_position_value") or 0)
    leverage = int((key or {}).get("max_leverage") or 5)
    margin = base_margin * float(ratio)
    _ok("live_margin", margin >= 5, f"{margin:.2f}U x{leverage}")

    sl_pct = tp_pct = None
    if paper_fill > 0 and paper_sl > 0:
        raw_sl = (
            (paper_fill - paper_sl) / paper_fill * 100
            if side == "LONG"
            else (paper_sl - paper_fill) / paper_fill * 100
        )
        sl_pct = raw_sl if raw_sl > 0 else None
    if paper_fill > 0 and paper_tp > 0:
        raw_tp = (
            (paper_tp - paper_fill) / paper_fill * 100
            if side == "LONG"
            else (paper_fill - paper_tp) / paper_fill * 100
        )
        tp_pct = raw_tp if raw_tp > 0 else None
    _ok("sl/tp pct", sl_pct is not None and tp_pct is not None, f"sl={sl_pct} tp={tp_pct}")

    print("\n--- price paths (this is the pre-send kill) ---")
    t0 = time.time()
    local_url = f"{args.api_base.rstrip('/')}/api/futures/price/{quote(symbol, safe='')}"
    code, body = _http_json(local_url, timeout=5)
    local_ms = int((time.time() - t0) * 1000)
    local_px = None
    if isinstance(body, dict) and body.get("price"):
        local_px = float(body["price"])
    _ok(
        "localhost /api/futures/price",
        local_px is not None and local_px > 0,
        f"{local_url} http={code} {local_ms}ms px={local_px} body={body if local_px is None else body.get('source')}",
    )

    bn_px, bn_detail = _binance_ticker(raw)
    _ok("binance ticker (engine.get_current_price)", bn_px is not None, bn_detail)

    old_path_ok = (local_px and local_px > 0) or (bn_px and bn_px > 0)
    _ok(
        "OLD PaperSync would get a price",
        bool(old_path_ok),
        "if FAIL here, old code marks FAILED and never sends",
    )
    _ok(
        "NEW PaperSync can use paper fill",
        paper_fill > 0,
        f"paper_fill={paper_fill} (used only if live ticker missing)",
    )

    price = local_px or bn_px or paper_fill
    qty = Decimal(str(round(margin * leverage / price, 6))) if price > 0 else Decimal("0")
    print(f"\n--- size: margin={margin:.2f} lev={leverage} px={price} raw_qty={qty} ---")
    info = _binance_filters(raw)
    print("filters", info)
    lot = info.get("LOT_SIZE") or {}
    mkt = info.get("MARKET_LOT_SIZE") or {}
    step = Decimal(str(lot.get("stepSize") or "1"))
    min_qty = Decimal(str(lot.get("minQty") or "1"))
    mkt_max = Decimal(str(mkt.get("maxQty") or "0"))
    min_n = Decimal(str((info.get("MIN_NOTIONAL") or {}).get("notional") or "5"))
    rounded = _round_qty(qty, step)
    if mkt_max > 0 and rounded > mkt_max:
        rounded = _round_qty(mkt_max, step)
    notional = rounded * Decimal(str(price))
    _ok("qty >= min", rounded >= min_qty, f"{rounded} min={min_qty} step={step}")
    _ok(
        "qty <= market max",
        mkt_max <= 0 or rounded <= mkt_max,
        f"{rounded} max={mkt_max}",
    )
    _ok("notional >= min", notional >= min_n, f"{notional:.4f} min={min_n}")

    would_send = (
        live_on
        and allowed
        and dec is None
        and margin >= 5
        and sl_pct is not None
        and tp_pct is not None
        and price > 0
        and rounded >= min_qty
        and notional >= min_n
        and (mkt_max <= 0 or rounded <= mkt_max)
    )
    print("\n=== verdict ===")
    if would_send:
        print("WOULD_SEND  按当前检查会走到「发送开仓订单」。本脚本不下单。")
        if not old_path_ok:
            print("NOTE  旧代码会在取价失败处 FAILED；新代码会用模拟成交价继续。")
    else:
        print("WOULD_NOT_SEND  发单前就会停。上面第一个 FAIL 就是原因。")
    print("deployed code still needs: systemctl restart crypto-app-main")
    conn.close()
    return 0 if would_send else 2


if __name__ == "__main__":
    raise SystemExit(main())
