#!/usr/bin/env python3
"""将 trading_symbol_rating 统一为 BASE/USDT 格式，合并重复 clean key.

冲突合并策略: rating_level 取 max (更严格); 数值字段取较大/较新行.
默认预览; --apply 写入.

用法:
  python scripts/normalize_trading_symbol_rating.py
  python scripts/normalize_trading_symbol_rating.py --apply
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import (
    futures_symbol_clean,
    futures_symbol_rating_canonical,
    sql_rating_symbol_clean,
)

_RATING_CLEAN = sql_rating_symbol_clean("symbol")


def _pick_row(rows: list[dict]) -> dict:
    """合并同一 clean key 的多行 -> 一条 canonical 记录."""
    best = max(rows, key=lambda r: (int(r.get("rating_level") or 0), r.get("updated_at") or r.get("id") or 0))
    canon = futures_symbol_rating_canonical(best["symbol"])
    merged = dict(best)
    merged["symbol"] = canon
    merged["rating_level"] = max(int(r.get("rating_level") or 0) for r in rows)
    for num_key in (
        "hard_stop_loss_count",
        "total_loss_amount",
        "total_profit_amount",
        "total_trades",
    ):
        merged[num_key] = max(float(r.get(num_key) or 0) for r in rows)
    wr_vals = [float(r.get("win_rate") or 0) for r in rows]
    merged["win_rate"] = max(wr_vals) if wr_vals else 0
    return merged


def fetch_groups(conn) -> dict[str, list[dict]]:
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM trading_symbol_rating ORDER BY id")
        rows = cur.fetchall()
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sym = (r.get("symbol") or "").strip()
        if not sym:
            continue
        groups[futures_symbol_clean(sym)].append(r)
    return groups


def main() -> int:
    parser = argparse.ArgumentParser(description="规范化 trading_symbol_rating symbol 格式")
    parser.add_argument("--apply", action="store_true", help="写入数据库")
    args = parser.parse_args()

    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )
    try:
        groups = fetch_groups(conn)
        dup_groups = {k: v for k, v in groups.items() if len(v) > 1}
        non_canon = []
        for clean, rows in groups.items():
            canon = futures_symbol_rating_canonical(rows[0]["symbol"])
            for r in rows:
                if r["symbol"] != canon:
                    non_canon.append(r["symbol"])

        print(f"总行数: {sum(len(v) for v in groups.values())}")
        print(f"唯一 clean key: {len(groups)}")
        print(f"重复 clean key 组: {len(dup_groups)}")
        print(f"非 canonical 写法行数: {len(non_canon)}")

        if dup_groups:
            print("\n重复示例 (最多 10 组):")
            for i, (clean, rows) in enumerate(sorted(dup_groups.items())[:10]):
                syms = ", ".join(f"{r['symbol']}(L{r['rating_level']})" for r in rows)
                merged = _pick_row(rows)
                print(f"  {clean}: {syms} -> {merged['symbol']} L{merged['rating_level']}")

        if not args.apply:
            print("\n预览模式，加 --apply 执行合并与规范化。")
            return 0

        with conn.cursor() as cur:
            for clean, rows in groups.items():
                merged = _pick_row(rows)
                cur.execute(
                    f"DELETE FROM trading_symbol_rating WHERE {_RATING_CLEAN} = %s",
                    (clean,),
                )
                cols = [
                    "symbol",
                    "rating_level",
                    "margin_multiplier",
                    "score_bonus",
                    "hard_stop_loss_count",
                    "total_loss_amount",
                    "total_profit_amount",
                    "win_rate",
                    "total_trades",
                    "previous_level",
                    "level_changed_at",
                    "level_change_reason",
                    "stats_start_date",
                    "stats_end_date",
                    "reason",
                ]
                present = {k: merged.get(k) for k in cols if k in merged}
                if "margin_multiplier" not in present or present["margin_multiplier"] is None:
                    lvl = int(merged["rating_level"])
                    present["margin_multiplier"] = {3: 0.0, 2: 0.125, 1: 0.25}.get(lvl, 1.0)
                if "score_bonus" not in present or present["score_bonus"] is None:
                    lvl = int(merged["rating_level"])
                    present["score_bonus"] = {3: 999, 2: 10, 1: 5}.get(lvl, 0)

                keys = list(present.keys())
                placeholders = ", ".join(["%s"] * len(keys))
                col_sql = ", ".join(keys)
                cur.execute(
                    f"INSERT INTO trading_symbol_rating ({col_sql}) VALUES ({placeholders})",
                    tuple(present[k] for k in keys),
                )
        conn.commit()
        print(f"\nOK: 已规范化 {len(groups)} 条 canonical 记录。")
        return 0
    except Exception as e:
        conn.rollback()
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
