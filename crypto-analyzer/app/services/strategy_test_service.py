"""
策略测试服务
用于回测交易策略，模拟24小时的交易并计算盈亏
测试结果会保存到数据库中
"""

import pymysql
import pandas as pd
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from loguru import logger
from fastapi import HTTPException
from app.database.db_service import DatabaseService


class StrategyTestService:
    """策略测试服务"""
    
    def __init__(self, db_config: Dict, technical_analyzer=None):
        """
        初始化策略测试服务
        
        Args:
            db_config: 数据库配置
            technical_analyzer: 技术分析器实例（可选）
        """
        self.db_config = db_config
        self.technical_analyzer = technical_analyzer
        
        # 定义本地时区（UTC+8）
        self.LOCAL_TZ = timezone(timedelta(hours=8))
        
        # 初始化数据库服务，用于保存交易记录
        try:
            db_service_config = {
                'type': 'mysql',
                'mysql': db_config
            }
            self.db_service = DatabaseService(db_service_config)
        except Exception as e:
            logger.warning(f"初始化数据库服务失败，交易记录将不会保存: {e}")
            self.db_service = None
    
    def get_local_time(self) -> datetime:
        """获取本地时间（UTC+8）"""
        return datetime.now(self.LOCAL_TZ).replace(tzinfo=None)
    
    def _save_trade_record(self, symbol: str, action: str, direction: str, entry_price: float, 
                          exit_price: float, quantity: float, leverage: int, fee: float,
                          realized_pnl: float, strategy_id, strategy_name: str, 
                          account_id: int, reason: str, trade_time: datetime):
        """保存交易记录到数据库的辅助方法"""
        if not self.db_service:
            logger.warning(f"{symbol} 数据库服务未初始化，无法保存测试交易记录")
            return
        
        try:
            margin = (entry_price * quantity) / leverage if entry_price and quantity else None
            total_value = (exit_price or entry_price) * quantity if quantity else None
            
            trade_record_data = {
                'strategy_id': strategy_id,
                'strategy_name': strategy_name,
                'account_id': account_id,
                'symbol': symbol,
                'action': action,
                'direction': direction,
                'position_side': 'LONG' if direction == 'long' else 'SHORT',
                'entry_price': entry_price,
                'exit_price': exit_price,
                'quantity': quantity,
                'leverage': leverage,
                'margin': margin,
                'total_value': total_value,
                'fee': fee,
                'realized_pnl': realized_pnl,
                'position_id': None,
                'order_id': None,
                'signal_id': None,
                'reason': reason,
                'trade_time': trade_time if trade_time else self.get_local_time()
            }
            # 明确调用测试记录保存方法，保存到 strategy_test_records 表
            logger.debug(f"{symbol} 准备保存测试交易记录到 strategy_test_records 表: {action} {direction} {quantity} @ {entry_price}")
            success = self.db_service.save_strategy_test_record(trade_record_data)
            if success:
                logger.info(f"{symbol} ✅ 保存测试交易记录成功到 strategy_test_records 表: {action} {direction} {quantity} @ {entry_price}")
            else:
                logger.error(f"{symbol} ❌ 保存测试交易记录失败: 返回False")
        except Exception as e:
            logger.error(f"{symbol} ❌ 保存测试交易记录失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def get_quantity_precision(self, symbol: str) -> int:
        """根据交易对获取数量精度（小数位数）"""
        symbol_upper = symbol.upper().replace('/', '')
        if 'PUMP' in symbol_upper or 'DOGE' in symbol_upper:
            return 8
        return 8
    
    def round_quantity(self, quantity: float, symbol: str) -> float:
        """根据交易对精度对数量进行四舍五入"""
        precision = self.get_quantity_precision(symbol)
        return round(quantity, precision)
    
    def parse_time(self, t):
        """解析时间，保持UTC时间用于计算和比较"""
        if isinstance(t, str):
            try:
                return datetime.strptime(t, '%Y-%m-%d %H:%M:%S')
            except:
                try:
                    return datetime.strptime(t, '%Y-%m-%dT%H:%M:%S')
                except:
                    try:
                        return datetime.strptime(t, '%Y-%m-%d %H:%M:%S.%f')
                    except:
                        return datetime.now(timezone.utc).replace(tzinfo=None)
        elif isinstance(t, datetime):
            if t.tzinfo is not None:
                return t.astimezone(timezone.utc).replace(tzinfo=None)
            return t
        else:
            return datetime.now(timezone.utc).replace(tzinfo=None)
    
    def utc_to_local(self, utc_dt):
        """将UTC时间转换为本地时间（仅用于显示）"""
        if utc_dt is None:
            return None
        if isinstance(utc_dt, datetime):
            if utc_dt.tzinfo is None:
                dt_utc = utc_dt.replace(tzinfo=timezone.utc)
                dt_local = dt_utc.astimezone(self.LOCAL_TZ).replace(tzinfo=None)
                return dt_local
            else:
                return utc_dt.astimezone(self.LOCAL_TZ).replace(tzinfo=None)
        return utc_dt
    
    async def test_strategy(self, request: dict) -> Dict:
        """
        测试策略：模拟最近3天的EMA合约交易下单并计算盈亏
        
        Args:
            request: 包含策略配置的字典
        
        Returns:
            测试结果，包含交易记录和盈亏统计
        """
        # 初始化 results，确保在所有情况下都有定义
        results = []
        
        try:
            connection = pymysql.connect(**self.db_config)
            cursor = connection.cursor(pymysql.cursors.DictCursor)
            
            try:
                symbols = request.get('symbols', [])
                buy_directions = request.get('buyDirection', [])
                leverage = request.get('leverage', 5)
                buy_signal = request.get('buySignals')
                buy_volume_enabled = request.get('buyVolumeEnabled', False)
                buy_volume_long_enabled = request.get('buyVolumeLongEnabled', False)
                buy_volume_short_enabled = request.get('buyVolumeShortEnabled', False)
                buy_volume = request.get('buyVolume')
                buy_volume_long = request.get('buyVolumeLong')
                buy_volume_short = request.get('buyVolumeShort')
                sell_signal = request.get('sellSignals')
                sell_volume_enabled = request.get('sellVolumeEnabled', False)
                sell_volume = request.get('sellVolume')
                sell_volume_long_enabled = request.get('sellVolumeLongEnabled', False)
                sell_volume_short_enabled = request.get('sellVolumeShortEnabled', False)
                sell_volume_long = request.get('sellVolumeLong')
                sell_volume_short = request.get('sellVolumeShort')
                position_size = request.get('positionSize', 10)
                max_positions = request.get('maxPositions')  # 最大持仓数
                max_long_positions = request.get('maxLongPositions')  # 最大做多持仓数
                max_short_positions = request.get('maxShortPositions')  # 最大做空持仓数
                long_price_type = request.get('longPrice', 'market')
                short_price_type = request.get('shortPrice', 'market')
                stop_loss_pct = request.get('stopLoss')
                take_profit_pct = request.get('takeProfit')
                ma10_ema10_trend_filter = request.get('ma10Ema10TrendFilter', False)
                min_ema_cross_strength = request.get('minEMACrossStrength', 0.0)
                min_ma10_cross_strength = request.get('minMA10CrossStrength', 0.0)
                # 新的信号强度配置（优先级高于旧格式）
                min_signal_strength = request.get('minSignalStrength', {})
                if min_signal_strength:
                    min_ema_cross_strength = max(min_ema_cross_strength, min_signal_strength.get('ema9_26', 0.0))
                    min_ma10_cross_strength = max(min_ma10_cross_strength, min_signal_strength.get('ma10_ema10', 0.0))
                trend_confirm_bars = request.get('trendConfirmBars', 0)
                exit_on_ma_flip = request.get('exitOnMAFlip', False)  # MA10/EMA10反转时立即平仓
                exit_on_ma_flip_threshold = request.get('exitOnMAFlipThreshold', 0.1)  # MA10/EMA10反转阈值（%），避免小幅波动触发
                exit_on_ema_weak = request.get('exitOnEMAWeak', False)  # EMA差值<0.05%时平仓
                exit_on_ema_weak_threshold = request.get('exitOnEMAWeakThreshold', 0.05)  # EMA弱信号阈值（%），默认0.05%
                early_stop_loss_pct = request.get('earlyStopLossPct', None)  # 早期止损百分比，基于EMA差值或价格回撤
                trend_confirm_ema_threshold = request.get('trendConfirmEMAThreshold', 0.0)  # 趋势确认EMA差值阈值（%），增强趋势确认
                prevent_duplicate_entry = request.get('preventDuplicateEntry', False)  # 防止重复开仓
                close_opposite_on_entry = request.get('closeOppositeOnEntry', False)  # 开仓前先平掉相反方向的持仓
                min_holding_time_hours = request.get('minHoldingTimeHours', 0)  # 最小持仓时间（小时）
                fee_rate = request.get('feeRate', 0.0004)
                
                # 新指标过滤配置
                rsi_filter = request.get('rsiFilter', {})
                rsi_filter_enabled = rsi_filter.get('enabled', False) if isinstance(rsi_filter, dict) else False
                rsi_long_max = rsi_filter.get('longMax', 70) if isinstance(rsi_filter, dict) else 70
                rsi_short_min = rsi_filter.get('shortMin', 30) if isinstance(rsi_filter, dict) else 30
                
                macd_filter = request.get('macdFilter', {})
                macd_filter_enabled = macd_filter.get('enabled', False) if isinstance(macd_filter, dict) else False
                macd_long_require_positive = macd_filter.get('longRequirePositive', True) if isinstance(macd_filter, dict) else True
                macd_short_require_negative = macd_filter.get('shortRequireNegative', True) if isinstance(macd_filter, dict) else True
                
                kdj_filter = request.get('kdjFilter', {})
                kdj_filter_enabled = kdj_filter.get('enabled', False) if isinstance(kdj_filter, dict) else False
                kdj_long_max_k = kdj_filter.get('longMaxK', 80) if isinstance(kdj_filter, dict) else 80
                kdj_short_min_k = kdj_filter.get('shortMinK', 20) if isinstance(kdj_filter, dict) else 20
                kdj_allow_strong_signal = kdj_filter.get('allowStrongSignal', False) if isinstance(kdj_filter, dict) else False
                kdj_strong_signal_threshold = kdj_filter.get('strongSignalThreshold', 1.0) if isinstance(kdj_filter, dict) else 1.0
                
                bollinger_filter = request.get('bollingerFilter', {})
                bollinger_filter_enabled = bollinger_filter.get('enabled', False) if isinstance(bollinger_filter, dict) else False
                
                # 确定买入和卖出的时间周期
                timeframe_map = {
                    'ema_5m': '5m',
                    'ema_15m': '15m',
                    'ema_1h': '1h',
                    'ma_ema5': '5m',
                    'ma_ema10': '5m'
                }
                buy_timeframe = timeframe_map.get(buy_signal, '15m')
                sell_timeframe = timeframe_map.get(sell_signal, '5m')
                
                # 计算时间范围：最近24小时
                now_local = datetime.now(self.LOCAL_TZ).replace(tzinfo=None)
                end_time_local = now_local
                
                # 计算24小时前的起始时间
                start_time_local = now_local - timedelta(hours=24)
                
                # 转换为UTC时间用于数据库查询
                end_time_utc = end_time_local.replace(tzinfo=self.LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
                start_time_utc = start_time_local.replace(tzinfo=self.LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
                
                end_time = end_time_utc
                start_time = start_time_utc
                
                # results 已在函数开始处初始化，这里重置为空列表
                results = []
                
                for symbol in symbols:
                    # 获取买入和卖出时间周期的K线数据
                    extended_start_time = start_time - timedelta(days=30)
                    
                    # 获取买入时间周期的K线数据
                    cursor.execute(
                        """SELECT timestamp, open_price, high_price, low_price, close_price, volume 
                        FROM kline_data 
                        WHERE symbol = %s AND timeframe = %s 
                        AND timestamp >= %s AND timestamp <= %s
                        ORDER BY timestamp ASC""",
                        (symbol, buy_timeframe, extended_start_time, end_time)
                    )
                    buy_klines = cursor.fetchall()
                    
                    # 获取卖出时间周期的K线数据
                    cursor.execute(
                        """SELECT timestamp, open_price, high_price, low_price, close_price, volume 
                        FROM kline_data 
                        WHERE symbol = %s AND timeframe = %s 
                        AND timestamp >= %s AND timestamp <= %s
                        ORDER BY timestamp ASC""",
                        (symbol, sell_timeframe, extended_start_time, end_time)
                    )
                    sell_klines = cursor.fetchall()
                    
                    # 根据时间周期确定最小K线数量要求
                    min_klines_map = {
                        '5m': 100,
                        '15m': 100,
                        '1h': 50,
                        '4h': 50,
                        '1d': 50
                    }
                    min_buy_klines = min_klines_map.get(buy_timeframe, 50)
                    min_sell_klines = min_klines_map.get(sell_timeframe, 50)
                    
                    if not buy_klines or len(buy_klines) < min_buy_klines:
                        results.append({
                            'symbol': symbol,
                            'error': f'买入时间周期({buy_timeframe})K线数据不足（仅{len(buy_klines) if buy_klines else 0}条，至少需要{min_buy_klines}条）',
                            'klines_count': len(buy_klines) if buy_klines else 0
                        })
                        continue
                    
                    if not sell_klines or len(sell_klines) < min_sell_klines:
                        results.append({
                            'symbol': symbol,
                            'error': f'卖出时间周期({sell_timeframe})K线数据不足（仅{len(sell_klines) if sell_klines else 0}条，至少需要{min_sell_klines}条）',
                            'klines_count': len(sell_klines) if sell_klines else 0
                        })
                        continue
                    
                    # 筛选出最近48小时内的K线用于回测
                    buy_test_klines = [k for k in buy_klines if self.parse_time(k['timestamp']) >= start_time]
                    sell_test_klines = [k for k in sell_klines if self.parse_time(k['timestamp']) >= start_time]
                    
                    if len(buy_test_klines) < 10:
                        results.append({
                            'symbol': symbol,
                            'error': f'最近48小时内买入时间周期({buy_timeframe})K线数据不足（仅{len(buy_test_klines)}条）',
                            'klines_count': len(buy_test_klines)
                        })
                        continue
                    
                    if len(sell_test_klines) < 10:
                        results.append({
                            'symbol': symbol,
                            'error': f'最近48小时内卖出时间周期({sell_timeframe})K线数据不足（仅{len(sell_test_klines)}条）',
                            'klines_count': len(sell_test_klines)
                        })
                        continue
                    
                    # 调用内部方法执行测试逻辑
                    result = await self._test_symbol_strategy(
                        symbol=symbol,
                        buy_klines=buy_klines,
                        sell_klines=sell_klines,
                        buy_test_klines=buy_test_klines,
                        sell_test_klines=sell_test_klines,
                        buy_timeframe=buy_timeframe,
                        sell_timeframe=sell_timeframe,
                        start_time=start_time,
                        start_time_local=start_time_local,
                        end_time_local=end_time_local,
                        buy_directions=buy_directions,
                        leverage=leverage,
                        buy_signal=buy_signal,
                        buy_volume_enabled=buy_volume_enabled,
                        buy_volume_long_enabled=buy_volume_long_enabled,
                        buy_volume_short_enabled=buy_volume_short_enabled,
                        buy_volume=buy_volume,
                        buy_volume_long=buy_volume_long,
                        buy_volume_short=buy_volume_short,
                        sell_signal=sell_signal,
                        sell_volume_enabled=sell_volume_enabled,
                        sell_volume=sell_volume,
                        sell_volume_long_enabled=sell_volume_long_enabled,
                        sell_volume_short_enabled=sell_volume_short_enabled,
                        sell_volume_long=sell_volume_long,
                        sell_volume_short=sell_volume_short,
                        position_size=position_size,
                        max_positions=max_positions,
                        long_price_type=long_price_type,
                        short_price_type=short_price_type,
                        stop_loss_pct=stop_loss_pct,
                        take_profit_pct=take_profit_pct,
                        ma10_ema10_trend_filter=ma10_ema10_trend_filter,
                        min_ema_cross_strength=min_ema_cross_strength,
                        min_ma10_cross_strength=min_ma10_cross_strength,
                        trend_confirm_bars=trend_confirm_bars,
                        trend_confirm_ema_threshold=trend_confirm_ema_threshold,
                        exit_on_ma_flip=exit_on_ma_flip,
                        strategy_id=request.get('id'),
                        strategy_name=request.get('name', '测试策略'),
                        account_id=0,  # 测试时使用0作为账户ID
                        exit_on_ma_flip_threshold=exit_on_ma_flip_threshold,
                        exit_on_ema_weak=exit_on_ema_weak,
                        exit_on_ema_weak_threshold=exit_on_ema_weak_threshold,
                        early_stop_loss_pct=early_stop_loss_pct,
                        prevent_duplicate_entry=prevent_duplicate_entry,
                        close_opposite_on_entry=close_opposite_on_entry,
                        min_holding_time_hours=min_holding_time_hours,
                        fee_rate=fee_rate,
                        max_long_positions=max_long_positions,
                        max_short_positions=max_short_positions,
                        rsi_filter_enabled=rsi_filter_enabled,
                        rsi_long_max=rsi_long_max,
                        rsi_short_min=rsi_short_min,
                        macd_filter_enabled=macd_filter_enabled,
                        macd_long_require_positive=macd_long_require_positive,
                        macd_short_require_negative=macd_short_require_negative,
                        kdj_filter_enabled=kdj_filter_enabled,
                        kdj_long_max_k=kdj_long_max_k,
                        kdj_short_min_k=kdj_short_min_k,
                        kdj_allow_strong_signal=kdj_allow_strong_signal,
                        kdj_strong_signal_threshold=kdj_strong_signal_threshold,
                        bollinger_filter_enabled=bollinger_filter_enabled
                    )
                    
                    results.append(result)
                
                # 转换结果中的 datetime 对象为字符串
                def convert_datetime_to_str(obj):
                    """递归转换 datetime 对象为字符串"""
                    if isinstance(obj, dict):
                        return {key: convert_datetime_to_str(value) for key, value in obj.items()}
                    elif isinstance(obj, list):
                        return [convert_datetime_to_str(item) for item in obj]
                    elif isinstance(obj, datetime):
                        return obj.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        return obj
                
                results = convert_datetime_to_str(results)
                
                # 计算测试结果汇总
                total_symbols = len(results)
                total_trades = sum(r.get('trades_count', 0) for r in results if not r.get('error'))
                winning_trades = 0
                losing_trades = 0
                total_pnl = 0
                total_pnl_percent = 0
                initial_balance = 10000.00
                final_balance = initial_balance
                
                for r in results:
                    if not r.get('error'):
                        trades = r.get('trades', [])
                        sell_trades = [t for t in trades if t.get('type') == 'SELL' and t.get('pnl') is not None]
                        winning_trades += len([t for t in sell_trades if t.get('pnl', 0) > 0])
                        losing_trades += len([t for t in sell_trades if t.get('pnl', 0) < 0])
                        total_pnl += r.get('total_pnl', 0)
                        if r.get('final_balance'):
                            final_balance = max(final_balance, r.get('final_balance', initial_balance))
                
                win_rate = (winning_trades / (winning_trades + losing_trades) * 100) if (winning_trades + losing_trades) > 0 else 0
                total_pnl_percent = ((final_balance - initial_balance) / initial_balance * 100) if initial_balance > 0 else 0
                
                # 保存测试结果到数据库
                test_result_id = None
                try:
                    import json
                    test_duration_hours = (end_time_local - start_time_local).total_seconds() / 3600
                    
                    # 插入主表
                    cursor.execute("""
                        INSERT INTO strategy_test_results 
                        (strategy_id, strategy_name, strategy_config, test_start_time, test_end_time, 
                         test_duration_hours, total_symbols, total_trades, winning_trades, losing_trades, 
                         win_rate, initial_balance, final_balance, total_pnl, total_pnl_percent, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        request.get('id'),
                        request.get('name', '未命名策略'),
                        json.dumps(request, ensure_ascii=False),
                        start_time_local,
                        end_time_local,
                        test_duration_hours,
                        total_symbols,
                        total_trades,
                        winning_trades,
                        losing_trades,
                        win_rate,
                        initial_balance,
                        final_balance,
                        total_pnl,
                        total_pnl_percent,
                        'completed'
                    ))
                    test_result_id = cursor.lastrowid
                    connection.commit()
                    
                    # 插入详情表
                    for r in results:
                        symbol = r.get('symbol')
                        if not symbol:
                            continue
                        
                        trades = r.get('trades', [])
                        buy_count = len([t for t in trades if t.get('type') == 'BUY'])
                        sell_count = len([t for t in trades if t.get('type') == 'SELL'])
                        sell_trades = [t for t in trades if t.get('type') == 'SELL' and t.get('pnl') is not None]
                        symbol_winning = len([t for t in sell_trades if t.get('pnl', 0) > 0])
                        symbol_losing = len([t for t in sell_trades if t.get('pnl', 0) < 0])
                        symbol_win_rate = (symbol_winning / (symbol_winning + symbol_losing) * 100) if (symbol_winning + symbol_losing) > 0 else 0
                        
                        # 提取调试信息
                        debug_info = r.get('debug_info', [])
                        debug_info_json = json.dumps(debug_info, ensure_ascii=False) if debug_info else None
                        debug_info_count = len(debug_info) if debug_info else 0
                        
                        # 检查debug_info字段是否存在
                        cursor.execute("""
                            SELECT COLUMN_NAME 
                            FROM INFORMATION_SCHEMA.COLUMNS 
                            WHERE TABLE_SCHEMA = DATABASE() 
                            AND TABLE_NAME = 'strategy_test_result_details' 
                            AND COLUMN_NAME = 'debug_info'
                        """)
                        has_debug_info_column = cursor.fetchone() is not None
                        
                        if has_debug_info_column:
                            # 如果字段存在，使用包含debug_info的SQL
                            cursor.execute("""
                                INSERT INTO strategy_test_result_details
                                (test_result_id, symbol, trades_count, buy_count, sell_count, 
                                 winning_trades, losing_trades, win_rate, initial_balance, final_balance,
                                 total_pnl, total_pnl_percent, golden_cross_count, death_cross_count,
                                 klines_count, indicators_count, error_message, test_result_data, 
                                 debug_info, debug_info_count)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                test_result_id,
                                symbol,
                                r.get('trades_count', 0),
                                buy_count,
                                sell_count,
                                symbol_winning,
                                symbol_losing,
                                symbol_win_rate,
                                r.get('initial_balance', initial_balance),
                                r.get('final_balance', initial_balance),
                                r.get('total_pnl', 0),
                                r.get('total_pnl_pct', 0),
                                r.get('golden_cross_count', 0),
                                r.get('death_cross_count', 0),
                                r.get('klines_count', 0),
                                r.get('indicators_count', 0),
                                r.get('error'),
                                json.dumps(r, ensure_ascii=False) if not r.get('error') else None,
                                debug_info_json,
                                debug_info_count
                            ))
                        else:
                            # 如果字段不存在，使用不包含debug_info的SQL（调试信息会保存在test_result_data中）
                            cursor.execute("""
                                INSERT INTO strategy_test_result_details
                                (test_result_id, symbol, trades_count, buy_count, sell_count, 
                                 winning_trades, losing_trades, win_rate, initial_balance, final_balance,
                                 total_pnl, total_pnl_percent, golden_cross_count, death_cross_count,
                                 klines_count, indicators_count, error_message, test_result_data)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """, (
                                test_result_id,
                                symbol,
                                r.get('trades_count', 0),
                                buy_count,
                                sell_count,
                                symbol_winning,
                                symbol_losing,
                                symbol_win_rate,
                                r.get('initial_balance', initial_balance),
                                r.get('final_balance', initial_balance),
                                r.get('total_pnl', 0),
                                r.get('total_pnl_pct', 0),
                                r.get('golden_cross_count', 0),
                                r.get('death_cross_count', 0),
                                r.get('klines_count', 0),
                                r.get('indicators_count', 0),
                                r.get('error'),
                                json.dumps(r, ensure_ascii=False) if not r.get('error') else None
                            ))
                    connection.commit()
                    logger.info(f"策略测试结果已保存到数据库，测试ID: {test_result_id}")
                    
                except Exception as e:
                    logger.error(f"保存测试结果到数据库失败: {e}", exc_info=True)
                    connection.rollback()
                    # 即使保存失败，也返回测试结果
                
                return {
                    'success': True,
                    'data': results,
                    'test_result_id': test_result_id,
                    'summary': {
                        'total_symbols': total_symbols,
                        'total_trades': total_trades,
                        'winning_trades': winning_trades,
                        'losing_trades': losing_trades,
                        'win_rate': round(win_rate, 2),
                        'total_pnl': round(total_pnl, 2),
                        'total_pnl_percent': round(total_pnl_percent, 2),
                        'initial_balance': initial_balance,
                        'final_balance': round(final_balance, 2)
                    }
                }
                
            finally:
                cursor.close()
                connection.close()
                
        except Exception as e:
            logger.error(f"策略测试失败: {e}", exc_info=True)
            # 确保 results 已初始化，如果未初始化则设置为空列表
            if 'results' not in locals():
                results = []
            # 返回错误信息，而不是直接抛出异常，以便前端能正确处理
            return {
                'success': False,
                'error': str(e),
                'data': results
            }
    
    async def _test_symbol_strategy(self, **kwargs) -> Dict:
        """
        测试单个交易对的策略（内部方法）
        这个方法包含了完整的策略回测逻辑
        """
        # 提取参数
        symbol = kwargs.get('symbol')
        buy_klines = kwargs.get('buy_klines', [])
        sell_klines = kwargs.get('sell_klines', [])
        buy_test_klines = kwargs.get('buy_test_klines', [])
        sell_test_klines = kwargs.get('sell_test_klines', [])
        buy_timeframe = kwargs.get('buy_timeframe', '15m')
        sell_timeframe = kwargs.get('sell_timeframe', '5m')
        start_time = kwargs.get('start_time')
        start_time_local = kwargs.get('start_time_local')
        end_time_local = kwargs.get('end_time_local')
        buy_directions = kwargs.get('buy_directions', [])
        leverage = kwargs.get('leverage', 5)
        buy_signal = kwargs.get('buy_signal')
        buy_volume_enabled = kwargs.get('buy_volume_enabled', False)
        buy_volume_long_enabled = kwargs.get('buy_volume_long_enabled', False)
        buy_volume_short_enabled = kwargs.get('buy_volume_short_enabled', False)
        buy_volume = kwargs.get('buy_volume')
        buy_volume_long = kwargs.get('buy_volume_long')
        buy_volume_short = kwargs.get('buy_volume_short')
        sell_signal = kwargs.get('sell_signal')
        sell_volume_enabled = kwargs.get('sell_volume_enabled', False)
        sell_volume = kwargs.get('sell_volume')
        sell_volume_long_enabled = kwargs.get('sell_volume_long_enabled', False)
        sell_volume_short_enabled = kwargs.get('sell_volume_short_enabled', False)
        sell_volume_long = kwargs.get('sell_volume_long')
        sell_volume_short = kwargs.get('sell_volume_short')
        position_size = kwargs.get('position_size', 10)
        max_positions = kwargs.get('max_positions')  # 最大持仓数
        long_price_type = kwargs.get('long_price_type', 'market')
        short_price_type = kwargs.get('short_price_type', 'market')
        stop_loss_pct = kwargs.get('stop_loss_pct')
        take_profit_pct = kwargs.get('take_profit_pct')
        ma10_ema10_trend_filter = kwargs.get('ma10_ema10_trend_filter', False)
        min_ema_cross_strength = kwargs.get('min_ema_cross_strength', 0.0)
        min_ma10_cross_strength = kwargs.get('min_ma10_cross_strength', 0.0)
        trend_confirm_bars = kwargs.get('trend_confirm_bars', 0)
        trend_confirm_bars = kwargs.get('trend_confirm_bars', 0)  # 趋势至少持续K线数（默认0表示不启用）
        trend_confirm_ema_threshold = kwargs.get('trend_confirm_ema_threshold', 0.0)  # 趋势确认EMA差值阈值（%），增强趋势确认
        exit_on_ma_flip = kwargs.get('exit_on_ma_flip', False)  # MA10/EMA10反转时立即平仓
        exit_on_ma_flip_threshold = kwargs.get('exit_on_ma_flip_threshold', 0.1)  # MA10/EMA10反转阈值（%），避免小幅波动触发
        exit_on_ema_weak = kwargs.get('exit_on_ema_weak', False)  # EMA差值<0.05%时平仓
        exit_on_ema_weak_threshold = kwargs.get('exit_on_ema_weak_threshold', 0.05)  # EMA弱信号阈值（%），默认0.05%
        early_stop_loss_pct = kwargs.get('early_stop_loss_pct', None)  # 早期止损百分比，基于EMA差值或价格回撤
        prevent_duplicate_entry = kwargs.get('prevent_duplicate_entry', False)  # 防止重复开仓
        close_opposite_on_entry = kwargs.get('close_opposite_on_entry', False)  # 开仓前先平掉相反方向的持仓
        min_holding_time_hours = kwargs.get('min_holding_time_hours', 0)  # 最小持仓时间（小时）
        fee_rate = kwargs.get('fee_rate', 0.0004)
        max_long_positions = kwargs.get('max_long_positions')  # 最大做多持仓数
        max_short_positions = kwargs.get('max_short_positions')  # 最大做空持仓数
        rsi_filter_enabled = kwargs.get('rsi_filter_enabled', False)
        rsi_long_max = kwargs.get('rsi_long_max', 70)
        rsi_short_min = kwargs.get('rsi_short_min', 30)
        macd_filter_enabled = kwargs.get('macd_filter_enabled', False)
        macd_long_require_positive = kwargs.get('macd_long_require_positive', True)
        macd_short_require_negative = kwargs.get('macd_short_require_negative', True)
        kdj_filter_enabled = kwargs.get('kdj_filter_enabled', False)
        kdj_long_max_k = kwargs.get('kdj_long_max_k', 80)
        kdj_short_min_k = kwargs.get('kdj_short_min_k', 20)
        kdj_allow_strong_signal = kwargs.get('kdj_allow_strong_signal', False)
        kdj_strong_signal_threshold = kwargs.get('kdj_strong_signal_threshold', 1.0)
        bollinger_filter_enabled = kwargs.get('bollinger_filter_enabled', False)
        strategy_id = kwargs.get('strategy_id')
        strategy_name = kwargs.get('strategy_name', '测试策略')
        account_id = kwargs.get('account_id', 0)
        
        # 模拟交易
        trades = []
        positions = []  # 当前持仓
        initial_balance = 10000  # 初始资金
        balance = initial_balance
        debug_info = []  # 调试信息
        
        # 添加调试信息：记录K线时间范围
        if buy_test_klines:
            first_buy_time = self.parse_time(buy_test_klines[0]['timestamp'])
            last_buy_time = self.parse_time(buy_test_klines[-1]['timestamp'])
            debug_info.append(f"📊 买入时间周期({buy_timeframe})K线范围: {first_buy_time.strftime('%Y-%m-%d %H:%M')} 至 {last_buy_time.strftime('%Y-%m-%d %H:%M')}（本地时间 UTC+8），共{len(buy_test_klines)}条")
            debug_info.append(f"📊 测试时间范围: {start_time_local.strftime('%Y-%m-%d %H:%M')} 至 {end_time_local.strftime('%Y-%m-%d %H:%M')}（本地时间 UTC+8）")
        
        # 将K线数据转换为DataFrame格式（用于计算技术指标）
        import pandas as pd
        
        # 为买入时间周期的每个K线计算技术指标
        def calculate_indicators(klines, test_klines, timeframe_name):
            indicator_pairs = []
            for test_kline in test_klines:
                test_kline_time = self.parse_time(test_kline['timestamp'])
                
                # 获取到当前K线为止的所有历史K线（用于计算技术指标）
                historical_klines = [k for k in klines if self.parse_time(k['timestamp']) <= test_kline_time]
                
                # 根据时间周期确定最小历史K线数量
                min_historical_map = {
                    '5m': 100,
                    '15m': 100,
                    '1h': 50,
                    '4h': 50,
                    '1d': 50
                }
                timeframe_key = timeframe_name.split('(')[1].split(')')[0] if '(' in timeframe_name else '15m'
                min_historical = min_historical_map.get(timeframe_key, 50)
                
                if len(historical_klines) < min_historical:
                    continue
                
                # 转换为DataFrame
                df = pd.DataFrame([{
                    'timestamp': self.parse_time(k['timestamp']),
                    'open': float(k['open_price']),
                    'high': float(k['high_price']),
                    'low': float(k['low_price']),
                    'close': float(k['close_price']),
                    'volume': float(k['volume'])
                } for k in historical_klines])
                
                # 使用技术分析器计算指标
                if self.technical_analyzer is None:
                    continue
                
                try:
                    # 计算技术指标
                    indicators_result = self.technical_analyzer.analyze(df)
                    
                    if not indicators_result:
                        continue
                    
                    # 提取需要的指标
                    ema_data = indicators_result.get('ema', {})
                    ma_ema10_data = indicators_result.get('ma_ema10', {})
                    ma_ema5_data = indicators_result.get('ma_ema5', {})
                    volume_data = indicators_result.get('volume', {})
                    rsi_data = indicators_result.get('rsi', {})
                    macd_data = indicators_result.get('macd', {})
                    kdj_data = indicators_result.get('kdj', {})
                    
                    ema_short = ema_data.get('short') if isinstance(ema_data, dict) else None
                    ema_long = ema_data.get('long') if isinstance(ema_data, dict) else None
                    ma10 = ma_ema10_data.get('ma10') if isinstance(ma_ema10_data, dict) else None
                    ema10 = ma_ema10_data.get('ema10') if isinstance(ma_ema10_data, dict) else None
                    ma5 = ma_ema5_data.get('ma5') if isinstance(ma_ema5_data, dict) else None
                    ema5 = ma_ema5_data.get('ema5') if isinstance(ma_ema5_data, dict) else None
                    
                    # volume_ratio在ema字段中，或者从volume字段计算
                    volume_ratio = ema_data.get('volume_ratio', 1.0) if isinstance(ema_data, dict) else 1.0
                    if volume_ratio == 1.0 and isinstance(volume_data, dict):
                        vol_current = volume_data.get('current', 0)
                        vol_ma20 = volume_data.get('ma20', 1)
                        if vol_ma20 > 0:
                            volume_ratio = vol_current / vol_ma20
                    rsi_value = rsi_data.get('value') if isinstance(rsi_data, dict) else None
                    macd_histogram = macd_data.get('histogram') if isinstance(macd_data, dict) else None
                    kdj_k = kdj_data.get('k') if isinstance(kdj_data, dict) else None
                    
                    # 如果无法从analyze结果获取，尝试从DataFrame获取
                    if ema_short is None and 'ema_short' in df.columns:
                        ema_short = float(df['ema_short'].iloc[-1]) if not pd.isna(df['ema_short'].iloc[-1]) else None
                    if ema_long is None and 'ema_long' in df.columns:
                        ema_long = float(df['ema_long'].iloc[-1]) if not pd.isna(df['ema_long'].iloc[-1]) else None
                    if ma10 is None and 'ma10' in df.columns:
                        ma10 = float(df['ma10'].iloc[-1]) if not pd.isna(df['ma10'].iloc[-1]) else None
                    if ema10 is None and 'ema10' in df.columns:
                        ema10 = float(df['ema10'].iloc[-1]) if not pd.isna(df['ema10'].iloc[-1]) else None
                    if volume_ratio == 1.0:
                        if 'volume' in df.columns and 'vol_ma20' in df.columns:
                            vol_current = float(df['volume'].iloc[-1])
                            vol_ma20 = float(df['vol_ma20'].iloc[-1])
                            if vol_ma20 > 0:
                                volume_ratio = vol_current / vol_ma20
                    if rsi_value is None and 'rsi' in df.columns:
                        rsi_value = float(df['rsi'].iloc[-1]) if not pd.isna(df['rsi'].iloc[-1]) else None
                    if macd_histogram is None and 'macd_histogram' in df.columns:
                        macd_histogram = float(df['macd_histogram'].iloc[-1]) if not pd.isna(df['macd_histogram'].iloc[-1]) else None
                    if kdj_k is None and 'kdj_k' in df.columns:
                        kdj_k = float(df['kdj_k'].iloc[-1]) if not pd.isna(df['kdj_k'].iloc[-1]) else None
                    
                    indicator_pairs.append({
                        'kline': test_kline,
                        'indicator': {
                            'ema_short': ema_short,
                            'ema_long': ema_long,
                            'ma10': ma10,
                            'ema10': ema10,
                            'ma5': ma5,
                            'ema5': ema5,
                            'volume_ratio': volume_ratio,
                            'rsi': rsi_value,
                            'macd_histogram': macd_histogram,
                            'kdj_k': kdj_k,
                            'updated_at': test_kline_time
                        }
                    })
                except Exception as e:
                    logger.error(f"计算{timeframe_name}技术指标失败 {symbol} {test_kline_time}: {e}")
                    continue
            
            return indicator_pairs
        
        # 计算买入和卖出时间周期的指标
        buy_indicator_pairs = calculate_indicators(buy_klines, buy_test_klines, f'买入({buy_timeframe})')
        sell_indicator_pairs = calculate_indicators(sell_klines, sell_test_klines, f'卖出({sell_timeframe})')
        
        # 添加调试信息：记录成功计算的指标数量
        if buy_indicator_pairs:
            debug_info.append(f"✅ 买入时间周期({buy_timeframe})成功计算{len(buy_indicator_pairs)}个时间点的技术指标")
        if sell_indicator_pairs:
            debug_info.append(f"✅ 卖出时间周期({sell_timeframe})成功计算{len(sell_indicator_pairs)}个时间点的技术指标")
        
        if len(buy_indicator_pairs) < 2:
            return {
                'symbol': symbol,
                'error': f'买入时间周期({buy_timeframe})技术指标计算失败（K线:{len(buy_test_klines)}条, 成功计算:{len(buy_indicator_pairs)}条）',
                'klines_count': len(buy_test_klines),
                'indicators_count': len(buy_indicator_pairs),
                'matched_pairs_count': len(buy_indicator_pairs)
            }
        
        if len(sell_indicator_pairs) < 2:
            return {
                'symbol': symbol,
                'error': f'卖出时间周期({sell_timeframe})技术指标计算失败（K线:{len(sell_test_klines)}条, 成功计算:{len(sell_indicator_pairs)}条）',
                'klines_count': len(sell_test_klines),
                'indicators_count': len(sell_indicator_pairs),
                'matched_pairs_count': len(sell_indicator_pairs)
            }
        
        # 合并所有时间点，按时间顺序处理
        all_time_points = []
        for pair in buy_indicator_pairs:
            all_time_points.append({
                'time': pair['indicator']['updated_at'],
                'type': 'buy',
                'pair': pair
            })
        for pair in sell_indicator_pairs:
            all_time_points.append({
                'time': pair['indicator']['updated_at'],
                'type': 'sell',
                'pair': pair
            })
        
        # 按时间排序，如果时间相同，先处理卖出（type='sell'排在前面）
        all_time_points.sort(key=lambda x: (x['time'], 0 if x['type'] == 'sell' else 1))
        
        # 记录当前时间点是否已经平仓（用于防止滚仓）
        last_processed_time = None
        closed_at_current_time = False
        
        # 遍历所有时间点
        for time_point in all_time_points:
            current_time = time_point['time']  # UTC时间用于计算和比较
            current_time_local = self.utc_to_local(current_time)  # 本地时间用于显示
            
            # 如果时间点改变，重置平仓标志
            if last_processed_time is None or current_time != last_processed_time:
                closed_at_current_time = False
                last_processed_time = current_time
            
            # 如果是买入时间点，检查买入信号
            if time_point['type'] == 'buy':
                pair = time_point['pair']
                kline = pair['kline']
                indicator = pair['indicator']
                close_price = float(kline['close_price'])
                volume_ratio = float(indicator['volume_ratio']) if indicator.get('volume_ratio') else 1.0
                
                ema_short = float(indicator['ema_short']) if indicator.get('ema_short') else None
                ema_long = float(indicator['ema_long']) if indicator.get('ema_long') else None
                
                if not ema_short or not ema_long:
                    continue
                
                # 检查买入信号（EMA金叉）
                buy_signal_triggered = False
                try:
                    current_buy_index = buy_indicator_pairs.index(pair)
                except ValueError:
                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 无法找到当前K线在买入指标列表中的位置")
                    continue
                
                # 检查买入信号（EMA9/26金叉 和 MA10/EMA10金叉）
                curr_diff = ema_short - ema_long
                curr_diff_pct = (curr_diff / ema_long * 100) if ema_long > 0 else 0
                curr_status = "多头" if ema_short > ema_long else "空头"
                
                # 获取MA10/EMA10数据
                ma10 = float(indicator.get('ma10')) if indicator.get('ma10') else None
                ema10 = float(indicator.get('ema10')) if indicator.get('ema10') else None
                ma10_ema10_diff = (ema10 - ma10) if (ema10 and ma10) else None
                ma10_ema10_diff_pct = (ma10_ema10_diff / ma10 * 100) if (ma10_ema10_diff and ma10 and ma10 > 0) else None
                ma10_ema10_status = "多头" if (ema10 and ma10 and ema10 > ma10) else "空头" if (ema10 and ma10 and ema10 < ma10) else "中性"
                
                # 记录EMA9/26状态
                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: 📊 EMA9/26状态 - {curr_status} | EMA9={ema_short:.4f}, EMA26={ema_long:.4f}, 差值={curr_diff:.4f} ({curr_diff_pct:+.2f}%)")
                
                # 记录MA10/EMA10状态
                if ma10 and ema10:
                    debug_info.append(f"   📊 MA10/EMA10状态 - {ma10_ema10_status} | MA10={ma10:.4f}, EMA10={ema10:.4f}, 差值={ma10_ema10_diff:.4f} ({ma10_ema10_diff_pct:+.2f}%)" if ma10_ema10_diff_pct else f"   📊 MA10/EMA10状态 - {ma10_ema10_status} | MA10={ma10:.4f}, EMA10={ema10:.4f}")
                
                # 检查MA10/EMA10交叉
                ma10_ema10_golden_cross = False
                
                if current_buy_index > 0:
                    # 检查前3个时间点，确保不遗漏交叉信号
                    lookback_count = min(3, current_buy_index)
                    found_golden_cross = False
                    found_death_cross = False
                    detected_cross_type = None  # 'golden' 或 'death'
                    
                    for lookback in range(1, lookback_count + 1):
                        prev_pair = buy_indicator_pairs[current_buy_index - lookback]
                        prev_indicator = prev_pair['indicator']
                        prev_ema_short = float(prev_indicator['ema_short']) if prev_indicator.get('ema_short') else None
                        prev_ema_long = float(prev_indicator['ema_long']) if prev_indicator.get('ema_long') else None
                        prev_ma10 = float(prev_indicator.get('ma10')) if prev_indicator.get('ma10') else None
                        prev_ema10 = float(prev_indicator.get('ema10')) if prev_indicator.get('ema10') else None
                        prev_time = prev_indicator['updated_at']
                        
                        if prev_ema_short and prev_ema_long:
                            # EMA9/26金叉（向上穿越）
                            is_golden_cross = (prev_ema_short <= prev_ema_long and ema_short > ema_long) or \
                                             (prev_ema_short < prev_ema_long and ema_short >= ema_long)
                            
                            # EMA9/26死叉（向下穿越）
                            is_death_cross = (prev_ema_short >= prev_ema_long and ema_short < ema_long) or \
                                             (prev_ema_short > prev_ema_long and ema_short <= ema_long)
                            
                            # MA10/EMA10金叉检测（只在循环外检测一次，避免重复输出）
                            if prev_ma10 and prev_ema10 and ma10 and ema10 and not ma10_ema10_golden_cross:
                                ma10_ema10_is_golden = (prev_ema10 <= prev_ma10 and ema10 > ma10) or \
                                                       (prev_ema10 < prev_ma10 and ema10 >= ma10)
                                if ma10_ema10_is_golden:
                                    # 只在首次检测到MA10/EMA10金叉时输出日志
                                    ma10_ema10_golden_cross = True
                                    debug_info.append(f"   ➕➕➕ MA10/EMA10金叉检测成功！")
                            
                            # 买入信号：根据 buySignals 配置决定使用哪个信号
                            signal_triggered = False
                            
                            if buy_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                                # 检测 EMA9/26 金叉（做多）和死叉（做空）
                                if is_golden_cross and 'long' in buy_directions:
                                    # 金叉 = 做多信号
                                    # 检查信号强度过滤
                                    ema_strength_pct = abs(curr_diff_pct)
                                    if min_ema_cross_strength > 0 and ema_strength_pct < min_ema_cross_strength:
                                        debug_info.append(f"   ⚠️ EMA9/26金叉信号强度不足 (差值={ema_strength_pct:.2f}%, 需要≥{min_ema_cross_strength:.2f}%)，已过滤")
                                        break
                                    
                                    signal_triggered = True
                                    buy_signal_triggered = True
                                    found_golden_cross = True
                                    detected_cross_type = 'golden'
                                    debug_info.append(f"   ✅✅✅ EMA9/26金叉检测成功（做多信号）！")
                                    if min_ema_cross_strength > 0:
                                        debug_info.append(f"   ✅ 信号强度检查通过 (差值={ema_strength_pct:.2f}% ≥ {min_ema_cross_strength:.2f}%)")
                                elif is_death_cross and 'short' in buy_directions:
                                    # 死叉 = 做空信号
                                    # 检查信号强度过滤
                                    ema_strength_pct = abs(curr_diff_pct)
                                    if min_ema_cross_strength > 0 and ema_strength_pct < min_ema_cross_strength:
                                        debug_info.append(f"   ⚠️ EMA9/26死叉信号强度不足 (差值={ema_strength_pct:.2f}%, 需要≥{min_ema_cross_strength:.2f}%)，已过滤")
                                        break
                                    
                                    signal_triggered = True
                                    buy_signal_triggered = True
                                    found_death_cross = True
                                    detected_cross_type = 'death'
                                    debug_info.append(f"   ✅✅✅ EMA9/26死叉检测成功（做空信号）！")
                                    if min_ema_cross_strength > 0:
                                        debug_info.append(f"   ✅ 信号强度检查通过 (差值={ema_strength_pct:.2f}% ≥ {min_ema_cross_strength:.2f}%)")
                            elif buy_signal == 'ma_ema10':
                                # 使用 MA10/EMA10 金叉
                                if ma10_ema10_golden_cross:
                                    # 检查信号强度过滤
                                    if ma10 and ema10:
                                        ma10_ema10_strength_pct = abs(ma10_ema10_diff / ma10 * 100) if ma10 > 0 else 0
                                        if min_ma10_cross_strength > 0 and ma10_ema10_strength_pct < min_ma10_cross_strength:
                                            debug_info.append(f"   ⚠️ MA10/EMA10金叉信号强度不足 (差值={ma10_ema10_strength_pct:.2f}%, 需要≥{min_ma10_cross_strength:.2f}%)，已过滤")
                                            break
                                    
                                    signal_triggered = True
                                    buy_signal_triggered = True
                                    found_golden_cross = True
                                    debug_info.append(f"   ✅✅✅ MA10/EMA10金叉检测成功！")
                            
                            if signal_triggered:
                                debug_info.append(f"   📊 成交量比率: {volume_ratio:.2f}x")
                                break
                
                # 执行买入
                # 检查是否可以开仓：防止重复开仓或检查最大持仓数
                can_open_position = True
                if prevent_duplicate_entry and len(positions) > 0:
                    can_open_position = False
                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 防止重复开仓已启用，当前已有{len(positions)}个持仓，跳过买入信号")
                elif max_positions is not None and len(positions) >= max_positions:
                    can_open_position = False
                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已达到最大持仓数限制（{max_positions}个），当前持仓{len(positions)}个，跳过买入信号")
                
                if buy_signal_triggered and can_open_position and not closed_at_current_time:
                    if len(buy_directions) > 0:
                        # 根据检测到的交叉类型确定方向（金叉=做多，死叉=做空）
                        direction = None
                        
                        if detected_cross_type == 'golden':
                            # 金叉 = 做多
                            direction = 'long'
                            debug_info.append(f"   📊 方向判断：检测到金叉，选择做多")
                        elif detected_cross_type == 'death':
                            # 死叉 = 做空
                            direction = 'short'
                            debug_info.append(f"   📊 方向判断：检测到死叉，选择做空")
                        else:
                            # 如果没有检测到交叉，根据EMA状态判断（兼容旧逻辑）
                            ema_bullish = ema_short > ema_long
                            ma10_ema10_bullish = (ma10 and ema10 and ema10 > ma10) if (ma10 and ema10) else None
                            
                            if len(buy_directions) > 1:
                                if ema_bullish and 'long' in buy_directions:
                                    direction = 'long'
                                    debug_info.append(f"   📊 方向判断：EMA多头，选择做多")
                                elif not ema_bullish and 'short' in buy_directions:
                                    direction = 'short'
                                    debug_info.append(f"   📊 方向判断：EMA空头，选择做空")
                                elif ma10_ema10_bullish is not None:
                                    if ma10_ema10_bullish and 'long' in buy_directions:
                                        direction = 'long'
                                        debug_info.append(f"   📊 方向判断：MA10/EMA10多头，选择做多")
                                    elif not ma10_ema10_bullish and 'short' in buy_directions:
                                        direction = 'short'
                                        debug_info.append(f"   📊 方向判断：MA10/EMA10空头，选择做空")
                                if direction is None:
                                    if 'long' in buy_directions:
                                        direction = 'long'
                                        debug_info.append(f"   📊 方向判断：默认选择做多")
                                    elif 'short' in buy_directions:
                                        direction = 'short'
                                        debug_info.append(f"   📊 方向判断：默认选择做空")
                                    else:
                                        direction = buy_directions[0]
                                        debug_info.append(f"   📊 方向判断：使用第一个方向 {direction}")
                            else:
                                direction = buy_directions[0]
                                debug_info.append(f"   📊 方向判断：单一方向 {direction}")
                        
                        if direction is None:
                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 无法确定交易方向")
                            continue
                        
                        # 检查成交量条件
                        volume_condition_met = True
                        volume_reason = ""
                        if direction == 'long':
                            if buy_volume_enabled and buy_volume_long_enabled:
                                volume_condition = buy_volume_long or buy_volume
                                if volume_condition:
                                    # 支持新的范围格式: 0-1, 1-1.25, 1.25-2, >2
                                    if volume_condition == '0-1':
                                        if not (0 <= volume_ratio <= 1.0):
                                            volume_condition_met = False
                                            volume_reason = f"做多成交量不符合 (当前:{volume_ratio:.2f}x, 需要:0-1x)"
                                    elif volume_condition == '1-1.25':
                                        if not (1.0 <= volume_ratio <= 1.25):
                                            volume_condition_met = False
                                            volume_reason = f"做多成交量不符合 (当前:{volume_ratio:.2f}x, 需要:1-1.25x)"
                                    elif volume_condition == '1.25-2':
                                        if not (1.25 <= volume_ratio <= 2.0):
                                            volume_condition_met = False
                                            volume_reason = f"做多成交量不符合 (当前:{volume_ratio:.2f}x, 需要:1.25-2x)"
                                    elif volume_condition == '>2':
                                        if volume_ratio <= 2.0:
                                            volume_condition_met = False
                                            volume_reason = f"做多成交量不符合 (当前:{volume_ratio:.2f}x, 需要:>2x)"
                                    else:
                                        # 尝试解析为单一数值（兼容旧格式）
                                        try:
                                            required_ratio = float(volume_condition)
                                            if volume_ratio < required_ratio:
                                                volume_condition_met = False
                                                volume_reason = f"做多成交量不足 (当前:{volume_ratio:.2f}x, 需要:≥{required_ratio}x)"
                                        except:
                                            volume_condition_met = False
                                            volume_reason = f"做多成交量条件格式错误: {volume_condition}"
                        else:
                            if buy_volume_enabled and (buy_volume_short_enabled or buy_volume_short):
                                volume_condition = buy_volume_short
                                if volume_condition:
                                    # 支持新的范围格式: 0-0.5, 0.5-1, >1
                                    if volume_condition == '0-0.5':
                                        if not (0 <= volume_ratio <= 0.5):
                                            volume_condition_met = False
                                            volume_reason = f"做空成交量不符合 (当前:{volume_ratio:.2f}x, 需要:0-0.5x)"
                                    elif volume_condition == '0.5-1':
                                        if not (0.5 <= volume_ratio <= 1.0):
                                            volume_condition_met = False
                                            volume_reason = f"做空成交量不符合 (当前:{volume_ratio:.2f}x, 需要:0.5-1x)"
                                    elif volume_condition == '>1':
                                        if volume_ratio <= 1.0:
                                            volume_condition_met = False
                                            volume_reason = f"做空成交量不符合 (当前:{volume_ratio:.2f}x, 需要:>1x)"
                                    else:
                                        # 尝试解析为单一数值（兼容旧格式）
                                        try:
                                            required_ratio = float(volume_condition)
                                            if volume_ratio < required_ratio:
                                                volume_condition_met = False
                                                volume_reason = f"做空成交量不足 (当前:{volume_ratio:.2f}x, 需要:≥{required_ratio}x)"
                                        except (ValueError, TypeError):
                                            volume_condition_met = False
                                            volume_reason = f"做空成交量条件格式错误: {volume_condition}"
                        
                        if not volume_condition_met:
                            signal_type = "EMA金叉" if direction == 'long' else "EMA死叉"
                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ {signal_type}但{volume_reason}")
                            continue
                        
                        # 检查同方向持仓限制
                        if direction == 'long' and max_long_positions is not None:
                            long_positions_count = len([p for p in positions if p['direction'] == 'long'])
                            if long_positions_count >= max_long_positions:
                                can_open_position = False
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已达到最大做多持仓数限制（{max_long_positions}个），当前做多持仓{long_positions_count}个，跳过买入信号")
                                continue
                        elif direction == 'short' and max_short_positions is not None:
                            short_positions_count = len([p for p in positions if p['direction'] == 'short'])
                            if short_positions_count >= max_short_positions:
                                can_open_position = False
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已达到最大做空持仓数限制（{max_short_positions}个），当前做空持仓{short_positions_count}个，跳过买入信号")
                                continue
                        
                        # 开仓前先平掉相反方向的持仓（如果启用）
                        if close_opposite_on_entry:
                            opposite_positions = [p for p in positions if p['direction'] != direction]
                            if opposite_positions:
                                close_price = float(kline['close_price'])
                                for opp_position in opposite_positions[:]:
                                    opp_entry_price = opp_position['entry_price']
                                    opp_quantity = opp_position['quantity']
                                    opp_direction = opp_position['direction']
                                    
                                    # 使用当前价格平仓
                                    exit_price = close_price
                                    
                                    if opp_direction == 'long':
                                        gross_pnl = (exit_price - opp_entry_price) * opp_quantity
                                    else:
                                        gross_pnl = (opp_entry_price - exit_price) * opp_quantity
                                    
                                    close_fee = (exit_price * opp_quantity) * fee_rate
                                    open_fee = opp_position.get('open_fee', 0)
                                    total_fee = open_fee + close_fee
                                    pnl = gross_pnl - total_fee
                                    
                                    margin_used = (opp_entry_price * opp_quantity) / leverage
                                    pnl_pct = (pnl / margin_used) * 100 if margin_used > 0 else 0
                                    
                                    balance += gross_pnl - close_fee
                                    
                                    opp_direction_text = "做多" if opp_direction == 'long' else "做空"
                                    trades.append({
                                        'type': 'SELL',
                                        'direction': opp_direction,
                                        'price': exit_price,
                                        'quantity': opp_quantity,
                                        'time': current_time,
                                        'balance': balance,
                                        'pnl': pnl,
                                        'pnl_pct': pnl_pct,
                                        'fee': close_fee,
                                        'fee_rate': fee_rate,
                                        'exit_reason': f'开{direction}仓前平仓'
                                    })
                                    
                                    # 保存交易记录到数据库
                                    self._save_trade_record(
                                        symbol=symbol,
                                        action='CLOSE',
                                        direction=opp_direction,
                                        entry_price=opp_entry_price,
                                        exit_price=exit_price,
                                        quantity=opp_quantity,
                                        leverage=leverage,
                                        fee=close_fee,
                                        realized_pnl=pnl,
                                        strategy_id=strategy_id,
                                        strategy_name=strategy_name,
                                        account_id=account_id,
                                        reason=f'开{direction}仓前平仓',
                                        trade_time=self.utc_to_local(current_time) if current_time else self.get_local_time()
                                    )
                                    
                                    positions.remove(opp_position)
                                    closed_at_current_time = True
                                    
                                    qty_precision = self.get_quantity_precision(symbol)
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 开{direction}仓前平掉{opp_direction_text}持仓 | 入场价={opp_entry_price:.4f}, 平仓价={exit_price:.4f}, 数量={opp_quantity:.{qty_precision}f}, 实际盈亏={pnl:+.2f} ({pnl_pct:+.2f}%)")
                        
                        # 检查 RSI 过滤
                        if rsi_filter_enabled:
                            rsi_value = float(indicator.get('rsi')) if indicator.get('rsi') else None
                            if rsi_value is not None:
                                if direction == 'long' and rsi_value > rsi_long_max:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ RSI过滤：做多时RSI过高 (RSI={rsi_value:.2f} > {rsi_long_max})，已过滤")
                                    continue
                                elif direction == 'short' and rsi_value < rsi_short_min:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ RSI过滤：做空时RSI过低 (RSI={rsi_value:.2f} < {rsi_short_min})，已过滤")
                                    continue
                        
                        # 检查 MACD 过滤
                        if macd_filter_enabled:
                            macd_histogram = float(indicator.get('macd_histogram')) if indicator.get('macd_histogram') else None
                            if macd_histogram is not None:
                                if direction == 'long' and macd_long_require_positive and macd_histogram <= 0:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MACD过滤：做多时MACD柱状图非正 (MACD={macd_histogram:.4f})，已过滤")
                                    continue
                                elif direction == 'short' and macd_short_require_negative and macd_histogram >= 0:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MACD过滤：做空时MACD柱状图非负 (MACD={macd_histogram:.4f})，已过滤")
                                    continue
                        
                        # 检查 KDJ 过滤
                        if kdj_filter_enabled:
                            kdj_k = float(indicator.get('kdj_k')) if indicator.get('kdj_k') else None
                            if kdj_k is not None:
                                # 检查是否为强信号（EMA差值百分比）
                                # curr_diff_pct 在前面已经计算（第790行）
                                ema_diff_pct_abs = abs(curr_diff_pct) if 'curr_diff_pct' in locals() and curr_diff_pct is not None else 0
                                is_strong_signal = kdj_allow_strong_signal and ema_diff_pct_abs >= kdj_strong_signal_threshold
                                
                                if direction == 'long' and kdj_k > kdj_long_max_k:
                                    if is_strong_signal:
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ KDJ过滤：做多时KDJ K值过高 (K={kdj_k:.2f} > {kdj_long_max_k})，但EMA信号强度足够 (差值={ema_diff_pct_abs:.2f}% ≥ {kdj_strong_signal_threshold}%)，允许通过")
                                    else:
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ KDJ过滤：做多时KDJ K值过高 (K={kdj_k:.2f} > {kdj_long_max_k})，已过滤")
                                        continue
                                elif direction == 'short' and kdj_k < kdj_short_min_k:
                                    if is_strong_signal:
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ KDJ过滤：做空时KDJ K值过低 (K={kdj_k:.2f} < {kdj_short_min_k})，但EMA信号强度足够 (差值={ema_diff_pct_abs:.2f}% ≥ {kdj_strong_signal_threshold}%)，允许通过")
                                    else:
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ KDJ过滤：做空时KDJ K值过低 (K={kdj_k:.2f} < {kdj_short_min_k})，已过滤")
                                        continue
                        
                        # 检查 MA10/EMA10 信号强度（如果配置了）
                        ma10_ema10_ok = True
                        if ma10 and ema10:
                            # 检查MA10/EMA10信号强度过滤（无论是否启用trend_filter都要检查）
                            if min_ma10_cross_strength > 0:
                                ma10_ema10_diff = ema10 - ma10
                                ma10_ema10_strength_pct = abs(ma10_ema10_diff / ma10 * 100) if ma10 > 0 else 0
                                if ma10_ema10_strength_pct < min_ma10_cross_strength:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MA10/EMA10信号强度不足 (差值={ma10_ema10_strength_pct:.2f}%, 需要≥{min_ma10_cross_strength:.2f}%)，已过滤")
                                    continue
                            
                            # 检查 MA10/EMA10 是否与交易方向同向（如果启用了过滤）
                            if ma10_ema10_trend_filter:
                                if direction == 'long':
                                    ma10_ema10_ok = ema10 > ma10
                                else:
                                    ma10_ema10_ok = ema10 < ma10
                                if not ma10_ema10_ok:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MA10/EMA10不同向")
                                    continue
                        else:
                            # 如果没有 MA10/EMA10 数据，记录警告
                            if min_ma10_cross_strength > 0 or ma10_ema10_trend_filter:
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 缺少 MA10/EMA10 数据，跳过检查")
                                if min_ma10_cross_strength > 0:
                                    continue  # 如果要求信号强度但数据缺失，跳过
                        
                        # 检查趋势持续性
                        trend_confirm_ok = True
                        if trend_confirm_bars > 0:
                            # 找到金叉发生的索引位置
                            golden_cross_index = None
                            for check_lookback in range(1, min(4, current_buy_index + 1)):
                                check_prev_index = current_buy_index - check_lookback
                                if check_prev_index >= 0 and check_prev_index < len(buy_indicator_pairs):
                                    check_prev_pair = buy_indicator_pairs[check_prev_index]
                                    check_prev_indicator = check_prev_pair['indicator']
                                    check_prev_ema_short = float(check_prev_indicator.get('ema_short', 0)) if check_prev_indicator.get('ema_short') else None
                                    check_prev_ema_long = float(check_prev_indicator.get('ema_long', 0)) if check_prev_indicator.get('ema_long') else None
                                    
                                    if buy_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                                        if check_prev_ema_short and check_prev_ema_long and ema_short and ema_long:
                                            # 检查是否在当前K线发生金叉
                                            is_cross_now = (check_prev_ema_short <= check_prev_ema_long and ema_short > ema_long) or \
                                                          (check_prev_ema_short < check_prev_ema_long and ema_short >= ema_long)
                                            if is_cross_now:
                                                golden_cross_index = current_buy_index
                                                break
                                    elif buy_signal == 'ma_ema10':
                                        check_prev_ma10 = float(check_prev_indicator.get('ma10', 0)) if check_prev_indicator.get('ma10') else None
                                        check_prev_ema10 = float(check_prev_indicator.get('ema10', 0)) if check_prev_indicator.get('ema10') else None
                                        if check_prev_ma10 and check_prev_ema10 and ma10 and ema10:
                                            is_cross_now = (check_prev_ema10 <= check_prev_ma10 and ema10 > ma10) or \
                                                          (check_prev_ema10 < check_prev_ma10 and ema10 >= ma10)
                                            if is_cross_now:
                                                golden_cross_index = current_buy_index
                                                break
                            
                            if golden_cross_index is not None:
                                # 如果金叉发生在当前K线，且trend_confirm_bars=1，则当前K线已经满足条件（1根K线确认）
                                # 如果金叉发生在之前的K线，需要检查是否持续了足够的K线数
                                bars_since_cross = current_buy_index - golden_cross_index
                                
                                # 如果金叉发生在当前K线，bars_since_cross=0，但当前K线本身就算1根，所以需要 >= (trend_confirm_bars - 1)
                                # 如果金叉发生在之前的K线，需要 >= trend_confirm_bars
                                required_bars = trend_confirm_bars - 1 if golden_cross_index == current_buy_index else trend_confirm_bars
                                
                                if bars_since_cross >= required_bars:
                                    # 检查从金叉到当前的所有K线，趋势是否一直维持
                                    trend_maintained = True
                                    ema_strength_ok = True
                                    
                                    for check_index in range(golden_cross_index, current_buy_index + 1):
                                        if check_index < len(buy_indicator_pairs):
                                            check_pair = buy_indicator_pairs[check_index]
                                            check_indicator = check_pair['indicator']
                                            check_ema_short = float(check_indicator.get('ema_short', 0)) if check_indicator.get('ema_short') else None
                                            check_ema_long = float(check_indicator.get('ema_long', 0)) if check_indicator.get('ema_long') else None
                                            check_ma10 = float(check_indicator.get('ma10', 0)) if check_indicator.get('ma10') else None
                                            check_ema10 = float(check_indicator.get('ema10', 0)) if check_indicator.get('ema10') else None
                                            
                                            if buy_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                                                if check_ema_short and check_ema_long:
                                                    if direction == 'long' and check_ema_short <= check_ema_long:
                                                        trend_maintained = False
                                                        debug_info.append(f"   ⚠️ 趋势确认失败：在索引{check_index}处趋势反转")
                                                        break
                                                    elif direction == 'short' and check_ema_short >= check_ema_long:
                                                        trend_maintained = False
                                                        debug_info.append(f"   ⚠️ 趋势确认失败：在索引{check_index}处趋势反转")
                                                        break
                                                    
                                                    # 检查EMA差值是否满足阈值（增强趋势确认）
                                                    if trend_confirm_ema_threshold > 0:
                                                        check_ema_diff = abs(check_ema_short - check_ema_long)
                                                        check_ema_diff_pct = (check_ema_diff / check_ema_long * 100) if check_ema_long > 0 else 0
                                                        if check_ema_diff_pct < trend_confirm_ema_threshold:
                                                            ema_strength_ok = False
                                                            debug_info.append(f"   ⚠️ 趋势确认失败：在索引{check_index}处EMA差值过小({check_ema_diff_pct:.2f}% < {trend_confirm_ema_threshold}%)")
                                                            break
                                            elif buy_signal == 'ma_ema10':
                                                if check_ma10 and check_ema10:
                                                    if direction == 'long' and check_ema10 <= check_ma10:
                                                        trend_maintained = False
                                                        debug_info.append(f"   ⚠️ 趋势确认失败：在索引{check_index}处趋势反转")
                                                        break
                                                    elif direction == 'short' and check_ema10 >= check_ma10:
                                                        trend_maintained = False
                                                        debug_info.append(f"   ⚠️ 趋势确认失败：在索引{check_index}处趋势反转")
                                                        break
                                    
                                    # 检查当前K线的EMA差值是否满足阈值
                                    if trend_confirm_ema_threshold > 0 and trend_maintained:
                                        if buy_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                                            curr_ema_diff = abs(ema_short - ema_long)
                                            curr_ema_diff_pct = (curr_ema_diff / ema_long * 100) if ema_long > 0 else 0
                                            if curr_ema_diff_pct < trend_confirm_ema_threshold:
                                                ema_strength_ok = False
                                                debug_info.append(f"   ⚠️ 趋势确认失败：当前EMA差值过小({curr_ema_diff_pct:.2f}% < {trend_confirm_ema_threshold}%)")
                                    
                                    if not trend_maintained:
                                        trend_confirm_ok = False
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 趋势确认失败，趋势未持续{trend_confirm_bars}根K线")
                                    elif not ema_strength_ok:
                                        trend_confirm_ok = False
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 趋势确认失败，EMA差值未达到阈值({trend_confirm_ema_threshold}%)")
                                else:
                                    # 金叉刚发生，还需要等待更多K线
                                    trend_confirm_ok = False
                                    wait_bars = required_bars - bars_since_cross
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 趋势确认中，金叉发生在索引{golden_cross_index}，当前索引{current_buy_index}，已过{bars_since_cross}根K线，需要等待{wait_bars}根K线（共需{trend_confirm_bars}根）")
                            else:
                                # 未找到金叉，可能是信号触发逻辑有问题
                                trend_confirm_ok = False
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 未找到金叉位置，无法进行趋势确认")
                        
                        if not trend_confirm_ok:
                            continue
                        
                        # 添加调试信息：所有检查都通过，准备买入
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅ 所有买入条件检查通过，准备执行买入操作")
                        
                        # 计算入场价格
                        high_price = float(kline.get('high', kline.get('high_price', close_price)))
                        low_price = float(kline.get('low', kline.get('low_price', close_price)))
                        
                        entry_price = None
                        can_execute = False
                        
                        if direction == 'long':
                            if long_price_type == 'market':
                                entry_price = close_price
                                can_execute = True
                            elif long_price_type == 'market_minus_0_2':
                                target_price = close_price * 0.998
                                if low_price <= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif long_price_type == 'market_minus_0_4':
                                target_price = close_price * 0.996
                                if low_price <= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif long_price_type == 'market_minus_0_6':
                                target_price = close_price * 0.994
                                if low_price <= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif long_price_type == 'market_minus_0_8':
                                target_price = close_price * 0.992
                                if low_price <= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif long_price_type == 'market_minus_1':
                                target_price = close_price * 0.99
                                if low_price <= target_price:
                                    entry_price = target_price
                                    can_execute = True
                        elif direction == 'short':
                            if short_price_type == 'market':
                                entry_price = close_price
                                can_execute = True
                            elif short_price_type == 'market_plus_0_2':
                                target_price = close_price * 1.002
                                if high_price >= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif short_price_type == 'market_plus_0_4':
                                target_price = close_price * 1.004
                                if high_price >= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif short_price_type == 'market_plus_0_6':
                                target_price = close_price * 1.006
                                if high_price >= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif short_price_type == 'market_plus_0_8':
                                target_price = close_price * 1.008
                                if high_price >= target_price:
                                    entry_price = target_price
                                    can_execute = True
                            elif short_price_type == 'market_plus_1':
                                target_price = close_price * 1.01
                                if high_price >= target_price:
                                    entry_price = target_price
                                    can_execute = True
                        
                        if not can_execute or entry_price is None:
                            continue
                        
                        # 计算仓位大小
                        position_value = balance * (position_size / 100)
                        quantity = (position_value * leverage) / entry_price
                        quantity = self.round_quantity(quantity, symbol)
                        
                        if quantity > 0:
                            # 计算开仓手续费
                            open_fee = (entry_price * quantity) * fee_rate
                            balance -= open_fee
                            
                            # 计算止损止盈价格
                            stop_loss_price = None
                            take_profit_price = None
                            if stop_loss_pct is not None:
                                if direction == 'long':
                                    stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
                                else:
                                    stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
                            if take_profit_pct is not None:
                                if direction == 'long':
                                    take_profit_price = entry_price * (1 + take_profit_pct / 100)
                                else:
                                    take_profit_price = entry_price * (1 - take_profit_pct / 100)
                            
                            position = {
                                'direction': direction,
                                'entry_price': entry_price,
                                'quantity': quantity,
                                'entry_time': current_time,  # UTC时间用于计算
                                'entry_time_local': current_time_local,  # 本地时间用于显示
                                'leverage': leverage,
                                'open_fee': open_fee,
                                'stop_loss_price': stop_loss_price,
                                'take_profit_price': take_profit_price
                            }
                            positions.append(position)
                            
                            trades.append({
                                'type': 'BUY',
                                'direction': direction,
                                'price': entry_price,
                                'quantity': quantity,
                                'time': current_time,
                                'balance': balance,
                                'fee': open_fee,
                                'fee_rate': fee_rate
                            })
                            
                            # 保存交易记录到数据库
                            self._save_trade_record(
                                symbol=symbol,
                                action='BUY',
                                direction=direction,
                                entry_price=entry_price,
                                exit_price=None,
                                quantity=quantity,
                                leverage=leverage,
                                fee=open_fee,
                                realized_pnl=None,
                                strategy_id=strategy_id,
                                strategy_name=strategy_name,
                                account_id=account_id,
                                reason='买入信号触发',
                                trade_time=self.utc_to_local(current_time) if current_time else self.get_local_time()
                            )
                            
                            direction_text = "做多" if direction == 'long' else "做空"
                            qty_precision = self.get_quantity_precision(symbol)
                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 买入{direction_text}，价格={entry_price:.4f}，数量={quantity:.{qty_precision}f}，开仓手续费={open_fee:.4f}，余额={balance:.2f}")
            
            # 如果是卖出时间点，检查卖出信号
            elif time_point['type'] == 'sell' and len(positions) > 0:
                pair = time_point['pair']
                kline = pair['kline']
                indicator = pair['indicator']
                close_price = float(kline['close_price'])
                high_price = float(kline.get('high', kline.get('high_price', close_price)))
                low_price = float(kline.get('low', kline.get('low_price', close_price)))
                volume_ratio = float(indicator['volume_ratio']) if indicator.get('volume_ratio') else 1.0
                
                # 获取当前时间（用于最小持仓时间检查）
                current_time = self.parse_time(kline['timestamp'])
                current_time_local = self.utc_to_local(current_time)
                
                # 先检查止损止盈
                for position in positions[:]:
                    entry_price = position['entry_price']
                    direction = position['direction']
                    stop_loss_price = position.get('stop_loss_price')
                    take_profit_price = position.get('take_profit_price')
                    entry_time = position.get('entry_time')
                    
                    exit_price = None
                    exit_reason = None
                    
                    # 止损检查（不受最小持仓时间限制）
                    if stop_loss_price:
                        if direction == 'long' and low_price <= stop_loss_price:
                            exit_price = stop_loss_price
                            exit_reason = "止损"
                        elif direction == 'short' and high_price >= stop_loss_price:
                            exit_price = stop_loss_price
                            exit_reason = "止损"
                    
                    # 止盈检查（需要满足最小持仓时间）
                    if not exit_price and take_profit_price:
                        # 检查最小持仓时间
                        can_exit = True
                        if min_holding_time_hours > 0 and entry_time:
                            holding_time = current_time - entry_time
                            min_holding_time = timedelta(hours=min_holding_time_hours)
                            if holding_time < min_holding_time:
                                can_exit = False
                                remaining_time = min_holding_time - holding_time
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⏳ 止盈触发但持仓时间不足，已持仓{holding_time.total_seconds()/3600:.1f}小时，需要至少{min_holding_time_hours}小时，还需等待{remaining_time.total_seconds()/3600:.1f}小时")
                        
                        if can_exit:
                            if direction == 'long' and high_price >= take_profit_price:
                                exit_price = take_profit_price
                                exit_reason = "止盈"
                            elif direction == 'short' and low_price <= take_profit_price:
                                exit_price = take_profit_price
                                exit_reason = "止盈"
                    
                    if exit_price and exit_reason:
                        quantity = position['quantity']
                        
                        if direction == 'long':
                            gross_pnl = (exit_price - entry_price) * quantity
                        else:
                            gross_pnl = (entry_price - exit_price) * quantity
                        
                        close_fee = (exit_price * quantity) * fee_rate
                        open_fee = position.get('open_fee', 0)
                        total_fee = open_fee + close_fee
                        pnl = gross_pnl - total_fee
                        
                        balance += gross_pnl - close_fee
                        
                        margin_used = (entry_price * quantity) / leverage
                        pnl_pct = (pnl / margin_used) * 100 if margin_used > 0 else 0
                        
                        direction_text = "做多" if direction == 'long' else "做空"
                        trades.append({
                            'type': 'SELL',
                            'direction': direction,
                            'price': exit_price,
                            'quantity': quantity,
                            'time': current_time,
                            'balance': balance,
                            'pnl': pnl,
                            'pnl_pct': pnl_pct,
                            'fee': close_fee,
                            'fee_rate': fee_rate,
                            'exit_reason': exit_reason
                        })
                        
                        # 保存交易记录到数据库
                        self._save_trade_record(
                            symbol=symbol,
                            action='SELL',
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            quantity=quantity,
                            leverage=leverage,
                            fee=close_fee,
                            realized_pnl=pnl,
                            strategy_id=strategy_id,
                            strategy_name=strategy_name,
                            account_id=account_id,
                            reason=exit_reason,
                            trade_time=self.utc_to_local(current_time) if current_time else self.get_local_time()
                        )
                        
                        positions.remove(position)
                        closed_at_current_time = True
                
                # 如果已经因为止损止盈平仓，跳过卖出信号检查
                if closed_at_current_time or len(positions) == 0:
                    continue
                
                # 检查趋势反转退出机制（优先级高于卖出信号）
                should_exit = False
                exit_reason = None
                
                # 需要获取前一个时间点的指标来检查反转
                current_sell_index = None
                try:
                    current_sell_index = sell_indicator_pairs.index(pair)
                except ValueError:
                    pass
                
                if current_sell_index is not None and current_sell_index > 0:
                    prev_pair = sell_indicator_pairs[current_sell_index - 1]
                    prev_indicator = prev_pair['indicator']
                    
                    # 检查 MA10/EMA10 反转退出（带阈值，避免小幅波动触发）
                    if exit_on_ma_flip:
                        if indicator.get('ma10') and indicator.get('ema10') and \
                           prev_indicator.get('ma10') and prev_indicator.get('ema10'):
                            ma10 = float(indicator['ma10'])
                            ema10 = float(indicator['ema10'])
                            prev_ma10 = float(prev_indicator['ma10'])
                            prev_ema10 = float(prev_indicator['ema10'])
                            
                            # 计算MA10/EMA10差值百分比
                            prev_diff = prev_ema10 - prev_ma10
                            prev_diff_pct = abs(prev_diff / prev_ma10 * 100) if prev_ma10 > 0 else 0
                            curr_diff = ema10 - ma10
                            curr_diff_pct = abs(curr_diff / ma10 * 100) if ma10 > 0 else 0
                            
                            # 检查是否反转（从多头转为空头，或从空头转为多头）
                            prev_bullish = prev_ema10 > prev_ma10
                            curr_bullish = ema10 > ma10
                            
                            # 只有当差值百分比超过阈值时才触发反转退出（避免小幅波动）
                            if prev_bullish != curr_bullish:
                                # 检查差值是否超过阈值
                                if prev_diff_pct >= exit_on_ma_flip_threshold or curr_diff_pct >= exit_on_ma_flip_threshold:
                                    should_exit = True
                                    exit_reason = f'MA10/EMA10反转(阈值≥{exit_on_ma_flip_threshold}%)'
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 检测到MA10/EMA10反转，触发退出机制（前差值={prev_diff_pct:.2f}%，当前差值={curr_diff_pct:.2f}%，阈值={exit_on_ma_flip_threshold}%）")
                                else:
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: 📊 MA10/EMA10反转但差值过小（前差值={prev_diff_pct:.2f}%，当前差值={curr_diff_pct:.2f}% < 阈值{exit_on_ma_flip_threshold}%），忽略")
                    
                    # 检查 EMA 弱信号退出（使用可配置阈值）
                    if not should_exit and exit_on_ema_weak:
                        if indicator.get('ema_short') and indicator.get('ema_long'):
                            ema_short = float(indicator['ema_short'])
                            ema_long = float(indicator['ema_long'])
                            ema_diff = abs(ema_short - ema_long)
                            ema_diff_pct = (ema_diff / ema_long * 100) if ema_long > 0 else 0
                            
                            if ema_diff_pct < exit_on_ema_weak_threshold:  # EMA差值小于阈值
                                should_exit = True
                                exit_reason = f'EMA信号过弱(差值<{exit_on_ema_weak_threshold}%)'
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ EMA差值过小({ema_diff_pct:.2f}% < {exit_on_ema_weak_threshold}%)，触发退出机制")
                    
                    # 检查早期止损（基于EMA差值或价格回撤）
                    if not should_exit and early_stop_loss_pct is not None and early_stop_loss_pct > 0:
                        for position in positions[:]:
                            entry_price = position['entry_price']
                            direction = position['direction']
                            
                            # 基于EMA差值计算早期止损
                            if indicator.get('ema_short') and indicator.get('ema_long'):
                                ema_short = float(indicator['ema_short'])
                                ema_long = float(indicator['ema_long'])
                                ema_diff_pct = abs(ema_short - ema_long) / ema_long * 100 if ema_long > 0 else 0
                                
                                # 如果EMA差值缩小到阈值以下，触发早期止损
                                if ema_diff_pct < early_stop_loss_pct:
                                    should_exit = True
                                    exit_reason = f'早期止损(EMA差值{ema_diff_pct:.2f}% < {early_stop_loss_pct}%)'
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ EMA差值缩小({ema_diff_pct:.2f}% < {early_stop_loss_pct}%)，触发早期止损")
                                    break
                            
                            # 基于价格回撤计算早期止损
                            if direction == 'long':
                                price_drop_pct = (entry_price - close_price) / entry_price * 100
                                if price_drop_pct >= early_stop_loss_pct:
                                    should_exit = True
                                    exit_reason = f'早期止损(价格回撤{price_drop_pct:.2f}% ≥ {early_stop_loss_pct}%)'
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 做多价格回撤({price_drop_pct:.2f}% ≥ {early_stop_loss_pct}%)，触发早期止损")
                                    break
                            else:  # short
                                price_rise_pct = (close_price - entry_price) / entry_price * 100
                                if price_rise_pct >= early_stop_loss_pct:
                                    should_exit = True
                                    exit_reason = f'早期止损(价格回撤{price_rise_pct:.2f}% ≥ {early_stop_loss_pct}%)'
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 做空价格回撤({price_rise_pct:.2f}% ≥ {early_stop_loss_pct}%)，触发早期止损")
                                    break
                
                # 如果触发趋势反转退出，立即平仓
                if should_exit:
                    for position in positions[:]:
                        entry_price = position['entry_price']
                        quantity = position['quantity']
                        direction = position['direction']
                        
                        # 使用当前K线价格平仓
                        exit_price = close_price
                        
                        if direction == 'long':
                            gross_pnl = (exit_price - entry_price) * quantity
                        else:
                            gross_pnl = (entry_price - exit_price) * quantity
                        
                        close_fee = (exit_price * quantity) * fee_rate
                        open_fee = position.get('open_fee', 0)
                        total_fee = open_fee + close_fee
                        pnl = gross_pnl - total_fee
                        
                        margin_used = (entry_price * quantity) / leverage
                        pnl_pct = (pnl / margin_used) * 100 if margin_used > 0 else 0
                        
                        balance += gross_pnl - close_fee
                        
                        direction_text = "做多" if direction == 'long' else "做空"
                        trades.append({
                            'type': 'SELL',
                            'direction': direction,
                            'price': exit_price,
                            'quantity': quantity,
                            'time': current_time,
                            'balance': balance,
                            'pnl': pnl,
                            'pnl_pct': pnl_pct,
                            'fee': close_fee,
                            'fee_rate': fee_rate,
                            'exit_reason': exit_reason
                        })
                        
                        # 保存交易记录到数据库
                        self._save_trade_record(
                            symbol=symbol,
                            action='SELL',
                            direction=direction,
                            entry_price=entry_price,
                            exit_price=exit_price,
                            quantity=quantity,
                            leverage=leverage,
                            fee=close_fee,
                            realized_pnl=pnl,
                            strategy_id=strategy_id,
                            strategy_name=strategy_name,
                            account_id=account_id,
                            reason=exit_reason,
                            trade_time=self.utc_to_local(current_time) if current_time else self.get_local_time()
                        )
                        
                        positions.remove(position)
                        closed_at_current_time = True
                        
                        qty_precision = self.get_quantity_precision(symbol)
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 趋势反转退出{direction_text} | 入场价={entry_price:.4f}, 平仓价={exit_price:.4f}, 数量={quantity:.{qty_precision}f}, 实际盈亏={pnl:+.2f} ({pnl_pct:+.2f}%), 原因: {exit_reason}")
                    
                    continue  # 已平仓，跳过后续卖出信号检查
                
                # 卖出信号检查
                sell_signal_triggered = False
                try:
                    current_sell_index = sell_indicator_pairs.index(pair)
                except ValueError:
                    # 如果找不到当前时间点的指标，跳过卖出信号检查
                    if len(positions) > 0:
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 未找到卖出时间周期({sell_timeframe})的指标数据，跳过卖出信号检查")
                    continue
                
                ma5 = float(indicator.get('ma5')) if indicator.get('ma5') else None
                ema5 = float(indicator.get('ema5')) if indicator.get('ema5') else None
                ma10 = float(indicator.get('ma10')) if indicator.get('ma10') else None
                ema10 = float(indicator.get('ema10')) if indicator.get('ema10') else None
                ema_short = float(indicator.get('ema_short')) if indicator.get('ema_short') else None
                ema_long = float(indicator.get('ema_long')) if indicator.get('ema_long') else None
                
                if current_sell_index > 0 and len(positions) > 0:
                    lookback_count = min(3, current_sell_index)

                    for lookback in range(1, lookback_count + 1):
                        if sell_signal_triggered:
                            break
                        prev_pair = sell_indicator_pairs[current_sell_index - lookback]
                        prev_indicator = prev_pair['indicator']

                        if sell_signal == 'ma_ema5':
                            prev_ma5 = float(prev_indicator.get('ma5')) if prev_indicator.get('ma5') else None
                            prev_ema5 = float(prev_indicator.get('ema5')) if prev_indicator.get('ema5') else None

                            if ma5 and ema5 and prev_ma5 and prev_ema5:
                                # 检测金叉和死叉
                                ma5_ema5_is_golden = (prev_ema5 <= prev_ma5 and ema5 > ma5) or \
                                                     (prev_ema5 < prev_ma5 and ema5 >= ma5)
                                ma5_ema5_is_death = (prev_ema5 >= prev_ma5 and ema5 < ma5) or \
                                                    (prev_ema5 > prev_ma5 and ema5 <= ma5)

                                # 根据持仓方向决定平仓信号
                                for pos in positions:
                                    pos_direction = pos.get('direction')
                                    if pos_direction == 'long' and ma5_ema5_is_death:
                                        sell_signal_triggered = True
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA5/EMA5死叉 - 触发做多平仓信号")
                                        break
                                    elif pos_direction == 'short' and ma5_ema5_is_golden:
                                        sell_signal_triggered = True
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA5/EMA5金叉 - 触发做空平仓信号")
                                        break
                                if sell_signal_triggered:
                                    break

                        elif sell_signal == 'ma_ema10':
                            prev_ma10 = float(prev_indicator.get('ma10')) if prev_indicator.get('ma10') else None
                            prev_ema10 = float(prev_indicator.get('ema10')) if prev_indicator.get('ema10') else None

                            if ma10 and ema10 and prev_ma10 and prev_ema10:
                                # 检测金叉和死叉
                                ma10_ema10_is_golden = (prev_ema10 <= prev_ma10 and ema10 > ma10) or \
                                                       (prev_ema10 < prev_ma10 and ema10 >= ma10)
                                ma10_ema10_is_death = (prev_ema10 >= prev_ma10 and ema10 < ma10) or \
                                                      (prev_ema10 > prev_ma10 and ema10 <= ma10)

                                # 根据持仓方向决定平仓信号
                                for pos in positions:
                                    pos_direction = pos.get('direction')
                                    if pos_direction == 'long' and ma10_ema10_is_death:
                                        sell_signal_triggered = True
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA10/EMA10死叉 - 触发做多平仓信号")
                                        break
                                    elif pos_direction == 'short' and ma10_ema10_is_golden:
                                        sell_signal_triggered = True
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA10/EMA10金叉 - 触发做空平仓信号")
                                        break
                                if sell_signal_triggered:
                                    break

                        elif sell_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                            prev_ema_short = float(prev_indicator.get('ema_short')) if prev_indicator.get('ema_short') else None
                            prev_ema_long = float(prev_indicator.get('ema_long')) if prev_indicator.get('ema_long') else None

                            if ema_short and ema_long and prev_ema_short and prev_ema_long:
                                # 检测金叉和死叉
                                ema_is_golden = (prev_ema_short <= prev_ema_long and ema_short > ema_long) or \
                                                (prev_ema_short < prev_ema_long and ema_short >= ema_long)
                                ema_is_death = (prev_ema_short >= prev_ema_long and ema_short < ema_long) or \
                                               (prev_ema_short > prev_ema_long and ema_short <= ema_long)

                                # 根据持仓方向决定平仓信号
                                for pos in positions:
                                    pos_direction = pos.get('direction')
                                    if pos_direction == 'long' and ema_is_death:
                                        sell_signal_triggered = True
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到EMA9/26死叉 - 触发做多平仓信号")
                                        break
                                    elif pos_direction == 'short' and ema_is_golden:
                                        sell_signal_triggered = True
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到EMA9/26金叉 - 触发做空平仓信号")
                                        break
                                if sell_signal_triggered:
                                    break
                else:
                    # 如果没有历史数据，无法检测卖出信号
                    if len(positions) > 0:
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ⚠️ 卖出时间周期历史数据不足，无法检测卖出信号（需要至少1个历史数据点）")
                
                # 如果持仓存在但没有卖出信号，记录日志（每10个时间点记录一次，避免日志过多）
                if len(positions) > 0 and not sell_signal_triggered:
                    # 使用时间戳的分钟数来判断是否记录（每10分钟记录一次）
                    if current_time_local.minute % 10 == 0:
                        position_info = ', '.join([f"{p['direction']}({p['entry_price']:.4f})" for p in positions])
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: 📊 当前持仓: {position_info}，未检测到卖出信号（{sell_signal}）")
                
                # 检查卖出成交量条件（按持仓方向分开检查）
                # 注意：这里需要根据每个持仓的方向分别检查对应的成交量条件
                sell_volume_condition_met_by_direction = {}
                for pos in positions:
                    pos_direction = pos.get('direction')
                    if pos_direction == 'long':
                        # 平仓做多：支持 <1, 1-2, >2
                        if sell_volume_long_enabled and sell_volume_long:
                            volume_condition = sell_volume_long
                            if volume_condition == '<1':
                                sell_volume_condition_met_by_direction['long'] = volume_ratio < 1.0
                            elif volume_condition == '1-2':
                                sell_volume_condition_met_by_direction['long'] = (1.0 <= volume_ratio <= 2.0)
                            elif volume_condition == '>2':
                                sell_volume_condition_met_by_direction['long'] = volume_ratio > 2.0
                            else:
                                # 兼容旧格式
                                try:
                                    required_ratio = float(volume_condition)
                                    sell_volume_condition_met_by_direction['long'] = volume_ratio >= required_ratio
                                except:
                                    sell_volume_condition_met_by_direction['long'] = True
                        else:
                            sell_volume_condition_met_by_direction['long'] = True
                    elif pos_direction == 'short':
                        # 平仓做空：支持 >2, 1-2, <1
                        if sell_volume_short_enabled and sell_volume_short:
                            volume_condition = sell_volume_short
                            if volume_condition == '>2':
                                sell_volume_condition_met_by_direction['short'] = volume_ratio > 2.0
                            elif volume_condition == '1-2':
                                sell_volume_condition_met_by_direction['short'] = (1.0 <= volume_ratio <= 2.0)
                            elif volume_condition == '<1':
                                sell_volume_condition_met_by_direction['short'] = volume_ratio < 1.0
                            else:
                                # 兼容旧格式
                                try:
                                    required_ratio = float(volume_condition)
                                    sell_volume_condition_met_by_direction['short'] = volume_ratio >= required_ratio
                                except:
                                    sell_volume_condition_met_by_direction['short'] = True
                        else:
                            sell_volume_condition_met_by_direction['short'] = True

                # 兼容旧的单一卖出成交量设置（如果没有启用分开的设置）
                if sell_volume_enabled and sell_volume and not sell_volume_long_enabled and not sell_volume_short_enabled:
                    sell_volume_condition_met = True
                    if sell_volume == '>1':
                        if volume_ratio <= 1.0:
                            sell_volume_condition_met = False
                    elif sell_volume == '0.8-1':
                        if not (0.8 <= volume_ratio <= 1.0):
                            sell_volume_condition_met = False
                    elif sell_volume == '0.6-0.8':
                        if not (0.6 <= volume_ratio < 0.8):
                            sell_volume_condition_met = False
                    elif sell_volume == '<0.6':
                        if volume_ratio >= 0.6:
                            sell_volume_condition_met = False
                    # 应用到所有方向
                    sell_volume_condition_met_by_direction = {'long': sell_volume_condition_met, 'short': sell_volume_condition_met}

                # 如果卖出信号触发但成交量条件不满足，记录日志
                sell_volume_check_result = all(sell_volume_condition_met_by_direction.get(p['direction'], True) for p in positions)
                if sell_signal_triggered and not sell_volume_check_result:
                    failed_directions = [d for d, met in sell_volume_condition_met_by_direction.items() if not met]
                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ⚠️ 卖出信号已触发，但成交量条件不满足（成交量比率={volume_ratio:.2f}x，{failed_directions}方向不满足），跳过平仓")
                
                # 如果持仓存在但没有卖出信号，记录日志（每10个时间点记录一次，避免日志过多）
                if len(positions) > 0 and not sell_signal_triggered:
                    # 使用时间戳的分钟数来判断是否记录（每10分钟记录一次）
                    if current_time_local.minute % 10 == 0:
                        position_info = ', '.join([f"{p['direction']}({p['entry_price']:.4f})" for p in positions])
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: 📊 当前持仓: {position_info}，未检测到卖出信号（{sell_signal}）")
                
                # 执行卖出
                if sell_signal_triggered:
                    for position in positions[:]:
                        entry_price = position['entry_price']
                        quantity = position['quantity']
                        direction = position['direction']

                        # 检查该方向的成交量条件
                        if not sell_volume_condition_met_by_direction.get(direction, True):
                            continue

                        # 平仓价格逻辑：卖出信号触发时直接使用当前价格平仓
                        # 对于做多，使用收盘价或最高价（取较低者，更保守）
                        # 对于做空，使用收盘价或最低价（取较高者，更保守）
                        if direction == 'long':
                            # 做多平仓：使用收盘价（更保守，避免使用最高价）
                            exit_price = close_price
                            can_execute = True
                        else:
                            # 做空平仓：使用收盘价（更保守，避免使用最低价）
                            exit_price = close_price
                            can_execute = True
                        
                        if not can_execute or exit_price is None:
                            continue
                        
                        # 计算盈亏
                        if direction == 'long':
                            gross_pnl = (exit_price - entry_price) * quantity
                        else:
                            gross_pnl = (entry_price - exit_price) * quantity
                        
                        close_fee = (exit_price * quantity) * fee_rate
                        open_fee = position.get('open_fee', 0)
                        total_fee = open_fee + close_fee
                        pnl = gross_pnl - total_fee
                        
                        margin_used = (entry_price * quantity) / leverage
                        pnl_pct = (pnl / margin_used) * 100 if margin_used > 0 else 0
                        
                        balance += gross_pnl - close_fee
                        
                        direction_text = "做多" if direction == 'long' else "做空"
                        trades.append({
                            'type': 'SELL',
                            'direction': direction,
                            'price': exit_price,
                            'quantity': quantity,
                            'time': current_time,
                            'pnl': pnl,
                            'gross_pnl': gross_pnl,
                            'pnl_pct': pnl_pct,
                            'balance': balance,
                            'entry_price': entry_price,
                            'entry_time': position['entry_time'],
                            'open_fee': open_fee,
                            'close_fee': close_fee,
                            'total_fee': total_fee,
                            'fee_rate': fee_rate
                        })
                        
                        positions.remove(position)
                        closed_at_current_time = True
                        
                        qty_precision = self.get_quantity_precision(symbol)
                        current_time_local = self.utc_to_local(current_time)
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 平仓{direction_text} | 入场价={entry_price:.4f}, 平仓价={exit_price:.4f}, 数量={quantity:.{qty_precision}f}, 实际盈亏={pnl:+.2f} ({pnl_pct:+.2f}%), 余额={balance:.2f}")
        
        # 计算最终盈亏（平掉所有未平仓的持仓）
        final_balance = balance
        if len(positions) > 0:
            if sell_indicator_pairs:
                last_pair = sell_indicator_pairs[-1]
                last_kline = last_pair['kline']
                last_time = self.parse_time(last_kline['timestamp'])
                exit_price = float(last_kline['close_price'])
            elif buy_indicator_pairs:
                last_pair = buy_indicator_pairs[-1]
                last_kline = last_pair['kline']
                last_time = self.parse_time(last_kline['timestamp'])
                exit_price = float(last_kline['close_price'])
            else:
                exit_price = positions[0]['entry_price']
                last_time = datetime.now()
            
            for position in positions:
                entry_price = position['entry_price']
                quantity = position['quantity']
                direction = position['direction']
                
                if direction == 'long':
                    gross_pnl = (exit_price - entry_price) * quantity
                else:
                    gross_pnl = (entry_price - exit_price) * quantity
                
                close_fee = (exit_price * quantity) * fee_rate
                open_fee = position.get('open_fee', 0)
                total_fee = open_fee + close_fee
                pnl = gross_pnl - total_fee
                
                margin_used = (entry_price * quantity) / leverage
                pnl_pct = (pnl / margin_used) * 100 if margin_used > 0 else 0
                
                final_balance += gross_pnl - close_fee
                
                direction_text = "做多" if direction == 'long' else "做空"
                trades.append({
                    'type': 'SELL',
                    'direction': direction,
                    'price': exit_price,
                    'quantity': quantity,
                    'time': last_time,
                    'pnl': pnl,
                    'gross_pnl': gross_pnl,
                    'pnl_pct': pnl_pct,
                    'balance': final_balance,
                    'entry_price': entry_price,
                    'entry_time': position['entry_time'],
                    'open_fee': open_fee,
                    'close_fee': close_fee,
                    'total_fee': total_fee,
                    'fee_rate': fee_rate,
                    'force_close': True
                })
                
                # 保存交易记录到数据库
                self._save_trade_record(
                    symbol=symbol,
                    action='SELL',
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    leverage=leverage,
                    fee=close_fee,
                    realized_pnl=pnl,
                    strategy_id=strategy_id,
                    strategy_name=strategy_name,
                    account_id=account_id,
                    reason='测试结束强制平仓',
                    trade_time=self.utc_to_local(last_time) if last_time else self.get_local_time()
                )
        
        total_pnl = final_balance - initial_balance
        total_pnl_pct = (total_pnl / initial_balance) * 100
        
        # 统计信号检测情况
        golden_cross_count = len([info for info in debug_info if '金叉' in info])
        death_cross_count = len([info for info in debug_info if '死叉' in info])
        
        return {
            'symbol': symbol,
            'initial_balance': initial_balance,
            'final_balance': final_balance,
            'total_pnl': total_pnl,
            'total_pnl_pct': total_pnl_pct,
            'trades_count': len(trades),
            'trades': trades,
            'open_positions': len(positions),
            'debug_info': debug_info,
            'klines_count': len(buy_test_klines) + len(sell_test_klines),
            'indicators_count': len(buy_indicator_pairs) + len(sell_indicator_pairs),
            'matched_pairs_count': len(buy_indicator_pairs) + len(sell_indicator_pairs),
            'golden_cross_count': golden_cross_count,
            'death_cross_count': death_cross_count,
            'buy_directions': buy_directions,
            'buy_volume_enabled': buy_volume_enabled,
            'buy_volume': buy_volume,
            'buy_volume_long': buy_volume_long,
            'buy_volume_short': buy_volume_short,
            'sell_volume_enabled': sell_volume_enabled,
            'sell_volume': sell_volume,
            'sell_volume_long_enabled': sell_volume_long_enabled,
            'sell_volume_short_enabled': sell_volume_short_enabled,
            'sell_volume_long': sell_volume_long,
            'sell_volume_short': sell_volume_short
        }

