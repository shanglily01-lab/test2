# -*- coding: utf-8 -*-
"""模拟盘限价单执行器 — 价格触发成交，30 分钟超时取消。"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Dict, Optional

import pymysql
from loguru import logger

from app.services.paper_limit_entry import (
    PAPER_LIMIT_MIN_FILL_AGE_SEC,
    PAPER_LIMIT_TIMEOUT_MINUTES,
    parse_order_notes,
)
from app.utils.futures_price import get_futures_trade_price
from app.utils.futures_symbol import futures_symbol_rating_canonical


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
        )

    def _get_price(self, conn, symbol: str) -> Decimal:
        """与挂单创建/UI 一致：优先 mark/ticker 实时价，失败再回退引擎取价。"""
        sym = futures_symbol_rating_canonical(symbol)
        try:
            fresh = get_futures_trade_price(
                conn, sym, max_age_seconds=30, log_tag="limit_executor", require_fresh=False,
            )
            if fresh and fresh > 0:
                return Decimal(str(fresh))
        except Exception as e:
            logger.debug(f"[限价执行器] {sym} get_futures_trade_price 失败: {e}")
        try:
            px = self.trading_engine.get_current_price(sym, use_realtime=True)
            return Decimal(str(px)) if px else Decimal('0')
        except Exception as e:
            logger.warning(f"[限价执行器] 获取价格失败 {sym}: {e}")
            return Decimal('0')

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

    def check_and_execute_limit_orders(self) -> None:
        conn = None
        try:
            conn = self._connect()
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
                    LIMIT 50
                    """
                )
                orders = cur.fetchall()

            for order in orders:
                try:
                    order_id = order['order_id']
                    symbol = order['symbol']
                    side = order['side']
                    limit_price = Decimal(str(order['price']))
                    meta = parse_order_notes(order.get('notes'))
                    timeout_minutes = int(meta.get('timeout_minutes') or PAPER_LIMIT_TIMEOUT_MINUTES)
                    min_fill_age = int(meta.get('min_fill_age_sec') or PAPER_LIMIT_MIN_FILL_AGE_SEC)
                    elapsed = int(order.get('elapsed_seconds') or 0)

                    if elapsed < min_fill_age:
                        continue

                    if elapsed >= timeout_minutes * 60:
                        self._cancel_order(conn, order_id, 'timeout')
                        logger.info(
                            f"[限价执行器] 超时取消 {symbol} {side} "
                            f"限价={limit_price} 等待={elapsed // 60}min"
                        )
                        continue

                    current_price = self._get_price(conn, symbol)
                    if current_price <= 0:
                        logger.debug(
                            f"[限价执行器] {symbol} {side} 无有效市价 "
                            f"限价={limit_price} elapsed={elapsed}s"
                        )
                        continue

                    ref_px = meta.get('ref_price')
                    if ref_px and float(ref_px) > 0:
                        ref_dev = abs(float(current_price) - float(ref_px)) / float(ref_px)
                        if ref_dev > 0.15:
                            self._cancel_order(
                                conn, order_id,
                                f'stale_price_feed ref={ref_px} market={current_price}',
                            )
                            logger.warning(
                                f"[限价执行器] 取消陈旧限价单 {symbol} {side} "
                                f"挂单参考={ref_px} 现价={current_price} 偏离={ref_dev * 100:.1f}%"
                            )
                            continue

                    # 做多限价应低于市价；限价明显高于现价说明按过期行情挂单
                    if side == 'OPEN_LONG' and limit_price > current_price * Decimal('1.03'):
                        self._cancel_order(
                            conn, order_id,
                            f'stale_limit_above_market limit={limit_price} market={current_price}',
                        )
                        logger.warning(
                            f"[限价执行器] 取消失真做多限价 {symbol} "
                            f"限价={limit_price} 现价={current_price}"
                        )
                        continue
                    if side == 'OPEN_SHORT' and limit_price < current_price * Decimal('0.97'):
                        self._cancel_order(
                            conn, order_id,
                            f'stale_limit_below_market limit={limit_price} market={current_price}',
                        )
                        logger.warning(
                            f"[限价执行器] 取消失真做空限价 {symbol} "
                            f"限价={limit_price} 现价={current_price}"
                        )
                        continue

                    should_fill = False
                    if side == 'OPEN_LONG' and current_price <= limit_price:
                        should_fill = True
                    elif side == 'OPEN_SHORT' and current_price >= limit_price:
                        should_fill = True

                    if not should_fill:
                        continue

                    result = self.trading_engine.fill_paper_limit_order(order)
                    if result.get('success'):
                        logger.info(
                            f"[限价执行器] 成交 {symbol} {side} "
                            f"触发价={current_price} 限价={limit_price}"
                        )
                    else:
                        logger.warning(
                            f"[限价执行器] 成交失败 {symbol} {side}: {result.get('message')}"
                        )
                except Exception as e:
                    logger.error(f"[限价执行器] 处理订单异常: {e}")
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
        logger.info(f"[限价执行器] 启动 (interval={interval}s)")
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
