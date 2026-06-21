# -*- coding: utf-8 -*-
"""模拟盘限价单执行器 — 价格触发成交；超时按 system_settings 放弃或转市价。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

import pymysql
from loguru import logger

from app.services.paper_limit_entry import (
    PAPER_LIMIT_MIN_FILL_AGE_SEC,
    PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET,
    PAPER_LIMIT_TIMEOUT_MINUTES,
    get_paper_limit_timeout_action,
    parse_order_notes,
)
from app.utils.futures_price import (
    build_futures_limit_trigger_price_map,
    lookup_limit_trigger_price,
)
from app.utils.futures_symbol import futures_symbol_rating_canonical

# 每 tick 扫描上限（原 LIMIT 50 导致第 51+ 笔永不被处理）
PENDING_FETCH_LIMIT = 500
FILLING_STALE_MINUTES = 3


class FuturesLimitOrderExecutor:
    """轮询 futures_orders PENDING 限价开仓单。"""

    def __init__(self, db_config: Dict, trading_engine):
        self.db_config = db_config
        self.trading_engine = trading_engine
        self.running = False
        self.task: Optional[asyncio.Task] = None

    def _connect(self):
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10,
        )

    def _recover_stale_filling_orders(self, conn) -> int:
        """成交中断后 FILLING 卡死 → 还原 PENDING 以便重试。"""
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE futures_orders
                SET status='PENDING', updated_at=NOW()
                WHERE status='FILLING'
                  AND order_type='LIMIT'
                  AND side IN ('OPEN_LONG', 'OPEN_SHORT')
                  AND updated_at < NOW() - INTERVAL %s MINUTE
                """,
                (FILLING_STALE_MINUTES,),
            )
            n = cur.rowcount
        if n:
            logger.warning(f"[限价执行器] 恢复 {n} 笔卡住 FILLING → PENDING")
        return n

    def _cancel_order(self, conn, order_id: str, reason: str) -> None:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE futures_orders
                SET status='EXPIRED', cancellation_reason=%s,
                    canceled_at=NOW(), updated_at=NOW()
                WHERE order_id=%s AND status='PENDING'
                """,
                (reason, order_id),
            )

    @staticmethod
    def _should_fill_at_price(
        side: str, limit_price: Decimal, current_price: Decimal,
    ) -> bool:
        if current_price <= 0:
            return False
        if side == 'OPEN_LONG' and current_price <= limit_price:
            return True
        if side == 'OPEN_SHORT' and current_price >= limit_price:
            return True
        return False

    @staticmethod
    def _stale_limit_cancel(
        side: str, limit_price: Decimal, current_price: Decimal,
    ) -> bool:
        if current_price <= 0:
            return False
        if side == 'OPEN_LONG' and limit_price > current_price * Decimal('1.03'):
            return True
        if side == 'OPEN_SHORT' and limit_price < current_price * Decimal('0.97'):
            return True
        return False

    def _classify_order(
        self,
        order: Dict,
        price_map: Dict[str, float],
    ) -> Tuple[str, Optional[Decimal], Optional[str]]:
        """
        Returns:
            action: fill | timeout | cancel | skip
            current_price (if known)
            cancel_reason (for cancel)
        """
        side = order['side']
        symbol = order['symbol']
        limit_price = Decimal(str(order['price']))
        meta = parse_order_notes(order.get('notes'))
        timeout_minutes = int(meta.get('timeout_minutes') or PAPER_LIMIT_TIMEOUT_MINUTES)
        min_fill_age = int(meta.get('min_fill_age_sec') or PAPER_LIMIT_MIN_FILL_AGE_SEC)
        elapsed = int(order.get('elapsed_seconds') or 0)

        if elapsed < min_fill_age:
            return 'skip', None, None

        if elapsed >= timeout_minutes * 60:
            return 'timeout', None, None

        px = lookup_limit_trigger_price(price_map, symbol)
        if px is None or px <= 0:
            return 'skip', None, None
        current_price = Decimal(str(px))

        ref_px = meta.get('ref_price')
        if ref_px and float(ref_px) > 0:
            ref_dev = abs(float(current_price) - float(ref_px)) / float(ref_px)
            if ref_dev > 0.15:
                return 'cancel', current_price, (
                    f'stale_price_feed ref={ref_px} market={current_price}'
                )

        if self._stale_limit_cancel(side, limit_price, current_price):
            if side == 'OPEN_LONG':
                reason = f'stale_limit_above_market limit={limit_price} market={current_price}'
            else:
                reason = f'stale_limit_below_market limit={limit_price} market={current_price}'
            return 'cancel', current_price, reason

        if self._should_fill_at_price(side, limit_price, current_price):
            return 'fill', current_price, None

        return 'skip', current_price, None

    def check_and_execute_limit_orders(self) -> None:
        conn = None
        stats = {
            'pending': 0, 'fillable': 0, 'filled': 0, 'fill_fail': 0,
            'timeout_ok': 0, 'timeout_fail': 0, 'expired': 0, 'no_price': 0,
        }
        try:
            conn = self._connect()
            self._recover_stale_filling_orders(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT o.*,
                           TIMESTAMPDIFF(SECOND, o.created_at, NOW()) AS elapsed_seconds
                    FROM futures_orders o
                    WHERE o.status='PENDING'
                      AND o.order_type='LIMIT'
                      AND o.side IN ('OPEN_LONG', 'OPEN_SHORT')
                    ORDER BY o.created_at ASC
                    LIMIT %s
                    """,
                    (PENDING_FETCH_LIMIT,),
                )
                orders = cur.fetchall()

            stats['pending'] = len(orders)
            if not orders:
                return

            symbols = list({o['symbol'] for o in orders if o.get('symbol')})
            price_map = build_futures_limit_trigger_price_map(
                conn, symbols, max_age_seconds=30, log_tag="limit_executor",
            )

            fill_queue: List[Tuple[Dict, Decimal]] = []
            timeout_queue: List[Dict] = []

            for order in orders:
                action, cur_px, cancel_reason = self._classify_order(order, price_map)
                if action == 'fill' and cur_px is not None:
                    fill_queue.append((order, cur_px))
                elif action == 'timeout':
                    timeout_queue.append(order)
                elif action == 'cancel' and cancel_reason:
                    self._cancel_order(conn, order['order_id'], cancel_reason)
                    stats['expired'] += 1
                    logger.warning(
                        f"[限价执行器] 取消 {order['symbol']} {order['side']} "
                        f"reason={cancel_reason}"
                    )
                elif action == 'skip' and cur_px is None:
                    stats['no_price'] += 1

            stats['fillable'] = len(fill_queue)

            if stats['pending'] >= 50 and stats['fillable'] > 0:
                beyond_old_cap = stats['pending'] > 50
                if beyond_old_cap:
                    logger.info(
                        f"[限价执行器] pending={stats['pending']} "
                        f"fillable={stats['fillable']} (批量扫描，非仅最老50笔)"
                    )

            for order, current_price in fill_queue:
                order_id = order['order_id']
                symbol = order['symbol']
                side = order['side']
                limit_price = Decimal(str(order['price']))
                try:
                    logger.info(
                        f"[限价执行器] 触价 {symbol} {side} "
                        f"现价={current_price} 限价={limit_price} order={order_id}"
                    )
                    result = self.trading_engine.fill_paper_limit_order(order)
                    if result.get('success'):
                        stats['filled'] += 1
                        logger.info(
                            f"[限价执行器] 成交 {symbol} {side} "
                            f"触发价={current_price} 限价={limit_price}"
                        )
                    else:
                        stats['fill_fail'] += 1
                        logger.warning(
                            f"[限价执行器] 触价但成交失败 {symbol} {side} "
                            f"现价={current_price} 限价={limit_price}: "
                            f"{result.get('message')}"
                        )
                except Exception as e:
                    stats['fill_fail'] += 1
                    logger.error(f"[限价执行器] 成交异常 {symbol} {order_id}: {e}")

            for order in timeout_queue:
                order_id = order['order_id']
                symbol = order['symbol']
                side = order['side']
                limit_price = Decimal(str(order['price']))
                elapsed = int(order.get('elapsed_seconds') or 0)
                try:
                    if get_paper_limit_timeout_action() == PAPER_LIMIT_TIMEOUT_ACTION_CONVERT_MARKET:
                        result = self.trading_engine.fill_paper_limit_order(
                            order, at_market=True,
                        )
                        if result.get('success'):
                            stats['timeout_ok'] += 1
                            logger.info(
                                f"[限价执行器] 超时转市价 {symbol} {side} "
                                f"限价={limit_price} 等待={elapsed // 60}min"
                            )
                        elif result.get('message') == '订单已处理或不存在':
                            logger.debug(
                                f"[限价执行器] 超时转市价跳过 {symbol} {side}（已被其他进程成交）"
                            )
                        else:
                            stats['timeout_fail'] += 1
                            self._cancel_order(
                                conn, order_id,
                                f"timeout_market_failed:{result.get('message', '')}",
                            )
                            logger.warning(
                                f"[限价执行器] 超时转市价失败，已放弃 {symbol} {side}: "
                                f"{result.get('message')}"
                            )
                    else:
                        self._cancel_order(conn, order_id, 'timeout')
                        stats['expired'] += 1
                        logger.info(
                            f"[限价执行器] 超时放弃 {symbol} {side} "
                            f"限价={limit_price} 等待={elapsed // 60}min"
                        )
                except Exception as e:
                    stats['timeout_fail'] += 1
                    logger.error(f"[限价执行器] 超时处理异常 {symbol} {order_id}: {e}")

            if stats['fillable'] or stats['fill_fail'] or stats['filled']:
                logger.info(
                    f"[限价执行器] tick 汇总 pending={stats['pending']} "
                    f"fillable={stats['fillable']} filled={stats['filled']} "
                    f"fail={stats['fill_fail']} no_price={stats['no_price']} "
                    f"expired={stats['expired']}"
                )
        except Exception as e:
            logger.error(f"[限价执行器] 轮询异常: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    async def run_loop(self, interval: int = 5) -> None:
        self.running = True
        logger.info(f"[限价执行器] 启动 (interval={interval}s, batch_limit={PENDING_FETCH_LIMIT})")
        while self.running:
            try:
                await asyncio.to_thread(self.check_and_execute_limit_orders)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[限价执行器] loop 异常: {e}")
            await asyncio.sleep(interval)

    def stop(self) -> None:
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()


_executor: Optional[FuturesLimitOrderExecutor] = None


def init_futures_limit_order_executor(db_config: Dict, trading_engine) -> FuturesLimitOrderExecutor:
    global _executor
    _executor = FuturesLimitOrderExecutor(db_config, trading_engine)
    return _executor


def get_futures_limit_order_executor() -> Optional[FuturesLimitOrderExecutor]:
    return _executor
