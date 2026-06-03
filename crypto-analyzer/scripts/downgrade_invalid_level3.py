#!/usr/bin/env python3
"""将不满足新 L3 规则的 rating_level=3 交易对降级.

新 L3 条件 (与 update_top_performers 一致):
  - 累计盈利 < -200U，或
  - 胜率 < 40% 且累计盈利 < 0

用法:
  python scripts/downgrade_invalid_level3.py              # 预览
  python scripts/downgrade_invalid_level3.py --apply      # 写入
  python scripts/downgrade_invalid_level3.py --apply --include-no-stats  # 含不足5笔平仓的L3
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql

from app.services.optimization_config import OptimizationConfig
from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical
from update_top_performers import (
    BLACKLIST_MAX_PNL,
    BLACKLIST_MAX_WIN_RATE,
    MIN_TRADES,
    WHITELIST_MIN_PNL,
    WHITELIST_MIN_WIN_RATE,
    _should_ban_level3,
)

ACCOUNT_ID = 2

STATS_SQL = """
    SELECT
        symbol,
        COUNT(*) AS total_trades,
        COALESCE(SUM(realized_pnl), 0) AS net_pnl,
        COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit,
        COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END), 0) AS gross_loss,
        CASE
            WHEN COUNT(*) > 0
            THEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
            ELSE 0
        END AS win_rate
    FROM futures_positions
    WHERE account_id = %s AND status = 'closed' AND realized_pnl IS NOT NULL
    GROUP BY symbol
"""


def _keep_manual_l3(reason: str) -> bool:
    r = (reason or "").lower()
    markers = ("手动", "manual", "流动性", "波动极端", "permanent", "永久")
    return any(m in r for m in markers) and "日终自动黑名单3级" not in (reason or "")


def _target_level(pnl: float, win_rate: float) -> tuple[int, str]:
    """L3 下架后的目标等级."""
    if pnl > WHITELIST_MIN_PNL or win_rate > WHITELIST_MIN_WIN_RATE:
        return 0, f"纠偏下架L3→白名单(盈利{pnl:.0f}U/胜率{win_rate:.1f}%)"
    if pnl >= 0:
        return 0, f"纠偏下架L3→白名单(累计盈利{pnl:.0f}U, 不满足新永久封禁)"
    if pnl >= BLACKLIST_MAX_PNL and win_rate >= BLACKLIST_MAX_WIN_RATE:
        return 0, f"纠偏下架L3→白名单(盈利{pnl:.0f}U/胜率{win_rate:.1f}%, 未达L3门槛)"
    if pnl < 0 and pnl >= BLACKLIST_MAX_PNL:
        return 1, f"纠偏下架L3→L1(盈利{pnl:.0f}U/胜率{win_rate:.1f}%, 轻度亏损)"
    return 0, f"纠偏下架L3→白名单(盈利{pnl:.0f}U)"


def fetch_stats_map(conn, account_id: int) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(STATS_SQL, (account_id,))
        rows = cur.fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        key = futures_symbol_clean(r["symbol"])
        out[key] = {
            "symbol": r["symbol"],
            "total_trades": int(r["total_trades"] or 0),
            "net_pnl": float(r["net_pnl"] or 0),
            "gross_profit": float(r["gross_profit"] or 0),
            "gross_loss": float(r["gross_loss"] or 0),
            "win_rate": float(r["win_rate"] or 0),
        }
    return out


def fetch_level3_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, rating_level, level_change_reason, updated_at
            FROM trading_symbol_rating
            WHERE rating_level >= 3
            ORDER BY symbol
            """
        )
        return list(cur.fetchall())


def main() -> int:
    parser = argparse.ArgumentParser(description="降级不满足新 L3 规则的交易对")
    parser.add_argument("--apply", action="store_true", help="写入数据库 (默认仅预览)")
    parser.add_argument(
        "--include-no-stats",
        action="store_true",
        help="对无足够平仓统计的 L3 也降级为 0 (默认跳过含手动/流动性备注的)",
    )
    parser.add_argument("--account-id", type=int, default=ACCOUNT_ID)
    args = parser.parse_args()

    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        l3_rows = fetch_level3_rows(conn)
        stats_map = fetch_stats_map(conn, args.account_id)
    finally:
        conn.close()

    keep = []
    downgrade = []
    skip_manual = []
    skip_no_stats = []

    for row in l3_rows:
        sym = row["symbol"]
        reason = row.get("level_change_reason") or ""
        key = futures_symbol_clean(sym)
        st = stats_map.get(key)

        if not st or st["total_trades"] < MIN_TRADES:
            if _keep_manual_l3(reason):
                skip_manual.append(row)
                continue
            if not args.include_no_stats:
                skip_no_stats.append(row)
                continue
            downgrade.append({
                "symbol": sym,
                "pnl": None,
                "wr": None,
                "trades": 0,
                "new_level": 0,
                "reason": "纠偏下架L3→白名单(平仓不足, 移除误封)",
                "old_reason": reason[:80],
            })
            continue

        pnl = st["net_pnl"]
        wr = st["win_rate"]
        if _should_ban_level3(pnl, wr)[0]:
            keep.append((sym, pnl, wr, reason[:60]))
            continue

        new_level, new_reason = _target_level(pnl, wr)
        downgrade.append({
            "symbol": sym,
            "pnl": pnl,
            "wr": wr,
            "trades": st["total_trades"],
            "new_level": new_level,
            "reason": new_reason,
            "old_reason": reason[:80],
        })

    print(f"L3 总数: {len(l3_rows)}")
    print(f"  保留 L3 (仍满足新规则): {len(keep)}")
    print(f"  计划降级: {len(downgrade)}")
    print(f"  跳过(手动/流动性备注): {len(skip_manual)}")
    print(f"  跳过(无统计且未 --include-no-stats): {len(skip_no_stats)}")
    print()

    if downgrade:
        print(f"{'交易对':<18} {'新等级':>4} {'平仓':>5} {'盈亏':>10} {'胜率':>7} 原因")
        print("-" * 90)
        for d in downgrade[:40]:
            pnl_s = f"{d['pnl']:+.1f}" if d["pnl"] is not None else "n/a"
            wr_s = f"{d['wr']:.1f}%" if d["wr"] is not None else "n/a"
            print(
                f"{d['symbol']:<18} L{d['new_level']:>2} "
                f"{d['trades']:>5} {pnl_s:>10} {wr_s:>7} {d['reason'][:40]}"
            )
        if len(downgrade) > 40:
            print(f"... 另有 {len(downgrade) - 40} 条")

    if not downgrade:
        print("无需降级。")
        return 0

    if not args.apply:
        print()
        print("预览模式，未改库。执行: python scripts/downgrade_invalid_level3.py --apply")
        return 0

    opt = OptimizationConfig(get_db_config())
    ok = 0
    for d in downgrade:
        st = stats_map.get(futures_symbol_clean(d["symbol"]), {})
        opt.update_symbol_rating(
            symbol=futures_symbol_rating_canonical(d["symbol"]),
            new_level=d["new_level"],
            reason=d["reason"],
            total_loss_amount=float(st.get("gross_loss") or 0),
            total_profit_amount=float(st.get("gross_profit") or 0),
            win_rate=float(d["wr"] or 0) / 100.0,
            total_trades=int(d["trades"] or 0),
        )
        ok += 1
        print(f"  OK {d['symbol']} -> L{d['new_level']}")

    print()
    print(f"已降级 {ok} 个交易对。保留 L3: {len(keep)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
