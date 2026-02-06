#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能自动交易服务 - 生产环境版本
直接在服务器后台运行
"""

import time
import sys
import os
import asyncio
from datetime import datetime, time as dt_time, timezone, timedelta
from decimal import Decimal
from loguru import logger
import pymysql
from dotenv import load_dotenv

# 导入 WebSocket 价格服务
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.binance_ws_price import get_ws_price_service, BinanceWSPriceService
from app.services.adaptive_optimizer import AdaptiveOptimizer
from app.services.optimization_config import OptimizationConfig
from app.services.symbol_rating_manager import SymbolRatingManager
from app.services.volatility_profile_updater import VolatilityProfileUpdater
from app.services.smart_entry_executor import SmartEntryExecutor
from app.services.smart_exit_optimizer import SmartExitOptimizer
from app.services.big4_trend_detector import Big4TrendDetector
from app.strategies.range_market_detector import RangeMarketDetector
from app.strategies.bollinger_mean_reversion import BollingerMeanReversionStrategy
from app.strategies.range_reversal_strategy import RangeReversalStrategy
from app.strategies.mode_switcher import TradingModeSwitcher
from app.strategies.safe_mode_switcher import SafeModeSwitcher

# 加载环境变量
load_dotenv()

# 配置日志
logger.remove()
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
    level="INFO"
)
logger.add(
    "logs/smart_trader_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
    level="INFO"
)


class SmartDecisionBrain:
    """智能决策大脑 - 内嵌版本"""

    def __init__(self, db_config: dict, trader_service=None):
        self.db_config = db_config
        self.connection = None
        self.trader_service = trader_service  # 🔥 持有trader_service引用用于紧急平仓

        # 从config.yaml加载配置
        self._load_config()

        self.threshold = 35  # 开仓阈值 (提高到35分,过滤低质量信号,防追高)

        # 🔥 紧急干预标志 - 底部/顶部反转时触发
        self.emergency_bottom_reversal_time = None  # 底部反转触发时间
        self.emergency_top_reversal_time = None     # 顶部反转触发时间
        self.emergency_block_duration_hours = 4     # 紧急干预持续时间(小时)

        # 🔥 紧急干预标志 - 总亏损超过阈值时触发
        self.emergency_loss_limit_time = None       # 总亏损触发时间
        self.emergency_loss_threshold = 600         # 总亏损阈值(USDT)
        self.emergency_loss_block_hours = 2         # 总亏损干预持续时间(小时)

        # 🔥 紧急熔断标志 - 连续止损过多时触发
        self.emergency_stop_loss_circuit_time = None  # 止损熔断触发时间
        self.circuit_check_recent_trades = 10         # 检查最近N笔交易
        self.circuit_stop_loss_threshold = 5          # 止损笔数阈值
        self.circuit_block_hours = 2                  # 熔断持续时间(小时)

    def _load_config(self):
        """从数据库加载黑名单和自适应参数,从config.yaml加载交易对列表"""
        try:
            import yaml

            # 1. 从config.yaml加载交易对列表
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                all_symbols = config.get('symbols', [])

            # 2. 从数据库加载黑名单
            conn = self._get_connection()
            cursor = conn.cursor()

            # 从 trading_symbol_rating 加载黑名单
            # Level 3 = 永久禁止交易
            cursor.execute("""
                SELECT symbol, rating_level, margin_multiplier
                FROM trading_symbol_rating
                WHERE rating_level >= 1
                ORDER BY rating_level DESC, created_at DESC
            """)
            blacklist_rows = cursor.fetchall()
            # Level 3 完全禁止交易
            self.blacklist = [row['symbol'] for row in blacklist_rows if row['rating_level'] == 3]

            # 3. 从数据库加载自适应参数
            cursor.execute("""
                SELECT param_key, param_value
                FROM adaptive_params
                WHERE param_type = 'long'
            """)
            long_params = {row['param_key']: float(row['param_value']) for row in cursor.fetchall()}

            cursor.execute("""
                SELECT param_key, param_value
                FROM adaptive_params
                WHERE param_type = 'short'
            """)
            short_params = {row['param_key']: float(row['param_value']) for row in cursor.fetchall()}

            cursor.close()

            # 4. 构建自适应参数字典
            self.adaptive_long = {
                'stop_loss_pct': long_params.get('long_stop_loss_pct', 0.03),
                'take_profit_pct': long_params.get('long_take_profit_pct', 0.02),
                'min_holding_minutes': long_params.get('long_min_holding_minutes', 60),
                'position_size_multiplier': long_params.get('long_position_size_multiplier', 1.0)
            }

            self.adaptive_short = {
                'stop_loss_pct': short_params.get('short_stop_loss_pct', 0.03),
                'take_profit_pct': short_params.get('short_take_profit_pct', 0.02),
                'min_holding_minutes': short_params.get('short_min_holding_minutes', 60),
                'position_size_multiplier': short_params.get('short_position_size_multiplier', 1.0)
            }

            # 5. 从数据库加载信号黑名单
            self.signal_blacklist = {}
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT signal_type, position_side
                    FROM signal_blacklist
                    WHERE is_active = TRUE
                """)
                signal_blacklist_rows = cursor.fetchall()
                for row in signal_blacklist_rows:
                    key = f"{row['signal_type']}_{row['position_side']}"
                    self.signal_blacklist[key] = True
                cursor.close()
            except:
                # 如果表不存在，使用空字典
                self.signal_blacklist = {}

            # 6. 所有交易对都可以交易（不过滤黑名单）
            self.whitelist = all_symbols

            logger.info(f"✅ 从数据库加载配置:")
            logger.info(f"   总交易对: {len(all_symbols)}")
            logger.info(f"   数据库黑名单: {len(self.blacklist)} 个 (使用100U小仓位)")
            logger.info(f"   可交易: {len(self.whitelist)} 个")
            logger.info(f"   📊 自适应参数 (从数据库):")
            logger.info(f"      LONG止损: {self.adaptive_long['stop_loss_pct']*100:.1f}%, 止盈: {self.adaptive_long['take_profit_pct']*100:.1f}%, 最小持仓: {self.adaptive_long['min_holding_minutes']:.0f}分钟, 仓位倍数: {self.adaptive_long['position_size_multiplier']:.1f}")
            logger.info(f"      SHORT止损: {self.adaptive_short['stop_loss_pct']*100:.1f}%, 止盈: {self.adaptive_short['take_profit_pct']*100:.1f}%, 最小持仓: {self.adaptive_short['min_holding_minutes']:.0f}分钟, 仓位倍数: {self.adaptive_short['position_size_multiplier']:.1f}")

            if self.blacklist:
                logger.info(f"   ⚠️  黑名单交易对(小仓位): {', '.join(self.blacklist)}")

            if self.signal_blacklist:
                logger.info(f"   🚫 禁用信号: {len(self.signal_blacklist)} 个")

            # 7. 从数据库加载评分权重
            self.scoring_weights = {}
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT signal_component, weight_long, weight_short
                    FROM signal_scoring_weights
                    WHERE is_active = TRUE
                """)
                weight_rows = cursor.fetchall()
                for row in weight_rows:
                    self.scoring_weights[row['signal_component']] = {
                        'long': float(row['weight_long']),
                        'short': float(row['weight_short'])
                    }
                cursor.close()

                if self.scoring_weights:
                    logger.info(f"   📊 评分权重: 从数据库加载 {len(self.scoring_weights)} 个组件")
            except:
                # 如果表不存在，使用默认权重（硬编码）
                self.scoring_weights = {
                    'position_low': {'long': 20, 'short': 0},
                    'position_mid': {'long': 10, 'short': 5},             # 提高LONG评分，中位上涨是好信号
                    'position_high': {'long': 0, 'short': 20},
                    'momentum_down_3pct': {'long': 0, 'short': 15},       # 恢复: 24H跌>3%是强信号
                    'momentum_up_3pct': {'long': 15, 'short': 0},         # 恢复: 24H涨>3%是强信号
                    'trend_1h_bull': {'long': 20, 'short': 0},
                    'trend_1h_bear': {'long': 0, 'short': 20},
                    'volatility_high': {'long': 10, 'short': 10},
                    'consecutive_bull': {'long': 15, 'short': 0},
                    'consecutive_bear': {'long': 0, 'short': 15},
                    'volume_power_bull': {'long': 25, 'short': 0},        # 1H+15M量能多头
                    'volume_power_bear': {'long': 0, 'short': 25},        # 1H+15M量能空头
                    'volume_power_1h_bull': {'long': 15, 'short': 0},     # 仅1H量能多头
                    'volume_power_1h_bear': {'long': 0, 'short': 15},     # 仅1H量能空头
                    'breakout_long': {'long': 20, 'short': 0},            # 高位突破追涨
                    'breakdown_short': {'long': 0, 'short': 20}           # 低位破位追空
                    # 已移除: ema_bull, ema_bear (Big4市场趋势判断已足够)
                }
                logger.info(f"   📊 评分权重: 使用默认权重")

        except Exception as e:
            logger.error(f"读取数据库配置失败: {e}, 使用默认配置")
            self.whitelist = [
                'BCH/USDT', 'LDO/USDT', 'ENA/USDT', 'WIF/USDT', 'TAO/USDT',
                'DASH/USDT', 'ETC/USDT', 'VIRTUAL/USDT', 'NEAR/USDT',
                'AAVE/USDT', 'SUI/USDT', 'UNI/USDT', 'ADA/USDT', 'SOL/USDT'
            ]
            self.blacklist = []
            self.adaptive_long = {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02, 'min_holding_minutes': 60, 'position_size_multiplier': 1.0}
            self.adaptive_short = {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02, 'min_holding_minutes': 60, 'position_size_multiplier': 1.0}
            self.scoring_weights = {
                # 🔥🔥🔥 强破位信号（最高优先级，单独触发）
                'breakout_strong': {'long': 50, 'short': 50},         # 24H强破位，追涨杀跌

                # 量能信号（辅助确认）
                'volume_power_bull': {'long': 20, 'short': 0},        # 1H+15M量能多头（权重降低）
                'volume_power_bear': {'long': 0, 'short': 20},        # 1H+15M量能空头（权重降低）
                'volume_power_1h_bull': {'long': 15, 'short': 0},     # 仅1H量能多头
                'volume_power_1h_bear': {'long': 0, 'short': 15},     # 仅1H量能空头

                # 趋势信号
                'trend_1h_bull': {'long': 15, 'short': 0},            # 权重降低
                'trend_1h_bear': {'long': 0, 'short': 15},            # 权重降低
                'consecutive_bull': {'long': 15, 'short': 0},
                'consecutive_bear': {'long': 0, 'short': 15},

                # 位置信号（辅助）
                'position_low': {'long': 15, 'short': 0},             # 权重降低
                'position_mid': {'long': 5, 'short': 5},
                'position_high': {'long': 0, 'short': 15},            # 权重降低

                # 动量信号
                'momentum_down_3pct': {'long': 0, 'short': 10},
                'momentum_up_3pct': {'long': 10, 'short': 0},

                # 其他
                'volatility_high': {'long': 10, 'short': 10},

                # 已废弃的信号（保留兼容性）
                'breakout_long': {'long': 20, 'short': 0},            # 旧版突破信号
                'breakdown_short': {'long': 0, 'short': 20}           # 旧版破位信号
            }

    def reload_config(self):
        """重新加载配置 - 供外部调用"""
        logger.info("🔄 重新加载配置文件...")
        self._load_config()
        return len(self.whitelist)

    def _get_connection(self):
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    **self.db_config,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
        return self.connection

    def check_anti_fomo_filter(self, symbol: str, current_price: float, side: str) -> tuple:
        """
        防追高/追跌过滤器

        做多防追高: 不在24H区间80%以上位置开多
        做空防杀跌: 不在24H区间20%以下位置开空

        返回: (是否通过, 原因)
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查24H价格位置
            cursor.execute("""
                SELECT high_24h, low_24h, change_24h
                FROM price_stats_24h
                WHERE symbol = %s
            """, (symbol,))

            stats_24h = cursor.fetchone()
            cursor.close()

            if not stats_24h:
                return True, "无24H数据,跳过过滤"

            high_24h = float(stats_24h['high_24h'])
            low_24h = float(stats_24h['low_24h'])
            change_24h = float(stats_24h['change_24h'] or 0)

            # 计算价格在24H区间的位置百分比
            if high_24h > low_24h:
                position_pct = (current_price - low_24h) / (high_24h - low_24h) * 100
            else:
                position_pct = 50  # 无波动时默认中间位置

            # 防FOMO过滤器已全部禁用（用户要求：市场本来就是要追涨杀跌的）
            # 做多防追高: 已禁用
            # if side == 'LONG' and position_pct > 80:
            #     return False, f"防追高-价格位于24H区间{position_pct:.1f}%位置,距最高仅{(high_24h-current_price)/current_price*100:.2f}%"

            # 做空防杀跌: 已禁用
            # if side == 'SHORT' and position_pct < 20:
            #     return False, f"防杀跌-价格位于24H区间{position_pct:.1f}%位置,距最低仅{(current_price-low_24h)/current_price*100:.2f}%"

            # 额外检查: 24H大涨且在高位 → 已禁用
            # if side == 'LONG' and change_24h > 15 and position_pct > 70:
            #     return False, f"防追高-24H涨{change_24h:+.2f}%且位于{position_pct:.1f}%高位"

            # 额外检查: 24H大跌且在低位 → 已禁用
            # if side == 'SHORT' and change_24h < -15 and position_pct < 30:
            #     return False, f"防杀跌-24H跌{change_24h:+.2f}%且位于{position_pct:.1f}%低位"

            return True, f"位置{position_pct:.1f}%,24H{change_24h:+.2f}%"

        except Exception as e:
            logger.error(f"防追高检查失败 {symbol}: {e}")
            return True, "检查失败,放行"

    def detect_big4_bottom_reversal(self, side: str) -> tuple:
        """
        检测Big4同步触底反转 (底部反转保护)

        场景: 昨夜暴跌,Big4同步触底,但Big4趋势判断滞后,系统继续做空导致亏损

        核心逻辑:
        利用Big4的同步性判断市场底部,而不是等Big4的滞后趋势信号

        检测逻辑:
        1. 获取BTC/ETH/BNB/SOL最近4小时的15M K线
        2. 找每个币种的最低点位置和反弹幅度
        3. 检查4个币种是否同步触底(时间偏差≤2根K线=30分钟)
        4. 检查至少3个币种反弹≥3%
        5. 检查触底时间在4小时内
        6. 满足条件 → 阻止所有SHORT信号

        Args:
            side: 交易方向 ('LONG' or 'SHORT')

        Returns:
            (should_block, reason) - 是否应该阻止开仓, 原因
        """
        # 只对做空方向检查
        if side != 'SHORT':
            return False, None

        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            big4_symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']
            bottom_info = {}

            # 获取Big4每个币种的K线数据 (4小时范围)
            for symbol in big4_symbols:
                cursor.execute("""
                    SELECT open_time, open_price, high_price, low_price, close_price
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = '15m'
                    AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 4 HOUR)) * 1000
                    ORDER BY open_time DESC LIMIT 16
                """, (symbol,))

                klines = list(cursor.fetchall())

                if len(klines) < 6:  # 至少需要1.5小时数据
                    continue

                # 转换数据类型
                for k in klines:
                    k['open_time'] = int(k['open_time'])
                    k['low'] = float(k['low_price'])  # 🔥 修复: 数据库字段是low_price
                    k['close'] = float(k['close_price'])  # 🔥 修复: 数据库字段是close_price

                # 找最低点 (从旧到新,索引0=最早)
                klines.reverse()
                lows = [k['low'] for k in klines]
                min_low = min(lows)
                min_idx = lows.index(min_low)
                bottom_time = klines[min_idx]['open_time']
                current_price = klines[-1]['close']

                # 计算反弹幅度
                bounce_pct = (current_price - min_low) / min_low * 100

                bottom_info[symbol] = {
                    'min_idx': min_idx,  # 最低点在第几根K线(0=最早)
                    'min_low': min_low,
                    'bottom_time': bottom_time,  # 触底时间戳(毫秒)
                    'current': current_price,
                    'bounce_pct': bounce_pct
                }

            cursor.close()

            # 需要至少3个币种有数据
            if len(bottom_info) < 3:
                return False, None

            # 检查Big4是否同步触底
            min_indices = [info['min_idx'] for info in bottom_info.values()]
            bounces = [info['bounce_pct'] for info in bottom_info.values()]
            bottom_times = [info['bottom_time'] for info in bottom_info.values()]

            # 条件1: 最低点时间接近(最大差距≤2根K线=30分钟)
            time_spread = max(min_indices) - min(min_indices)
            time_sync = time_spread <= 2

            # 条件2: 至少3个币种反弹>=3%
            strong_bounce_count = sum(1 for b in bounces if b >= 3.0)

            # 条件3: 触底时间在2小时内 (使用最早触底时间)
            import time
            earliest_bottom = min(bottom_times)
            current_time_ms = int(time.time() * 1000)
            hours_since_bottom = (current_time_ms - earliest_bottom) / 1000 / 3600
            within_time_limit = hours_since_bottom <= 2.0

            if time_sync and strong_bounce_count >= 3 and within_time_limit:
                avg_bounce = sum(bounces) / len(bounces)
                details = ', '.join([
                    f"{sym.split('/')[0]}:{info['bounce_pct']:.1f}%"
                    for sym, info in bottom_info.items()
                ])

                reason = (f"Big4同步触底反转: 时间偏差{time_spread}根K线(≤30分钟), "
                         f"{strong_bounce_count}/4币种反弹≥3%, 平均反弹{avg_bounce:.1f}%, "
                         f"触底{hours_since_bottom:.1f}小时内 ({details})")

                logger.warning(f"🚫 [BIG4-BOTTOM] {reason}, 阻止做空")

                # 🔥 紧急干预: 立即平掉所有空单
                if self.trader_service:
                    self.trader_service._emergency_close_all_positions('SHORT', reason)
                    # 🔥 设置紧急干预标志,2小时内禁止开空单
                    import time
                    self.trader_service.emergency_bottom_reversal_time = time.time()
                else:
                    logger.error("❌ 无法执行紧急平仓: trader_service未设置")

                return True, reason

            return False, None

        except Exception as e:
            logger.error(f"[BIG4-BOTTOM-ERROR] Big4触底检测失败: {e}")
            return False, None  # 检测失败时不阻止,避免影响正常交易

    def detect_big4_top_reversal(self, side: str) -> tuple:
        """
        检测Big4同步见顶反转 (顶部反转保护)

        场景: 暴涨后,Big4同步见顶,但Big4趋势判断滞后,系统继续做多导致亏损

        核心逻辑:
        利用Big4的同步性判断市场顶部,而不是等Big4的滞后趋势信号

        检测逻辑:
        1. 获取BTC/ETH/BNB/SOL最近4小时的15M K线
        2. 找每个币种的最高点位置和回调幅度
        3. 检查4个币种是否同步见顶(时间偏差≤2根K线=30分钟)
        4. 检查至少3个币种回调≥3%
        5. 检查见顶时间在4小时内
        6. 满足条件 → 阻止所有LONG信号

        Args:
            side: 交易方向 ('LONG' or 'SHORT')

        Returns:
            (should_block, reason) - 是否应该阻止开仓, 原因
        """
        # 只对做多方向检查
        if side != 'LONG':
            return False, None

        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            big4_symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']
            top_info = {}

            # 获取Big4每个币种的K线数据 (4小时范围)
            for symbol in big4_symbols:
                cursor.execute("""
                    SELECT open_time, open_price, high_price, low_price, close_price
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = '15m'
                    AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 4 HOUR)) * 1000
                    ORDER BY open_time DESC LIMIT 16
                """, (symbol,))

                klines = list(cursor.fetchall())

                if len(klines) < 6:  # 至少需要1.5小时数据
                    continue

                # 转换数据类型
                for k in klines:
                    k['open_time'] = int(k['open_time'])
                    k['high'] = float(k['high_price'])
                    k['close'] = float(k['close_price'])

                # 找最高点 (从旧到新,索引0=最早)
                klines.reverse()
                highs = [k['high'] for k in klines]
                max_high = max(highs)
                max_idx = highs.index(max_high)
                top_time = klines[max_idx]['open_time']
                current_price = klines[-1]['close']

                # 计算回调幅度
                pullback_pct = (max_high - current_price) / max_high * 100

                top_info[symbol] = {
                    'max_idx': max_idx,  # 最高点在第几根K线(0=最早)
                    'max_high': max_high,
                    'top_time': top_time,  # 见顶时间戳(毫秒)
                    'current': current_price,
                    'pullback_pct': pullback_pct
                }

            cursor.close()

            # 需要至少3个币种有数据
            if len(top_info) < 3:
                return False, None

            # 检查Big4是否同步见顶
            max_indices = [info['max_idx'] for info in top_info.values()]
            pullbacks = [info['pullback_pct'] for info in top_info.values()]
            top_times = [info['top_time'] for info in top_info.values()]

            # 条件1: 最高点时间接近(最大差距≤2根K线=30分钟)
            time_spread = max(max_indices) - min(max_indices)
            time_sync = time_spread <= 2

            # 条件2: 至少3个币种回调>=3%
            strong_pullback_count = sum(1 for p in pullbacks if p >= 3.0)

            # 条件3: 见顶时间在4小时内 (使用最早见顶时间)
            import time
            earliest_top = min(top_times)
            current_time_ms = int(time.time() * 1000)
            hours_since_top = (current_time_ms - earliest_top) / 1000 / 3600
            within_time_limit = hours_since_top <= 4.0

            if time_sync and strong_pullback_count >= 3 and within_time_limit:
                avg_pullback = sum(pullbacks) / len(pullbacks)
                details = ', '.join([
                    f"{sym.split('/')[0]}:-{info['pullback_pct']:.1f}%"
                    for sym, info in top_info.items()
                ])

                reason = (f"Big4同步见顶反转: 时间偏差{time_spread}根K线(≤30分钟), "
                         f"{strong_pullback_count}/4币种回调≥3%, 平均回调{avg_pullback:.1f}%, "
                         f"见顶{hours_since_top:.1f}小时内 ({details})")

                logger.warning(f"🚫 [BIG4-TOP] {reason}, 阻止做多")

                # 🔥 紧急干预: 立即平掉所有多单
                if self.trader_service:
                    self.trader_service._emergency_close_all_positions('LONG', reason)
                    # 🔥 设置紧急干预标志,4小时内禁止开多单
                    import time
                    self.trader_service.emergency_top_reversal_time = time.time()
                else:
                    logger.error("❌ 无法执行紧急平仓: trader_service未设置")

                return True, reason

            return False, None

        except Exception as e:
            logger.error(f"[BIG4-TOP-ERROR] Big4见顶检测失败: {e}")
            return False, None  # 检测失败时不阻止,避免影响正常交易

    def load_klines(self, symbol: str, timeframe: str, limit: int = 100):
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        query = """
            SELECT open_price as open, high_price as high,
                   low_price as low, close_price as close,
                   volume
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s
            AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 60 DAY)) * 1000
            ORDER BY open_time DESC LIMIT %s
        """
        cursor.execute(query, (symbol, timeframe, limit))
        klines = list(cursor.fetchall())
        cursor.close()

        klines.reverse()
        for k in klines:
            k['open'] = float(k['open'])
            k['high'] = float(k['high'])
            k['low'] = float(k['low'])
            k['close'] = float(k['close'])
            k['volume'] = float(k['volume'])

        return klines

    def detect_strong_breakout(self, symbol: str, klines_15m: list) -> dict:
        """
        🔥 强破位信号检测（24H区间）

        核心策略：只做强破位，追涨杀跌

        强破位三要素（全部满足）：
        1. Big4天王确认方向
        2. 15M短周期爆发突破（涨跌>0.5%，量能>2倍）
        3. 突破24H新高/新低 > 0.5%

        Args:
            symbol: 交易对
            klines_15m: 15分钟K线数据

        Returns:
            {
                'is_breakout': True/False,
                'direction': 'LONG'/'SHORT'/None,
                'breakout_pct': 突破幅度%,
                'volume_surge': 量能放大倍数,
                'confidence': 置信度(0-100),
                'price': 当前价格,
                'stop_loss_price': 止损价格（破位点位）,
                'reason': 触发原因
            }
        """
        try:
            # 1️⃣ 获取24H高低点
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT high_24h, low_24h, change_24h
                FROM price_stats_24h
                WHERE symbol = %s
            """, (symbol,))

            stats_24h = cursor.fetchone()
            cursor.close()

            if not stats_24h:
                return {'is_breakout': False, 'reason': '无24H数据'}

            high_24h = float(stats_24h['high_24h'])
            low_24h = float(stats_24h['low_24h'])

            # 2️⃣ 检查最近15M K线的爆发性突破
            if len(klines_15m) < 20:
                return {'is_breakout': False, 'reason': '15M数据不足'}

            last_15m = klines_15m[-1]
            prev_15m = klines_15m[-2]

            current_price = last_15m['close']

            # 计算15M涨跌幅
            change_15m_pct = (last_15m['close'] - prev_15m['close']) / prev_15m['close'] * 100

            # 计算量能放大倍数
            recent_volumes = [k['volume'] for k in klines_15m[-20:]]
            avg_volume = sum(recent_volumes) / len(recent_volumes)
            volume_surge = last_15m['volume'] / avg_volume if avg_volume > 0 else 0

            # 3️⃣ 判断是否满足强破位条件

            # 向上强破位
            if (change_15m_pct > 0.5 and                    # 15M涨幅>0.5%
                volume_surge > 2.0 and                       # 量能放大>2倍
                current_price > high_24h):                   # 突破24H新高

                breakout_pct = (current_price - high_24h) / high_24h * 100

                # 必须突破幅度>0.5%
                if breakout_pct < 0.5:
                    return {'is_breakout': False, 'reason': f'突破幅度不足{breakout_pct:.2f}%<0.5%'}

                # 4️⃣ Big4方向确认（调用现有检测器）
                should_block, block_reason = self.detect_big4_top_reversal('LONG')
                if should_block:
                    return {'is_breakout': False, 'reason': f'Big4见顶，拒绝做多'}

                # 计算置信度
                confidence = min(100, 80 + breakout_pct * 20 + (volume_surge - 2.0) * 5)

                return {
                    'is_breakout': True,
                    'direction': 'LONG',
                    'breakout_pct': breakout_pct,
                    'volume_surge': volume_surge,
                    'change_15m_pct': change_15m_pct,
                    'confidence': confidence,
                    'price': current_price,
                    'stop_loss_price': high_24h,  # 止损设在24H高点
                    'reason': (f'🔥强破位做多: 突破24H新高{breakout_pct:.2f}%, '
                              f'15M涨{change_15m_pct:.2f}%, 量能{volume_surge:.1f}x')
                }

            # 向下强破位
            elif (change_15m_pct < -0.5 and                 # 15M跌幅>0.5%
                  volume_surge > 2.0 and                     # 量能放大>2倍
                  current_price < low_24h):                  # 跌破24H新低

                breakout_pct = (low_24h - current_price) / low_24h * 100

                # 必须突破幅度>0.5%
                if breakout_pct < 0.5:
                    return {'is_breakout': False, 'reason': f'突破幅度不足{breakout_pct:.2f}%<0.5%'}

                # 4️⃣ Big4方向确认
                # Big4反转检测已移至主循环统一处理，这里不再重复检测

                # 计算置信度
                confidence = min(100, 80 + breakout_pct * 20 + (volume_surge - 2.0) * 5)

                return {
                    'is_breakout': True,
                    'direction': 'SHORT',
                    'breakout_pct': breakout_pct,
                    'volume_surge': volume_surge,
                    'change_15m_pct': change_15m_pct,
                    'confidence': confidence,
                    'price': current_price,
                    'stop_loss_price': low_24h,  # 止损设在24H低点
                    'reason': (f'🔥强破位做空: 跌破24H新低{breakout_pct:.2f}%, '
                              f'15M跌{change_15m_pct:.2f}%, 量能{volume_surge:.1f}x')
                }

            return {'is_breakout': False, 'reason': '未满足强破位条件'}

        except Exception as e:
            logger.error(f"{symbol} 强破位检测失败: {e}")
            return {'is_breakout': False, 'reason': f'检测异常: {str(e)}'}

    def analyze(self, symbol: str):
        """分析并决策 - 支持做多和做空 (主要使用1小时K线)"""
        if symbol not in self.whitelist:
            return None

        try:
            klines_1d = self.load_klines(symbol, '1d', 50)
            klines_1h = self.load_klines(symbol, '1h', 100)
            klines_15m = self.load_klines(symbol, '15m', 96)  # 24小时的15分钟K线

            if len(klines_1d) < 30 or len(klines_1h) < 72 or len(klines_15m) < 48:  # 至少需要72小时(3天)数据
                return None

            current = klines_1h[-1]['close']

            # 🔥🔥🔥 最高优先级：强破位信号检测 🔥🔥🔥
            # 只做强破位，追涨杀跌
            breakout_result = self.detect_strong_breakout(symbol, klines_15m)

            if breakout_result['is_breakout']:
                # 🔥 强破位信号触发！（只在趋势模式启用）
                logger.critical(f"🔥🔥🔥 [TREND-MODE] {symbol} {breakout_result['reason']}")

                # 🔥 检查是否有反向持仓需要平仓
                if self.trader_service:
                    reverse_side = 'SHORT' if breakout_result['direction'] == 'LONG' else 'LONG'
                    self.trader_service._emergency_close_position_by_symbol_side(
                        symbol=symbol,
                        side=reverse_side,
                        reason=f"BREAKOUT_REVERSE:{breakout_result['reason']}"
                    )

                # 🔥 立即返回强破位信号，权重50分（单独触发）
                return {
                    'symbol': symbol,
                    'side': breakout_result['direction'],
                    'score': 50,  # 强破位固定50分
                    'current_price': breakout_result['price'],
                    'signal_components': {
                        'breakout_strong': 50  # 强破位信号
                    },
                    'breakout_info': {
                        'breakout_pct': breakout_result['breakout_pct'],
                        'volume_surge': breakout_result['volume_surge'],
                        'change_15m_pct': breakout_result['change_15m_pct'],
                        'confidence': breakout_result['confidence'],
                        'stop_loss_price': breakout_result['stop_loss_price']
                    }
                }

            # 分别计算做多和做空得分
            long_score = 0
            short_score = 0

            # 记录信号组成 (用于后续性能分析)
            signal_components = {}

            # ========== 1小时K线分析 (主要) ==========

            # 1. 位置评分 - 使用72小时(3天)高低点
            high_72h = max(k['high'] for k in klines_1h[-72:])
            low_72h = min(k['low'] for k in klines_1h[-72:])

            if high_72h == low_72h:
                position_pct = 50
            else:
                position_pct = (current - low_72h) / (high_72h - low_72h) * 100

            # 提前计算1H量能（在位置判断之前）
            volumes_1h = [k['volume'] for k in klines_1h[-24:]]
            avg_volume_1h = sum(volumes_1h) / len(volumes_1h) if volumes_1h else 1

            strong_bull_1h = 0  # 有力量的阳线
            strong_bear_1h = 0  # 有力量的阴线

            for k in klines_1h[-24:]:
                is_bull = k['close'] > k['open']
                is_high_volume = k['volume'] > avg_volume_1h * 1.2  # 成交量 > 1.2倍平均量

                if is_bull and is_high_volume:
                    strong_bull_1h += 1
                elif not is_bull and is_high_volume:
                    strong_bear_1h += 1

            net_power_1h = strong_bull_1h - strong_bear_1h

            # 低位做多，高位做空 (但要检查量能,避免在破位时做多)
            if position_pct < 30:
                # 检查是否有强空头量能 (破位信号)
                # 如果有强空头量能,不做多 (避免破位时抄底)
                if net_power_1h > -2:  # 没有强空头量能,可以考虑做多
                    weight = self.scoring_weights.get('position_low', {'long': 20, 'short': 0})
                    long_score += weight['long']
                    if weight['long'] > 0:
                        signal_components['position_low'] = weight['long']
            elif position_pct > 70:
                weight = self.scoring_weights.get('position_high', {'long': 0, 'short': 20})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['position_high'] = weight['short']
            else:
                weight = self.scoring_weights.get('position_mid', {'long': 5, 'short': 5})
                long_score += weight['long']
                short_score += weight['short']
                if weight['long'] > 0:
                    signal_components['position_mid'] = weight['long']

            # 2. 短期动量 - 最近24小时涨幅
            gain_24h = (current - klines_1h[-24]['close']) / klines_1h[-24]['close'] * 100
            if gain_24h < -3:  # 24小时跌超过3% - 看跌信号,应该做空
                weight = self.scoring_weights.get('momentum_down_3pct', {'long': 0, 'short': 15})  # 修复: 下跌应该增加SHORT评分
                short_score += weight['short']  # 修复: 改为增加short_score
                if weight['short'] > 0:
                    signal_components['momentum_down_3pct'] = weight['short']
            elif gain_24h > 3:  # 24小时涨超过3% - 看涨信号,应该做多
                weight = self.scoring_weights.get('momentum_up_3pct', {'long': 15, 'short': 0})  # 修复: 上涨应该增加LONG评分
                long_score += weight['long']  # 修复: 改为增加long_score
                if weight['long'] > 0:
                    signal_components['momentum_up_3pct'] = weight['long']

            # 3. 1小时趋势评分 - 最近48根K线(2天)
            bullish_1h = sum(1 for k in klines_1h[-48:] if k['close'] > k['open'])
            bearish_1h = 48 - bullish_1h

            if bullish_1h > 30:  # 超过62.5%是阳线
                weight = self.scoring_weights.get('trend_1h_bull', {'long': 20, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['trend_1h_bull'] = weight['long']
            elif bearish_1h > 30:  # 超过62.5%是阴线
                weight = self.scoring_weights.get('trend_1h_bear', {'long': 0, 'short': 20})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['trend_1h_bear'] = weight['short']

            # 4. 波动率评分 - 最近24小时
            recent_24h = klines_1h[-24:]
            volatility = (max(k['high'] for k in recent_24h) - min(k['low'] for k in recent_24h)) / current * 100

            # 高波动率更适合交易 - 同时给LONG和SHORT加分（因为高波动适合双向交易）
            if volatility > 5:  # 波动超过5%
                weight = self.scoring_weights.get('volatility_high', {'long': 10, 'short': 10})
                long_score += weight['long']
                short_score += weight['short']
                # 记录在组件中（取平均值）
                if weight['long'] > 0 or weight['short'] > 0:
                    signal_components['volatility_high'] = (weight['long'] + weight['short']) / 2

            # 5. 连续趋势强化信号 - 最近10根1小时K线
            recent_10h = klines_1h[-10:]
            bullish_10h = sum(1 for k in recent_10h if k['close'] > k['open'])
            bearish_10h = 10 - bullish_10h

            # 计算最近10小时涨跌幅
            gain_10h = (current - recent_10h[0]['close']) / recent_10h[0]['close'] * 100

            # 连续阳线且上涨幅度适中(不在顶部) - 强做多信号
            if bullish_10h >= 7 and gain_10h < 5 and position_pct < 70:
                weight = self.scoring_weights.get('consecutive_bull', {'long': 15, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['consecutive_bull'] = weight['long']

            # 连续阴线且下跌幅度适中(不在底部) - 强做空信号
            elif bearish_10h >= 7 and gain_10h > -5 and position_pct > 30:
                weight = self.scoring_weights.get('consecutive_bear', {'long': 0, 'short': 15})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['consecutive_bear'] = weight['short']

            # ========== 量能加权K线分析 (核心趋势判断) ==========

            # 6. 1小时K线量能分析已在前面计算（提前用于位置判断）

            # 7. 15分钟K线量能分析 - 最近24根(6小时)
            volumes_15m = [k['volume'] for k in klines_15m[-24:]]
            avg_volume_15m = sum(volumes_15m) / len(volumes_15m) if volumes_15m else 1

            strong_bull_15m = 0
            strong_bear_15m = 0

            for k in klines_15m[-24:]:
                is_bull = k['close'] > k['open']
                is_high_volume = k['volume'] > avg_volume_15m * 1.2

                if is_bull and is_high_volume:
                    strong_bull_15m += 1
                elif not is_bull and is_high_volume:
                    strong_bear_15m += 1

            net_power_15m = strong_bull_15m - strong_bear_15m

            # 量能多头信号: 1H和15M都显示强力多头
            if net_power_1h >= 2 and net_power_15m >= 2:
                weight = self.scoring_weights.get('volume_power_bull', {'long': 25, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['volume_power_bull'] = weight['long']
                    logger.info(f"{symbol} 量能多头强势: 1H净力量={net_power_1h}, 15M净力量={net_power_15m}")

            # 量能空头信号: 1H和15M都显示强力空头
            elif net_power_1h <= -2 and net_power_15m <= -2:
                weight = self.scoring_weights.get('volume_power_bear', {'long': 0, 'short': 25})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['volume_power_bear'] = weight['short']
                    logger.info(f"{symbol} 量能空头强势: 1H净力量={net_power_1h}, 15M净力量={net_power_15m}")

            # 单一时间框架量能信号 (辅助)
            elif net_power_1h >= 3:  # 仅1H强力多头
                weight = self.scoring_weights.get('volume_power_1h_bull', {'long': 15, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['volume_power_1h_bull'] = weight['long']
            elif net_power_1h <= -3:  # 仅1H强力空头
                weight = self.scoring_weights.get('volume_power_1h_bear', {'long': 0, 'short': 15})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['volume_power_1h_bear'] = weight['short']

            # 8. 突破追涨信号: position_high + 强力量能多头 → 可以做多
            # 用户反馈: "不适合做空，那就适合做多啊", "K线多空比，还要结合量能一起看"
            # 🔥 修改: 放宽LONG信号条件，避免BULLISH市场下没有交易机会
            # 原条件: position > 70 太严格
            # 新条件: position > 50 且量能多头强势即可做多
            if position_pct > 50 and (net_power_1h >= 2 or (net_power_1h >= 2 and net_power_15m >= 2)):
                # 额外过滤条件: 防止追高
                can_breakout = True
                breakout_warnings = []

                # 过滤1: 检查最近3根1H K线是否有长上影线（抛压）
                recent_3_klines = klines_1h[-3:]
                for k in recent_3_klines:
                    upper_shadow_pct = (k['high'] - max(k['open'], k['close'])) / k['close'] if k['close'] > 0 else 0
                    if upper_shadow_pct > 0.015:  # 上影线>1.5%
                        can_breakout = False
                        breakout_warnings.append(f"长上影线{upper_shadow_pct*100:.1f}%")
                        break

                # 过滤2: 检查是否连续上涨太多天（追高风险）
                recent_5d_gains = sum(1 for k in klines_1d[-5:] if k['close'] > k['open'])
                if recent_5d_gains >= 4:  # 连续4天以上上涨
                    can_breakout = False
                    breakout_warnings.append(f"连续{recent_5d_gains}天上涨")

                # 移除过滤3: Big4市场趋势判断已足够,1D趋势检查多余且过于严格

                # position_high时有强力量能支撑,且通过过滤,可以追涨做多
                if can_breakout:
                    weight = self.scoring_weights.get('breakout_long', {'long': 20, 'short': 0})
                    long_score += weight['long']
                    if weight['long'] > 0:
                        signal_components['breakout_long'] = weight['long']
                        logger.info(f"{symbol} 突破追涨: position={position_pct:.1f}%, 1H净力量={net_power_1h}")
                        if breakout_warnings:
                            logger.warning(f"{symbol} 突破追涨警告: {', '.join(breakout_warnings)}")
                else:
                    logger.warning(f"{symbol} 追高风险过滤: {', '.join(breakout_warnings)}, 跳过突破信号")

            # 9. 破位追空信号: position_low + 强力量能空头 → 可以做空
            elif position_pct < 30 and (net_power_1h <= -2 or (net_power_1h <= -2 and net_power_15m <= -2)):
                # position_low时有强力量能压制,可以追空做空
                weight = self.scoring_weights.get('breakdown_short', {'long': 0, 'short': 20})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['breakdown_short'] = weight['short']
                    logger.info(f"{symbol} 破位追空: position={position_pct:.1f}%, 1H净力量={net_power_1h}")

            # ========== 移除EMA评分 (已有Big4市场趋势判断) ==========
            # 已移除: ema_bull, ema_bear
            # Big4 (BTC/ETH/BNB/SOL) 市场趋势判断已足够,EMA评分多余

            # ========== 移除1D信号 (4小时持仓不需要1D趋势) ==========
            # 已移除: trend_1d_bull, trend_1d_bear

            # 选择得分更高的方向 (只要达到阈值就可以)
            if long_score >= self.threshold or short_score >= self.threshold:
                if long_score >= short_score:
                    side = 'LONG'
                    score = long_score
                else:
                    side = 'SHORT'
                    score = short_score

                # 🔥 关键修复: 清理signal_components,只保留与最终方向一致的信号
                # 定义多头和空头信号 (已移除1D信号和EMA信号)
                bullish_signals = {
                    'position_low', 'breakout_long', 'volume_power_bull', 'volume_power_1h_bull',
                    'trend_1h_bull', 'momentum_up_3pct', 'consecutive_bull'
                }
                bearish_signals = {
                    'position_high', 'breakdown_short', 'volume_power_bear', 'volume_power_1h_bear',
                    'trend_1h_bear', 'momentum_down_3pct', 'consecutive_bear'
                }
                neutral_signals = {'position_mid', 'volatility_high'}  # 中性信号可以在任何方向

                # 过滤掉与方向相反的信号
                cleaned_components = {}
                for sig, val in signal_components.items():
                    if sig in neutral_signals:
                        cleaned_components[sig] = val  # 中性信号保留
                    elif side == 'LONG' and sig in bullish_signals:
                        cleaned_components[sig] = val  # 做多保留多头信号
                    elif side == 'SHORT' and sig in bearish_signals:
                        cleaned_components[sig] = val  # 做空保留空头信号
                    # 其他信号(方向不一致的)丢弃

                signal_components = cleaned_components  # 替换为清理后的信号

                # 生成信号组合键用于黑名单检查
                if signal_components:
                    sorted_signals = sorted(signal_components.keys())
                    signal_combination_key = " + ".join(sorted_signals)
                else:
                    signal_combination_key = "unknown"

                # 检查信号黑名单 (使用完整的信号组合键)
                # 🔥 临时禁用LONG信号黑名单检查，诊断为什么没有LONG信号
                blacklist_key = f"{signal_combination_key}_{side}"
                if blacklist_key in self.signal_blacklist:
                    if side == 'SHORT':  # 只检查SHORT黑名单
                        logger.info(f"🚫 {symbol} 信号 [{signal_combination_key}] {side} 在黑名单中，跳过（历史表现差）")
                        return None
                    else:  # LONG信号暂时忽略黑名单
                        logger.warning(f"⚠️ {symbol} LONG信号 [{signal_combination_key}] 在黑名单中，但暂时放行（诊断模式）")

                # 🔥 新增: 检查信号方向矛盾（防止逻辑错误）
                is_valid, contradiction_reason = self._validate_signal_direction(signal_components, side)
                if not is_valid:
                    logger.error(f"🚫 {symbol} 信号方向矛盾: {contradiction_reason} | 信号:{signal_combination_key} | 方向:{side}")
                    return None

                # 🔥 已禁用: 位置限制（强破位信号需要追涨杀跌）
                # 强破位信号已在前面单独处理,不会到达这里
                # 其他信号保持位置限制以控制风险
                # if side == 'LONG' and 'position_high' in signal_components:
                #     logger.warning(f"🚫 {symbol} 拒绝高位做多: position_high在{position_pct:.1f}%位置,容易买在顶部")
                #     return None
                #
                # if side == 'SHORT' and 'position_low' in signal_components:
                #     logger.warning(f"🚫 {symbol} 拒绝低位做空: position_low在{position_pct:.1f}%位置,容易遇到反弹")
                #     return None

                # 🔥 紧急干预检查: 如果处于紧急干预期,禁止开新仓
                import time

                # 检查止损熔断
                if self.emergency_stop_loss_circuit_time:
                    hours_since_circuit = (time.time() - self.emergency_stop_loss_circuit_time) / 3600
                    if hours_since_circuit <= self.circuit_block_hours:
                        logger.warning(f"🚨 [CIRCUIT-BREAKER] {symbol} 止损熔断中({hours_since_circuit:.1f}h/{self.circuit_block_hours}h),禁止开仓")
                        return None
                    else:
                        # 超过干预时间,清除标志
                        self.emergency_stop_loss_circuit_time = None

                # 检查总亏损干预
                if self.emergency_loss_limit_time:
                    hours_since_loss_emergency = (time.time() - self.emergency_loss_limit_time) / 3600
                    if hours_since_loss_emergency <= self.emergency_loss_block_hours:
                        logger.warning(f"🚨 [EMERGENCY-BLOCK] {symbol} 总亏损超限紧急干预中({hours_since_loss_emergency:.1f}h/{self.emergency_loss_block_hours}h),禁止开仓")
                        return None
                    else:
                        # 超过干预时间,清除标志
                        self.emergency_loss_limit_time = None

                # 检查底部反转干预
                if side == 'SHORT' and self.emergency_bottom_reversal_time:
                    hours_since_emergency = (time.time() - self.emergency_bottom_reversal_time) / 3600
                    if hours_since_emergency <= self.emergency_block_duration_hours:
                        logger.warning(f"🚨 [EMERGENCY-BLOCK] {symbol} 底部反转紧急干预中({hours_since_emergency:.1f}h/{self.emergency_block_duration_hours}h),禁止做空")
                        return None
                    else:
                        # 超过干预时间,清除标志
                        self.emergency_bottom_reversal_time = None

                # 检查顶部反转干预
                if side == 'LONG' and self.emergency_top_reversal_time:
                    hours_since_emergency = (time.time() - self.emergency_top_reversal_time) / 3600
                    if hours_since_emergency <= self.emergency_block_duration_hours:
                        logger.warning(f"🚨 [EMERGENCY-BLOCK] {symbol} 顶部反转紧急干预中({hours_since_emergency:.1f}h/{self.emergency_block_duration_hours}h),禁止做多")
                        return None
                    else:
                        # 超过干预时间,清除标志
                        self.emergency_top_reversal_time = None

                # 🔥 Big4反转检测已移至主循环统一处理，避免重复检测导致日志刷屏
                # 这里不再单独检测，由主循环在扫描后统一过滤

                # 🔥 调试日志：打印评分详情（帮助诊断为什么LONG信号变成SHORT）
                if long_score >= 30 or short_score >= 30:  # 只打印接近阈值的信号
                    # 打印具体的信号组件，看看是什么贡献了分数
                    logger.info(f"[SCORE] {symbol} {side}={score} | LONG={long_score} SHORT={short_score} | 组件={signal_components}")

                # 🔥 新增: 信号质量筛选（基于历史表现调整阈值，不修改权重）
                if hasattr(self, 'brain') and hasattr(self.brain, 'quality_manager') and self.brain.enable_quality_filter:
                    signal_key = self._generate_signal_combination_key(signal_components)
                    passed, reason = self.brain.quality_manager.check_signal_quality_filter(
                        symbol, side, score, signal_key, base_threshold=self.threshold
                    )
                    if not passed:
                        logger.info(f"[QUALITY_FILTER] {symbol} {side} | {reason}")
                        return None
                    else:
                        logger.info(f"[QUALITY_FILTER] {symbol} {side} | {reason}")

                return {
                    'symbol': symbol,
                    'side': side,
                    'score': score,
                    'current_price': current,
                    'signal_components': signal_components  # 添加信号组成
                }

            return None

        except Exception as e:
            logger.error(f"{symbol} 分析失败: {e}")
            return None

    def scan_all(self):
        """扫描所有币种"""
        opportunities = []
        for symbol in self.whitelist:
            result = self.analyze(symbol)
            if result:
                opportunities.append(result)
        return opportunities

    def _validate_signal_direction(self, signal_components: dict, side: str) -> tuple:
        """
        验证信号方向一致性,防止矛盾信号

        Args:
            signal_components: 信号组件字典
            side: 交易方向 (LONG/SHORT)

        Returns:
            (is_valid, reason) - 是否有效,原因描述
        """
        if not signal_components:
            return True, "无信号组件"

        # 定义空头信号（不应该出现在做多信号中）- 已移除1D信号
        bearish_signals = {
            'breakdown_short', 'volume_power_bear', 'volume_power_1h_bear',
            'trend_1h_bear', 'momentum_down_3pct', 'consecutive_bear'
        }

        # 定义多头信号（不应该出现在做空信号中）- 已移除1D和EMA信号
        bullish_signals = {
            'breakout_long', 'volume_power_bull', 'volume_power_1h_bull',
            'trend_1h_bull', 'momentum_up_3pct', 'consecutive_bull'
        }

        signal_set = set(signal_components.keys())

        if side == 'LONG':
            conflicts = bearish_signals & signal_set
            if conflicts:
                # 特殊情况：低位下跌3%可能是超跌反弹机会,允许做多
                if conflicts == {'momentum_down_3pct'} and 'position_low' in signal_set:
                    return True, "超跌反弹允许"
                return False, f"做多但包含空头信号: {', '.join(conflicts)}"

        elif side == 'SHORT':
            conflicts = bullish_signals & signal_set
            if conflicts:
                # 特殊情况：高位上涨3%可能是超涨回调机会,允许做空
                if conflicts == {'momentum_up_3pct'} and 'position_high' in signal_set:
                    return True, "超涨回调允许"
                return False, f"做空但包含多头信号: {', '.join(conflicts)}"

        return True, "信号方向一致"


class SmartTraderService:
    """智能交易服务"""

    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', '3306')),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'binance-data')
        }

        self.account_id = 2
        self.position_size_usdt = 400  # 默认仓位
        self.blacklist_position_size_usdt = 100  # 黑名单交易对使用小仓位
        self.max_positions = 999  # 不限制持仓数量
        self.leverage = 5
        self.scan_interval = 300

        self.brain = SmartDecisionBrain(self.db_config, trader_service=self)  # 🔥 传入self用于紧急平仓
        self.connection = None
        self.running = True
        self.event_loop = None  # 事件循环引用，在async_main中设置

        # WebSocket 价格服务
        self.ws_service: BinanceWSPriceService = get_ws_price_service()

        # 自适应优化器
        self.optimizer = AdaptiveOptimizer(self.db_config)
        self.last_optimization_date = None  # 记录上次优化日期

        # 🔥 紧急干预标志 - 底部/顶部反转时触发
        self.emergency_bottom_reversal_time = None  # 底部反转触发时间
        self.emergency_top_reversal_time = None     # 顶部反转触发时间
        self.emergency_block_duration_hours = 4     # 紧急干预持续时间(小时)

        # 🔥 紧急干预标志 - 总亏损超过阈值时触发
        self.emergency_loss_limit_time = None       # 总亏损触发时间
        self.emergency_loss_threshold = 600         # 总亏损阈值(USDT)
        self.emergency_loss_block_hours = 2         # 总亏损干预持续时间(小时)

        # 🔥 紧急熔断标志 - 连续止损过多时触发
        self.emergency_stop_loss_circuit_time = None  # 止损熔断触发时间
        self.circuit_check_recent_trades = 10         # 检查最近N笔交易
        self.circuit_stop_loss_threshold = 5          # 止损笔数阈值
        self.circuit_block_hours = 2                  # 熔断持续时间(小时)

        # 优化配置管理器 (支持自我优化的参数配置)
        self.opt_config = OptimizationConfig(self.db_config)

        # 交易对评级管理器 (3级黑名单制度)
        self.rating_manager = SymbolRatingManager(self.db_config)

        # 波动率配置更新器 (15M K线动态止盈)
        self.volatility_updater = VolatilityProfileUpdater(self.db_config)

        # 加载分批建仓和智能平仓配置
        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            self.batch_entry_config = config.get('signals', {}).get('batch_entry', {'enabled': False})
            self.smart_exit_config = config.get('signals', {}).get('smart_exit', {'enabled': False})

        # 初始化智能分批建仓执行器
        if self.batch_entry_config.get('enabled'):
            self.smart_entry_executor = SmartEntryExecutor(
                db_config=self.db_config,
                live_engine=self,
                price_service=self.ws_service
            )
            logger.info("✅ 智能分批建仓执行器已启动")
        else:
            self.smart_entry_executor = None
            logger.info("⚠️ 智能分批建仓未启用")

        # 初始化智能平仓优化器
        if self.smart_exit_config.get('enabled'):
            self.smart_exit_optimizer = SmartExitOptimizer(
                db_config=self.db_config,
                live_engine=self,
                price_service=self.ws_service
            )
            logger.info("✅ 智能平仓优化器已启动")
        else:
            self.smart_exit_optimizer = None
            logger.info("⚠️ 智能平仓优化器未启用")

        # 初始化Big4趋势检测器 (四大天王: BTC/ETH/BNB/SOL)
        self.big4_detector = Big4TrendDetector()
        self.big4_symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

        # Big4缓存机制: 15分钟检测一次, 1小时缓存有效期
        self.cached_big4_result = None
        self.big4_cache_time = None
        self.big4_cache_duration = 3600  # 1小时缓存
        self.big4_detection_interval = 900  # 15分钟检测间隔

        # ========== 震荡市交易策略模块 ==========
        self.range_detector = RangeMarketDetector(self.db_config)
        self.bollinger_strategy = BollingerMeanReversionStrategy(self.db_config)
        self.range_reversal_strategy = RangeReversalStrategy(self.db_config)  # 新增震荡反转策略
        self.mode_switcher = TradingModeSwitcher(self.db_config)
        self.safe_mode_switcher = SafeModeSwitcher(self.db_config)  # 安全模式切换器
        logger.info("✅ 震荡市交易策略模块已初始化（布林带+反转策略+安全模式切换器）")
        self.last_big4_detection_time = None

        logger.info("🔱 Big4趋势检测器已启动 (15分钟检测, 1小时缓存)")

        logger.info("=" * 60)
        logger.info("智能自动交易服务已启动")
        logger.info(f"账户ID: {self.account_id}")
        logger.info(f"仓位: 正常${self.position_size_usdt} / 黑名单${self.blacklist_position_size_usdt} | 杠杆: {self.leverage}x | 最大持仓: {self.max_positions}")
        logger.info(f"白名单: {len(self.brain.whitelist)}个币种 | 黑名单: {len(self.brain.blacklist)}个币种 | 扫描间隔: {self.scan_interval}秒")
        logger.info("🧠 自适应优化器已启用 (每日凌晨2点自动运行)")
        logger.info("🔧 优化配置管理器已启用 (支持4大优化问题的自我配置)")
        logger.info("=" * 60)

    def _get_connection(self):
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(**self.db_config, autocommit=True)
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(**self.db_config, autocommit=True)
        return self.connection

    def check_trading_enabled(self) -> bool:
        """
        检查交易是否启用

        Returns:
            bool: True=交易启用, False=交易停止
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # account_id=2 对应 U本位合约
            cursor.execute("""
                SELECT trading_enabled
                FROM trading_control
                WHERE account_id = %s AND trading_type = 'usdt_futures'
            """, (self.account_id,))

            result = cursor.fetchone()
            cursor.close()

            if result:
                return result['trading_enabled']
            else:
                # 如果数据库中没有记录，默认启用
                logger.warning(f"[TRADING-CONTROL] 未找到交易控制记录(account_id={self.account_id}), 默认启用")
                return True

        except Exception as e:
            # 出错时默认启用，避免影响交易
            logger.error(f"[TRADING-CONTROL] 检查交易状态失败: {e}, 默认启用")
            return True

    def get_big4_result(self):
        """
        获取Big4趋势结果 (带缓存机制)

        缓存策略:
        - 检测间隔: 15分钟
        - 缓存有效期: 1小时
        - 如果缓存过期或不存在，触发新检测
        """
        now = datetime.now()

        # 检查是否需要重新检测 (15分钟间隔)
        should_detect = (
            self.last_big4_detection_time is None or
            (now - self.last_big4_detection_time).total_seconds() >= self.big4_detection_interval
        )

        # 检查缓存是否有效 (1小时)
        cache_valid = (
            self.cached_big4_result is not None and
            self.big4_cache_time is not None and
            (now - self.big4_cache_time).total_seconds() < self.big4_cache_duration
        )

        # 如果需要检测且缓存无效，执行新检测
        if should_detect and not cache_valid:
            try:
                self.cached_big4_result = self.big4_detector.detect_market_trend()
                self.big4_cache_time = now
                self.last_big4_detection_time = now
                logger.info(f"🔱 Big4趋势已更新缓存 | {self.cached_big4_result['overall_signal']} (强度: {self.cached_big4_result['signal_strength']:.0f})")
            except Exception as e:
                import traceback
                logger.error(f"❌ Big4趋势检测失败: {e}")
                logger.error(f"完整错误堆栈:\n{traceback.format_exc()}")
                # 检测失败时，如果有旧缓存就继续用，否则返回空结果
                if self.cached_big4_result is None:
                    return {
                        'overall_signal': 'NEUTRAL',
                        'signal_strength': 0,
                        'details': {},
                        'timestamp': now
                    }

        # 如果需要检测但缓存仍有效，只更新检测时间（实际不检测）
        elif should_detect and cache_valid:
            self.last_big4_detection_time = now
            logger.debug(f"🔱 Big4缓存仍有效，跳过检测")

        return self.cached_big4_result

    def get_current_price(self, symbol: str):
        """获取当前价格 - 优先WebSocket实时价,回退到5m K线"""
        try:
            # 优先从WebSocket获取实时价格(与SmartExitOptimizer检查止盈时用同一价格源,避免止盈缩水)
            if self.ws_service:
                ws_price = self.ws_service.get_price(symbol)
                if ws_price and ws_price > 0:
                    logger.debug(f"[PRICE] {symbol} 使用WebSocket实时价: {ws_price}")
                    return ws_price
                else:
                    logger.debug(f"[PRICE] {symbol} WebSocket价格无效,回退到K线")

            # 回退到5分钟K线
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT close_price, open_time
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()
            cursor.close()

            if not result:
                logger.warning(f"[PRICE] {symbol} K线数据不存在")
                return None

            close_price, open_time = result

            # 检查数据新鲜度: 5m K线数据不能超过10分钟前
            import time
            current_timestamp_ms = int(time.time() * 1000)
            data_age_minutes = (current_timestamp_ms - open_time) / 1000 / 60

            if data_age_minutes > 10:
                logger.warning(
                    f"[DATA_STALE] {symbol} K线数据过时! "
                    f"最新K线时间: {data_age_minutes:.1f}分钟前, 拒绝使用"
                )
                return None

            logger.debug(f"[PRICE] {symbol} 使用K线价格: {close_price} (数据年龄: {data_age_minutes:.1f}分钟)")
            return float(close_price)
        except Exception as e:
            logger.error(f"[ERROR] 获取{symbol}价格失败: {e}")
            return None

    def get_open_positions_count(self):
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (self.account_id,))
            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        except:
            return 0

    def has_position(self, symbol: str, side: str = None):
        """
        检查是否有持仓
        symbol: 交易对
        side: 方向(LONG/SHORT), None表示检查任意方向
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if side:
                # 检查特定方向的持仓（包括正在建仓的持仓）
                cursor.execute("""
                    SELECT COUNT(*) FROM futures_positions
                    WHERE symbol = %s AND position_side = %s AND status IN ('open', 'building') AND account_id = %s
                """, (symbol, side, self.account_id))
            else:
                # 检查任意方向的持仓（包括正在建仓的持仓）
                cursor.execute("""
                    SELECT COUNT(*) FROM futures_positions
                    WHERE symbol = %s AND status IN ('open', 'building') AND account_id = %s
                """, (symbol, self.account_id))

            result = cursor.fetchone()
            cursor.close()
            return result[0] > 0 if result else False
        except:
            return False

    def validate_signal_timeframe(self, signal_components: dict, side: str, symbol: str) -> tuple:
        """
        验证信号组合的时间框架一致性

        Returns:
            (is_valid, reason) - 是否有效,原因描述
        """
        if not signal_components:
            return True, "无信号组件"

        # 提取趋势信号
        has_1h_bull = 'trend_1h_bull' in signal_components
        has_1h_bear = 'trend_1h_bear' in signal_components
        has_1d_bull = 'trend_1d_bull' in signal_components
        has_1d_bear = 'trend_1d_bear' in signal_components

        # 规则1: 做多时,1小时必须不能看跌
        if side == 'LONG' and has_1h_bear:
            return False, "时间框架冲突: 做多但1H看跌"

        # 规则2: 做空时,1小时必须不能看涨
        if side == 'SHORT' and has_1h_bull:
            return False, "时间框架冲突: 做空但1H看涨"

        # 规则3: 多空方向的日线趋势不能相反
        # 注意: 允许日线中性(既没有bull也没有bear)
        if side == 'LONG' and has_1d_bear:
            return False, "时间框架冲突: 做多但1D看跌"

        if side == 'SHORT' and has_1d_bull:
            return False, "时间框架冲突: 做空但1D看涨"

        return True, "时间框架一致"

    def calculate_volatility_adjusted_stop_loss(self, signal_components: dict, base_stop_loss_pct: float) -> float:
        """
        根据波动率调整止损百分比

        Args:
            signal_components: 信号组件
            base_stop_loss_pct: 基础止损百分比(如0.02)

        Returns:
            调整后的止损百分比
        """
        # 检查是否包含高波动信号
        has_high_volatility = 'volatility_high' in signal_components

        if has_high_volatility:
            # 高波动环境: 扩大止损到1.5倍(2% -> 3%)
            adjusted_sl = base_stop_loss_pct * 1.5
            logger.info(f"[VOLATILITY_ADJUST] 高波动环境,止损从{base_stop_loss_pct*100:.1f}%扩大到{adjusted_sl*100:.1f}%")
            return adjusted_sl

        return base_stop_loss_pct

    def validate_position_high_signal(self, symbol: str, signal_components: dict, side: str) -> tuple:
        """
        缺陷2修复: 增强position_high信号验证

        position_high单独不足以确认顶部,需要额外确认:
        1. 更长周期的位置检查(7天而非3天)
        2. 涨幅是否已经放缓(连续上影线)
        3. 是否有momentum_up信号(避免加速上涨时做空)

        Returns:
            (is_valid, reason)
        """
        # 只检查包含position_high的做空信号
        if side != 'SHORT' or 'position_high' not in signal_components:
            return True, "不是position_high做空"

        try:
            # 检查1: 是否伴随momentum_up(涨势)信号
            # 如果价格还在上涨3%+,说明动能未衰竭,不适合做空
            has_momentum_up = 'momentum_up_3pct' in signal_components
            if has_momentum_up:
                return False, "position_high但伴随momentum_up_3pct,动能未衰竭"

            # 检查2: 加载最近的K线,检查是否有顶部特征
            klines_1h = self.brain.load_klines(symbol, '1h', 24)
            if len(klines_1h) < 10:
                return True, "K线数据不足,跳过验证"

            # 计算最近10根K线的上影线比例
            recent_10 = klines_1h[-10:]
            upper_shadow_count = 0
            for k in recent_10:
                body_high = max(k['open'], k['close'])
                upper_shadow = k['high'] - body_high
                body_size = abs(k['close'] - k['open'])

                # 上影线 > 实体的50% 认为是上影线K线
                if body_size > 0 and upper_shadow / body_size > 0.5:
                    upper_shadow_count += 1

            upper_shadow_ratio = upper_shadow_count / 10

            # 如果最近10根K线上影线<30%,说明买盘还很强,不适合做空
            if upper_shadow_ratio < 0.3:
                return False, f"position_high但上影线比例仅{upper_shadow_ratio*100:.0f}%,买盘未衰竭"

            # 缺陷4修复: 检查成交量是否萎缩(顶部特征)
            recent_5 = klines_1h[-5:]
            earlier_5 = klines_1h[-10:-5]

            recent_volume = sum([float(k.get('volume', 0)) for k in recent_5])
            earlier_volume = sum([float(k.get('volume', 0)) for k in earlier_5])

            if recent_volume > 0 and earlier_volume > 0:
                volume_ratio = recent_volume / earlier_volume

                # 如果最近5根K线成交量 > 之前5根的1.2倍,说明成交量在放大,不是顶部
                if volume_ratio > 1.2:
                    return False, f"position_high但成交量放大{volume_ratio:.2f}倍,非顶部特征"

                logger.info(f"[VOLUME_CHECK] {symbol} 成交量比例{volume_ratio:.2f},符合顶部萎缩特征")

            logger.info(f"[POSITION_HIGH_VALID] {symbol} 上影线{upper_shadow_ratio*100:.0f}%,顶部特征明显")
            return True, "position_high验证通过"

        except Exception as e:
            logger.warning(f"[POSITION_HIGH_CHECK] {symbol} 验证失败: {e},默认通过")
            return True, "验证异常,默认通过"

    def open_position(self, opp: dict):
        """开仓 - 支持做多和做空，支持分批建仓，使用 WebSocket 实时价格"""
        symbol = opp['symbol']
        side = opp['side']  # 'LONG' 或 'SHORT'
        strategy = opp.get('strategy', 'default')  # 获取策略类型

        # ========== 第零步：验证symbol格式 ==========
        # U本位服务只应该交易 /USDT 交易对
        if symbol.endswith('/USD') and not symbol.endswith('/USDT'):
            logger.error(f"[SYMBOL_ERROR] {symbol} 是币本位交易对(/USD),不应在U本位服务开仓,已拒绝")
            return False

        if not symbol.endswith('/USDT'):
            logger.error(f"[SYMBOL_ERROR] {symbol} 格式错误,U本位服务只支持/USDT交易对,已拒绝")
            return False

        # ========== 第一步：验证信号（无论用哪种开仓方式都要验证） ==========
        signal_components = opp.get('signal_components', {})

        # 缺陷1修复: 验证时间框架一致性
        is_valid, reason = self.validate_signal_timeframe(signal_components, side, symbol)
        if not is_valid:
            logger.warning(f"[SIGNAL_REJECT] {symbol} {side} - {reason}")
            return False

        # 缺陷2修复: position_high信号额外验证
        is_valid, reason = self.validate_position_high_signal(symbol, signal_components, side)
        if not is_valid:
            logger.warning(f"[SIGNAL_REJECT] {symbol} {side} - {reason}")
            return False

        # 新增验证: 检查是否在平仓后冷却期内(1小时)
        if self.check_recent_close(symbol, side, cooldown_minutes=15):
            logger.warning(f"[SIGNAL_REJECT] {symbol} {side} - 平仓后15分钟冷却期内")
            return False

        # 新增验证: 防追高/追跌过滤
        current_price = self.ws_service.get_price(symbol)
        if current_price:
            pass_filter, filter_reason = self.brain.check_anti_fomo_filter(symbol, current_price, side)
            if not pass_filter:
                logger.warning(f"[ANTI-FOMO] {symbol} {side} - {filter_reason}")
                return False
            else:
                logger.info(f"[ANTI-FOMO] {symbol} {side} 通过防追高检查: {filter_reason}")

        # ========== 第二步：提前检查黑名单（分批建仓也要检查）==========
        rating_level = self.opt_config.get_symbol_rating_level(symbol)
        if rating_level == 3:
            logger.warning(f"[BLACKLIST_LEVEL3] {symbol} 已被永久禁止交易")
            return False

        # ========== 第三步：决定使用分批建仓还是一次性开仓 ==========
        # 检查是否启用分批建仓
        if self.smart_entry_executor and self.batch_entry_config.get('enabled'):
            # 检查是否在白名单中（如果白名单为空，则对所有币种启用）
            whitelist = self.batch_entry_config.get('whitelist_symbols', [])
            should_use_batch = (not whitelist) or (symbol in whitelist)

            # 反转开仓不使用分批建仓（直接一次性开仓）
            is_reversal = 'reversal_from' in opp

            # 震荡市策略不使用分批建仓（使用固定2%止损，与分批建仓的波动率止损不兼容）
            is_range_strategy = strategy == 'bollinger_mean_reversion'

            # 🔥 紧急修复: 中性市场禁用分批建仓
            disable_batch = opp.get('disable_batch_entry', False)

            if should_use_batch and not is_reversal and not is_range_strategy and not disable_batch:
                logger.info(f"[BATCH_ENTRY] {symbol} {side} 使用智能分批建仓（后台异步执行）")
                # 在后台异步执行分批建仓，不阻塞主循环
                import asyncio
                try:
                    # 使用保存的事件循环引用
                    if self.event_loop:
                        # 在后台创建任务，不等待完成
                        asyncio.run_coroutine_threadsafe(
                            self._open_position_with_batch(opp),
                            self.event_loop
                        )
                        logger.info(f"[BATCH_ENTRY] {symbol} {side} 分批建仓任务已启动（后台运行60分钟）")
                        return True  # 立即返回，不阻塞
                    else:
                        logger.error(f"[BATCH_ENTRY_ERROR] {symbol} {side} 事件循环未初始化，降级到一次性开仓")
                except Exception as e:
                    logger.error(f"[BATCH_ENTRY_ERROR] {symbol} {side} 分批建仓启动失败: {e}，降级到一次性开仓")
                    # 降级到原有一次性开仓逻辑

        # ========== 第三步：一次性开仓逻辑 ==========
        try:

            # 优先从 WebSocket 获取实时价格
            current_price = self.ws_service.get_price(symbol)

            # 如果 WebSocket 价格不可用，回退到数据库价格
            if not current_price or current_price <= 0:
                logger.warning(f"[WS_FALLBACK] {symbol} WebSocket价格不可用，回退到数据库价格")
                current_price = self.get_current_price(symbol)
                if not current_price:
                    logger.error(f"{symbol} 无法获取价格")
                    return False
                price_source = "DB"
            else:
                price_source = "WS"

            # 检查是否为反转开仓(使用原仓位保证金)
            is_reversal = 'reversal_from' in opp
            rating_level = 0  # 默认白名单
            is_hedge = False  # 默认非对冲
            adjusted_position_size = None  # 初始化变量,避免UnboundLocalError

            if is_reversal and 'original_margin' in opp:
                # 反转开仓: 使用原仓位相同的保证金
                adjusted_position_size = opp['original_margin']
                logger.info(f"[REVERSAL_MARGIN] {symbol} 反转开仓, 使用原仓位保证金: ${adjusted_position_size:.2f}")

                # 仍需获取自适应参数用于止损止盈
                if side == 'LONG':
                    adaptive_params = self.brain.adaptive_long
                else:  # SHORT
                    adaptive_params = self.brain.adaptive_short

                # 反转开仓也需要检查评级(用于日志显示)
                rating_level = self.opt_config.get_symbol_rating_level(symbol)

            if not is_reversal or 'original_margin' not in opp:
                # 正常开仓流程
                # 问题2优化: 使用3级评级制度替代简单黑名单
                # 注意: rating_level已在函数开头检查过了
                rating_config = self.opt_config.get_blacklist_config(rating_level)

                # ========== 检查是否为震荡市策略 ==========
                mode_config = None
                if strategy == 'bollinger_mean_reversion':
                    try:
                        mode_config = self.mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
                        if mode_config:
                            logger.info(f"[RANGE_MODE] {symbol} 使用震荡市交易参数")
                    except Exception as e:
                        logger.error(f"[MODE_ERROR] 获取模式配置失败: {e}")

                # 获取评级对应的保证金倍数
                rating_margin_multiplier = rating_config['margin_multiplier']

                # ========== 根据策略类型确定基础仓位大小 ==========
                if strategy == 'bollinger_mean_reversion' and mode_config:
                    # 震荡市模式: 使用range_position_size (默认3%)
                    range_position_pct = float(mode_config['range_position_size'])  # 转换Decimal为float
                    base_position_size = self.position_size_usdt * (range_position_pct / 5.0) * rating_margin_multiplier
                    logger.info(f"[RANGE_POSITION] {symbol} 震荡市仓位: {range_position_pct}% × {rating_margin_multiplier:.2f} = ${base_position_size:.2f}")
                else:
                    # 趋势模式: 使用默认仓位(5%)
                    base_position_size = self.position_size_usdt * rating_margin_multiplier

                # 记录评级信息
                rating_tag = f"[Level{rating_level}]" if rating_level > 0 else "[白名单]"
                logger.info(f"{rating_tag} {symbol} 保证金倍数: {rating_margin_multiplier:.2f}")

                # 根据Big4市场信号动态调整仓位倍数 (震荡市策略不调整仓位)
                if strategy == 'bollinger_mean_reversion':
                    position_multiplier = 1.0
                    logger.info(f"[RANGE_MODE] {symbol} 震荡市策略不使用Big4仓位调整")
                else:
                    try:
                        big4_result = self.get_big4_result()
                        market_signal = big4_result.get('overall_signal', 'NEUTRAL')

                        # 根据市场信号决定仓位倍数
                        if market_signal == 'BULLISH' and side == 'LONG':
                            position_multiplier = 1.2  # 市场看多,做多加仓
                            logger.info(f"[BIG4-POSITION] {symbol} 市场看多,做多仓位 × 1.2")
                        elif market_signal == 'BEARISH' and side == 'SHORT':
                            position_multiplier = 1.2  # 市场看空,做空加仓
                            logger.info(f"[BIG4-POSITION] {symbol} 市场看空,做空仓位 × 1.2")
                        else:
                            position_multiplier = 1.0  # 其他情况正常仓位
                            if market_signal != 'NEUTRAL':
                                logger.info(f"[BIG4-POSITION] {symbol} 逆势信号,仓位 × 1.0 (市场{market_signal}, 开仓{side})")
                    except Exception as e:
                        logger.warning(f"[BIG4-POSITION] 获取市场信号失败,使用默认仓位倍数1.0: {e}")
                        position_multiplier = 1.0

                # 获取自适应参数
                if side == 'LONG':
                    adaptive_params = self.brain.adaptive_long
                else:  # SHORT
                    adaptive_params = self.brain.adaptive_short

                # 应用仓位倍数
                adjusted_position_size = base_position_size * position_multiplier

            quantity = adjusted_position_size * self.leverage / current_price
            notional_value = quantity * current_price
            margin = adjusted_position_size

            # ========== 根据策略类型确定止损止盈 ==========
            if strategy == 'bollinger_mean_reversion' and 'take_profit_price' in opp and 'stop_loss_price' in opp:
                # 震荡市策略: 使用策略提供的具体价格
                stop_loss = opp['stop_loss_price']
                take_profit = opp['take_profit_price']

                # 计算实际百分比用于日志
                if side == 'LONG':
                    stop_loss_pct = (current_price - stop_loss) / current_price
                    take_profit_pct = (take_profit - current_price) / current_price
                else:  # SHORT
                    stop_loss_pct = (stop_loss - current_price) / current_price
                    take_profit_pct = (current_price - take_profit) / current_price

                logger.info(f"[RANGE_TP_SL] {symbol} 使用布林带策略止盈止损: TP=${take_profit:.4f}({take_profit_pct*100:.2f}%), SL=${stop_loss:.4f}({stop_loss_pct*100:.2f}%)")

            else:
                # 趋势模式: 使用原有逻辑
                # 使用自适应参数计算止损
                base_stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)

                # 缺陷5修复: 波动率自适应止损
                stop_loss_pct = self.calculate_volatility_adjusted_stop_loss(signal_components, base_stop_loss_pct)

                # 问题4优化: 使用波动率配置计算动态止盈
                volatility_profile = self.opt_config.get_symbol_volatility_profile(symbol)
                if volatility_profile:
                    # 根据方向使用对应的止盈配置
                    if side == 'LONG' and volatility_profile.get('long_fixed_tp_pct'):
                        take_profit_pct = float(volatility_profile['long_fixed_tp_pct'])
                        logger.debug(f"[TP_DYNAMIC] {symbol} LONG 使用15M阳线动态止盈: {take_profit_pct*100:.3f}%")
                    elif side == 'SHORT' and volatility_profile.get('short_fixed_tp_pct'):
                        take_profit_pct = float(volatility_profile['short_fixed_tp_pct'])
                        logger.debug(f"[TP_DYNAMIC] {symbol} SHORT 使用15M阴线动态止盈: {take_profit_pct*100:.3f}%")
                    else:
                        # 回退到自适应参数
                        take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)
                        logger.debug(f"[TP_FALLBACK] {symbol} {side} 波动率配置不全,使用自适应参数: {take_profit_pct*100:.2f}%")
                else:
                    # 回退到自适应参数
                    take_profit_pct = adaptive_params.get('take_profit_pct', 0.02)
                    logger.debug(f"[TP_FALLBACK] {symbol} 无波动率配置,使用自适应参数: {take_profit_pct*100:.2f}%")

                if side == 'LONG':
                    stop_loss = current_price * (1 - stop_loss_pct)    # 止损
                    take_profit = current_price * (1 + take_profit_pct) # 止盈
                else:  # SHORT
                    stop_loss = current_price * (1 + stop_loss_pct)    # 止损
                    take_profit = current_price * (1 - take_profit_pct) # 止盈

            logger.info(f"[OPEN] {symbol} {side} | 价格: ${current_price:.4f} ({price_source}) | 数量: {quantity:.2f}")

            conn = self._get_connection()
            cursor = conn.cursor()

            # 准备信号组成数据
            import json
            signal_components = opp.get('signal_components', {})
            logger.info(f"[DEBUG] signal_components: {signal_components}, has key: {'signal_components' in opp}")
            signal_components_json = json.dumps(signal_components) if signal_components else None
            entry_score = opp.get('score', 0)

            # 生成信号组合键 (按字母顺序排序信号名称)
            if signal_components:
                sorted_signals = sorted(signal_components.keys())
                signal_combination_key = " + ".join(sorted_signals)
            else:
                # 如果是震荡市策略但缺少signal_components（兼容旧版本）
                if strategy == 'bollinger_mean_reversion':
                    signal_combination_key = "range_trading"
                else:
                    signal_combination_key = "unknown"

            # 检查是否为反转信号
            if is_reversal:
                signal_combination_key = f"REVERSAL_{opp.get('reversal_from', 'unknown')}"

            # 震荡市策略特殊标记（如果还没有RANGE前缀）
            if strategy == 'bollinger_mean_reversion' and not signal_combination_key.startswith('RANGE_'):
                signal_combination_key = f"RANGE_{signal_combination_key}"

            # 趋势策略特殊标记：如果不是RANGE也不是REVERSAL，就是TREND策略
            elif not signal_combination_key.startswith(('RANGE_', 'REVERSAL_', 'TREND_')):
                signal_combination_key = f"TREND_{signal_combination_key}"

            logger.info(f"[SIGNAL_COMBO] {symbol} {side} 信号组合: {signal_combination_key} (评分: {entry_score}) 策略: {strategy}")

            # Big4 信号记录
            if opp.get('big4_adjusted'):
                big4_signal = opp.get('big4_signal', 'NEUTRAL')
                big4_strength = opp.get('big4_strength', 0)
                logger.info(f"[BIG4-APPLIED] {symbol} Big4趋势: {big4_signal} (强度: {big4_strength})")

            # ========== 根据策略类型确定超时时间 ==========
            # 🔥 紧急修复: 优先检查是否有中性市场指定的持仓时间
            if 'max_hold_minutes' in opp:
                base_timeout_minutes = opp['max_hold_minutes']
                logger.info(f"[中性市-TIMEOUT] {symbol} 使用指定持仓时间: {base_timeout_minutes}分钟")
            elif strategy == 'bollinger_mean_reversion' and mode_config:
                # 震荡市策略: 使用range_max_hold_hours (默认4小时)
                range_max_hold_hours = int(mode_config.get('range_max_hold_hours', 4))  # 转换Decimal为int
                base_timeout_minutes = range_max_hold_hours * 60
                logger.info(f"[RANGE_TIMEOUT] {symbol} 震荡市最大持仓时间: {base_timeout_minutes}分钟")
            else:
                # 趋势模式: 使用动态超时时间
                base_timeout_minutes = self.opt_config.get_timeout_by_score(entry_score)

            # 计算超时时间点 (UTC时间)
            from datetime import datetime, timedelta
            timeout_at = datetime.utcnow() + timedelta(minutes=base_timeout_minutes)

            # 准备entry_reason
            entry_reason = opp.get('reason', '')
            if strategy == 'bollinger_mean_reversion':
                entry_reason = f"[震荡市] {entry_reason}"

            # 插入持仓记录 (包含动态超时字段)
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_reason, entry_score, signal_components, max_hold_minutes, timeout_at,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, 'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, side, quantity, current_price, self.leverage,
                notional_value, margin, stop_loss, take_profit,
                signal_combination_key, entry_reason, entry_score, signal_components_json,
                base_timeout_minutes, timeout_at
            ))

            # 获取持仓ID
            position_id = cursor.lastrowid

            # 冻结资金 (开仓时扣除可用余额，增加冻结余额)
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET current_balance = current_balance - %s,
                    frozen_balance = frozen_balance + %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (margin, margin, self.account_id))

            cursor.close()

            # 显示实际使用的止损止盈百分比
            sl_pct = f"-{stop_loss_pct*100:.1f}%" if side == 'LONG' else f"+{stop_loss_pct*100:.1f}%"
            tp_pct = f"+{take_profit_pct*100:.1f}%" if side == 'LONG' else f"-{take_profit_pct*100:.1f}%"

            # 显示评级和对冲标签
            if rating_level == 0:
                rating_tag = ""
            elif rating_level == 1:
                rating_tag = " [黑名单L1-25%]"
            elif rating_level == 2:
                rating_tag = " [黑名单L2-12.5%]"
            else:
                rating_tag = " [黑名单L3-禁止]"

            hedge_tag = " [对冲]" if is_hedge else ""

            # 格式化信号组合显示(显示各信号的分数)
            if signal_components:
                signal_details = ", ".join([f"{k}:{v}" for k, v in sorted(signal_components.items(), key=lambda x: x[1], reverse=True)])
            else:
                signal_details = "无"

            logger.info(
                f"[SUCCESS] {symbol} {side}开仓成功{rating_tag}{hedge_tag} | "
                f"信号: [{signal_combination_key}] | "
                f"止损: ${stop_loss:.4f} ({sl_pct}) | 止盈: ${take_profit:.4f} ({tp_pct}) | "
                f"仓位: ${margin:.0f} | 超时: {base_timeout_minutes}分钟"
            )
            logger.info(f"[SIGNAL_DETAIL] {symbol} 信号详情: {signal_details}")

            # 启动智能平仓监控（统一平仓入口）
            if self.smart_exit_optimizer and self.event_loop:
                try:
                    import asyncio
                    asyncio.run_coroutine_threadsafe(
                        self.smart_exit_optimizer.start_monitoring_position(position_id),
                        self.event_loop
                    )
                    logger.info(f"✅ 持仓{position_id}已加入智能平仓监控")
                except Exception as e:
                    logger.error(f"❌ 持仓{position_id}启动监控失败: {e}")

            return True

        except Exception as e:
            logger.error(f"[ERROR] {symbol} 开仓失败: {e}")
            return False

    async def _open_position_with_batch(self, opp: dict):
        """使用智能分批建仓执行器开仓（信号已在调用前验证）"""
        symbol = opp['symbol']
        side = opp['side']

        try:
            # 注意：信号验证已在 open_position() 中完成，这里直接计算保证金
            signal_components = opp.get('signal_components', {})

            # 计算保证金（复用原有逻辑）
            rating_level = self.opt_config.get_symbol_rating_level(symbol)
            rating_config = self.opt_config.get_blacklist_config(rating_level)

            if rating_level == 3:
                logger.warning(f"[BLACKLIST_LEVEL3] {symbol} 已被永久禁止交易")
                return False

            rating_margin_multiplier = rating_config['margin_multiplier']
            base_position_size = self.position_size_usdt * rating_margin_multiplier

            # 根据Big4市场信号动态调整仓位倍数
            try:
                big4_result = self.get_big4_result()
                market_signal = big4_result.get('overall_signal', 'NEUTRAL')

                # 根据市场信号决定仓位倍数
                if market_signal == 'BULLISH' and side == 'LONG':
                    position_multiplier = 1.2  # 市场看多,做多加仓
                    logger.info(f"[BIG4-POSITION] {symbol} 市场看多,做多仓位 × 1.2")
                elif market_signal == 'BEARISH' and side == 'SHORT':
                    position_multiplier = 1.2  # 市场看空,做空加仓
                    logger.info(f"[BIG4-POSITION] {symbol} 市场看空,做空仓位 × 1.2")
                else:
                    position_multiplier = 1.0  # 其他情况正常仓位
                    if market_signal != 'NEUTRAL':
                        logger.info(f"[BIG4-POSITION] {symbol} 逆势信号,仓位 × 1.0 (市场{market_signal}, 开仓{side})")
            except Exception as e:
                logger.warning(f"[BIG4-POSITION] 获取市场信号失败,使用默认仓位倍数1.0: {e}")
                position_multiplier = 1.0

            # 获取自适应参数
            if side == 'LONG':
                adaptive_params = self.brain.adaptive_long
            else:
                adaptive_params = self.brain.adaptive_short

            adjusted_position_size = base_position_size * position_multiplier

            # 调用智能建仓执行器（作为后台任务，避免阻塞主循环）
            entry_task = asyncio.create_task(self.smart_entry_executor.execute_entry({
                'symbol': symbol,
                'direction': side,
                'total_margin': adjusted_position_size,
                'leverage': self.leverage,
                'strategy_id': 'smart_trader',
                'max_hold_minutes': opp.get('max_hold_minutes', 240),  # 🔥 传入持仓时间(中性市120,趋势市240)
                'trade_params': {
                    'entry_score': opp.get('score', 0),
                    'signal_components': signal_components,
                    'adaptive_params': adaptive_params,
                    'signal_combination_key': self._generate_signal_combination_key(signal_components)
                }
            }))

            # 添加完成回调来启动智能平仓监控
            # 明确捕获闭包变量
            _symbol = symbol
            _side = side
            _smart_exit_optimizer = self.smart_exit_optimizer

            def on_entry_complete(task):
                try:
                    entry_result = task.result()
                    if entry_result['success']:
                        position_id = entry_result['position_id']
                        logger.info(
                            f"✅ [BATCH_ENTRY_COMPLETE] {_symbol} {_side} | "
                            f"持仓ID: {position_id} | "
                            f"平均价格: ${entry_result['avg_price']:.4f} | "
                            f"总数量: {entry_result['total_quantity']:.2f}"
                        )

                        # 启动智能平仓监控（如果启用）
                        if _smart_exit_optimizer:
                            try:
                                loop = asyncio.get_event_loop()
                                if loop.is_closed():
                                    logger.warning(f"⚠️ 事件循环已关闭，无法启动智能平仓监控: 持仓{position_id}")
                                else:
                                    asyncio.create_task(_smart_exit_optimizer.start_monitoring_position(position_id))
                                    logger.info(f"✅ [SMART_EXIT] 已启动智能平仓监控: 持仓{position_id}")
                            except RuntimeError as e:
                                logger.warning(f"⚠️ 无法启动智能平仓监控: {e}")
                    else:
                        logger.error(f"❌ [BATCH_ENTRY_FAILED] {_symbol} {_side} | {entry_result.get('error')}")
                except Exception as e:
                    logger.error(f"❌ [BATCH_ENTRY_CALLBACK_ERROR] {_symbol} {_side} | {e}")

            entry_task.add_done_callback(on_entry_complete)
            logger.info(f"🚀 [BATCH_ENTRY_STARTED] {symbol} {side} | 分批建仓已启动（后台运行60分钟）")

            return True

        except Exception as e:
            logger.error(f"❌ [BATCH_ENTRY_ERROR] {symbol} {side} | {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _generate_signal_combination_key(self, signal_components: dict) -> str:
        """
        生成信号组合键
        注意: 此方法仅用于分批建仓,而分批建仓仅用于TREND策略
        因此直接添加TREND_前缀
        """
        if signal_components:
            sorted_signals = sorted(signal_components.keys())
            signal_key = " + ".join(sorted_signals)
            # 分批建仓只用于TREND策略,直接添加TREND_前缀
            return f"TREND_{signal_key}"
        else:
            return "TREND_unknown"

    def check_top_bottom(self, symbol: str, position_side: str, entry_price: float):
        """智能识别顶部和底部 - 使用1h K线更稳健的判断"""
        try:
            # 使用1小时K线分析（更稳健，减少假信号）
            klines_1h = self.brain.load_klines(symbol, '1h', 48)
            if len(klines_1h) < 24:
                return False, None

            current = klines_1h[-1]
            recent_24 = klines_1h[-24:]  # 最近24小时
            recent_12 = klines_1h[-12:]  # 最近12小时

            if position_side == 'LONG':
                # 做多持仓 - 寻找顶部信号

                # 1. 价格在最近12小时创新高后回落
                max_high = max(k['high'] for k in recent_12)
                max_high_idx = len(recent_12) - 1 - [k['high'] for k in reversed(recent_12)].index(max_high)
                is_peak = max_high_idx < 10  # 高点在前10根K线，现在回落

                # 2. 当前价格已经从高点回落（1h级别阈值提高到1.5%）
                current_price = current['close']
                pullback_pct = (max_high - current_price) / max_high * 100

                # 3. 最近4根1h K线趋势确认：至少3根收阴或长上影线
                recent_4 = klines_1h[-4:]
                bearish_count = sum(1 for k in recent_4 if k['close'] < k['open'])
                long_upper_shadow = sum(1 for k in recent_4 if (k['high'] - max(k['open'], k['close'])) > abs(k['close'] - k['open']) * 1.5)

                # 4. 成交量确认：最近3根K线成交量放大
                if len(recent_24) >= 24:
                    avg_volume_24h = sum(k['volume'] for k in recent_24[:21]) / 21
                    recent_3_volume = sum(k['volume'] for k in klines_1h[-3:]) / 3
                    volume_surge = recent_3_volume > avg_volume_24h * 1.2
                else:
                    volume_surge = True  # 数据不足时忽略成交量确认

                # 见顶判断条件（更严格）
                if is_peak and pullback_pct >= 1.5 and (bearish_count >= 3 or long_upper_shadow >= 2):
                    # 计算当前盈利
                    profit_pct = (current_price - entry_price) / entry_price * 100
                    return True, f"TOP_DETECTED(高点回落{pullback_pct:.1f}%,盈利{profit_pct:+.1f}%)"

            elif position_side == 'SHORT':
                # 做空持仓 - 寻找底部信号

                # 1. 价格在最近12小时创新低后反弹
                min_low = min(k['low'] for k in recent_12)
                min_low_idx = len(recent_12) - 1 - [k['low'] for k in reversed(recent_12)].index(min_low)
                is_bottom = min_low_idx < 10  # 低点在前10根K线，现在反弹

                # 2. 当前价格已经从低点反弹（1h级别阈值提高到1.5%）
                current_price = current['close']
                bounce_pct = (current_price - min_low) / min_low * 100

                # 3. 最近4根1h K线趋势确认：至少3根收阳或长下影线
                recent_4 = klines_1h[-4:]
                bullish_count = sum(1 for k in recent_4 if k['close'] > k['open'])
                long_lower_shadow = sum(1 for k in recent_4 if (min(k['open'], k['close']) - k['low']) > abs(k['close'] - k['open']) * 1.5)

                # 4. 成交量确认：最近3根K线成交量放大
                if len(recent_24) >= 24:
                    avg_volume_24h = sum(k['volume'] for k in recent_24[:21]) / 21
                    recent_3_volume = sum(k['volume'] for k in klines_1h[-3:]) / 3
                    volume_surge = recent_3_volume > avg_volume_24h * 1.2
                else:
                    volume_surge = True  # 数据不足时忽略成交量确认

                # 见底判断条件（更严格）
                if is_bottom and bounce_pct >= 1.5 and (bullish_count >= 3 or long_lower_shadow >= 2):
                    # 计算当前盈利
                    profit_pct = (entry_price - current_price) / entry_price * 100
                    return True, f"BOTTOM_DETECTED(低点反弹{bounce_pct:.1f}%,盈利{profit_pct:+.1f}%)"

            return False, None

        except Exception as e:
            logger.error(f"[ERROR] {symbol} 顶底识别失败: {e}")
            return False, None

    # ========== 以下方法已废弃，平仓逻辑已统一到SmartExitOptimizer ==========
    # check_stop_loss_take_profit() 和 close_old_positions() 已被移除
    # 所有平仓逻辑现在由 SmartExitOptimizer 统一处理


    def check_hedge_positions(self):
        """检查并处理对冲持仓 - 平掉亏损方向"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)  # 使用字典游标

            # 1. 找出所有存在对冲的交易对
            cursor.execute("""
                SELECT
                    symbol,
                    SUM(CASE WHEN position_side = 'LONG' THEN 1 ELSE 0 END) as long_count,
                    SUM(CASE WHEN position_side = 'SHORT' THEN 1 ELSE 0 END) as short_count
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
                GROUP BY symbol
                HAVING long_count > 0 AND short_count > 0
            """, (self.account_id,))

            hedge_pairs = cursor.fetchall()

            if not hedge_pairs:
                return

            logger.info(f"[HEDGE] 发现 {len(hedge_pairs)} 个对冲交易对")

            # 2. 处理每个对冲交易对
            for pair in hedge_pairs:
                symbol = pair['symbol']

                # 获取该交易对的所有持仓
                cursor.execute("""
                    SELECT id, position_side, entry_price, quantity, open_time
                    FROM futures_positions
                    WHERE symbol = %s AND status = 'open' AND account_id = %s
                    ORDER BY position_side, open_time
                """, (symbol, self.account_id))

                positions = cursor.fetchall()

                if len(positions) < 2:
                    continue

                # 获取当前价格
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                # 计算每个持仓的盈亏
                long_positions = []
                short_positions = []

                for pos in positions:
                    entry_price = float(pos['entry_price'])
                    quantity = float(pos['quantity'])

                    if pos['position_side'] == 'LONG':
                        pnl_pct = (current_price - entry_price) / entry_price * 100
                        realized_pnl = (current_price - entry_price) * quantity
                        long_positions.append({
                            'id': pos['id'],
                            'entry_price': entry_price,
                            'quantity': quantity,
                            'pnl_pct': pnl_pct,
                            'realized_pnl': realized_pnl,
                            'open_time': pos['open_time']
                        })
                    else:  # SHORT
                        pnl_pct = (entry_price - current_price) / entry_price * 100
                        realized_pnl = (entry_price - current_price) * quantity
                        short_positions.append({
                            'id': pos['id'],
                            'entry_price': entry_price,
                            'quantity': quantity,
                            'pnl_pct': pnl_pct,
                            'realized_pnl': realized_pnl,
                            'open_time': pos['open_time']
                        })

                # 策略1: 如果一方亏损>1%且另一方盈利,平掉亏损方
                for long_pos in long_positions:
                    for short_pos in short_positions:
                        # LONG亏损>1%, SHORT盈利 -> 平掉LONG
                        if long_pos['pnl_pct'] < -1 and short_pos['pnl_pct'] > 0:
                            logger.info(
                                f"[HEDGE_CLOSE] {symbol} LONG亏损{long_pos['pnl_pct']:.2f}% ({long_pos['realized_pnl']:+.2f} USDT), "
                                f"SHORT盈利{short_pos['pnl_pct']:.2f}% -> 平掉LONG"
                            )

                            # Get leverage and margin
                            cursor.execute("""
                                SELECT leverage, margin FROM futures_positions WHERE id = %s
                            """, (long_pos['id'],))
                            pos_detail = cursor.fetchone()
                            leverage = pos_detail['leverage'] if pos_detail else 1
                            margin = float(pos_detail['margin']) if pos_detail else 0.0
                            roi = (long_pos['realized_pnl'] / margin) * 100 if margin > 0 else 0

                            cursor.execute("""
                                UPDATE futures_positions
                                SET status = 'closed', mark_price = %s,
                                    realized_pnl = %s,
                                    close_time = NOW(), updated_at = NOW(),
                                    notes = CONCAT(IFNULL(notes, ''), '|hedge_loss_cut')
                                WHERE id = %s
                            """, (current_price, long_pos['realized_pnl'], long_pos['id']))

                            # Calculate values for orders and trades
                            import uuid
                            notional_value = current_price * long_pos['quantity']
                            fee = notional_value * 0.0004
                            order_id = f"HEDGE-{long_pos['id']}"
                            trade_id = str(uuid.uuid4())

                            # Create futures_orders record for close reason
                            cursor.execute("""
                                INSERT INTO futures_orders (
                                    account_id, order_id, position_id, symbol,
                                    side, order_type, leverage,
                                    price, quantity, executed_quantity,
                                    total_value, executed_value,
                                    fee, fee_rate, status,
                                    avg_fill_price, fill_time,
                                    realized_pnl, pnl_pct,
                                    order_source, notes
                                ) VALUES (
                                    %s, %s, %s, %s,
                                    %s, 'MARKET', %s,
                                    %s, %s, %s,
                                    %s, %s,
                                    %s, %s, 'FILLED',
                                    %s, %s,
                                    %s, %s,
                                    'smart_trader', %s
                                )
                            """, (
                                self.account_id, order_id, long_pos['id'], symbol,
                                'CLOSE_LONG', leverage,
                                current_price, long_pos['quantity'], long_pos['quantity'],
                                notional_value, notional_value,
                                fee, 0.0004,
                                current_price, datetime.utcnow(),
                                long_pos['realized_pnl'], long_pos['pnl_pct'], '对冲止损平仓'
                            ))

                            # Create futures_trades record for frontend display
                            cursor.execute("""
                                INSERT INTO futures_trades (
                                    trade_id, position_id, account_id, symbol, side,
                                    price, quantity, notional_value, leverage, margin,
                                    fee, realized_pnl, pnl_pct, roi, entry_price,
                                    close_price, order_id, trade_time, created_at
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s
                                )
                            """, (
                                trade_id, long_pos['id'], self.account_id, symbol, 'CLOSE_LONG',
                                current_price, long_pos['quantity'], notional_value, leverage, margin,
                                fee, long_pos['realized_pnl'], long_pos['pnl_pct'], roi, long_pos['entry_price'],
                                current_price, f"HEDGE-{long_pos['id']}", datetime.utcnow(), datetime.utcnow()
                            ))

                            # Update account balance
                            cursor.execute("""
                                UPDATE futures_trading_accounts
                                SET current_balance = current_balance + %s + %s,
                                    frozen_balance = frozen_balance - %s,
                                    realized_pnl = realized_pnl + %s,
                                    total_trades = total_trades + 1,
                                    winning_trades = winning_trades + IF(%s > 0, 1, 0),
                                    losing_trades = losing_trades + IF(%s < 0, 1, 0)
                                WHERE id = %s
                            """, (
                                float(margin), float(long_pos['realized_pnl']), float(margin),
                                float(long_pos['realized_pnl']), float(long_pos['realized_pnl']), float(long_pos['realized_pnl']),
                                self.account_id
                            ))

                            cursor.execute("""
                                UPDATE futures_trading_accounts
                                SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                                WHERE id = %s
                            """, (self.account_id,))

                        # SHORT亏损>1%, LONG盈利 -> 平掉SHORT
                        elif short_pos['pnl_pct'] < -1 and long_pos['pnl_pct'] > 0:
                            logger.info(
                                f"[HEDGE_CLOSE] {symbol} SHORT亏损{short_pos['pnl_pct']:.2f}% ({short_pos['realized_pnl']:+.2f} USDT), "
                                f"LONG盈利{long_pos['pnl_pct']:.2f}% -> 平掉SHORT"
                            )

                            # Get leverage and margin
                            cursor.execute("""
                                SELECT leverage, margin FROM futures_positions WHERE id = %s
                            """, (short_pos['id'],))
                            pos_detail = cursor.fetchone()
                            leverage = pos_detail['leverage'] if pos_detail else 1
                            margin = float(pos_detail['margin']) if pos_detail else 0.0
                            roi = (short_pos['realized_pnl'] / margin) * 100 if margin > 0 else 0

                            cursor.execute("""
                                UPDATE futures_positions
                                SET status = 'closed', mark_price = %s,
                                    realized_pnl = %s,
                                    close_time = NOW(), updated_at = NOW(),
                                    notes = CONCAT(IFNULL(notes, ''), '|hedge_loss_cut')
                                WHERE id = %s
                            """, (current_price, short_pos['realized_pnl'], short_pos['id']))

                            # Calculate values for orders and trades
                            import uuid
                            notional_value = current_price * short_pos['quantity']
                            fee = notional_value * 0.0004
                            order_id = f"HEDGE-{short_pos['id']}"
                            trade_id = str(uuid.uuid4())

                            # Create futures_orders record for close reason
                            cursor.execute("""
                                INSERT INTO futures_orders (
                                    account_id, order_id, position_id, symbol,
                                    side, order_type, leverage,
                                    price, quantity, executed_quantity,
                                    total_value, executed_value,
                                    fee, fee_rate, status,
                                    avg_fill_price, fill_time,
                                    realized_pnl, pnl_pct,
                                    order_source, notes
                                ) VALUES (
                                    %s, %s, %s, %s,
                                    %s, 'MARKET', %s,
                                    %s, %s, %s,
                                    %s, %s,
                                    %s, %s, 'FILLED',
                                    %s, %s,
                                    %s, %s,
                                    'smart_trader', %s
                                )
                            """, (
                                self.account_id, order_id, short_pos['id'], symbol,
                                'CLOSE_SHORT', leverage,
                                current_price, short_pos['quantity'], short_pos['quantity'],
                                notional_value, notional_value,
                                fee, 0.0004,
                                current_price, datetime.utcnow(),
                                short_pos['realized_pnl'], short_pos['pnl_pct']
                            ))

                            # Create futures_trades record for frontend display
                            cursor.execute("""
                                INSERT INTO futures_trades (
                                    trade_id, position_id, account_id, symbol, side,
                                    price, quantity, notional_value, leverage, margin,
                                    fee, realized_pnl, pnl_pct, roi, entry_price,
                                    close_price, order_id, trade_time, created_at
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s
                                )
                            """, (
                                trade_id, short_pos['id'], self.account_id, symbol, 'CLOSE_SHORT',
                                current_price, short_pos['quantity'], notional_value, leverage, margin,
                                fee, short_pos['realized_pnl'], short_pos['pnl_pct'], roi, short_pos['entry_price'],
                                current_price, order_id, datetime.utcnow(), datetime.utcnow()
                            ))

                            # Update account balance
                            cursor.execute("""
                                UPDATE futures_trading_accounts
                                SET current_balance = current_balance + %s + %s,
                                    frozen_balance = frozen_balance - %s,
                                    realized_pnl = realized_pnl + %s,
                                    total_trades = total_trades + 1,
                                    winning_trades = winning_trades + IF(%s > 0, 1, 0),
                                    losing_trades = losing_trades + IF(%s < 0, 1, 0)
                                WHERE id = %s
                            """, (
                                float(margin), float(short_pos['realized_pnl']), float(margin),
                                float(short_pos['realized_pnl']), float(short_pos['realized_pnl']), float(short_pos['realized_pnl']),
                                self.account_id
                            ))

                            cursor.execute("""
                                UPDATE futures_trading_accounts
                                SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                                WHERE id = %s
                            """, (self.account_id,))

            cursor.close()

        except Exception as e:
            logger.error(f"[ERROR] 检查对冲持仓失败: {e}")

    def get_position_score(self, symbol: str, side: str):
        """获取持仓的开仓得分"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)  # 使用字典游标

            cursor.execute("""
                SELECT entry_signal_type FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND status = 'open' AND account_id = %s
                LIMIT 1
            """, (symbol, side, self.account_id))

            result = cursor.fetchone()
            cursor.close()

            if result and result['entry_signal_type']:
                # entry_signal_type 格式: SMART_BRAIN_30
                signal_type = result['entry_signal_type']
                if 'SMART_BRAIN_' in signal_type:
                    score = int(signal_type.split('_')[-1])
                    return score

            return 0
        except:
            return 0

    def check_recent_close(self, symbol: str, side: str, cooldown_minutes: int = 15):
        """
        检查指定交易对和方向是否在冷却期内(刚刚平仓)
        返回True表示在冷却期,不应该开仓
        默认冷却期15分钟,避免反复开平造成频繁交易
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND status = 'closed'
                  AND account_id = %s
                  AND close_time >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            """, (symbol, side, self.account_id, cooldown_minutes))

            result = cursor.fetchone()
            cursor.close()

            # 如果最近X分钟内有平仓记录,返回True(冷却中)
            return result[0] > 0 if result else False
        except:
            return False

    def _emergency_close_all_positions(self, position_side: str, reason: str):
        """
        🔥 紧急干预: 立即平掉所有指定方向的持仓

        场景: Big4同步反转时,立即平掉所有持仓,避免继续亏损

        Args:
            position_side: 'LONG' 或 'SHORT'
            reason: 平仓原因
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询所有指定方向的开仓持仓
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price
                FROM futures_positions
                WHERE status = 'open'
                AND position_side = %s
                AND account_id = %s
            """, (position_side, self.account_id))

            positions = cursor.fetchall()
            cursor.close()

            if not positions:
                logger.info(f"🔥 [EMERGENCY] 无{position_side}持仓需要平仓")
                return

            logger.critical(f"🚨 [EMERGENCY] 检测到Big4反转,立即平掉所有{position_side}持仓! 数量:{len(positions)}个")

            # 立即平掉所有持仓
            closed_count = 0
            failed_count = 0

            for pos in positions:
                symbol = pos['symbol']
                try:
                    success = self.close_position_by_side(
                        symbol=symbol,
                        side=position_side,
                        reason=f"EMERGENCY:{reason}"
                    )

                    if success:
                        closed_count += 1
                        logger.critical(f"🚨 [EMERGENCY] {symbol} {position_side}持仓已紧急平仓")
                    else:
                        failed_count += 1
                        logger.error(f"❌ [EMERGENCY] {symbol} {position_side}持仓紧急平仓失败")

                except Exception as e:
                    failed_count += 1
                    logger.error(f"❌ [EMERGENCY] {symbol} {position_side}平仓异常: {e}")

            logger.critical(f"🚨 [EMERGENCY] 紧急平仓完成! 成功:{closed_count}, 失败:{failed_count}")

        except Exception as e:
            logger.error(f"❌ [EMERGENCY] 紧急平仓流程失败: {e}", exc_info=True)

    def _check_total_loss_emergency(self) -> bool:
        """
        🔥 检查总持仓亏损是否超过阈值,触发紧急干预

        检查逻辑:
        1. 计算所有开仓持仓的总浮亏
        2. 如果总亏损 > 600 USDT,触发紧急干预
        3. 设置emergency_loss_limit_time,2小时内禁止开新仓

        返回:
            bool: True表示触发了紧急干预,False表示正常
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询所有开仓持仓
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price, avg_entry_price
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
            """, (self.account_id,))

            positions = cursor.fetchall()
            cursor.close()

            if not positions:
                return False

            # 计算总浮动盈亏
            total_pnl = 0.0

            for pos in positions:
                symbol = pos['symbol']
                position_side = pos['position_side']
                quantity = float(pos['quantity'])
                entry_price = float(pos.get('avg_entry_price') or pos['entry_price'])

                # 获取当前价格
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                # 计算浮动盈亏 (USDT)
                if position_side == 'LONG':
                    pnl = (current_price - entry_price) * quantity
                else:  # SHORT
                    pnl = (entry_price - current_price) * quantity

                total_pnl += pnl

            # 检查是否超过亏损阈值
            if total_pnl < -self.emergency_loss_threshold:
                logger.critical(
                    f"🚨 [EMERGENCY-LOSS] 总持仓亏损超过阈值! "
                    f"当前总浮亏: {total_pnl:.2f} USDT (阈值: -{self.emergency_loss_threshold} USDT) | "
                    f"持仓数量: {len(positions)}个 | "
                    f"触发紧急干预,暂停开仓{self.emergency_loss_block_hours}小时"
                )

                # 设置紧急干预标志
                import time
                self.emergency_loss_limit_time = time.time()

                return True

            return False

        except Exception as e:
            logger.error(f"❌ [EMERGENCY-LOSS] 检查总亏损失败: {e}", exc_info=True)
            return False

    def _check_stop_loss_circuit(self) -> bool:
        """
        🔥 检查最近交易是否止损过多,触发熔断机制

        检查逻辑:
        1. 查询最近N笔已平仓订单(futures_orders)
        2. 从notes字段判断是否是止损平仓(包含"止损"关键字)
        3. 如果止损笔数 >= 阈值,触发熔断
        4. 设置emergency_stop_loss_circuit_time,2小时内禁止开新仓

        返回:
            bool: True表示触发了熔断,False表示正常
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询最近N笔平仓订单（从futures_orders查询，因为notes字段包含平仓原因）
            cursor.execute("""
                SELECT
                    o.order_id, o.symbol, o.side, o.realized_pnl,
                    o.notes, o.fill_time
                FROM futures_orders o
                WHERE o.account_id = %s
                AND o.side LIKE '%%CLOSE%%'
                AND o.status = 'FILLED'
                ORDER BY o.fill_time DESC
                LIMIT %s
            """, (self.account_id, self.circuit_check_recent_trades))

            recent_trades = cursor.fetchall()
            cursor.close()

            if len(recent_trades) < self.circuit_check_recent_trades:
                # 交易笔数不足N笔,不触发熔断
                return False

            # 统计止损笔数
            stop_loss_count = 0
            stop_loss_details = []

            for trade in recent_trades:
                notes = trade.get('notes', '') or ''
                realized_pnl = float(trade.get('realized_pnl', 0))

                # 判断是否是止损平仓（notes中包含"止损"）
                is_stop_loss = '止损' in notes

                if is_stop_loss:
                    stop_loss_count += 1
                    stop_loss_details.append({
                        'symbol': trade['symbol'],
                        'side': trade['side'],
                        'pnl': realized_pnl,
                        'reason': notes[:50]  # 截取前50字符
                    })

            # 检查是否超过阈值
            if stop_loss_count >= self.circuit_stop_loss_threshold:
                details_str = ', '.join([
                    f"{d['symbol']}({d['pnl']:.1f}U)"
                    for d in stop_loss_details[:5]  # 只显示前5笔
                ])

                logger.critical(
                    f"🚨 [CIRCUIT-BREAKER] 止损熔断触发! "
                    f"最近{self.circuit_check_recent_trades}笔交易中有{stop_loss_count}笔止损 "
                    f"(阈值: {self.circuit_stop_loss_threshold}笔) | "
                    f"止损详情: {details_str} | "
                    f"触发熔断,暂停开仓{self.circuit_block_hours}小时"
                )

                # 设置熔断标志
                import time
                self.emergency_stop_loss_circuit_time = time.time()

                return True

            return False

        except Exception as e:
            logger.error(f"❌ [CIRCUIT-BREAKER] 检查止损熔断失败: {e}", exc_info=True)
            return False

    def _emergency_close_position_by_symbol_side(self, symbol: str, side: str, reason: str):
        """
        🔥 紧急平仓：破位反手时平掉指定交易对的反向持仓

        Args:
            symbol: 交易对
            side: 要平掉的持仓方向（'LONG' or 'SHORT'）
            reason: 平仓原因
        """
        try:
            # 检查是否有该方向的持仓
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            cursor.execute("""
                SELECT id FROM futures_positions
                WHERE symbol = %s
                AND position_side = %s
                AND status = 'open'
                AND account_id = %s
            """, (symbol, side, self.account_id))

            position = cursor.fetchone()
            cursor.close()

            if position:
                logger.critical(f"🔥 [BREAKOUT_REVERSE] {symbol} 检测到反向持仓{side}，立即平仓！原因: {reason}")
                success = self.close_position_by_side(symbol, side, reason)
                if success:
                    logger.critical(f"✅ [BREAKOUT_REVERSE] {symbol} {side}持仓已平仓，准备反手")
                else:
                    logger.error(f"❌ [BREAKOUT_REVERSE] {symbol} {side}持仓平仓失败")
            else:
                logger.info(f"✅ [BREAKOUT_REVERSE] {symbol} 无{side}持仓，无需平仓")

        except Exception as e:
            logger.error(f"❌ [BREAKOUT_REVERSE] {symbol} 检查反向持仓失败: {e}")

    def close_position_by_side(self, symbol: str, side: str, reason: str = "reverse_signal"):
        """关闭指定交易对和方向的持仓"""
        try:
            current_price = self.get_current_price(symbol)
            if not current_price:
                return False

            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)  # 使用字典游标

            # 获取持仓信息用于日志和计算盈亏（包含signal_type用于更新质量）
            cursor.execute("""
                SELECT id, entry_price, quantity, leverage, margin, entry_signal_type FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND status = 'open' AND account_id = %s
            """, (symbol, side, self.account_id))

            positions = cursor.fetchall()

            for pos in positions:
                entry_price = float(pos['entry_price'])
                quantity = float(pos['quantity'])
                leverage = pos['leverage'] if pos.get('leverage') else 1
                margin = float(pos['margin']) if pos.get('margin') else 0.0
                pnl_pct = (current_price - entry_price) / entry_price * 100

                # Calculate realized PnL
                if side == 'LONG':
                    realized_pnl = (current_price - entry_price) * quantity
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:  # SHORT
                    realized_pnl = (entry_price - current_price) * quantity
                    pnl_pct = (entry_price - current_price) / entry_price * 100

                roi = (realized_pnl / margin) * 100 if margin > 0 else 0

                logger.info(
                    f"[REVERSE_CLOSE] {symbol} {side} | "
                    f"开仓: ${entry_price:.4f} | 平仓: ${current_price:.4f} | "
                    f"盈亏: {pnl_pct:+.2f}% ({realized_pnl:+.2f} USDT) | 原因: {reason}"
                )

                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'closed', mark_price = %s,
                        realized_pnl = %s,
                        close_time = NOW(), updated_at = NOW(),
                        notes = CONCAT(IFNULL(notes, ''), '|', %s)
                    WHERE id = %s
                """, (current_price, realized_pnl, reason, pos['id']))

                # 🔥 新增: 更新信号质量统计（平仓后）
                if self.brain and hasattr(self.brain, 'quality_manager') and pos.get('entry_signal_type'):
                    signal_key = pos['entry_signal_type']
                    self.brain.quality_manager.update_signal_quality(signal_key, side, realized_pnl)

                # Calculate values for orders and trades
                import uuid
                close_side = 'CLOSE_LONG' if side == 'LONG' else 'CLOSE_SHORT'
                notional_value = current_price * quantity
                fee = notional_value * 0.0004
                order_id = f"REVERSE-{pos['id']}"
                trade_id = str(uuid.uuid4())

                # Create futures_orders record for close reason
                cursor.execute("""
                    INSERT INTO futures_orders (
                        account_id, order_id, position_id, symbol,
                        side, order_type, leverage,
                        price, quantity, executed_quantity,
                        total_value, executed_value,
                        fee, fee_rate, status,
                        avg_fill_price, fill_time,
                        realized_pnl, pnl_pct,
                        order_source, notes
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, 'MARKET', %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, 'FILLED',
                        %s, %s,
                        %s, %s,
                        'smart_trader', %s
                    )
                """, (
                    self.account_id, order_id, pos['id'], symbol,
                    close_side, leverage,
                    current_price, quantity, quantity,
                    notional_value, notional_value,
                    fee, 0.0004,
                    current_price, datetime.utcnow(),
                    realized_pnl, pnl_pct, reason
                ))

                # Create futures_trades record for frontend display
                cursor.execute("""
                    INSERT INTO futures_trades (
                        trade_id, position_id, account_id, symbol, side,
                        price, quantity, notional_value, leverage, margin,
                        fee, realized_pnl, pnl_pct, roi, entry_price,
                        close_price, order_id, trade_time, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s
                    )
                """, (
                    trade_id, pos['id'], self.account_id, symbol, close_side,
                    current_price, quantity, notional_value, leverage, margin,
                    fee, realized_pnl, pnl_pct, roi, entry_price,
                    current_price, order_id, datetime.utcnow(), datetime.utcnow()
                ))

                # Update account balance
                cursor.execute("""
                    UPDATE futures_trading_accounts
                    SET current_balance = current_balance + %s + %s,
                        frozen_balance = frozen_balance - %s,
                        realized_pnl = realized_pnl + %s,
                        total_trades = total_trades + 1,
                        winning_trades = winning_trades + IF(%s > 0, 1, 0),
                        losing_trades = losing_trades + IF(%s < 0, 1, 0)
                    WHERE id = %s
                """, (
                    float(margin), float(realized_pnl), float(margin),
                    float(realized_pnl), float(realized_pnl), float(realized_pnl),
                    self.account_id
                ))

                cursor.execute("""
                    UPDATE futures_trading_accounts
                    SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                    WHERE id = %s
                """, (self.account_id,))

            cursor.close()
            return True

        except Exception as e:
            logger.error(f"[ERROR] 关闭{symbol} {side}持仓失败: {e}")
            return False

    async def close_position(self, symbol: str, direction: str, position_size: float, reason: str = "smart_exit"):
        """
        异步平仓方法（供SmartExitOptimizer调用）

        Args:
            symbol: 交易对
            direction: 方向 (LONG/SHORT)
            position_size: 持仓数量
            reason: 平仓原因

        Returns:
            dict: {'success': bool, 'error': str}
        """
        try:
            # 调用同步方法执行平仓
            success = self.close_position_by_side(symbol, direction, reason)

            if success:
                return {'success': True}
            else:
                return {'success': False, 'error': 'close_position_by_side returned False'}

        except Exception as e:
            logger.error(f"异步平仓失败: {symbol} {direction} | {e}")
            return {'success': False, 'error': str(e)}

    async def close_position_partial(self, position_id: int, close_ratio: float, reason: str):
        """
        部分平仓方法（供SmartExitOptimizer调用）

        Args:
            position_id: 持仓ID
            close_ratio: 平仓比例 (0.0-1.0)
            reason: 平仓原因

        Returns:
            dict: {'success': bool, 'position_id': int, 'closed_quantity': float}
        """
        conn = None
        cursor = None
        try:
            # 创建独立连接，避免与其他异步操作冲突（重要！）
            # SmartExitOptimizer异步调用此方法时，共享连接会导致竞态条件
            conn = pymysql.connect(
                **self.db_config,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False  # 使用事务确保数据一致性
            )
            cursor = conn.cursor()

            # 获取持仓信息
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price, avg_entry_price,
                       leverage, margin, status
                FROM futures_positions
                WHERE id = %s AND status = 'open' AND account_id = %s
            """, (position_id, self.account_id))

            position = cursor.fetchone()

            if not position:
                cursor.close()
                conn.close()
                logger.error(f"持仓 {position_id} 不存在或已关闭")
                return {'success': False, 'error': 'Position not found or already closed'}

            symbol = position['symbol']
            side = position['position_side']
            total_quantity = float(position['quantity'])
            entry_price = float(position['avg_entry_price'])
            leverage = position['leverage'] if position.get('leverage') else 1
            total_margin = float(position['margin']) if position.get('margin') else 0.0

            # 计算平仓数量和保证金
            close_quantity = total_quantity * close_ratio
            close_margin = total_margin * close_ratio
            remaining_quantity = total_quantity - close_quantity
            remaining_margin = total_margin - close_margin

            # 如果剩余保证金太小(<10 USDT),直接全部平仓避免垃圾仓位
            MIN_MARGIN_THRESHOLD = 10.0
            if remaining_margin < MIN_MARGIN_THRESHOLD and remaining_margin > 0:
                logger.warning(
                    f"⚠️ 剩余保证金太小(${remaining_margin:.2f} < ${MIN_MARGIN_THRESHOLD}), "
                    f"改为全部平仓避免垃圾仓位"
                )
                close_quantity = total_quantity
                close_margin = total_margin
                remaining_quantity = 0
                remaining_margin = 0
                close_ratio = 1.0

            # 获取当前价格
            current_price = self.get_current_price(symbol)
            if not current_price:
                cursor.close()
                conn.close()
                logger.error(f"无法获取 {symbol} 当前价格")
                return {'success': False, 'error': 'Failed to get current price'}

            # 计算盈亏
            if side == 'LONG':
                realized_pnl = (current_price - entry_price) * close_quantity
                pnl_pct = (current_price - entry_price) / entry_price * 100
            else:  # SHORT
                realized_pnl = (entry_price - current_price) * close_quantity
                pnl_pct = (entry_price - current_price) / entry_price * 100

            roi = (realized_pnl / close_margin) * 100 if close_margin > 0 else 0

            logger.info(
                f"[PARTIAL_CLOSE] {symbol} {side} | 持仓{position_id} | "
                f"平仓比例: {close_ratio*100:.0f}% | 数量: {close_quantity:.4f}/{total_quantity:.4f} | "
                f"盈亏: {pnl_pct:+.2f}% ({realized_pnl:+.2f} USDT) | 原因: {reason}"
            )

            # 更新持仓记录
            if remaining_quantity <= 0.0001:  # 全部平仓
                cursor.execute("""
                    UPDATE futures_positions
                    SET quantity = 0,
                        margin = 0,
                        notional_value = 0,
                        status = 'closed',
                        close_time = NOW(),
                        realized_pnl = IFNULL(realized_pnl, 0) + %s,
                        updated_at = NOW(),
                        notes = CONCAT(IFNULL(notes, ''), '|full_close:', %s, ' (from partial_close due to small remaining)')
                    WHERE id = %s
                """, (
                    realized_pnl,
                    reason,
                    position_id
                ))
                logger.info(f"✅ 持仓{position_id}已全部平仓(剩余保证金太小)")
            else:  # 部分平仓
                cursor.execute("""
                    UPDATE futures_positions
                    SET quantity = %s,
                        margin = %s,
                        notional_value = %s,
                        realized_pnl = IFNULL(realized_pnl, 0) + %s,
                        updated_at = NOW(),
                        notes = CONCAT(IFNULL(notes, ''), '|partial_close:', %s, ',ratio:', %s)
                    WHERE id = %s
                """, (
                    remaining_quantity,
                    remaining_margin,
                    remaining_quantity * entry_price,
                    realized_pnl,
                    reason,
                    f"{close_ratio:.2f}",
                    position_id
                ))

            # 创建平仓订单记录
            import uuid
            from datetime import datetime
            close_side = 'CLOSE_LONG' if side == 'LONG' else 'CLOSE_SHORT'
            notional_value = current_price * close_quantity
            fee = notional_value * 0.0004
            # 使用时间戳确保order_id唯一性，避免重复触发时主键冲突
            timestamp = datetime.now().strftime('%H%M%S%f')[:9]  # HHMMSSMMM (毫秒)
            order_id = f"PARTIAL-{position_id}-{int(close_ratio*100)}-{timestamp}"
            trade_id = str(uuid.uuid4())

            cursor.execute("""
                INSERT INTO futures_orders (
                    account_id, order_id, position_id, symbol,
                    side, order_type, leverage,
                    price, quantity, executed_quantity,
                    total_value, executed_value,
                    fee, fee_rate, status,
                    avg_fill_price, fill_time,
                    realized_pnl, pnl_pct,
                    order_source, notes
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, 'MARKET', %s,
                    %s, %s, %s,
                    %s, %s,
                    %s, %s, 'FILLED',
                    %s, %s,
                    %s, %s,
                    'smart_exit', %s
                )
            """, (
                self.account_id, order_id, position_id, symbol,
                close_side, leverage,
                current_price, close_quantity, close_quantity,
                notional_value, notional_value,
                fee, 0.0004,
                current_price, datetime.utcnow(),
                realized_pnl, pnl_pct,
                f"partial_close_{close_ratio:.0%}:{reason}"
            ))

            # 创建交易记录
            cursor.execute("""
                INSERT INTO futures_trades (
                    trade_id, position_id, account_id, symbol, side,
                    price, quantity, notional_value, leverage, margin,
                    fee, realized_pnl, pnl_pct, roi, entry_price,
                    close_price, order_id, trade_time, created_at
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
            """, (
                trade_id, position_id, self.account_id, symbol, close_side,
                current_price, close_quantity, notional_value, leverage, close_margin,
                fee, realized_pnl, pnl_pct, roi, entry_price,
                current_price, order_id, datetime.utcnow(), datetime.utcnow()
            ))

            # 更新账户余额
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET current_balance = current_balance + %s + %s,
                    frozen_balance = frozen_balance - %s,
                    realized_pnl = realized_pnl + %s,
                    total_trades = total_trades + 1,
                    winning_trades = winning_trades + IF(%s > 0, 1, 0),
                    losing_trades = losing_trades + IF(%s < 0, 1, 0)
                WHERE id = %s
            """, (
                float(close_margin), float(realized_pnl), float(close_margin),
                float(realized_pnl), float(realized_pnl), float(realized_pnl),
                self.account_id
            ))

            cursor.execute("""
                UPDATE futures_trading_accounts
                SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                WHERE id = %s
            """, (self.account_id,))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 部分平仓成功: 持仓{position_id} | 剩余数量: {remaining_quantity:.4f}")

            return {
                'success': True,
                'position_id': position_id,
                'closed_quantity': close_quantity,
                'remaining_quantity': remaining_quantity,
                'realized_pnl': realized_pnl
            }

        except Exception as e:
            logger.error(f"部分平仓失败: 持仓{position_id} | {e}")
            import traceback
            logger.error(traceback.format_exc())

            # 确保回滚事务并关闭连接
            try:
                if conn:
                    conn.rollback()
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
            except Exception as cleanup_error:
                logger.error(f"清理连接时出错: {cleanup_error}")

            return {'success': False, 'error': str(e)}

    def run_adaptive_optimization(self):
        """运行自适应优化 - 每日定时任务"""
        try:
            logger.info("=" * 80)
            logger.info("🧠 开始运行自适应优化...")
            logger.info("=" * 80)

            # 生成24小时优化报告
            report = self.optimizer.generate_optimization_report(hours=24)

            # 打印报告
            self.optimizer.print_report(report)

            # 检查是否有高严重性问题
            high_severity_count = report['summary']['high_severity_issues']

            if high_severity_count > 0:
                logger.warning(f"🔴 发现 {high_severity_count} 个高严重性问题!")
                # TODO: 发送Telegram通知 (需要集成telegram bot)

            # 自动应用优化 (黑名单 + 参数调整)
            if report['blacklist_candidates'] or report['problematic_signals']:
                logger.info(f"📝 准备应用优化:")
                if report['blacklist_candidates']:
                    logger.info(f"   🚫 黑名单候选: {len(report['blacklist_candidates'])} 个")
                if report['problematic_signals']:
                    logger.info(f"   ⚙️  问题信号: {len(report['problematic_signals'])} 个")

                # 自动应用优化 (包括参数调整和权重调整)
                results = self.optimizer.apply_optimizations(report, auto_apply=True, apply_params=True, apply_weights=True)

                if results['blacklist_added']:
                    logger.info(f"✅ 自动添加 {len(results['blacklist_added'])} 个交易对到黑名单")
                    for item in results['blacklist_added']:
                        logger.info(f"   ➕ {item['symbol']} - {item['reason']}")

                if results['params_updated']:
                    logger.info(f"✅ 自动调整 {len(results['params_updated'])} 个参数")
                    for update in results['params_updated']:
                        logger.info(f"   📊 {update}")

                if results.get('weights_adjusted'):
                    logger.info(f"✅ 自动调整 {len(results['weights_adjusted'])} 个评分权重")

                # 重新加载配置以应用所有更新
                if results['blacklist_added'] or results['params_updated'] or results.get('weights_adjusted'):
                    whitelist_count = self.brain.reload_config()
                    logger.info(f"🔄 配置已重新加载，当前可交易: {whitelist_count} 个币种")

                if results['warnings']:
                    logger.warning("⚠️ 优化警告:")
                    for warning in results['warnings']:
                        logger.warning(f"   {warning}")
            else:
                logger.info("✅ 无需加入黑名单的交易对")

            logger.info("=" * 80)
            logger.info("🧠 自适应优化完成")
            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"❌ 自适应优化失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def check_and_run_daily_optimization(self):
        """检查是否需要运行每日优化 (凌晨2点)"""
        try:
            now = datetime.utcnow()
            current_date = now.date()

            # 检查是否是凌晨2点且今天还没运行过
            if now.hour == 2 and self.last_optimization_date != current_date:
                logger.info(f"⏰ 触发每日自适应优化 (时间: {now.strftime('%Y-%m-%d %H:%M:%S')})")

                # 1. 运行原有的自适应优化 (参数调整)
                self.run_adaptive_optimization()

                # 2. 问题2优化: 更新交易对评级
                logger.info("=" * 80)
                logger.info("🏆 开始更新交易对评级 (3级黑名单制度)")
                logger.info("=" * 80)
                rating_results = self.rating_manager.update_all_symbol_ratings()
                self.rating_manager.print_rating_report(rating_results)

                # 3. 问题4优化: 更新波动率配置 (15M K线动态止盈)
                logger.info("=" * 80)
                logger.info("📊 开始更新波动率配置 (15M K线动态止盈)")
                logger.info("=" * 80)
                volatility_results = self.volatility_updater.update_all_symbols_volatility(self.brain.whitelist)
                self.volatility_updater.print_volatility_report(volatility_results)

                self.last_optimization_date = current_date

        except Exception as e:
            logger.error(f"检查每日优化失败: {e}")

    async def init_ws_service(self):
        """初始化 WebSocket 价格服务"""
        try:
            # 启动 WebSocket 服务并订阅所有白名单币种
            if not self.ws_service.is_running():
                logger.info(f"🚀 初始化 WebSocket 价格服务，订阅 {len(self.brain.whitelist)} 个币种")
                asyncio.create_task(self.ws_service.start(self.brain.whitelist))
                await asyncio.sleep(3)  # 等待连接建立

                # 检查连接状态
                if self.ws_service.is_running():
                    logger.info("✅ WebSocket 价格服务已启动")
                else:
                    logger.warning("⚠️ WebSocket 价格服务启动失败，将使用数据库价格")
        except Exception as e:
            logger.error(f"WebSocket 服务初始化失败: {e}，将使用数据库价格")

    async def _start_smart_exit_monitoring(self):
        """为所有已开仓的持仓启动统一智能平仓监控（包括普通持仓和分批建仓持仓）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询所有开仓持仓（不再区分是否分批建仓，统一由SmartExitOptimizer管理）
            cursor.execute("""
                SELECT id, symbol, position_side
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
            """, (self.account_id,))

            positions = cursor.fetchall()
            cursor.close()

            for pos in positions:
                position_id, symbol, side = pos
                await self.smart_exit_optimizer.start_monitoring_position(position_id)
                logger.info(f"✅ 启动智能平仓监控: 持仓{position_id} {symbol} {side}")

            logger.info(f"✅ 智能平仓监控已启动，统一监控 {len(positions)} 个持仓")

        except Exception as e:
            logger.error(f"❌ 启动智能平仓监控失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _check_and_restart_smart_exit_optimizer(self):
        """检查SmartExitOptimizer健康状态，发现问题立即重启"""
        try:
            if not self.smart_exit_optimizer or not self.event_loop:
                logger.warning("⚠️ SmartExitOptimizer未初始化")
                return

            # ========== 检查1: 监控任务数量是否匹配 ==========
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取数据库中的开仓持仓数量
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
            """, (self.account_id,))

            db_count = cursor.fetchone()[0]

            # 获取SmartExitOptimizer中的监控任务数量
            monitoring_count = len(self.smart_exit_optimizer.monitoring_tasks)

            # ========== 检查2: 是否有超时未平仓的持仓 ==========
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
                AND timeout_at IS NOT NULL
                AND NOW() > timeout_at
            """, (self.account_id,))

            timeout_count = cursor.fetchone()[0]

            cursor.close()

            # ========== 判断是否需要重启 ==========
            need_restart = False
            restart_reason = ""

            # 情况1: 监控任务数量不匹配
            if db_count != monitoring_count:
                need_restart = True
                restart_reason = (
                    f"监控任务数量不匹配 (数据库{db_count}个持仓, "
                    f"SmartExitOptimizer监控{monitoring_count}个)"
                )

            # 情况2: 有超时持仓（说明SmartExitOptimizer没有正常工作）
            if timeout_count > 0:
                need_restart = True
                if restart_reason:
                    restart_reason += f"; 发现{timeout_count}个超时未平仓持仓"
                else:
                    restart_reason = f"发现{timeout_count}个超时未平仓持仓"

            # ========== 执行重启 ==========
            if need_restart:
                logger.error(
                    f"❌ SmartExitOptimizer异常: {restart_reason}\n"
                    f"   立即重启SmartExitOptimizer..."
                )

                # 发送告警
                if hasattr(self, 'telegram_notifier') and self.telegram_notifier:
                    try:
                        self.telegram_notifier.send_message(
                            f"⚠️ SmartExitOptimizer自动重启\n\n"
                            f"原因: {restart_reason}\n"
                            f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"操作: 正在重启监控..."
                        )
                    except Exception as e:
                        logger.warning(f"发送Telegram告警失败: {e}")

                # 重启SmartExitOptimizer的监控
                asyncio.run_coroutine_threadsafe(
                    self._restart_smart_exit_monitoring(),
                    self.event_loop
                )

                logger.info("✅ SmartExitOptimizer重启完成")

            else:
                # 正常情况，偶尔打印健康状态
                if datetime.now().minute % 10 == 0:  # 每10分钟打印一次
                    logger.debug(
                        f"💓 SmartExitOptimizer健康检查: "
                        f"{monitoring_count}个持仓监控中, "
                        f"{timeout_count}个超时持仓"
                    )

        except Exception as e:
            logger.error(f"SmartExitOptimizer健康检查失败: {e}")

    async def _restart_smart_exit_monitoring(self):
        """重启SmartExitOptimizer监控"""
        try:
            logger.info("========== 重启SmartExitOptimizer监控 ==========")

            # 1. 取消所有现有监控任务
            if self.smart_exit_optimizer and self.smart_exit_optimizer.monitoring_tasks:
                logger.info(f"取消 {len(self.smart_exit_optimizer.monitoring_tasks)} 个现有监控任务...")

                for position_id, task in list(self.smart_exit_optimizer.monitoring_tasks.items()):
                    try:
                        task.cancel()
                        logger.debug(f"  取消监控任务: 持仓{position_id}")
                    except Exception as e:
                        logger.warning(f"  取消任务失败: 持仓{position_id} | {e}")

                # 等待任务取消
                await asyncio.sleep(1)

                # 清空监控任务字典
                self.smart_exit_optimizer.monitoring_tasks.clear()
                logger.info("✅ 已清空所有监控任务")

            # 2. 重新启动所有持仓的监控
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, symbol, position_side, planned_close_time
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
                ORDER BY id ASC
            """, (self.account_id,))

            positions = cursor.fetchall()
            cursor.close()

            logger.info(f"发现 {len(positions)} 个开仓持仓需要监控")

            success_count = 0
            fail_count = 0

            for pos in positions:
                position_id, symbol, side, planned_close = pos
                try:
                    await self.smart_exit_optimizer.start_monitoring_position(position_id)

                    planned_str = planned_close.strftime('%H:%M') if planned_close else 'None'
                    logger.info(
                        f"✅ [{success_count+1}/{len(positions)}] 重启监控: "
                        f"持仓{position_id} {symbol} {side} | "
                        f"计划平仓: {planned_str}"
                    )
                    success_count += 1

                except Exception as e:
                    logger.error(f"❌ 重启监控失败: 持仓{position_id} {symbol} | {e}")
                    fail_count += 1

            logger.info(
                f"========== 监控重启完成: 成功{success_count}, 失败{fail_count} =========="
            )

            # 3. 发送完成通知
            if hasattr(self, 'telegram_notifier') and self.telegram_notifier:
                try:
                    self.telegram_notifier.send_message(
                        f"✅ SmartExitOptimizer重启完成\n\n"
                        f"成功: {success_count}个持仓\n"
                        f"失败: {fail_count}个持仓\n"
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                except Exception as e:
                    logger.warning(f"发送Telegram通知失败: {e}")

        except Exception as e:
            logger.error(f"❌ 重启SmartExitOptimizer失败: {e}")
            import traceback
            logger.error(traceback.format_exc())

            # 发送失败告警
            if hasattr(self, 'telegram_notifier') and self.telegram_notifier:
                try:
                    self.telegram_notifier.send_message(
                        f"❌ SmartExitOptimizer重启失败\n\n"
                        f"错误: {str(e)}\n"
                        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"请手动检查服务状态"
                    )
                except Exception as e:
                    logger.warning(f"发送Telegram失败告警失败: {e}")

    def run(self):
        """主循环"""
        last_smart_exit_check = datetime.now()

        while self.running:
            try:
                # 0. 检查是否需要运行每日自适应优化 (凌晨2点)
                self.check_and_run_daily_optimization()

                # 注意：止盈止损、超时检查已统一迁移到SmartExitOptimizer
                # 1. [已停用] 检查止盈止损 -> 由SmartExitOptimizer处理
                # self.check_stop_loss_take_profit()

                # 2. 检查对冲持仓(平掉亏损方向)
                self.check_hedge_positions()

                # 3. [已停用] 关闭超时持仓 -> 由SmartExitOptimizer处理
                # self.close_old_positions()

                # 3.5. SmartExitOptimizer健康检查和自动重启（每分钟检查）
                now = datetime.now()
                if (now - last_smart_exit_check).total_seconds() >= 60:
                    self._check_and_restart_smart_exit_optimizer()
                    last_smart_exit_check = now

                # 4. 🔥 紧急干预: 检查总亏损是否超过阈值
                if self._check_total_loss_emergency():
                    logger.critical(f"🚨 [EMERGENCY-LOSS] 总亏损超过{self.emergency_loss_threshold}U,暂停开仓{self.emergency_loss_block_hours}小时")
                    time.sleep(self.scan_interval)
                    continue

                # 4.5. 🔥 熔断机制: 检查最近交易止损是否过多
                if self._check_stop_loss_circuit():
                    logger.critical(f"🚨 [CIRCUIT-BREAKER] 止损熔断触发,暂停开仓{self.circuit_block_hours}小时")
                    time.sleep(self.scan_interval)
                    continue

                # 5. 检查持仓
                current_positions = self.get_open_positions_count()
                logger.info(f"[STATUS] 持仓: {current_positions}/{self.max_positions}")

                if current_positions >= self.max_positions:
                    logger.info("[SKIP] 已达最大持仓,跳过扫描")
                    time.sleep(self.scan_interval)
                    continue

                # 5. 获取当前交易模式并扫描机会
                try:
                    mode_config = self.mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
                    current_mode = mode_config['mode_type'] if mode_config else 'trend'
                except Exception as e:
                    logger.error(f"[MODE-ERROR] 获取交易模式失败: {e}, 使用默认趋势模式")
                    current_mode = 'trend'

                logger.info(f"[SCAN] 模式:{current_mode} | 扫描 {len(self.brain.whitelist)} 个币种...")

                # 根据模式选择策略
                if current_mode == 'range':
                    # 🔥 震荡模式: 完全停止交易,只做趋势
                    logger.info(f"[RANGE-MODE] 🛑 震荡市场,停止开仓,只做趋势交易")
                    opportunities = []

                    # 注释掉原震荡策略,保留代码供未来参考
                    # big4_result = self.get_big4_result()
                    # big4_signal = big4_result.get('overall_signal', 'NEUTRAL')
                    # for symbol in self.brain.whitelist:
                    #     signal = self.range_reversal_strategy.generate_signal(symbol, big4_signal)
                    #     if not signal:
                    #         signal = self.bollinger_strategy.generate_signal(symbol, big4_signal, '15m')

                    logger.info(f"[RANGE-SCAN] 震荡模式,跳过扫描,等待趋势")
                else:
                    # 趋势模式: 使用原有策略
                    opportunities = self.brain.scan_all()
                    logger.info(f"[TREND-SCAN] 趋势模式扫描完成, 找到 {len(opportunities)} 个机会")

                if not opportunities:
                    logger.info("[SCAN] 无交易机会")
                    time.sleep(self.scan_interval)
                    continue

                # 5.5. 检查交易控制开关
                if not self.check_trading_enabled():
                    logger.info("[TRADING-DISABLED] ⏸️ U本位合约交易已停止，跳过开仓（不影响已有持仓）")
                    time.sleep(self.scan_interval)
                    continue

                # 5.6. 🔥 统一检测Big4反转（避免重复检测）
                big4_bottom_blocked = False
                big4_top_blocked = False

                # 检查是否有做空机会
                has_short_opportunities = any(opp['side'] == 'SHORT' for opp in opportunities)
                if has_short_opportunities:
                    should_block, reversal_reason = self.brain.detect_big4_bottom_reversal('SHORT')
                    if should_block:
                        big4_bottom_blocked = True
                        logger.warning(f"🚫 [BIG4-BOTTOM] {reversal_reason}, 过滤所有SHORT机会")

                # 检查是否有做多机会
                has_long_opportunities = any(opp['side'] == 'LONG' for opp in opportunities)
                if has_long_opportunities:
                    should_block, reversal_reason = self.brain.detect_big4_top_reversal('LONG')
                    if should_block:
                        big4_top_blocked = True
                        logger.warning(f"🚫 [BIG4-TOP] {reversal_reason}, 过滤所有LONG机会")

                # 6. 执行交易
                logger.info(f"[EXECUTE] 找到 {len(opportunities)} 个机会")

                for opp in opportunities:
                    if self.get_open_positions_count() >= self.max_positions:
                        break

                    symbol = opp['symbol']
                    new_side = opp['side']
                    new_score = opp['score']
                    opposite_side = 'SHORT' if new_side == 'LONG' else 'LONG'

                    # 🔥 Big4反转过滤: 统一检测结果
                    if big4_bottom_blocked and new_side == 'SHORT':
                        logger.info(f"[BIG4-FILTER] {symbol} SHORT被Big4底部反转阻止，跳过")
                        continue
                    if big4_top_blocked and new_side == 'LONG':
                        logger.info(f"[BIG4-FILTER] {symbol} LONG被Big4顶部反转阻止，跳过")
                        continue

                    # ========== 交易模式检查和自动切换（安全版） ==========
                    try:
                        big4_result = self.get_big4_result()
                        big4_signal = big4_result.get('overall_signal', 'NEUTRAL')
                        big4_strength = big4_result.get('signal_strength', 0)

                        # 使用安全模式切换器进行检查
                        switch_result = self.safe_mode_switcher.safe_auto_switch_check(
                            account_id=self.account_id,
                            trading_type='usdt_futures',
                            big4_signal=big4_signal,
                            big4_strength=big4_strength
                        )

                        if switch_result:
                            suggested_mode = switch_result['suggested_mode']
                            reason = switch_result['reason']
                            safety_checks = switch_result['safety_checks']

                            logger.info(f"🔄 [SAFE-MODE-SWITCH] Big4={big4_signal}({big4_strength:.1f})")
                            logger.info(f"   安全检查: 持仓={safety_checks['open_positions']}, "
                                      f"冷却={safety_checks['cooldown_passed']}, "
                                      f"确认={safety_checks['confirmation_passed']}")
                            logger.info(f"   建议切换: {safety_checks['current_mode']} → {suggested_mode}")

                            # 执行切换
                            self.safe_mode_switcher.switch_mode(
                                account_id=self.account_id,
                                trading_type='usdt_futures',
                                new_mode=suggested_mode,
                                trigger='auto',
                                reason=reason,
                                big4_signal=big4_signal,
                                big4_strength=big4_strength,
                                switched_by='safe_mode_switcher'
                            )

                        # 获取当前交易模式
                        current_mode_config = self.safe_mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
                        current_mode = current_mode_config['mode_type'] if current_mode_config else 'trend'

                        logger.info(f"📊 [TRADING-MODE] 当前模式: {current_mode} | Big4: {big4_signal}({big4_strength:.1f})")

                    except Exception as e:
                        logger.error(f"[MODE-CHECK-ERROR] 模式检查失败: {e}")
                        import traceback
                        logger.error(traceback.format_exc())
                        # 降级策略：保持当前模式，不强制切换
                        current_mode_config = self.safe_mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
                        current_mode = current_mode_config['mode_type'] if current_mode_config else 'trend'
                    # ========== 模式检查结束 ==========

                    # Big4 趋势检测 - 应用到所有币种
                    try:
                        # 如果是四大天王本身,使用该币种的专属信号
                        if symbol in self.big4_symbols:
                            symbol_detail = big4_result['details'].get(symbol, {})
                            symbol_signal = symbol_detail.get('signal', 'NEUTRAL')
                            signal_strength = symbol_detail.get('strength', 0)
                            logger.info(f"[BIG4-SELF] {symbol} 自身趋势: {symbol_signal} (强度: {signal_strength})")
                        else:
                            # 对其他币种,使用Big4整体趋势信号
                            symbol_signal = big4_result.get('overall_signal', 'NEUTRAL')
                            signal_strength = big4_result.get('signal_strength', 0)
                            logger.info(f"[BIG4-MARKET] {symbol} 市场整体趋势: {symbol_signal} (强度: {signal_strength:.1f})")

                        # ========== 震荡市过滤: 已在模式选择处完全禁止,这里无需额外处理 ==========
                        # 注: 震荡模式(range)下已经不会产生任何交易机会
                        # 如果走到这里,说明当前是趋势模式,允许正常交易

                        # 如果信号方向与交易方向冲突,降低评分或跳过
                        # 🔥 修复BUG: 任何方向冲突都应该直接跳过,不管强度高低
                        # 原因: 市场看多时开空单/市场看空时开多单都是反市场行为,风险极高
                        if symbol_signal == 'BEARISH' and new_side == 'LONG':
                            logger.info(f"[BIG4-SKIP] {symbol} 市场看空(强度{signal_strength:.1f}), 跳过LONG信号 (原评分{new_score})")
                            continue

                        elif symbol_signal == 'BULLISH' and new_side == 'SHORT':
                            logger.info(f"[BIG4-SKIP] {symbol} 市场看多(强度{signal_strength:.1f}), 跳过SHORT信号 (原评分{new_score})")
                            continue

                        # 如果信号方向一致,提升评分
                        elif symbol_signal == 'BULLISH' and new_side == 'LONG':
                            boost = min(20, int(signal_strength * 0.3))  # 最多提升20分
                            new_score = new_score + boost
                            logger.info(f"[BIG4-BOOST] {symbol} 市场看多与LONG方向一致, 评分提升: {opp['score']} -> {new_score} (+{boost})")

                        elif symbol_signal == 'BEARISH' and new_side == 'SHORT':
                            boost = min(20, int(signal_strength * 0.3))  # 最多提升20分
                            new_score = new_score + boost
                            logger.info(f"[BIG4-BOOST] {symbol} 市场看空与SHORT方向一致, 评分提升: {opp['score']} -> {new_score} (+{boost})")

                        # 更新机会评分 (用于后续记录)
                        opp['score'] = new_score
                        opp['big4_adjusted'] = True
                        opp['big4_signal'] = symbol_signal
                        opp['big4_strength'] = signal_strength

                    except Exception as e:
                        logger.error(f"[BIG4-ERROR] {symbol} Big4检测失败: {e}")
                        # 失败不影响正常交易流程

                    # 检查同方向是否已有持仓
                    if self.has_position(symbol, new_side):
                        logger.info(f"[SKIP] {symbol} {new_side}方向已有持仓")
                        continue

                    # 检查是否刚刚平仓(1小时冷却期)
                    if self.check_recent_close(symbol, new_side, cooldown_minutes=15):
                        logger.info(f"[SKIP] {symbol} {new_side}方向1小时内刚平仓,冷却中")
                        continue

                    # 检查是否有反向持仓 - 如果有则跳过,不做对冲
                    if self.has_position(symbol, opposite_side):
                        logger.info(f"[SKIP] {symbol} 已有{opposite_side}持仓,跳过{new_side}信号(不做对冲)")
                        continue

                    # 正常开仓
                    self.open_position(opp)
                    time.sleep(2)

                # 7. 等待
                logger.info(f"[WAIT] {self.scan_interval}秒后下一轮...")
                time.sleep(self.scan_interval)

            except KeyboardInterrupt:
                logger.info("[EXIT] 收到停止信号")
                self.running = False
                break
            except Exception as e:
                logger.error(f"[ERROR] 主循环异常: {e}")
                time.sleep(60)

        logger.info("[STOP] 服务已停止")


async def async_main():
    """异步主函数"""
    service = SmartTraderService()

    # 保存事件循环引用，供分批建仓使用
    service.event_loop = asyncio.get_event_loop()

    # 初始化 WebSocket 服务
    await service.init_ws_service()

    # 恢复未完成的分批建仓任务（系统重启后）
    if service.smart_entry_executor:
        logger.info("🔄 检查并恢复未完成的分批建仓任务...")
        await service.smart_entry_executor.recover_building_positions()

    # 初始化智能平仓监控（为所有已开仓的分批建仓持仓启动监控）
    if service.smart_exit_optimizer:
        await service._start_smart_exit_monitoring()

    # 在事件循环中运行同步的主循环
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, service.run)


if __name__ == '__main__':
    try:
        # 运行异步主函数
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
