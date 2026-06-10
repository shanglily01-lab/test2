#!/usr/bin/env python3
"""取消按过期 K 线挂出的模拟盘限价开仓单（只读检查 / --apply 执行取消）。"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql
from app.services.paper_limit_entry import parse_order_notes
from app.utils.config_loader import get_db_config
from app.utils.futures_price import get_futures_trade_price

REF_DEVIATION = 0.15
LONG_LIMIT_ABOVE_MARKET = Decimal("1.03")
SHORT_LIMIT_BELOW_MARKET = Decimal("0.97")


def main() -> int:
    parser = argparse.ArgumentParser(description="取消陈旧参考价的 PENDING 限价单")
    parser.add_argument("--apply", action="store_true", help="写入 EXPIRED（默认仅预览）")
    args = parser.parse_args()

    cfg = get_db_config()
    conn = pymysql.connect(**cfg, cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    cancelled = 0
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT order_id, symbol, side, price, notes, created_at
                FROM futures_orders
                WHERE status='PENDING' AND order_type='LIMIT'
                  AND side IN ('OPEN_LONG', 'OPEN_SHORT')
                ORDER BY created_at ASC
                """
            )
            orders = cur.fetchall()

        for o in orders:
            sym = o["symbol"]
            side = o["side"]
            limit_px = Decimal(str(o["price"]))
            meta = parse_order_notes(o.get("notes"))
            ref_px = meta.get("ref_price")
            live = get_futures_trade_price(conn, sym, log_tag="stale_limit_cleanup")
            if not live or live <= 0:
                print(f"SKIP {o['order_id']} {sym} {side}: 无新鲜市价")
                continue
            live_d = Decimal(str(live))
            reason = None
            if ref_px and float(ref_px) > 0:
                ref_dev = abs(float(live_d) - float(ref_px)) / float(ref_px)
                if ref_dev > REF_DEVIATION:
                    reason = f"stale_price_feed ref={ref_px} market={live_d} dev={ref_dev:.1%}"
            if reason is None and side == "OPEN_LONG" and limit_px > live_d * LONG_LIMIT_ABOVE_MARKET:
                reason = f"stale_limit_above_market limit={limit_px} market={live_d}"
            if reason is None and side == "OPEN_SHORT" and limit_px < live_d * SHORT_LIMIT_BELOW_MARKET:
                reason = f"stale_limit_below_market limit={limit_px} market={live_d}"

            if not reason:
                continue

            print(f"{'CANCEL' if args.apply else 'WOULD_CANCEL'} {o['order_id']} {sym} {side} "
                  f"limit={limit_px} ref={ref_px} live={live_d} created={o['created_at']}")
            print(f"  reason: {reason}")
            if args.apply:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE futures_orders
                        SET status='EXPIRED', cancellation_reason=%s,
                            canceled_at=NOW(), updated_at=NOW()
                        WHERE order_id=%s AND status='PENDING'
                        """,
                        (reason, o["order_id"]),
                    )
                    if cur.rowcount:
                        cancelled += 1
        if args.apply:
            conn.commit()
            print(f"\n已取消 {cancelled} 笔")
        else:
            print("\n预览模式，加 --apply 执行取消")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
