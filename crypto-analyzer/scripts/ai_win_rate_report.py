#!/usr/bin/env python3
"""AI 策略按日胜率报表 — 每日巡检用.

North Star: 长期各 source 胜率 >50%; 日检红线: 当日胜率 <40% 且样本足够 → 必须优化.
默认看近 7 天, 部署/改 prompt 后连续看一周即可判断效果.

用法:
  python scripts/ai_win_rate_report.py              # 近 7 天 (UTC 按 close_time 日切)
  python scripts/ai_win_rate_report.py --days 7
  python scripts/ai_win_rate_report.py --date 2026-05-31   # 仅单日
  python scripts/ai_win_rate_report.py --min-trades 5      # 至少 5 笔 closed 才判红/绿
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = v

import pymysql

from app.services.strategy_display_names import get_strategy_display_name
from app.utils.config_loader import get_db_config

PAPER_ACCOUNT_ID = 2
WIN_RATE_TARGET_PCT = 50.0
WIN_RATE_DAILY_FLOOR_PCT = 40.0

AI_SOURCE_SQL = (
    "source REGEXP '^(gemini|deepseek|s9_gemini)' "
    "OR source IN ('gemini_explore','gemini_predict','deepseek_explore',"
    "'deepseek_predict','gemini_reversal','deepseek_reversal','s9_gemini_ai')"
)


def _connect():
    return pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def fetch_daily_rows(conn, start_d: date, end_d: date) -> list[dict]:
    """按 UTC 日 (close_time) 聚合 account_id=2 已平仓 AI 单."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                DATE(close_time) AS trade_day,
                source,
                COUNT(*) AS n,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses,
                ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS pnl
            FROM futures_positions
            WHERE account_id = %s
              AND status = 'closed'
              AND close_time IS NOT NULL
              AND DATE(close_time) >= %s
              AND DATE(close_time) <= %s
              AND ({AI_SOURCE_SQL})
            GROUP BY DATE(close_time), source
            ORDER BY trade_day DESC, source
            """,
            (PAPER_ACCOUNT_ID, start_d.isoformat(), end_d.isoformat()),
        )
        return list(cur.fetchall())


def _win_rate_pct(wins: int, n: int) -> float | None:
    if n <= 0:
        return None
    return round(100.0 * wins / n, 1)


def _status(n: int, win_rate: float | None, min_trades: int) -> str:
    if n < min_trades:
        return "样本不足"
    if win_rate is None:
        return "样本不足"
    if win_rate < WIN_RATE_DAILY_FLOOR_PCT:
        return "必须优化"
    if win_rate >= WIN_RATE_TARGET_PCT:
        return "达标"
    return "观察"


def print_report(rows: list[dict], min_trades: int) -> int:
    """打印表格; 返回必须优化的 (source, day) 条数."""
    if not rows:
        print("无已平仓 AI 成交 (检查 account_id=2 / close_time / source 过滤).")
        return 0

    col_day = 12
    col_src = 22
    col_n = 5
    col_wr = 8
    col_pnl = 10
    col_st = 10

    header = (
        f"{'日期':<{col_day}} "
        f"{'策略':<{col_src}} "
        f"{'笔数':>{col_n}} "
        f"{'胜率%':>{col_wr}} "
        f"{'盈亏':>{col_pnl}} "
        f"{'状态':<{col_st}}"
    )
    print(header)
    print("-" * len(header))

    action_count = 0
    action_items: list[tuple] = []

    for r in rows:
        day = r["trade_day"]
        if hasattr(day, "isoformat"):
            day_s = day.isoformat()
        else:
            day_s = str(day)[:10]
        src = r["source"] or "?"
        name = get_strategy_display_name(src)
        if len(name) > col_src:
            name = name[: col_src - 1] + "…"
        n = int(r["n"] or 0)
        wins = int(r["wins"] or 0)
        wr = _win_rate_pct(wins, n)
        pnl = float(r["pnl"] or 0)
        st = _status(n, wr, min_trades)
        wr_s = f"{wr:.1f}" if wr is not None else "-"
        print(
            f"{day_s:<{col_day}} "
            f"{name:<{col_src}} "
            f"{n:>{col_n}} "
            f"{wr_s:>{col_wr}} "
            f"{pnl:>{col_pnl}.2f} "
            f"{st:<{col_st}}"
        )
        if st == "必须优化":
            action_count += 1
            action_items.append((day_s, src, name, n, wr, pnl))

    print()
    print(
        f"日检红线: 胜率 < {WIN_RATE_DAILY_FLOOR_PCT:.0f}% 且 笔数 ≥ {min_trades} → 必须优化; "
        f"长期目标 ≥ {WIN_RATE_TARGET_PCT:.0f}%"
    )

    if action_items:
        print()
        print(f"=== 必须优化 ({len(action_items)} 条) ===")
        for day_s, src, name, n, wr, pnl in action_items:
            print(f"  {day_s}  {name} ({src})  n={n}  win={wr:.1f}%  pnl={pnl:.2f}")
        print("  → 收紧 conf / catalyst / 开仓顾问 rubric; 改完连续看 7 天日胜率")
    else:
        print("本报表区间内无「必须优化」项 (或均为样本不足).")

    # 按 source 汇总近区间
    by_src: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_src[r["source"]].append(r)

    print()
    print("=== 区间汇总 (按策略) ===")
    for src in sorted(by_src.keys()):
        parts = by_src[src]
        total_n = sum(int(p["n"] or 0) for p in parts)
        total_wins = sum(int(p["wins"] or 0) for p in parts)
        total_pnl = sum(float(p["pnl"] or 0) for p in parts)
        wr = _win_rate_pct(total_wins, total_n)
        low_days = 0
        for p in parts:
            n = int(p["n"] or 0)
            w = int(p["wins"] or 0)
            rate = _win_rate_pct(w, n)
            if n >= min_trades and rate is not None and rate < WIN_RATE_DAILY_FLOOR_PCT:
                low_days += 1
        name = get_strategy_display_name(src)
        wr_s = f"{wr:.1f}%" if wr is not None else "-"
        flag = f"  ! {low_days} 天低于{WIN_RATE_DAILY_FLOOR_PCT:.0f}%" if low_days else ""
        print(f"  {name:<28} n={total_n:>4}  win={wr_s:>6}  pnl={total_pnl:>9.2f}{flag}")

    return action_count


def main() -> int:
    parser = argparse.ArgumentParser(description="AI 策略按日胜率 (模拟仓 account_id=2)")
    parser.add_argument("--days", type=int, default=7, help="回溯天数 (含今天 UTC)")
    parser.add_argument("--date", type=str, default="", help="仅查单日 YYYY-MM-DD (UTC)")
    parser.add_argument(
        "--min-trades",
        type=int,
        default=3,
        help="当日至少几笔 closed 才判定红/绿 (默认 3)",
    )
    args = parser.parse_args()

    if args.date:
        try:
            d = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("无效 --date, 需要 YYYY-MM-DD", file=sys.stderr)
            return 2
        start_d = end_d = d
        title = f"AI 日胜率 — {d.isoformat()} (UTC, min_trades={args.min_trades})"
    else:
        end_d = datetime.utcnow().date()
        start_d = end_d - timedelta(days=max(0, args.days - 1))
        title = (
            f"AI 日胜率 — {start_d} ~ {end_d} (UTC, {args.days} 天, "
            f"min_trades={args.min_trades})"
        )

    print(title)
    print()

    try:
        conn = _connect()
    except Exception as e:
        print(f"数据库连接失败: {e}", file=sys.stderr)
        return 2

    try:
        rows = fetch_daily_rows(conn, start_d, end_d)
    finally:
        conn.close()

    action_count = print_report(rows, args.min_trades)
    return 1 if action_count > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
