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
        self.connection = None  # 持久数据库连接
        
    def _get_connection(self):
        """获取数据库连接（复用持久连接）"""
        # 如果连接不存在或已断开，创建新连接
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=5,
                    read_timeout=10,
                    write_timeout=10,
                    autocommit=True  # 自动提交，避免事务问题
                )
                # 只在首次创建连接时记录（DEBUG级别）
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
        else:
            # 静默检查连接是否还活着（不打印日志）
            try:
                self.connection.ping(reconnect=True)
            except Exception as e:
                # 只有在连接真正断开需要重连时才记录
                logger.warning(f"数据库连接已断开，尝试重连: {e}")
                try:
                    self.connection = pymysql.connect(
                        host=self.db_config.get('host', 'localhost'),
                        port=self.db_config.get('port', 3306),
                        user=self.db_config.get('user', 'root'),
                        password=self.db_config.get('password', ''),
                        database=self.db_config.get('database', 'binance-data'),
                        charset='utf8mb4',
                        cursorclass=pymysql.cursors.DictCursor,
                        connect_timeout=5,
                        read_timeout=10,
                        write_timeout=10,
                        autocommit=True
                    )
                    logger.debug("✅ 数据库连接已重新建立（合约限价单执行器）")
                except Exception as e2:
                    logger.error(f"❌ 重连数据库失败: {e2}")
                    raise
        
        return self.connection
    
    def get_current_price(self, symbol: str, use_realtime: bool = False) -> Decimal:
        """
        获取当前价格
        
        Args:
            symbol: 交易对
            use_realtime: 是否使用实时API价格（限价单扫描时使用）
            
        Returns:
            当前价格，如果获取失败返回0
        """
        # 如果要求使用实时价格，直接调用交易引擎的实时价格方法
        if use_realtime:
            try:
                price = self.trading_engine.get_current_price(symbol, use_realtime=True)
                return Decimal(str(price)) if price else Decimal('0')
            except Exception as e:
                logger.error(f"获取 {symbol} 实时价格失败: {e}")
                return Decimal('0')
        
        # 优先使用价格缓存服务（非实时模式）
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
        """检查并执行限价单（每次查询都创建新连接，确保获取最新数据）"""
        if not self.running:
            return
            
        try:
            # 每次查询都创建新连接，确保获取最新数据
            connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=True
            )
            
            try:
                with connection.cursor() as cursor:
                    # 设置会话时区为 UTC+8（与存储的时间一致）
                    cursor.execute("SET time_zone = '+08:00'")

                    # 获取所有待成交的限价单（只处理开仓订单）
                    # 同时获取策略的超时配置
                    # 注意：使用 strategy_timeout 避免与 futures_orders.timeout_minutes 字段冲突
                    cursor.execute(
                        """SELECT o.*,
                               COALESCE(
                                   CAST(JSON_EXTRACT(s.config, '$.limitOrderTimeoutMinutes') AS UNSIGNED),
                                   0
                               ) as strategy_timeout,
                               s.config as strategy_config,
                               s.name as strategy_name,
                               NOW() as db_now,
                               TIMESTAMPDIFF(SECOND, o.created_at, NOW()) as elapsed_seconds
                        FROM futures_orders o
                        LEFT JOIN trading_strategies s ON CAST(o.strategy_id AS UNSIGNED) = CAST(s.id AS UNSIGNED)
                        WHERE o.status = 'PENDING'
                        AND o.order_type = 'LIMIT'
                        AND o.side IN ('OPEN_LONG', 'OPEN_SHORT')
                        ORDER BY o.created_at ASC"""
                    )
                    pending_orders = cursor.fetchall()
                
                if not pending_orders:
                    return
                
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
                        
                        # 获取当前价格（限价单扫描使用实时价格）
                        current_price = self.get_current_price(symbol, use_realtime=True)

                        if current_price == 0:
                            logger.warning(f"无法获取 {symbol} 的价格，跳过订单 {order_id}")
                            continue

                        # 检查是否达到触发条件
                        should_execute = False
                        execute_at_market = False  # 是否以市价执行（超时转市价）
                        position_side = 'LONG' if side == 'OPEN_LONG' else 'SHORT'

                        # 检查超时转市价（从策略配置中读取）
                        strategy_timeout_raw = order.get('strategy_timeout')
                        try:
                            timeout_minutes = int(strategy_timeout_raw) if strategy_timeout_raw else 0
                        except (ValueError, TypeError):
                            timeout_minutes = 0

                        if timeout_minutes > 0:
                            # 使用数据库计算的时间差，避免时区问题
                            elapsed_seconds = order.get('elapsed_seconds', 0) or 0
                            elapsed_minutes = elapsed_seconds / 60
                            timeout_seconds = timeout_minutes * 60

                            if elapsed_seconds >= timeout_seconds:
                                # 超时，检查价格偏离是否过大
                                # 做多：当前价格高于限价太多则取消（避免追高）
                                # 做空：当前价格低于限价太多则取消（避免杀低）
                                max_deviation_pct = Decimal('0.5')  # 最大允许偏离 0.5%

                                if side == 'OPEN_LONG':
                                    deviation_pct = (current_price - limit_price) / limit_price * 100
                                else:  # OPEN_SHORT
                                    deviation_pct = (limit_price - current_price) / limit_price * 100

                                if deviation_pct > max_deviation_pct:
                                    # 价格偏离过大，取消订单
                                    logger.info(f"⏰ 限价单超时取消: {symbol} {position_side} 价格偏离过大 ({deviation_pct:.2f}% > {max_deviation_pct}%), 限价={limit_price}, 当前={current_price}")

                                    # 解冻保证金
                                    frozen_margin = Decimal(str(order.get('margin', 0)))
                                    if frozen_margin > 0:
                                        with connection.cursor() as update_cursor:
                                            update_cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET current_balance = current_balance + %s,
                                                    frozen_balance = frozen_balance - %s
                                                WHERE id = %s""",
                                                (float(frozen_margin), float(frozen_margin), account_id)
                                            )

                                    # 更新订单状态为已取消
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE futures_orders
                                            SET status = 'CANCELLED',
                                                notes = CONCAT(COALESCE(notes, ''), ' TIMEOUT_PRICE_DEVIATION')
                                            WHERE order_id = %s""",
                                            (order_id,)
                                        )

                                    connection.commit()
                                    continue  # 跳过此订单
                                else:
                                    # 价格偏离在可接受范围内，以市价执行
                                    should_execute = True
                                    execute_at_market = True
                                    logger.info(f"⏰ 限价单超时转市价: {symbol} {position_side} 已等待 {elapsed_minutes:.1f} 分钟, 偏离 {deviation_pct:.2f}%")

                        # 如果没有超时，检查价格是否达到限价条件
                        if not should_execute:
                            if side == 'OPEN_LONG':
                                # 做多：当前价格 <= 限价时触发
                                if current_price <= limit_price:
                                    should_execute = True
                                    logger.info(f"✅ 做多限价单触发: {symbol} @ {current_price} <= {limit_price}")
                            elif side == 'OPEN_SHORT':
                                # 做空：当前价格 >= 限价时触发
                                if current_price >= limit_price:
                                    should_execute = True
                                    logger.info(f"✅ 做空限价单触发: {symbol} @ {current_price} >= {limit_price}")
                        
                        if should_execute:
                            # 执行开仓（使用限价作为成交价）
                            try:
                                # 先解冻保证金（因为限价单创建时已经冻结了保证金）
                                # 开仓时会重新冻结，所以这里先解冻避免重复冻结
                                frozen_margin = Decimal(str(order.get('margin', 0)))
                                if frozen_margin > 0:
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE paper_trading_accounts
                                            SET current_balance = current_balance + %s,
                                                frozen_balance = frozen_balance - %s
                                            WHERE id = %s""",
                                            (float(frozen_margin), float(frozen_margin), account_id)
                                        )
                                
                                # 提交解冻操作
                                connection.commit()
                                
                                # 执行开仓
                                # 保留原始订单的来源和信号ID（如果是策略订单）
                                original_source = order.get('order_source', 'limit_order')
                                original_signal_id = order.get('signal_id')

                                # 根据是否超时决定使用限价还是市价
                                if execute_at_market:
                                    # 超时转市价：使用当前市价执行
                                    execution_price = current_price
                                    logger.info(f"⏰ 以市价执行: {symbol} {position_side} @ {current_price} (原限价: {limit_price})")
                                else:
                                    # 正常限价单触发：使用限价
                                    execution_price = limit_price

                                result = self.trading_engine.open_position(
                                    account_id=account_id,
                                    symbol=symbol,
                                    position_side=position_side,
                                    quantity=quantity,
                                    leverage=leverage,
                                    limit_price=execution_price,  # 使用执行价格
                                    stop_loss_price=stop_loss_price,
                                    take_profit_price=take_profit_price,
                                    source=original_source,  # 保留原始来源（strategy 或 limit_order）
                                    signal_id=original_signal_id  # 保留原始信号ID
                                )

                                if result.get('success'):
                                    # 从结果中获取实际的 symbol（确保一致性）
                                    actual_symbol = result.get('symbol', symbol)

                                    # 验证 symbol 是否匹配
                                    if actual_symbol != symbol:
                                        logger.warning(f"⚠️  限价单 {order_id} symbol 不匹配: 订单中为 {symbol}, 开仓结果为 {actual_symbol}")

                                    # 计算已成交价值
                                    executed_value = float(execution_price * quantity)

                                    # 更新订单状态为已成交
                                    # 如果是超时转市价，添加备注
                                    fill_note = 'TIMEOUT_MARKET' if execute_at_market else None
                                    with connection.cursor() as update_cursor:
                                        update_cursor.execute(
                                            """UPDATE futures_orders
                                            SET status = 'FILLED',
                                                executed_quantity = %s,
                                                executed_value = %s,
                                                avg_fill_price = %s,
                                                fill_time = NOW(),
                                                notes = CASE WHEN %s IS NOT NULL THEN %s ELSE notes END
                                            WHERE order_id = %s""",
                                            (float(quantity), executed_value, float(execution_price), fill_note, fill_note, order_id)
                                        )

                                    connection.commit()

                                    if execute_at_market:
                                        logger.info(f"✅ 限价单超时转市价执行成功: {symbol} {position_side} {quantity} @ {execution_price} (原限价: {limit_price})")
                                    else:
                                        logger.info(f"✅ 限价单执行成功: {symbol} {position_side} {quantity} @ {execution_price}")
                                else:
                                    # 如果开仓失败，恢复冻结的保证金
                                    if frozen_margin > 0:
                                        with connection.cursor() as update_cursor:
                                            update_cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET current_balance = current_balance - %s,
                                                    frozen_balance = frozen_balance + %s
                                                WHERE id = %s""",
                                                (float(frozen_margin), float(frozen_margin), account_id)
                                            )
                                        connection.commit()
                                    logger.error(f"❌ 限价单执行失败: {symbol} {position_side} - {result.get('message', '未知错误')}")
                                    
                            except Exception as e:
                                logger.error(f"执行限价单 {order_id} 时出错: {e}")
                                import traceback
                                traceback.print_exc()
                                # 如果出错，尝试恢复冻结的保证金
                                try:
                                    frozen_margin = Decimal(str(order.get('margin', 0)))
                                    if frozen_margin > 0:
                                        with connection.cursor() as update_cursor:
                                            update_cursor.execute(
                                                """UPDATE paper_trading_accounts
                                                SET current_balance = current_balance - %s,
                                                    frozen_balance = frozen_balance + %s
                                                WHERE id = %s""",
                                                (float(frozen_margin), float(frozen_margin), account_id)
                                            )
                                        connection.commit()
                                except:
                                    pass
                                continue
                        else:
                            pass
                            
                    except Exception as e:
                        logger.error(f"处理限价单 {order.get('order_id', 'unknown')} 时出错: {e}")
                        import traceback
                        traceback.print_exc()
                        continue
            finally:
                connection.close()
                
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
        logger.info(f"🔄 合约限价单自动执行服务已启动（间隔: {interval}秒）")
        
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
    
    def stop(self):
        """停止后台任务"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.debug("⏹️  合约限价单自动执行服务已停止")
        
        # 关闭数据库连接
        if self.connection and self.connection.open:
            try:
                self.connection.close()
                # 静默关闭，不打印日志
            except Exception as e:
                logger.warning(f"关闭数据库连接时出错: {e}")
            finally:
                self.connection = None

