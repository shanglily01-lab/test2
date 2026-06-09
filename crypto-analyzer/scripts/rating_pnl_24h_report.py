"""24h PnL for whitelist/blacklist symbols — paper (account_id=2) vs live."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
import pymysql.cursors

from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_clean

HOURS = 24


def _pnl_row(rows: list) -> dict:
    wins = losses = breakeven = 0
    net = gross_profit = gross_loss = 0.0
    for r in rows:
        pnl = float(r.get("realized_pnl") or 0)
        net += pnl
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        elif pnl < 0:
            losses += 1
            gross_loss += abs(pnl)
        else:
            breakeven += 1
    total = wins + losses + breakeven
    wr = round(wins / total * 100, 1) if total else 0.0
    return {
        "trades": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate_pct": wr,
        "net_pnl": round(net, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
    }


def _print_group(title: str, paper: dict, live: dict) -> None:
    print(f"\n=== {title} ===")
    for label, d in (("模拟盘 (futures_positions acct=2)", paper), ("实盘 (live_futures_positions)", live)):
        print(
            f"  {label}: {d['trades']}笔 "
            f"胜{d['wins']}/负{d['losses']} 胜率{d['win_rate_pct']}% "
            f"盈亏{d['net_pnl']:+.2f}U "
            f"(盈{d['gross_profit']:+.2f} / 亏-{d['gross_loss']:.2f})"
        )


def _print_top(title: str, rows: list, limit: int = 15) -> None:
    if not rows:
        print(f"  (无)")
        return
    print(f"\n--- {title} (按盈亏) ---")
    for r in rows[:limit]:
        print(
            f"  {r['symbol']:14s} L{r['rating_level']} "
            f"{r['book']:4s} {int(r['n']):2d}笔 {float(r['pnl']):+8.2f}U"
        )


def main() -> int:
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    cur = conn.cursor()

    cur.execute("SELECT NOW() AS db_now, UTC_TIMESTAMP() AS db_utc")
    ts = cur.fetchone()
    print(f"统计窗口: 近 {HOURS} 小时 (DB NOW={ts['db_now']}, UTC={ts['db_utc']})")

    cur.execute(
        """
        SELECT symbol, rating_level, level_change_reason
        FROM trading_symbol_rating
        WHERE rating_level IN (0, 1, 2, 3)
        """
    )
    rating_map = {}
    for r in cur.fetchall():
        key = futures_symbol_clean(r["symbol"])
        rating_map[key] = {
            "symbol": r["symbol"],
            "rating_level": int(r["rating_level"]),
            "reason": r.get("level_change_reason") or "",
        }

    wl_keys = {k for k, v in rating_map.items() if v["rating_level"] == 0}
    bl_keys = {k for k, v in rating_map.items() if v["rating_level"] >= 1}

    # 模拟盘
    cur.execute(
        """
        SELECT symbol, realized_pnl, source, close_time
        FROM futures_positions
        WHERE account_id = 2
          AND status = 'closed'
          AND realized_pnl IS NOT NULL
          AND close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
        """,
        (HOURS,),
    )
    paper_rows = cur.fetchall()

    # 实盘
    cur.execute(
        """
        SELECT symbol, realized_pnl, source, close_time, account_id
        FROM live_futures_positions
        WHERE status IN ('CLOSED', 'LIQUIDATED')
          AND realized_pnl IS NOT NULL
          AND close_time >= DATE_SUB(NOW(), INTERVAL %s HOUR)
        """,
        (HOURS,),
    )
    live_rows = cur.fetchall()

    def bucket(rows, keys, book: str):
        out = []
        for r in rows:
            key = futures_symbol_clean(r["symbol"])
            if key not in keys:
                continue
            out.append(r)
        return out

    paper_wl = bucket(paper_rows, wl_keys, "paper")
    paper_bl = bucket(paper_rows, bl_keys, "paper")
    live_wl = bucket(live_rows, wl_keys, "live")
    live_bl = bucket(live_rows, bl_keys, "paper")

    wl_paper = _pnl_row(paper_wl)
    wl_live = _pnl_row(live_wl)
    bl_paper = _pnl_row(paper_bl)
    bl_live = _pnl_row(live_bl)

    all_paper = _pnl_row(paper_wl + paper_bl)
    all_live = _pnl_row(live_wl + live_bl)

    print(f"\n评级库: 白名单 L0={len(wl_keys)} 个, 黑名单 L1-L3={len(bl_keys)} 个")
    print(
        f"近24h平仓: 模拟盘 {len(paper_rows)} 笔, 实盘 {len(live_rows)} 笔 "
        f"(其中白名单币 模拟{len(paper_wl)}/实盘{len(live_wl)}, "
        f"黑名单币 模拟{len(paper_bl)}/实盘{len(live_bl)})"
    )

    _print_group("白名单 L0", wl_paper, wl_live)
    _print_group("黑名单 L1/L2/L3", bl_paper, bl_live)
    _print_group("白名单+黑名单 合计", all_paper, all_live)

    # per-symbol breakdown
    sym_agg = {}
    for r in paper_wl + paper_bl:
        key = futures_symbol_clean(r["symbol"])
        meta = rating_map[key]
        k = (key, "paper")
        sym_agg.setdefault(k, {"symbol": meta["symbol"], "rating_level": meta["rating_level"], "book": "paper", "n": 0, "pnl": 0.0})
        sym_agg[k]["n"] += 1
        sym_agg[k]["pnl"] += float(r["realized_pnl"] or 0)
    for r in live_wl + live_bl:
        key = futures_symbol_clean(r["symbol"])
        meta = rating_map[key]
        k = (key, "live")
        sym_agg.setdefault(k, {"symbol": meta["symbol"], "rating_level": meta["rating_level"], "book": "live", "n": 0, "pnl": 0.0})
        sym_agg[k]["n"] += 1
        sym_agg[k]["pnl"] += float(r["realized_pnl"] or 0)

    ranked = sorted(sym_agg.values(), key=lambda x: x["pnl"], reverse=True)
    _print_top("盈利 TOP", ranked)
    _print_top("亏损 TOP", sorted(sym_agg.values(), key=lambda x: x["pnl"]))

    # by blacklist level
    print("\n=== 黑名单分级 (近24h) ===")
    for lvl in (1, 2, 3):
        keys = {k for k, v in rating_map.items() if v["rating_level"] == lvl}
        p = _pnl_row(bucket(paper_rows, keys, "paper"))
        l = _pnl_row(bucket(live_rows, keys, "live"))
        print(
            f"  L{lvl}: 模拟 {p['trades']}笔 {p['net_pnl']:+.2f}U | "
            f"实盘 {l['trades']}笔 {l['net_pnl']:+.2f}U"
        )

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
