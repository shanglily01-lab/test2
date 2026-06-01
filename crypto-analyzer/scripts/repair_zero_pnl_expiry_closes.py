#!/usr/bin/env python3
"""
Repair CLOSE trades where expiry close used entry/mark_price (realized_pnl=0, entry=close).

Recalculates close price from kline_data at close_time (multi symbol key).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql
from decimal import Decimal
from dotenv import dotenv_values

from app.utils.futures_symbol import futures_symbol_kline_keys

FEE_RATE = Decimal("0.0004")


def _db():
    c = dotenv_values(".env")
    return {
        "host": c.get("DB_HOST", "localhost"),
        "port": int(c.get("DB_PORT", 3306)),
        "user": c.get("DB_USER", "root"),
        "password": c.get("DB_PASSWORD", ""),
        "database": c.get("DB_NAME", "binance-data"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


def _kline_at_close(cur, symbol: str, close_time) -> Decimal | None:
    for sk in futures_symbol_kline_keys(symbol):
        for tf in ("1m", "5m"):
            cur.execute(
                "SELECT close_price FROM kline_data "
                "WHERE symbol=%s AND timeframe=%s "
                "AND open_time <= UNIX_TIMESTAMP(%s)*1000 "
                "ORDER BY open_time DESC LIMIT 1",
                (sk, tf, close_time),
            )
            row = cur.fetchone()
            if row and row.get("close_price"):
                return Decimal(str(row["close_price"]))
    return None


def _metrics(pos: dict, close_price: Decimal) -> dict:
    entry = Decimal(str(pos["entry_price"]))
    qty = Decimal(str(pos["quantity"]))
    margin = Decimal(str(pos["margin"] or 0))
    side = pos["position_side"]
    cv = close_price * qty
    if side == "LONG":
        gross = (close_price - entry) * qty
    else:
        gross = (entry - close_price) * qty
    fee = cv * FEE_RATE
    realized = gross - fee
    open_val = entry * qty
    pnl_pct = (gross / open_val * Decimal("100")) if open_val > 0 else Decimal("0")
    roi = (gross / margin * Decimal("100")) if margin > 0 else Decimal("0")
    return {
        "close_price": close_price,
        "realized_pnl": realized.quantize(Decimal("0.01")),
        "pnl_pct": pnl_pct.quantize(Decimal("0.0001")),
        "roi": roi.quantize(Decimal("0.0001")),
        "fee": fee,
        "gross": gross,
    }


def repair(apply: bool, position_ids: list[int] | None) -> int:
    conn = pymysql.connect(**_db())
    cur = conn.cursor()

    id_filter = ""
    params: list = []
    if position_ids:
        placeholders = ",".join(["%s"] * len(position_ids))
        id_filter = f" AND t.position_id IN ({placeholders})"
        params.extend(position_ids)

    cur.execute(
        f"""
        SELECT t.position_id, t.order_id, t.trade_id, t.symbol, t.side,
               p.position_side, p.entry_price, p.quantity, p.margin, p.leverage,
               p.close_time, p.account_id
        FROM futures_trades t
        JOIN futures_positions p ON p.id = t.position_id
        JOIN futures_orders o ON o.order_id = t.order_id
        WHERE t.side IN ('CLOSE_LONG', 'CLOSE_SHORT')
          AND t.realized_pnl = 0
          AND t.entry_price = t.price
          AND (o.notes LIKE %s OR o.notes LIKE %s)
          {id_filter}
        ORDER BY t.trade_time DESC
        """,
        ("%计划平仓%", "%超时强制平仓%", *params),
    )
    rows = cur.fetchall()
    print(f"candidates: {len(rows)}")

    fixed = 0
    skipped = 0
    for row in rows:
        pid = row["position_id"]
        close_time = row["close_time"]
        k_price = _kline_at_close(cur, row["symbol"], close_time)
        if not k_price:
            print(f"  SKIP id={pid} {row['symbol']} no kline at {close_time}")
            skipped += 1
            continue
        entry = Decimal(str(row["entry_price"]))
        if abs(k_price - entry) / entry <= Decimal("1e-8"):
            print(f"  SKIP id={pid} {row['symbol']} kline==entry {k_price}")
            skipped += 1
            continue

        m = _metrics(row, k_price)
        print(
            f"  {'APPLY' if apply else 'dry'} id={pid} {row['symbol']} {row['position_side']} "
            f"close {entry} -> {k_price} pnl={m['realized_pnl']:+.2f}"
        )
        if not apply:
            fixed += 1
            continue

        cur.execute(
            """
            UPDATE futures_trades
            SET price=%s, close_price=%s, realized_pnl=%s, pnl_pct=%s, roi=%s, fee=%s,
                notional_value=%s
            WHERE trade_id=%s
            """,
            (
                float(m["close_price"]),
                float(m["close_price"]),
                float(m["realized_pnl"]),
                float(m["pnl_pct"]),
                float(m["roi"]),
                float(m["fee"]),
                float(m["close_price"] * Decimal(str(row["quantity"]))),
                row["trade_id"],
            ),
        )
        cur.execute(
            """
            UPDATE futures_orders
            SET price=%s, avg_fill_price=%s, realized_pnl=%s, pnl_pct=%s, fee=%s,
                executed_value=%s, total_value=%s
            WHERE order_id=%s
            """,
            (
                float(m["close_price"]),
                float(m["close_price"]),
                float(m["realized_pnl"]),
                float(m["pnl_pct"]),
                float(m["fee"]),
                float(m["close_price"] * Decimal(str(row["quantity"]))),
                float(m["close_price"] * Decimal(str(row["quantity"]))),
                row["order_id"],
            ),
        )
        cur.execute(
            """
            UPDATE futures_positions
            SET mark_price=%s, realized_pnl=%s
            WHERE id=%s
            """,
            (float(m["close_price"]), float(m["gross"]), pid),
        )
        conn.commit()
        fixed += 1

    cur.close()
    conn.close()
    print(f"done: fixed={fixed}, skipped={skipped}")
    return 0


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--ids", type=str, default="", help="comma position ids, e.g. 28800,28810")
    args = p.parse_args()
    ids = [int(x) for x in args.ids.split(",") if x.strip()] or None
    sys.exit(repair(apply=args.apply, position_ids=ids))


if __name__ == "__main__":
    main()
