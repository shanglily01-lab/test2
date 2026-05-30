#!/usr/bin/env python3
"""诊断战术探索策略：runs / verdict 跳过原因 / 持仓."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config

TACTICAL = [
    ("gemini_pullback", "gemini_pullback_explore_runs", "gemini_pullback_explore_verdicts"),
    ("gemini_rebound", "gemini_rebound_explore_runs", "gemini_rebound_explore_verdicts"),
    ("gemini_chase", "gemini_chase_explore_runs", "gemini_chase_explore_verdicts"),
    ("gemini_dump", "gemini_dump_explore_runs", "gemini_dump_explore_verdicts"),
    ("deepseek_pullback", "deepseek_pullback_explore_runs", "deepseek_pullback_explore_verdicts"),
    ("deepseek_rebound", "deepseek_rebound_explore_runs", "deepseek_rebound_explore_verdicts"),
    ("deepseek_chase", "deepseek_chase_explore_runs", "deepseek_chase_explore_verdicts"),
    ("deepseek_dump", "deepseek_dump_explore_runs", "deepseek_dump_explore_verdicts"),
    ("gemini_reversal", "gemini_reversal_explore_runs", "gemini_reversal_explore_verdicts"),
    ("deepseek_reversal", "deepseek_reversal_explore_runs", "deepseek_reversal_explore_verdicts"),
]


def main() -> None:
    cfg = get_db_config()
    conn = pymysql.connect(
        **cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor
    )
    cur = conn.cursor()

    print("=== runs (30d) ===")
    for label, rt, _vt in TACTICAL:
        try:
            cur.execute(
                f"""
                SELECT COUNT(*) c, SUM(COALESCE(trades_opened,0)) opened,
                       SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) ok_runs,
                       MAX(asof_utc) last_run
                FROM {rt}
                WHERE asof_utc >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                """
            )
            r = cur.fetchone()
            print(
                f"{label:22} runs={r['c']:3} ok={r['ok_runs']:3} "
                f"opened={int(r['opened'] or 0):3} last={r['last_run']}"
            )
        except Exception as e:
            print(f"{label:22} ERROR: {e}")

    print("\n=== verdict action_taken (30d) ===")
    for label, rt, vt in TACTICAL:
        try:
            cur.execute(
                f"""
                SELECT v.action_taken, COUNT(*) cnt
                FROM {vt} v
                JOIN {rt} r ON r.id = v.run_id
                WHERE r.asof_utc >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                GROUP BY v.action_taken
                ORDER BY cnt DESC
                LIMIT 10
                """
            )
            rows = cur.fetchall()
            if not rows:
                print(f"{label:22} (no verdicts)")
                continue
            parts = ", ".join(f"{x['action_taken']}:{x['cnt']}" for x in rows)
            print(f"{label:22} {parts}")
        except Exception as e:
            print(f"{label:22} ERROR: {e}")

    print("\n=== top skip_reason (entry verdicts only, 7d) ===")
    for label, rt, vt in TACTICAL:
        try:
            cur.execute(
                f"""
                SELECT v.skip_reason, COUNT(*) cnt
                FROM {vt} v
                JOIN {rt} r ON r.id = v.run_id
                WHERE r.asof_utc >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                  AND v.category = 'entry'
                  AND v.action_taken != 'opened'
                GROUP BY v.skip_reason
                ORDER BY cnt DESC
                LIMIT 5
                """
            )
            rows = cur.fetchall()
            if rows:
                parts = "; ".join(
                    f"{(x['skip_reason'] or '')[:60]}:{x['cnt']}" for x in rows
                )
                print(f"{label:22} {parts}")
        except Exception as e:
            print(f"{label:22} ERROR: {e}")

    print("\n=== futures_positions by source ===")
    for src, _, _ in TACTICAL:
        cur.execute(
            """
            SELECT status, COUNT(*) c FROM futures_positions
            WHERE source=%s AND account_id=2 GROUP BY status
            """,
            (src,),
        )
        rows = cur.fetchall()
        s = ", ".join(f"{r['status']}:{r['c']}" for r in rows) or "none"
        print(f"{src:22} {s}")

    conn.close()


if __name__ == "__main__":
    main()
