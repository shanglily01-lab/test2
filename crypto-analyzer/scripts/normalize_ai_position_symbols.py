#!/usr/bin/env python3
"""将 AI 模块 futures_positions / verdicts 的 symbol 统一为 BASE/USDT.

用法:
  python scripts/normalize_ai_position_symbols.py
  python scripts/normalize_ai_position_symbols.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_rating_canonical, sql_rating_symbol_clean

_CLEAN = sql_rating_symbol_clean("symbol")


def _ai_source_sql(column: str = "source") -> str:
    return (
        f"({column} LIKE 'gemini_%' OR {column} LIKE 'deepseek_%' OR {column} LIKE 'gpt_%')"
    )


def _normalize_table(conn, table: str, dry_run: bool) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'symbol'")
        if not cur.fetchone():
            return 0
        cur.execute(
            f"SELECT id, symbol FROM `{table}` WHERE symbol NOT LIKE '%/%'"
        )
        rows = cur.fetchall()
    changed = 0
    for row in rows:
        old = row["symbol"]
        new = futures_symbol_rating_canonical(old)
        if not new or new == old:
            continue
        changed += 1
        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(f"UPDATE `{table}` SET symbol=%s WHERE id=%s", (new, row["id"]))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化 AI 持仓/verdict symbol")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        pos_changed = 0
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id, symbol, source FROM futures_positions "
                f"WHERE symbol NOT LIKE '%/%' AND {_ai_source_sql('source')}"
            )
            pos_rows = cur.fetchall()
        for row in pos_rows:
            new = futures_symbol_rating_canonical(row["symbol"])
            if not new or new == row["symbol"]:
                continue
            pos_changed += 1
            print(f"  futures_positions #{row['id']} {row['source']}: {row['symbol']} -> {new}")
            if not dry_run:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE futures_positions SET symbol=%s WHERE id=%s",
                        (new, row["id"]),
                    )

        verdict_changed = 0
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [
                list(r.values())[0] if isinstance(r, dict) else r[0]
                for r in cur.fetchall()
            ]
        for table in tables:
            if not (
                table.endswith("_explore_verdicts")
                or table.endswith("_predict_verdicts")
            ):
                continue
            n = _normalize_table(conn, table, dry_run)
            if n:
                print(f"  {table}: {n} rows")
                verdict_changed += n

        print(
            f"\n{'预览' if dry_run else '已更新'}: "
            f"futures_positions {pos_changed} 行, verdicts {verdict_changed} 行"
        )
        if dry_run:
            print("加 --apply 写入。")
        else:
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
