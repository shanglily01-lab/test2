#!/usr/bin/env python3
"""将模拟仓累计盈亏为负的白名单交易对移出白名单 (默认降为黑名单 1 级).

口径与 TOP50 白名单统计一致:
  - 白名单: trading_symbol_rating.rating_level = 0
  - 盈亏: futures_positions account_id=2, status='closed', SUM(realized_pnl)

用法:
  python scripts/remove_negative_pnl_whitelist.py              # 仅预览
  python scripts/remove_negative_pnl_whitelist.py --apply      # 执行降级
  python scripts/remove_negative_pnl_whitelist.py --min-trades 3 --apply
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
from app.utils.pnl_stats import parse_pnl_counts

PAPER_ACCOUNT_ID = 2
DEFAULT_TARGET_LEVEL = 1
REASON = "脚本自动移除白名单: 模拟仓累计盈亏为负"


def _normalize_symbol_key(symbol: str) -> str:
    return futures_symbol_clean(symbol)


def fetch_whitelist_pnl_rows(conn, account_id: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT symbol, level_change_reason
            FROM trading_symbol_rating
            WHERE rating_level = 0
            ORDER BY symbol
            """
        )
        wl_rows = cur.fetchall()

        wl_meta: dict[str, dict] = {}
        for r in wl_rows:
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            wl_meta[_normalize_symbol_key(sym)] = {
                "symbol": sym,
                "reason": (r.get("level_change_reason") or "").strip(),
            }

        per_symbol = {
            key: {
                "wins": 0,
                "losses": 0,
                "breakeven": 0,
                "net_pnl": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
            }
            for key in wl_meta
        }

        if wl_meta:
            cur.execute(
                """
                SELECT symbol, realized_pnl
                FROM futures_positions
                WHERE account_id = %s
                  AND status = 'closed'
                  AND realized_pnl IS NOT NULL
                """,
                (account_id,),
            )
            for fp in cur.fetchall():
                key = _normalize_symbol_key(fp.get("symbol") or "")
                if key not in per_symbol:
                    continue
                agg = per_symbol[key]
                pnl = float(fp.get("realized_pnl") or 0)
                agg["net_pnl"] += pnl
                if pnl > 0:
                    agg["wins"] += 1
                    agg["gross_profit"] += pnl
                elif pnl < 0:
                    agg["losses"] += 1
                    agg["gross_loss"] += abs(pnl)
                else:
                    agg["breakeven"] += 1

    rows: list[dict] = []
    for key, meta in wl_meta.items():
        agg = per_symbol[key]
        total_trades = agg["wins"] + agg["losses"] + agg["breakeven"]
        counts = parse_pnl_counts(
            {
                "total_trades": total_trades,
                "wins": agg["wins"],
                "losses": agg["losses"],
                "breakeven": agg["breakeven"],
            }
        )
        rows.append(
            {
                "symbol": meta["symbol"],
                "whitelist_reason": meta["reason"],
                "total_trades": total_trades,
                "wins": counts["wins"],
                "losses": counts["losses"],
                "win_rate": counts["win_rate"],
                "net_pnl": round(agg["net_pnl"], 2),
                "gross_profit": round(agg["gross_profit"], 2),
                "gross_loss": round(agg["gross_loss"], 2),
            }
        )
    rows.sort(key=lambda x: (x["net_pnl"], x["symbol"]))
    return rows


def print_candidates(candidates: list[dict]) -> None:
    if not candidates:
        print("无累计盈亏为负的白名单交易对。")
        return

    header = f"{'交易对':<16} {'平仓':>5} {'胜率%':>7} {'累计盈亏':>10} {'原备注'}"
    print(header)
    print("-" * len(header))
    for r in candidates:
        reason = (r["whitelist_reason"] or "")[:40]
        if len(r["whitelist_reason"] or "") > 40:
            reason += "…"
        print(
            f"{r['symbol']:<16} "
            f"{r['total_trades']:>5} "
            f"{r['win_rate']:>7.1f} "
            f"{r['net_pnl']:>10.2f} "
            f"{reason or '-'}"
        )


def apply_downgrades(
    opt: OptimizationConfig,
    candidates: list[dict],
    target_level: int,
) -> None:
    for r in candidates:
        opt.update_symbol_rating(
            symbol=futures_symbol_rating_canonical(r["symbol"]),
            new_level=target_level,
            reason=REASON,
            total_loss_amount=float(r["gross_loss"]),
            total_profit_amount=float(r["gross_profit"]),
            win_rate=float(r["win_rate"]) / 100.0,
            total_trades=int(r["total_trades"]),
        )
        print(f"  OK {r['symbol']} -> level {target_level} (pnl={r['net_pnl']:+.2f} U)")


def main() -> int:
    parser = argparse.ArgumentParser(description="移出累计盈亏为负的白名单交易对")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="写入数据库 (默认仅预览)",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=1,
        help="至少 N 笔已平仓才处理 (默认 1)",
    )
    parser.add_argument(
        "--target-level",
        type=int,
        default=DEFAULT_TARGET_LEVEL,
        choices=(1, 2, 3),
        help="移出后设为的黑名单等级 (默认 1)",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        default=PAPER_ACCOUNT_ID,
        help=f"统计盈亏的模拟仓 account_id (默认 {PAPER_ACCOUNT_ID})",
    )
    args = parser.parse_args()

    if args.min_trades < 1:
        print("--min-trades 须 >= 1", file=sys.stderr)
        return 2

    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        rows = fetch_whitelist_pnl_rows(conn, args.account_id)
    finally:
        conn.close()

    candidates = [
        r
        for r in rows
        if r["net_pnl"] < 0 and r["total_trades"] >= args.min_trades
    ]

    print(
        f"白名单共 {len(rows)} 个 · 累计盈亏为负且平仓≥{args.min_trades} 笔: {len(candidates)} 个"
    )
    print()
    print_candidates(candidates)

    if not candidates:
        return 0

    if not args.apply:
        print()
        print(f"预览模式，未修改数据库。执行请加: --apply (将降为 level {args.target_level})")
        return 0

    print()
    print(f"正在写入: {len(candidates)} 个 → rating_level={args.target_level}")
    opt = OptimizationConfig(get_db_config())
    apply_downgrades(opt, candidates, args.target_level)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
