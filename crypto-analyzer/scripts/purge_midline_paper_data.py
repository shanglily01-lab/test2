#!/usr/bin/env python3
"""清理全部中线策略数据：模拟盘订单/持仓/成交 + 扫描运行历史。

默认 dry-run；加 --execute 才真正写库。
可选 --no-kill-switch 仅删数据不关闭策略开关。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql

from app.services.midline_swing_config import (
    MIDLINE_ACCOUNT_ID,
    MIDLINE_KILL_SWITCH,
    MIDLINE_SOURCES,
)
from app.utils.config_loader import get_db_config


def _ph(n: int) -> str:
    return ", ".join(["%s"] * n)


def _count(cur, sql: str, params: tuple) -> int:
    cur.execute(sql, params)
    row = cur.fetchone()
    return int((row or {}).get("cnt") or 0)


def _table_exists(cur, name: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (name,))
    return cur.fetchone() is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="清理中线策略订单/持仓/成交/扫描历史")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行（默认仅预览计数）",
    )
    parser.add_argument(
        "--account-id",
        type=int,
        default=MIDLINE_ACCOUNT_ID,
        help=f"模拟账户 ID（默认 {MIDLINE_ACCOUNT_ID}，仅用于权益刷新）",
    )
    parser.add_argument(
        "--no-kill-switch",
        action="store_true",
        help="不修改四路 kill switch",
    )
    args = parser.parse_args()
    execute = args.execute
    account_id = args.account_id
    sources = tuple(sorted(MIDLINE_SOURCES))
    ph = _ph(len(sources))

    cfg = get_db_config()
    conn = pymysql.connect(**cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)

    try:
        with conn.cursor() as cur:
            print("=" * 60)
            print("中线策略数据清理（订单 + 持仓 + 成交 + 扫描历史）")
            print(f"模式: {'EXECUTE' if execute else 'DRY-RUN'}")
            print(f"equity refresh account_id={account_id}")
            print(f"sources: {', '.join(sources)}")
            print("=" * 60)

            if not args.no_kill_switch:
                print("\n[1] Kill Switch -> OFF")
                for source in sources:
                    key = MIDLINE_KILL_SWITCH[source]
                    cur.execute(
                        "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
                        (key,),
                    )
                    row = cur.fetchone()
                    old = str((row or {}).get("setting_value", "0")).strip()
                    print(f"  {key}: {old} -> 0")
                    if execute:
                        cur.execute(
                            """
                            INSERT INTO system_settings (setting_key, setting_value)
                            VALUES (%s, '0')
                            ON DUPLICATE KEY UPDATE setting_value='0'
                            """,
                            (key,),
                        )
            else:
                print("\n[1] Kill Switch: 跳过 (--no-kill-switch)")

            cur.execute(
                f"""
                SELECT id, symbol, status, source, account_id
                FROM futures_positions
                WHERE LOWER(source) IN ({ph})
                ORDER BY id
                """,
                sources,
            )
            positions = cur.fetchall()
            pos_ids = [int(r["id"]) for r in positions]
            open_cnt = sum(1 for r in positions if (r.get("status") or "").lower() == "open")
            print(f"\n[2] futures_positions 命中 {len(pos_ids)} 条 (OPEN={open_cnt})")

            trades_by_pos = 0
            trades_by_order = 0
            orders_by_source = _count(
                cur,
                f"""
                SELECT COUNT(*) AS cnt FROM futures_orders
                WHERE LOWER(order_source) IN ({ph})
                """,
                sources,
            )
            if pos_ids:
                pid_ph = _ph(len(pos_ids))
                trades_by_pos = _count(
                    cur,
                    f"SELECT COUNT(*) AS cnt FROM futures_trades WHERE position_id IN ({pid_ph})",
                    tuple(pos_ids),
                )
            cur.execute(
                f"""
                SELECT COUNT(DISTINCT t.id) AS cnt
                FROM futures_trades t
                INNER JOIN futures_orders o ON o.order_id = t.order_id AND o.account_id = t.account_id
                WHERE LOWER(o.order_source) IN ({ph})
                """,
                sources,
            )
            trades_by_order = int((cur.fetchone() or {}).get("cnt") or 0)

            runs_cnt = 0
            verdicts_cnt = 0
            if _table_exists(cur, "midline_swing_runs"):
                runs_cnt = _count(
                    cur,
                    f"SELECT COUNT(*) AS cnt FROM midline_swing_runs WHERE LOWER(source) IN ({ph})",
                    sources,
                )
            if _table_exists(cur, "midline_swing_verdicts"):
                verdicts_cnt = _count(
                    cur,
                    f"SELECT COUNT(*) AS cnt FROM midline_swing_verdicts WHERE LOWER(source) IN ({ph})",
                    sources,
                )

            print("\n[3] 待删除计数")
            print(f"  futures_trades (by position_id): {trades_by_pos}")
            print(f"  futures_trades (by midline order_source): {trades_by_order}")
            print(f"  futures_orders (by order_source): {orders_by_source}")
            print(f"  futures_positions: {len(pos_ids)}")
            print(f"  midline_swing_runs: {runs_cnt}")
            print(f"  midline_swing_verdicts: {verdicts_cnt}")

            if pos_ids:
                cur.execute(
                    f"""
                    SELECT id, symbol, status, source, paper_position_id
                    FROM live_futures_positions
                    WHERE paper_position_id IN ({_ph(len(pos_ids))})
                    """,
                    tuple(pos_ids),
                )
                live_rows = cur.fetchall()
                if live_rows:
                    print(
                        f"\n[警告] live_futures_positions 仍有 {len(live_rows)} 条关联模拟仓"
                        "（本脚本不删实盘表；请自行在交易所/实盘表处理）"
                    )
                    for r in live_rows[:10]:
                        print(
                            f"  live#{r['id']} {r['symbol']} {r['status']} "
                            f"paper={r['paper_position_id']} source={r.get('source')}"
                        )
                    if len(live_rows) > 10:
                        print(f"  ... 另有 {len(live_rows) - 10} 条")

            if not execute:
                print("\nDRY-RUN 完成。加 --execute 执行删除。")
                return 0

            deleted_trades = 0
            if pos_ids:
                pid_ph = _ph(len(pos_ids))
                cur.execute(
                    f"DELETE FROM futures_trades WHERE position_id IN ({pid_ph})",
                    tuple(pos_ids),
                )
                deleted_trades += cur.rowcount
            cur.execute(
                f"""
                DELETE t FROM futures_trades t
                INNER JOIN futures_orders o
                  ON o.order_id = t.order_id AND o.account_id = t.account_id
                WHERE LOWER(o.order_source) IN ({ph})
                """,
                sources,
            )
            deleted_trades += cur.rowcount

            cur.execute(
                f"""
                DELETE FROM futures_orders
                WHERE LOWER(order_source) IN ({ph})
                """,
                sources,
            )
            deleted_orders = cur.rowcount

            cur.execute(
                f"""
                DELETE FROM futures_positions
                WHERE LOWER(source) IN ({ph})
                """,
                sources,
            )
            deleted_positions = cur.rowcount

            deleted_runs = 0
            deleted_verdicts = 0
            if _table_exists(cur, "midline_swing_verdicts"):
                cur.execute(
                    f"DELETE FROM midline_swing_verdicts WHERE LOWER(source) IN ({ph})",
                    sources,
                )
                deleted_verdicts = cur.rowcount
            if _table_exists(cur, "midline_swing_runs"):
                cur.execute(
                    f"DELETE FROM midline_swing_runs WHERE LOWER(source) IN ({ph})",
                    sources,
                )
                deleted_runs = cur.rowcount

            cur.execute(
                """
                UPDATE futures_trading_accounts a
                SET a.total_equity = a.current_balance + COALESCE((
                    SELECT SUM(p.unrealized_pnl)
                    FROM futures_positions p
                    WHERE p.account_id = a.id AND p.status = 'open'
                ), 0)
                WHERE a.id = %s
                """,
                (account_id,),
            )

            if not args.no_kill_switch:
                from app.services.data_cache_service import invalidate_setting_cache
                from app.services.system_settings_loader import invalidate_loader_cache
                invalidate_setting_cache()
                invalidate_loader_cache()

            conn.commit()

            print("\n[4] 已执行")
            print(f"  futures_trades deleted (rowcount sum): {deleted_trades}")
            print(f"  futures_orders deleted: {deleted_orders}")
            print(f"  futures_positions deleted: {deleted_positions}")
            print(f"  midline_swing_verdicts deleted: {deleted_verdicts}")
            print(f"  midline_swing_runs deleted: {deleted_runs}")
            if not args.no_kill_switch:
                print("  kill switches: 4 -> OFF")
            print(f"  account #{account_id} total_equity refreshed")
            return 0
    except Exception as exc:
        conn.rollback()
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
