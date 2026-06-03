#!/usr/bin/env python3
"""扫描模拟仓表现，将盈利>阈值或胜率>阈值且不在白名单的交易对加入白名单.

口径: futures_positions account_id=2, status='closed', 按 symbol 聚合 (与 TOP50 统计一致).
白名单: trading_symbol_rating.rating_level = 0 (无记录视为未显式入白名单，需写入).

用法:
  python scripts/promote_whitelist_by_performance.py
  python scripts/promote_whitelist_by_performance.py --apply
  python scripts/promote_whitelist_by_performance.py --min-pnl 500 --min-win-rate 52 --apply
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
from update_top_performers import qualifies_whitelist

PAPER_ACCOUNT_ID = 2
DEFAULT_MIN_PNL = 200.0
DEFAULT_MIN_WIN_RATE = 55.0
DEFAULT_MIN_TRADES = 5


def _normalize_symbol_key(symbol: str) -> str:
    return futures_symbol_clean(symbol)


def _reason_for(row: dict) -> str:
    parts = []
    pnl, wr = row["net_pnl"], row["win_rate"]
    if pnl > DEFAULT_MIN_PNL:
        parts.append(f"累计盈利{pnl:.0f}U")
    if wr > DEFAULT_MIN_WIN_RATE:
        parts.append(f"胜率{wr:.1f}%")
    return "脚本自动加入白名单: " + ", ".join(parts)


def fetch_symbol_performance(conn, account_id: int, min_trades: int) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                symbol,
                COUNT(*) AS total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) AS losses,
                COALESCE(SUM(realized_pnl), 0) AS net_pnl,
                COALESCE(SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END), 0) AS gross_profit,
                COALESCE(SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END), 0) AS gross_loss,
                CASE
                    WHEN COUNT(*) > 0
                    THEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*)
                    ELSE 0
                END AS win_rate
            FROM futures_positions
            WHERE account_id = %s
              AND status = 'closed'
              AND realized_pnl IS NOT NULL
            GROUP BY symbol
            HAVING total_trades >= %s
            ORDER BY net_pnl DESC
            """,
            (account_id, min_trades),
        )
        rows = []
        for r in cur.fetchall():
            rows.append(
                {
                    "symbol": r["symbol"],
                    "total_trades": int(r["total_trades"] or 0),
                    "wins": int(r["wins"] or 0),
                    "losses": int(r["losses"] or 0),
                    "net_pnl": round(float(r["net_pnl"] or 0), 2),
                    "gross_profit": round(float(r["gross_profit"] or 0), 2),
                    "gross_loss": round(float(r["gross_loss"] or 0), 2),
                    "win_rate": round(float(r["win_rate"] or 0), 2),
                }
            )
        return rows


def fetch_rating_map(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, rating_level FROM trading_symbol_rating"
        )
        out: dict[str, dict] = {}
        for r in cur.fetchall():
            sym = (r.get("symbol") or "").strip()
            if not sym:
                continue
            key = _normalize_symbol_key(sym)
            level = int(r.get("rating_level") or 0)
            prev = out.get(key)
            if prev is None or level > prev["rating_level"]:
                out[key] = {
                    "symbol": sym,
                    "rating_level": level,
                }
        return out


def is_whitelisted(key: str, rating_map: dict[str, dict]) -> bool:
    row = rating_map.get(key)
    return row is not None and row["rating_level"] == 0


def pick_candidates(
    perf_rows: list[dict],
    rating_map: dict[str, dict],
    min_pnl: float,
    min_win_rate: float,
    skip_level3: bool,
) -> list[dict]:
    candidates = []
    for row in perf_rows:
        if not qualifies_whitelist(row["net_pnl"], row["win_rate"]):
            continue
        key = _normalize_symbol_key(row["symbol"])
        if is_whitelisted(key, rating_map):
            continue
        rating = rating_map.get(key)
        if skip_level3 and rating and rating["rating_level"] >= 3:
            continue
        row = dict(row)
        row["current_level"] = rating["rating_level"] if rating else None
        row["reason"] = _reason_for(row)
        candidates.append(row)
    return candidates


def print_candidates(candidates: list[dict]) -> None:
    if not candidates:
        print("无符合条件且待加入白名单的交易对。")
        return
    header = f"{'交易对':<16} {'原等级':>6} {'平仓':>5} {'胜率%':>7} {'累计盈亏':>10} {'触发条件'}"
    print(header)
    print("-" * len(header))
    for r in candidates:
        lvl = r["current_level"]
        lvl_s = str(lvl) if lvl is not None else "无"
        print(
            f"{r['symbol']:<16} "
            f"{lvl_s:>6} "
            f"{r['total_trades']:>5} "
            f"{r['win_rate']:>7.1f} "
            f"{r['net_pnl']:>10.2f} "
            f"{r['reason']}"
        )


def apply_whitelist(opt: OptimizationConfig, candidates: list[dict]) -> None:
    for r in candidates:
        opt.update_symbol_rating(
            symbol=futures_symbol_rating_canonical(r["symbol"]),
            new_level=0,
            reason=r["reason"],
            total_loss_amount=float(r["gross_loss"]),
            total_profit_amount=float(r["gross_profit"]),
            win_rate=float(r["win_rate"]) / 100.0,
            total_trades=int(r["total_trades"]),
        )
        print(f"  OK {r['symbol']} -> level 0 (pnl={r['net_pnl']:+.2f} U, wr={r['win_rate']:.1f}%)")


def main() -> int:
    parser = argparse.ArgumentParser(description="按盈利/胜率自动加入白名单")
    parser.add_argument("--apply", action="store_true", help="写入数据库 (默认预览)")
    parser.add_argument("--min-pnl", type=float, default=DEFAULT_MIN_PNL, help="累计盈利阈值 U (默认 200)")
    parser.add_argument(
        "--min-win-rate",
        type=float,
        default=DEFAULT_MIN_WIN_RATE,
        help="胜率阈值 %% (默认 55，满足盈利或胜率其一即可)",
    )
    parser.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES, help="至少 N 笔平仓 (默认 5)")
    parser.add_argument("--account-id", type=int, default=PAPER_ACCOUNT_ID)
    parser.add_argument(
        "--include-level3",
        action="store_true",
        help="允许从永久禁止(3级)恢复为白名单 (默认跳过)",
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
        perf_rows = fetch_symbol_performance(conn, args.account_id, args.min_trades)
        rating_map = fetch_rating_map(conn)
    finally:
        conn.close()

    wl_count = sum(1 for v in rating_map.values() if v["rating_level"] == 0)
    candidates = pick_candidates(
        perf_rows,
        rating_map,
        args.min_pnl,
        args.min_win_rate,
        skip_level3=not args.include_level3,
    )

    print(
        f"扫描 {len(perf_rows)} 个有成交交易对 (平仓>={args.min_trades}) · "
        f"当前白名单 {wl_count} 个 · "
        f"条件: 盈利>{args.min_pnl}U 或 胜率>{args.min_win_rate}%"
    )
    print()
    print_candidates(candidates)

    if not candidates:
        return 0

    if not args.apply:
        print()
        print("预览模式，未修改数据库。执行请加: --apply")
        return 0

    print()
    print(f"正在写入: {len(candidates)} 个 -> rating_level=0")
    opt = OptimizationConfig(get_db_config())
    apply_whitelist(opt, candidates)
    print("完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
