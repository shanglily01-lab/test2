#!/usr/bin/env python3
"""审计 trading_symbol_rating L3：与日终规则口径对照."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql
from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_clean
from update_top_performers import MIN_TRADES, _should_ban_level3

ACCOUNT_ID = 2

STATS_SQL = """
    SELECT
        symbol,
        COUNT(*) as total_trades,
        COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
        CASE
            WHEN COUNT(*) > 0
            THEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            ELSE 0
        END as win_rate
    FROM futures_positions
    WHERE account_id = %s AND status = 'closed' AND realized_pnl IS NOT NULL
    GROUP BY symbol
    HAVING total_trades >= %s
"""


def main() -> int:
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, rating_level, level_change_reason, updated_at "
                "FROM trading_symbol_rating WHERE rating_level >= 3 ORDER BY symbol"
            )
            l3_rows = cur.fetchall()

            cur.execute(STATS_SQL, (ACCOUNT_ID, MIN_TRADES))
            stats_by_key = {
                futures_symbol_clean(r["symbol"]): r for r in cur.fetchall()
            }

        print(f"L3 记录数 (rating_level>=3): {len(l3_rows)}")
        print(f"有>={MIN_TRADES}笔平仓的交易对: {len(stats_by_key)}")
        print()

        auto_daily = 0
        other_reason = 0
        hit_current_rule = 0
        hit_pnl_only = 0
        hit_wr_only = 0
        hit_neither = 0
        no_stats = 0
        profitable_low_wr = []

        for row in l3_rows:
            reason = (row.get("level_change_reason") or "")
            if "日终自动黑名单3级" in reason:
                auto_daily += 1
            else:
                other_reason += 1

            key = futures_symbol_clean(row.get("symbol") or "")
            st = stats_by_key.get(key)
            if not st:
                no_stats += 1
                continue

            pnl = float(st["total_realized_pnl"] or 0)
            wr = float(st["win_rate"] or 0)
            if _should_ban_level3(pnl, wr)[0]:
                hit_current_rule += 1
            elif pnl < -200:
                hit_pnl_only += 1
            elif wr < 40:
                hit_wr_only += 1
                if pnl >= 0:
                    profitable_low_wr.append((row["symbol"], pnl, wr, reason[:60]))
            else:
                hit_neither += 1

        print("=== L3 原因来源 ===")
        print(f"  日终自动黑名单3级: {auto_daily}")
        print(f"  其他原因(手动/旧逻辑): {other_reason}")
        print()
        print("=== L3 对照 account_id=2 累计统计 (有>=5笔平仓) ===")
        print(f"  满足当前规则(亏损>200U 且 胜率<40%): {hit_current_rule}")
        print(f"  仅亏损>200U(胜率>=40%, 当前规则不封): {hit_pnl_only}")
        print(f"  仅胜率<40%(亏损<=200U, 当前规则不封): {hit_wr_only}")
        print(f"    其中盈利>=0仅因胜率<40%: {len(profitable_low_wr)}")
        print(f"  两项都不满足(历史L3/手动): {hit_neither}")
        print(f"  无平仓统计(未交易够{MIN_TRADES}笔): {no_stats}")
        print()

        would_and = would_old_or = 0
        for st in stats_by_key.values():
            pnl = float(st["total_realized_pnl"] or 0)
            wr = float(st["win_rate"] or 0)
            if _should_ban_level3(pnl, wr)[0]:
                would_and += 1
            if pnl < -200 or wr < 40:
                would_old_or += 1

        print("=== 当前日终规则: 亏损>200U 且 胜率<40% ===")
        print(f"  会设为 L3: {would_and} / {len(stats_by_key)}")
        print("=== 旧规则(已废弃): 亏损>200U 或 胜率<40% ===")
        print(f"  会设为 L3: {would_old_or} / {len(stats_by_key)}")
        print()

        if profitable_low_wr:
            print(f"=== L3 但仅胜率<40%、累计仍盈利 (前15个) ===")
            for sym, pnl, wr, rsn in profitable_low_wr[:15]:
                print(f"  {sym:16} pnl={pnl:+8.2f} wr={wr:5.1f}% | {rsn}")
            if len(profitable_low_wr) > 15:
                print(f"  ... 共 {len(profitable_low_wr)} 个")

    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
