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

ACCOUNT_ID = 2
MIN_TRADES = 5
BLACKLIST_MAX_PNL = -200.0
BLACKLIST_MAX_WIN_RATE = 40.0

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
        by_pnl_only = 0
        by_wr_only = 0
        by_both = 0
        by_neither = 0
        no_stats = 0
        wr_only_but_pnl_positive = []

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
            hit_pnl = pnl < BLACKLIST_MAX_PNL
            hit_wr_loss = wr < BLACKLIST_MAX_WIN_RATE and pnl < 0
            hit_wr_only_old = wr < BLACKLIST_MAX_WIN_RATE and not hit_pnl

            if hit_pnl and hit_wr_loss:
                by_both += 1
            elif hit_pnl:
                by_pnl_only += 1
            elif hit_wr_loss:
                by_wr_only += 1
            elif hit_wr_only_old:
                if pnl >= 0:
                    wr_only_but_pnl_positive.append(
                        (row["symbol"], pnl, wr, reason[:60])
                    )
            else:
                by_neither += 1

        print("=== L3 原因来源 ===")
        print(f"  日终自动黑名单3级: {auto_daily}")
        print(f"  其他原因(手动/旧逻辑): {other_reason}")
        print()
        print("=== L3 对照 account_id=2 累计统计 (有>=5笔平仓) ===")
        print(f"  同时 pnl<-200 且 胜率<40%: {by_both}")
        print(f"  仅 pnl<-200: {by_pnl_only}")
        print(f"  仅 胜率<40%且盈利<0(新规则): {by_wr_only}")
        print(f"  旧规则误伤(胜率<40%但盈利>=0): {len(wr_only_but_pnl_positive)}")
        print(f"  两项都不满足(历史L3或口径不一致): {by_neither}")
        print(f"  无平仓统计(未交易够5笔): {no_stats}")
        print()

        # 若全市场跑日终规则会命中多少
        would_old = would_new = 0
        would_old_wr_profitable = 0
        for st in stats_by_key.values():
            pnl = float(st["total_realized_pnl"] or 0)
            wr = float(st["win_rate"] or 0)
            if pnl < BLACKLIST_MAX_PNL or wr < BLACKLIST_MAX_WIN_RATE:
                would_old += 1
                if wr < BLACKLIST_MAX_WIN_RATE and pnl >= 0:
                    would_old_wr_profitable += 1
            if pnl < BLACKLIST_MAX_PNL or (wr < BLACKLIST_MAX_WIN_RATE and pnl < 0):
                would_new += 1

        print("=== 旧规则 pnl<-200 OR 胜率<40% ===")
        print(f"  会设为 L3: {would_old} / {len(stats_by_key)}")
        print(f"    其中盈利>=0仅因胜率<40%: {would_old_wr_profitable}")
        print("=== 新规则 pnl<-200 OR (胜率<40% AND 盈利<0) ===")
        print(f"  会设为 L3: {would_new} / {len(stats_by_key)}")
        print()

        if wr_only_but_pnl_positive:
            print(f"=== 典型：L3 但累计盈利>=0、只因胜率<40% (前15个) ===")
            for sym, pnl, wr, rsn in wr_only_but_pnl_positive[:15]:
                print(f"  {sym:16} pnl={pnl:+8.2f} wr={wr:5.1f}% | {rsn}")
            if len(wr_only_but_pnl_positive) > 15:
                print(f"  ... 共 {len(wr_only_but_pnl_positive)} 个")

    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
