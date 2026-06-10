#!/usr/bin/env python3
"""无 API 验证限价成交/转市价核心路径（不写入数据库）。"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.trading.futures_trading_engine import FuturesTradingEngine
from app.services.paper_limit_entry import parse_order_notes
from app.utils.config_loader import get_db_config


def fail(msg: str) -> None:
    print(f"FAIL: {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def main() -> None:
    cfg = get_db_config()
    engine = FuturesTradingEngine(cfg)

    # 1) 旧 bug：错误方法名不存在
    if hasattr(engine, "_calculate_liquidation_price"):
        fail("_calculate_liquidation_price 仍存在（旧 bug 未修）")
    if not hasattr(engine, "calculate_liquidation_price"):
        fail("calculate_liquidation_price 不存在")
    ok("强平价方法名正确")

    try:
        engine._calculate_liquidation_price(Decimal("1"), "LONG", 5)  # type: ignore[attr-defined]
        fail("错误方法名居然可调用")
    except AttributeError:
        ok("旧方法名 _calculate_liquidation_price 会 AttributeError（与线上失败一致）")

    # 2) 取一笔真实 PENDING 限价单做预检（不写库）
    conn = pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor, connect_timeout=10)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM futures_orders
            WHERE status='PENDING' AND order_type='LIMIT'
              AND side IN ('OPEN_LONG', 'OPEN_SHORT')
            ORDER BY created_at DESC LIMIT 1
            """
        )
        order = cur.fetchone()
        cur.execute(
            """
            SELECT order_id, symbol, status, avg_fill_price, position_id
            FROM futures_orders
            WHERE order_id='FUT-B864149E43FA4270'
            LIMIT 1
            """
        )
        filled_sample = cur.fetchone()
    conn.close()

    if not order:
        ok("当前无 PENDING 限价单可预检（跳过订单级预检）")
    else:
        symbol = order["symbol"]
        side_raw = order["side"]
        position_side = "LONG" if side_raw == "OPEN_LONG" else "SHORT"
        entry_price = Decimal(str(order["price"]))
        quantity = Decimal(str(order["quantity"]))
        leverage = int(order.get("leverage") or 1)
        meta = parse_order_notes(order.get("notes"))

        lp = engine.calculate_liquidation_price(entry_price, position_side, leverage)
        if lp <= 0:
            fail(f"强平价计算异常 {lp}")

        market_px = engine._resolve_market_entry_price(symbol)
        if not market_px or market_px <= 0:
            fail(f"无法解析 {symbol} 市价（转市价会失败）")

        cursor = engine._get_cursor()
        cursor.execute(
            "SELECT current_balance, frozen_balance FROM futures_trading_accounts WHERE id=%s",
            (int(order["account_id"]),),
        )
        acct = cursor.fetchone()
        if not acct:
            fail("账户不存在")

        notional = entry_price * quantity
        margin = notional / Decimal(leverage)
        fee = notional * Decimal("0.0004")
        available = Decimal(str(acct["current_balance"])) - Decimal(str(acct.get("frozen_balance") or 0))
        if available < margin + fee:
            ok(
                f"预检订单 {order['order_id']} {symbol}: 路径可达但余额不足 "
                f"(需 {float(margin + fee):.2f} 可用 {float(available):.2f})"
            )
        else:
            ok(
                f"预检订单 {order['order_id']} {symbol}: 限价/市价/强平/余额检查均可通过 "
                f"(限价={entry_price}, 市价={market_px}, 强平={lp})"
            )

    # 3) 上次修复后真实成交留痕（若存在）
    if filled_sample and filled_sample.get("status") == "FILLED" and filled_sample.get("position_id"):
        ok(
            f"历史验证单 {filled_sample['order_id']} {filled_sample['symbol']} "
            f"status=FILLED position_id={filled_sample['position_id']} "
            f"avg_fill={filled_sample.get('avg_fill_price')}"
        )
    else:
        ok("未找到 FUT-B864149E43FA4270 成交留痕（可能已清理或非本库）")

    print("\nvalidate_limit_fill_path: PASS")


if __name__ == "__main__":
    main()
