#!/usr/bin/env python3
"""Audit symbol column formats across DB tables (XXX/USDT vs XXXUSDT)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config

TABLES = [
    "futures_trades",
    "futures_orders",
    "futures_positions",
    "futures_liquidations",
    "kline_data",
    "trading_symbols",
    "trade_data",
    "futures_open_interest",
    "orderbook_data",
    "funding_rate_data",
    "live_futures_trades",
    "live_futures_orders",
    "live_futures_positions",
    "top_performing_symbols",
    "signal_blacklist",
    "gemini_advisor_reviews",
    "deepseek_advisor_reviews",
    "gpt_advisor_reviews",
    "pending_positions",
    "sentinel_orders",
    "coin_kline_scores",
    "technical_signals_cache",
    "technical_indicators_cache",
    "trading_symbol_rating",
    "futures_long_short_ratio",
]


def main() -> int:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            all_tables = {list(r.values())[0] for r in cur.fetchall()}

        print("=== Tables with symbol column (no slash format) ===\n")
        print(f"{'table':<40} {'total':>12} {'no_slash':>12} {'dist_ns':>8}")
        print("-" * 76)

        extra_symbol_tables = []
        for table in sorted(all_tables):
            with conn.cursor() as cur:
                cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'symbol'")
                if not cur.fetchone():
                    continue
                extra_symbol_tables.append(table)

        for table in sorted(set(TABLES) | set(extra_symbol_tables)):
            with conn.cursor() as cur:
                cur.execute(f"SHOW COLUMNS FROM `{table}` LIKE 'symbol'")
                if not cur.fetchone():
                    continue
                try:
                    cur.execute(f"SELECT COUNT(*) AS n FROM `{table}`")
                    total = int(cur.fetchone()["n"])
                    cur.execute(
                        f"SELECT COUNT(*) AS n FROM `{table}` WHERE symbol NOT LIKE '%/%'"
                    )
                    bad = int(cur.fetchone()["n"])
                    cur.execute(
                        f"SELECT COUNT(DISTINCT symbol) AS n FROM `{table}` "
                        f"WHERE symbol NOT LIKE '%/%'"
                    )
                    ds = int(cur.fetchone()["n"])
                except Exception as e:
                    print(f"{table:<40} SKIP ({e})")
                    continue
                if bad > 0 or table in TABLES:
                    print(f"{table:<40} {total:>12} {bad:>12} {ds:>8}")
                    if 0 < bad <= 20:
                        cur.execute(
                            f"SELECT DISTINCT symbol FROM `{table}` "
                            f"WHERE symbol NOT LIKE '%/%' LIMIT 15"
                        )
                        samples = [r["symbol"] for r in cur.fetchall()]
                        print(f"  samples: {samples}")
                    elif bad > 20:
                        cur.execute(
                            f"SELECT symbol, COUNT(*) c FROM `{table}` "
                            f"WHERE symbol NOT LIKE '%/%' "
                            f"GROUP BY symbol ORDER BY c DESC LIMIT 10"
                        )
                        for r in cur.fetchall():
                            print(f"  top: {r['symbol']} x{r['c']}")

        # data_cache DB
        print("\n=== data_cache schema (if accessible) ===")
        try:
            cfg = get_db_config()
            cache_cfg = dict(cfg)
            cache_cfg["database"] = "data_cache"
            cconn = pymysql.connect(
                **cache_cfg,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
            )
            with cconn.cursor() as cur:
                cur.execute("SHOW TABLES")
                for row in cur.fetchall():
                    t = list(row.values())[0]
                    cur.execute(f"SHOW COLUMNS FROM `{t}`")
                    cols = [c["Field"] for c in cur.fetchall()]
                    sym_cols = [c for c in cols if "symbol" in c.lower()]
                    if sym_cols:
                        for col in sym_cols:
                            cur.execute(
                                f"SELECT COUNT(*) n FROM `{t}` "
                                f"WHERE `{col}` NOT LIKE '%/%' AND `{col}` != ''"
                            )
                            bad = int(cur.fetchone()["n"])
                            if bad:
                                print(f"  data_cache.{t}.{col}: {bad} rows without slash")
            cconn.close()
        except Exception as e:
            print(f"  (skip data_cache: {e})")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
