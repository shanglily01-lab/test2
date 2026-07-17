"""Read-only report for today's paper win rate and same-side dedup impact."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config


def q(cur, title: str, sql: str, params: tuple = ()) -> None:
    print(f"\n## {title}")
    cur.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        print("(none)")
        return
    for row in rows:
        print(row)


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    with conn.cursor() as cur:
        q(cur, "clock", "SELECT NOW() AS db_now, UTC_TIMESTAMP() AS utc_now, @@session.time_zone AS tz")

        today_where = "close_time >= CURDATE() AND close_time < CURDATE() + INTERVAL 1 DAY"
        open_today_where = "open_time >= CURDATE() AND open_time < CURDATE() + INTERVAL 1 DAY"

        q(
            cur,
            "today closed overall account=2",
            f"""
            SELECT
              COUNT(*) AS closed,
              SUM(realized_pnl > 0) AS wins,
              SUM(realized_pnl <= 0) AS losses,
              ROUND(SUM(realized_pnl > 0) / NULLIF(COUNT(*), 0) * 100, 1) AS win_rate_pct,
              ROUND(SUM(realized_pnl), 2) AS pnl,
              ROUND(AVG(realized_pnl), 2) AS avg_pnl,
              ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 1) AS avg_hold_min
            FROM futures_positions
            WHERE account_id=2 AND status='closed' AND realized_pnl IS NOT NULL
              AND {today_where}
            """,
        )

        q(
            cur,
            "today closed by source",
            f"""
            SELECT
              source,
              COUNT(*) AS closed,
              SUM(realized_pnl > 0) AS wins,
              ROUND(SUM(realized_pnl > 0) / NULLIF(COUNT(*), 0) * 100, 1) AS win_rate_pct,
              ROUND(SUM(realized_pnl), 2) AS pnl,
              ROUND(AVG(realized_pnl), 2) AS avg_pnl,
              ROUND(AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)), 1) AS avg_hold_min
            FROM futures_positions
            WHERE account_id=2 AND status='closed' AND realized_pnl IS NOT NULL
              AND {today_where}
            GROUP BY source
            ORDER BY pnl ASC
            """,
        )

        q(
            cur,
            "today closed by source and side",
            f"""
            SELECT
              source, position_side,
              COUNT(*) AS closed,
              SUM(realized_pnl > 0) AS wins,
              ROUND(SUM(realized_pnl > 0) / NULLIF(COUNT(*), 0) * 100, 1) AS win_rate_pct,
              ROUND(SUM(realized_pnl), 2) AS pnl,
              ROUND(AVG(realized_pnl), 2) AS avg_pnl
            FROM futures_positions
            WHERE account_id=2 AND status='closed' AND realized_pnl IS NOT NULL
              AND {today_where}
            GROUP BY source, position_side
            ORDER BY pnl ASC
            """,
        )

        q(
            cur,
            "today worst trades",
            f"""
            SELECT
              id, symbol, source, position_side, open_time, close_time,
              ROUND(realized_pnl, 2) AS pnl,
              COALESCE(NULLIF(notes, ''), '(empty)') AS notes
            FROM futures_positions
            WHERE account_id=2 AND status='closed' AND realized_pnl IS NOT NULL
              AND {today_where}
            ORDER BY realized_pnl ASC
            LIMIT 20
            """,
        )

        q(
            cur,
            "today opened/open positions account=2",
            f"""
            SELECT
              status, source, position_side,
              COUNT(*) AS cnt,
              ROUND(SUM(COALESCE(realized_pnl, 0)), 2) AS realized_pnl,
              ROUND(SUM(COALESCE(unrealized_pnl, 0)), 2) AS unrealized_pnl
            FROM futures_positions
            WHERE account_id=2 AND {open_today_where}
            GROUP BY status, source, position_side
            ORDER BY status, source, position_side
            """,
        )

        q(
            cur,
            "today same-side duplicate clusters opened account=2",
            f"""
            SELECT
              symbol, position_side,
              COUNT(*) AS opened_today,
              SUM(status='open') AS still_open,
              ROUND(SUM(COALESCE(realized_pnl, 0)), 2) AS realized_pnl,
              ROUND(SUM(COALESCE(unrealized_pnl, 0)), 2) AS unrealized_pnl,
              GROUP_CONCAT(DISTINCT source ORDER BY source SEPARATOR ',') AS sources
            FROM futures_positions
            WHERE account_id=2 AND {open_today_where}
            GROUP BY symbol, position_side
            HAVING opened_today >= 2
            ORDER BY opened_today DESC, realized_pnl ASC
            LIMIT 50
            """,
        )

        for table in (
            "deepseek_explore_verdicts",
            "deepseek_predict_verdicts",
            "gemini_explore_verdicts",
            "gemini_predict_verdicts",
            "gpt_explore_verdicts",
            "gpt_predict_verdicts",
        ):
            try:
                q(
                    cur,
                    f"{table} today action counts",
                    f"""
                    SELECT action_taken, category, COUNT(*) AS cnt,
                           ROUND(AVG(confidence), 3) AS avg_conf
                    FROM {table}
                    WHERE created_at >= CURDATE()
                      AND created_at < CURDATE() + INTERVAL 1 DAY
                    GROUP BY action_taken, category
                    ORDER BY cnt DESC
                    LIMIT 30
                    """,
                )
            except Exception as exc:
                print(f"\n## {table} today action counts\nERROR: {exc}")

        for table in ("deepseek_advisor_reviews", "gemini_advisor_reviews"):
            try:
                q(
                    cur,
                    f"{table} today open decisions",
                    f"""
                    SELECT decision, review_type, source, COUNT(*) AS cnt
                    FROM {table}
                    WHERE created_at >= CURDATE()
                      AND created_at < CURDATE() + INTERVAL 1 DAY
                      AND review_type='open'
                    GROUP BY decision, review_type, source
                    ORDER BY cnt DESC
                    LIMIT 50
                    """,
                )
            except Exception as exc:
                print(f"\n## {table} today open decisions\nERROR: {exc}")

    conn.close()


if __name__ == "__main__":
    main()
