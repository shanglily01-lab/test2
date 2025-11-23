"""
策略自动执行服务
定期检查启用的策略，根据EMA信号自动执行买入和平仓操作
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
import pymysql
from loguru import logger

# 定义本地时区（UTC+8）
LOCAL_TIMEZONE = timezone(timedelta(hours=8))

def get_local_time() -> datetime:
    """获取本地时间（UTC+8）"""
    return datetime.now(LOCAL_TIMEZONE).replace(tzinfo=None)

def get_quantity_precision(symbol: str) -> int:
    """
    根据交易对获取数量精度（小数位数）
    
    Args:
        symbol: 交易对，如 'PUMP/USDT', 'DOGE/USDT'
    
    Returns:
        数量精度（小数位数）
    """
    symbol_upper = symbol.upper().replace('/', '')
    # PUMP/USDT 和 DOGE/USDT 保持8位小数
    if 'PUMP' in symbol_upper or 'DOGE' in symbol_upper:
        return 8
    # 其他交易对默认8位小数（数据库字段支持）
    return 8

def round_quantity(quantity: Decimal, symbol: str) -> Decimal:
    """
    根据交易对精度对数量进行四舍五入
    
    Args:
        quantity: 数量
        symbol: 交易对
    
    Returns:
        四舍五入后的数量
    """
    precision = get_quantity_precision(symbol)
    # 使用 quantize 进行精度控制
    from decimal import ROUND_HALF_UP
    quantize_str = '0.' + '0' * precision
    return quantity.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)

from app.trading.futures_trading_engine import FuturesTradingEngine
from app.analyzers.technical_indicators import TechnicalIndicators
from app.services.strategy_hit_recorder import StrategyHitRecorder


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
        self.hit_recorder = StrategyHitRecorder(db_config)  # 策略命中记录器
        
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
            buy_volume_long_enabled = strategy.get('buyVolumeLongEnabled', False)
            buy_volume_short_enabled = strategy.get('buyVolumeShortEnabled', False)
            buy_volume = strategy.get('buyVolume')  # 兼容旧格式
            buy_volume_long = strategy.get('buyVolumeLong')
            buy_volume_short = strategy.get('buyVolumeShort')
            sell_signal = strategy.get('sellSignals')
            sell_volume_enabled = strategy.get('sellVolumeEnabled', False)
            sell_volume = strategy.get('sellVolume')
            position_size = strategy.get('positionSize', 10)
            max_positions = strategy.get('maxPositions')  # 最大持仓数
            max_long_positions = strategy.get('maxLongPositions')  # 最大做多持仓数
            max_short_positions = strategy.get('maxShortPositions')  # 最大做空持仓数
            long_price_type = strategy.get('longPrice', 'market')
            short_price_type = strategy.get('shortPrice', 'market')
            # 止损止盈参数
            stop_loss_pct = strategy.get('stopLoss')  # 止损百分比
            take_profit_pct = strategy.get('takeProfit')  # 止盈百分比
            # 开仓前先平掉相反方向的持仓
            close_opposite_on_entry = strategy.get('closeOppositeOnEntry', False)
            # MA10/EMA10 同向过滤
            ma10_ema10_trend_filter = strategy.get('ma10Ema10TrendFilter', False)  # 是否启用 MA10/EMA10 同向过滤
            # 信号强度过滤参数（兼容旧格式和新格式）
            min_ema_cross_strength = strategy.get('minEMACrossStrength', 0.0)  # EMA差值最小百分比（默认0.0表示不启用）
            min_ma10_cross_strength = strategy.get('minMA10CrossStrength', 0.0)  # MA10/EMA10差值最小百分比（默认0.0表示不启用）
            # 新的信号强度配置（优先级高于旧格式）
            min_signal_strength = strategy.get('minSignalStrength', {})
            if min_signal_strength:
                min_ema_cross_strength = max(min_ema_cross_strength, min_signal_strength.get('ema9_26', 0.0))
                min_ma10_cross_strength = max(min_ma10_cross_strength, min_signal_strength.get('ma10_ema10', 0.0))
            # 趋势持续性检查参数
            trend_confirm_bars = strategy.get('trendConfirmBars', 0)  # 趋势至少持续K线数（默认0表示不启用）
            # 趋势反转退出机制
            exit_on_ma_flip = strategy.get('exitOnMAFlip', False)  # MA10/EMA10反转时立即平仓
            exit_on_ema_weak = strategy.get('exitOnEMAWeak', False)  # EMA差值<0.05%时平仓
            
            if not symbols or not buy_directions or not buy_signal or not sell_signal:
                return {'success': False, 'message': '策略配置不完整'}
            
            # 确定时间周期
            timeframe_map = {
                'ema_5m': '5m',
                'ema_15m': '15m',
                'ema_1h': '1h',
                'ma_ema5': '5m',  # MA5/EMA5 使用 5分钟周期
                'ma_ema10': '5m'  # MA10/EMA10 使用 5分钟周期（或根据实际需求调整）
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
                        
                        # 获取K线数据并实时计算技术指标
                        # 买入信号检查：需要至少26根K线来计算EMA26，多获取一些以确保有足够的有效数据
                        cursor.execute("""
                            SELECT * 
                            FROM kline_data
                            WHERE symbol = %s AND timeframe = %s
                            ORDER BY timestamp DESC
                            LIMIT 50
                        """, (symbol, buy_timeframe))
                        buy_klines_raw = cursor.fetchall()
                        
                        # 记录K线数据情况
                        kline_count = len(buy_klines_raw) if buy_klines_raw else 0
                        logger.info(f"{symbol} 📈 获取到 {kline_count} 根K线数据（时间周期: {buy_timeframe}）")
                        
                        # 实时计算技术指标
                        if buy_klines_raw and len(buy_klines_raw) >= 9:  # 至少需要9根K线才能计算EMA9
                            import pandas as pd
                            tech_indicators = TechnicalIndicators()
                            # 转换为DataFrame（注意：需要按时间正序排列）
                            df = pd.DataFrame(list(reversed(buy_klines_raw)))
                            # 重命名列名以匹配技术指标计算所需的格式
                            if 'close_price' in df.columns:
                                df['close'] = pd.to_numeric(df['close_price'], errors='coerce')
                            elif 'close' not in df.columns:
                                # 如果既没有close_price也没有close，尝试其他可能的列名
                                logger.error(f"{symbol} ⚠️ DataFrame中找不到close或close_price列，可用列: {df.columns.tolist()}")
                                raise KeyError("close")
                            else:
                                df['close'] = pd.to_numeric(df['close'], errors='coerce')
                            
                            # 检查是否有有效的close价格
                            valid_close_count = df['close'].notna().sum()
                            logger.info(f"{symbol} 📊 有效收盘价数量: {valid_close_count}/{len(df)}")
                            
                            if valid_close_count < 9:
                                logger.warning(f"{symbol} ⚠️ 有效收盘价不足9个，无法计算EMA指标")
                            else:
                                # 计算EMA9（至少需要9根K线）
                                ema_short_series = tech_indicators.calculate_ema(df, period=9)
                                ema_short_valid = ema_short_series.notna().sum()
                                logger.info(f"{symbol} ✅ EMA9计算完成，有效值: {ema_short_valid}/{len(ema_short_series)}")
                                
                                # 计算EMA26（需要26根K线，如果不足则返回NaN）
                                if len(buy_klines_raw) >= 26:
                                    ema_long_series = tech_indicators.calculate_ema(df, period=26)
                                    ema_long_valid = ema_long_series.notna().sum()
                                    logger.info(f"{symbol} ✅ EMA26计算完成，有效值: {ema_long_valid}/{len(ema_long_series)}")
                                else:
                                    logger.warning(f"{symbol} ⚠️ K线数据不足26根（当前{kline_count}根），无法计算EMA26，需要至少26根K线")
                                    ema_long_series = pd.Series([None] * len(df))
                                
                                # 计算MA10和EMA10（如果需要）
                                if len(buy_klines_raw) >= 10:
                                    ma10_series = tech_indicators.calculate_ma(df, period=10)
                                    ema10_series = tech_indicators.calculate_ema(df, period=10)
                                else:
                                    ma10_series = pd.Series([None] * len(df))
                                    ema10_series = pd.Series([None] * len(df))
                                
                                # 计算MA5和EMA5（如果需要）
                                if len(buy_klines_raw) >= 5:
                                    ma5_series = tech_indicators.calculate_ma(df, period=5)
                                    ema5_series = tech_indicators.calculate_ema(df, period=5)
                                else:
                                    ma5_series = pd.Series([None] * len(df))
                                    ema5_series = pd.Series([None] * len(df))
                                
                                # 将指标值添加到K线数据中
                                ema_short_added = 0
                                ema_long_added = 0
                                for i, kline in enumerate(buy_klines_raw):
                                    idx = len(buy_klines_raw) - 1 - i  # 反转索引
                                    if idx < len(ema_short_series) and not pd.isna(ema_short_series.iloc[idx]):
                                        kline['ema_short'] = float(ema_short_series.iloc[idx])
                                        ema_short_added += 1
                                    if idx < len(ema_long_series) and not pd.isna(ema_long_series.iloc[idx]):
                                        kline['ema_long'] = float(ema_long_series.iloc[idx])
                                        ema_long_added += 1
                                    if idx < len(ma10_series) and not pd.isna(ma10_series.iloc[idx]):
                                        kline['ma10'] = float(ma10_series.iloc[idx])
                                    if idx < len(ema10_series) and not pd.isna(ema10_series.iloc[idx]):
                                        kline['ema10'] = float(ema10_series.iloc[idx])
                                    if idx < len(ma5_series) and not pd.isna(ma5_series.iloc[idx]):
                                        kline['ma5'] = float(ma5_series.iloc[idx])
                                    if idx < len(ema5_series) and not pd.isna(ema5_series.iloc[idx]):
                                        kline['ema5'] = float(ema5_series.iloc[idx])
                                logger.info(f"{symbol} 📝 EMA数据已添加到K线: EMA9={ema_short_added}根, EMA26={ema_long_added}根")
                        else:
                            logger.warning(f"{symbol} ⚠️ K线数据不足（需要至少9根，实际{kline_count}根），无法计算EMA指标。请检查数据库中是否有足够的K线数据。")
                        
                        # 只取最新的2根K线用于信号检测
                        buy_klines = buy_klines_raw[:2] if buy_klines_raw else []
                        
                        # 卖出信号检查：同样需要计算技术指标，多获取一些以确保有足够的有效数据
                        cursor.execute("""
                            SELECT * 
                            FROM kline_data
                            WHERE symbol = %s AND timeframe = %s
                            ORDER BY timestamp DESC
                            LIMIT 50
                        """, (symbol, sell_timeframe))
                        sell_klines_raw = cursor.fetchall()
                        
                        # 实时计算技术指标
                        if sell_klines_raw and len(sell_klines_raw) >= 26:
                            import pandas as pd
                            tech_indicators = TechnicalIndicators()
                            # 转换为DataFrame（注意：需要按时间正序排列）
                            df = pd.DataFrame(list(reversed(sell_klines_raw)))
                            # 重命名列名以匹配技术指标计算所需的格式
                            if 'close_price' in df.columns:
                                df['close'] = pd.to_numeric(df['close_price'], errors='coerce')
                            elif 'close' not in df.columns:
                                # 如果既没有close_price也没有close，尝试其他可能的列名
                                logger.error(f"{symbol} ⚠️ DataFrame中找不到close或close_price列，可用列: {df.columns.tolist()}")
                                raise KeyError("close")
                            else:
                                df['close'] = pd.to_numeric(df['close'], errors='coerce')
                            
                            # 计算EMA9和EMA26
                            ema_short_series = tech_indicators.calculate_ema(df, period=9)
                            ema_long_series = tech_indicators.calculate_ema(df, period=26)
                            
                            # 计算MA10和EMA10（如果需要）
                            ma10_series = tech_indicators.calculate_ma(df, period=10)
                            ema10_series = tech_indicators.calculate_ema(df, period=10)
                            
                            # 计算MA5和EMA5（如果需要）
                            ma5_series = tech_indicators.calculate_ma(df, period=5)
                            ema5_series = tech_indicators.calculate_ema(df, period=5)
                            
                            # 将指标值添加到K线数据中
                            for i, kline in enumerate(sell_klines_raw):
                                idx = len(sell_klines_raw) - 1 - i  # 反转索引
                                if idx < len(ema_short_series) and not pd.isna(ema_short_series.iloc[idx]):
                                    kline['ema_short'] = float(ema_short_series.iloc[idx])
                                if idx < len(ema_long_series) and not pd.isna(ema_long_series.iloc[idx]):
                                    kline['ema_long'] = float(ema_long_series.iloc[idx])
                                if idx < len(ma10_series) and not pd.isna(ma10_series.iloc[idx]):
                                    kline['ma10'] = float(ma10_series.iloc[idx])
                                if idx < len(ema10_series) and not pd.isna(ema10_series.iloc[idx]):
                                    kline['ema10'] = float(ema10_series.iloc[idx])
                                if idx < len(ma5_series) and not pd.isna(ma5_series.iloc[idx]):
                                    kline['ma5'] = float(ma5_series.iloc[idx])
                                if idx < len(ema5_series) and not pd.isna(ema5_series.iloc[idx]):
                                    kline['ema5'] = float(ema5_series.iloc[idx])
                        
                        # 只取最新的2根K线用于信号检测
                        sell_klines = sell_klines_raw[:2] if sell_klines_raw else []
                        
                        if not buy_klines or len(buy_klines) < 2:
                            logger.debug(f"{symbol} K线数据不足（需要2根，实际{len(buy_klines) if buy_klines else 0}根），跳过")
                            continue
                        
                        # 检查是否有未成交的策略限价单（避免重复创建）
                        cursor.execute("""
                            SELECT COUNT(*) as count
                            FROM futures_orders
                            WHERE account_id = %s 
                            AND symbol = %s 
                            AND status = 'PENDING'
                            AND order_type = 'LIMIT'
                            AND order_source = 'strategy'
                            AND signal_id = %s
                        """, (account_id, symbol, strategy.get('id')))
                        pending_strategy_orders = cursor.fetchone()
                        has_pending_strategy_order = pending_strategy_orders and pending_strategy_orders.get('count', 0) > 0
                        
                        logger.info(f"{symbol} 🔍 开始检查交易信号: 持仓数={len(existing_positions)}, 未成交限价单={has_pending_strategy_order}, 配置方向={buy_directions}")
                        
                        # 检查买入信号：基于EMA(9,26)交叉
                        # - EMA9向上穿越EMA26（金叉）= 做多信号
                        # - EMA9向下穿越EMA26（死叉）= 做空信号
                        
                        # 检查是否可以开新仓（允许有持仓时开新仓，但需要检查最大持仓限制）
                        can_open_new_position = True
                        if max_positions is not None and len(existing_positions) >= max_positions:
                            can_open_new_position = False
                            logger.info(f"{symbol} ⚠️ 已达到最大持仓数限制（{max_positions}个），当前持仓{len(existing_positions)}个，跳过买入信号")
                        
                        if can_open_new_position and not has_pending_strategy_order:
                            latest_kline = buy_klines[0]
                            prev_kline = buy_klines[1]
                            
                            ema_short_exists = latest_kline.get('ema_short') is not None
                            ema_long_exists = latest_kline.get('ema_long') is not None
                            logger.info(f"{symbol} 📊 检查买入信号（EMA9/26交叉）: 最新K线时间={latest_kline.get('timestamp')}, EMA9存在={ema_short_exists}, EMA26存在={ema_long_exists}, EMA数据完整={ema_short_exists and ema_long_exists}")
                            
                            if not ema_short_exists:
                                logger.warning(f"{symbol} ⚠️ 最新K线缺少EMA9数据，可能原因：1) K线数据不足9根 2) 收盘价数据无效")
                            if not ema_long_exists:
                                logger.warning(f"{symbol} ⚠️ 最新K线缺少EMA26数据，可能原因：1) K线数据不足26根（当前需要至少26根） 2) 收盘价数据无效")
                            
                            if latest_kline.get('ema_short') and latest_kline.get('ema_long'):
                                ema_short = float(latest_kline['ema_short'])  # EMA9
                                ema_long = float(latest_kline['ema_long'])    # EMA26
                                prev_ema_short = float(prev_kline.get('ema_short', 0)) if prev_kline.get('ema_short') else None
                                prev_ema_long = float(prev_kline.get('ema_long', 0)) if prev_kline.get('ema_long') else None
                                
                                logger.debug(f"{symbol} ✅ EMA数据完整: 当前EMA9={ema_short:.4f}, EMA26={ema_long:.4f}, 前EMA9={prev_ema_short}, 前EMA26={prev_ema_long}")
                                
                                if prev_ema_short is None or prev_ema_long is None:
                                    logger.info(f"{symbol} ⚠️ 前一根K线缺少EMA数据，跳过交叉检测（前EMA9={prev_ema_short}, 前EMA26={prev_ema_long}）")
                                    continue
                                
                                # 检测EMA(9,26)交叉
                                # 金叉：EMA9向上穿越EMA26（做多信号）
                                is_golden_cross = (prev_ema_short <= prev_ema_long and ema_short > ema_long) or \
                                                 (prev_ema_short < prev_ema_long and ema_short >= ema_long)
                                
                                # 死叉：EMA9向下穿越EMA26（做空信号）
                                # 条件1：前EMA9 >= 前EMA26 且 当前EMA9 < 当前EMA26（从上方穿越到下方）
                                # 条件2：前EMA9 > 前EMA26 且 当前EMA9 <= 当前EMA26（从上方穿越到下方或持平）
                                is_death_cross = (prev_ema_short >= prev_ema_long and ema_short < ema_long) or \
                                                 (prev_ema_short > prev_ema_long and ema_short <= ema_long)
                                
                                # 详细记录死叉检测过程
                                if 'short' in buy_directions:
                                    logger.info(f"{symbol} 🔍 做空信号检测详情:")
                                    logger.info(f"   前EMA9={prev_ema_short:.6f}, 前EMA26={prev_ema_long:.6f}, 差值={prev_ema_short - prev_ema_long:.6f}")
                                    logger.info(f"   当前EMA9={ema_short:.6f}, 当前EMA26={ema_long:.6f}, 差值={ema_short - ema_long:.6f}")
                                    logger.info(f"   条件1(前>=后且当前<): {prev_ema_short >= prev_ema_long} and {ema_short < ema_long} = {prev_ema_short >= prev_ema_long and ema_short < ema_long}")
                                    logger.info(f"   条件2(前>后且当前<=): {prev_ema_short > prev_ema_long} and {ema_short <= ema_long} = {prev_ema_short > prev_ema_long and ema_short <= ema_long}")
                                    logger.info(f"   死叉结果: {is_death_cross}")
                                
                                # 记录EMA交叉检测结果（使用info级别以便追踪）
                                logger.info(f"{symbol} 📊 EMA(9,26)交叉检测: 前EMA9={prev_ema_short:.4f}, 前EMA26={prev_ema_long:.4f}, 当前EMA9={ema_short:.4f}, 当前EMA26={ema_long:.4f}")
                                logger.info(f"{symbol} 📊 交叉状态: 向上穿越(做多)={is_golden_cross}, 向下穿越(做空)={is_death_cross}, 配置方向={buy_directions}")
                                
                                # 根据交叉类型和配置的方向确定交易信号
                                signal_triggered = False
                                target_direction = None
                                
                                if is_golden_cross and 'long' in buy_directions:
                                    # EMA9向上穿越EMA26 = 做多信号
                                    signal_triggered = True
                                    target_direction = 'long'
                                    logger.info(f"{symbol} ✅ 检测到EMA(9,26)向上穿越信号（做多）！EMA9={ema_short:.4f} > EMA26={ema_long:.4f}")
                                elif is_death_cross and 'short' in buy_directions:
                                    # EMA9向下穿越EMA26 = 做空信号
                                    signal_triggered = True
                                    target_direction = 'short'
                                    logger.info(f"{symbol} ✅ 检测到EMA(9,26)向下穿越信号（做空）！EMA9={ema_short:.4f} < EMA26={ema_long:.4f}")
                                else:
                                    # 记录为什么没有触发信号
                                    if is_golden_cross and 'long' not in buy_directions:
                                        logger.info(f"{symbol} ⚠️ 检测到向上穿越，但未配置做多方向（buyDirection={buy_directions}）")
                                        # 即使方向未配置，也记录这个信号（用于分析）
                                        hit_signal_type = 'BUY_LONG'
                                        logger.info(f"{symbol} 📝 准备记录命中信息（方向未配置）: 策略={strategy.get('name')}, 信号={hit_signal_type}")
                                        try:
                                            result = self.hit_recorder.record_signal_hit(
                                                strategy=strategy,
                                                symbol=symbol,
                                                signal_type=hit_signal_type,
                                                signal_source='ema_9_26',
                                                signal_timeframe=buy_timeframe,
                                                kline_data=latest_kline,
                                                direction='long',
                                                executed=False,
                                                execution_result='SKIPPED',
                                                execution_reason=f'方向未配置: buyDirection={buy_directions}',
                                                volume_ratio=float(latest_kline.get('volume_ratio', 1.0))
                                            )
                                            if result:
                                                logger.info(f"{symbol} ✅ 命中信息记录成功（方向未配置）")
                                        except Exception as e:
                                            logger.error(f"{symbol} ❌ 记录命中信息时出错: {e}")
                                    elif is_death_cross and 'short' not in buy_directions:
                                        logger.info(f"{symbol} ⚠️ 检测到向下穿越，但未配置做空方向（buyDirection={buy_directions}）")
                                        # 即使方向未配置，也记录这个信号（用于分析）
                                        hit_signal_type = 'BUY_SHORT'
                                        logger.info(f"{symbol} 📝 准备记录命中信息（方向未配置）: 策略={strategy.get('name')}, 信号={hit_signal_type}")
                                        try:
                                            result = self.hit_recorder.record_signal_hit(
                                                strategy=strategy,
                                                symbol=symbol,
                                                signal_type=hit_signal_type,
                                                signal_source='ema_9_26',
                                                signal_timeframe=buy_timeframe,
                                                kline_data=latest_kline,
                                                direction='short',
                                                executed=False,
                                                execution_result='SKIPPED',
                                                execution_reason=f'方向未配置: buyDirection={buy_directions}',
                                                volume_ratio=float(latest_kline.get('volume_ratio', 1.0))
                                            )
                                            if result:
                                                logger.info(f"{symbol} ✅ 命中信息记录成功（方向未配置）")
                                        except Exception as e:
                                            logger.error(f"{symbol} ❌ 记录命中信息时出错: {e}")
                                    elif not is_golden_cross and not is_death_cross:
                                        # 即使没有交叉，也显示当前EMA状态，帮助调试
                                        ema_status = "多头" if ema_short > ema_long else "空头" if ema_short < ema_long else "持平"
                                        prev_ema_status = "多头" if prev_ema_short > prev_ema_long else "空头" if prev_ema_short < prev_ema_long else "持平"
                                        logger.info(f"{symbol} 📊 未检测到交叉信号: 当前EMA9={ema_short:.4f}, EMA26={ema_long:.4f} ({ema_status}), 前EMA9={prev_ema_short:.4f}, 前EMA26={prev_ema_long:.4f} ({prev_ema_status})")
                                
                                # 即使没有触发信号，也记录检测过程（用于追踪和分析）
                                # 记录"未检测到信号"的情况，这样可以看到策略的检测频率
                                if not signal_triggered:
                                    logger.debug(f"{symbol} ⏭️ 未触发交易信号，跳过（可能原因：未检测到交叉、方向未配置、或其他条件）")
                                    # 记录未检测到信号的情况（可选，如果不想记录可以注释掉）
                                    # 这里不记录，因为会产生大量无用记录
                                    continue
                                
                                # 交易方向已经根据交叉类型确定
                                direction = target_direction
                                ema_bullish = ema_short > ema_long  # EMA9 > EMA26 表示多头
                                signal_type = '向上穿越' if is_golden_cross else '向下穿越'
                                
                                logger.info(f"{symbol} ✅ 检测到EMA(9,26){signal_type}信号（{direction}）！开始检查交易条件...")
                                
                                # 记录信号命中（在检查过滤条件之前）
                                signal_strength_ok = True
                                ema_strength_pct = None
                                if min_ema_cross_strength > 0:
                                    ema_diff = ema_short - ema_long
                                    ema_strength_pct = abs(ema_diff / ema_long * 100) if ema_long > 0 else 0
                                    signal_strength_ok = ema_strength_pct >= min_ema_cross_strength
                                
                                # 记录信号命中
                                hit_signal_type = 'BUY_LONG' if direction == 'long' else 'BUY_SHORT'
                                logger.info(f"{symbol} 📝 准备记录命中信息: 策略={strategy.get('name')}, 信号={hit_signal_type}")
                                try:
                                    result = self.hit_recorder.record_signal_hit(
                                        strategy=strategy,
                                        symbol=symbol,
                                        signal_type=hit_signal_type,
                                        signal_source='ema_9_26',
                                        signal_timeframe=buy_timeframe,
                                        kline_data=latest_kline,
                                        direction=direction,
                                        executed=False,  # 稍后会更新
                                        execution_result=None,
                                        volume_ratio=float(latest_kline.get('volume_ratio', 1.0)),
                                        signal_strength_ok=signal_strength_ok
                                    )
                                    if result:
                                        logger.info(f"{symbol} ✅ 命中信息记录成功")
                                    else:
                                        logger.warning(f"{symbol} ⚠️ 命中信息记录失败（返回False）")
                                except Exception as e:
                                    logger.error(f"{symbol} ❌ 记录命中信息时出错: {e}")
                                    import traceback
                                    traceback.print_exc()
                                
                                # 检查信号强度过滤
                                if min_ema_cross_strength > 0:
                                    if not signal_strength_ok:
                                        logger.info(f"{symbol} ⚠️ EMA9/26{signal_type}信号强度不足 (差值={ema_strength_pct:.2f}%, 需要≥{min_ema_cross_strength:.2f}%)，已过滤")
                                        # 记录信号被过滤的情况
                                        try:
                                            self.hit_recorder.record_signal_hit(
                                                strategy=strategy,
                                                symbol=symbol,
                                                signal_type=hit_signal_type,
                                                signal_source='ema_9_26',
                                                signal_timeframe=buy_timeframe,
                                                kline_data=latest_kline,
                                                direction=direction,
                                                executed=False,
                                                execution_result='SKIPPED',
                                                execution_reason=f'信号强度不足: {ema_strength_pct:.2f}% < {min_ema_cross_strength:.2f}%',
                                                volume_ratio=float(latest_kline.get('volume_ratio', 1.0)),
                                                signal_strength_ok=False
                                            )
                                        except Exception as e:
                                            logger.error(f"{symbol} ❌ 记录被过滤信号时出错: {e}")
                                        continue
                                else:
                                    logger.debug(f"{symbol} 信号强度检查通过（未启用过滤）")
                                
                                logger.info(f"{symbol} 确定交易方向: {direction} (信号类型={signal_type}, EMA9={ema_short:.4f}, EMA26={ema_long:.4f}, EMA多头={ema_bullish})")
                                
                                # 检查成交量条件（根据交易方向选择对应的成交量条件）
                                volume_ratio = float(latest_kline.get('volume_ratio', 1.0))
                                volume_ok = True
                                
                                if direction == 'long':
                                    # 做多：检查是否启用了做多成交量条件
                                    if buy_volume_enabled and buy_volume_long_enabled:
                                        # 使用 buy_volume_long 或兼容旧格式 buy_volume
                                        volume_condition = buy_volume_long or buy_volume
                                        if volume_condition:
                                            try:
                                                required_ratio = float(volume_condition)
                                                volume_ok = volume_ratio >= required_ratio
                                                if not volume_ok:
                                                    logger.info(f"{symbol} ⚠️ 做多成交量不足: {volume_ratio:.2f}x < {required_ratio}x")
                                            except:
                                                volume_ok = False
                                else:
                                    # 做空：检查是否启用了做空成交量条件
                                    logger.info(f"{symbol} 📊 做空成交量检查: buy_volume_enabled={buy_volume_enabled}, buy_volume_short_enabled={buy_volume_short_enabled}, buy_volume_short={buy_volume_short}, 当前成交量比率={volume_ratio:.2f}x")
                                    
                                    # 修复：如果 buy_volume_short 有值，即使 buy_volume_short_enabled 未设置，也应该检查
                                    if buy_volume_enabled and (buy_volume_short_enabled or buy_volume_short):
                                        # 使用 buy_volume_short
                                        volume_condition = buy_volume_short
                                        if volume_condition:
                                            # 尝试解析为数值（支持 "0.3" 这样的格式）
                                            try:
                                                required_ratio = float(volume_condition)
                                                # 如果是数值格式，检查是否 >= 该值
                                                volume_ok = volume_ratio >= required_ratio
                                                if not volume_ok:
                                                    logger.info(f"{symbol} ⚠️ 做空成交量不足: {volume_ratio:.2f}x < {required_ratio}x（需要≥{required_ratio}x）")
                                                else:
                                                    logger.info(f"{symbol} ✅ 做空成交量条件满足: {volume_ratio:.2f}x >= {required_ratio}x")
                                            except (ValueError, TypeError):
                                                # 如果不是数值，按字符串格式处理
                                                if volume_condition == '>1':
                                                    volume_ok = volume_ratio > 1.0
                                                elif volume_condition == '0.8-1':
                                                    volume_ok = 0.8 <= volume_ratio <= 1.0
                                                elif volume_condition == '0.6-0.8':
                                                    volume_ok = 0.6 <= volume_ratio < 0.8
                                                elif volume_condition == '<0.6':
                                                    volume_ok = volume_ratio < 0.6
                                                else:
                                                    volume_ok = False
                                                    logger.warning(f"{symbol} 做空成交量条件格式错误: {volume_condition}")
                                                if not volume_ok:
                                                    logger.info(f"{symbol} ⚠️ 做空成交量条件不满足: {volume_ratio:.2f}x, 需要: {volume_condition}")
                                                else:
                                                    logger.info(f"{symbol} ✅ 做空成交量条件满足: {volume_ratio:.2f}x, 条件: {volume_condition}")
                                    else:
                                        logger.info(f"{symbol} ✅ 做空成交量检查跳过（未启用或未配置）")
                                
                                # 检查 MA10/EMA10 信号强度（如果配置了）
                                ma10_ema10_ok = True
                                if latest_kline.get('ma10') and latest_kline.get('ema10'):
                                    ma10 = float(latest_kline['ma10'])
                                    ema10 = float(latest_kline['ema10'])
                                    
                                    logger.info(f"{symbol} 📊 MA10/EMA10数据: MA10={ma10:.4f}, EMA10={ema10:.4f}, 差值={ema10-ma10:.4f} ({'多头' if ema10 > ma10 else '空头' if ema10 < ma10 else '持平'})")
                                    
                                    # 检查MA10/EMA10信号强度过滤（无论是否启用trend_filter都要检查）
                                    if min_ma10_cross_strength > 0:
                                        ma10_ema10_diff = ema10 - ma10
                                        ma10_ema10_strength_pct = abs(ma10_ema10_diff / ma10 * 100) if ma10 > 0 else 0
                                        if ma10_ema10_strength_pct < min_ma10_cross_strength:
                                            logger.info(f"{symbol} ⚠️ MA10/EMA10信号强度不足 (差值={ma10_ema10_strength_pct:.2f}%, 需要≥{min_ma10_cross_strength:.2f}%)，已过滤")
                                            # 记录信号被过滤的情况
                                            try:
                                                self.hit_recorder.record_signal_hit(
                                                    strategy=strategy,
                                                    symbol=symbol,
                                                    signal_type=hit_signal_type,
                                                    signal_source='ema_9_26',
                                                    signal_timeframe=buy_timeframe,
                                                    kline_data=latest_kline,
                                                    direction=direction,
                                                    executed=False,
                                                    execution_result='SKIPPED',
                                                    execution_reason=f'MA10/EMA10信号强度不足: {ma10_ema10_strength_pct:.2f}% < {min_ma10_cross_strength:.2f}%',
                                                    volume_ratio=float(latest_kline.get('volume_ratio', 1.0)),
                                                    signal_strength_ok=True,
                                                    ma10_ema10_trend_ok=None
                                                )
                                            except Exception as e:
                                                logger.error(f"{symbol} ❌ 记录被过滤信号时出错: {e}")
                                            continue
                                    
                                    # 检查 MA10/EMA10 是否与交易方向同向（如果启用了过滤）
                                    if ma10_ema10_trend_filter:
                                        if direction == 'long':
                                            # 做多：需要 EMA10 > MA10（MA10/EMA10 多头）
                                            ma10_ema10_ok = ema10 > ma10
                                            if not ma10_ema10_ok:
                                                logger.info(f"{symbol} ⚠️ 做多但MA10/EMA10不同向: EMA10={ema10:.4f} <= MA10={ma10:.4f}（需要EMA10 > MA10）")
                                            else:
                                                logger.info(f"{symbol} ✅ 做多MA10/EMA10同向: EMA10={ema10:.4f} > MA10={ma10:.4f}")
                                        else:  # short
                                            # 做空：需要 EMA10 < MA10（MA10/EMA10 空头）
                                            ma10_ema10_ok = ema10 < ma10
                                            if not ma10_ema10_ok:
                                                logger.info(f"{symbol} ⚠️ 做空但MA10/EMA10不同向: EMA10={ema10:.4f} >= MA10={ma10:.4f}（需要EMA10 < MA10），做空信号被过滤")
                                                logger.info(f"{symbol} 💡 提示：如果希望更多做空机会，可以在策略配置中关闭'启用 MA10/EMA10 同向过滤'选项")
                                            else:
                                                logger.info(f"{symbol} ✅ 做空MA10/EMA10同向: EMA10={ema10:.4f} < MA10={ma10:.4f}")
                                else:
                                    # 如果没有 MA10/EMA10 数据，记录警告
                                    if min_ma10_cross_strength > 0 or ma10_ema10_trend_filter:
                                        logger.warning(f"{symbol} ⚠️ 缺少 MA10/EMA10 数据，但启用了过滤条件")
                                        if min_ma10_cross_strength > 0:
                                            continue  # 如果要求信号强度但数据缺失，跳过
                                        # 如果只是启用了trend_filter但没有数据，允许继续（不强制要求）
                                        logger.info(f"{symbol} ⚠️ MA10/EMA10数据缺失，但trend_filter已启用，允许继续（可能影响交易决策）")
                                
                                # 检查趋势持续性（如果启用了）
                                trend_confirm_ok = True
                                if trend_confirm_bars > 0:
                                    # 需要获取更多历史K线来检查趋势持续性
                                    cursor.execute("""
                                        SELECT k.*, t.* 
                                        FROM kline_data k
                                        LEFT JOIN (
                                            SELECT t1.* 
                                            FROM technical_indicators_cache t1
                                            INNER JOIN (
                                                SELECT symbol, timeframe, MAX(updated_at) as max_updated_at
                                                FROM technical_indicators_cache
                                                WHERE symbol = %s AND timeframe = %s
                                                GROUP BY symbol, timeframe
                                            ) t2 ON t1.symbol = t2.symbol 
                                                AND t1.timeframe = t2.timeframe 
                                                AND t1.updated_at = t2.max_updated_at
                                        ) t ON k.symbol = t.symbol AND k.timeframe = t.timeframe
                                        WHERE k.symbol = %s AND k.timeframe = %s
                                        ORDER BY k.timestamp DESC
                                        LIMIT %s
                                    """, (symbol, buy_timeframe, symbol, buy_timeframe, trend_confirm_bars + 2))
                                    history_klines = cursor.fetchall()
                                    
                                    if len(history_klines) >= trend_confirm_bars + 1:
                                        # 检查从交叉发生到现在是否一直保持趋势
                                        trend_maintained = True
                                        for i in range(len(history_klines) - 1):
                                            check_kline = history_klines[i]
                                            check_ema_short = float(check_kline.get('ema_short', 0)) if check_kline.get('ema_short') else None
                                            check_ema_long = float(check_kline.get('ema_long', 0)) if check_kline.get('ema_long') else None
                                            
                                            if check_ema_short and check_ema_long:
                                                if direction == 'long' and check_ema_short <= check_ema_long:
                                                    trend_maintained = False
                                                    break
                                                elif direction == 'short' and check_ema_short >= check_ema_long:
                                                    trend_maintained = False
                                                    break
                                        
                                        if not trend_maintained:
                                            trend_confirm_ok = False
                                            logger.info(f"{symbol} ⚠️ 趋势持续性检查失败（{signal_type}后趋势未持续{trend_confirm_bars}个周期）")
                                    else:
                                        # 历史K线不足，无法检查趋势持续性
                                        trend_confirm_ok = False
                                        logger.debug(f"{symbol} 历史K线不足，无法检查趋势持续性（需要{trend_confirm_bars + 2}根，仅{len(history_klines)}根）")
                                
                                # 检查同方向持仓限制（在检查其他条件之前）
                                position_limit_ok = True
                                if direction == 'long' and max_long_positions is not None:
                                    long_positions_count = len([p for p in existing_positions if p.get('position_side') == 'LONG'])
                                    if long_positions_count >= max_long_positions:
                                        position_limit_ok = False
                                        logger.info(f"{symbol} ⚠️ 已达到最大做多持仓数限制（{max_long_positions}个），当前做多持仓{long_positions_count}个，跳过买入信号")
                                elif direction == 'short' and max_short_positions is not None:
                                    short_positions_count = len([p for p in existing_positions if p.get('position_side') == 'SHORT'])
                                    if short_positions_count >= max_short_positions:
                                        position_limit_ok = False
                                        logger.info(f"{symbol} ⚠️ 已达到最大做空持仓数限制（{max_short_positions}个），当前做空持仓{short_positions_count}个，跳过买入信号")
                                
                                # 总结所有条件检查结果
                                all_conditions_met = volume_ok and ma10_ema10_ok and trend_confirm_ok and position_limit_ok
                                logger.info(f"{symbol} 📋 交易条件检查总结: 成交量={volume_ok}, MA10/EMA10={ma10_ema10_ok}, 趋势持续性={trend_confirm_ok}, 持仓限制={position_limit_ok}, 全部满足={all_conditions_met}")
                                
                                # 获取最近一次命中记录的ID（用于后续更新）
                                hit_id = None
                                try:
                                    # 查询最近一次该策略和交易对的命中记录
                                    cursor.execute("""
                                        SELECT id FROM strategy_hits
                                        WHERE strategy_id = %s AND symbol = %s
                                        ORDER BY created_at DESC
                                        LIMIT 1
                                    """, (strategy.get('id'), symbol))
                                    hit_record = cursor.fetchone()
                                    if hit_record:
                                        hit_id = hit_record['id']
                                except Exception as e:
                                    logger.debug(f"查询命中记录ID失败: {e}")
                                
                                if not all_conditions_met:
                                    failed_conditions = []
                                    if not volume_ok:
                                        failed_conditions.append("成交量条件")
                                    if not ma10_ema10_ok:
                                        failed_conditions.append("MA10/EMA10过滤（做空需要EMA10 < MA10）")
                                    if not trend_confirm_ok:
                                        failed_conditions.append("趋势持续性")
                                    if not position_limit_ok:
                                        failed_conditions.append("持仓限制")
                                    logger.info(f"{symbol} ❌ 交易条件未全部满足，失败的条件: {', '.join(failed_conditions)}")
                                    if direction == 'short' and not ma10_ema10_ok:
                                        logger.info(f"{symbol} 💡 做空建议：如果希望更多做空机会，可以在策略配置中关闭'启用 MA10/EMA10 同向过滤'选项")
                                    
                                    # 更新命中记录：条件未满足，未执行
                                    if hit_id:
                                        self.hit_recorder.update_execution_result(
                                            hit_id=hit_id,
                                            executed=False,
                                            execution_result='SKIPPED',
                                            execution_reason=f"条件未满足: {', '.join(failed_conditions)}"
                                        )
                                
                                if volume_ok and ma10_ema10_ok and trend_confirm_ok and all_conditions_met:
                                    action_name = '买入(做多)' if direction == 'long' else '卖出(做空)'
                                    logger.info(f"{symbol} ✅ 所有交易条件满足，准备执行{action_name}...")
                                    
                                    # 开仓前先平掉相反方向的持仓（如果启用）
                                    if close_opposite_on_entry:
                                        opposite_side = 'SHORT' if direction == 'long' else 'LONG'
                                        opposite_positions = [p for p in existing_positions if p.get('position_side') == opposite_side]
                                        if opposite_positions:
                                            logger.info(f"{symbol} 🔄 开{direction}仓前，先平掉{len(opposite_positions)}个{opposite_side}持仓")
                                            for opp_position in opposite_positions:
                                                try:
                                                    result = self.futures_engine.close_position(
                                                        position_id=opp_position['id'],
                                                        reason=f'开{direction}仓前平仓'
                                                    )
                                                    if result.get('success'):
                                                        logger.info(f"{symbol} ✅ 已平掉{opposite_side}持仓 ID {opp_position['id']}")
                                                        # 从列表中移除已平仓的持仓
                                                        existing_positions.remove(opp_position)
                                                    else:
                                                        logger.warning(f"{symbol} ⚠️ 平掉{opposite_side}持仓失败: {result.get('message', '未知错误')}")
                                                except Exception as e:
                                                    logger.error(f"{symbol} ❌ 平掉{opposite_side}持仓时出错: {e}")
                                    
                                    # 执行开仓
                                    # 获取实时价格用于计算
                                    try:
                                        current_price = float(self.futures_engine.get_current_price(symbol, use_realtime=True))
                                        if not current_price or current_price <= 0:
                                            # 如果实时价格获取失败，使用K线收盘价
                                            current_price = float(latest_kline['close_price'])
                                            logger.warning(f"{symbol} 实时价格获取失败，使用K线收盘价: {current_price}")
                                    except Exception as e:
                                        # 如果获取实时价格出错，使用K线收盘价
                                        current_price = float(latest_kline['close_price'])
                                        logger.warning(f"{symbol} 获取实时价格出错，使用K线收盘价: {current_price}, 错误: {e}")
                                    
                                    # 计算限价（如果有）或使用市价
                                    limit_price = None
                                    if direction == 'long':
                                        # 做多价格处理
                                        if long_price_type == 'market':
                                            # 市价单：使用实时价格（做多使用卖一价，但get_current_price返回的是中间价，这里先使用实时价格）
                                            limit_price = None  # 市价单，不设置限价
                                        elif long_price_type == 'market_minus_0_2':
                                            limit_price = Decimal(str(current_price * 0.998))
                                        elif long_price_type == 'market_minus_0_4':
                                            limit_price = Decimal(str(current_price * 0.996))
                                        elif long_price_type == 'market_minus_0_6':
                                            limit_price = Decimal(str(current_price * 0.994))
                                        elif long_price_type == 'market_minus_0_8':
                                            limit_price = Decimal(str(current_price * 0.992))
                                        elif long_price_type == 'market_minus_1':
                                            limit_price = Decimal(str(current_price * 0.99))
                                    else:
                                        # 做空价格处理
                                        if short_price_type == 'market':
                                            # 市价单：使用实时价格（做空使用买一价）
                                            limit_price = None  # 市价单，不设置限价
                                        elif short_price_type == 'market_plus_0_2':
                                            limit_price = Decimal(str(current_price * 1.002))
                                        elif short_price_type == 'market_plus_0_4':
                                            limit_price = Decimal(str(current_price * 1.004))
                                        elif short_price_type == 'market_plus_0_6':
                                            limit_price = Decimal(str(current_price * 1.006))
                                        elif short_price_type == 'market_plus_0_8':
                                            limit_price = Decimal(str(current_price * 1.008))
                                        elif short_price_type == 'market_plus_1':
                                            limit_price = Decimal(str(current_price * 1.01))
                                    
                                    # 计算数量（使用当前价格估算，实际成交价格可能略有不同）
                                    account_info = self.futures_engine.get_account(account_id)
                                    if not account_info or not account_info.get('success'):
                                        continue
                                    
                                    balance = Decimal(str(account_info['data']['current_balance']))
                                    position_value = balance * Decimal(str(position_size)) / Decimal('100')
                                    # 使用限价或当前价格计算数量
                                    price_for_quantity = float(limit_price) if limit_price else current_price
                                    quantity = (position_value * Decimal(str(leverage))) / Decimal(str(price_for_quantity))
                                    # 根据交易对精度对数量进行四舍五入
                                    quantity = round_quantity(quantity, symbol)
                                    
                                    # 计算止损止盈价格（基于估算的入场价格）
                                    # 注意：实际止损止盈价格会在开仓后根据实际成交价格重新计算
                                    estimated_entry_price = float(limit_price) if limit_price else current_price
                                    stop_loss_price = None
                                    take_profit_price = None
                                    if stop_loss_pct:
                                        if direction == 'long':
                                            stop_loss_price = Decimal(str(estimated_entry_price * (1 - float(stop_loss_pct) / 100)))
                                        else:
                                            stop_loss_price = Decimal(str(estimated_entry_price * (1 + float(stop_loss_pct) / 100)))
                                    if take_profit_pct:
                                        if direction == 'long':
                                            take_profit_price = Decimal(str(estimated_entry_price * (1 + float(take_profit_pct) / 100)))
                                        else:
                                            take_profit_price = Decimal(str(estimated_entry_price * (1 - float(take_profit_pct) / 100)))
                                    
                                    # 开仓
                                    position_side = 'LONG' if direction == 'long' else 'SHORT'
                                    logger.info(f"{symbol} 🚀 执行{action_name}开仓: 方向={position_side}, 数量={quantity}, 杠杆={leverage}, 限价={limit_price}, 止损={stop_loss_price}, 止盈={take_profit_price}")
                                    
                                    result = self.futures_engine.open_position(
                                        account_id=account_id,
                                        symbol=symbol,
                                        position_side=position_side,
                                        quantity=quantity,
                                        leverage=leverage,
                                        limit_price=limit_price,
                                        stop_loss_price=stop_loss_price,
                                        take_profit_price=take_profit_price,
                                        stop_loss_pct=Decimal(str(stop_loss_pct)) / 100 if stop_loss_pct else None,
                                        take_profit_pct=Decimal(str(take_profit_pct)) / 100 if take_profit_pct else None,
                                        source='strategy',
                                        signal_id=strategy.get('id')
                                    )
                                    
                                    if result.get('success'):
                                        logger.info(f"{symbol} ✅ {action_name}开仓成功！")
                                        # 使用实际成交价格（从结果中获取）
                                        # open_position 返回的结果中，entry_price 是实际成交价格
                                        actual_entry_price = result.get('entry_price')
                                        if not actual_entry_price:
                                            # 如果没有 entry_price，尝试从其他字段获取
                                            actual_entry_price = result.get('current_price') or result.get('limit_price') or estimated_entry_price
                                        
                                        # 获取持仓ID和订单ID
                                        position_id = result.get('position_id')
                                        order_id = result.get('order_id')
                                        
                                        # 更新命中记录：执行成功
                                        if hit_id:
                                            self.hit_recorder.update_execution_result(
                                                hit_id=hit_id,
                                                executed=True,
                                                execution_result='SUCCESS',
                                                execution_reason='所有条件满足，已执行开仓',
                                                position_id=position_id,
                                                order_id=str(order_id) if order_id else None
                                            )
                                        
                                        results.append({
                                            'symbol': symbol,
                                            'action': 'buy',
                                            'direction': direction,
                                            'price': float(actual_entry_price) if isinstance(actual_entry_price, Decimal) else actual_entry_price,
                                            'quantity': float(quantity),
                                            'success': True
                                        })
                                        price_info = f"实际: {actual_entry_price:.4f}"
                                        if limit_price:
                                            price_info += f", 限价: {limit_price:.4f}"
                                        else:
                                            price_info += f", 市价(估算: {estimated_entry_price:.4f})"
                                        # 记录当前时间（本地时间）
                                        current_time_str = get_local_time().strftime('%Y-%m-%d %H:%M:%S')
                                        # 根据交易对确定数量显示精度
                                        qty_precision = get_quantity_precision(symbol)
                                        logger.info(f"{current_time_str}: ✅ 策略{action_name}: {symbol} {direction} @ {price_info}, 数量={float(quantity):.{qty_precision}f}")
                                    else:
                                        # 执行失败，更新命中记录
                                        if hit_id:
                                            self.hit_recorder.update_execution_result(
                                                hit_id=hit_id,
                                                executed=False,
                                                execution_result='FAILED',
                                                execution_reason=result.get('message', '开仓失败')
                                            )
                        
                        # 检查卖出信号（平仓）
                        if len(existing_positions) > 0:
                            if sell_klines and len(sell_klines) >= 2:
                                latest_sell_kline = sell_klines[0]
                                prev_sell_kline = sell_klines[1]
                                
                                # 先检查趋势反转退出机制（优先级高于卖出信号）
                                should_exit = False
                                exit_reason = None
                                
                                # 检查 MA10/EMA10 反转退出
                                if exit_on_ma_flip:
                                    if latest_sell_kline.get('ma10') and latest_sell_kline.get('ema10') and \
                                       prev_sell_kline.get('ma10') and prev_sell_kline.get('ema10'):
                                        ma10 = float(latest_sell_kline['ma10'])
                                        ema10 = float(latest_sell_kline['ema10'])
                                        prev_ma10 = float(prev_sell_kline['ma10'])
                                        prev_ema10 = float(prev_sell_kline['ema10'])
                                        
                                        # 检查是否反转（从多头转为空头，或从空头转为多头）
                                        prev_bullish = prev_ema10 > prev_ma10
                                        curr_bullish = ema10 > ma10
                                        
                                        if prev_bullish != curr_bullish:
                                            should_exit = True
                                            exit_reason = 'MA10/EMA10反转'
                                            logger.info(f"⚠️ {symbol} 检测到MA10/EMA10反转，触发退出机制")
                                
                                # 检查 EMA 弱信号退出
                                if not should_exit and exit_on_ema_weak:
                                    if latest_sell_kline.get('ema_short') and latest_sell_kline.get('ema_long'):
                                        ema_short = float(latest_sell_kline['ema_short'])
                                        ema_long = float(latest_sell_kline['ema_long'])
                                        ema_diff = abs(ema_short - ema_long)
                                        ema_diff_pct = (ema_diff / ema_long * 100) if ema_long > 0 else 0
                                        
                                        if ema_diff_pct < 0.05:  # EMA差值<0.05%
                                            should_exit = True
                                            exit_reason = 'EMA信号过弱'
                                            logger.info(f"⚠️ {symbol} EMA差值过小({ema_diff_pct:.2f}%)，触发退出机制")
                                
                                # 如果触发趋势反转退出，立即平仓
                                if should_exit:
                                    for position in existing_positions:
                                        result = self.futures_engine.close_position(
                                            position_id=position['id'],
                                            reason=f'strategy_exit_{exit_reason}'
                                        )
                                        
                                        if result.get('success'):
                                            results.append({
                                                'symbol': symbol,
                                                'action': 'sell',
                                                'position_id': position['id'],
                                                'success': True,
                                                'exit_reason': exit_reason
                                            })
                                            logger.info(f"✅ 策略趋势反转退出: {symbol} 持仓ID {position['id']}，原因: {exit_reason}")
                                    continue  # 已平仓，跳过后续卖出信号检查
                                
                                # 根据卖出信号类型检查不同的死叉
                                is_death_cross = False
                                
                                if sell_signal == 'ma_ema5':
                                    # MA5/EMA5死叉
                                    if latest_sell_kline.get('ma5') and latest_sell_kline.get('ema5'):
                                        ma5 = float(latest_sell_kline['ma5'])
                                        ema5 = float(latest_sell_kline['ema5'])
                                        prev_ma5 = float(prev_sell_kline.get('ma5', 0))
                                        prev_ema5 = float(prev_sell_kline.get('ema5', 0))
                                        
                                        # 死叉检测：EMA5下穿MA5
                                        is_death_cross = (prev_ema5 >= prev_ma5 and ema5 < ma5) or \
                                                        (prev_ema5 > prev_ma5 and ema5 <= ma5)
                                elif sell_signal == 'ma_ema10':
                                    # MA10/EMA10死叉
                                    if latest_sell_kline.get('ma10') and latest_sell_kline.get('ema10'):
                                        ma10 = float(latest_sell_kline['ma10'])
                                        ema10 = float(latest_sell_kline['ema10'])
                                        prev_ma10 = float(prev_sell_kline.get('ma10', 0))
                                        prev_ema10 = float(prev_sell_kline.get('ema10', 0))
                                        
                                        # 死叉检测：EMA10下穿MA10
                                        is_death_cross = (prev_ema10 >= prev_ma10 and ema10 < ma10) or \
                                                        (prev_ema10 > prev_ma10 and ema10 <= ma10)
                                elif sell_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                                    # EMA9/26死叉
                                    if latest_sell_kline.get('ema_short') and latest_sell_kline.get('ema_long'):
                                        ema_short = float(latest_sell_kline['ema_short'])
                                        ema_long = float(latest_sell_kline['ema_long'])
                                        prev_ema_short = float(prev_sell_kline.get('ema_short', 0))
                                        prev_ema_long = float(prev_sell_kline.get('ema_long', 0))
                                        
                                        # 死叉检测：短期EMA下穿长期EMA
                                        is_death_cross = (prev_ema_short >= prev_ema_long and ema_short < ema_long) or \
                                                        (prev_ema_short > prev_ema_long and ema_short <= ema_long)
                                    
                                    if is_death_cross:
                                        # 记录卖出信号命中
                                        logger.info(f"{symbol} 📝 准备记录卖出信号命中信息")
                                        try:
                                            result = self.hit_recorder.record_signal_hit(
                                                strategy=strategy,
                                                symbol=symbol,
                                                signal_type='SELL',
                                                signal_source=sell_signal,
                                                signal_timeframe=sell_timeframe,
                                                kline_data=latest_sell_kline,
                                                direction=None,  # 卖出信号不区分方向
                                                executed=False,  # 稍后会更新
                                                execution_result=None
                                            )
                                            if result:
                                                logger.info(f"{symbol} ✅ 卖出信号命中信息记录成功")
                                            else:
                                                logger.warning(f"{symbol} ⚠️ 卖出信号命中信息记录失败（返回False）")
                                        except Exception as e:
                                            logger.error(f"{symbol} ❌ 记录卖出信号命中信息时出错: {e}")
                                            import traceback
                                            traceback.print_exc()
                                        
                                        # 检查成交量条件
                                        volume_ratio = float(latest_sell_kline.get('volume_ratio', 1.0))
                                        volume_ok = True
                                        if sell_volume_enabled and sell_volume:
                                            if sell_volume == '>1':
                                                # 成交量 > 1倍
                                                volume_ok = volume_ratio > 1.0
                                            elif sell_volume == '0.8-1':
                                                # 成交量 0.8 <= x <= 1
                                                volume_ok = 0.8 <= volume_ratio <= 1.0
                                            elif sell_volume == '0.6-0.8':
                                                # 成交量 0.6 <= x < 0.8
                                                volume_ok = 0.6 <= volume_ratio < 0.8
                                            elif sell_volume == '<0.6':
                                                # 成交量 < 0.6
                                                volume_ok = volume_ratio < 0.6
                                            else:
                                                # 兼容旧格式（向后兼容）
                                                try:
                                                    required_ratio = float(sell_volume.replace('<', '').replace('≤', ''))
                                                    if sell_volume.startswith('<'):
                                                        volume_ok = volume_ratio < required_ratio
                                                    else:
                                                        volume_ok = volume_ratio <= required_ratio
                                                except:
                                                    volume_ok = False
                                        
                                        if volume_ok:
                                            # 获取最近一次卖出信号命中记录的ID
                                            sell_hit_id = None
                                            try:
                                                cursor.execute("""
                                                    SELECT id FROM strategy_hits
                                                    WHERE strategy_id = %s AND symbol = %s AND signal_type = 'SELL'
                                                    ORDER BY created_at DESC
                                                    LIMIT 1
                                                """, (strategy.get('id'), symbol))
                                                sell_hit_record = cursor.fetchone()
                                                if sell_hit_record:
                                                    sell_hit_id = sell_hit_record['id']
                                            except Exception as e:
                                                logger.debug(f"查询卖出信号命中记录ID失败: {e}")
                                            
                                            # 平仓所有持仓
                                            for position in existing_positions:
                                                result = self.futures_engine.close_position(
                                                    position_id=position['id'],
                                                    reason='strategy_signal'
                                                )
                                                
                                                if result.get('success'):
                                                    # 更新卖出信号命中记录
                                                    if sell_hit_id:
                                                        self.hit_recorder.update_execution_result(
                                                            hit_id=sell_hit_id,
                                                            executed=True,
                                                            execution_result='SUCCESS',
                                                            execution_reason='卖出信号触发，已平仓',
                                                            position_id=position['id']
                                                        )
                                                    
                                                    results.append({
                                                        'symbol': symbol,
                                                        'action': 'sell',
                                                        'position_id': position['id'],
                                                        'success': True
                                                    })
                                                    logger.info(f"✅ 策略平仓: {symbol} 持仓ID {position['id']}")
                                                else:
                                                    # 平仓失败，更新命中记录
                                                    if sell_hit_id:
                                                        self.hit_recorder.update_execution_result(
                                                            hit_id=sell_hit_id,
                                                            executed=False,
                                                            execution_result='FAILED',
                                                            execution_reason=result.get('message', '平仓失败')
                                                        )
                                        else:
                                            # 成交量条件不满足，更新命中记录
                                            try:
                                                cursor.execute("""
                                                    SELECT id FROM strategy_hits
                                                    WHERE strategy_id = %s AND symbol = %s AND signal_type = 'SELL'
                                                    ORDER BY created_at DESC
                                                    LIMIT 1
                                                """, (strategy.get('id'), symbol))
                                                sell_hit_record = cursor.fetchone()
                                                if sell_hit_record:
                                                    self.hit_recorder.update_execution_result(
                                                        hit_id=sell_hit_record['id'],
                                                        executed=False,
                                                        execution_result='SKIPPED',
                                                        execution_reason=f'成交量条件不满足: {volume_ratio:.2f}x'
                                                    )
                                            except Exception as e:
                                                logger.debug(f"更新卖出信号命中记录失败: {e}")
                    
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
    
    def _load_strategies_from_file(self) -> List[Dict]:
        """从配置文件加载策略"""
        try:
            from pathlib import Path
            import json
            
            # 策略配置文件路径
            strategies_file = Path(__file__).parent.parent.parent / 'config' / 'strategies' / 'futures_strategies.json'
            
            # 如果文件不存在，返回空列表
            if not strategies_file.exists():
                return []
            
            # 读取策略配置
            with open(strategies_file, 'r', encoding='utf-8') as f:
                strategies = json.load(f)
            
            # 只返回启用的策略
            enabled_strategies = [s for s in strategies if s.get('enabled', False)]
            return enabled_strategies
            
        except Exception as e:
            logger.error(f"加载策略配置失败: {e}")
            return []
    
    async def check_and_execute_strategies(self):
        """检查并执行所有启用的策略"""
        try:
            # 从配置文件加载启用的策略
            strategies = self._load_strategies_from_file()
            
            if not strategies:
                logger.warning("⚠️ 未找到启用的策略，跳过策略检查")
                return
            
            logger.info(f"📊 找到 {len(strategies)} 个启用的策略，开始检查...")
            logger.debug(f"策略列表: {[s.get('name') for s in strategies]}")
            
            # 执行每个策略
            for strategy in strategies:
                try:
                    strategy_name = strategy.get('name', '未知')
                    logger.info(f"🔍 检查策略: {strategy_name} (ID: {strategy.get('id')})")
                    account_id = strategy.get('account_id', 2)
                    result = await self.execute_strategy(strategy, account_id=account_id)
                    
                    if result.get('success') and result.get('results'):
                        logger.info(f"策略 {strategy_name} 执行成功，执行了 {len(result['results'])} 个操作")
                    elif not result.get('success'):
                        logger.warning(f"策略 {strategy_name} 执行失败: {result.get('message', '未知错误')}")
                    else:
                        logger.debug(f"策略 {strategy_name} 检查完成，无交易操作")
                        
                except Exception as e:
                    logger.error(f"执行策略 {strategy.get('name', '未知')} 时出错: {e}")
                    import traceback
                    traceback.print_exc()
                    continue
                    
        except Exception as e:
            logger.error(f"检查策略时出错: {e}")
            import traceback
            traceback.print_exc()
    
    async def run_loop(self, interval: int = 5):
        """
        运行监控循环（实时监控模式）
        
        Args:
            interval: 检查间隔（秒），默认5秒（实时监控）
        """
        self.running = True
        logger.info(f"🔄 策略实时监控服务已启动（间隔: {interval}秒）")
        
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
    
    def start(self, interval: int = 30):
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

