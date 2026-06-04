#!/usr/bin/env python3
"""All-strategy win rate for rolling last N hours (UTC, close_time)."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pymysql
from dotenv import dotenv_values

from app.services.strategy_display_names import get_strategy_display_name
from app.utils.config_loader import get_db_config

PAPER_ACCOUNT_ID = 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--compare-hours", type=int, default=0, help="Prior window same length")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now - timedelta(hours=args.hours)

    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

    def fetch(since: datetime, until: datetime | None = None) -> list[dict]:
        sql = """
            SELECT
                COALESCE(NULLIF(TRIM(source), ''), '(empty)') AS source,
                COUNT(*) AS n,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses,
                ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS pnl,
                ROUND(AVG(realized_pnl), 2) AS avg_pnl
            FROM futures_positions
            WHERE account_id = %s
              AND status = 'closed'
              AND close_time IS NOT NULL
              AND close_time >= %s
        """
        params: list = [PAPER_ACCOUNT_ID, since]
        if until is not None:
            sql += " AND close_time < %s"
            params.append(until)
        sql += " GROUP BY source ORDER BY n DESC, source"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall())

    rows = fetch(start)
    print(f"=== 模拟仓 account_id={PAPER_ACCOUNT_ID} | 近 {args.hours}h (UTC) ===")
    print(f"窗口: {start.isoformat()} ~ {now.isoformat()}")
    print()

    if not rows:
        print("无已平仓记录")
    else:
        total_n = total_w = 0
        total_pnl = 0.0
        hdr = f"{'策略 source':<28} {'显示名':<16} {'笔数':>5} {'胜':>4} {'负':>4} {'胜率%':>7} {'总盈亏':>10} {'均盈亏':>8}"
        print(hdr)
        print("-" * len(hdr))
        for r in rows:
            n = int(r["n"])
            wins = int(r["wins"])
            wr = round(100.0 * wins / n, 1) if n else 0.0
            src = r["source"]
            disp = get_strategy_display_name(src) or src
            print(
                f"{src:<28} {disp[:16]:<16} {n:>5} {wins:>4} {int(r['losses']):>4} "
                f"{wr:>6.1f}% {float(r['pnl']):>10.2f} {float(r['avg_pnl'] or 0):>8.2f}"
            )
            total_n += n
            total_w += wins
            total_pnl += float(r["pnl"])
        twr = round(100.0 * total_w / total_n, 1) if total_n else 0.0
        print("-" * len(hdr))
        print(
            f"{'合计':<28} {'':16} {total_n:>5} {total_w:>4} {total_n - total_w:>4} "
            f"{twr:>6.1f}% {total_pnl:>10.2f} {total_pnl / total_n if total_n else 0:>8.2f}"
        )

    if args.compare_hours > 0:
        h = args.compare_hours
        prev_end = start
        prev_start = prev_end - timedelta(hours=h)
        prev = fetch(prev_start, prev_end)
        print()
        print(f"=== 对比窗口: 前 {h}h ===")
        print(f"窗口: {prev_start.isoformat()} ~ {prev_end.isoformat()}")
        if not prev:
            print("无已平仓记录")
        else:
            for r in prev:
                n = int(r["n"])
                wins = int(r["wins"])
                wr = round(100.0 * wins / n, 1) if n else 0.0
                src = r["source"]
                disp = get_strategy_display_name(src) or src
                print(
                    f"  {src:<26} {disp[:14]:<14} n={n:>3} wr={wr:>5.1f}% pnl={float(r['pnl']):>8.2f}"
                )

    # AI teachers breakdown
    print()
    print("=== AI 三教师分组 (近 24h) ===")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                CASE
                    WHEN source LIKE 'gemini%%' THEN 'Gemini系'
                    WHEN source LIKE 'deepseek%%' THEN 'DeepSeek系'
                    WHEN source LIKE 'gpt%%' THEN 'GPT系'
                    WHEN source LIKE 's9%%' OR source = 's9_gemini_ai' THEN 'S9'
                    WHEN source LIKE 's1%%' THEN 'S1'
                    WHEN source LIKE 's6%%' THEN 'S6'
                    WHEN source = 'smart_trader' THEN 'smart_trader'
                    ELSE '其他'
                END AS grp,
                COUNT(*) AS n,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS pnl
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed'
              AND close_time >= %s
            GROUP BY grp ORDER BY n DESC
            """,
            (PAPER_ACCOUNT_ID, start),
        )
        for r in cur.fetchall():
            n = int(r["n"])
            w = int(r["wins"])
            wr = round(100.0 * w / n, 1) if n else 0
            print(f"  {r['grp']:<14} n={n:>4} wr={wr:>5.1f}% pnl={float(r['pnl']):>9.2f}")

    conn.close()
    return 0


