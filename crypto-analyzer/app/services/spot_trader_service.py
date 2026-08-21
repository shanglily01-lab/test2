#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货持仓监控（REQ-SPOT §7.4）

开仓不在本循环扫描：由合约模拟成交镜像（spot_paper_mirror）。
本循环只检查已开模拟现货仓的 TP / SL / 到期。
旧 DCA 抄底已停用。现货实盘仅在模拟开/平仓瞬间同步。
"""

import asyncio
import sys
import os
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pymysql
import pymysql.cursors
from loguru import logger

from app.utils.config_loader import get_db_config
from app.utils.position_time import utc_now_naive
from app.services.spot_paper_mirror import SPOT_ACCOUNT_ID

POSITION_CHECK_INTERVAL_SECONDS = 60


def _get_conn():
    return pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _get_current_price(symbol: str) -> Optional[float]:
    try:
        from app.services.binance_data_hub import get_global_data_hub

        hub = get_global_data_hub()
        p = hub.get_price_sync(symbol, max_age_seconds=90)
        if p is not None and p > 0:
            return float(p)
    except Exception as e:
        logger.warning(f"[现货] HUB 取价失败 {symbol}: {e}")
    return None


def _close_position(pos_id: int, symbol: str, reason: str) -> None:
    """平模拟现货仓；若现货实盘开关开且有映射，同步市价卖出。"""
    try:
        price = _get_current_price(symbol)
        if not price or price <= 0:
            logger.warning(f"[现货] {symbol} 平仓价无效, 跳过")
            return

        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT quantity FROM spot_positions WHERE id=%s AND status='open'",
            (pos_id,),
        )
        row = cur.fetchone()
        qty = float(row["quantity"]) if row else 0.0
        cur.execute(
            """
            UPDATE spot_positions
            SET status='closed', close_price=%s, mark_price=%s,
                realized_pnl = (%s - entry_price) * quantity,
                close_time=NOW(), close_reason=%s
            WHERE id=%s AND status='open'
            """,
            (price, price, price, reason, pos_id),
        )
        closed = cur.rowcount
        cur.close()
        conn.close()
        if not closed:
            return
        logger.info(f"[现货] 卖出 {symbol} id={pos_id} reason={reason} price={price:.6g}")
        try:
            from app.services.spot_live_sync import sync_spot_live_sell
            sync_spot_live_sell(spot_position_id=pos_id, symbol=symbol, quantity=qty)
        except Exception as live_ex:
            logger.warning(f"[现货] 实盘卖出失败 {symbol}: {live_ex}")
    except Exception as e:
        logger.error(f"[现货] 卖出失败 {symbol}: {e}")


def _check_open_positions() -> None:
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, symbol, entry_price, take_profit_price, stop_loss_price,
                   planned_close_time
            FROM spot_positions
            WHERE status='open' AND account_id=%s
            """,
            (SPOT_ACCOUNT_ID,),
        )
        positions = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        logger.error(f"[现货] 查询持仓失败: {e}")
        return

    now = utc_now_naive()
    for pos in positions:
        pid = pos["id"]
        symbol = pos["symbol"]
        entry = float(pos["entry_price"])
        tp = float(pos["take_profit_price"]) if pos.get("take_profit_price") else None
        sl = float(pos["stop_loss_price"]) if pos.get("stop_loss_price") else None
        planned_close = pos.get("planned_close_time")

        price = _get_current_price(symbol)
        if not price or price <= 0:
            continue

        profit_pct = (price - entry) / entry if entry else 0.0
        if tp and price >= tp:
            _close_position(pid, symbol, f"止盈+{profit_pct*100:.1f}%")
            continue
        if sl and price <= sl:
            _close_position(pid, symbol, f"止损{profit_pct*100:.1f}%")
            continue
        if planned_close:
            try:
                due = planned_close if hasattr(planned_close, "year") else datetime.strptime(
                    str(planned_close)[:19], "%Y-%m-%d %H:%M:%S"
                )
                if now >= due:
                    _close_position(pid, symbol, f"超时平仓(profit={profit_pct*100:+.1f}%)")
            except Exception:
                continue


def run_position_check() -> None:
    _check_open_positions()


def run_scan() -> None:
    """旧 DCA 扫描入口已废。保留空函数以免外部 import 崩。"""
    logger.debug("[现货] DCA 扫描已停用，开仓改走合约成交镜像")


async def spot_trader_loop():
    logger.info("[现货] 持仓监控启动（镜像开仓，无 DCA）")
    while True:
        try:
            _check_open_positions()
        except Exception as e:
            logger.error(f"[现货] 持仓检查异常: {e}")
        await asyncio.sleep(POSITION_CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    logger.info("现货持仓监控 — 独立启动")
    asyncio.run(spot_trader_loop())
