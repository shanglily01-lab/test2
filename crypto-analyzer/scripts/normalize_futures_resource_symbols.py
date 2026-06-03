#!/usr/bin/env python3
"""将合约相关资源表的 symbol 统一为 BASE/USDT（与 futures_positions 一致）.

覆盖:
  futures_trades, futures_orders
  gemini/deepseek/gpt_advisor_reviews
  optimization_logs

不处理: kline_data (采集已是 XXX/USDT)、hyperliquid/news (裸币名)

用法:
  python scripts/normalize_futures_resource_symbols.py
  python scripts/normalize_futures_resource_symbols.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_rating_canonical

TARGET_TABLES = (
    "futures_trades",
    "futures_orders",
    "gemini_advisor_reviews",
    "deepseek_advisor_reviews",
    "gpt_advisor_reviews",
    "optimization_logs",
    "trading_symbols",
    "trading_symbol_rating",
)


def _to_futures_canonical(symbol: str) -> str:
    """BASE/USDT；已是斜杠则 canonical；裸 BASE 补 /USDT。"""
    s = (symbol or "").strip()
    if not s:
        return s
    if "/" in s:
        return futures_symbol_rating_canonical(s)
    canon = futures_symbol_rating_canonical(s)
    if "/" in canon:
        return canon
    return f"{canon}/USDT"


def _normalize_table(conn, table: str, dry_run: bool) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'symbol'")
        if not cur.fetchone():
            return 0
        cur.execute(f"SELECT id, symbol FROM `{table}` WHERE symbol NOT LIKE '%/%'")
        rows = cur.fetchall()
    if table == "trading_symbol_rating":
        with conn.cursor() as cur:
            for r in rows:
                cur.execute(
                    "SELECT rating_level FROM trading_symbol_rating WHERE id=%s",
                    (r["id"],),
                )
                lvl = cur.fetchone()
                if lvl:
                    r["rating_level"] = lvl.get("rating_level")

    changed = 0
    for row in rows:
        old = (row.get("symbol") or "").strip()
        new = _to_futures_canonical(old)
        if not new or new == old:
            continue
        if table == "trading_symbols":
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM trading_symbols WHERE symbol=%s AND exchange='binance_futures' LIMIT 1",
                    (new,),
                )
                dup = cur.fetchone()
                if dup and int(dup["id"]) != int(row["id"]):
                    if not dry_run:
                        cur.execute("DELETE FROM trading_symbols WHERE id=%s", (row["id"],))
                    changed += 1
                    print(f"  trading_symbols #{row['id']} {old} -> drop (dup {new})")
                    continue
        if table == "trading_symbol_rating":
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, rating_level FROM trading_symbol_rating WHERE symbol=%s LIMIT 1",
                    (new,),
                )
                dup = cur.fetchone()
                if dup and int(dup["id"]) != int(row["id"]):
                    keep_level = max(int(dup.get("rating_level") or 0), int(row.get("rating_level") or 0))
                    if not dry_run:
                        cur.execute(
                            "UPDATE trading_symbol_rating SET rating_level=%s WHERE id=%s",
                            (keep_level, dup["id"]),
                        )
                        cur.execute("DELETE FROM trading_symbol_rating WHERE id=%s", (row["id"],))
                    changed += 1
                    print(f"  trading_symbol_rating #{row['id']} {old} -> merge into {new}")
                    continue
        changed += 1
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE `{table}` SET symbol=%s WHERE id=%s",
                    (new, row["id"]),
                )
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化合约资源表 symbol 为 BASE/USDT")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    total = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            existing = {list(r.values())[0] for r in cur.fetchall()}

        for table in TARGET_TABLES:
            if table not in existing:
                print(f"  skip {table} (missing)")
                continue
            n = _normalize_table(conn, table, dry_run)
            if n:
                print(f"  {table}: {n} rows")
                total += n

        print(
            f"\n{'预览' if dry_run else '已更新'}合计 {total} 行"
            + ("" if dry_run else "。")
        )
        if dry_run and total:
            print("加 --apply 写入。")
        elif not dry_run:
            conn.commit()
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
