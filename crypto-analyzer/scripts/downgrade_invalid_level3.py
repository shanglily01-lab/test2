#!/usr/bin/env python3
"""将不满足新 L3 规则的 rating_level=3 交易对降级.

新 L3 条件 (与 update_top_performers 一致):
  - 累计亏损 > 200U 且 胜率 < 40%

下架后目标等级:
  - 仅当 盈利>200U 或 胜率>55% → L0 白名单
  - 其余 → L1（禁止直接进白名单）

用法:
  python scripts/downgrade_invalid_level3.py                    # 预览 L3 降级
  python scripts/downgrade_invalid_level3.py --apply
  python scripts/downgrade_invalid_level3.py --fix-mis-whitelist       # 预览纠偏误标 L0
  python scripts/downgrade_invalid_level3.py --fix-all-invalid-l0      # 预览全部无效 L0
  python scripts/downgrade_invalid_level3.py --fix-all-invalid-l0 --apply
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
    MIN_TRADES,
    qualifies_whitelist,
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
    """L3 下架后的目标等级（非白名单一律 L1）."""
    if qualifies_whitelist(pnl, win_rate):
        return 0, f"纠偏下架L3→白名单(盈利{pnl:.0f}U/胜率{win_rate:.1f}%)"
    return 1, (
        f"纠偏下架L3→L1(盈利{pnl:.0f}U/胜率{win_rate:.1f}%, "
        f"未达白名单>200U或>55%胜率)"
    )


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


def fetch_mis_whitelist_rows(conn) -> list[dict]:
    """L0 且由纠偏脚本误标、但统计上不满足白名单门槛."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, rating_level, level_change_reason
            FROM trading_symbol_rating
            WHERE rating_level = 0
              AND level_change_reason LIKE %s
            ORDER BY symbol
            """,
            ("%纠偏下架L3%",),
        )
        return list(cur.fetchall())


def fetch_all_whitelist_rows(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, rating_level, level_change_reason
            FROM trading_symbol_rating
            WHERE rating_level = 0
            ORDER BY symbol
            """
        )
        return list(cur.fetchall())


def _keep_manual_whitelist(reason: str) -> bool:
    r = (reason or "").lower()
    return "手动" in r or "manual" in r


def run_l3_downgrade(args, stats_map: dict, opt: OptimizationConfig | None) -> int:
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        l3_rows = fetch_level3_rows(conn)
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
                "new_level": 1,
                "reason": "纠偏下架L3→L1(平仓不足, 移除误封)",
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
    wl_cnt = sum(1 for d in downgrade if d["new_level"] == 0)
    print(f"    其中 → 白名单 L0: {wl_cnt}，→ L1: {len(downgrade) - wl_cnt}")
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
                f"{d['trades']:>5} {pnl_s:>10} {wr_s:>7} {d['reason'][:42]}"
            )
        if len(downgrade) > 40:
            print(f"... 另有 {len(downgrade) - 40} 条")

    if not downgrade:
        print("无需降级 L3。")
        return 0

    if not args.apply:
        print()
        print("预览模式。执行: python scripts/downgrade_invalid_level3.py --apply")
        return 0

    assert opt is not None
    for d in downgrade:
        _apply_rating(opt, stats_map, d)
    print()
    print(f"已处理 {len(downgrade)} 个 L3 降级。保留 L3: {len(keep)}")
    return 0


def run_fix_mis_whitelist(args, stats_map: dict, opt: OptimizationConfig | None) -> int:
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        rows = fetch_mis_whitelist_rows(conn)
    finally:
        conn.close()

    fixes = []
    keep_wl = []
    no_stats = []

    for row in rows:
        sym = row["symbol"]
        key = futures_symbol_clean(sym)
        st = stats_map.get(key)
        if not st or st["total_trades"] < MIN_TRADES:
            no_stats.append(sym)
            continue
        pnl = st["net_pnl"]
        wr = st["win_rate"]
        if qualifies_whitelist(pnl, wr):
            keep_wl.append((sym, pnl, wr))
            continue
        fixes.append({
            "symbol": sym,
            "pnl": pnl,
            "wr": wr,
            "trades": st["total_trades"],
            "new_level": 1,
            "reason": (
                f"纠偏误标白名单→L1(盈利{pnl:.0f}U/胜率{wr:.1f}%, "
                f"需>200U或>55%胜率)"
            ),
        })

    print(f"纠偏误标白名单(L0) 记录: {len(rows)}")
    print(f"  维持 L0 (满足>200U或>55%胜率): {len(keep_wl)}")
    print(f"  改回 L1: {len(fixes)}")
    print(f"  无足够平仓统计(跳过): {len(no_stats)}")
    print()

    if fixes:
        print(f"{'交易对':<18} {'盈亏':>10} {'胜率':>7}")
        print("-" * 40)
        for d in fixes[:30]:
            print(f"{d['symbol']:<18} {d['pnl']:>+10.1f} {d['wr']:>6.1f}%")
        if len(fixes) > 30:
            print(f"... 另有 {len(fixes) - 30} 个")

    if not fixes:
        print("无需修正误标白名单。")
        return 0

    if not args.apply:
        print()
        print("预览模式。执行: python scripts/downgrade_invalid_level3.py --fix-mis-whitelist --apply")
        return 0

    assert opt is not None
    for d in fixes:
        _apply_rating(opt, stats_map, d)
    print()
    print(f"已将 {len(fixes)} 个误标白名单改回 L1。")
    return 0


