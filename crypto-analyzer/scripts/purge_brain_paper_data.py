#!/usr/bin/env python3
"""清理超级大脑（BRAIN）模拟盘历史：持仓/订单/成交 + 机会/扫描表。

默认 dry-run；加 --execute 才真正写库。
默认不关 brain_swing_enabled（加 --disable-strategy 才关）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.services.brain_config import BRAIN_ACCOUNT_ID, BRAIN_ENABLED_KEY, BRAIN_SOURCE
from app.utils.config_loader import get_db_config


def _count(cur, sql: str, params: tuple = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int((row or {}).get("cnt") or 0)


def _table_exists(cur, name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (name,))
    return cur.fetchone() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 BRAIN 模拟仓与机会历史")
    parser.add_argument("--execute", action="store_true", help="实际执行（默认仅预览）")
    parser.add_argument(
        "--account-id",
        type=int,
        default=BRAIN_ACCOUNT_ID,
        help=f"模拟账户 ID（默认 {BRAIN_ACCOUNT_ID}，权益刷新用）",
    )
    parser.add_argument(
        "--disable-strategy",
        action="store_true",
        help=f"同时将 {BRAIN_ENABLED_KEY} 设为 0",
    )
    args = parser.parse_args()
    execute = args.execute
    account_id = args.account_id

    cfg = get_db_config()
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)

    try:
        with conn.cursor() as cur:
            print("=" * 60)
            print("超级大脑 BRAIN 数据清理（持仓 + 订单 + 成交 + 机会表）")
            print(f"模式: {'EXECUTE' if execute else 'DRY-RUN'}")
            print(f"match: source/order_source LIKE 'brain_%' (含 {BRAIN_SOURCE})")
            print(f"equity refresh account_id={account_id}")
            print("=" * 60)

            if args.disable_strategy:
                cur.execute(
                    "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
                    (BRAIN_ENABLED_KEY,),
                )
                row = cur.fetchone()
                old = str((row or {}).get("setting_value", "(missing)")).strip()
                print(f"\n[1] Kill switch {BRAIN_ENABLED_KEY}: {old} -> 0")
                if execute:
                    cur.execute(
                        """
                        INSERT INTO system_settings (setting_key, setting_value)
                        VALUES (%s, '0')
                        ON DUPLICATE KEY UPDATE setting_value='0'
                        """,
                        (BRAIN_ENABLED_KEY,),
                    )
            else:
                print(f"\n[1] Kill switch: 保持不变（不加 --disable-strategy）")

            # 用 REGEXP，避免 PyMySQL 把 LIKE 里的 %/_ 当格式化占位符
            cur.execute(
                """
                SELECT id, symbol, status, source, account_id
                FROM futures_positions
                WHERE LOWER(source) REGEXP '^brain_'
                ORDER BY id
                """
            )
            positions = cur.fetchall() or []
            pos_ids = [int(r["id"]) for r in positions]
            open_cnt = sum(1 for r in positions if (r.get("status") or "").lower() == "open")
            closed_cnt = len(pos_ids) - open_cnt
            print(f"\n[2] futures_positions 命中 {len(pos_ids)} 条 (OPEN={open_cnt} CLOSED={closed_cnt})")
            for r in positions[:15]:
                print(f"  #{r['id']} {r['symbol']} {r['status']} {r.get('source')}")
            if len(positions) > 15:
                print(f"  ... 另有 {len(positions) - 15} 条")

            orders_cnt = _count(
                cur,
                """
                SELECT COUNT(*) AS cnt FROM futures_orders
                WHERE LOWER(order_source) REGEXP '^brain_'
                """,
            )
            trades_by_pos = 0
            if pos_ids:
                ph = ", ".join(["%s"] * len(pos_ids))
                trades_by_pos = _count(
                    cur,
                    f"SELECT COUNT(*) AS cnt FROM futures_trades WHERE position_id IN ({ph})",
                    tuple(pos_ids),
                )
            cur.execute(
                """
                SELECT COUNT(DISTINCT t.id) AS cnt
                FROM futures_trades t
                INNER JOIN futures_orders o
                  ON o.order_id = t.order_id AND o.account_id = t.account_id
                WHERE LOWER(o.order_source) REGEXP '^brain_'
                """
            )
            trades_by_order = int((cur.fetchone() or {}).get("cnt") or 0)

            opp_cnt = 0
            rounds_cnt = 0
            if _table_exists(cur, "brain_opportunities"):
                opp_cnt = _count(cur, "SELECT COUNT(*) AS cnt FROM brain_opportunities")
            if _table_exists(cur, "brain_scan_rounds"):
                rounds_cnt = _count(cur, "SELECT COUNT(*) AS cnt FROM brain_scan_rounds")

            print("\n[3] 待删除计数")
            print(f"  futures_trades (by position_id): {trades_by_pos}")
            print(f"  futures_trades (by brain order_source): {trades_by_order}")
            print(f"  futures_orders: {orders_cnt}")
            print(f"  futures_positions: {len(pos_ids)}")
            print(f"  brain_opportunities: {opp_cnt}")
            print(f"  brain_scan_rounds: {rounds_cnt}")

            if pos_ids:
                ph = ", ".join(["%s"] * len(pos_ids))
                if _table_exists(cur, "live_futures_positions"):
                    cur.execute(
                        f"""
                        SELECT id, symbol, status, source, paper_position_id
                        FROM live_futures_positions
                        WHERE paper_position_id IN ({ph})
                        """,
                        tuple(pos_ids),
                    )
                    live_rows = cur.fetchall() or []
                    if live_rows:
                        print(
                            f"\n[警告] live_futures_positions 仍有 {len(live_rows)} 条关联"
                            "（本脚本不删实盘表）"
                        )
                        for r in live_rows[:10]:
                            print(
                                f"  live#{r['id']} {r['symbol']} {r['status']} "
                                f"paper={r['paper_position_id']}"
                            )

            if not execute:
                print("\nDRY-RUN 完成。确认后加 --execute 执行删除。")
                return 0

            deleted_trades = 0
            if pos_ids:
                ph = ", ".join(["%s"] * len(pos_ids))
                cur.execute(
                    f"DELETE FROM futures_trades WHERE position_id IN ({ph})",
                    tuple(pos_ids),
                )
                deleted_trades += cur.rowcount
            cur.execute(
                """
                DELETE t FROM futures_trades t
                INNER JOIN futures_orders o
                  ON o.order_id = t.order_id AND o.account_id = t.account_id
                WHERE LOWER(o.order_source) REGEXP '^brain_'
                """
            )
            deleted_trades += cur.rowcount

            cur.execute(
                """
                DELETE FROM futures_orders
                WHERE LOWER(order_source) REGEXP '^brain_'
                """
            )
            deleted_orders = cur.rowcount

            cur.execute(
                """
                DELETE FROM futures_positions
                WHERE LOWER(source) REGEXP '^brain_'
                """
            )
            deleted_positions = cur.rowcount

            deleted_opp = 0
            deleted_rounds = 0
            if _table_exists(cur, "brain_opportunities"):
                cur.execute("DELETE FROM brain_opportunities")
                deleted_opp = cur.rowcount
            if _table_exists(cur, "brain_scan_rounds"):
                cur.execute("DELETE FROM brain_scan_rounds")
                deleted_rounds = cur.rowcount

            cur.execute(
                """
                UPDATE futures_trading_accounts a
                SET a.total_equity = a.current_balance + COALESCE((
                    SELECT SUM(p.unrealized_pnl)
                    FROM futures_positions p
                    WHERE p.account_id = a.id AND p.status = 'open'
                ), 0)
                WHERE a.id = %s
                """,
                (account_id,),
            )

            if args.disable_strategy:
                try:
                    from app.services.data_cache_service import invalidate_setting_cache
                    from app.services.system_settings_loader import invalidate_loader_cache

                    invalidate_setting_cache()
                    invalidate_loader_cache()
                except Exception as e:
                    print(f"  (cache invalidate warn: {e})")

            conn.commit()

            print("\n[4] 已执行")
            print(f"  futures_trades deleted (rowcount sum): {deleted_trades}")
            print(f"  futures_orders deleted: {deleted_orders}")
            print(f"  futures_positions deleted: {deleted_positions}")
            print(f"  brain_opportunities deleted: {deleted_opp}")
            print(f"  brain_scan_rounds deleted: {deleted_rounds}")
            print(f"  account #{account_id} total_equity refreshed")
            if args.disable_strategy:
                print(f"  {BRAIN_ENABLED_KEY} -> 0")
            else:
                print("  strategy switch unchanged (仍可继续开仓)")
            return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
