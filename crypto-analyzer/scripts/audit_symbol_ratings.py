"""Audit TOP50 / whitelist / blacklist coverage for symbols with trades."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
import pymysql.cursors

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical
from update_top_performers import MIN_TRADES, compute_rating_level

TARGETS = ["SAHARA", "DEXE", "SAHARA/USDT", "DEXE/USDT", "SAHARAUSDT", "DEXEUSDT"]


def main() -> int:
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()

    print("=== 机制摘要 ===")
    print(f"- TOP50 / 评级数据源: futures_positions account_id=2, status=closed")
    print(f"- 入表门槛: 至少 {MIN_TRADES} 笔已平仓")
    print(f"- trading_symbol_rating: 仅对达标币种写入; <5笔不在表中")
    print()

    for sym in TARGETS:
        clean = futures_symbol_clean(sym)
        canon = futures_symbol_rating_canonical(sym)
        print(f"--- {sym} (clean={clean}, canon={canon}) ---")

        cur.execute(
            """
            SELECT account_id, symbol, COUNT(*) AS n,
                   SUM(realized_pnl) AS pnl,
                   SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM futures_positions
            WHERE REPLACE(UPPER(symbol), '/', '') = %s
              AND status = 'closed' AND realized_pnl IS NOT NULL
            GROUP BY account_id, symbol
            ORDER BY account_id, symbol
            """,
            (clean,),
        )
        rows = cur.fetchall()
        if not rows:
            print("  futures_positions: 无已平仓记录")
        else:
            for r in rows:
                wr = (r["wins"] / r["n"] * 100) if r["n"] else 0
                print(
                    f"  acct={r['account_id']} sym={r['symbol']} closed={r['n']} "
                    f"pnl={float(r['pnl'] or 0):+.2f} wr={wr:.1f}%"
                )

        cur.execute(
            """
            SELECT symbol, COUNT(*) AS n, SUM(realized_pnl) AS pnl,
                   SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
            FROM futures_positions
            WHERE account_id = 2 AND status = 'closed' AND realized_pnl IS NOT NULL
              AND REPLACE(UPPER(symbol), '/', '') = %s
            GROUP BY symbol
            """,
            (clean,),
        )
        sim = cur.fetchone()
        if sim:
            n = int(sim["n"])
            pnl = float(sim["pnl"] or 0)
            wr = (int(sim["wins"]) / n * 100) if n else 0
            level, reason = compute_rating_level(pnl, wr, n)
            print(
                f"  模拟仓(account=2) 合计: {n}笔 pnl={pnl:+.2f} wr={wr:.1f}% "
                f"→ 应评 L{level} ({reason})"
            )
            if n < MIN_TRADES:
                print(f"  [!] 未达 {MIN_TRADES} 笔门槛 -> 不会写入 trading_symbol_rating / TOP50")
        else:
            print("  模拟仓(account=2): 无已平仓")

        cur.execute(
            "SELECT symbol, rating_level, total_trades, level_change_reason, updated_at "
            "FROM trading_symbol_rating WHERE REPLACE(UPPER(symbol), '/', '') = %s",
            (clean,),
        )
        ratings = cur.fetchall()
        if ratings:
            for r in ratings:
                print(
                    f"  trading_symbol_rating: {r['symbol']} L{r['rating_level']} "
                    f"trades={r['total_trades']} reason={r['level_change_reason']}"
                )
        else:
            print("  trading_symbol_rating: 无记录")

        cur.execute(
            "SELECT symbol, rank_score, total_realized_pnl, total_trades "
            "FROM top_performing_symbols WHERE REPLACE(UPPER(symbol), '/', '') = %s",
            (clean,),
        )
        top = cur.fetchone()
        if top:
            print(
                f"  top_performing_symbols: rank={top['rank_score']} "
                f"pnl={float(top['total_realized_pnl']):+.2f} trades={top['total_trades']}"
            )
        else:
            print("  top_performing_symbols: 不在 TOP50")
        print()

    print("=== 有模拟仓交易但未入评级表的币种 (account=2, closed>=1, <5笔) ===")
    cur.execute(
        """
        SELECT symbol, COUNT(*) AS n, SUM(realized_pnl) AS pnl
        FROM futures_positions
        WHERE account_id = 2 AND status = 'closed' AND realized_pnl IS NOT NULL
        GROUP BY symbol
        HAVING n > 0 AND n < %s
        ORDER BY n DESC, pnl DESC
        LIMIT 30
        """,
        (MIN_TRADES,),
    )
    for r in cur.fetchall():
        clean = futures_symbol_clean(r["symbol"])
        cur.execute(
            "SELECT 1 FROM trading_symbol_rating WHERE REPLACE(UPPER(symbol), '/', '') = %s LIMIT 1",
            (clean,),
        )
        in_rating = bool(cur.fetchone())
        cur.execute(
            "SELECT 1 FROM top_performing_symbols WHERE REPLACE(UPPER(symbol), '/', '') = %s LIMIT 1",
            (clean,),
        )
        in_top = bool(cur.fetchone())
        if not in_rating and not in_top:
            print(
                f"  {r['symbol']}: {r['n']}笔 pnl={float(r['pnl'] or 0):+.2f} "
                f"(未入表)"
            )

    print()
    print("=== 有模拟仓交易且>=5笔但不在评级表的异常币种 ===")
    cur.execute(
        """
        SELECT symbol, COUNT(*) AS n, SUM(realized_pnl) AS pnl,
               SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
        FROM futures_positions
        WHERE account_id = 2 AND status = 'closed' AND realized_pnl IS NOT NULL
        GROUP BY symbol
        HAVING n >= %s
        ORDER BY n DESC
        """,
        (MIN_TRADES,),
    )
    for r in cur.fetchall():
        clean = futures_symbol_clean(r["symbol"])
        cur.execute(
            "SELECT 1 FROM trading_symbol_rating WHERE REPLACE(UPPER(symbol), '/', '') = %s LIMIT 1",
            (clean,),
        )
        if not cur.fetchone():
            n = int(r["n"])
            pnl = float(r["pnl"] or 0)
            wr = (int(r["wins"]) / n * 100) if n else 0
            level, reason = compute_rating_level(pnl, wr, n)
            print(
                f"  [!] {r['symbol']}: {n}笔 pnl={pnl:+.2f} 应L{level} 但无评级记录!"
            )

    print("=== SAHARA/DEXE 全部持仓明细 ===")
    for clean in ("SAHARAUSDT", "DEXEUSDT"):
        cur.execute(
            """
            SELECT id, account_id, symbol, status, source, realized_pnl, open_time, close_time
            FROM futures_positions
            WHERE REPLACE(UPPER(symbol), '/', '') = %s
            ORDER BY id
            """,
            (clean,),
        )
        rows = cur.fetchall()
        print(f"{clean}: {len(rows)} rows")
        for r in rows:
            print(
                f"  id={r['id']} acct={r['account_id']} {r['symbol']} {r['status']} "
                f"src={r['source']} pnl={r['realized_pnl']}"
            )

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
