"""
策略自动执行服务
定期检查启用的策略，根据EMA信号自动执行买入和平仓操作
"""

import asyncio
import pymysql
import pandas as pd
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Dict, List, Optional
from loguru import logger
from app.database.db_service import DatabaseService
from app.trading.futures_trading_engine import FuturesTradingEngine
from app.analyzers.technical_indicators import TechnicalIndicators
from app.services.strategy_hit_recorder import StrategyHitRecorder
from app.services.market_regime_detector import MarketRegimeDetector, get_regime_display_name


class StrategyExecutor:
    """策略自动执行器"""

    def __init__(self, db_config: Dict, futures_engine: FuturesTradingEngine, technical_analyzer=None):
        """
        初始化策略执行器

        Args:
            db_config: 数据库配置
            futures_engine: 合约交易引擎（模拟引擎）
            technical_analyzer: 技术分析器实例（可选）
        """
        self.db_config = db_config
        self.futures_engine = futures_engine  # 模拟引擎（保持兼容）
        self.live_engine = None  # 实盘引擎（延迟初始化）
        self.live_engine_error = None  # 实盘引擎初始化错误
        self.technical_analyzer = technical_analyzer if technical_analyzer else TechnicalIndicators()
        self.running = False
        self.task = None
        self.hit_recorder = StrategyHitRecorder(db_config)  # 策略命中记录器
        self.regime_detector = MarketRegimeDetector(db_config)  # 行情类型检测器

        # 定义本地时区（UTC+8）
        self.LOCAL_TZ = timezone(timedelta(hours=8))

        # EMA信号检查间隔控制（60秒检查一次，止损止盈仍然5秒检查）
        self.ema_check_interval = 60  # EMA信号检查间隔（秒）
        self.last_ema_check_time = {}  # 记录每个策略+币种的上次EMA检查时间

        # 初始化数据库服务，用于保存交易记录
        try:
            db_service_config = {
                'type': 'mysql',
                'mysql': db_config
            }
            self.db_service = DatabaseService(db_service_config)
        except Exception as e:
            # logger.warning(f"初始化数据库服务失败，交易记录将不会保存: {e}")
            self.db_service = None

        # 尝试初始化实盘引擎
        self._init_live_engine()

    def _init_live_engine(self):
        """初始化实盘交易引擎（延迟加载）"""
        try:
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            self.live_engine = BinanceFuturesEngine(self.db_config)
            logger.info("实盘交易引擎初始化成功")
        except Exception as e:
            self.live_engine_error = str(e)
            logger.warning(f"实盘交易引擎初始化失败（实盘功能不可用）: {e}")

    def get_engine_for_strategy(self, strategy: Dict):
        """
        根据策略配置获取对应的交易引擎

        Args:
            strategy: 策略配置字典

        Returns:
            交易引擎实例
        """
        market_type = strategy.get('market_type', 'test')

        if market_type == 'live':
            if self.live_engine is None:
                logger.error(f"策略 {strategy.get('name')} 配置为实盘模式，但实盘引擎不可用: {self.live_engine_error}")
                raise RuntimeError(f"实盘引擎不可用: {self.live_engine_error}")
            return self.live_engine
        else:
            return self.futures_engine

    def apply_regime_adaptive_params(self, strategy: Dict, symbol: str,
                                      timeframe: str, kline_data: List[Dict] = None) -> Dict:
        """
        应用行情自适应参数

        根据当前行情类型（趋势/震荡），自动调整策略参数

        Args:
            strategy: 原始策略配置
            symbol: 交易对符号
            timeframe: 时间周期
            kline_data: K线数据（可选）

        Returns:
            调整后的策略配置
        """
        # 检查是否启用行情自适应
        adaptive_regime = strategy.get('adaptiveRegime', False)
        if not adaptive_regime:
            return strategy

        strategy_id = strategy.get('id')
        if not strategy_id:
            return strategy

        try:
            # 检测当前行情类型
            regime_result = self.regime_detector.detect_regime(symbol, timeframe, kline_data)
            regime_type = regime_result.get('regime_type', 'ranging')
            regime_score = regime_result.get('regime_score', 0)

            # 获取该行情类型对应的参数配置
            regime_params = self.regime_detector.get_regime_params(strategy_id, regime_type)

            if regime_params:
                # 检查是否在该行情类型下启用交易
                if not regime_params.get('enabled', True):
                    logger.info(f"📊 {symbol} [{timeframe}] 当前行情: {get_regime_display_name(regime_type)} "
                               f"(得分:{regime_score:.1f}) - 该行情类型已禁用交易")
                    # 返回一个禁用交易的策略配置
                    modified_strategy = strategy.copy()
                    modified_strategy['_regime_disabled'] = True
                    modified_strategy['_regime_type'] = regime_type
                    modified_strategy['_regime_score'] = regime_score
                    return modified_strategy

                # 应用行情参数覆盖
                params = regime_params.get('params', {})
                modified_strategy = strategy.copy()

                # 覆盖相关参数
                for key, value in params.items():
                    # 转换驼峰命名
                    if key == 'sustainedTrend':
                        if isinstance(modified_strategy.get('sustainedTrend'), dict):
                            modified_strategy['sustainedTrend']['enabled'] = value
                        else:
                            modified_strategy['sustainedTrend'] = {'enabled': value}
                    elif key == 'sustainedTrendMinStrength':
                        if isinstance(modified_strategy.get('sustainedTrend'), dict):
                            modified_strategy['sustainedTrend']['minStrength'] = value
                        else:
                            modified_strategy['sustainedTrend'] = {'minStrength': value}
                    elif key == 'sustainedTrendMaxStrength':
                        if isinstance(modified_strategy.get('sustainedTrend'), dict):
                            modified_strategy['sustainedTrend']['maxStrength'] = value
                        else:
                            modified_strategy['sustainedTrend'] = {'maxStrength': value}
                    elif key == 'allowLong':
                        # 调整买入方向
                        buy_directions = modified_strategy.get('buyDirection', [])
                        if value and 'long' not in buy_directions:
                            buy_directions.append('long')
                        elif not value and 'long' in buy_directions:
                            buy_directions.remove('long')
                        modified_strategy['buyDirection'] = buy_directions
                    elif key == 'allowShort':
                        buy_directions = modified_strategy.get('buyDirection', [])
                        if value and 'short' not in buy_directions:
                            buy_directions.append('short')
                        elif not value and 'short' in buy_directions:
                            buy_directions.remove('short')
                        modified_strategy['buyDirection'] = buy_directions
                    elif key == 'stopLossPercent':
                        modified_strategy['stopLoss'] = value
                    elif key == 'takeProfitPercent':
                        modified_strategy['takeProfit'] = value
                    else:
                        modified_strategy[key] = value

                # 记录行情信息
                modified_strategy['_regime_type'] = regime_type
                modified_strategy['_regime_score'] = regime_score

                logger.info(f"📊 {symbol} [{timeframe}] 行情自适应: {get_regime_display_name(regime_type)} "
                           f"(得分:{regime_score:.1f}) - 已应用对应参数")

                return modified_strategy
            else:
                # 没有配置该行情类型的参数，使用默认策略
                logger.debug(f"📊 {symbol} [{timeframe}] 当前行情: {get_regime_display_name(regime_type)} "
                            f"- 未配置对应参数，使用默认配置")
                strategy['_regime_type'] = regime_type
                strategy['_regime_score'] = regime_score
                return strategy

        except Exception as e:
            logger.warning(f"应用行情自适应参数失败: {e}")
            return strategy

    def get_local_time(self) -> datetime:
        """获取本地时间（UTC+8）"""
        return datetime.now(self.LOCAL_TZ).replace(tzinfo=None)

    def should_check_ema_signal(self, strategy_id: int, symbol: str) -> bool:
        """
        检查是否需要检查EMA信号（每60秒检查一次）
        止损止盈每5秒检查，EMA信号每60秒检查

        Args:
            strategy_id: 策略ID
            symbol: 交易对

        Returns:
            bool: 是否需要检查EMA信号
        """
        key = f"{strategy_id}_{symbol}"
        current_time = datetime.now()
        last_check = self.last_ema_check_time.get(key)

        if last_check is None:
            # 首次检查
            self.last_ema_check_time[key] = current_time
            return True

        elapsed = (current_time - last_check).total_seconds()
        if elapsed >= self.ema_check_interval:
            self.last_ema_check_time[key] = current_time
            return True

        return False

    def _save_trade_record(self, symbol: str, action: str, direction: str, 
                            entry_price: float, exit_price: Optional[float], quantity: float, leverage: int, 
                            fee: Optional[float] = None, realized_pnl: Optional[float] = None,
                            strategy_id: Optional[int] = None, strategy_name: Optional[str] = None,
                            account_id: Optional[int] = None, reason: Optional[str] = None,
                            trade_time: Optional[datetime] = None, position_id: Optional[int] = None,
                            order_id: Optional[str] = None):
        """保存交易记录到数据库的辅助方法"""
        if not self.db_service:
            return
        
        try:
            margin = (entry_price * quantity) / leverage if entry_price and quantity else None
            total_value = (exit_price or entry_price) * quantity if quantity else None
            
            trade_record_data = {
                'strategy_id': strategy_id,
                'strategy_name': strategy_name or '未命名策略',
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
                'position_id': position_id,
                'order_id': order_id,
                'signal_id': None,
                'reason': reason,
                'trade_time': trade_time if trade_time else self.get_local_time()
            }
            self.db_service.save_strategy_trade_record(trade_record_data)
        except Exception as e:
            logger.error(f"{symbol} ❌ 保存交易记录失败: {e}")
    
    def _save_capital_record(self, symbol: str, change_type: str, amount_change: float,
                             balance_before: Optional[float] = None, balance_after: Optional[float] = None,
                             frozen_before: Optional[float] = None, frozen_after: Optional[float] = None,
                             available_before: Optional[float] = None, available_after: Optional[float] = None,
                             strategy_id: Optional[int] = None, strategy_name: Optional[str] = None,
                             account_id: Optional[int] = None, action: Optional[str] = None,
                             direction: Optional[str] = None, entry_price: Optional[float] = None,
                             exit_price: Optional[float] = None, quantity: Optional[float] = None,
                             leverage: Optional[int] = None, margin: Optional[float] = None,
                             realized_pnl: Optional[float] = None, fee: Optional[float] = None,
                             reason: Optional[str] = None, description: Optional[str] = None,
                             trade_record_id: Optional[int] = None, position_id: Optional[int] = None,
                             order_id: Optional[str] = None, change_time: Optional[datetime] = None):
        """保存资金管理记录到数据库的辅助方法"""
        if not self.db_service:
            return
        
        try:
            capital_data = {
                'strategy_id': strategy_id,
                'strategy_name': strategy_name or '未命名策略',
                'account_id': account_id,
                'symbol': symbol,
                'trade_record_id': trade_record_id,
                'position_id': position_id,
                'order_id': order_id,
                'change_type': change_type,
                'action': action,
                'direction': direction,
                'amount_change': amount_change,
                'balance_before': balance_before,
                'balance_after': balance_after,
                'frozen_before': frozen_before,
                'frozen_after': frozen_after,
                'available_before': available_before,
                'available_after': available_after,
                'entry_price': entry_price,
                'exit_price': exit_price,
                'quantity': quantity,
                'leverage': leverage,
                'margin': margin,
                'realized_pnl': realized_pnl,
                'fee': fee,
                'reason': reason,
                'description': description,
                'change_time': change_time if change_time else self.get_local_time()
            }
            self.db_service.save_strategy_capital_record(capital_data)
        except Exception as e:
            logger.error(f"{symbol} ❌ 保存资金管理记录失败: {e}")
    
    def get_current_price(self, symbol: str) -> float:
        """
        获取实时价格
        
        Args:
            symbol: 交易对
            
        Returns:
            实时价格，如果获取失败返回0
        """
        try:
            price = self.futures_engine.get_current_price(symbol, use_realtime=True)
            return float(price) if price else 0.0
        except Exception as e:
            logger.error(f"获取 {symbol} 实时价格失败: {e}")
            return 0.0
    
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
            connection = pymysql.connect(
                **self.db_config,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30
            )
            cursor = connection.cursor(pymysql.cursors.DictCursor)

            symbols = strategy.get('symbols', [])
            buy_directions = strategy.get('buyDirection', [])
            leverage = strategy.get('leverage', 5)
            buy_signal = strategy.get('buySignals')
            buy_volume_enabled = strategy.get('buyVolumeEnabled', False)
            buy_volume_long_enabled = strategy.get('buyVolumeLongEnabled', False)
            buy_volume_short_enabled = strategy.get('buyVolumeShortEnabled', False)
            buy_volume = strategy.get('buyVolume')
            buy_volume_long = strategy.get('buyVolumeLong')
            buy_volume_short = strategy.get('buyVolumeShort')
            sell_signal = strategy.get('sellSignals')
            # 平仓成交量已移除，不再限制
            position_size = strategy.get('positionSize', 10)
            max_positions = strategy.get('maxPositions')  # 最大持仓数
            max_long_positions = strategy.get('maxLongPositions')  # 最大做多持仓数
            max_short_positions = strategy.get('maxShortPositions')  # 最大做空持仓数
            long_price_type = strategy.get('longPrice', 'market')
            short_price_type = strategy.get('shortPrice', 'market')
            # 限价单超时自动转市价（分钟），0表示不转换
            limit_order_timeout_minutes = strategy.get('limitOrderTimeoutMinutes', 0)
            stop_loss_pct = strategy.get('stopLoss')
            take_profit_pct = strategy.get('takeProfit')
            ma10_ema10_trend_filter = strategy.get('ma10Ema10TrendFilter', False)
            min_ema_cross_strength = strategy.get('minEMACrossStrength', 0.0)
            min_ma10_cross_strength = strategy.get('minMA10CrossStrength', 0.0)
            # 新的信号强度配置（优先级高于旧格式）
            min_signal_strength = strategy.get('minSignalStrength', {})
            if min_signal_strength:
                min_ema_cross_strength = max(min_ema_cross_strength, min_signal_strength.get('ema9_26', 0.0))
                min_ma10_cross_strength = max(min_ma10_cross_strength, min_signal_strength.get('ma10_ema10', 0.0))
            trend_confirm_bars = strategy.get('trendConfirmBars', 0)
            exit_on_ma_flip = strategy.get('exitOnMAFlip', False)  # MA10/EMA10反转时立即平仓
            exit_on_ma_flip_threshold = strategy.get('exitOnMAFlipThreshold', 0.1)  # MA10/EMA10反转阈值（%），避免小幅波动触发
            exit_on_ma_flip_confirm_bars = strategy.get('exitOnMAFlipConfirmBars', 1)  # MA10/EMA10反转确认K线数，连续几根K线反转才触发平仓
            exit_on_ema_weak = strategy.get('exitOnEMAWeak', False)  # EMA差值<0.05%时平仓
            exit_on_ema_weak_threshold = strategy.get('exitOnEMAWeakThreshold', 0.05)  # EMA弱信号阈值（%），默认0.05%
            early_stop_loss_pct = strategy.get('earlyStopLossPct', None)  # 早期止损百分比，基于EMA差值或价格回撤
            trend_confirm_ema_threshold = strategy.get('trendConfirmEMAThreshold', 0.0)  # 趋势确认EMA差值阈值（%），增强趋势确认
            prevent_duplicate_entry = strategy.get('preventDuplicateEntry', False)  # 防止重复开仓
            close_opposite_on_entry = strategy.get('closeOppositeOnEntry', False)  # 开仓前先平掉相反方向的持仓
            min_holding_time_hours = strategy.get('minHoldingTimeHours', 0)  # 最小持仓时间（小时）
            fee_rate = strategy.get('feeRate', 0.0004)

            # 新指标过滤配置
            rsi_filter = strategy.get('rsiFilter', {})
            rsi_filter_enabled = rsi_filter.get('enabled', False) if isinstance(rsi_filter, dict) else False
            rsi_long_max = rsi_filter.get('longMax', 70) if isinstance(rsi_filter, dict) else 70
            rsi_short_min = rsi_filter.get('shortMin', 30) if isinstance(rsi_filter, dict) else 30

            macd_filter = strategy.get('macdFilter', {})
            macd_filter_enabled = macd_filter.get('enabled', False) if isinstance(macd_filter, dict) else False
            macd_long_require_positive = macd_filter.get('longRequirePositive', True) if isinstance(macd_filter, dict) else True
            macd_short_require_negative = macd_filter.get('shortRequireNegative', True) if isinstance(macd_filter, dict) else True

            kdj_filter = strategy.get('kdjFilter', {})
            kdj_filter_enabled = kdj_filter.get('enabled', False) if isinstance(kdj_filter, dict) else False
            kdj_long_max_k = kdj_filter.get('longMaxK', 80) if isinstance(kdj_filter, dict) else 80
            kdj_short_min_k = kdj_filter.get('shortMinK', 20) if isinstance(kdj_filter, dict) else 20
            kdj_allow_strong_signal = kdj_filter.get('allowStrongSignal', False) if isinstance(kdj_filter, dict) else False
            kdj_strong_signal_threshold = kdj_filter.get('strongSignalThreshold', 1.0) if isinstance(kdj_filter, dict) else 1.0

            bollinger_filter = strategy.get('bollingerFilter', {})
            bollinger_filter_enabled = bollinger_filter.get('enabled', False) if isinstance(bollinger_filter, dict) else False

            # 提前入场配置（预判金叉/死叉）
            early_entry = strategy.get('earlyEntry', {})
            early_entry_enabled = early_entry.get('enabled', False) if isinstance(early_entry, dict) else False
            early_entry_gap_threshold = early_entry.get('gapThreshold', 0.3) if isinstance(early_entry, dict) else 0.3  # EMA差距阈值(%)
            early_entry_require_upward_slope = early_entry.get('requireUpwardSlope', True) if isinstance(early_entry, dict) else True  # 要求EMA9向上斜率
            early_entry_require_price_above_ema = early_entry.get('requirePriceAboveEMA', True) if isinstance(early_entry, dict) else True  # 要求价格在EMA上方
            early_entry_slope_min_pct = early_entry.get('slopeMinPct', 0.1) if isinstance(early_entry, dict) else 0.1  # EMA斜率最小百分比（从0.05提高到0.1）

            # 持续趋势信号配置（允许在趋势已经确立后开仓，而不仅仅是在穿越发生时）
            sustained_trend = strategy.get('sustainedTrend', {})
            sustained_trend_enabled = sustained_trend.get('enabled', False) if isinstance(sustained_trend, dict) else False
            # 持续趋势的最小EMA差距（%），趋势必须足够强
            sustained_trend_min_strength = sustained_trend.get('minStrength', 0.15) if isinstance(sustained_trend, dict) else 0.15
            # 持续趋势的最大EMA差距（%），防止追高杀低
            sustained_trend_max_strength = sustained_trend.get('maxStrength', 1.0) if isinstance(sustained_trend, dict) else 1.0
            # MA10/EMA10必须确认趋势方向
            sustained_trend_require_ma10_confirm = sustained_trend.get('requireMA10Confirm', True) if isinstance(sustained_trend, dict) else True
            # 价格必须符合趋势方向（做多时价格在EMA9上方，做空时价格在EMA9下方）
            sustained_trend_require_price_confirm = sustained_trend.get('requirePriceConfirm', True) if isinstance(sustained_trend, dict) else True
            # 连续多少根K线保持趋势（防止频繁开仓，设置为0表示不检查）
            sustained_trend_min_bars = sustained_trend.get('minBars', 2) if isinstance(sustained_trend, dict) else 2
            # 开仓冷却时间（分钟）：在触发持续趋势信号后，多少分钟内不再触发同方向信号
            sustained_trend_cooldown_minutes = sustained_trend.get('cooldownMinutes', 60) if isinstance(sustained_trend, dict) else 60

            # 价格距离EMA限制配置（防止追高杀低）
            price_distance_limit = strategy.get('priceDistanceLimit', {})
            price_distance_limit_enabled = price_distance_limit.get('enabled', False) if isinstance(price_distance_limit, dict) else False
            # 做多时，价格高于EMA9的最大百分比（超过则不开仓，等回调）
            price_distance_max_above_ema = price_distance_limit.get('maxAboveEMA', 1.0) if isinstance(price_distance_limit, dict) else 1.0
            # 做空时，价格低于EMA9的最大百分比（超过则不开仓，等反弹）
            price_distance_max_below_ema = price_distance_limit.get('maxBelowEMA', 1.0) if isinstance(price_distance_limit, dict) else 1.0

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

            # 实时运行：检查过去24小时内的信号，但只执行当前时间点的交易
            now_local = datetime.now(self.LOCAL_TZ).replace(tzinfo=None)
            end_time_local = now_local
            # 检查过去24小时内的K线，以便捕捉可能遗漏的信号
            start_time_local = now_local - timedelta(hours=24)

            # 转换为UTC时间用于数据库查询
            end_time_utc = end_time_local.replace(tzinfo=self.LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
            start_time_utc = start_time_local.replace(tzinfo=self.LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
            end_time = end_time_utc
            start_time = start_time_utc  # 检查过去24小时内的K线

            # 获取足够的历史数据用于计算技术指标（需要30天的历史数据）
            extended_start_time = end_time - timedelta(days=30)

            # results 已在函数开始处初始化，这里重置为空列表
            results = []

            for symbol in symbols:
                # 获取买入时间周期的K线数据（优先使用合约数据）
                cursor.execute(
                    """SELECT timestamp, open_price, high_price, low_price, close_price, volume
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
                    AND timestamp >= %s AND timestamp <= %s
                    ORDER BY timestamp ASC""",
                    (symbol, buy_timeframe, extended_start_time, end_time)
                )
                buy_klines = cursor.fetchall()

                # 如果没有合约数据，回退到现货数据
                if not buy_klines or len(buy_klines) < 10:
                    cursor.execute(
                        """SELECT timestamp, open_price, high_price, low_price, close_price, volume
                        FROM kline_data
                        WHERE symbol = %s AND timeframe = %s
                        AND timestamp >= %s AND timestamp <= %s
                        ORDER BY timestamp ASC""",
                        (symbol, buy_timeframe, extended_start_time, end_time)
                    )
                    buy_klines = cursor.fetchall()
                    if buy_klines and len(buy_klines) >= 10:
                        logger.warning(f"合约K线数据不足，使用现货数据: {symbol} {buy_timeframe}")

                # 获取卖出时间周期的K线数据（优先使用合约数据）
                cursor.execute(
                    """SELECT timestamp, open_price, high_price, low_price, close_price, volume
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
                    AND timestamp >= %s AND timestamp <= %s
                    ORDER BY timestamp ASC""",
                    (symbol, sell_timeframe, extended_start_time, end_time)
                )
                sell_klines = cursor.fetchall()

                # 如果没有合约数据，回退到现货数据
                if not sell_klines or len(sell_klines) < 10:
                    cursor.execute(
                        """SELECT timestamp, open_price, high_price, low_price, close_price, volume
                        FROM kline_data
                        WHERE symbol = %s AND timeframe = %s
                        AND timestamp >= %s AND timestamp <= %s
                        ORDER BY timestamp ASC""",
                        (symbol, sell_timeframe, extended_start_time, end_time)
                    )
                    sell_klines = cursor.fetchall()
                    if sell_klines and len(sell_klines) >= 10:
                        logger.warning(f"合约K线数据不足，使用现货数据: {symbol} {sell_timeframe}")

                # 根据时间周期确定最小K线数量要求
                # 注意：EMA26 需要至少26根K线，为保证稳定性建议至少50根
                # 15分钟K线：24小时=96根，所以设为50
                min_klines_map = {
                    '5m': 50,
                    '15m': 50,
                    '1h': 30,
                    '4h': 30,
                    '1d': 30
                }
                min_buy_klines = min_klines_map.get(buy_timeframe, 50)
                min_sell_klines = min_klines_map.get(sell_timeframe, 50)

                if not buy_klines or len(buy_klines) < min_buy_klines:
                    error_msg = f'买入时间周期({buy_timeframe})K线数据不足（仅{len(buy_klines) if buy_klines else 0}条，至少需要{min_buy_klines}条）'
                    logger.warning(f"{symbol} {error_msg}")
                    results.append({
                        'symbol': symbol,
                        'error': error_msg,
                        'klines_count': len(buy_klines) if buy_klines else 0
                    })
                    continue

                if not sell_klines or len(sell_klines) < min_sell_klines:
                    error_msg = f'卖出时间周期({sell_timeframe})K线数据不足（仅{len(sell_klines) if sell_klines else 0}条，至少需要{min_sell_klines}条）'
                    logger.warning(f"{symbol} {error_msg}")
                    results.append({
                        'symbol': symbol,
                        'error': error_msg,
                        'klines_count': len(sell_klines) if sell_klines else 0
                    })
                    continue

                # 实时运行：检查过去24小时内的K线，以便捕捉可能遗漏的信号
                # 筛选出过去24小时内的K线（用于信号检测）
                buy_test_klines = []
                sell_test_klines = []
                
                for kline in buy_klines:
                    kline_time = self.parse_time(kline['timestamp'])
                    kline_time_utc = kline_time.replace(tzinfo=timezone.utc) if kline_time.tzinfo is None else kline_time
                    kline_time_utc = kline_time_utc.replace(tzinfo=None) if kline_time_utc.tzinfo else kline_time_utc
                    if start_time <= kline_time_utc <= end_time:
                        buy_test_klines.append(kline)
                
                for kline in sell_klines:
                    kline_time = self.parse_time(kline['timestamp'])
                    kline_time_utc = kline_time.replace(tzinfo=timezone.utc) if kline_time.tzinfo is None else kline_time
                    kline_time_utc = kline_time_utc.replace(tzinfo=None) if kline_time_utc.tzinfo else kline_time_utc
                    if start_time <= kline_time_utc <= end_time:
                        sell_test_klines.append(kline)
                
                # 如果没有找到测试K线，至少使用最新的K线
                if not buy_test_klines and buy_klines:
                    buy_test_klines = [buy_klines[-1]]
                if not sell_test_klines and sell_klines:
                    sell_test_klines = [sell_klines[-1]]
                
                if not buy_test_klines:
                    results.append({
                        'symbol': symbol,
                        'error': f'无法获取买入时间周期({buy_timeframe})测试K线数据（过去24小时）',
                        'klines_count': 0
                    })
                    continue

                if not sell_test_klines:
                    results.append({
                        'symbol': symbol,
                        'error': f'无法获取卖出时间周期({sell_timeframe})测试K线数据（过去24小时）',
                        'klines_count': 0
                    })
                    continue

                # 应用行情自适应参数（如果启用）
                adaptive_strategy = self.apply_regime_adaptive_params(
                    strategy, symbol, buy_timeframe, buy_klines
                )

                # 检查是否因行情类型被禁用交易
                if adaptive_strategy.get('_regime_disabled'):
                    regime_type = adaptive_strategy.get('_regime_type', 'unknown')
                    regime_score = adaptive_strategy.get('_regime_score', 0)
                    results.append({
                        'symbol': symbol,
                        'status': 'skipped',
                        'reason': f'行情类型({regime_type})已禁用交易',
                        'regime_type': regime_type,
                        'regime_score': regime_score
                    })
                    continue

                # 如果行情自适应调整了参数，更新相关变量
                if adaptive_strategy.get('_regime_type'):
                    # 更新受行情影响的参数
                    buy_directions = adaptive_strategy.get('buyDirection', buy_directions)
                    stop_loss_pct = adaptive_strategy.get('stopLoss', stop_loss_pct)
                    take_profit_pct = adaptive_strategy.get('takeProfit', take_profit_pct)

                    # 更新持续趋势参数
                    sustained_trend = adaptive_strategy.get('sustainedTrend', {})
                    if isinstance(sustained_trend, dict):
                        sustained_trend_enabled = sustained_trend.get('enabled', sustained_trend_enabled)
                        sustained_trend_min_strength = sustained_trend.get('minStrength', sustained_trend_min_strength)
                        sustained_trend_max_strength = sustained_trend.get('maxStrength', sustained_trend_max_strength)

                # 调用内部方法执行实时逻辑
                result = await self._execute_symbol_strategy(
                    symbol=symbol,
                    buy_klines=buy_klines,
                    sell_klines=sell_klines,
                    buy_test_klines=buy_test_klines,
                    sell_test_klines=sell_test_klines,
                    buy_timeframe=buy_timeframe,
                    sell_timeframe=sell_timeframe,
                    start_time=end_time,
                    start_time_local=end_time_local,
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
                    strategy_id=strategy.get('id'),
                    strategy_name=strategy.get('name', '未命名策略'),
                    account_id=account_id,
                    exit_on_ma_flip_threshold=exit_on_ma_flip_threshold,
                    exit_on_ma_flip_confirm_bars=exit_on_ma_flip_confirm_bars,
                    limit_order_timeout_minutes=limit_order_timeout_minutes,
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
                    bollinger_filter_enabled=bollinger_filter_enabled,
                    early_entry_enabled=early_entry_enabled,
                    early_entry_gap_threshold=early_entry_gap_threshold,
                    early_entry_require_upward_slope=early_entry_require_upward_slope,
                    early_entry_require_price_above_ema=early_entry_require_price_above_ema,
                    early_entry_slope_min_pct=early_entry_slope_min_pct,
                    sustained_trend_enabled=sustained_trend_enabled,
                    sustained_trend_min_strength=sustained_trend_min_strength,
                    sustained_trend_max_strength=sustained_trend_max_strength,
                    sustained_trend_require_ma10_confirm=sustained_trend_require_ma10_confirm,
                    sustained_trend_require_price_confirm=sustained_trend_require_price_confirm,
                    sustained_trend_min_bars=sustained_trend_min_bars,
                    sustained_trend_cooldown_minutes=sustained_trend_cooldown_minutes,
                    price_distance_limit_enabled=price_distance_limit_enabled,
                    price_distance_max_above_ema=price_distance_max_above_ema,
                    price_distance_max_below_ema=price_distance_max_below_ema,
                    market_type=strategy.get('market_type', 'test')  # 市场类型: test/live
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

            # 计算执行结果汇总
            total_symbols = len(results)
            total_trades = sum(r.get('trades_count', 0) for r in results if not r.get('error'))
            winning_trades = 0
            losing_trades = 0
            total_pnl = 0
            total_pnl_percent = 0

            # 获取账户初始余额和最终余额
            initial_balance = 10000.00
            final_balance = initial_balance
            try:
                cursor.execute(
                    "SELECT total_equity, current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
                    (account_id,)
                )
                account = cursor.fetchone()
                if account:
                    # 优先使用 total_equity，如果没有则使用 current_balance + frozen_balance
                    if account.get('total_equity') is not None:
                        initial_balance = float(account['total_equity'])
                    elif account.get('current_balance') is not None:
                        frozen = float(account.get('frozen_balance', 0) or 0)
                        initial_balance = float(account['current_balance']) + frozen
                    final_balance = initial_balance
            except Exception as e:
                # logger.warning(f"获取账户余额失败: {e}，使用默认值10000")
                pass

            # 计算盈亏统计（从交易记录中获取）
            for r in results:
                if not r.get('error'):
                    trades = r.get('trades', [])
                    # 平仓交易（SELL 或 CLOSE）才有盈亏
                    sell_trades = [t for t in trades if (t.get('action') == 'SELL' or t.get('action') == 'CLOSE') and t.get('realized_pnl') is not None]
                    winning_trades += len([t for t in sell_trades if t.get('realized_pnl', 0) > 0])
                    losing_trades += len([t for t in sell_trades if t.get('realized_pnl', 0) < 0])
                    # 累计盈亏
                    for t in sell_trades:
                        if t.get('realized_pnl') is not None:
                            total_pnl += float(t.get('realized_pnl', 0))

            # 更新最终余额
            final_balance = initial_balance + total_pnl

            win_rate = (winning_trades / (winning_trades + losing_trades) * 100) if (winning_trades + losing_trades) > 0 else 0
            total_pnl_percent = ((final_balance - initial_balance) / initial_balance * 100) if initial_balance > 0 else 0

            # 保存执行结果到数据库
            execution_result_id = None
            try:
                import json
                execution_duration_hours = (end_time_local - start_time_local).total_seconds() / 3600

                # 插入主表
                cursor.execute("""
                    INSERT INTO strategy_execution_results 
                    (strategy_id, strategy_name, account_id, strategy_config, execution_start_time, execution_end_time, 
                     execution_duration_hours, total_symbols, total_trades, winning_trades, losing_trades, 
                     win_rate, initial_balance, final_balance, total_pnl, total_pnl_percent, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    strategy.get('id'),
                    strategy.get('name', '未命名策略'),
                    account_id,
                    json.dumps(strategy, ensure_ascii=False),
                    start_time_local,
                    end_time_local,
                    execution_duration_hours,
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
                execution_result_id = cursor.lastrowid
                connection.commit()

                # 插入详情表（批量插入，每10条提交一次，减少锁定时间）
                detail_insert_count = 0
                # 先检查debug_info字段是否存在（只检查一次，避免重复查询）
                cursor.execute("""
                    SELECT COLUMN_NAME 
                    FROM INFORMATION_SCHEMA.COLUMNS 
                    WHERE TABLE_SCHEMA = DATABASE() 
                    AND TABLE_NAME = 'strategy_execution_result_details' 
                    AND COLUMN_NAME = 'debug_info'
                """)
                has_debug_info_column = cursor.fetchone() is not None
                
                for r in results:
                    symbol = r.get('symbol')
                    if not symbol:
                        continue

                    trades = r.get('trades', [])
                    buy_count = len([t for t in trades if t.get('action') == 'BUY'])
                    sell_count = len([t for t in trades if t.get('action') == 'SELL' or t.get('action') == 'CLOSE'])
                    # 平仓交易（SELL 或 CLOSE）才有盈亏
                    sell_trades = [t for t in trades if (t.get('action') == 'SELL' or t.get('action') == 'CLOSE') and t.get('realized_pnl') is not None]
                    symbol_winning = len([t for t in sell_trades if t.get('realized_pnl', 0) > 0])
                    symbol_losing = len([t for t in sell_trades if t.get('realized_pnl', 0) < 0])
                    symbol_win_rate = (symbol_winning / (symbol_winning + symbol_losing) * 100) if (symbol_winning + symbol_losing) > 0 else 0

                    # 计算该交易对的盈亏
                    symbol_total_pnl = sum(float(t.get('realized_pnl', 0)) for t in sell_trades if t.get('realized_pnl') is not None)
                    symbol_final_balance = initial_balance + symbol_total_pnl
                    symbol_pnl_percent = ((symbol_final_balance - initial_balance) / initial_balance * 100) if initial_balance > 0 else 0

                    # 提取调试信息
                    debug_info = r.get('debug_info', [])
                    debug_info_json = json.dumps(debug_info, ensure_ascii=False) if debug_info else None
                    debug_info_count = len(debug_info) if debug_info else 0

                    try:
                        if has_debug_info_column:
                            # 如果字段存在，使用包含debug_info的SQL
                            cursor.execute("""
                            INSERT INTO strategy_execution_result_details
                            (execution_result_id, symbol, trades_count, buy_count, sell_count, 
                             winning_trades, losing_trades, win_rate, initial_balance, final_balance,
                             total_pnl, total_pnl_percent, golden_cross_count, death_cross_count,
                             klines_count, indicators_count, error_message, execution_result_data, 
                             debug_info, debug_info_count)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            execution_result_id,
                            symbol,
                            r.get('trades_count', 0),
                            buy_count,
                            sell_count,
                            symbol_winning,
                            symbol_losing,
                            symbol_win_rate,
                            initial_balance,
                            symbol_final_balance,
                            symbol_total_pnl,
                            symbol_pnl_percent,
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
                            # 如果字段不存在，使用不包含debug_info的SQL（调试信息会保存在execution_result_data中）
                            cursor.execute("""
                            INSERT INTO strategy_execution_result_details
                            (execution_result_id, symbol, trades_count, buy_count, sell_count, 
                             winning_trades, losing_trades, win_rate, initial_balance, final_balance,
                             total_pnl, total_pnl_percent, golden_cross_count, death_cross_count,
                             klines_count, indicators_count, error_message, execution_result_data)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """, (
                            execution_result_id,
                            symbol,
                            r.get('trades_count', 0),
                            buy_count,
                            sell_count,
                            symbol_winning,
                            symbol_losing,
                            symbol_win_rate,
                            initial_balance,
                            symbol_final_balance,
                            symbol_total_pnl,
                            symbol_pnl_percent,
                            r.get('golden_cross_count', 0),
                            r.get('death_cross_count', 0),
                            r.get('klines_count', 0),
                            r.get('indicators_count', 0),
                            r.get('error'),
                            json.dumps(r, ensure_ascii=False) if not r.get('error') else None
                            ))
                        
                        detail_insert_count += 1
                        
                        # 每插入10条时提交，减少锁定时间
                        if detail_insert_count % 10 == 0:
                            try:
                                connection.commit()
                            except Exception as commit_error:
                                logger.warning(f"批量提交详情数据失败: {commit_error}，继续尝试...")
                                connection.rollback()
                    except pymysql.err.OperationalError as insert_error:
                        error_code = insert_error.args[0] if insert_error.args else 0
                        if error_code == 1205:  # Lock wait timeout
                            logger.warning(f"插入详情数据失败 (symbol={symbol}): 锁等待超时，跳过该条记录")
                        else:
                            logger.warning(f"插入详情数据失败 (symbol={symbol}): {insert_error}，跳过该条记录")
                        connection.rollback()
                        continue
                    except Exception as insert_error:
                        logger.warning(f"插入详情数据失败 (symbol={symbol}): {insert_error}，跳过该条记录")
                        connection.rollback()
                        continue
                
                # 最终提交（确保所有数据都已提交）
                if detail_insert_count > 0:
                    try:
                        connection.commit()
                    except Exception as commit_error:
                        logger.warning(f"最终提交详情数据时出错（可能已提交）: {commit_error}")
                        
            except pymysql.err.OperationalError as e:
                # 处理锁等待超时等操作错误
                error_code = e.args[0] if e.args else 0
                if error_code == 1205:  # Lock wait timeout exceeded
                    logger.error(f"保存执行结果到数据库失败: 锁等待超时，可能是清理脚本正在运行。错误: {e}")
                    connection.rollback()
                    # 尝试重试一次
                    try:
                        import time
                        time.sleep(1)  # 等待1秒后重试
                        logger.info("重试保存执行结果...")
                        # 这里可以添加重试逻辑，但为了简化，先记录错误
                    except Exception as retry_error:
                        logger.error(f"重试保存执行结果失败: {retry_error}")
                else:
                    logger.error(f"保存执行结果到数据库失败: {e}", exc_info=True)
                    connection.rollback()
            except pymysql.err.OperationalError as e:
                # 处理锁等待超时等操作错误
                error_code = e.args[0] if e.args else 0
                if error_code == 1205:  # Lock wait timeout exceeded
                    logger.error(f"保存执行结果到数据库失败: 锁等待超时，可能是清理脚本正在运行。错误: {e}")
                    connection.rollback()
                else:
                    logger.error(f"保存执行结果到数据库失败: {e}", exc_info=True)
                    connection.rollback()
            except Exception as e:
                logger.error(f"保存执行结果到数据库失败: {e}", exc_info=True)
                connection.rollback()
                # 即使保存失败，也返回执行结果

            # 返回执行结果
            return {
                'success': True,
                'data': results,
                'execution_result_id': execution_result_id
            }

        except Exception as e:
            logger.error(f"策略执行失败: {e}", exc_info=True)
            # 返回错误信息
            return {
                'success': False,
                'error': str(e),
                'data': []
            }
        finally:
            cursor.close()
            connection.close()
    
    async def _execute_symbol_strategy(self, **kwargs) -> Dict:
        """
        执行单个交易对的策略（内部方法）
        这个方法包含了完整的策略执行逻辑
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
        # 平仓成交量已移除，不再限制
        position_size = kwargs.get('position_size', 10)
        max_positions = kwargs.get('max_positions')  # 最大持仓数
        long_price_type = kwargs.get('long_price_type', 'market')
        short_price_type = kwargs.get('short_price_type', 'market')
        stop_loss_pct = kwargs.get('stop_loss_pct')
        take_profit_pct = kwargs.get('take_profit_pct')
        ma10_ema10_trend_filter = kwargs.get('ma10_ema10_trend_filter', False)
        min_ema_cross_strength = kwargs.get('min_ema_cross_strength', 0.0)
        min_ma10_cross_strength = kwargs.get('min_ma10_cross_strength', 0.0)
        trend_confirm_bars = kwargs.get('trend_confirm_bars', 0)  # 趋势至少持续K线数（默认0表示不启用）
        trend_confirm_ema_threshold = kwargs.get('trend_confirm_ema_threshold', 0.0)  # 趋势确认EMA差值阈值（%），增强趋势确认
        exit_on_ma_flip = kwargs.get('exit_on_ma_flip', False)  # MA10/EMA10反转时立即平仓
        exit_on_ma_flip_threshold = kwargs.get('exit_on_ma_flip_threshold', 0.1)  # MA10/EMA10反转阈值（%），避免小幅波动触发
        exit_on_ma_flip_confirm_bars = kwargs.get('exit_on_ma_flip_confirm_bars', 1)  # MA10/EMA10反转确认K线数
        limit_order_timeout_minutes = kwargs.get('limit_order_timeout_minutes', 0)  # 限价单超时自动转市价（分钟）
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
        # 提前入场配置（预判金叉/死叉）
        early_entry_enabled = kwargs.get('early_entry_enabled', False)
        early_entry_gap_threshold = kwargs.get('early_entry_gap_threshold', 0.3)  # EMA差距阈值(%)
        early_entry_require_upward_slope = kwargs.get('early_entry_require_upward_slope', True)  # 要求EMA9向上斜率
        early_entry_require_price_above_ema = kwargs.get('early_entry_require_price_above_ema', True)  # 要求价格在EMA上方
        early_entry_slope_min_pct = kwargs.get('early_entry_slope_min_pct', 0.1)  # EMA斜率最小百分比（从0.05提高到0.1）
        # 持续趋势信号配置
        sustained_trend_enabled = kwargs.get('sustained_trend_enabled', False)
        sustained_trend_min_strength = kwargs.get('sustained_trend_min_strength', 0.15)  # 持续趋势的最小EMA差距(%)
        sustained_trend_max_strength = kwargs.get('sustained_trend_max_strength', 1.0)  # 持续趋势的最大EMA差距(%)
        sustained_trend_require_ma10_confirm = kwargs.get('sustained_trend_require_ma10_confirm', True)  # MA10/EMA10必须确认趋势方向
        sustained_trend_require_price_confirm = kwargs.get('sustained_trend_require_price_confirm', True)  # 价格必须符合趋势方向
        sustained_trend_min_bars = kwargs.get('sustained_trend_min_bars', 2)  # 连续多少根K线保持趋势
        sustained_trend_cooldown_minutes = kwargs.get('sustained_trend_cooldown_minutes', 60)  # 开仓冷却时间（分钟）
        # 价格距离EMA限制配置（防止追高杀低）
        price_distance_limit_enabled = kwargs.get('price_distance_limit_enabled', False)
        price_distance_max_above_ema = kwargs.get('price_distance_max_above_ema', 1.0)  # 做多时价格高于EMA9的最大%
        price_distance_max_below_ema = kwargs.get('price_distance_max_below_ema', 1.0)  # 做空时价格低于EMA9的最大%
        strategy_id = kwargs.get('strategy_id')
        strategy_name = kwargs.get('strategy_name', '测试策略')
        account_id = kwargs.get('account_id', 0)
        market_type = kwargs.get('market_type', 'test')  # 市场类型: test/live

        # 根据市场类型选择交易引擎
        if market_type == 'live':
            if self.live_engine is None:
                logger.error(f"策略 {strategy_name} 配置为实盘模式，但实盘引擎不可用")
                return {'symbol': symbol, 'error': '实盘引擎不可用', 'market_type': market_type}
            trading_engine = self.live_engine
            logger.debug(f"[实盘] 策略 {strategy_name} 使用实盘引擎")
        else:
            trading_engine = self.futures_engine
            # logger.debug(f"[模拟] 策略 {strategy_name} 使用模拟引擎")

        # 从数据库获取当前持仓
        open_positions = trading_engine.get_open_positions(account_id)
        # 筛选出当前交易对的持仓
        positions = []
        for pos in open_positions:
            if pos.get('symbol') == symbol:
                # 转换为内部格式
                position = {
                    'position_id': pos.get('position_id') or pos.get('id'),
                    'direction': 'long' if pos.get('position_side') == 'LONG' else 'short',
                    'entry_price': float(pos.get('entry_price', 0)),
                    'quantity': float(pos.get('quantity', 0)),
                    'entry_time': self.parse_time(pos.get('open_time')),
                    'entry_time_local': self.utc_to_local(self.parse_time(pos.get('open_time'))) if pos.get('open_time') else None,
                    'leverage': pos.get('leverage', leverage),
                    'open_fee': float(pos.get('open_fee', 0)),
                    'stop_loss_price': float(pos.get('stop_loss_price', 0)) if pos.get('stop_loss_price') else None,
                    'take_profit_price': float(pos.get('take_profit_price', 0)) if pos.get('take_profit_price') else None
                }
                positions.append(position)
        
        trades = []  # 用于记录交易（仅用于返回结果）
        debug_info = []  # 调试信息
        
        # 添加调试信息：记录K线时间范围
        if buy_test_klines:
            first_buy_time = self.parse_time(buy_test_klines[0]['timestamp'])
            last_buy_time = self.parse_time(buy_test_klines[-1]['timestamp'])
            debug_info.append(f"📊 买入时间周期({buy_timeframe})K线范围: {first_buy_time.strftime('%Y-%m-%d %H:%M')} 至 {last_buy_time.strftime('%Y-%m-%d %H:%M')}（本地时间 UTC+8），共{len(buy_test_klines)}条")
            debug_info.append(f"📊 测试时间范围: {start_time_local.strftime('%Y-%m-%d %H:%M')} 至 {end_time_local.strftime('%Y-%m-%d %H:%M')}（本地时间 UTC+8）")
        
        # 将K线数据转换为DataFrame格式（用于计算技术指标）
        
        # 为买入时间周期的每个K线计算技术指标
        def calculate_indicators(klines, test_klines, timeframe_name):
            indicator_pairs = []
            for test_kline in test_klines:
                test_kline_time = self.parse_time(test_kline['timestamp'])

                # 获取到当前K线为止的所有历史K线（用于计算技术指标）
                historical_klines = [k for k in klines if self.parse_time(k['timestamp']) <= test_kline_time]
                
                # 根据时间周期确定最小历史K线数量
                # EMA26 需要至少26根K线，为保证稳定性建议至少30根
                min_historical_map = {
                    '5m': 30,
                    '15m': 30,
                    '1h': 30,
                    '4h': 30,
                    '1d': 30
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
            error_msg = f'买入时间周期({buy_timeframe})技术指标计算失败（K线:{len(buy_test_klines)}条, 成功计算:{len(buy_indicator_pairs)}条）'
            logger.warning(f"{symbol} {error_msg}")
            return {
                'symbol': symbol,
                'error': error_msg,
                'klines_count': len(buy_test_klines),
                'indicators_count': len(buy_indicator_pairs),
                'matched_pairs_count': len(buy_indicator_pairs)
            }
        
        if len(sell_indicator_pairs) < 2:
            error_msg = f'卖出时间周期({sell_timeframe})技术指标计算失败（K线:{len(sell_test_klines)}条, 成功计算:{len(sell_indicator_pairs)}条）'
            logger.warning(f"{symbol} {error_msg}")
            return {
                'symbol': symbol,
                'error': error_msg,
                'klines_count': len(sell_test_klines),
                'indicators_count': len(sell_indicator_pairs),
                'matched_pairs_count': len(sell_indicator_pairs)
            }
        
        # 实时运行：获取实时价格
        realtime_price = self.get_current_price(symbol)
        if realtime_price <= 0:
            return {
                'symbol': symbol,
                'error': f'无法获取 {symbol} 的实时价格',
                'klines_count': len(buy_test_klines) + len(sell_test_klines),
                'indicators_count': len(buy_indicator_pairs) + len(sell_indicator_pairs)
            }
        
        # 实时运行：只处理最新K线
        latest_buy_pair = buy_indicator_pairs[-1]
        latest_sell_pair = sell_indicator_pairs[-1]
        
        current_time = datetime.now(timezone.utc).replace(tzinfo=None)
        current_time_local = self.utc_to_local(current_time)
        closed_at_current_time = False
        
        # 实时运行：先检查卖出信号（平仓），再检查买入信号（开仓）
        # 1. 检查卖出信号（平仓）
        sell_pair = latest_sell_pair
        sell_kline = sell_pair['kline']
        sell_indicator = sell_pair['indicator']

        # 使用实时价格
        close_price = realtime_price
        high_price = realtime_price  # 实时运行时，使用实时价格作为high和low
        low_price = realtime_price
        volume_ratio = float(sell_indicator['volume_ratio']) if sell_indicator.get('volume_ratio') else 1.0

        # EMA信号每60秒检查一次，止损止盈每5秒检查
        should_check_ema = self.should_check_ema_signal(strategy_id, symbol)

        # 实时运行：先检查卖出信号（平仓），再检查买入信号（开仓）
        # 1. 检查卖出信号（平仓）- 使用实时价格
        if len(positions) > 0:
            # 先检查止损止盈（使用实时价格）
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
                    direction_text = "做多" if direction == 'long' else "做空"
                    if direction == 'long' and realtime_price <= stop_loss_price:
                        exit_price = stop_loss_price
                        exit_reason = "止损"
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: 🛑 {direction_text}触发止损，入场={entry_price:.4f}，止损价={stop_loss_price:.4f}，当前价={realtime_price:.4f}")
                    elif direction == 'short' and realtime_price >= stop_loss_price:
                        exit_price = stop_loss_price
                        exit_reason = "止损"
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: 🛑 {direction_text}触发止损，入场={entry_price:.4f}，止损价={stop_loss_price:.4f}，当前价={realtime_price:.4f}")
                
                # 止盈检查（止盈不受最小持仓时间限制，触发即执行）
                if not exit_price and take_profit_price:
                    if direction == 'long' and realtime_price >= take_profit_price:
                        exit_price = take_profit_price
                        exit_reason = "止盈"
                    elif direction == 'short' and realtime_price <= take_profit_price:
                        exit_price = take_profit_price
                        exit_reason = "止盈"
                
                if exit_price and exit_reason:
                    position_id = position.get('position_id')
                    quantity = position['quantity']
                    
                    if position_id:
                        # 使用实时价格平仓
                        exit_price_decimal = Decimal(str(realtime_price))
                        close_result = trading_engine.close_position(
                            position_id=position_id,
                            close_quantity=None,
                            reason=exit_reason,
                            close_price=exit_price_decimal
                        )
                        
                        if close_result.get('success'):
                            actual_exit_price = float(close_result.get('exit_price', close_result.get('close_price', realtime_price)))
                            actual_quantity = float(close_result.get('close_quantity', quantity))
                            actual_pnl = float(close_result.get('realized_pnl', 0))
                            actual_fee = float(close_result.get('fee', 0))
                            order_id = close_result.get('order_id')
                            margin_used = float(close_result.get('margin', (entry_price * actual_quantity) / leverage))
                            
                            # 从平仓结果中获取余额信息（futures_engine 已返回）
                            balance_before = close_result.get('balance_before')
                            balance_after = close_result.get('balance_after')
                            frozen_before = close_result.get('frozen_before')
                            frozen_after = close_result.get('frozen_after')
                            available_before = close_result.get('available_before')
                            available_after = close_result.get('available_after')
                            
                            # 保存交易记录到数据库
                            self._save_trade_record(
                                symbol=symbol,
                                action='SELL',
                                direction=direction,
                                entry_price=entry_price,
                                exit_price=actual_exit_price,
                                quantity=actual_quantity,
                                leverage=leverage,
                                fee=actual_fee,
                                realized_pnl=actual_pnl,
                                strategy_id=strategy_id,
                                strategy_name=strategy_name,
                                account_id=account_id,
                                reason=exit_reason,
                                trade_time=current_time_local,
                                position_id=position_id,
                                order_id=order_id
                            )
                            
                            # 记录解冻保证金
                            if margin_used > 0:
                                self._save_capital_record(
                                    symbol=symbol,
                                    change_type='UNFROZEN',
                                    amount_change=margin_used,  # 正数表示增加可用余额
                                    balance_before=balance_before,
                                    balance_after=balance_after,
                                    frozen_before=frozen_before,
                                    frozen_after=frozen_after,
                                    available_before=available_before,
                                    available_after=available_after,
                                    strategy_id=strategy_id,
                                    strategy_name=strategy_name,
                                    account_id=account_id,
                                    action='SELL',
                                    direction=direction,
                                    entry_price=entry_price,
                                    exit_price=actual_exit_price,
                                    quantity=actual_quantity,
                                    leverage=leverage,
                                    margin=margin_used,
                                    reason=f'平仓解冻保证金 ({exit_reason})',
                                    position_id=position_id,
                                    order_id=order_id,
                                    change_time=current_time_local
                                )
                            
                            # 记录已实现盈亏
                            if actual_pnl != 0:
                                self._save_capital_record(
                                    symbol=symbol,
                                    change_type='REALIZED_PNL',
                                    amount_change=actual_pnl,  # 正数表示盈利，负数表示亏损
                                    balance_before=balance_after,  # 使用解冻后的余额
                                    balance_after=balance_after + actual_pnl if balance_after else None,
                                    frozen_before=frozen_after,
                                    frozen_after=frozen_after,
                                    available_before=available_after,
                                    available_after=available_after + actual_pnl if available_after else None,
                                    strategy_id=strategy_id,
                                    strategy_name=strategy_name,
                                    account_id=account_id,
                                    action='SELL',
                                    direction=direction,
                                    entry_price=entry_price,
                                    exit_price=actual_exit_price,
                                    quantity=actual_quantity,
                                    leverage=leverage,
                                    realized_pnl=actual_pnl,
                                    reason=f'平仓已实现盈亏 ({exit_reason})',
                                    position_id=position_id,
                                    order_id=order_id,
                                    change_time=current_time_local
                                )
                            
                            # 记录平仓手续费
                            if actual_fee > 0:
                                self._save_capital_record(
                                    symbol=symbol,
                                    change_type='FEE',
                                    amount_change=-actual_fee,  # 负数表示减少余额
                                    balance_before=balance_after + actual_pnl if balance_after and actual_pnl else balance_after,
                                    balance_after=balance_after + actual_pnl - actual_fee if balance_after and actual_pnl else (balance_after - actual_fee if balance_after else None),
                                    frozen_before=frozen_after,
                                    frozen_after=frozen_after,
                                    available_before=available_after + actual_pnl if available_after and actual_pnl else available_after,
                                    available_after=available_after + actual_pnl - actual_fee if available_after and actual_pnl else (available_after - actual_fee if available_after else None),
                                    strategy_id=strategy_id,
                                    strategy_name=strategy_name,
                                    account_id=account_id,
                                    action='SELL',
                                    direction=direction,
                                    entry_price=entry_price,
                                    exit_price=actual_exit_price,
                                    quantity=actual_quantity,
                                    leverage=leverage,
                                    fee=actual_fee,
                                    reason=f'平仓手续费 ({exit_reason})',
                                    position_id=position_id,
                                    order_id=order_id,
                                    change_time=current_time_local
                                )
                            
                            direction_text = "做多" if direction == 'long' else "做空"
                            qty_precision = self.get_quantity_precision(symbol)
                            pnl_pct = (actual_pnl / margin_used) * 100 if margin_used > 0 else 0
                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 平仓{direction_text} | 入场价={entry_price:.4f}, 平仓价={actual_exit_price:.4f}, 数量={actual_quantity:.{qty_precision}f}, 实际盈亏={actual_pnl:+.2f} ({pnl_pct:+.2f}%), 原因: {exit_reason}")
                            
                            positions.remove(position)
                            closed_at_current_time = True
                        else:
                            error_msg = close_result.get('message', '未知错误')
                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ❌ 平仓失败: {error_msg}")
                            logger.error(f"{symbol} 平仓失败 (持仓ID: {position_id}): {error_msg}")
                    else:
                        positions.remove(position)
                        closed_at_current_time = True
            
            # 检查趋势反转退出机制（优先级高于卖出信号）
            if not closed_at_current_time and len(positions) > 0:
                should_exit = False
                exit_reason = None
                
                current_sell_index = len(sell_indicator_pairs) - 1
                if current_sell_index > 0:
                    prev_pair = sell_indicator_pairs[current_sell_index - 1]
                    prev_indicator = prev_pair['indicator']
                                    
                    # 检查 MA10/EMA10 反转退出（只有当反转方向与持仓方向相反时才触发）
                    if exit_on_ma_flip and positions:
                        if sell_indicator.get('ma10') and sell_indicator.get('ema10') and \
                           prev_indicator.get('ma10') and prev_indicator.get('ema10'):
                            ma10 = float(sell_indicator['ma10'])
                            ema10 = float(sell_indicator['ema10'])
                            prev_ma10 = float(prev_indicator['ma10'])
                            prev_ema10 = float(prev_indicator['ema10'])

                            # 计算MA10/EMA10差值百分比（带符号，正=多头，负=空头）
                            curr_diff = ema10 - ma10
                            curr_diff_pct = (curr_diff / ma10 * 100) if ma10 > 0 else 0

                            # 检查当前MA10/EMA10状态
                            curr_bullish = ema10 > ma10  # 当前是多头状态

                            # 获取当前持仓方向（取第一个持仓的方向）
                            position_direction = positions[0]['direction']

                            # 连续K线确认检查
                            confirm_bars_needed = max(1, exit_on_ma_flip_confirm_bars)
                            confirmed_bars = 0

                            # 检查最近N根K线是否都满足反转条件
                            for bar_offset in range(confirm_bars_needed):
                                check_idx = current_sell_index - bar_offset
                                if check_idx < 0:
                                    break
                                check_pair = sell_indicator_pairs[check_idx]
                                check_indicator = check_pair['indicator']
                                if check_indicator.get('ma10') and check_indicator.get('ema10'):
                                    check_ma10 = float(check_indicator['ma10'])
                                    check_ema10 = float(check_indicator['ema10'])
                                    check_diff = check_ema10 - check_ma10
                                    check_diff_pct = (check_diff / check_ma10 * 100) if check_ma10 > 0 else 0
                                    check_bullish = check_ema10 > check_ma10

                                    # 判断该K线是否满足反转条件
                                    if position_direction == 'long' and not check_bullish and abs(check_diff_pct) >= exit_on_ma_flip_threshold:
                                        confirmed_bars += 1
                                    elif position_direction == 'short' and check_bullish and abs(check_diff_pct) >= exit_on_ma_flip_threshold:
                                        confirmed_bars += 1

                            # 只有当MA状态与持仓方向相反，且差值超过阈值，且连续确认K线数满足要求时才触发退出
                            # 同时需要满足最小持仓时间要求
                            can_exit_ma_flip = True
                            if min_holding_time_hours > 0 and positions:
                                for pos in positions:
                                    entry_time = pos.get('entry_time')
                                    if entry_time:
                                        holding_time = current_time - entry_time
                                        min_holding_time = timedelta(hours=min_holding_time_hours)
                                        if holding_time < min_holding_time:
                                            can_exit_ma_flip = False
                                            break

                            if position_direction == 'long' and not curr_bullish:
                                # 做多但MA转空头，检查空头差值是否超过阈值
                                if abs(curr_diff_pct) >= exit_on_ma_flip_threshold and confirmed_bars >= confirm_bars_needed:
                                    if can_exit_ma_flip:
                                        should_exit = True
                                        exit_reason = f'MA10/EMA10转空头(差值{abs(curr_diff_pct):.2f}%≥{exit_on_ma_flip_threshold}%)'
                                    else:
                                        remaining_hours = (min_holding_time - holding_time).total_seconds() / 3600
                                        debug_info.append(f"   ⏳ MA10/EMA10转空头但持仓时间不足，已持仓{holding_time.total_seconds()/3600:.1f}小时，需要至少{min_holding_time_hours}小时")
                                elif abs(curr_diff_pct) >= exit_on_ma_flip_threshold and confirmed_bars < confirm_bars_needed:
                                    debug_info.append(f"   📊 MA10/EMA10转空头但确认K线数不足({confirmed_bars}/{confirm_bars_needed}根)")
                            elif position_direction == 'short' and curr_bullish:
                                # 做空但MA转多头，检查多头差值是否超过阈值
                                if abs(curr_diff_pct) >= exit_on_ma_flip_threshold and confirmed_bars >= confirm_bars_needed:
                                    if can_exit_ma_flip:
                                        should_exit = True
                                        exit_reason = f'MA10/EMA10转多头(差值{abs(curr_diff_pct):.2f}%≥{exit_on_ma_flip_threshold}%)'
                                    else:
                                        remaining_hours = (min_holding_time - holding_time).total_seconds() / 3600
                                        debug_info.append(f"   ⏳ MA10/EMA10转多头但持仓时间不足，已持仓{holding_time.total_seconds()/3600:.1f}小时，需要至少{min_holding_time_hours}小时")
                                elif abs(curr_diff_pct) >= exit_on_ma_flip_threshold and confirmed_bars < confirm_bars_needed:
                                    debug_info.append(f"   📊 MA10/EMA10转多头但确认K线数不足({confirmed_bars}/{confirm_bars_needed}根)")
                    
                    # 检查 EMA 弱信号退出（需要满足最小持仓时间）
                    if not should_exit and exit_on_ema_weak:
                        if sell_indicator.get('ema_short') and sell_indicator.get('ema_long'):
                            ema_short = float(sell_indicator['ema_short'])
                            ema_long = float(sell_indicator['ema_long'])
                            ema_diff_pct = abs(ema_short - ema_long) / ema_long * 100 if ema_long > 0 else 0

                            if ema_diff_pct < exit_on_ema_weak_threshold:
                                # 检查是否满足最小持仓时间要求
                                can_exit_ema_weak = True
                                if min_holding_time_hours > 0 and positions:
                                    for pos in positions:
                                        entry_time = pos.get('entry_time')
                                        if entry_time:
                                            holding_time = current_time - entry_time
                                            min_holding_time = timedelta(hours=min_holding_time_hours)
                                            if holding_time < min_holding_time:
                                                can_exit_ema_weak = False
                                                remaining_hours = (min_holding_time - holding_time).total_seconds() / 3600
                                                debug_info.append(f"   ⏳ EMA信号过弱但持仓时间不足，已持仓{holding_time.total_seconds()/3600:.1f}小时，需要至少{min_holding_time_hours}小时，还需等待{remaining_hours:.1f}小时")
                                                break

                                if can_exit_ema_weak:
                                    should_exit = True
                                    exit_reason = f'EMA信号过弱(差值<{exit_on_ema_weak_threshold}%)'
                    
                    # 检查早期止损
                    if not should_exit and early_stop_loss_pct is not None and early_stop_loss_pct > 0:
                        for position in positions[:]:
                            entry_price = position['entry_price']
                            direction = position['direction']
                            
                            if sell_indicator.get('ema_short') and sell_indicator.get('ema_long'):
                                ema_short = float(sell_indicator['ema_short'])
                                ema_long = float(sell_indicator['ema_long'])
                                ema_diff_pct = abs(ema_short - ema_long) / ema_long * 100 if ema_long > 0 else 0
                                
                                if ema_diff_pct < early_stop_loss_pct:
                                    should_exit = True
                                    exit_reason = f'早期止损(EMA差值{ema_diff_pct:.2f}% < {early_stop_loss_pct}%)'
                                    break
                            
                            if direction == 'long':
                                price_drop_pct = (entry_price - realtime_price) / entry_price * 100
                                if price_drop_pct >= early_stop_loss_pct:
                                    should_exit = True
                                    exit_reason = f'早期止损(价格回撤{price_drop_pct:.2f}% ≥ {early_stop_loss_pct}%)'
                                    break
                            else:
                                price_rise_pct = (realtime_price - entry_price) / entry_price * 100
                                if price_rise_pct >= early_stop_loss_pct:
                                    should_exit = True
                                    exit_reason = f'早期止损(价格回撤{price_rise_pct:.2f}% ≥ {early_stop_loss_pct}%)'
                                    break
                                    
                # 如果触发趋势反转退出，立即平仓
                if should_exit:
                    for position in positions[:]:
                        position_id = position.get('position_id')
                        entry_price = position['entry_price']
                        quantity = position['quantity']
                        direction = position['direction']
                        
                        if position_id:
                            exit_price_decimal = Decimal(str(realtime_price))
                            close_result = trading_engine.close_position(
                                position_id=position_id,
                                close_quantity=None,
                                reason=exit_reason,
                                close_price=exit_price_decimal
                            )

                            if close_result.get('success'):
                                actual_exit_price = float(close_result.get('exit_price', realtime_price))
                                actual_quantity = float(close_result.get('quantity', quantity))
                                actual_pnl = float(close_result.get('realized_pnl', 0))
                                actual_fee = float(close_result.get('fee', 0))

                                self._save_trade_record(
                                                    symbol=symbol,
                                    action='SELL',
                                    direction=direction,
                                    entry_price=entry_price,
                                    exit_price=actual_exit_price,
                                    quantity=actual_quantity,
                                    leverage=leverage,
                                    fee=actual_fee,
                                    realized_pnl=actual_pnl,
                                    strategy_id=strategy_id,
                                    strategy_name=strategy_name,
                                    account_id=account_id,
                                    reason=exit_reason,
                                    trade_time=current_time_local
                                )
                                
                                direction_text = "做多" if direction == 'long' else "做空"
                                qty_precision = self.get_quantity_precision(symbol)
                                margin_used = (entry_price * actual_quantity) / leverage
                                pnl_pct = (actual_pnl / margin_used) * 100 if margin_used > 0 else 0
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 趋势反转退出{direction_text} | 入场价={entry_price:.4f}, 平仓价={actual_exit_price:.4f}, 数量={actual_quantity:.{qty_precision}f}, 实际盈亏={actual_pnl:+.2f} ({pnl_pct:+.2f}%), 原因: {exit_reason}")
                                
                                positions.remove(position)
                                closed_at_current_time = True
                            else:
                                error_msg = close_result.get('message', '未知错误')
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ❌ 平仓失败: {error_msg}")
                                logger.error(f"{symbol} 平仓失败 (持仓ID: {position_id}): {error_msg}")
                        else:
                            positions.remove(position)
                            closed_at_current_time = True
            
            # 检查卖出信号（使用实时价格）- 只检测最新K线的穿越（与买入逻辑一致）
            # EMA信号每60秒检查一次，止损止盈每5秒检查（上面已处理）
            if not closed_at_current_time and len(positions) > 0 and should_check_ema:
                sell_signal_triggered = False
                current_sell_index = len(sell_indicator_pairs) - 1

                # 只检测最新K线与前一根K线之间是否发生穿越
                if current_sell_index > 0:
                    curr_sell_pair = sell_indicator_pairs[current_sell_index]
                    prev_sell_pair = sell_indicator_pairs[current_sell_index - 1]
                    curr_sell_indicator = curr_sell_pair['indicator']
                    prev_sell_indicator = prev_sell_pair['indicator']

                    if sell_signal == 'ma_ema5':
                        ma5 = float(curr_sell_indicator.get('ma5')) if curr_sell_indicator.get('ma5') else None
                        ema5 = float(curr_sell_indicator.get('ema5')) if curr_sell_indicator.get('ema5') else None
                        prev_ma5 = float(prev_sell_indicator.get('ma5')) if prev_sell_indicator.get('ma5') else None
                        prev_ema5 = float(prev_sell_indicator.get('ema5')) if prev_sell_indicator.get('ema5') else None

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
                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA5/EMA5死叉 - 触发做多平仓信号"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    break
                                elif pos_direction == 'short' and ma5_ema5_is_golden:
                                    sell_signal_triggered = True
                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA5/EMA5金叉 - 触发做空平仓信号"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    break

                            if not sell_signal_triggered:
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: 📊 MA5/EMA5状态 | MA5={ma5:.4f}, EMA5={ema5:.4f}, 当前K线未发生反向穿越")

                    elif sell_signal == 'ma_ema10':
                        sell_ma10 = float(curr_sell_indicator.get('ma10')) if curr_sell_indicator.get('ma10') else None
                        sell_ema10 = float(curr_sell_indicator.get('ema10')) if curr_sell_indicator.get('ema10') else None
                        prev_ma10 = float(prev_sell_indicator.get('ma10')) if prev_sell_indicator.get('ma10') else None
                        prev_ema10 = float(prev_sell_indicator.get('ema10')) if prev_sell_indicator.get('ema10') else None

                        if sell_ma10 and sell_ema10 and prev_ma10 and prev_ema10:
                            # 检测金叉和死叉
                            ma10_ema10_is_golden = (prev_ema10 <= prev_ma10 and sell_ema10 > sell_ma10) or \
                                                   (prev_ema10 < prev_ma10 and sell_ema10 >= sell_ma10)
                            ma10_ema10_is_death = (prev_ema10 >= prev_ma10 and sell_ema10 < sell_ma10) or \
                                                  (prev_ema10 > prev_ma10 and sell_ema10 <= sell_ma10)

                            # 根据持仓方向决定平仓信号
                            for pos in positions:
                                pos_direction = pos.get('direction')
                                if pos_direction == 'long' and ma10_ema10_is_death:
                                    sell_signal_triggered = True
                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA10/EMA10死叉 - 触发做多平仓信号"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    break
                                elif pos_direction == 'short' and ma10_ema10_is_golden:
                                    sell_signal_triggered = True
                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到MA10/EMA10金叉 - 触发做空平仓信号"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    break

                            if not sell_signal_triggered:
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: 📊 MA10/EMA10状态 | MA10={sell_ma10:.4f}, EMA10={sell_ema10:.4f}, 当前K线未发生反向穿越")

                    elif sell_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                        sell_ema_short = float(curr_sell_indicator.get('ema_short')) if curr_sell_indicator.get('ema_short') else None
                        sell_ema_long = float(curr_sell_indicator.get('ema_long')) if curr_sell_indicator.get('ema_long') else None
                        prev_ema_short = float(prev_sell_indicator.get('ema_short')) if prev_sell_indicator.get('ema_short') else None
                        prev_ema_long = float(prev_sell_indicator.get('ema_long')) if prev_sell_indicator.get('ema_long') else None

                        if sell_ema_short and sell_ema_long and prev_ema_short and prev_ema_long:
                            # 检测金叉和死叉
                            ema_is_golden = (prev_ema_short <= prev_ema_long and sell_ema_short > sell_ema_long) or \
                                            (prev_ema_short < prev_ema_long and sell_ema_short >= sell_ema_long)
                            ema_is_death = (prev_ema_short >= prev_ema_long and sell_ema_short < sell_ema_long) or \
                                           (prev_ema_short > prev_ema_long and sell_ema_short <= sell_ema_long)

                            # 根据持仓方向决定平仓信号：
                            # - 做多持仓：检测到死叉时平仓
                            # - 做空持仓：检测到金叉时平仓
                            # 遍历所有持仓，只要有反向信号就触发平仓
                            for pos in positions:
                                pos_direction = pos.get('direction')
                                if pos_direction == 'long' and ema_is_death:
                                    sell_signal_triggered = True
                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到EMA9/26死叉 - 触发做多平仓信号"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    break
                                elif pos_direction == 'short' and ema_is_golden:
                                    sell_signal_triggered = True
                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: ✅ 检测到EMA9/26金叉 - 触发做空平仓信号"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    break

                            if not sell_signal_triggered:
                                sell_status = "多头" if sell_ema_short > sell_ema_long else "空头"
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{sell_timeframe}]: 📊 EMA9/26状态 - {sell_status} | EMA9={sell_ema_short:.4f}, EMA26={sell_ema_long:.4f}, 当前K线未发生反向穿越")

                # 平仓成交量条件已移除，直接执行卖出

                # 执行卖出（使用实时价格）
                if sell_signal_triggered:
                    for position in positions[:]:
                        position_id = position.get('position_id')
                        entry_price = position['entry_price']
                        quantity = position['quantity']
                        direction = position['direction']

                        if position_id:
                            exit_price_decimal = Decimal(str(realtime_price))
                            close_result = trading_engine.close_position(
                                position_id=position_id,
                                close_quantity=None,
                                reason='卖出信号触发',
                                close_price=exit_price_decimal
                            )

                            if close_result.get('success'):
                                actual_exit_price = float(close_result.get('exit_price', realtime_price))
                                actual_quantity = float(close_result.get('quantity', quantity))
                                actual_pnl = float(close_result.get('realized_pnl', 0))
                                actual_fee = float(close_result.get('fee', 0))

                                self._save_trade_record(
                                            symbol=symbol,
                                    action='SELL',
                                            direction=direction,
                                    entry_price=entry_price,
                                    exit_price=actual_exit_price,
                                    quantity=actual_quantity,
                                    leverage=leverage,
                                    fee=actual_fee,
                                    realized_pnl=actual_pnl,
                                    strategy_id=strategy_id,
                                    strategy_name=strategy_name,
                                    account_id=account_id,
                                    reason='卖出信号触发',
                                    trade_time=current_time_local
                                )
                                
                                direction_text = "做多" if direction == 'long' else "做空"
                                qty_precision = self.get_quantity_precision(symbol)
                                margin_used = (entry_price * actual_quantity) / leverage
                                pnl_pct = (actual_pnl / margin_used) * 100 if margin_used > 0 else 0
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 平仓{direction_text} | 入场价={entry_price:.4f}, 平仓价={actual_exit_price:.4f}, 数量={actual_quantity:.{qty_precision}f}, 实际盈亏={actual_pnl:+.2f} ({pnl_pct:+.2f}%)")
                                
                                positions.remove(position)
                                closed_at_current_time = True
                            else:
                                error_msg = close_result.get('message', '未知错误')
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ❌ 平仓失败: {error_msg}")
                                logger.error(f"{symbol} 平仓失败 (持仓ID: {position_id}): {error_msg}")
                        else:
                            positions.remove(position)
                            closed_at_current_time = True
        
        # 2. 检查买入信号（开仓）- 使用实时价格
        # EMA信号每60秒检查一次（should_check_ema 在卖出信号检查时已设置）
        # 先遍历过去24小时内的所有K线对，检查是否有EMA穿越信号
        # EMA穿越信号需要比较相邻两个K线的EMA值，不能只检查当前K线

        # 使用实时价格
        entry_price_base = realtime_price

        # 初始化变量
        buy_signal_triggered = False
        is_early_entry_signal = False  # 是否为预判信号（预判信号不触发closeOppositeOnEntry）
        is_sustained_signal = False  # 是否为持续趋势信号（跳过MACD/KDJ/MA10信号强度检查）
        found_golden_cross = False
        found_death_cross = False
        detected_cross_type = None
        buy_pair = None
        buy_indicator = None
        ema_short = None
        ema_long = None
        ma10 = None
        ema10 = None
        ma10_ema10_diff = None
        ma10_ema10_diff_pct = None
        curr_diff_pct = 0

        # 只检测最新K线是否发生穿越（使用24小时数据计算EMA，但只在当前穿越时买入）
        # 只有当 should_check_ema 为 True 时才检查买入信号
        current_buy_index = len(buy_indicator_pairs) - 1
        if current_buy_index > 0 and should_check_ema:
            # 只检测最新K线与前一根K线之间是否发生穿越
            curr_pair = buy_indicator_pairs[current_buy_index]
            prev_pair = buy_indicator_pairs[current_buy_index - 1]
            curr_indicator = curr_pair['indicator']
            prev_indicator = prev_pair['indicator']

            curr_ema_short = float(curr_indicator['ema_short']) if curr_indicator.get('ema_short') else None
            curr_ema_long = float(curr_indicator['ema_long']) if curr_indicator.get('ema_long') else None
            prev_ema_short = float(prev_indicator['ema_short']) if prev_indicator.get('ema_short') else None
            prev_ema_long = float(prev_indicator['ema_long']) if prev_indicator.get('ema_long') else None
            curr_ma10 = float(curr_indicator.get('ma10')) if curr_indicator.get('ma10') else None
            curr_ema10 = float(curr_indicator.get('ema10')) if curr_indicator.get('ema10') else None
            prev_ma10 = float(prev_indicator.get('ma10')) if prev_indicator.get('ma10') else None
            prev_ema10 = float(prev_indicator.get('ema10')) if prev_indicator.get('ema10') else None

            # 检查EMA数据是否完整
            if prev_ema_short and prev_ema_long and curr_ema_short and curr_ema_long:
                # 记录EMA状态用于诊断
                signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                signal_time_local = self.utc_to_local(signal_time)
                prev_status = "多头" if prev_ema_short > prev_ema_long else "空头"
                curr_status = "多头" if curr_ema_short > curr_ema_long else "空头"
                logger.debug(f"{symbol} [{buy_timeframe}] {signal_time_local.strftime('%Y-%m-%d %H:%M')}: EMA状态检查 | 前K线: EMA9={prev_ema_short:.4f}, EMA26={prev_ema_long:.4f} ({prev_status}) | 当前K线: EMA9={curr_ema_short:.4f}, EMA26={curr_ema_long:.4f} ({curr_status})")
                
                # EMA9/26金叉（向上穿越）：前一个K线EMA9 <= EMA26，当前K线EMA9 > EMA26
                is_golden_cross = (prev_ema_short <= prev_ema_long and curr_ema_short > curr_ema_long) or \
                                 (prev_ema_short < prev_ema_long and curr_ema_short >= curr_ema_long)
                
                # EMA9/26死叉（向下穿越）：前一个K线EMA9 >= EMA26，当前K线EMA9 < EMA26
                is_death_cross = (prev_ema_short >= prev_ema_long and curr_ema_short < curr_ema_long) or \
                                 (prev_ema_short > prev_ema_long and curr_ema_short <= curr_ema_long)
                
                if is_golden_cross:
                    logger.debug(f"{symbol} [{buy_timeframe}] {signal_time_local.strftime('%Y-%m-%d %H:%M')}: 🔍 检测到EMA9/26金叉（向上穿越）")
                elif is_death_cross:
                    logger.debug(f"{symbol} [{buy_timeframe}] {signal_time_local.strftime('%Y-%m-%d %H:%M')}: 🔍 检测到EMA9/26死叉（向下穿越）")
                
                # MA10/EMA10金叉检测
                ma10_ema10_golden_cross = False
                if prev_ma10 and prev_ema10 and curr_ma10 and curr_ema10:
                    ma10_ema10_is_golden = (prev_ema10 <= prev_ma10 and curr_ema10 > curr_ma10) or \
                                           (prev_ema10 < prev_ma10 and curr_ema10 >= curr_ma10)
                    if ma10_ema10_is_golden:
                        ma10_ema10_golden_cross = True
                
                # 买入信号：根据 buySignals 配置决定使用哪个信号
                if buy_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                    # 检测 EMA9/26 金叉（做多）和死叉（做空）
                    if is_golden_cross and 'long' in buy_directions:
                        # 金叉 = 做多信号
                        # 使用当前K线的差值计算信号强度
                        curr_diff = curr_ema_short - curr_ema_long
                        curr_diff_pct = (curr_diff / curr_ema_long * 100) if curr_ema_long > 0 else 0
                        ema_strength_pct = abs(curr_diff_pct)

                        # 检查信号强度过滤（使用round避免浮点精度问题，如0.0799999显示为0.08但实际<0.08）
                        if min_ema_cross_strength > 0 and round(ema_strength_pct, 4) < round(min_ema_cross_strength, 4):
                            # 信号强度不足，记录调试信息
                            signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                            signal_time_local = self.utc_to_local(signal_time)
                            msg = f"{signal_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ EMA9/26金叉检测到，但信号强度不足 (差值={ema_strength_pct:.4f}% < {min_ema_cross_strength:.2f}%)"
                            debug_info.append(msg)
                            logger.info(f"{symbol} {msg}")
                        else:
                            # 找到信号，保存相关信息
                            buy_signal_triggered = True
                            found_golden_cross = True
                            detected_cross_type = 'golden'
                            buy_pair = curr_pair
                            buy_indicator = curr_indicator
                            ema_short = curr_ema_short
                            ema_long = curr_ema_long
                            curr_diff_pct = ema_strength_pct

                            # 记录信号检测信息
                            signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                            signal_time_local = self.utc_to_local(signal_time)
                            msg = f"{signal_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅✅✅ EMA9/26金叉检测成功（做多信号）- 当前K线穿越！"
                            debug_info.append(msg)
                            logger.info(f"{symbol} {msg}")
                            msg_detail = f"   📊 EMA9={ema_short:.4f}, EMA26={ema_long:.4f}, 差值={curr_diff:.4f} ({curr_diff_pct:+.2f}%)"
                            debug_info.append(msg_detail)
                            logger.info(f"{symbol} {msg_detail}")
                            if min_ema_cross_strength > 0:
                                msg_strength = f"   ✅ 信号强度检查通过 (差值={ema_strength_pct:.2f}% ≥ {min_ema_cross_strength:.2f}%)"
                                debug_info.append(msg_strength)
                                logger.info(f"{symbol} {msg_strength}")

                    elif is_death_cross and 'short' in buy_directions:
                        # 死叉 = 做空信号
                        # 使用当前K线的差值计算信号强度
                        curr_diff = curr_ema_short - curr_ema_long
                        curr_diff_pct = (curr_diff / curr_ema_long * 100) if curr_ema_long > 0 else 0
                        ema_strength_pct = abs(curr_diff_pct)

                        # 检查信号强度过滤（使用round避免浮点精度问题）
                        if min_ema_cross_strength > 0 and round(ema_strength_pct, 4) < round(min_ema_cross_strength, 4):
                            # 信号强度不足，记录调试信息
                            signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                            signal_time_local = self.utc_to_local(signal_time)
                            msg = f"{signal_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ EMA9/26死叉检测到，但信号强度不足 (差值={ema_strength_pct:.4f}% < {min_ema_cross_strength:.2f}%)"
                            debug_info.append(msg)
                            logger.info(f"{symbol} {msg}")
                        else:
                            # 找到信号，保存相关信息
                            buy_signal_triggered = True
                            found_death_cross = True
                            detected_cross_type = 'death'
                            buy_pair = curr_pair
                            buy_indicator = curr_indicator
                            ema_short = curr_ema_short
                            ema_long = curr_ema_long
                            curr_diff_pct = ema_strength_pct

                            # 记录信号检测信息
                            signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                            signal_time_local = self.utc_to_local(signal_time)
                            msg = f"{signal_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅✅✅ EMA9/26死叉检测成功（做空信号）- 当前K线穿越！"
                            debug_info.append(msg)
                            logger.info(f"{symbol} {msg}")
                            msg_detail = f"   📊 EMA9={ema_short:.4f}, EMA26={ema_long:.4f}, 差值={curr_diff:.4f} ({curr_diff_pct:+.2f}%)"
                            debug_info.append(msg_detail)
                            logger.info(f"{symbol} {msg_detail}")
                            if min_ema_cross_strength > 0:
                                msg_strength = f"   ✅ 信号强度检查通过 (差值={ema_strength_pct:.2f}% ≥ {min_ema_cross_strength:.2f}%)"
                                debug_info.append(msg_strength)
                                logger.info(f"{symbol} {msg_strength}")
                    else:
                        # 当前K线没有穿越，记录当前EMA状态
                        latest_diff = curr_ema_short - curr_ema_long
                        latest_diff_pct = (latest_diff / curr_ema_long * 100) if curr_ema_long > 0 else 0
                        latest_status = "多头" if curr_ema_short > curr_ema_long else "空头"
                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: 📊 EMA9/26状态 - {latest_status} | EMA9={curr_ema_short:.4f}, EMA26={curr_ema_long:.4f}, 差值={latest_diff:.4f} ({latest_diff_pct:+.2f}%)")

                        # ==================== 提前入场预判逻辑 ====================
                        # 如果启用了提前入场且当前没有检测到金叉/死叉，检查是否接近穿越点
                        if early_entry_enabled and not buy_signal_triggered:
                            # 获取当前K线收盘价
                            curr_close = float(curr_pair['kline']['close_price']) if curr_pair['kline'].get('close_price') else None

                            # 计算EMA差距百分比（绝对值）
                            ema_gap_pct = abs(latest_diff_pct)

                            # 计算EMA9斜率（当前EMA9 vs 前一根K线EMA9）
                            ema9_slope = 0
                            ema9_slope_pct = 0
                            if prev_ema_short and curr_ema_short:
                                ema9_slope = curr_ema_short - prev_ema_short
                                ema9_slope_pct = (ema9_slope / prev_ema_short * 100) if prev_ema_short > 0 else 0

                            # 预判金叉条件（做多）：
                            # 1. 当前处于空头状态（EMA9 < EMA26）
                            # 2. EMA差距小于阈值（即将穿越）
                            # 3. EMA9斜率为正（向上趋势）
                            # 4. 价格在EMA9上方（可选）
                            if 'long' in buy_directions and curr_ema_short < curr_ema_long:
                                early_entry_conditions_met = True
                                early_entry_reasons = []

                                # 条件1：EMA差距检查
                                if ema_gap_pct > early_entry_gap_threshold:
                                    early_entry_conditions_met = False
                                    early_entry_reasons.append(f"EMA差距过大({ema_gap_pct:.2f}% > {early_entry_gap_threshold}%)")

                                # 条件2：EMA9向上斜率检查
                                if early_entry_require_upward_slope:
                                    if ema9_slope_pct < early_entry_slope_min_pct:
                                        early_entry_conditions_met = False
                                        early_entry_reasons.append(f"EMA9斜率不足({ema9_slope_pct:.3f}% < {early_entry_slope_min_pct}%)")

                                # 条件3：价格在EMA9上方检查
                                if early_entry_require_price_above_ema and curr_close:
                                    if curr_close <= curr_ema_short:
                                        early_entry_conditions_met = False
                                        early_entry_reasons.append(f"价格未在EMA9上方(价格={curr_close:.4f}, EMA9={curr_ema_short:.4f})")

                                if early_entry_conditions_met:
                                    # 预判金叉成功！
                                    buy_signal_triggered = True
                                    found_golden_cross = True
                                    detected_cross_type = 'golden'
                                    is_early_entry_signal = True  # 标记为预判信号
                                    buy_pair = curr_pair
                                    buy_indicator = curr_indicator
                                    ema_short = curr_ema_short
                                    ema_long = curr_ema_long
                                    curr_diff_pct = ema_gap_pct

                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: 🔮🔮🔮 预判金叉信号（提前入场做多）！"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    msg_detail = f"   📊 EMA9={curr_ema_short:.4f}, EMA26={curr_ema_long:.4f}, 差距={ema_gap_pct:.2f}%, EMA9斜率={ema9_slope_pct:+.3f}%"
                                    debug_info.append(msg_detail)
                                    logger.info(f"{symbol} {msg_detail}")
                                    if curr_close:
                                        msg_price = f"   📊 当前价格={curr_close:.4f}, 价格/EMA9比={((curr_close/curr_ema_short-1)*100):+.2f}%"
                                        debug_info.append(msg_price)
                                        logger.info(f"{symbol} {msg_price}")
                                else:
                                    # 预判条件不满足，记录原因
                                    debug_info.append(f"   📊 预判金叉检查: EMA差距={ema_gap_pct:.2f}%, EMA9斜率={ema9_slope_pct:+.3f}%")
                                    for reason in early_entry_reasons:
                                        debug_info.append(f"   ⚠️ 预判条件不满足: {reason}")

                            # 预判死叉条件（做空）：
                            # 1. 当前处于多头状态（EMA9 > EMA26）
                            # 2. EMA差距小于阈值（即将穿越）
                            # 3. EMA9斜率为负（向下趋势）
                            # 4. 价格在EMA9下方（可选）
                            elif 'short' in buy_directions and curr_ema_short > curr_ema_long:
                                early_entry_conditions_met = True
                                early_entry_reasons = []

                                # 条件1：EMA差距检查
                                if ema_gap_pct > early_entry_gap_threshold:
                                    early_entry_conditions_met = False
                                    early_entry_reasons.append(f"EMA差距过大({ema_gap_pct:.2f}% > {early_entry_gap_threshold}%)")

                                # 条件2：EMA9向下斜率检查（做空时要求向下）
                                if early_entry_require_upward_slope:
                                    if ema9_slope_pct > -early_entry_slope_min_pct:  # 做空时要求负斜率
                                        early_entry_conditions_met = False
                                        early_entry_reasons.append(f"EMA9斜率不足({ema9_slope_pct:.3f}% > -{early_entry_slope_min_pct}%)")

                                # 条件3：价格在EMA9下方检查（做空时）
                                if early_entry_require_price_above_ema and curr_close:
                                    if curr_close >= curr_ema_short:
                                        early_entry_conditions_met = False
                                        early_entry_reasons.append(f"价格未在EMA9下方(价格={curr_close:.4f}, EMA9={curr_ema_short:.4f})")

                                if early_entry_conditions_met:
                                    # 预判死叉成功！
                                    buy_signal_triggered = True
                                    found_death_cross = True
                                    detected_cross_type = 'death'
                                    is_early_entry_signal = True  # 标记为预判信号
                                    buy_pair = curr_pair
                                    buy_indicator = curr_indicator
                                    ema_short = curr_ema_short
                                    ema_long = curr_ema_long
                                    curr_diff_pct = ema_gap_pct

                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: 🔮🔮🔮 预判死叉信号（提前入场做空）！"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    msg_detail = f"   📊 EMA9={curr_ema_short:.4f}, EMA26={curr_ema_long:.4f}, 差距={ema_gap_pct:.2f}%, EMA9斜率={ema9_slope_pct:+.3f}%"
                                    debug_info.append(msg_detail)
                                    logger.info(f"{symbol} {msg_detail}")
                                    if curr_close:
                                        msg_price = f"   📊 当前价格={curr_close:.4f}, 价格/EMA9比={((curr_close/curr_ema_short-1)*100):+.2f}%"
                                        debug_info.append(msg_price)
                                        logger.info(f"{symbol} {msg_price}")
                                else:
                                    # 预判条件不满足，记录原因
                                    debug_info.append(f"   📊 预判死叉检查: EMA差距={ema_gap_pct:.2f}%, EMA9斜率={ema9_slope_pct:+.3f}%")
                                    for reason in early_entry_reasons:
                                        debug_info.append(f"   ⚠️ 预判条件不满足: {reason}")

                        # ==================== 持续趋势信号逻辑 ====================
                        # 如果启用了持续趋势信号且当前没有检测到任何信号，检查是否处于强趋势中
                        if sustained_trend_enabled and not buy_signal_triggered:
                            # 获取当前K线收盘价
                            curr_close = float(curr_pair['kline']['close_price']) if curr_pair['kline'].get('close_price') else None

                            # 计算EMA差距百分比（绝对值）
                            ema_strength_pct = abs(latest_diff_pct)

                            # 输出持续趋势检查的基础状态日志
                            ema_status = "空头" if curr_ema_short < curr_ema_long else "多头"
                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: 🔄 持续趋势检查 - EMA9/26 {ema_status} | EMA9={curr_ema_short:.4f}, EMA26={curr_ema_long:.4f}, 差值={latest_diff:.4f} ({latest_diff_pct:+.2f}%)")
                            logger.debug(f"{symbol} [{buy_timeframe}] {current_time_local.strftime('%Y-%m-%d %H:%M')}: 🔄 持续趋势检查 - EMA9/26 {ema_status}")

                            if curr_ma10 and curr_ema10:
                                ma10_diff = curr_ema10 - curr_ma10
                                ma10_diff_pct = (ma10_diff / curr_ma10 * 100) if curr_ma10 > 0 else 0
                                ma10_status = "多头" if curr_ema10 > curr_ma10 else "空头"
                                debug_info.append(f"   📊 MA10/EMA10状态 - {ma10_status} | MA10={curr_ma10:.4f}, EMA10={curr_ema10:.4f}, 差值={ma10_diff:.4f} ({ma10_diff_pct:+.2f}%)")

                            # 检查是否处于持续空头趋势（做空机会）
                            if 'short' in buy_directions and curr_ema_short < curr_ema_long:
                                sustained_conditions_met = True
                                sustained_reasons = []
                                is_sustained_signal = False

                                # 条件1：趋势强度检查（EMA差距在合理范围内）
                                if ema_strength_pct < sustained_trend_min_strength:
                                    sustained_conditions_met = False
                                    sustained_reasons.append(f"趋势强度不足({ema_strength_pct:.2f}% < {sustained_trend_min_strength}%)")
                                elif ema_strength_pct > sustained_trend_max_strength:
                                    sustained_conditions_met = False
                                    sustained_reasons.append(f"趋势强度过高({ema_strength_pct:.2f}% > {sustained_trend_max_strength}%)，可能追高")

                                # 条件2：MA10/EMA10趋势确认（如果启用）
                                if sustained_trend_require_ma10_confirm and curr_ma10 and curr_ema10:
                                    if curr_ema10 >= curr_ma10:  # 做空时要求EMA10 < MA10（空头趋势）
                                        sustained_conditions_met = False
                                        sustained_reasons.append(f"MA10/EMA10未确认空头趋势(EMA10={curr_ema10:.4f} >= MA10={curr_ma10:.4f})")

                                # 条件3：价格确认（如果启用）- 使用实时价格而非历史K线收盘价
                                if sustained_trend_require_price_confirm and realtime_price:
                                    if realtime_price >= curr_ema_short:  # 做空时价格应在EMA9下方
                                        sustained_conditions_met = False
                                        sustained_reasons.append(f"价格未在EMA9下方(实时价格={realtime_price:.4f} >= EMA9={curr_ema_short:.4f})")

                                # 条件4：连续K线确认（检查历史K线是否持续保持趋势）
                                if sustained_trend_min_bars > 0 and current_buy_index >= sustained_trend_min_bars:
                                    bars_in_trend = 0
                                    for i in range(sustained_trend_min_bars):
                                        check_idx = current_buy_index - i
                                        if check_idx >= 0:
                                            check_pair = buy_indicator_pairs[check_idx]
                                            check_ema_short = float(check_pair['indicator'].get('ema_short', 0))
                                            check_ema_long = float(check_pair['indicator'].get('ema_long', 0))
                                            if check_ema_short < check_ema_long:  # 空头状态
                                                bars_in_trend += 1
                                    if bars_in_trend < sustained_trend_min_bars:
                                        sustained_conditions_met = False
                                        sustained_reasons.append(f"趋势持续性不足(连续{bars_in_trend}根K线 < 要求{sustained_trend_min_bars}根)")

                                # 条件5：冷却时间检查（检查最近是否已经因持续趋势信号开过仓）
                                if sustained_conditions_met and sustained_trend_cooldown_minutes > 0:
                                    # 查询最近的交易记录，检查是否在冷却期内有过开仓
                                    cooldown_start = current_time_local - timedelta(minutes=sustained_trend_cooldown_minutes)
                                    for pos in positions:
                                        pos_entry_time = pos.get('entry_time_local')
                                        if pos_entry_time and pos.get('direction') == 'short':
                                            if pos_entry_time >= cooldown_start:
                                                sustained_conditions_met = False
                                                remaining_minutes = sustained_trend_cooldown_minutes - int((current_time_local - pos_entry_time).total_seconds() / 60)
                                                sustained_reasons.append(f"冷却期内(剩余{remaining_minutes}分钟)")
                                                break

                                if sustained_conditions_met:
                                    # 持续趋势做空信号触发！
                                    buy_signal_triggered = True
                                    found_death_cross = True
                                    detected_cross_type = 'death'
                                    is_sustained_signal = True
                                    buy_pair = curr_pair
                                    buy_indicator = curr_indicator
                                    ema_short = curr_ema_short
                                    ema_long = curr_ema_long
                                    curr_diff_pct = ema_strength_pct

                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅✅✅ 持续趋势做空信号触发！"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    msg_strength = f"   ✅ 趋势强度检查通过 (差值={ema_strength_pct:.2f}%, 范围={sustained_trend_min_strength}%~{sustained_trend_max_strength}%)"
                                    debug_info.append(msg_strength)
                                    logger.info(f"{symbol} {msg_strength}")
                                    if curr_ma10 and curr_ema10:
                                        ma10_diff_val = curr_ema10 - curr_ma10
                                        ma10_diff_pct_val = (ma10_diff_val / curr_ma10 * 100) if curr_ma10 > 0 else 0
                                        ma10_status = "空头" if curr_ema10 < curr_ma10 else "多头"
                                        msg_ma10 = f"   ✅ MA10/EMA10趋势确认 ({ma10_status}) | MA10={curr_ma10:.4f}, EMA10={curr_ema10:.4f}, 差值={ma10_diff_pct_val:+.2f}%"
                                        debug_info.append(msg_ma10)
                                        logger.info(f"{symbol} {msg_ma10}")
                                    if realtime_price:
                                        msg_price = f"   ✅ 价格确认 | 实时价格={realtime_price:.4f} < EMA9={curr_ema_short:.4f}"
                                        debug_info.append(msg_price)
                                        logger.info(f"{symbol} {msg_price}")
                                    if sustained_trend_min_bars > 0:
                                        msg_bars = f"   ✅ 趋势持续性检查通过 (连续{sustained_trend_min_bars}根K线保持空头)"
                                        debug_info.append(msg_bars)
                                        logger.info(f"{symbol} {msg_bars}")
                                    # 获取成交量比率
                                    volume_ratio = float(curr_indicator.get('volume_ratio', 1.0)) if curr_indicator.get('volume_ratio') else 1.0
                                    msg_volume = f"   📊 成交量比率: {volume_ratio:.2f}x"
                                    debug_info.append(msg_volume)
                                    logger.info(f"{symbol} {msg_volume}")
                                    msg_direction = f"   📊 方向判断：持续空头趋势，选择做空"
                                    debug_info.append(msg_direction)
                                    logger.info(f"{symbol} {msg_direction}")
                                else:
                                    # 持续趋势条件不满足
                                    for reason in sustained_reasons:
                                        debug_info.append(f"   ⚠️ 持续空头趋势条件不满足: {reason}")
                                        logger.debug(f"{symbol} ⚠️ 持续空头趋势条件不满足: {reason}")

                            # 检查是否处于持续多头趋势（做多机会）
                            elif 'long' in buy_directions and curr_ema_short > curr_ema_long:
                                sustained_conditions_met = True
                                sustained_reasons = []
                                is_sustained_signal = False

                                # 条件1：趋势强度检查
                                if ema_strength_pct < sustained_trend_min_strength:
                                    sustained_conditions_met = False
                                    sustained_reasons.append(f"趋势强度不足({ema_strength_pct:.2f}% < {sustained_trend_min_strength}%)")
                                elif ema_strength_pct > sustained_trend_max_strength:
                                    sustained_conditions_met = False
                                    sustained_reasons.append(f"趋势强度过高({ema_strength_pct:.2f}% > {sustained_trend_max_strength}%)，可能追高")

                                # 条件2：MA10/EMA10趋势确认
                                if sustained_trend_require_ma10_confirm and curr_ma10 and curr_ema10:
                                    if curr_ema10 <= curr_ma10:  # 做多时要求EMA10 > MA10（多头趋势）
                                        sustained_conditions_met = False
                                        sustained_reasons.append(f"MA10/EMA10未确认多头趋势(EMA10={curr_ema10:.4f} <= MA10={curr_ma10:.4f})")

                                # 条件3：价格确认 - 使用实时价格而非历史K线收盘价
                                if sustained_trend_require_price_confirm and realtime_price:
                                    if realtime_price <= curr_ema_short:  # 做多时价格应在EMA9上方
                                        sustained_conditions_met = False
                                        sustained_reasons.append(f"价格未在EMA9上方(实时价格={realtime_price:.4f} <= EMA9={curr_ema_short:.4f})")

                                # 条件4：连续K线确认
                                if sustained_trend_min_bars > 0 and current_buy_index >= sustained_trend_min_bars:
                                    bars_in_trend = 0
                                    for i in range(sustained_trend_min_bars):
                                        check_idx = current_buy_index - i
                                        if check_idx >= 0:
                                            check_pair = buy_indicator_pairs[check_idx]
                                            check_ema_short = float(check_pair['indicator'].get('ema_short', 0))
                                            check_ema_long = float(check_pair['indicator'].get('ema_long', 0))
                                            if check_ema_short > check_ema_long:  # 多头状态
                                                bars_in_trend += 1
                                    if bars_in_trend < sustained_trend_min_bars:
                                        sustained_conditions_met = False
                                        sustained_reasons.append(f"趋势持续性不足(连续{bars_in_trend}根K线 < 要求{sustained_trend_min_bars}根)")

                                # 条件5：冷却时间检查
                                if sustained_conditions_met and sustained_trend_cooldown_minutes > 0:
                                    cooldown_start = current_time_local - timedelta(minutes=sustained_trend_cooldown_minutes)
                                    for pos in positions:
                                        pos_entry_time = pos.get('entry_time_local')
                                        if pos_entry_time and pos.get('direction') == 'long':
                                            if pos_entry_time >= cooldown_start:
                                                sustained_conditions_met = False
                                                remaining_minutes = sustained_trend_cooldown_minutes - int((current_time_local - pos_entry_time).total_seconds() / 60)
                                                sustained_reasons.append(f"冷却期内(剩余{remaining_minutes}分钟)")
                                                break

                                if sustained_conditions_met:
                                    # 持续趋势做多信号触发！
                                    buy_signal_triggered = True
                                    found_golden_cross = True
                                    detected_cross_type = 'golden'
                                    is_sustained_signal = True
                                    buy_pair = curr_pair
                                    buy_indicator = curr_indicator
                                    ema_short = curr_ema_short
                                    ema_long = curr_ema_long
                                    curr_diff_pct = ema_strength_pct

                                    msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅✅✅ 持续趋势做多信号触发！"
                                    debug_info.append(msg)
                                    logger.info(f"{symbol} {msg}")
                                    msg_strength = f"   ✅ 趋势强度检查通过 (差值={ema_strength_pct:.2f}%, 范围={sustained_trend_min_strength}%~{sustained_trend_max_strength}%)"
                                    debug_info.append(msg_strength)
                                    logger.info(f"{symbol} {msg_strength}")
                                    if curr_ma10 and curr_ema10:
                                        ma10_diff_val = curr_ema10 - curr_ma10
                                        ma10_diff_pct_val = (ma10_diff_val / curr_ma10 * 100) if curr_ma10 > 0 else 0
                                        ma10_status = "多头" if curr_ema10 > curr_ma10 else "空头"
                                        msg_ma10 = f"   ✅ MA10/EMA10趋势确认 ({ma10_status}) | MA10={curr_ma10:.4f}, EMA10={curr_ema10:.4f}, 差值={ma10_diff_pct_val:+.2f}%"
                                        debug_info.append(msg_ma10)
                                        logger.info(f"{symbol} {msg_ma10}")
                                    if realtime_price:
                                        msg_price = f"   ✅ 价格确认 | 实时价格={realtime_price:.4f} > EMA9={curr_ema_short:.4f}"
                                        debug_info.append(msg_price)
                                        logger.info(f"{symbol} {msg_price}")
                                    if sustained_trend_min_bars > 0:
                                        msg_bars = f"   ✅ 趋势持续性检查通过 (连续{sustained_trend_min_bars}根K线保持多头)"
                                        debug_info.append(msg_bars)
                                        logger.info(f"{symbol} {msg_bars}")
                                    # 获取成交量比率
                                    volume_ratio = float(curr_indicator.get('volume_ratio', 1.0)) if curr_indicator.get('volume_ratio') else 1.0
                                    msg_volume = f"   📊 成交量比率: {volume_ratio:.2f}x"
                                    debug_info.append(msg_volume)
                                    logger.info(f"{symbol} {msg_volume}")
                                    msg_direction = f"   📊 方向判断：持续多头趋势，选择做多"
                                    debug_info.append(msg_direction)
                                    logger.info(f"{symbol} {msg_direction}")
                                else:
                                    # 持续趋势条件不满足
                                    for reason in sustained_reasons:
                                        debug_info.append(f"   ⚠️ 持续多头趋势条件不满足: {reason}")
                                        logger.debug(f"{symbol} ⚠️ 持续多头趋势条件不满足: {reason}")

                        if not buy_signal_triggered:
                            debug_info.append(f"   ⚠️ 当前K线未发生EMA穿越")

                elif buy_signal == 'ma_ema10':
                    # 使用 MA10/EMA10 金叉
                    if ma10_ema10_golden_cross:
                        # 检查信号强度过滤
                        strength_ok = True
                        if curr_ma10 and curr_ema10:
                            ma10_ema10_diff = curr_ema10 - curr_ma10
                            ma10_ema10_strength_pct = abs(ma10_ema10_diff / curr_ma10 * 100) if curr_ma10 > 0 else 0
                            if min_ma10_cross_strength > 0 and ma10_ema10_strength_pct < min_ma10_cross_strength:
                                strength_ok = False
                                signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                                signal_time_local = self.utc_to_local(signal_time)
                                debug_info.append(f"{signal_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MA10/EMA10金叉检测到，但信号强度不足 (差值={ma10_ema10_strength_pct:.2f}% < {min_ma10_cross_strength:.2f}%)")

                        if strength_ok:
                            # 找到信号，保存相关信息
                            buy_signal_triggered = True
                            found_golden_cross = True
                            buy_pair = curr_pair
                            buy_indicator = curr_indicator
                            ma10 = curr_ma10
                            ema10 = curr_ema10
                            ma10_ema10_diff = curr_ema10 - curr_ma10
                            ma10_ema10_diff_pct = (ma10_ema10_diff / ma10 * 100) if ma10 > 0 else None

                            # 记录信号检测信息
                            signal_time = self.parse_time(curr_pair['kline']['timestamp'])
                            signal_time_local = self.utc_to_local(signal_time)
                            debug_info.append(f"{signal_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅✅✅ MA10/EMA10金叉检测成功 - 当前K线穿越！")
                            debug_info.append(f"   📊 MA10={ma10:.4f}, EMA10={ema10:.4f}, 差值={ma10_ema10_diff:.4f} ({ma10_ema10_diff_pct:+.2f}%)" if ma10_ema10_diff_pct else f"   📊 MA10={ma10:.4f}, EMA10={ema10:.4f}")
        
        # 初始化 buy_volume_ratio 默认值（避免后续使用时未定义）
        buy_volume_ratio = 1.0

        # 如果检测到信号，使用信号K线的指标
        if buy_signal_triggered and buy_indicator:
            buy_volume_ratio = float(buy_indicator['volume_ratio']) if buy_indicator.get('volume_ratio') else 1.0
            debug_info.append(f"   📊 成交量比率: {buy_volume_ratio:.2f}x")
        
        # 执行买入
        # 检查是否可以开仓：防止重复开仓或检查最大持仓数
        can_open_position = True

        # 获取当前K线的时间戳（用于防止同一根K线重复触发）
        # 注意：使用最新K线的时间戳，而不是信号触发K线的时间戳
        # 因为信号可能是在几根K线前触发的（例如趋势确认时），但我们要防止的是在当前K线重复开仓
        current_kline_time = None
        if buy_signal_triggered and buy_indicator_pairs:
            # 使用最新K线的时间戳
            latest_buy_pair = buy_indicator_pairs[-1]
            current_kline_time = self.parse_time(latest_buy_pair['kline']['timestamp'])

        # 检查是否在同一根K线内已经开过仓（防止重复触发）
        # 注意：需要查询数据库中最近的交易记录，而不是只检查当前持仓（因为可能已平仓）
        if buy_signal_triggered and current_kline_time:
            # 计算K线的时间间隔（分钟）
            timeframe_minutes = {'5m': 5, '15m': 15, '1h': 60, '4h': 240, '1d': 1440}.get(buy_timeframe, 15)

            # 先检查当前持仓
            for pos in positions:
                pos_entry_time = pos.get('entry_time')
                if pos_entry_time:
                    time_diff = abs((pos_entry_time - current_kline_time).total_seconds() / 60)
                    if time_diff < timeframe_minutes:
                        can_open_position = False
                        msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 同一根K线内已有持仓，跳过重复信号（开仓时间: {pos_entry_time.strftime('%Y-%m-%d %H:%M')}）"
                        debug_info.append(msg)
                        logger.info(f"{symbol} {msg}")
                        break

            # 如果当前没有持仓，还需要查询最近的交易记录和待成交订单（防止平仓后立即重新开仓）
            if can_open_position:
                try:
                    connection = self._get_connection()
                    cursor = connection.cursor(pymysql.cursors.DictCursor)
                    # 查询该策略在当前K线时间范围内是否有开仓记录
                    # 注意：需要同时检查开始时间和结束时间，确保只匹配当前K线范围内的交易
                    kline_start_time = current_kline_time
                    kline_end_time = current_kline_time + timedelta(minutes=timeframe_minutes)
                    cursor.execute("""
                        SELECT trade_time FROM strategy_trade_records
                        WHERE symbol = %s AND strategy_id = %s AND action = 'BUY'
                        AND trade_time >= %s AND trade_time < %s
                        ORDER BY trade_time DESC LIMIT 1
                    """, (symbol, strategy_id, kline_start_time, kline_end_time))
                    recent_trade = cursor.fetchone()

                    if recent_trade:
                        can_open_position = False
                        recent_time = recent_trade['trade_time']
                        msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 同一根K线内已有交易记录，跳过重复信号（上次交易: {recent_time.strftime('%Y-%m-%d %H:%M:%S')}）"
                        debug_info.append(msg)
                        logger.info(f"{symbol} {msg}")
                    else:
                        # 检查是否有待成交的限价单（限价单创建时不写入交易记录）
                        cursor.execute("""
                            SELECT order_id, created_at FROM futures_orders
                            WHERE symbol = %s AND strategy_id = %s AND status = 'PENDING'
                            AND account_id = %s
                            ORDER BY created_at DESC LIMIT 1
                        """, (symbol, strategy_id, account_id))
                        pending_order = cursor.fetchone()

                        if pending_order:
                            can_open_position = False
                            order_time = pending_order['created_at']
                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已有待成交的限价单，跳过重复信号（订单创建: {order_time.strftime('%Y-%m-%d %H:%M:%S')}）"
                            debug_info.append(msg)
                            logger.info(f"{symbol} {msg}")

                    cursor.close()
                    connection.close()
                except Exception as e:
                    logger.warning(f"{symbol} 查询最近交易记录失败: {e}")

        if prevent_duplicate_entry and len(positions) > 0 and can_open_position:
            can_open_position = False
            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 防止重复开仓已启用，当前已有{len(positions)}个持仓，跳过买入信号"
            debug_info.append(msg)
            logger.info(f"{symbol} {msg}")
        elif max_positions is not None and len(positions) >= max_positions and can_open_position:
            can_open_position = False
            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已达到最大持仓数限制（{max_positions}个），当前持仓{len(positions)}个，跳过买入信号"
            debug_info.append(msg)
            logger.info(f"{symbol} {msg}")
        
        if buy_signal_triggered and can_open_position and not closed_at_current_time:
            if len(buy_directions) > 0:
                # 根据检测到的交叉类型确定方向（金叉=做多，死叉=做空）
                direction = None

                if detected_cross_type == 'golden':
                    # 金叉 = 做多
                    direction = 'long'
                    msg = f"   📊 方向判断：检测到金叉，选择做多"
                    debug_info.append(msg)
                    logger.info(f"{symbol} {msg}")
                elif detected_cross_type == 'death':
                    # 死叉 = 做空
                    direction = 'short'
                    msg = f"   📊 方向判断：检测到死叉，选择做空"
                    debug_info.append(msg)
                    logger.info(f"{symbol} {msg}")
                else:
                    # 如果没有检测到交叉类型，根据EMA状态判断（兼容旧逻辑）
                    ema_bullish = (ema_short and ema_long and ema_short > ema_long)
                    ma10_ema10_bullish = (ma10 and ema10 and ema10 > ma10) if (ma10 and ema10) else None

                    if len(buy_directions) > 1:
                        # 多方向配置，根据指标状态选择
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
                    else:
                        # 单方向配置，直接使用配置的方向
                        direction = buy_directions[0]
                        debug_info.append(f"   📊 方向判断：单一方向配置 {direction}")

                    # 如果仍未确定方向，使用默认逻辑
                    if direction is None:
                        if 'long' in buy_directions:
                            direction = 'long'
                            debug_info.append(f"   📊 方向判断：默认选择做多")
                        elif 'short' in buy_directions:
                            direction = 'short'
                            debug_info.append(f"   📊 方向判断：默认选择做空")
                        elif len(buy_directions) > 0:
                            direction = buy_directions[0]
                            debug_info.append(f"   📊 方向判断：使用第一个方向 {direction}")

                if direction is None:
                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 无法确定交易方向")
                else:
                    # 检查成交量条件
                    volume_condition_met = True
                    volume_reason = ""
                    if direction == 'long':
                        if buy_volume_enabled and buy_volume_long_enabled:
                            volume_condition = buy_volume_long or buy_volume
                            if volume_condition:
                                # 支持新的范围格式: <1, 1-2, >2
                                if volume_condition == '<1':
                                    if buy_volume_ratio >= 1.0:
                                        volume_condition_met = False
                                        volume_reason = f"做多成交量不符合 (当前:{buy_volume_ratio:.2f}x, 需要:<1x)"
                                elif volume_condition == '1-2':
                                    if not (1.0 <= buy_volume_ratio <= 2.0):
                                        volume_condition_met = False
                                        volume_reason = f"做多成交量不符合 (当前:{buy_volume_ratio:.2f}x, 需要:1-2x)"
                                elif volume_condition == '>2':
                                    if buy_volume_ratio <= 2.0:
                                        volume_condition_met = False
                                        volume_reason = f"做多成交量不符合 (当前:{buy_volume_ratio:.2f}x, 需要:>2x)"
                                else:
                                    # 尝试解析为单一数值（兼容旧格式）
                                    try:
                                        required_ratio = float(volume_condition)
                                        if buy_volume_ratio < required_ratio:
                                            volume_condition_met = False
                                            volume_reason = f"做多成交量不足 (当前:{buy_volume_ratio:.2f}x, 需要:≥{required_ratio}x)"
                                    except:
                                        volume_condition_met = False
                                        volume_reason = f"做多成交量条件格式错误: {volume_condition}"
                    else:
                        if buy_volume_enabled and (buy_volume_short_enabled or buy_volume_short):
                            volume_condition = buy_volume_short
                            if volume_condition:
                                # 支持新的范围格式: <1, 1-2, >2
                                if volume_condition == '<1':
                                    if buy_volume_ratio >= 1.0:
                                        volume_condition_met = False
                                        volume_reason = f"做空成交量不符合 (当前:{buy_volume_ratio:.2f}x, 需要:<1x)"
                                elif volume_condition == '1-2':
                                    if not (1.0 <= buy_volume_ratio <= 2.0):
                                        volume_condition_met = False
                                        volume_reason = f"做空成交量不符合 (当前:{buy_volume_ratio:.2f}x, 需要:1-2x)"
                                elif volume_condition == '>2':
                                    if buy_volume_ratio <= 2.0:
                                        volume_condition_met = False
                                        volume_reason = f"做空成交量不符合 (当前:{buy_volume_ratio:.2f}x, 需要:>2x)"
                                else:
                                    # 尝试解析为单一数值（兼容旧格式）
                                    try:
                                        required_ratio = float(volume_condition)
                                        if buy_volume_ratio < required_ratio:
                                            volume_condition_met = False
                                            volume_reason = f"做空成交量不足 (当前:{buy_volume_ratio:.2f}x, 需要:≥{required_ratio}x)"
                                    except (ValueError, TypeError):
                                        volume_condition_met = False
                                        volume_reason = f"做空成交量条件格式错误: {volume_condition}"

                    if not volume_condition_met:
                        signal_type = "EMA金叉" if direction == 'long' else "EMA死叉"
                        msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ {signal_type}但{volume_reason}"
                        debug_info.append(msg)
                        logger.info(f"{symbol} {msg}")
                    else:
                        logger.info(f"{symbol} [{buy_timeframe}]: ✅ 成交量条件检查通过 (成交量比率: {buy_volume_ratio:.2f}x)")
                        # 检查同方向持仓限制
                        if direction == 'long' and max_long_positions is not None:
                            long_positions_count = len([p for p in positions if p['direction'] == 'long'])
                            if long_positions_count >= max_long_positions:
                                can_open_position = False
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已达到最大做多持仓数限制（{max_long_positions}个），当前做多持仓{long_positions_count}个，跳过买入信号")
                        elif direction == 'short' and max_short_positions is not None:
                            short_positions_count = len([p for p in positions if p['direction'] == 'short'])
                            if short_positions_count >= max_short_positions:
                                can_open_position = False
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 已达到最大做空持仓数限制（{max_short_positions}个），当前做空持仓{short_positions_count}个，跳过买入信号")
                                
                        if can_open_position:
                            # 开仓前先平掉相反方向的持仓（如果启用）
                            # 注意：预判信号不触发此功能，只有确认信号才会平掉反向持仓
                            if close_opposite_on_entry and not is_early_entry_signal:
                                opposite_positions = [p for p in positions if p['direction'] != direction]
                                if opposite_positions:
                                    for opp_position in opposite_positions[:]:
                                        opp_position_id = opp_position.get('position_id')
                                        opp_entry_price = opp_position['entry_price']
                                        opp_quantity = opp_position['quantity']
                                        opp_direction = opp_position['direction']
                                                
                                        if opp_position_id:
                                            # 使用交易引擎执行平仓（使用实时价格）
                                            exit_price_decimal = Decimal(str(realtime_price))
                                            close_result = trading_engine.close_position(
                                                position_id=opp_position_id,
                                                close_quantity=None,  # None表示全部平仓
                                                reason=f'开{direction}仓前平仓',
                                                close_price=exit_price_decimal
                                            )
                                                    
                                            if close_result.get('success'):
                                                actual_exit_price = float(close_result.get('exit_price', realtime_price))
                                                actual_quantity = float(close_result.get('quantity', opp_quantity))
                                                actual_pnl = float(close_result.get('realized_pnl', 0))
                                                actual_fee = float(close_result.get('fee', 0))
                                                        
                                                # 保存交易记录到数据库
                                                self._save_trade_record(
                                                    symbol=symbol,
                                                    action='CLOSE',
                                                    direction=opp_direction,
                                                    entry_price=opp_entry_price,
                                                    exit_price=actual_exit_price,
                                                    quantity=actual_quantity,
                                                    leverage=leverage,
                                                    fee=actual_fee,
                                                    realized_pnl=actual_pnl,
                                                    strategy_id=strategy_id,
                                                    strategy_name=strategy_name,
                                                    account_id=account_id,
                                                    reason=f'开{direction}仓前平仓',
                                                    trade_time=self.utc_to_local(current_time) if current_time else self.get_local_time()
                                                )
                                                        
                                                opp_direction_text = "做多" if opp_direction == 'long' else "做空"
                                                qty_precision = self.get_quantity_precision(symbol)
                                                margin_used = (opp_entry_price * actual_quantity) / leverage
                                                pnl_pct = (actual_pnl / margin_used) * 100 if margin_used > 0 else 0
                                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 开{direction}仓前平掉{opp_direction_text}持仓 | 入场价={opp_entry_price:.4f}, 平仓价={actual_exit_price:.4f}, 数量={actual_quantity:.{qty_precision}f}, 实际盈亏={actual_pnl:+.2f} ({pnl_pct:+.2f}%)")
                                                        
                                                positions.remove(opp_position)
                                                closed_at_current_time = True
                                            else:
                                                error_msg = close_result.get('message', '未知错误')
                                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ❌ 平仓失败: {error_msg}")
                                                logger.error(f"{symbol} 平仓失败 (持仓ID: {opp_position_id}): {error_msg}")
                                        else:
                                            # 如果没有position_id，说明是模拟持仓，直接移除
                                            positions.remove(opp_position)
                                            closed_at_current_time = True
                                    
                            # 初始化趋势确认标志
                            trend_confirm_ok = True
                            logger.info(f"{symbol} [{buy_timeframe}]: 🔍 开始趋势确认和过滤检查 (方向: {direction})")
                                    
                            # 检查 RSI 过滤
                            # 预判信号只检查极端值（RSI<20或RSI>80），确认信号检查正常阈值
                            if rsi_filter_enabled:
                                rsi_value = float(buy_indicator.get('rsi')) if buy_indicator.get('rsi') else None
                                if rsi_value is not None:
                                    if is_early_entry_signal:
                                        # 预判信号：只过滤RSI极端值
                                        if direction == 'long' and rsi_value > 80:
                                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ RSI极端值过滤(预判)：做多时RSI过高 (RSI={rsi_value:.2f} > 80)，已过滤"
                                            debug_info.append(msg)
                                            logger.info(f"{symbol} {msg}")
                                            trend_confirm_ok = False
                                        elif direction == 'short' and rsi_value < 20:
                                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ RSI极端值过滤(预判)：做空时RSI过低 (RSI={rsi_value:.2f} < 20)，已过滤"
                                            debug_info.append(msg)
                                            logger.info(f"{symbol} {msg}")
                                            trend_confirm_ok = False
                                        else:
                                            logger.debug(f"{symbol} [{buy_timeframe}]: ✅ RSI极端值检查通过(预判) (RSI={rsi_value:.2f})")
                                    else:
                                        # 确认信号：使用正常阈值
                                        if direction == 'long' and rsi_value > rsi_long_max:
                                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ RSI过滤：做多时RSI过高 (RSI={rsi_value:.2f} > {rsi_long_max})，已过滤"
                                            debug_info.append(msg)
                                            logger.info(f"{symbol} {msg}")
                                            trend_confirm_ok = False
                                        elif direction == 'short' and rsi_value < rsi_short_min:
                                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ RSI过滤：做空时RSI过低 (RSI={rsi_value:.2f} < {rsi_short_min})，已过滤"
                                            debug_info.append(msg)
                                            logger.info(f"{symbol} {msg}")
                                            trend_confirm_ok = False
                                        else:
                                            logger.debug(f"{symbol} [{buy_timeframe}]: ✅ RSI过滤通过 (RSI={rsi_value:.2f})")
                                    
                            # 检查 MACD 过滤（预判信号和持续趋势信号跳过此过滤）
                            if trend_confirm_ok and macd_filter_enabled and not is_early_entry_signal and not is_sustained_signal:
                                macd_histogram = float(buy_indicator.get('macd_histogram')) if buy_indicator.get('macd_histogram') else None
                                if macd_histogram is not None:
                                            if direction == 'long' and macd_long_require_positive and macd_histogram <= 0:
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MACD过滤：做多时MACD柱状图非正 (MACD={macd_histogram:.4f})，已过滤"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                                trend_confirm_ok = False
                                            elif direction == 'short' and macd_short_require_negative and macd_histogram >= 0:
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MACD过滤：做空时MACD柱状图非负 (MACD={macd_histogram:.4f})，已过滤"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                                trend_confirm_ok = False
                                            else:
                                                logger.debug(f"{symbol} [{buy_timeframe}]: ✅ MACD过滤通过 (MACD={macd_histogram:.4f})")
                                    
                            # 检查 KDJ 过滤（预判信号和持续趋势信号跳过此过滤）
                            if trend_confirm_ok and kdj_filter_enabled and not is_early_entry_signal and not is_sustained_signal:
                                kdj_k = float(buy_indicator.get('kdj_k')) if buy_indicator.get('kdj_k') else None
                                if kdj_k is not None:
                                    ema_diff_pct_abs = abs(curr_diff_pct) if curr_diff_pct is not None else 0
                                    is_strong_signal = kdj_allow_strong_signal and ema_diff_pct_abs >= kdj_strong_signal_threshold
                                            
                                    if direction == 'long' and kdj_k > kdj_long_max_k:
                                        if not is_strong_signal:
                                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ KDJ过滤：做多时KDJ K值过高 (K={kdj_k:.2f} > {kdj_long_max_k})，已过滤"
                                            debug_info.append(msg)
                                            logger.info(f"{symbol} {msg}")
                                            trend_confirm_ok = False
                                    elif direction == 'short' and kdj_k < kdj_short_min_k:
                                        if not is_strong_signal:
                                            msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ KDJ过滤：做空时KDJ K值过低 (K={kdj_k:.2f} < {kdj_short_min_k})，已过滤"
                                            debug_info.append(msg)
                                            logger.info(f"{symbol} {msg}")
                                            trend_confirm_ok = False
                                    else:
                                        logger.debug(f"{symbol} [{buy_timeframe}]: ✅ KDJ过滤通过 (K={kdj_k:.2f})")
                                    
                            # 检查 MA10/EMA10 信号强度（预判信号和持续趋势信号跳过此过滤，因为已在信号检测阶段检查过）
                            if trend_confirm_ok and not is_early_entry_signal and not is_sustained_signal:
                                ma10_ema10_ok = True
                                if ma10 and ema10:
                                    if min_ma10_cross_strength > 0:
                                        ma10_ema10_strength_pct = abs(ma10_ema10_diff / ma10 * 100) if ma10 > 0 else 0
                                        if ma10_ema10_strength_pct < min_ma10_cross_strength:
                                            debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MA10/EMA10信号强度不足 (差值={ma10_ema10_strength_pct:.2f}%, 需要≥{min_ma10_cross_strength:.2f}%)，已过滤")
                                            trend_confirm_ok = False
                                        else:
                                            # 信号强度通过，检查趋势过滤
                                            if ma10_ema10_trend_filter:
                                                if direction == 'long':
                                                    ma10_ema10_ok = ema10 > ma10
                                                else:
                                                    ma10_ema10_ok = ema10 < ma10
                                                if not ma10_ema10_ok:
                                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ MA10/EMA10不同向")
                                                    trend_confirm_ok = False
                                else:
                                    if min_ma10_cross_strength > 0 or ma10_ema10_trend_filter:
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 缺少 MA10/EMA10 数据，跳过检查")
                                    
                            # 检查趋势持续性（预判信号和持续趋势信号跳过此检查）
                            # 注意：当只检测当前K线穿越时，trend_confirm_bars > 1 的配置将导致信号永远不会触发
                            # 因为交叉刚发生，无法满足"持续N根K线"的要求
                            # 如果需要趋势确认功能，建议设置 trend_confirm_bars = 0 或 1
                            # 持续趋势信号已经在信号检测阶段确认了趋势持续性，不需要再检查
                            if trend_confirm_ok and trend_confirm_bars > 0 and not is_early_entry_signal and not is_sustained_signal:
                                # 找到金叉/死叉发生的索引位置（根据交易方向）
                                cross_index = None
                                for check_lookback in range(1, min(4, current_buy_index + 1)):
                                    check_prev_index = current_buy_index - check_lookback
                                    if check_prev_index >= 0 and check_prev_index < len(buy_indicator_pairs):
                                        check_prev_pair = buy_indicator_pairs[check_prev_index]
                                        check_prev_indicator = check_prev_pair['indicator']
                                        check_prev_ema_short = float(check_prev_indicator.get('ema_short', 0)) if check_prev_indicator.get('ema_short') else None
                                        check_prev_ema_long = float(check_prev_indicator.get('ema_long', 0)) if check_prev_indicator.get('ema_long') else None

                                        if buy_signal in ['ema_5m', 'ema_15m', 'ema_1h']:
                                            if check_prev_ema_short and check_prev_ema_long and ema_short and ema_long:
                                                if direction == 'long':
                                                    # 检查是否在当前K线发生金叉（做多）
                                                    is_cross_now = (check_prev_ema_short <= check_prev_ema_long and ema_short > ema_long) or \
                                                                  (check_prev_ema_short < check_prev_ema_long and ema_short >= ema_long)
                                                else:
                                                    # 检查是否在当前K线发生死叉（做空）
                                                    is_cross_now = (check_prev_ema_short >= check_prev_ema_long and ema_short < ema_long) or \
                                                                  (check_prev_ema_short > check_prev_ema_long and ema_short <= ema_long)
                                                if is_cross_now:
                                                    cross_index = current_buy_index
                                                    break
                                        elif buy_signal == 'ma_ema10':
                                            check_prev_ma10 = float(check_prev_indicator.get('ma10', 0)) if check_prev_indicator.get('ma10') else None
                                            check_prev_ema10 = float(check_prev_indicator.get('ema10', 0)) if check_prev_indicator.get('ema10') else None
                                            if check_prev_ma10 and check_prev_ema10 and ma10 and ema10:
                                                if direction == 'long':
                                                    # 检查是否在当前K线发生金叉（做多）
                                                    is_cross_now = (check_prev_ema10 <= check_prev_ma10 and ema10 > ma10) or \
                                                                  (check_prev_ema10 < check_prev_ma10 and ema10 >= ma10)
                                                else:
                                                    # 检查是否在当前K线发生死叉（做空）
                                                    is_cross_now = (check_prev_ema10 >= check_prev_ma10 and ema10 < ma10) or \
                                                                  (check_prev_ema10 > check_prev_ma10 and ema10 <= ma10)
                                                if is_cross_now:
                                                    cross_index = current_buy_index
                                                    break

                                if cross_index is not None:
                                    # 如果交叉发生在当前K线，且trend_confirm_bars=1，则当前K线已经满足条件（1根K线确认）
                                    # 如果交叉发生在之前的K线，需要检查是否持续了足够的K线数
                                    bars_since_cross = current_buy_index - cross_index

                                    # 如果交叉发生在当前K线，bars_since_cross=0，但当前K线本身就算1根，所以需要 >= (trend_confirm_bars - 1)
                                    # 如果交叉发生在之前的K线，需要 >= trend_confirm_bars
                                    required_bars = trend_confirm_bars - 1 if cross_index == current_buy_index else trend_confirm_bars

                                    if bars_since_cross >= required_bars:
                                        # 检查从交叉点到当前的所有K线，趋势是否一直维持
                                        trend_maintained = True
                                        ema_strength_ok = True

                                        for check_index in range(cross_index, current_buy_index + 1):
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
                                        # 交叉刚发生，还需要等待更多K线
                                        trend_confirm_ok = False
                                        wait_bars = required_bars - bars_since_cross
                                        cross_type = "金叉" if direction == 'long' else "死叉"
                                        debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 趋势确认中，{cross_type}发生在索引{cross_index}，当前索引{current_buy_index}，已过{bars_since_cross}根K线，需要等待{wait_bars}根K线（共需{trend_confirm_bars}根）")
                                else:
                                    # 未找到交叉点，可能是信号触发逻辑有问题
                                    trend_confirm_ok = False
                                    cross_type = "金叉" if direction == 'long' else "死叉"
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 未找到{cross_type}位置，无法进行趋势确认")
                                                                    
                            if not trend_confirm_ok:
                                # 趋势确认失败，跳过买入
                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ❌ 趋势确认/过滤检查未通过，跳过买入"
                                debug_info.append(msg)
                                logger.info(f"{symbol} {msg}")
                            else:
                                # 添加调试信息：所有检查都通过，准备买入
                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ✅ 所有买入条件检查通过，准备执行买入操作")

                                # 计算入场价格（使用实时价格）
                                entry_price = None
                                can_execute = False

                                if direction == 'long':
                                    if long_price_type == 'market':
                                        entry_price = realtime_price
                                        can_execute = True
                                    elif long_price_type == 'market_minus_0_2':
                                        entry_price = realtime_price * 0.998
                                        can_execute = True
                                    elif long_price_type == 'market_minus_0_4':
                                        entry_price = realtime_price * 0.996
                                        can_execute = True
                                    elif long_price_type == 'market_minus_0_6':
                                        entry_price = realtime_price * 0.994
                                        can_execute = True
                                    elif long_price_type == 'market_minus_0_8':
                                        entry_price = realtime_price * 0.992
                                        can_execute = True
                                    elif long_price_type == 'market_minus_1':
                                        entry_price = realtime_price * 0.99
                                        can_execute = True
                                elif direction == 'short':
                                    if short_price_type == 'market':
                                        entry_price = realtime_price
                                        can_execute = True
                                    elif short_price_type == 'market_plus_0_2':
                                        entry_price = realtime_price * 1.002
                                        can_execute = True
                                    elif short_price_type == 'market_plus_0_4':
                                        entry_price = realtime_price * 1.004
                                        can_execute = True
                                    elif short_price_type == 'market_plus_0_6':
                                        entry_price = realtime_price * 1.006
                                        can_execute = True
                                    elif short_price_type == 'market_plus_0_8':
                                        entry_price = realtime_price * 1.008
                                        can_execute = True
                                    elif short_price_type == 'market_plus_1':
                                        entry_price = realtime_price * 1.01
                                        can_execute = True
                                        
                                if not can_execute or entry_price is None:
                                    # 无法执行，跳过
                                    debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ⚠️ 无法确定入场价格或执行条件不满足")
                                else:
                                    # 检查价格距离EMA限制（防止追高杀低）
                                    # 使用入场价格（限价单用限价，市价单用实时价格）来计算距离
                                    if price_distance_limit_enabled and entry_price and ema_short:
                                        entry_ema_distance_pct = ((entry_price - ema_short) / ema_short) * 100
                                        is_limit_order = (direction == 'long' and long_price_type != 'market') or \
                                                        (direction == 'short' and short_price_type != 'market')
                                        order_type_text = "限价" if is_limit_order else "市价"

                                        if direction == 'long':
                                            # 做多时，检查入场价格是否高于EMA9太多
                                            if entry_ema_distance_pct > price_distance_max_above_ema:
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 价格距离EMA过远，跳过做多 | {order_type_text}入场价={entry_price:.4f}, EMA9={ema_short:.4f}, 偏离={entry_ema_distance_pct:+.2f}% > {price_distance_max_above_ema}%（等待回调）"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                                can_execute = False
                                            else:
                                                logger.debug(f"{symbol} [{buy_timeframe}]: ✅ 价格距离EMA检查通过 ({order_type_text}入场价偏离={entry_ema_distance_pct:+.2f}%)")
                                        else:  # direction == 'short'
                                            # 做空时，检查入场价格是否低于EMA9太多
                                            if entry_ema_distance_pct < -price_distance_max_below_ema:
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 价格距离EMA过远，跳过做空 | {order_type_text}入场价={entry_price:.4f}, EMA9={ema_short:.4f}, 偏离={entry_ema_distance_pct:+.2f}% < -{price_distance_max_below_ema}%（等待反弹）"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                                can_execute = False
                                            else:
                                                logger.debug(f"{symbol} [{buy_timeframe}]: ✅ 价格距离EMA检查通过 ({order_type_text}入场价偏离={entry_ema_distance_pct:+.2f}%)")

                                if not can_execute:
                                    # 价格距离检查未通过或其他原因无法执行
                                    pass
                                else:
                                    # 计算仓位大小
                                    # 从数据库获取账户余额
                                    connection_balance = None
                                    cursor_balance = None
                                    try:
                                        connection_balance = self._get_connection()
                                        cursor_balance = connection_balance.cursor(pymysql.cursors.DictCursor)
                                        cursor_balance.execute(
                                            "SELECT total_equity, current_balance, frozen_balance FROM paper_trading_accounts WHERE id = %s",
                                            (account_id,)
                                        )
                                        account = cursor_balance.fetchone()

                                        if account:
                                            # 优先使用 total_equity，如果没有则使用 current_balance + frozen_balance
                                            if account.get('total_equity') is not None:
                                                account_equity = float(account['total_equity'])
                                            elif account.get('current_balance') is not None:
                                                frozen = float(account.get('frozen_balance', 0) or 0)
                                                account_equity = float(account['current_balance']) + frozen
                                            else:
                                                account_equity = 10000.0
                                        else:
                                            account_equity = 10000.0
                                    except Exception as e:
                                        # logger.warning(f"获取账户余额失败: {e}，使用默认值10000")
                                        account_equity = 10000.0
                                    finally:
                                        # 确保数据库连接正确释放
                                        if cursor_balance:
                                            try:
                                                cursor_balance.close()
                                            except:
                                                pass
                                        if connection_balance:
                                            try:
                                                connection_balance.close()
                                            except:
                                                pass

                                    position_value = account_equity * (position_size / 100)
                                    quantity = (position_value * leverage) / entry_price
                                    quantity = self.round_quantity(quantity, symbol)
                                            
                                    if quantity > 0:
                                        # 计算开仓手续费（预估）
                                        open_fee = (entry_price * quantity) * fee_rate
                                                
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
                                                
                                        # 使用 futures_engine 执行真实开仓（使用实时价格）
                                        position_side = 'LONG' if direction == 'long' else 'SHORT'
                                        quantity_decimal = Decimal(str(quantity))
                                        entry_price_decimal = Decimal(str(entry_price))
                                                
                                        # 判断是否使用限价单：做多时检查long_price_type，做空时检查short_price_type
                                        use_limit_price = False
                                        if direction == 'long' and long_price_type != 'market':
                                            use_limit_price = True
                                        elif direction == 'short' and short_price_type != 'market':
                                            use_limit_price = True

                                        # 添加开仓调试日志
                                        market_label = "[实盘]" if market_type == 'live' else "[模拟]"
                                        logger.info(f"🔔 {market_label} {symbol} 准备开仓: 方向={direction}, 实时价格={realtime_price:.4f}, 入场价格={entry_price:.4f}, 使用限价={use_limit_price}")

                                        # ========== 开仓前再次检查（防止并发重复开仓） ==========
                                        # 由于策略执行器可能被并发调用，需要在真正开仓前再次检查
                                        # 1. 检查当前K线内是否已有交易记录
                                        # 2. 检查当前是否已有持仓（针对该交易对）
                                        # 3. 检查是否已有待成交的限价单（针对该交易对和策略）
                                        skip_open = False
                                        try:
                                            check_conn = self._get_connection()
                                            check_cursor = check_conn.cursor(pymysql.cursors.DictCursor)

                                            # 检查当前K线内是否已有开仓记录
                                            check_cursor.execute("""
                                                SELECT COUNT(*) as cnt FROM strategy_trade_records
                                                WHERE symbol = %s AND strategy_id = %s AND action = 'BUY'
                                                AND trade_time >= %s AND trade_time < %s
                                            """, (symbol, strategy_id, current_kline_time, current_kline_time + timedelta(minutes=timeframe_minutes)))
                                            trade_count = check_cursor.fetchone()['cnt']

                                            # 检查当前是否已有该交易对的持仓
                                            # 注意：futures_positions 表没有 strategy_id 列，只检查交易对级别的持仓
                                            check_cursor.execute("""
                                                SELECT COUNT(*) as cnt FROM futures_positions
                                                WHERE account_id = %s AND symbol = %s AND status = 'open'
                                            """, (account_id, symbol))
                                            position_count = check_cursor.fetchone()['cnt']

                                            # 检查是否已有待成交的限价单（防止重复下限价单）
                                            check_cursor.execute("""
                                                SELECT COUNT(*) as cnt FROM futures_orders
                                                WHERE account_id = %s AND symbol = %s AND status = 'PENDING'
                                                AND strategy_id = %s
                                            """, (account_id, symbol, strategy_id))
                                            pending_order_count = check_cursor.fetchone()['cnt']

                                            check_cursor.close()
                                            check_conn.close()

                                            if trade_count > 0:
                                                skip_open = True
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 开仓前检查发现当前K线已有{trade_count}条交易记录，跳过开仓"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                            elif position_count > 0 and prevent_duplicate_entry:
                                                skip_open = True
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 开仓前检查发现已有{position_count}个持仓，跳过开仓"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                            elif pending_order_count > 0:
                                                skip_open = True
                                                msg = f"{current_time_local.strftime('%Y-%m-%d %H:%M')} [{buy_timeframe}]: ⚠️ 开仓前检查发现已有{pending_order_count}个待成交限价单，跳过开仓"
                                                debug_info.append(msg)
                                                logger.info(f"{symbol} {msg}")
                                        except Exception as e:
                                            logger.warning(f"{symbol} 开仓前检查失败: {e}")
                                        # ========== 开仓前检查结束 ==========

                                        if not skip_open:
                                            open_result = trading_engine.open_position(
                                                account_id=account_id,
                                                symbol=symbol,
                                                position_side=position_side,
                                                quantity=quantity_decimal,
                                                leverage=leverage,
                                                limit_price=entry_price_decimal if use_limit_price else None,
                                                stop_loss_pct=Decimal(str(stop_loss_pct)) if stop_loss_pct else None,
                                                take_profit_pct=Decimal(str(take_profit_pct)) if take_profit_pct else None,
                                                source='strategy',
                                                signal_id=None,
                                                strategy_id=strategy_id
                                            )

                                            if open_result.get('success'):
                                                position_id = open_result.get('position_id')
                                                order_id = open_result.get('order_id')
                                                actual_entry_price = float(open_result.get('entry_price', entry_price))
                                                actual_quantity = float(open_result.get('quantity', quantity))
                                                actual_fee = float(open_result.get('fee', open_fee))
                                                actual_margin = float(open_result.get('margin', (actual_entry_price * actual_quantity) / leverage))

                                                # 从开仓结果中获取余额信息（futures_engine 已返回）
                                                balance_before = open_result.get('balance_before')
                                                balance_after = open_result.get('balance_after')
                                                frozen_before = open_result.get('frozen_before')
                                                frozen_after = open_result.get('frozen_after')
                                                available_before = open_result.get('available_before')
                                                available_after = open_result.get('available_after')

                                                # 保存交易记录到数据库
                                                self._save_trade_record(
                                                    symbol=symbol,
                                                    action='BUY',
                                                    direction=direction,
                                                    entry_price=actual_entry_price,
                                                    exit_price=None,
                                                    quantity=actual_quantity,
                                                    leverage=leverage,
                                                    fee=actual_fee,
                                                    realized_pnl=None,
                                                    strategy_id=strategy_id,
                                                    strategy_name=strategy_name,
                                                    account_id=account_id,
                                                    reason='买入信号触发',
                                                    trade_time=current_time_local,
                                                    position_id=position_id,
                                                    order_id=order_id
                                                )

                                                # 记录冻结保证金
                                                if actual_margin > 0:
                                                    self._save_capital_record(
                                                        symbol=symbol,
                                                        change_type='FROZEN',
                                                        amount_change=-actual_margin,  # 负数表示减少可用余额
                                                        balance_before=balance_before,
                                                        balance_after=balance_after,
                                                        frozen_before=frozen_before,
                                                        frozen_after=frozen_after,
                                                        available_before=available_before,
                                                        available_after=available_after,
                                                        strategy_id=strategy_id,
                                                        strategy_name=strategy_name,
                                                        account_id=account_id,
                                                        action='BUY',
                                                        direction=direction,
                                                        entry_price=actual_entry_price,
                                                        quantity=actual_quantity,
                                                        leverage=leverage,
                                                        margin=actual_margin,
                                                        reason='开仓冻结保证金',
                                                        position_id=position_id,
                                                        order_id=order_id,
                                                        change_time=current_time_local
                                                    )

                                                # 记录开仓手续费
                                                if actual_fee > 0:
                                                    self._save_capital_record(
                                                        symbol=symbol,
                                                        change_type='FEE',
                                                        amount_change=-actual_fee,  # 负数表示减少余额
                                                        balance_before=balance_after,  # 使用冻结后的余额
                                                        balance_after=balance_after - actual_fee if balance_after else None,
                                                        frozen_before=frozen_after,
                                                        frozen_after=frozen_after,
                                                        available_before=available_after,
                                                        available_after=available_after - actual_fee if available_after else None,
                                                        strategy_id=strategy_id,
                                                        strategy_name=strategy_name,
                                                        account_id=account_id,
                                                        action='BUY',
                                                        direction=direction,
                                                        entry_price=actual_entry_price,
                                                        quantity=actual_quantity,
                                                        leverage=leverage,
                                                        fee=actual_fee,
                                                        reason='开仓手续费',
                                                        position_id=position_id,
                                                        order_id=order_id,
                                                        change_time=current_time_local
                                                    )

                                                # 添加到模拟持仓列表（用于后续卖出逻辑）
                                                position = {
                                                    'position_id': position_id,
                                                    'direction': direction,
                                                    'entry_price': actual_entry_price,
                                                    'quantity': actual_quantity,
                                                    'entry_time': current_time,
                                                    'entry_time_local': current_time_local,
                                                    'leverage': leverage,
                                                    'open_fee': actual_fee,
                                                    'stop_loss_price': stop_loss_price,
                                                    'take_profit_price': take_profit_price
                                                }
                                                positions.append(position)

                                                direction_text = "做多" if direction == 'long' else "做空"
                                                qty_precision = self.get_quantity_precision(symbol)
                                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ✅ 买入{direction_text}，价格={actual_entry_price:.4f}，数量={actual_quantity:.{qty_precision}f}，开仓手续费={actual_fee:.4f}，持仓ID={position_id}")
                                            else:
                                                error_msg = open_result.get('message', '未知错误')
                                                debug_info.append(f"{current_time_local.strftime('%Y-%m-%d %H:%M')}: ❌ 开仓失败: {error_msg}")
                                                logger.error(f"{symbol} 开仓失败: {error_msg}")
        
        # 实时运行：不再强制平仓，让策略自然执行
        # 统计信号检测情况
        golden_cross_count = len([info for info in debug_info if '金叉' in info])
        death_cross_count = len([info for info in debug_info if '死叉' in info])
        
        return {
                                                    'symbol': symbol,
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
            'buy_volume_short': buy_volume_short
        }
    
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
        """从数据库加载启用的策略"""
        try:
            connection = self._get_connection()
            cursor = connection.cursor(pymysql.cursors.DictCursor)
            
            try:
                # 从 trading_strategies 表加载启用的策略
                cursor.execute("""
                    SELECT * FROM trading_strategies 
                    WHERE enabled = 1
                    ORDER BY id ASC
                """)
                strategies = cursor.fetchall()
                
                # logger.debug(f"从数据库加载到 {len(strategies)} 个启用的策略")
                
                # 将数据库记录转换为策略配置字典
                result = []
                for strategy in strategies:
                    try:
                        import json
                        # 解析策略配置JSON
                        config = json.loads(strategy.get('config', '{}')) if strategy.get('config') else {}
                        strategy_dict = {
                            'id': strategy.get('id'),
                            'name': strategy.get('name', '未命名策略'),
                            'account_id': strategy.get('account_id', 2),
                            'enabled': strategy.get('enabled', 0),
                            'market_type': strategy.get('market_type', 'test'),  # 市场类型: test/live
                            'adaptiveRegime': strategy.get('adaptive_regime', False),  # 行情自适应开关
                            **config  # 合并配置
                        }
                        result.append(strategy_dict)
                        # logger.debug(f"  策略: {strategy_dict['name']} (ID: {strategy_dict['id']}, 账户: {strategy_dict['account_id']})")
                    except Exception as e:
                        logger.error(f"解析策略配置失败 (ID: {strategy.get('id')}): {e}")
                        continue
                
                return result
            finally:
                cursor.close()
                connection.close()
        except Exception as e:
            logger.error(f"加载策略失败: {e}", exc_info=True)
            return []
    
    async def check_and_execute_strategies(self):
        """检查并执行所有启用的策略"""
        # 如果 running 为 False，设置为 True（允许外部调用）
        if not self.running:
            self.running = True
            # logger.debug("策略执行器状态已激活")
        
        try:
            # 加载启用的策略
            strategies = self._load_strategies()
            
            logger.info(f"已加载 {len(strategies)} 个启用策略")

            if not strategies:
                # logger.debug("没有启用的策略需要执行")
                return
            
            # logger.info(f"📊 检查到 {len(strategies)} 个启用的策略，开始执行...")
            
            # 执行每个策略
            for strategy in strategies:
                try:
                    account_id = strategy.get('account_id', 2)
                    strategy_name = strategy.get('name', '未知策略')
                    strategy_id = strategy.get('id', '未知ID')
                    # logger.info(f"🔄 执行策略: {strategy_name} (ID: {strategy_id}, 账户: {account_id})")
                    await self.execute_strategy(strategy, account_id=account_id)
                    # logger.debug(f"✓ 策略 {strategy_name} 执行完成")
                except Exception as e:
                    logger.error(f"❌ 执行策略失败 (ID: {strategy.get('id')}, 名称: {strategy.get('name')}): {e}", exc_info=True)
                    continue
                    
            # logger.debug(f"✓ 所有策略检查完成（共 {len(strategies)} 个）")
        except Exception as e:
            logger.error(f"❌ 检查策略时出错: {e}", exc_info=True)
    
    async def run_loop(self, interval: int = 5):
        """
        运行策略执行循环
        
        Args:
            interval: 检查间隔（秒），默认5秒
        """
        self.running = True
        # logger.info(f"🔄 策略自动执行服务已启动（间隔: {interval}秒）")
        
        try:
            while self.running:
                try:
                    await self.check_and_execute_strategies()
                except Exception as e:
                    logger.error(f"策略执行循环出错: {e}", exc_info=True)
                
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            # logger.info("策略执行服务已取消")
            raise
        except Exception as e:
            logger.error(f"策略执行循环异常退出: {e}", exc_info=True)
        finally:
            self.running = False
    
    def start(self, interval: int = 30):
        """
        启动后台任务
        
        Args:
            interval: 检查间隔（秒），默认30秒
        """
        if self.running:
            # logger.warning("策略执行器已在运行")
            return
        
        # 获取或创建事件循环
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        self.task = loop.create_task(self.run_loop(interval))
        # logger.info(f"策略执行器已启动（间隔: {interval}秒）")
    
    def stop(self):
        """停止后台任务"""
        self.running = False
        if self.task and not self.task.done():
            self.task.cancel()
            # logger.info("⏹️  策略自动执行服务已停止")

