#!/usr/bin/env python3
"""
Backfill futures_orders + futures_trades for closed futures_positions
that have no CLOSE_LONG / CLOSE_SHORT trade row.

Idempotent: order_id=BFUT-{position_id}, trade_id=BT-{position_id}.
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import pymysql
from dotenv import dotenv_values

FEE_RATE = Decimal("0.0004")
ACCOUNT_ID = 2

SQL_ORPHANS = """
SELECT
    p.id, p.account_id, p.symbol, p.position_side, p.leverage,
    p.quantity, p.margin, p.notional_value, p.entry_price, p.mark_price,
    p.realized_pnl, p.close_time, p.open_time, p.source, p.notes
FROM futures_positions p
LEFT JOIN futures_trades t
  ON t.position_id = p.id AND t.side IN ('CLOSE_LONG', 'CLOSE_SHORT')
WHERE p.account_id = %s
  AND LOWER(p.status) = 'closed'
  AND t.id IS NULL
ORDER BY p.close_time ASC, p.id ASC
"""


def _db_config() -> dict:
    cfg = dotenv_values(".env")
    return {
        "host": cfg.get("DB_HOST", "localhost"),
        "port": int(cfg.get("DB_PORT", 3306)),
        "user": cfg.get("DB_USER", "root"),
        "password": cfg.get("DB_PASSWORD", ""),
        "database": cfg.get("DB_NAME", "binance-data"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


def _d(v: Any, default: Decimal = Decimal("0")) -> Decimal:
    if v is None:
        return default
    return Decimal(str(v))


def _close_reason(notes: Optional[str], source: Optional[str]) -> str:
    if notes and notes.strip():
        return notes.strip()[:500]
    return source or "backfill"


def _calc_metrics(pos: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = _d(pos["entry_price"])
    qty = _d(pos["quantity"])
    margin = _d(pos["margin"])
    leverage = int(pos["leverage"] or 5)
    side = (pos["position_side"] or "").upper()
    if qty <= 0 or entry <= 0 or side not in ("LONG", "SHORT"):
        return None

    close_price = _d(pos["mark_price"])
    if close_price <= 0:
        close_price = entry

    close_value = close_price * qty
    open_value = entry * qty
    if side == "LONG":
        gross_pnl = (close_price - entry) * qty
    else:
        gross_pnl = (entry - close_price) * qty

    stored_pnl = pos.get("realized_pnl")
    if stored_pnl is not None:
        realized_pnl = _d(stored_pnl)
    else:
        fee = close_value * FEE_RATE
        realized_pnl = gross_pnl - fee

    fee = close_value * FEE_RATE
    pnl_pct = (gross_pnl / open_value * Decimal("100")) if open_value > 0 else Decimal("0")
    roi = (gross_pnl / margin * Decimal("100")) if margin > 0 else Decimal("0")

    trade_time = pos.get("close_time") or pos.get("open_time") or datetime.now()
    close_side = f"CLOSE_{side}"
    order_id = f"BFUT-{pos['id']}"
    trade_id = f"BT-{pos['id']}"
    order_source = (pos.get("source") or "backfill")[:500]
    notes = _close_reason(pos.get("notes"), pos.get("source"))

    return {
        "position_id": pos["id"],
        "account_id": pos["account_id"],
        "symbol": pos["symbol"],
        "close_side": close_side,
        "leverage": leverage,
        "close_price": close_price,
        "quantity": qty,
        "close_value": close_value,
        "margin": margin,
        "fee": fee,
        "realized_pnl": realized_pnl,
        "pnl_pct": pnl_pct,
        "roi": roi,
        "entry_price": entry,
        "trade_time": trade_time,
        "order_id": order_id,
        "trade_id": trade_id,
        "order_source": order_source,
        "notes": notes,
    }


def backfill(apply: bool, limit: Optional[int]) -> int:
    conn = pymysql.connect(**_db_config())
    cur = conn.cursor()
    cur.execute(SQL_ORPHANS, (ACCOUNT_ID,))
    rows: List[Dict[str, Any]] = cur.fetchall()
    if limit:
        rows = rows[:limit]

    print(f"orphan closed positions to backfill: {len(rows)} (account_id={ACCOUNT_ID})")
    if not rows:
        cur.close()
        conn.close()
        return 0

    inserted_orders = 0
    inserted_trades = 0
    skipped = 0
    errors = 0

    order_sql = """
        INSERT INTO futures_orders (
            account_id, order_id, position_id, symbol,
            side, order_type, leverage,
            price, quantity, executed_quantity,
            total_value, executed_value,
            fee, fee_rate, status,
            avg_fill_price, fill_time,
            realized_pnl, pnl_pct,
            order_source, notes
        ) VALUES (
            %s, %s, %s, %s,
            %s, 'MARKET', %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, 'FILLED',
            %s, %s,
            %s, %s,
            %s, %s
        )
    """

    trade_sql = """
        INSERT INTO futures_trades (
            account_id, order_id, position_id, trade_id,
            symbol, side, price, quantity, notional_value,
            leverage, margin, fee, fee_rate,
            realized_pnl, pnl_pct, roi,
            entry_price, close_price, trade_time
        ) VALUES (
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s
        )
    """

    for pos in rows:
        m = _calc_metrics(pos)
        if not m:
            errors += 1
            print(f"  SKIP invalid id={pos['id']} {pos.get('symbol')} side={pos.get('position_side')}")
            continue

        if not apply:
            print(
                f"  [dry-run] id={m['position_id']} {m['symbol']} {m['close_side']} "
                f"close={m['close_price']:.8f} pnl={float(m['realized_pnl']):+.2f} "
                f"time={m['trade_time']}"
            )
            inserted_trades += 1
            continue

        try:
            cur.execute(
                "SELECT id FROM futures_orders WHERE order_id=%s LIMIT 1",
                (m["order_id"],),
            )
            has_order = cur.fetchone() is not None
            cur.execute(
                "SELECT id FROM futures_trades WHERE trade_id=%s LIMIT 1",
                (m["trade_id"],),
            )
            has_trade = cur.fetchone() is not None

            if has_order and has_trade:
                skipped += 1
                continue

            cp = float(m["close_price"])
            qty = float(m["quantity"])
            cv = float(m["close_value"])
            fee = float(m["fee"])
            rpnl = float(m["realized_pnl"])
            pnl_pct = float(m["pnl_pct"])
            roi = float(m["roi"])
            ep = float(m["entry_price"])
            margin = float(m["margin"])

            if not has_order:
                cur.execute(
                    order_sql,
                    (
                        m["account_id"],
                        m["order_id"],
                        m["position_id"],
                        m["symbol"],
                        m["close_side"],
                        m["leverage"],
                        cp,
                        qty,
                        qty,
                        cv,
                        cv,
                        fee,
                        float(FEE_RATE),
                        cp,
                        m["trade_time"],
                        rpnl,
                        pnl_pct,
                        m["order_source"],
                        m["notes"],
                    ),
                )
                inserted_orders += 1

            if not has_trade:
                cur.execute(
                    trade_sql,
                    (
                        m["account_id"],
                        m["order_id"],
                        m["position_id"],
                        m["trade_id"],
                        m["symbol"],
                        m["close_side"],
                        cp,
                        qty,
                        cv,
                        m["leverage"],
                        margin,
                        fee,
                        float(FEE_RATE),
                        rpnl,
                        pnl_pct,
                        roi,
                        ep,
                        cp,
                        m["trade_time"],
                    ),
                )
                inserted_trades += 1

            conn.commit()
        except Exception as e:
            conn.rollback()
            errors += 1
            print(f"  ERROR id={pos['id']}: {e}")

    cur.close()
    conn.close()

    mode = "APPLIED" if apply else "DRY-RUN"
    print(
        f"\n{mode}: orders+={inserted_orders}, trades+={inserted_trades}, "
        f"skipped(existing)={skipped}, errors={errors}"
    )
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing futures_trades for closed positions")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write to DB (default: dry-run only)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max rows to process")
    args = parser.parse_args()
    code = backfill(apply=args.apply, limit=args.limit)
    sys.exit(code)


if __name__ == "__main__":
    main()