def run_fix_all_invalid_l0(args, stats_map: dict, opt: OptimizationConfig | None) -> int:
    """扫描全部 L0，不满足白名单门槛的改 L1（跳过手动）."""
    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        rows = fetch_all_whitelist_rows(conn)
    finally:
        conn.close()

    fixes = []
    keep_wl = []
    skip_manual = []
    no_stats = []

    for row in rows:
        sym = row["symbol"]
        reason = row.get("level_change_reason") or ""
        if _keep_manual_whitelist(reason):
            skip_manual.append(sym)
            continue
        key = futures_symbol_clean(sym)
        st = stats_map.get(key)
        if not st or st["total_trades"] < MIN_TRADES:
            no_stats.append(sym)
            continue
        pnl = st["net_pnl"]
        wr = st["win_rate"]
        if qualifies_whitelist(pnl, wr):
            keep_wl.append((sym, pnl, wr, reason[:50]))
            continue
        fixes.append({
            "symbol": sym,
            "pnl": pnl,
            "wr": wr,
            "trades": st["total_trades"],
            "new_level": 1,
            "reason": (
                f"白名单纠偏→L1(盈利{pnl:.0f}U/胜率{wr:.1f}%, "
                f"需>200U或>55%胜率; 原:{reason[:40]})"
            ),
        })

    print(f"白名单 L0 总数: {len(rows)}")
    print(f"  符合门槛保留: {len(keep_wl)}")
    print(f"  改回 L1: {len(fixes)}")
    print(f"  跳过(手动): {len(skip_manual)}")
    print(f"  无足够平仓统计(跳过): {len(no_stats)}")
    print()

    if fixes:
        print(f"{'交易对':<18} {'盈亏':>10} {'胜率':>7}")
        print("-" * 40)
        for d in fixes[:40]:
            print(f"{d['symbol']:<18} {d['pnl']:>+10.1f} {d['wr']:>6.1f}%")
        if len(fixes) > 40:
            print(f"... 另有 {len(fixes) - 40} 个")

    if not fixes:
        print("无需修正无效白名单。")
        return 0

    if not args.apply:
        print()
        print("预览模式。执行: python scripts/downgrade_invalid_level3.py --fix-all-invalid-l0 --apply")
        return 0

    assert opt is not None
    for d in fixes:
        _apply_rating(opt, stats_map, d)
    print()
    print(f"已将 {len(fixes)} 个无效白名单改回 L1。")
    return 0


def _apply_rating(opt: OptimizationConfig, stats_map: dict, d: dict) -> None:
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
    print(f"  OK {d['symbol']} -> L{d['new_level']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="L3 纠偏与白名单误标修正")
    parser.add_argument("--apply", action="store_true", help="写入数据库")
    parser.add_argument(
        "--include-no-stats",
        action="store_true",
        help="对无足够平仓统计的 L3 也降级为 L1",
    )
    parser.add_argument(
        "--fix-mis-whitelist",
        action="store_true",
        help="将纠偏误标为 L0、但不满足白名单门槛的改回 L1",
    )
    parser.add_argument(
        "--fix-all-invalid-l0",
        action="store_true",
        help="将全部不满足白名单门槛的 L0 改回 L1（跳过手动设置）",
    )
    parser.add_argument("--account-id", type=int, default=ACCOUNT_ID)
    args = parser.parse_args()

    conn = pymysql.connect(**get_db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        stats_map = fetch_stats_map(conn, args.account_id)
    finally:
        conn.close()

    opt = OptimizationConfig(get_db_config()) if args.apply else None

    if args.fix_mis_whitelist:
        return run_fix_mis_whitelist(args, stats_map, opt)
    if args.fix_all_invalid_l0:
        return run_fix_all_invalid_l0(args, stats_map, opt)
    return run_l3_downgrade(args, stats_map, opt)


if __name__ == "__main__":
    raise SystemExit(main())
