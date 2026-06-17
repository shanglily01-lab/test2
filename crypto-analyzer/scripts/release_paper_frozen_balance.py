#!/usr/bin/env python3
"""释放模拟盘账户全部 frozen_balance（一次性运维）。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pymysql
from app.services.paper_limit_entry import PAPER_ACCOUNT_ID, is_paper_futures_account
from app.trading.futures_trading_engine import _update_account_total_equity
from app.utils.config_loader import get_db_config
from update_account_stats import update_account_statistics


def release_paper_frozen_balance() -> None:
    conn = pymysql.connect(**get_db_config(), charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, current_balance, frozen_balance FROM futures_trading_accounts WHERE id=%s",
            (PAPER_ACCOUNT_ID,),
        )
        before = cur.fetchone()
        if not before:
            print(f"account {PAPER_ACCOUNT_ID} not found")
            return
        print(f"before: frozen={before['frozen_balance']} balance={before['current_balance']}")

        cur.execute(
            "UPDATE futures_trading_accounts SET frozen_balance=0, updated_at=NOW() WHERE id=%s",
            (PAPER_ACCOUNT_ID,),
        )
        _update_account_total_equity(cur, PAPER_ACCOUNT_ID)
        conn.commit()

        cur.execute(
            "SELECT current_balance, frozen_balance, total_equity FROM futures_trading_accounts WHERE id=%s",
            (PAPER_ACCOUNT_ID,),
        )
        after = cur.fetchone()
        print(f"after:  frozen={after['frozen_balance']} balance={after['current_balance']} equity={after['total_equity']}")
        print(f"available (paper) = current_balance = {after['current_balance']}")
    finally:
        conn.close()

    update_account_statistics(PAPER_ACCOUNT_ID)
    print("done")


if __name__ == "__main__":
    assert is_paper_futures_account(PAPER_ACCOUNT_ID)
    release_paper_frozen_balance()
