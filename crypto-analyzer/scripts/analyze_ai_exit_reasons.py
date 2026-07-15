"""Read-only AI exit reason performance summary."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config


AI_SOURCES = (
    "deepseek_explore",
    "deepseek_predict",
    "gemini_explore",
    "gemini_predict",
)


def main() -> None:
    conn = pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  CASE
                    WHEN notes LIKE '%%AI trend-sl%%'
                      OR notes LIKE '%%ai-trend-sl%%'
                      OR notes LIKE '%%trend-sl%%'
                      THEN 'trend_sl'
                    WHEN notes LIKE '%%AI soft-sl%%'
                      OR notes LIKE '%%ai-soft-sl%%'
                      OR notes LIKE '%%soft-sl%%'
                      THEN 'soft_sl'
                    WHEN notes LIKE '%%ai-trail-tp%%'
                      OR notes LIKE '%%trail-tp%%'
                      OR notes LIKE '%%移动止盈%%'
                      THEN 'trail'
                    WHEN notes LIKE '%%止盈%%' OR notes='take_profit'
                      THEN 'tp'
                    WHEN notes LIKE '%%止损%%' OR notes='stop_loss'
                      THEN 'sl'
                    WHEN notes LIKE '%%planned_close_time_expired%%'
                      OR notes LIKE '%%TIMEOUT%%'
                      OR notes LIKE '%%计划平仓%%'
                      THEN 'timeout'
                    ELSE LEFT(COALESCE(notes, 'unknown'), 60)
                  END AS reason,
                  source,
                  COUNT(*) AS trades,
                  ROUND(SUM(realized_pnl > 0) / COUNT(*) * 100, 1) AS wr,
                  ROUND(SUM(realized_pnl), 2) AS pnl,
                  ROUND(AVG(realized_pnl), 2) AS avg_pnl,
                  ROUND(AVG(max_profit_pct), 2) AS avg_max_price_pct,
                  ROUND(AVG(unrealized_pnl_pct), 2) AS avg_close_price_pct
                FROM futures_positions
                WHERE account_id=2
                  AND status='closed'
                  AND close_time >= UTC_TIMESTAMP() - INTERVAL 14 DAY
                  AND realized_pnl IS NOT NULL
                  AND source IN %s
                GROUP BY reason, source
                HAVING trades >= 3
                ORDER BY pnl ASC
                """,
                (AI_SOURCES,),
            )
            print("reason x source last 14d")
            for row in cur.fetchall() or []:
                print(row)

            cur.execute(
                """
                SELECT
                  CASE
                    WHEN max_profit_pct < 1.5 THEN '<1.5'
                    WHEN max_profit_pct < 3.0 THEN '1.5-3'
                    WHEN max_profit_pct < 5.0 THEN '3-5'
                    WHEN max_profit_pct < 8.0 THEN '5-8'
                    ELSE '>=8'
                  END AS peak_bucket,
                  COUNT(*) AS trades,
                  ROUND(SUM(realized_pnl > 0) / COUNT(*) * 100, 1) AS wr,
                  ROUND(SUM(realized_pnl), 2) AS pnl,
                  ROUND(AVG(realized_pnl), 2) AS avg_pnl,
                  ROUND(AVG(max_profit_pct), 2) AS avg_max_price_pct,
                  ROUND(AVG(unrealized_pnl_pct), 2) AS avg_close_price_pct
                FROM futures_positions
                WHERE account_id=2
                  AND status='closed'
                  AND close_time >= UTC_TIMESTAMP() - INTERVAL 14 DAY
                  AND realized_pnl IS NOT NULL
                  AND source IN %s
                  AND (
                    notes LIKE '%%ai-trail-tp%%'
                    OR notes LIKE '%%trail-tp%%'
                    OR notes LIKE '%%移动止盈%%'
                  )
                GROUP BY peak_bucket
                ORDER BY MIN(max_profit_pct)
                """,
                (AI_SOURCES,),
            )
            print("\ntrail peak buckets last 14d")
            for row in cur.fetchall() or []:
                print(row)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
