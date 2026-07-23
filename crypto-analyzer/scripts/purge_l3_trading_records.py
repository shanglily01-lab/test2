#!/usr/bin/env python3
"""删除黑名单3级 (rating_level>=3) 交易对的历史成交数据。

覆盖表:
  - futures_trades
  - futures_orders
  - futures_positions
  - live_futures_positions / live_futures_orders（若存在，按 symbol 或 paper_position_id）

默认 dry-run；加 --execute 才真正删除。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import sql_rating_symbol_clean


def _count(cur, sql: str, params=None) -> int:
    cur.execute(sql, params or ())
    row = cur.fetchone()
    return int((row or {}).get("cnt") or 0)


def _table_exists(cur, name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (name,))
    return cur.fetchone() is not None


def _column_exists(cur, table: str, column: str) -> bool:
    cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
    return cur.fetchone() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="清理 L3 交易对的 orders/positions/trades")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行删除（默认仅预览计数）",
    )
    parser.add_argument(
        "--include-locked",
        action="store_true",
        help="同时删除 rating_locked=1（非 L3）的交易记录",
    )
    args = parser.parse_args()
    execute = args.execute

    if args.include_locked:
        rating_where = "rating_level >= 3 OR COALESCE(rating_locked, 0) = 1"
        scope = "L3 + rating_locked"
    else:
        rating_where = "rating_level >= 3"
        scope = "L3 only (rating_level>=3)"

    clean_sym = sql_rating_symbol_clean("symbol")
    clean_p = sql_rating_symbol_clean("p.symbol")
    clean_o = sql_rating_symbol_clean("o.symbol")
    clean_t = sql_rating_symbol_clean("t.symbol")
    banned_subq = (
        f"SELECT {sql_rating_symbol_clean('symbol')} FROM trading_symbol_rating "
        f"WHERE {rating_where}"
    )

    cfg = get_db_config()
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    # 大批量删除：关闭 autocommit，统一事务
    conn.autocommit(False)

    try:
        with conn.cursor() as cur:
            print("=" * 64)
            print("L3 交易记录清理（futures_orders / positions / trades）")
            print(f"模式: {'EXECUTE' if execute else 'DRY-RUN'}")
            print(f"范围: {scope}")
            print("=" * 64)

            n_banned = _count(
                cur,
                f"SELECT COUNT(*) AS cnt FROM trading_symbol_rating WHERE {rating_where}",
            )
            print(f"\n禁止交易对数量: {n_banned}")

            n_pos = _count(
                cur,
                f"SELECT COUNT(*) AS cnt FROM futures_positions p "
                f"WHERE {clean_p} IN ({banned_subq})",
            )
            n_pos_open = _count(
                cur,
                f"SELECT COUNT(*) AS cnt FROM futures_positions p "
                f"WHERE LOWER(COALESCE(p.status,''))='open' "
                f"AND {clean_p} IN ({banned_subq})",
            )
            n_ord = _count(
                cur,
                f"SELECT COUNT(*) AS cnt FROM futures_orders o "
                f"WHERE {clean_o} IN ({banned_subq})",
            )
            n_trd = _count(
                cur,
                f"SELECT COUNT(*) AS cnt FROM futures_trades t "
                f"WHERE {clean_t} IN ({banned_subq})",
            )

            print(f"futures_positions: {n_pos} (OPEN={n_pos_open})")
            print(f"futures_orders:    {n_ord}")
            print(f"futures_trades:    {n_trd}")

            # 关联 live 表
            live_pos = live_ord = 0
            has_live_pos = _table_exists(cur, "live_futures_positions")
            has_live_ord = _table_exists(cur, "live_futures_orders")
            if has_live_pos:
                live_pos = _count(
                    cur,
                    f"SELECT COUNT(*) AS cnt FROM live_futures_positions p "
                    f"WHERE {clean_p} IN ({banned_subq})",
                )
                print(f"live_futures_positions: {live_pos}")
            if has_live_ord:
                live_ord = _count(
                    cur,
                    f"SELECT COUNT(*) AS cnt FROM live_futures_orders o "
                    f"WHERE {clean_o} IN ({banned_subq})",
                )
                print(f"live_futures_orders: {live_ord}")

            cur.execute(
                f"""
                SELECT p.account_id, COUNT(*) AS cnt
                FROM futures_positions p
                WHERE {clean_p} IN ({banned_subq})
                GROUP BY p.account_id
                ORDER BY cnt DESC
                """
            )
            print("\npositions by account_id:")
            for row in cur.fetchall():
                print(f"  account_id={row['account_id']}: {row['cnt']}")

            if not execute:
                print("\nDRY-RUN 完成。确认后加 --execute 执行删除。")
                return 0

            print("\n开始删除（先 trades → orders → positions → live）...")

            # 1) trades by symbol
            cur.execute(
                f"DELETE t FROM futures_trades t WHERE {clean_t} IN ({banned_subq})"
            )
            del_trd = cur.rowcount
            print(f"  DELETE futures_trades: {del_trd}")

            # 2) orders by symbol
            cur.execute(
                f"DELETE o FROM futures_orders o WHERE {clean_o} IN ({banned_subq})"
            )
            del_ord = cur.rowcount
            print(f"  DELETE futures_orders: {del_ord}")

            # 3) live tables before positions (may FK via paper_position_id)
            if has_live_ord:
                cur.execute(
                    f"DELETE o FROM live_futures_orders o WHERE {clean_o} IN ({banned_subq})"
                )
                print(f"  DELETE live_futures_orders: {cur.rowcount}")
            if has_live_pos:
                # also clear live rows linked to paper positions being deleted
                if _column_exists(cur, "live_futures_positions", "paper_position_id"):
                    cur.execute(
                        f"""
                        DELETE lp FROM live_futures_positions lp
                        WHERE {sql_rating_symbol_clean('lp.symbol')} IN ({banned_subq})
                           OR lp.paper_position_id IN (
                                SELECT id FROM (
                                    SELECT p.id FROM futures_positions p
                                    WHERE {clean_p} IN ({banned_subq})
                                ) x
                           )
                        """
                    )
                else:
                    cur.execute(
                        f"DELETE p FROM live_futures_positions p "
                        f"WHERE {clean_p} IN ({banned_subq})"
                    )
                print(f"  DELETE live_futures_positions: {cur.rowcount}")

            # 4) positions
            cur.execute(
                f"DELETE p FROM futures_positions p WHERE {clean_p} IN ({banned_subq})"
            )
            del_pos = cur.rowcount
            print(f"  DELETE futures_positions: {del_pos}")

            # orphan trades/orders by position_id left? already deleted by symbol.
            # Clean any remaining orphans referencing deleted position ids is N/A after symbol delete.

            conn.commit()
            print("\nCOMMIT 完成。")
            print(
                f"汇总: trades={del_trd}, orders={del_ord}, positions={del_pos}"
            )
            return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
