"""
待成交订单自动执行服务
后台定时检查待成交订单，当价格达到触发条件时自动执行
"""

import asyncio
from decimal import Decimal
from typing import Dict, Optional
import pymysql
from loguru import logger


class PendingOrderExecutor:
    """待成交订单自动执行器"""
    
    def __init__(self, db_config: Dict, trading_engine, price_cache_service=None):
        """
        初始化执行器
        
        Args:
            db_config: 数据库配置
            trading_engine: 交易引擎实例
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
    
    async def check_and_execute_pending_orders(self):
        """检查并执行待成交订单"""
        if not self.running:
            return
            
        try:
            conn = self._get_connection()
            try:
                with conn.cursor() as cursor:
                    # 获取所有未执行的待成交订单
                    cursor.execute(
                        """SELECT * FROM paper_trading_pending_orders
                        WHERE executed = FALSE AND status = 'PENDING'
                        ORDER BY created_at ASC"""
                    )
                    pending_orders = cursor.fetchall()
                    
                    if not pending_orders:
                        logger.info("📋 当前没有待成交订单需要检查")
                        return
                    
                    logger.info(f"📋 检查 {len(pending_orders)} 个待成交订单")
                    
                    for order in pending_orders:
                        try:
                            account_id = order['account_id']
                            order_id = order['order_id']
                            symbol = order['symbol']
                            side = order['side']
                            quantity = Decimal(str(order['quantity']))
                            trigger_price = Decimal(str(order['trigger_price']))
                            
                            # 获取当前价格
                            current_price = self.get_current_price(symbol)
                            
                            if current_price == 0:
                                logger.warning(f"无法获取 {symbol} 的价格，跳过订单 {order_id}")
                                continue
                            
                            # 检查是否达到触发条件
                            should_execute = False
                            logger.info(f"🔍 检查订单 {order_id}: {symbol} {side} {quantity} @ 触发价 {trigger_price}, 当前价 {current_price}")
                            
                            if side == 'BUY' and current_price <= trigger_price:
                                should_execute = True
                                logger.info(f"✅ 买入订单触发: {symbol} 当前价格 {current_price} <= 触发价格 {trigger_price}")
                            elif side == 'SELL' and current_price >= trigger_price:
                                should_execute = True
                                logger.info(f"✅ 卖出订单触发: {symbol} 当前价格 {current_price} >= 触发价格 {trigger_price}")
                            else:
                                logger.debug(f"⏳ 订单未触发: {symbol} {side} 当前价 {current_price} vs 触发价 {trigger_price}")
                            
                            if should_execute:
                                # 执行订单
                                success, message, executed_order_id = self.trading_engine.place_order(
                                    account_id=account_id,
                                    symbol=symbol,
                                    side=side,
                                    quantity=quantity,
                                    order_type='MARKET',
                                    order_source='auto',
                                    pending_order_id=order_id  # 传递待成交订单ID，用于精确匹配
                                )
                                
                                if success:
                                    logger.info(f"✅ 待成交订单 {order_id} 执行成功: {message}")
                                else:
                                    logger.error(f"❌ 待成交订单 {order_id} 执行失败: {message}")
                                    
                        except Exception as e:
                            logger.error(f"处理待成交订单 {order.get('order_id', 'unknown')} 时出错: {e}")
                            continue
                            
            finally:
                conn.close()
                
        except Exception as e:
            logger.error(f"检查待成交订单时出错: {e}")
    
    async def run_loop(self, interval: int = 5):
        """
        运行监控循环
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        self.running = True
        logger.info(f"🔄 待成交订单自动执行服务已启动，检查间隔: {interval}秒")
        
        try:
            while self.running:
                try:
                    await self.check_and_execute_pending_orders()
                except Exception as e:
                    logger.error(f"待成交订单执行循环出错: {e}")
                
                # 等待指定间隔
                try:
                    await asyncio.sleep(interval)
                except asyncio.CancelledError:
                    logger.info("待成交订单执行服务收到取消信号")
                    break
        except asyncio.CancelledError:
            logger.info("待成交订单执行服务已取消")
            raise
    
    def start(self, interval: int = 5):
        """
        启动后台任务
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        if self.running:
            logger.warning("待成交订单执行器已在运行")
            return
        
        # 获取或创建事件循环
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        self.task = loop.create_task(self.run_loop(interval))
        logger.info("✅ 待成交订单自动执行服务已启动")
    
    def stop(self):
        """停止后台任务"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("⏹️  待成交订单自动执行服务已停止")

