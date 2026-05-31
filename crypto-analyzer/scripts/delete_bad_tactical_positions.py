#!/usr/bin/env python3
"""删除取价异常导致的模拟仓 (TA deepseek_chase / LAB deepseek_reversal)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pymysql

from app.utils.config_loader import get_db_config

# position_id, verdict_table, verdict_id, runs_table, run_id
TARGETS = [
    (28206, "deepseek_chase_explore_verdicts", 2, "deepseek_chase_explore_runs", 2),
    (28209, "deepseek_reversal_explore_verdicts", 1, "deepseek_reversal_explore_runs", 2),
]


def main() -> None:
    conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        conn.autocommit(False)
        cur = conn.cursor()
        for pid, vt, vid, rt, run_id in TARGETS:
            cur.execute("SELECT id, symbol, source FROM futures_positions WHERE id=%s", (pid,))
            pos = cur.fetchone()
            if not pos:
                print(f"skip pid={pid}: not found")
                continue
            print(f"delete pid={pid} {pos['symbol']} source={pos['source']}")

            cur.execute("DELETE FROM futures_trades WHERE position_id=%s", (pid,))
            print(f"  futures_trades deleted={cur.rowcount}")

            cur.execute("DELETE FROM futures_orders WHERE position_id=%s", (pid,))
            print(f"  futures_orders deleted={cur.rowcount}")

            cur.execute(f"DELETE FROM {vt} WHERE id=%s", (vid,))
            print(f"  {vt} deleted={cur.rowcount}")

            cur.execute("DELETE FROM futures_positions WHERE id=%s", (pid,))
            print(f"  futures_positions deleted={cur.rowcount}")

            cur.execute(
                f"UPDATE {rt} SET trades_opened=GREATEST(trades_opened-1,0) WHERE id=%s",
                (run_id,),
            )
            print(f"  {rt} trades_opened decremented")

        conn.commit()
        print("done")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
