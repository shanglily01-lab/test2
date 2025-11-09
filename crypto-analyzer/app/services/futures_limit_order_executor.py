# -*- coding: utf-8 -*-
"""
合约限价单自动执行服务
后台定时检查合约限价单，当价格达到触发条件时自动执行
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional
import pymysql
from loguru import logger


class FuturesLimitOrderExecutor:
    """合约限价单自动执行器"""
    
    def __init__(self, db_config: Dict, trading_engine, price_cache_service=None):
        """
        初始化执行器
        
        Args:
            db_config: 数据库配置
            trading_engine: 合约交易引擎实例 (FuturesTradingEngine)
            price_cache_service: 价格缓存服务（可选）
        """
        self.db_config = db_config
        self.trading_engine = trading_engine
        self.price_cache_service = price_cache_service
        self.running = False
        self.task = None
        
    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=10
        )
    
    def get_current_price(self, symbol: str) -> Decimal:
        """
        获取当前价格
        
        Args:
            symbol: 交易对
            
        Returns:
            当前价格，如果获取失败返回0
        """
        # 优先使用价格缓存服务
        if self.price_cache_service:
            try:
                price = self.price_cache_service.get_price(symbol)
                if price and price > 0:
                    return Decimal(str(price))
            except Exception as e:
                logger.debug(f"从价格缓存获取 {symbol} 价格失败: {e}")
        
        # 回退到交易引擎的价格获取方法
        try:
            price = self.trading_engine.get_current_price(symbol)
            return Decimal(str(price)) if price else Decimal('0')
        except Exception as e:
            logger.error(f"获取 {symbol} 价格失败: {e}")
            return Decimal('0')
    
    async def check_and_execute_limit_orders(self):
        """检查并执行限价单"""
        if not self.running:
            return
            
        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cursor:
                    # 获取所有待成交的限价单（只处理开仓订单）
                    cursor.execute(
                        """SELECT * FROM futures_orders
                        WHERE status = 'PENDING' 
                        AND order_type = 'LIMIT'
                        AND side IN ('OPEN_LONG', 'OPEN_SHORT')
                        ORDER BY created_at ASC"""
                    )
                    pending_orders = cursor.fetchall()
                    
                    if not pending_orders:
                        logger.debug("📋 当前没有合约限价单需要检查")
                        return
                    
                    logger.info(f"📋 检查 {len(pending_orders)} 个合约限价单")
                    
                    for order in pending_orders:
                        try:
                            order_id = order['order_id']
                            account_id = order['account_id']
                            symbol = order['symbol']
                            side = order['side']  # OPEN_LONG 或 OPEN_SHORT
                            limit_price = Decimal(str(order['price']))
                            quantity = Decimal(str(order['quantity']))
                            leverage = order.get('leverage', 1)
                            stop_loss_price = Decimal(str(order['stop_loss_price'])) if order.get('stop_loss_price') else None
                            take_profit_price = Decimal(str(order['take_profit_price'])) if order.get('take_profit_price') else None
                            
                            # 获取当前价格
                            current_price = self.get_current_price(symbol)
                            
                            if current_price == 0:
                                logger.warning(f"无法获取 {symbol} 的价格，跳过订单 {order_id}")
                                continue
                            
                            # 检查是否达到触发条件
                            should_execute = False
                            position_side = 'LONG' if side == 'OPEN_LONG' else 'SHORT'
                            
                            logger.debug(f"🔍 检查限价单 {order_id}: {symbol} {position_side} {quantity} @ 限价 {limit_price}, 当前价 {current_price}")
                            
                            if side == 'OPEN_LONG':
                                # 做多：当前价格 <= 限价时触发
                                if current_price <= limit_price:
                                    should_execute = True
                                    logger.info(f"✅ 做多限价单触发: {symbol} 当前价格 {current_price} <= 限价 {limit_price}")
                            elif side == 'OPEN_SHORT':
                                # 做空：当前价格 >= 限价时触发
                                if current_price >= limit_price:
                                    should_execute = True
                                    logger.info(f"✅ 做空限价单触发: {symbol} 当前价格 {current_price} >= 限价 {limit_price}")
                            
                            if should_execute:
                                # 执行开仓（使用限价作为成交价）
                                try:
                                    # 先解冻保证金（因为限价单创建时已经冻结了保证金）
                                    # 开仓时会重新冻结，所以这里先解冻避免重复冻结
                                    frozen_margin = Decimal(str(order.get('margin', 0)))
                                    if frozen_margin > 0:
                                        cursor.execute(
                                            """UPDATE paper_trading_accounts
                                            SET current_balance = current_balance + %s,
                                                frozen_balance = frozen_balance - %s
                                            WHERE id = %s""",
                                            (float(frozen_margin), float(frozen_margin), account_id)
                                        )
                                    
                                    # 提交解冻操作
                                    conn.commit()
                                    
                                    # 执行开仓（使用限价作为成交价）
                                    # 注意：由于价格已经达到限价，open_position 会立即成交
                                    result = self.trading_engine.open_position(
                                        account_id=account_id,
                                        symbol=symbol,
                                        position_side=position_side,
                                        quantity=quantity,
                                        leverage=leverage,
                                        limit_price=limit_price,  # 使用限价作为成交价
                                        stop_loss_price=stop_loss_price,
                                        take_profit_price=take_profit_price,
                                        source='limit_order'
                                    )
                                    
                                    if result.get('success'):
                                        # 更新订单状态为已成交
                                        cursor.execute(
                                            """UPDATE futures_orders
                                            SET status = 'FILLED',
                                                executed_quantity = quantity,
                                                executed_value = total_value,
                                                avg_fill_price = %s,
                                                fill_time = NOW()
                                            WHERE order_id = %s""",
                                            (float(limit_price), order_id)
                                        )
                                        
                                        conn.commit()
                                        
                                        logger.info(f"✅ 限价单 {order_id} 执行成功: {result.get('message', '')}")
                                    else:
                                        # 如果开仓失败，恢复冻结的保证金
                                        if frozen_margin > 0:
                                            cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET current_balance = current_balance - %s,
                                                    frozen_balance = frozen_balance + %s
                                                WHERE id = %s""",
                                                (float(frozen_margin), float(frozen_margin), account_id)
                                            )
                                            conn.commit()
                                        logger.error(f"❌ 限价单 {order_id} 执行失败: {result.get('message', '未知错误')}")
                                        
                                except Exception as e:
                                    logger.error(f"执行限价单 {order_id} 时出错: {e}")
                                    import traceback
                                    traceback.print_exc()
                                    # 如果出错，尝试恢复冻结的保证金
                                    try:
                                        frozen_margin = Decimal(str(order.get('margin', 0)))
                                        if frozen_margin > 0:
                                            cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET current_balance = current_balance - %s,
                                                    frozen_balance = frozen_balance + %s
                                                WHERE id = %s""",
                                                (float(frozen_margin), float(frozen_margin), account_id)
                                            )
                                            conn.commit()
                                    except:
                                        pass
                                    continue
                            else:
                                logger.debug(f"⏳ 限价单未触发: {symbol} {position_side} 当前价 {current_price} vs 限价 {limit_price}")
                                
                        except Exception as e:
                            logger.error(f"处理限价单 {order.get('order_id', 'unknown')} 时出错: {e}")
                            import traceback
                            traceback.print_exc()
                            continue
                            
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"检查合约限价单时出错: {e}")
            import traceback
            traceback.print_exc()
    
    async def run_loop(self, interval: int = 5):
        """
        运行监控循环
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        self.running = True
        logger.info(f"🔄 合约限价单自动执行服务已启动，检查间隔: {interval}秒")
        
        try:
            while self.running:
                try:
                    await self.check_and_execute_limit_orders()
                except Exception as e:
                    logger.error(f"合约限价单执行循环出错: {e}")
                
                # 等待指定间隔
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    logger.info("合约限价单执行服务收到取消信号")
                    break
        except asyncio.CancelledError:
            logger.info("合约限价单执行服务已取消")
            raise
    
    def start(self, interval: int = 5):
        """
        启动后台任务
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        if self.running:
            logger.warning("合约限价单执行器已在运行")
            return
        
        # 获取或创建事件循环
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        self.task = loop.create_task(self.run_loop(interval))
        logger.info("✅ 合约限价单自动执行服务已启动")
    
    def stop(self):
        """停止后台任务"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("⏹️  合约限价单自动执行服务已停止")

