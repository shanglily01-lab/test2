"""
策略命中记录服务
实时记录策略信号命中情况并保存到数据库
"""

import pymysql
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional
from loguru import logger
from decimal import Decimal


class StrategyHitRecorder:
    """策略命中记录器"""
    
    def __init__(self, db_config: dict):
        """
        初始化策略命中记录器
        
        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.local_tz = timezone(timedelta(hours=8))
    
    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
    
    def record_signal_hit(
        self,
        strategy: Dict,
        symbol: str,
        signal_type: str,  # 'BUY_LONG', 'BUY_SHORT', 'SELL'
        signal_source: str,  # 'ema_9_26', 'ma_ema5', 'ma_ema10'
        signal_timeframe: str,  # '5m', '15m', '1h'
        kline_data: Dict,
        direction: Optional[str] = None,
        executed: bool = False,
        execution_result: Optional[str] = None,
        execution_reason: Optional[str] = None,
        position_id: Optional[int] = None,
        order_id: Optional[str] = None,
        **kwargs
    ) -> Optional[int]:
        """记录策略信号命中"""
        logger.info(f"📝 record_signal_hit 被调用: 策略={strategy.get('name') if strategy else 'None'}, 交易对={symbol}, 信号={signal_type}")
        """
        记录策略信号命中
        
        Args:
            strategy: 策略配置
            symbol: 交易对
            signal_type: 信号类型
            signal_source: 信号来源
            signal_timeframe: 信号时间周期
            kline_data: K线数据（包含技术指标值）
            direction: 交易方向
            executed: 是否已执行
            execution_result: 执行结果
            execution_reason: 执行原因
            position_id: 持仓ID
            order_id: 订单ID
            **kwargs: 其他信息（如成交量条件、过滤结果等）
        
        Returns:
            保存成功返回 hit_id，失败返回 None
        """
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            try:
                # 获取当前时间（本地时间）
                signal_timestamp = datetime.now(self.local_tz).replace(tzinfo=None)
                
                # 计算信号强度
                ema_cross_strength_pct = None
                ma10_ema10_strength_pct = None
                
                if kline_data.get('ema_short') and kline_data.get('ema_long'):
                    ema_short = float(kline_data['ema_short'])
                    ema_long = float(kline_data['ema_long'])
                    if ema_long > 0:
                        ema_cross_strength_pct = abs((ema_short - ema_long) / ema_long * 100)
                
                if kline_data.get('ma10') and kline_data.get('ema10'):
                    ma10 = float(kline_data['ma10'])
                    ema10 = float(kline_data['ema10'])
                    if ma10 > 0:
                        ma10_ema10_strength_pct = abs((ema10 - ma10) / ma10 * 100)
                
                # 插入记录
                insert_sql = """
                    INSERT INTO strategy_hits (
                        strategy_id, strategy_name, symbol, account_id,
                        signal_type, signal_source, signal_timeframe, signal_timestamp,
                        ema_short, ema_long, ma10, ema10, ma5, ema5, current_price,
                        ema_cross_strength_pct, ma10_ema10_strength_pct,
                        volume_ratio, volume_condition_met,
                        ma10_ema10_trend_ok, trend_confirm_ok, signal_strength_ok,
                        executed, execution_result, execution_reason,
                        position_id, order_id, direction, leverage, position_size_pct
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                """
                
                cursor.execute(insert_sql, (
                    strategy.get('id'),
                    strategy.get('name', '未知策略'),
                    symbol,
                    strategy.get('account_id', 2),
                    signal_type,
                    signal_source,
                    signal_timeframe,
                    signal_timestamp,
                    kline_data.get('ema_short'),
                    kline_data.get('ema_long'),
                    kline_data.get('ma10'),
                    kline_data.get('ema10'),
                    kline_data.get('ma5'),
                    kline_data.get('ema5'),
                    float(kline_data.get('close_price', 0)),
                    ema_cross_strength_pct,
                    ma10_ema10_strength_pct,
                    kwargs.get('volume_ratio'),
                    kwargs.get('volume_condition_met'),
                    kwargs.get('ma10_ema10_trend_ok'),
                    kwargs.get('trend_confirm_ok'),
                    kwargs.get('signal_strength_ok'),
                    executed,
                    execution_result,
                    execution_reason,
                    position_id,
                    order_id,
                    direction,
                    strategy.get('leverage'),
                    strategy.get('positionSize')
                ))
                
                connection.commit()
                hit_id = cursor.lastrowid
                
                logger.info(f"✅ 策略命中记录已保存: 策略={strategy.get('name')}, 交易对={symbol}, 信号={signal_type}, ID={hit_id}")
                logger.info(f"   数据库: {self.db_config.get('database')}, 表: strategy_hits, 时间: {signal_timestamp}")
                return hit_id
                
            except Exception as e:
                connection.rollback()
                logger.error(f"❌ 保存策略命中记录失败: {e}")
                logger.error(f"   策略: {strategy.get('name', '未知')}, 交易对: {symbol}, 信号: {signal_type}")
                import traceback
                logger.error(f"   错误详情:\n{traceback.format_exc()}")
                return None
            finally:
                cursor.close()
                connection.close()
                
        except Exception as e:
            logger.error(f"❌ 记录策略命中时出错: {e}")
            logger.error(f"   策略: {strategy.get('name', '未知') if strategy else 'None'}, 交易对: {symbol}, 信号: {signal_type}")
            import traceback
            logger.error(f"   错误详情:\n{traceback.format_exc()}")
            return None
    
    def update_execution_result(
        self,
        hit_id: int,
        executed: bool,
        execution_result: str,
        execution_reason: Optional[str] = None,
        position_id: Optional[int] = None,
        order_id: Optional[str] = None
    ) -> bool:
        """
        更新策略命中记录的执行结果
        
        Args:
            hit_id: 命中记录ID
            executed: 是否已执行
            execution_result: 执行结果
            execution_reason: 执行原因
            position_id: 持仓ID
            order_id: 订单ID
        
        Returns:
            是否更新成功
        """
        try:
            connection = self._get_connection()
            cursor = connection.cursor()
            
            try:
                update_sql = """
                    UPDATE strategy_hits
                    SET executed = %s,
                        execution_result = %s,
                        execution_reason = %s,
                        position_id = %s,
                        order_id = %s,
                        updated_at = NOW()
                    WHERE id = %s
                """
                
                cursor.execute(update_sql, (
                    executed,
                    execution_result,
                    execution_reason,
                    position_id,
                    order_id,
                    hit_id
                ))
                
                connection.commit()
                logger.debug(f"策略命中记录已更新: ID={hit_id}, 执行结果={execution_result}")
                return True
                
            except Exception as e:
                connection.rollback()
                logger.error(f"更新策略命中记录失败: {e}")
                return False
            finally:
                cursor.close()
                connection.close()
                
        except Exception as e:
            logger.error(f"更新策略命中记录时出错: {e}")
            return False