def compare_en_prompt_cutoff(cutoff_iso: str = "2026-06-03T20:19:00") -> None:
    """Before/after English prompt deploy (commit 4be7c501)."""
    cutoff = datetime.fromisoformat(cutoff_iso)
    before_start = cutoff - timedelta(hours=24)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    main_src = [
        "gemini_explore", "gemini_predict", "deepseek_explore",
        "deepseek_predict", "gpt_explore", "gpt_predict",
    ]
    tactical = [
        "gemini_pullback", "gemini_rebound", "gemini_chase", "gemini_dump",
        "deepseek_pullback", "deepseek_rebound", "deepseek_chase", "deepseek_dump",
        "gpt_pullback", "gpt_rebound", "gpt_chase", "gpt_dump",
    ]
    reversal = ["gemini_reversal", "deepseek_reversal", "gpt_reversal"]

    conn = pymysql.connect(
        **get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )

    def q(since: datetime, until: datetime, srcs: list[str]) -> list[dict]:
        ph = ",".join(["%s"] * len(srcs))
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT source, COUNT(*) AS n,
                  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                  ROUND(COALESCE(SUM(realized_pnl), 0), 2) AS pnl
                FROM futures_positions
                WHERE account_id = %s AND status = 'closed'
                  AND close_time >= %s AND close_time < %s
                  AND source IN ({ph})
                GROUP BY source ORDER BY n DESC
                """,
                [PAPER_ACCOUNT_ID, since, until, *srcs],
            )
            return list(cur.fetchall())

    def summarize(rows: list[dict]) -> tuple[int, float, float]:
        n = sum(int(r["n"]) for r in rows)
        w = sum(int(r["wins"]) for r in rows)
        pnl = sum(float(r["pnl"]) for r in rows)
        wr = round(100.0 * w / n, 1) if n else 0.0
        return n, wr, round(pnl, 2)

    windows = [
        ("英文前 24h", before_start, cutoff),
        ("英文后 ~24h", cutoff, now),
    ]
    groups = [
        ("主探索/预测", main_src),
        ("战术四策略", tactical),
        ("顶空底多", reversal),
    ]

    print(f"\n=== 英文 prompt 切换对比 (切点 UTC {cutoff_iso}) ===")
    for wlabel, ws, we in windows:
        print(f"\n--- {wlabel}: {ws.isoformat()} ~ {we.isoformat()} ---")
        for glabel, srcs in groups:
            rows = q(ws, we, srcs)
            n, wr, pnl = summarize(rows)
            print(f"  {glabel}: n={n} wr={wr}% pnl={pnl:+.2f}")
            for r in rows:
                rn = int(r["n"])
                if rn >= 2:
                    rw = int(r["wins"])
                    rwr = round(100.0 * rw / rn, 1)
                    print(f"    {r['source']:<22} n={rn:>3} wr={rwr:>5.1f}% pnl={float(r['pnl']):>+8.2f}")

    conn.close()


if __name__ == "__main__":
    import os
    if "--en-compare" in sys.argv:
        compare_en_prompt_cutoff()
        raise SystemExit(0)
    raise SystemExit(main())
