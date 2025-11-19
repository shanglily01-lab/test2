"""
策略自动执行服务
定期检查启用的策略，根据EMA信号自动执行买入和平仓操作
"""

import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional
import pymysql
from loguru import logger

from app.trading.futures_trading_engine import FuturesTradingEngine
from app.analyzers.technical_indicators import TechnicalIndicators


class StrategyExecutor:
    """策略自动执行器"""
    
    def __init__(self, db_config: dict, futures_engine: FuturesTradingEngine):
        """
        初始化策略执行器
        
        Args:
            db_config: 数据库配置
            futures_engine: 合约交易引擎
        """
        self.db_config = db_config
        self.futures_engine = futures_engine
        self.running = False
        self.task = None
        self.technical_analyzer = TechnicalIndicators()
        
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
    
    def _load_strategies(self) -> List[Dict]:
        """从localStorage加载策略（暂时从数据库或配置文件加载）"""
        # TODO: 后续可以改为从数据库加载策略
        # 目前策略存储在localStorage，需要通过API获取
        # 这里先返回空列表，由API端点提供策略数据
        return []
    
    async def execute_strategy(self, strategy: Dict, account_id: int = 2) -> Dict:
        """
        执行单个策略
        
        Args:
            strategy: 策略配置
            account_id: 账户ID
            
        Returns:
            执行结果
        """
        try:
            symbols = strategy.get('symbols', [])
            buy_directions = strategy.get('buyDirection', [])
            leverage = strategy.get('leverage', 5)
            buy_signal = strategy.get('buySignals')
            buy_volume_enabled = strategy.get('buyVolumeEnabled', False)
            buy_volume = strategy.get('buyVolume')
            sell_signal = strategy.get('sellSignals')
            sell_volume_enabled = strategy.get('sellVolumeEnabled', False)
            sell_volume = strategy.get('sellVolume')
            position_size = strategy.get('positionSize', 10)
            long_price_type = strategy.get('longPrice', 'market')
            short_price_type = strategy.get('shortPrice', 'market')
            
            if not symbols or not buy_directions or not buy_signal or not sell_signal:
                return {'success': False, 'message': '策略配置不完整'}
            
            # 确定时间周期
            timeframe_map = {
                'ema_5m': '5m',
                'ema_15m': '15m',
                'ema_1h': '1h'
            }
            buy_timeframe = timeframe_map.get(buy_signal, '15m')
            sell_timeframe = timeframe_map.get(sell_signal, '5m')
            
            connection = self._get_connection()
            cursor = connection.cursor()
            
            try:
                results = []
                
                for symbol in symbols:
                    try:
                        # 获取当前持仓
                        cursor.execute("""
                            SELECT * FROM futures_positions 
                            WHERE account_id = %s AND symbol = %s AND status = 'open'
                        """, (account_id, symbol))
                        existing_positions = cursor.fetchall()
                        
                        # 获取最新的K线和技术指标
                        # 买入信号检查
                        cursor.execute("""
                            SELECT k.*, t.* 
                            FROM kline_data k
                            LEFT JOIN technical_indicators_cache t 
                                ON k.symbol = t.symbol 
                                AND k.timeframe = t.timeframe
                                AND k.timestamp = t.updated_at
                            WHERE k.symbol = %s AND k.timeframe = %s
                            ORDER BY k.timestamp DESC
                            LIMIT 2
                        """, (symbol, buy_timeframe))
                        buy_klines = cursor.fetchall()
                        
                        # 卖出信号检查
                        cursor.execute("""
                            SELECT k.*, t.* 
                            FROM kline_data k
                            LEFT JOIN technical_indicators_cache t 
                                ON k.symbol = t.symbol 
                                AND k.timeframe = t.timeframe
                                AND k.timestamp = t.updated_at
                            WHERE k.symbol = %s AND k.timeframe = %s
                            ORDER BY k.timestamp DESC
                            LIMIT 2
                        """, (symbol, sell_timeframe))
                        sell_klines = cursor.fetchall()
                        
                        if not buy_klines or len(buy_klines) < 2:
                            continue
                        
                        # 检查买入信号
                        if len(existing_positions) == 0:
                            # 检查EMA金叉
                            latest_kline = buy_klines[0]
                            prev_kline = buy_klines[1]
                            
                            if latest_kline.get('ema_short') and latest_kline.get('ema_long'):
                                ema_short = float(latest_kline['ema_short'])
                                ema_long = float(latest_kline['ema_long'])
                                prev_ema_short = float(prev_kline.get('ema_short', 0))
                                prev_ema_long = float(prev_kline.get('ema_long', 0))
                                
                                # 金叉检测
                                is_golden_cross = (prev_ema_short <= prev_ema_long and ema_short > ema_long) or \
                                                 (prev_ema_short < prev_ema_long and ema_short >= ema_long)
                                
                                if is_golden_cross:
                                    # 检查成交量条件
                                    volume_ratio = float(latest_kline.get('volume_ratio', 1.0))
                                    volume_ok = True
                                    if buy_volume_enabled and buy_volume:
                                        required_ratio = float(buy_volume)
                                        volume_ok = volume_ratio >= required_ratio
                                    
                                    if volume_ok:
                                        # 执行买入
                                        close_price = float(latest_kline['close_price'])
                                        
                                        # 计算入场价格
                                        entry_price = close_price
                                        if 'long' in buy_directions:
                                            if long_price_type == 'market_minus_0_2':
                                                entry_price = close_price * 0.998
                                            elif long_price_type == 'market_minus_0_4':
                                                entry_price = close_price * 0.996
                                            elif long_price_type == 'market_minus_0_6':
                                                entry_price = close_price * 0.994
                                            elif long_price_type == 'market_minus_0_8':
                                                entry_price = close_price * 0.992
                                            elif long_price_type == 'market_minus_1':
                                                entry_price = close_price * 0.99
                                        
                                        # 确定方向
                                        direction = 'long' if 'long' in buy_directions else 'short'
                                        
                                        # 计算数量
                                        account_info = self.futures_engine.get_account(account_id)
                                        if not account_info or not account_info.get('success'):
                                            continue
                                        
                                        balance = Decimal(str(account_info['data']['current_balance']))
                                        position_value = balance * Decimal(str(position_size)) / Decimal('100')
                                        quantity = (position_value * Decimal(str(leverage))) / Decimal(str(entry_price))
                                        
                                        # 开仓
                                        result = self.futures_engine.open_position(
                                            account_id=account_id,
                                            symbol=symbol,
                                            position_side='LONG' if direction == 'long' else 'SHORT',
                                            quantity=quantity,
                                            leverage=leverage,
                                            source='strategy',
                                            signal_id=strategy.get('id')
                                        )
                                        
                                        if result.get('success'):
                                            results.append({
                                                'symbol': symbol,
                                                'action': 'buy',
                                                'direction': direction,
                                                'price': entry_price,
                                                'quantity': float(quantity),
                                                'success': True
                                            })
                                            logger.info(f"✅ 策略买入: {symbol} {direction} @ {entry_price}")
                        
                        # 检查卖出信号（平仓）
                        if len(existing_positions) > 0:
                            if sell_klines and len(sell_klines) >= 2:
                                latest_sell_kline = sell_klines[0]
                                prev_sell_kline = sell_klines[1]
                                
                                # 检查MA5/EMA5死叉
                                if latest_sell_kline.get('ma5') and latest_sell_kline.get('ema5'):
                                    ma5 = float(latest_sell_kline['ma5'])
                                    ema5 = float(latest_sell_kline['ema5'])
                                    prev_ma5 = float(prev_sell_kline.get('ma5', 0))
                                    prev_ema5 = float(prev_sell_kline.get('ema5', 0))
                                    
                                    # 死叉检测
                                    is_death_cross = (prev_ema5 >= prev_ma5 and ema5 < ma5) or \
                                                    (prev_ema5 > prev_ma5 and ema5 <= ma5)
                                    
                                    if is_death_cross:
                                        # 检查成交量条件
                                        volume_ratio = float(latest_sell_kline.get('volume_ratio', 1.0))
                                        volume_ok = True
                                        if sell_volume_enabled and sell_volume:
                                            required_ratio = float(sell_volume.replace('<', '').replace('≤', ''))
                                            if sell_volume.startswith('<'):
                                                volume_ok = volume_ratio < required_ratio
                                            else:
                                                volume_ok = volume_ratio <= required_ratio
                                        
                                        if volume_ok:
                                            # 平仓所有持仓
                                            for position in existing_positions:
                                                result = self.futures_engine.close_position(
                                                    position_id=position['id'],
                                                    reason='strategy_signal'
                                                )
                                                
                                                if result.get('success'):
                                                    results.append({
                                                        'symbol': symbol,
                                                        'action': 'sell',
                                                        'position_id': position['id'],
                                                        'success': True
                                                    })
                                                    logger.info(f"✅ 策略平仓: {symbol} 持仓ID {position['id']}")
                    
                    except Exception as e:
                        logger.error(f"执行策略时出错 ({symbol}): {e}")
                        continue
                
                return {'success': True, 'results': results}
                
            finally:
                cursor.close()
                connection.close()
                
        except Exception as e:
            logger.error(f"执行策略失败: {e}")
            return {'success': False, 'message': str(e)}
    
    async def check_and_execute_strategies(self):
        """检查并执行所有启用的策略"""
        try:
            # 从API获取启用的策略（通过HTTP请求）
            # 这里暂时返回，由API端点调用
            pass
        except Exception as e:
            logger.error(f"检查策略时出错: {e}")
    
    async def run_loop(self, interval: int = 60):
        """
        运行监控循环
        
        Args:
            interval: 检查间隔（秒），默认60秒
        """
        self.running = True
        logger.info(f"🔄 策略自动执行服务已启动（间隔: {interval}秒）")
        
        try:
            while self.running:
                try:
                    await self.check_and_execute_strategies()
                except Exception as e:
                    logger.error(f"策略执行循环出错: {e}")
                
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            logger.info("策略执行服务已取消")
            raise
    
    def start(self, interval: int = 60):
        """启动后台任务"""
        if self.running:
            logger.warning("策略执行器已在运行")
            return
        
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

