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
from app.services.smart_exit_optimizer import SmartExitOptimizer
from app.services.smart_entry_executor import SmartEntryExecutor
from app.services.kline_pullback_entry_executor import KlinePullbackEntryExecutor
from app.services.big4_trend_detector import Big4TrendDetector
from app.services.breakout_signal_booster import BreakoutSignalBooster
from app.services.signal_blacklist_checker import SignalBlacklistChecker
from app.services.signal_score_v2_service import SignalScoreV2Service
from app.strategies.range_market_detector import RangeMarketDetector
from app.strategies.bollinger_mean_reversion import BollingerMeanReversionStrategy
from app.strategies.mode_switcher import TradingModeSwitcher

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

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.connection = None

        # 从config.yaml加载配置
        self._load_config()

        self.threshold = 60  # 开仓阈值 (强信号过滤,理论最大232分,60分≈26%强度)

        # 初始化信号黑名单检查器（动态加载，5分钟缓存）
        self.blacklist_checker = SignalBlacklistChecker(db_config, cache_minutes=5)

        # V2评分服务已在_load_config()中初始化

    def _reload_blacklist(self):
        """重新加载黑名单和白名单（每5分钟运行）"""
        try:
            import yaml

            # 重新加载config.yaml中的交易对列表
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                all_symbols = set(config.get('symbols', []))

            conn = self._get_connection()
            cursor = conn.cursor()

            # 重新加载黑名单标识（rating_level 1-2，用于小仓位）
            cursor.execute("""
                SELECT symbol FROM trading_symbol_rating
                WHERE rating_level >= 1 AND rating_level < 3
                ORDER BY rating_level DESC, updated_at DESC
            """)
            blacklist_rows = cursor.fetchall()
            old_blacklist = set(self.blacklist) if hasattr(self, 'blacklist') else set()
            new_blacklist = set([row['symbol'] for row in blacklist_rows]) if blacklist_rows else set()

            # 扫描池 = config.yaml 的所有交易对（不过滤）
            old_whitelist = set(self.whitelist) if hasattr(self, 'whitelist') else set()
            new_whitelist = all_symbols

            cursor.close()

            # 记录黑名单变化
            blacklist_added = new_blacklist - old_blacklist
            blacklist_removed = old_blacklist - new_blacklist

            if blacklist_added:
                logger.info(f"[BLACKLIST-UPDATE] ➕ 新增黑名单: {', '.join(sorted(blacklist_added))}")
            if blacklist_removed:
                logger.info(f"[BLACKLIST-UPDATE] ➖ 移除黑名单: {', '.join(sorted(blacklist_removed))}")

            # 记录扫描池变化（config.yaml变化）
            whitelist_added = new_whitelist - old_whitelist
            whitelist_removed = old_whitelist - new_whitelist

            if whitelist_added:
                logger.info(f"[WHITELIST-UPDATE] ➕ 新增扫描池: {', '.join(sorted(whitelist_added))}")
            if whitelist_removed:
                logger.info(f"[WHITELIST-UPDATE] ➖ 移除扫描池: {', '.join(sorted(whitelist_removed))}")

            self.blacklist = list(new_blacklist)
            self.whitelist = list(new_whitelist)

            return len(blacklist_added) > 0 or len(blacklist_removed) > 0 or len(whitelist_added) > 0 or len(whitelist_removed) > 0
        except Exception as e:
            logger.error(f"[BLACKLIST-RELOAD-ERROR] 重新加载黑白名单失败: {e}")
            return False

    def _load_config(self):
        """从数据库加载黑名单和自适应参数,从config.yaml加载交易对列表"""
        try:
            import yaml

            # 1. 从config.yaml加载交易对列表
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                all_symbols = config.get('symbols', [])

            # 2. 从数据库加载黑名单标识（rating_level 1-2级，用于小仓位）
            # rating_level: 0=白名单, 1=黑名单1级, 2=黑名单2级, 3=黑名单3级(永久禁止，不扫描)
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol FROM trading_symbol_rating
                WHERE rating_level >= 1 AND rating_level < 3
                ORDER BY rating_level DESC, updated_at DESC
            """)
            blacklist_rows = cursor.fetchall()
            self.blacklist = [row['symbol'] for row in blacklist_rows] if blacklist_rows else []

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

            # 6. 扫描池 = config.yaml 中的所有交易对
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
                    'position_mid': {'long': 5, 'short': 5},
                    'position_high': {'long': 0, 'short': 20},
                    'momentum_down_3pct': {'long': 0, 'short': 10},       # 震荡市优化: 从15降到10,需要更多信号配合
                    'momentum_up_3pct': {'long': 10, 'short': 0},         # 震荡市优化: 从15降到10,避免追涨杀跌
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

            # V2评分过滤服务（协同确认）
            resonance_config = config.get('signals', {}).get('resonance_filter', {})
            self.score_v2_service = SignalScoreV2Service(
                db_config=self.db_config,
                score_config=resonance_config
            )
            logger.info(f"   ✅ V2评分过滤服务已初始化")

        except Exception as e:
            import traceback
            logger.error(f"❌ 读取数据库配置失败，使用默认14个交易对")
            logger.error(f"   错误类型: {type(e).__name__}")
            logger.error(f"   错误信息: {e}")
            logger.error(f"   当前工作目录: {os.getcwd()}")
            logger.error(f"   详细堆栈:\n{traceback.format_exc()}")
            self.whitelist = [
                'BCH/USDT', 'LDO/USDT', 'ENA/USDT', 'WIF/USDT', 'TAO/USDT',
                'DASH/USDT', 'ETC/USDT', 'VIRTUAL/USDT', 'NEAR/USDT',
                'AAVE/USDT', 'SUI/USDT', 'UNI/USDT', 'ADA/USDT', 'SOL/USDT'
            ]
            self.blacklist = []
            self.adaptive_long = {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02, 'min_holding_minutes': 60, 'position_size_multiplier': 1.0}
            self.adaptive_short = {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02, 'min_holding_minutes': 60, 'position_size_multiplier': 1.0}
            # 🔥 修复: 初始化signal_blacklist
            self.signal_blacklist = {}
            # 🔥 修复: 初始化scoring_weights
            self.scoring_weights = {
                'position_low': {'long': 20, 'short': 0},
                'position_mid': {'long': 5, 'short': 5},
                'position_high': {'long': 0, 'short': 20},
                'momentum_down_3pct': {'long': 0, 'short': 10},
                'momentum_up_3pct': {'long': 10, 'short': 0},
                'trend_1h_bull': {'long': 20, 'short': 0},
                'trend_1h_bear': {'long': 0, 'short': 20},
                'volatility_high': {'long': 10, 'short': 10},
                'consecutive_bull': {'long': 15, 'short': 0},
                'consecutive_bear': {'long': 0, 'short': 15},
                'volume_power_bull': {'long': 25, 'short': 0},
                'volume_power_bear': {'long': 0, 'short': 25},
                'volume_power_1h_bull': {'long': 15, 'short': 0},
                'volume_power_1h_bear': {'long': 0, 'short': 15},
                'breakout_long': {'long': 20, 'short': 0},
                'breakdown_short': {'long': 0, 'short': 20}
            }
            # 🔥 修复: 初始化score_v2_service（异常情况下也需要）
            try:
                self.score_v2_service = SignalScoreV2Service(
                    db_config=self.db_config,
                    score_config={'enabled': True, 'min_symbol_score': 15}
                )
                logger.info(f"   ✅ V2评分过滤服务已初始化（降级模式）")
            except Exception as v2_error:
                logger.error(f"   ❌ V2评分过滤服务初始化失败: {v2_error}")
                self.score_v2_service = None

    def reload_config(self):
        """重新加载配置 - 供外部调用"""
        logger.info("🔄 重新加载配置文件...")
        self._load_config()
        # 同时强制重新加载信号黑名单
        if hasattr(self, 'blacklist_checker'):
            self.blacklist_checker.force_reload()
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
        🔥 已废弃 (V5.1优化 - 2026-02-09)

        防追高/追跌过滤器

        废弃原因:
        1. Big4触底检测已提供全局保护（禁止做空2小时）
        2. 防杀跌过滤容易误杀破位追空信号
        3. 与Big4紧急干预机制逻辑冲突

        保留此方法仅供历史参考

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

            # 做多防追高: 不在高于80%位置开多
            if side == 'LONG' and position_pct > 80:
                return False, f"防追高-价格位于24H区间{position_pct:.1f}%位置,距最高仅{(high_24h-current_price)/current_price*100:.2f}%"

            # 做空防杀跌: 不在低于20%位置开空
            if side == 'SHORT' and position_pct < 20:
                return False, f"防杀跌-价格位于24H区间{position_pct:.1f}%位置,距最低仅{(current_price-low_24h)/current_price*100:.2f}%"

            # 额外检查: 24H大涨且在高位 → 更严格
            if side == 'LONG' and change_24h > 15 and position_pct > 70:
                return False, f"防追高-24H涨{change_24h:+.2f}%且位于{position_pct:.1f}%高位"

            # 额外检查: 24H大跌且在低位 → 更严格
            if side == 'SHORT' and change_24h < -15 and position_pct < 30:
                return False, f"防杀跌-24H跌{change_24h:+.2f}%且位于{position_pct:.1f}%低位"

            return True, f"位置{position_pct:.1f}%,24H{change_24h:+.2f}%"

        except Exception as e:
            logger.error(f"防追高检查失败 {symbol}: {e}")
            return True, "检查失败,放行"

    def load_klines(self, symbol: str, timeframe: str, limit: int = 100):
        conn = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        query = """
            SELECT open_price as open, high_price as high,
                   low_price as low, close_price as close,
                   volume
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
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

    def analyze(self, symbol: str, big4_result: dict = None):
        """分析并决策 - 支持做多和做空 (主要使用1小时K线)

        Args:
            symbol: 交易对
            big4_result: Big4趋势结果 (由SmartTraderService传入)
        """
        if symbol not in self.whitelist:
            return None

        try:
            klines_1d = self.load_klines(symbol, '1d', 50)
            klines_1h = self.load_klines(symbol, '1h', 100)
            klines_15m = self.load_klines(symbol, '15m', 96)  # 24小时的15分钟K线

            if len(klines_1d) < 30 or len(klines_1h) < 72 or len(klines_15m) < 48:  # 至少需要72小时(3天)数据
                return None

            current = klines_1h[-1]['close']

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
            volumes_1h = [k['volume'] for k in klines_1h[-48:]]
            avg_volume_1h = sum(volumes_1h) / len(volumes_1h) if volumes_1h else 1

            strong_bull_1h = 0  # 有力量的阳线
            strong_bear_1h = 0  # 有力量的阴线

            for k in klines_1h[-48:]:
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

            if bullish_1h >= 30:  # 阳线>=30根(62.5%)
                weight = self.scoring_weights.get('trend_1h_bull', {'long': 20, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['trend_1h_bull'] = weight['long']
            elif bearish_1h >= 30:  # 阴线>=30根(62.5%)
                weight = self.scoring_weights.get('trend_1h_bear', {'long': 0, 'short': 20})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['trend_1h_bear'] = weight['short']

            # 4. 波动率评分 - 最近24小时
            recent_24h = klines_1h[-24:]
            volatility = (max(k['high'] for k in recent_24h) - min(k['low'] for k in recent_24h)) / current * 100

            # 高波动率更适合交易
            if volatility > 5:  # 波动超过5%
                weight = self.scoring_weights.get('volatility_high', {'long': 10, 'short': 10})
                if long_score > short_score:
                    long_score += weight['long']
                    if weight['long'] > 0:
                        signal_components['volatility_high'] = weight['long']
                else:
                    short_score += weight['short']
                    if weight['short'] > 0:
                        signal_components['volatility_high'] = weight['short']

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

            # 8. 突破追涨信号: 已禁用 (历史数据: 85笔, 28.2%胜率, -$600亏损)
            # 禁用原因: 追高风险大，胜率低，容易买在顶部

            # 9. 破位追空信号: position_low + 强力量能空头 → 可以做空
            # 历史数据验证: 643笔订单, 55.8%胜率, $5736盈利 (最赚钱的信号之一)
            # 触发条件: 价格低位 + 强力空头量能
            if position_pct < 30 and (net_power_1h <= -2 or (net_power_1h <= -2 and net_power_15m <= -2)):
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

            # V1评分计算完成，稍后与V2一起打印

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
                # 🔥 修复 (2026-02-11): position_low应该是多头信号, position_high应该是空头信号
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

                # 🔥 强制验证: 至少需要2个信号组合 (2026-02-11)
                if len(signal_components) < 2:
                    logger.warning(f"🚫 {symbol} 信号不足: 只有{len(signal_components)}个信号 "
                                   f"[{', '.join(signal_components.keys())}], 得分{score}分, 方向{side}, 拒绝开仓")
                    return None

                # 🔥 特殊验证: position_mid信号需要至少3个信号配合
                if 'position_mid' in signal_components and len(signal_components) < 3:
                    logger.warning(f"🚫 {symbol} 中位信号需要更多佐证: 只有{len(signal_components)}个信号, 拒绝开仓")
                    return None

                # 生成信号组合键用于黑名单检查
                if signal_components:
                    sorted_signals = sorted(signal_components.keys())
                    signal_combination_key = " + ".join(sorted_signals)
                else:
                    signal_combination_key = "unknown"

                # 检查信号黑名单 (使用动态黑名单检查器)
                is_blacklisted, blacklist_reason = self.blacklist_checker.is_blacklisted(signal_combination_key, side)
                if is_blacklisted:
                    logger.info(f"🚫 {symbol} 信号 [{signal_combination_key}] {side} 在黑名单中，跳过（{blacklist_reason}）")
                    return None

                # 🔥 新增: 检查信号方向矛盾（防止逻辑错误）
                is_valid, contradiction_reason = self._validate_signal_direction(signal_components, side)
                if not is_valid:
                    logger.error(f"🚫 {symbol} 信号方向矛盾: {contradiction_reason} | 信号:{signal_combination_key} | 方向:{side}")
                    return None

                # 🔥 V2协同过滤：方向一致性检查 + 强度验证
                v2_direction = 'N/A'
                v2_score = 0
                v2_passed = '未启用'

                if self.score_v2_service:
                    v2_result = self.score_v2_service.check_score_filter(symbol, side)

                    # 提取V2数据
                    if v2_result.get('coin_score'):
                        coin = v2_result['coin_score']
                        v2_direction = '多' if coin.get('direction') == 'LONG' else '空' if coin.get('direction') == 'SHORT' else '中性'
                        v2_score = coin.get('total_score', 0)

                    v2_passed = '✅通过' if v2_result['passed'] else f"❌不通过({v2_result['reason']})"

                    # 打印合并日志：V1评分 + V2趋势 + 协同过滤
                    signal_names = ', '.join(signal_components.keys()) if signal_components else '无'
                    logger.info(f"📊 {symbol:<12} V1评分 多【{long_score}分】空【{short_score}分】信号【{signal_names}】，V2 趋势【{v2_direction}】分数：{v2_score:+d}，协同过滤 【{v2_passed}】")

                    if not v2_result['passed']:
                        return None
                else:
                    # 没有V2服务，只打印V1
                    signal_names = ', '.join(signal_components.keys()) if signal_components else '无'
                    logger.info(f"📊 {symbol:<12} V1评分 多【{long_score}分】空【{short_score}分】信号【{signal_names}】，V2趋势【未启用】")

                # 🔥 新增: 禁止高风险位置交易（代码层面强制）
                if side == 'LONG' and 'position_high' in signal_components:
                    logger.warning(f"🚫 {symbol} 拒绝高位做多: position_high在{position_pct:.1f}%位置,容易买在顶部")
                    return None

                if side == 'SHORT' and 'position_low' in signal_components:
                    logger.warning(f"🚫 {symbol} 拒绝低位做空: position_low在{position_pct:.1f}%位置,容易遇到反弹")
                    return None

                # 🔥 Big4方向过滤（简化版）：只在强度>=70时禁止反向
                # V1技术指标评分作为主导，Big4只做简单的方向过滤
                # 移除V2共振检查（V1和V2维度不同，强行共振没有意义）
                # Big4方向过滤已在scan_all()中处理，这里不需要额外检查

                # 生成signal_type用于模式匹配
                signal_type = f"TREND_{signal_combination_key}_{side}_{int(score)}"

                return {
                    'symbol': symbol,
                    'side': side,
                    'score': score,
                    'current_price': current,
                    'signal_components': signal_components,  # 添加信号组成
                    'signal_type': signal_type,  # 添加信号类型，用于模式过滤
                    'signal_time': datetime.now()  # 🔥 关键修复：记录信号产生的时间
                }

            return None

        except Exception as e:
            logger.error(f"{symbol} 分析失败: {e}")
            return None

    def scan_all(self, big4_result: dict = None, big4_filter_enabled: bool = True):
        """扫描所有币种

        Args:
            big4_result: Big4趋势结果 (由SmartTraderService传入)
            big4_filter_enabled: Big4过滤器是否启用 (由SmartTraderService传入)
        """
        # 每次扫描前重新加载黑名单,确保运行时添加的黑名单立即生效
        self._reload_blacklist()

        logger.info(f"\n{'='*100}")
        logger.info(f"🔍 开始扫描 {len(self.whitelist)} 个交易对 | 开仓阈值: {self.threshold}分")

        # 显示Big4状态
        big4_signal = 'NEUTRAL'
        big4_strength = 0
        if big4_result:
            big4_signal = big4_result.get('overall_signal', 'NEUTRAL')
            big4_strength = big4_result.get('signal_strength', 0)
            logger.info(f"📊 Big4市场趋势: {big4_signal} (强度: {big4_strength:.1f})")

        logger.info(f"{'='*100}")

        opportunities = []
        filtered_count = 0  # 被Big4过滤的信号数

        for symbol in self.whitelist:
            result = self.analyze(symbol, big4_result=big4_result)
            if result:
                # ✅ 通过V1+V2验证的信号（详细日志已在analyze()中打印）
                signal_side = result['side']
                signal_score = result['score']

                # 🔥 Big4紧急干预过滤（使用detector中已设置的block标志）
                if big4_result and big4_filter_enabled:
                    emergency = big4_result.get('emergency_intervention', {})

                    if emergency.get('block_long', False) and signal_side == 'LONG':
                        logger.info(f"🚫 [Big4过滤] {symbol} LONG | {emergency.get('details', 'Big4强趋势阻止做多')}")
                        filtered_count += 1
                        continue

                    if emergency.get('block_short', False) and signal_side == 'SHORT':
                        logger.info(f"🚫 [Big4过滤] {symbol} SHORT | {emergency.get('details', 'Big4强趋势阻止做空')}")
                        filtered_count += 1
                        continue

                logger.info(f"🎯 [最终入选] {symbol} {signal_side} | V1评分:{signal_score} | Big4:{big4_signal}({big4_strength:.0f})")
                opportunities.append(result)

        logger.info(f"{'='*100}")
        logger.info(f"✅ 扫描完成 | 合格信号: {len(opportunities)} 个 | Big4过滤: {filtered_count} 个 | Big4状态: {big4_signal}(强度{big4_strength:.0f})")
        logger.info(f"{'='*100}\n")

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

        self.brain = SmartDecisionBrain(self.db_config)
        self.connection = None
        self.running = True
        self.event_loop = None  # 事件循环引用，在async_main中设置

        # WebSocket 价格服务
        self.ws_service: BinanceWSPriceService = get_ws_price_service()

        # 自适应优化器
        self.optimizer = AdaptiveOptimizer(self.db_config)
        self.last_optimization_date = None  # 记录上次优化日期

        # 优化配置管理器 (支持自我优化的参数配置)
        self.opt_config = OptimizationConfig(self.db_config)

        # 交易对评级管理器 (3级黑名单制度)
        self.rating_manager = SymbolRatingManager(self.db_config)

        # 波动率配置更新器 (15M K线动态止盈)
        self.volatility_updater = VolatilityProfileUpdater(self.db_config)

        # 加载智能平仓配置
        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            self.smart_exit_config = config.get('signals', {}).get('smart_exit', {'enabled': False})

            # 🔥 从数据库读取系统配置（优先级高于config.yaml）
            from app.services.system_settings_loader import get_big4_filter_enabled, get_batch_entry_strategy

            # Big4过滤器配置
            big4_enabled_from_db = get_big4_filter_enabled()
            self.big4_filter_config = {'enabled': big4_enabled_from_db}
            logger.info(f"📊 从数据库加载Big4过滤器配置: {'启用' if big4_enabled_from_db else '禁用'}")

            # K线回调建仓策略配置
            self.batch_entry_strategy = get_batch_entry_strategy()
            logger.info(f"📊 建仓策略: {self.batch_entry_strategy} ({'V2回调' if self.batch_entry_strategy == 'kline_pullback' else 'V1采样'})")

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

        # 初始化价格采样建仓执行器（V1策略：15分钟价格采样找最优点，一次性开仓）
        self.smart_entry_executor = SmartEntryExecutor(
            db_config=self.db_config,
            live_engine=self,
            price_service=self.ws_service,
            account_id=self.account_id
        )
        logger.info("✅ 价格采样建仓执行器已启动 (V1: 15分钟价格采样，一次性开仓)")

        # 初始化K线回调建仓执行器（V2策略：等待15M阴线回调，一次性开仓）
        self.pullback_executor = KlinePullbackEntryExecutor(
            db_config=self.db_config,
            live_engine=self,
            price_service=self.ws_service,
            account_id=self.account_id,
            brain=self.brain,
            opt_config=self.opt_config
        )
        logger.info("✅ K线回调建仓执行器已启动 (V2: 15M阴线回调，一次性开仓)")

        # 初始化Big4趋势检测器 (四大天王: BTC/ETH/BNB/SOL)
        self.big4_detector = Big4TrendDetector()
        self.big4_symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

        # ========== 破位信号加权系统 ==========
        self.breakout_booster = BreakoutSignalBooster(expiry_hours=4)
        logger.info("✅ 破位信号加权系统已初始化 (4小时有效期)")

        # ========== 震荡市交易策略模块 ==========
        self.range_detector = RangeMarketDetector(self.db_config)
        self.bollinger_strategy = BollingerMeanReversionStrategy(self.db_config)
        self.mode_switcher = TradingModeSwitcher(self.db_config)
        logger.info("✅ 震荡市交易策略模块已初始化")

        logger.info("🔱 Big4趋势检测器已启动 (实时检测模式)")

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
        获取Big4趋势结果 (实时检测模式)

        每次调用都会实时检测市场趋势，确保信号的时效性
        """
        try:
            result = self.big4_detector.detect_market_trend()
            logger.debug(f"🔱 Big4趋势实时检测 | {result['overall_signal']} (强度: {result['signal_strength']:.0f})")

            # 更新破位信号加权系统
            # BULLISH=看涨→LONG, BEARISH=看跌→SHORT
            direction_map = {'BULLISH': 'LONG', 'BEARISH': 'SHORT', 'NEUTRAL': 'NEUTRAL'}
            direction = direction_map.get(result['overall_signal'], 'NEUTRAL')
            if direction != 'NEUTRAL':
                self.breakout_booster.update_big4_breakout(
                    direction,
                    result['signal_strength']
                )
                logger.debug(f"💥 破位系统已更新: {direction} 强度{result['signal_strength']:.0f}")

            return result
        except Exception as e:
            logger.error(f"❌ Big4趋势检测失败: {e}")
            # 检测失败时返回中性结果
            return {
                'overall_signal': 'NEUTRAL',
                'signal_strength': 0,
                'details': {},
                'timestamp': datetime.now()
            }

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

            # 检查数据新鲜度: 5m K线数据不能超过15分钟前
            import time
            current_timestamp_ms = int(time.time() * 1000)
            data_age_minutes = (current_timestamp_ms - open_time) / 1000 / 60

            if data_age_minutes > 15:
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

    def count_positions(self, symbol: str, side: str = None):
        """
        统计持仓数量
        symbol: 交易对
        side: 方向(LONG/SHORT), None表示统计任意方向
        Returns: 持仓数量
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if side:
                # 统计特定方向的持仓数量
                cursor.execute("""
                    SELECT COUNT(*) FROM futures_positions
                    WHERE symbol = %s AND position_side = %s AND status IN ('open', 'building') AND account_id = %s
                """, (symbol, side, self.account_id))
            else:
                # 统计任意方向的持仓数量
                cursor.execute("""
                    SELECT COUNT(*) FROM futures_positions
                    WHERE symbol = %s AND status IN ('open', 'building') AND account_id = %s
                """, (symbol, self.account_id))

            result = cursor.fetchone()
            cursor.close()
            return result[0] if result else 0
        except:
            return 0

    def _get_margin_per_batch(self, symbol: str) -> float:
        """
        根据评级等级获取每批固定保证金

        Args:
            symbol: 交易对

        Returns:
            每批保证金金额（USDT）
        """
        rating_level = self.opt_config.get_symbol_rating_level(symbol)

        if rating_level == 0:
            return 400.0  # 白名单/默认
        elif rating_level == 1:
            return 100.0  # 黑名单1级
        elif rating_level == 2:
            return 50.0   # 黑名单2级
        else:
            return 0.0    # 黑名单3级 - 禁止交易

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

        # 新增验证: 检查交易方向是否允许
        if not self.opt_config.is_direction_allowed(side):
            direction_name = "做多" if side == "LONG" else "做空"
            logger.warning(f"[SIGNAL_REJECT] {symbol} {side} - 系统已禁止{direction_name}")
            return False

        # 🔥 V5.1优化: 移除防追高/防杀跌过滤
        # 原因: Big4触底检测已提供全局保护（禁止做空2小时）
        # 防杀跌过滤容易误杀破位追空信号，与Big4机制冲突
        # 移除日期: 2026-02-09

        # ========== 第二步：提前检查黑名单 ==========
        rating_level = self.opt_config.get_symbol_rating_level(symbol)
        if rating_level == 3:
            logger.warning(f"[BLACKLIST_LEVEL3] {symbol} 已被永久禁止交易")
            return False

        # ========== 第三步：根据数据库配置选择建仓策略（一次性开仓） ==========
        # batch_entry_strategy: 'kline_pullback' (V2) or 'price_percentile' (V1)

        # V2策略: K线回调建仓（等待15M阴线，一次性开仓）
        if self.batch_entry_strategy == 'kline_pullback' and self.pullback_executor and self.event_loop:
            try:
                # 准备信号字典
                signal = {
                    'symbol': symbol,
                    'direction': side,
                    'leverage': self.leverage,
                    'signal_time': datetime.now(),  # 信号触发时间
                    'strategy_id': 'smart_trader_v2',
                    'trade_params': {
                        'entry_score': opp.get('score', 0),
                        'signal_components': opp.get('signal_components', {}),
                        'signal_combination_key': self._generate_signal_combination_key(opp.get('signal_components', {}))
                    }
                }

                # 在事件循环中创建异步任务（后台执行）
                asyncio.run_coroutine_threadsafe(
                    self.pullback_executor.execute_entry(signal),
                    self.event_loop
                )

                logger.info(f"🚀 [V2-PULLBACK] {symbol} {side} K线回调建仓任务已启动 (等待15M阴线，一次性开仓)")
                logger.info(f"   📝 信号评分: {opp.get('score', 0)} | 信号组合: {signal['trade_params']['signal_combination_key']}")
                return True

            except Exception as e:
                logger.error(f"❌ [V2-PULLBACK-ERROR] {symbol} 启动回调任务失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False  # 避免继续执行回退策略造成重复下单

        # V1策略: 价格采样建仓（15分钟价格采样找最优点，一次性开仓）
        if self.batch_entry_strategy == 'price_percentile' and self.smart_entry_executor and self.event_loop:
            try:
                # 准备信号字典
                signal = {
                    'symbol': symbol,
                    'direction': side,
                    'leverage': self.leverage,
                    'signal_time': datetime.now(),
                    'strategy_id': 'smart_trader_v1',
                    'trade_params': {
                        'entry_score': opp.get('score', 0),
                        'signal_components': opp.get('signal_components', {}),
                        'signal_combination_key': self._generate_signal_combination_key(opp.get('signal_components', {}))
                    }
                }

                # 在事件循环中创建异步任务（后台执行）
                asyncio.run_coroutine_threadsafe(
                    self.smart_entry_executor.execute_entry(signal),
                    self.event_loop
                )

                logger.info(f"🚀 [V1-PRICE-SAMPLING] {symbol} {side} 价格采样建仓任务已启动 (15分钟采样，一次性开仓)")
                logger.info(f"   📝 信号评分: {opp.get('score', 0)} | 信号组合: {signal['trade_params']['signal_combination_key']}")
                return True

            except Exception as e:
                logger.error(f"❌ [V1-PRICE-SAMPLING-ERROR] {symbol} 启动采样任务失败: {e}")
                import traceback
                logger.error(traceback.format_exc())
                return False  # 避免继续执行回退策略造成重复下单

        # ========== 回退策略：一次性直接开仓（执行器不可用时使用）==========
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
                # 使用固定保证金金额替代倍数逻辑
                margin_per_batch = self._get_margin_per_batch(symbol)

                # Level 3 = 永久禁止
                if margin_per_batch == 0:
                    logger.warning(f"[BLACKLIST_LEVEL3] {symbol} 已被永久禁止交易 (Level{rating_level})")
                    return False

                # 记录评级信息
                rating_tag = f"[Level{rating_level}]" if rating_level > 0 else ""
                logger.info(f"{rating_tag} {symbol} 固定保证金: ${margin_per_batch:.2f}")

                # ========== 检查是否为震荡市策略 ==========
                mode_config = None
                if strategy == 'bollinger_mean_reversion':
                    try:
                        mode_config = self.mode_switcher.get_current_mode(self.account_id, 'usdt_futures')
                        if mode_config:
                            logger.info(f"[RANGE_MODE] {symbol} 使用震荡市交易参数")
                            # 震荡市模式使用固定保证金的60%
                            base_position_size = margin_per_batch * 0.6
                            logger.info(f"[RANGE_POSITION] {symbol} 震荡市仓位: ${base_position_size:.2f} (60%)")
                        else:
                            base_position_size = margin_per_batch
                    except Exception as e:
                        logger.error(f"[MODE_ERROR] 获取模式配置失败: {e}")
                        base_position_size = margin_per_batch
                else:
                    # 趋势模式: 使用完整固定保证金
                    base_position_size = margin_per_batch

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

            logger.info(f"[SIGNAL_COMBO] {symbol} {side} 信号组合: {signal_combination_key} (评分: {entry_score}) 策略: {strategy}")

            # Big4 信号记录
            if opp.get('big4_adjusted'):
                big4_signal = opp.get('big4_signal', 'NEUTRAL')
                big4_strength = opp.get('big4_strength', 0)
                logger.info(f"[BIG4-APPLIED] {symbol} Big4趋势: {big4_signal} (强度: {big4_strength})")

            # ========== 根据策略类型确定超时时间 ==========
            if strategy == 'bollinger_mean_reversion' and mode_config:
                # 震荡市策略: 使用range_max_hold_hours (默认4小时)
                range_max_hold_hours = int(mode_config.get('range_max_hold_hours', 4))  # 转换Decimal为int
                base_timeout_minutes = range_max_hold_hours * 60
                logger.info(f"[RANGE_TIMEOUT] {symbol} 震荡市最大持仓时间: {base_timeout_minutes}分钟")
            else:
                # 趋势模式: 使用动态超时时间
                base_timeout_minutes = self.opt_config.get_timeout_by_score(entry_score)

            # 计算超时时间点 (UTC时间)
            timeout_at = datetime.utcnow() + timedelta(minutes=base_timeout_minutes)

            # 准备entry_reason
            entry_reason = opp.get('reason', '')
            if strategy == 'bollinger_mean_reversion':
                entry_reason = f"[震荡市] {entry_reason}"

            # 插入持仓记录 (包含动态超时字段和计划平仓时间)
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_reason, entry_score, signal_components, max_hold_minutes, timeout_at,
                 planned_close_time, source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s,
                        DATE_ADD(NOW(), INTERVAL %s MINUTE), 'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, side, quantity, current_price, self.leverage,
                notional_value, margin, stop_loss, take_profit,
                signal_combination_key, entry_reason, entry_score, signal_components_json,
                base_timeout_minutes, timeout_at,
                base_timeout_minutes  # planned_close_time = NOW() + max_hold_minutes
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

    def _generate_signal_combination_key(self, signal_components: dict) -> str:
        """生成信号组合键"""
        if signal_components:
            sorted_signals = sorted(signal_components.keys())
            return " + ".join(sorted_signals)
        else:
            return "unknown"

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

    def close_position_by_side(self, symbol: str, side: str, reason: str = "reverse_signal"):
        """关闭指定交易对和方向的持仓"""
        try:
            current_price = self.get_current_price(symbol)
            if not current_price:
                return False

            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)  # 使用字典游标

            # 获取持仓信息用于日志和计算盈亏
            cursor.execute("""
                SELECT id, entry_price, quantity, leverage, margin FROM futures_positions
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

                # 4. 新增: 评估信号黑名单（动态升级/降级）
                logger.info("=" * 80)
                logger.info("🔍 开始评估信号黑名单（动态管理）")
                logger.info("=" * 80)
                try:
                    from app.services.signal_blacklist_reviewer import SignalBlacklistReviewer
                    reviewer = SignalBlacklistReviewer(self.db_config)
                    review_results = reviewer.review_all_blacklisted_signals()
                    reviewer.close()

                    # 打印评估结果摘要
                    if review_results['removed']:
                        logger.info(f"✅ 解除黑名单: {len(review_results['removed'])} 个信号")
                        for item in review_results['removed'][:5]:  # 只显示前5个
                            logger.info(f"   - {item['signal'][:50]} ({item['side']})")
                    if review_results['upgraded']:
                        logger.info(f"📈 降低等级: {len(review_results['upgraded'])} 个信号")
                    if review_results['downgraded']:
                        logger.warning(f"📉 提高等级: {len(review_results['downgraded'])} 个信号")

                    # 如果有信号被解除黑名单，重新加载配置
                    if review_results['removed'] or review_results['upgraded']:
                        logger.info("🔄 重新加载黑名单配置...")
                        self.brain.reload_blacklist()

                except Exception as e:
                    logger.error(f"❌ 信号黑名单评估失败: {e}")
                    import traceback
                    logger.error(traceback.format_exc())

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
                AND planned_close_time IS NOT NULL
                AND NOW() > planned_close_time
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
        last_blacklist_reload = datetime.now()

        while self.running:
            try:
                # 0. 检查是否需要运行每日自适应优化 (凌晨2点)
                self.check_and_run_daily_optimization()

                # 0.5. 定期重新加载黑名单 (每5分钟)
                now = datetime.now()
                if (now - last_blacklist_reload).total_seconds() >= 300:  # 5分钟
                    self.brain._reload_blacklist()
                    last_blacklist_reload = now

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

                # 4. 检查持仓
                current_positions = self.get_open_positions_count()
                logger.info(f"[STATUS] 持仓: {current_positions}/{self.max_positions}")

                if current_positions >= self.max_positions:
                    logger.info("[SKIP] 已达最大持仓,跳过扫描")
                    time.sleep(self.scan_interval)
                    continue

                # 5. 🔥 强制只做趋势单,不再做震荡市场的单
                logger.info(f"[SCAN] 模式: TREND (只做趋势) | 扫描 {len(self.brain.whitelist)} 个币种...")

                # 获取Big4结果并扫描趋势信号
                big4_result = self.get_big4_result()
                opportunities = self.brain.scan_all(
                    big4_result=big4_result,
                    big4_filter_enabled=self.big4_filter_config.get('enabled', True)
                )
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

                # 5.8. 🚀 反弹交易窗口检查 (优先于正常信号)
                # 逻辑: Big4触底 = 全市场信号，所有交易对都开多
                try:
                    conn_bounce = self._get_connection()
                    cursor = conn_bounce.cursor()

                    # 检查是否有Big4的活跃反弹窗口
                    BIG4 = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

                    cursor.execute("""
                        SELECT symbol, lower_shadow_pct, window_end, trigger_time
                        FROM bounce_window
                        WHERE account_id = 2
                        AND trading_type = 'usdt_futures'
                        AND symbol IN ('BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT')
                        AND window_end > NOW()
                        ORDER BY trigger_time DESC
                        LIMIT 1
                    """)

                    big4_bounce = cursor.fetchone()

                    if big4_bounce:
                        # 🚀 Big4触底 = 全市场反弹信号!
                        window_end = big4_bounce['window_end']
                        remaining_minutes = (window_end - datetime.now()).total_seconds() / 60
                        trigger_symbol = big4_bounce['symbol']

                        logger.warning(f"🚀🚀🚀 [MARKET-BOUNCE] {trigger_symbol} 触发全市场反弹窗口! "
                                     f"下影{big4_bounce['lower_shadow_pct']:.1f}%, 剩余{remaining_minutes:.0f}分钟")

                        # 获取所有交易对
                        cursor.execute("""
                            SELECT DISTINCT symbol
                            FROM symbols
                            WHERE trading_type = 'usdt_futures'
                            AND is_active = TRUE
                        """)
                        all_symbols = [row['symbol'] for row in cursor.fetchall()]
                        logger.info(f"🚀 [MARKET-BOUNCE] 准备对 {len(all_symbols)} 个交易对开多")

                        opened_count = 0
                        for symbol in all_symbols:
                            if self.get_open_positions_count() >= self.max_positions:
                                logger.info(f"[BOUNCE-SKIP] 已达最大持仓 {self.max_positions}，停止反弹交易")
                                break

                            # 检查是否已有该币种的LONG仓位
                            if self.has_position(symbol, 'LONG'):
                                continue

                            # 检查是否有SHORT仓位
                            if self.has_position(symbol, 'SHORT'):
                                continue

                            # 检查最近是否平仓过LONG (冷却期)
                            if self.check_recent_close(symbol, 'LONG', cooldown_minutes=30):
                                continue

                            # 获取当前价格
                            try:
                                current_price = self.binance_api.get_current_price(symbol)
                            except Exception as e:
                                logger.error(f"[BOUNCE-ERROR] {symbol} 获取价格失败: {e}")
                                continue

                            # 🔥 激进开仓策略: 立即开仓
                            # Big4触底 = 市场信号，所有币跟涨
                            bounce_opp = {
                                'symbol': symbol,
                                'side': 'LONG',
                                'score': 100,
                                'strategy': 'emergency_bounce',
                                'reason': f"🚀市场反弹: {trigger_symbol}触底{big4_bounce['lower_shadow_pct']:.1f}%, 窗口{remaining_minutes:.0f}分钟",
                                'signal_type': 'EMERGENCY_BOUNCE',
                                'position_size_pct': 70,  # 🔥 激进仓位70%
                                'take_profit_pct': 8.0,   # 🔥 止盈8%（基于历史平均反弹12.6%）
                                'stop_loss_pct': 3.0,     # 🔥 止损3%
                                'trailing_stop_pct': 5.0, # 🔥 动态追踪：回撤5%平仓
                            }

                            # 开仓
                            try:
                                self.open_position(bounce_opp)
                                opened_count += 1
                                logger.info(f"✅ [BOUNCE-OPENED] {symbol} 反弹多单已开 ({opened_count}/{len(all_symbols)})")
                                time.sleep(1)  # 避免频率限制
                            except Exception as e:
                                logger.error(f"❌ [BOUNCE-ERROR] {symbol} 反弹开仓失败: {e}")

                        logger.warning(f"🚀 [MARKET-BOUNCE] 反弹交易完成: 共开仓 {opened_count} 个币种")

                    cursor.close()
                    conn_bounce.close()

                except Exception as e:
                    logger.error(f"[BOUNCE-CHECK-ERROR] 反弹窗口检查失败: {e}")

                # 6. 执行交易
                logger.info(f"[EXECUTE] 找到 {len(opportunities)} 个机会")

                # 输出所有机会的详细信息
                if opportunities:
                    logger.info(f"\n{'='*100}")
                    logger.info(f"🎯 开仓机会列表 (按评分排序)")
                    logger.info(f"{'='*100}")
                    logger.info(f"{'币种':<14} {'方向':<6} {'评分':<6} {'信号组成':<50}")
                    logger.info(f"{'-'*100}")

                    sorted_opps = sorted(opportunities, key=lambda x: x['score'], reverse=True)
                    for opp in sorted_opps:
                        signal_comps = ', '.join(opp.get('signal_components', {}).keys())
                        logger.info(f"{opp['symbol']:<14} {opp['side']:<6} {opp['score']:<6} {signal_comps:<50}")

                    logger.info(f"{'='*100}\n")

                for opp in opportunities:
                    if self.get_open_positions_count() >= self.max_positions:
                        break

                    symbol = opp['symbol']
                    new_side = opp['side']
                    new_score = opp['score']
                    opposite_side = 'SHORT' if new_side == 'LONG' else 'LONG'

                    # 🔥 获取Big4状态（用于后续判断）
                    try:
                        big4_result = self.get_big4_result()
                    except Exception as e:
                        logger.error(f"[BIG4-ERROR] Big4检测失败: {e}")
                        big4_result = None

                    # 🔥 只做趋势单 - Big4中性检查（可配置禁用）
                    if self.big4_filter_config.get('enabled', True):
                        if big4_result:
                            big4_signal = big4_result.get('overall_signal', 'NEUTRAL')
                            big4_strength = big4_result.get('signal_strength', 0)
                            logger.info(f"📊 [TRADING-MODE] 固定趋势模式 | Big4: {big4_signal}({big4_strength:.1f})")

                            # 🚫 Big4中性时完全禁止开仓
                            # Big4中性意味着市场方向不明确，风险太高，完全禁止开仓
                            if big4_signal == 'NEUTRAL':
                                logger.warning(f"🚫 [BIG4-NEUTRAL-BLOCK] {symbol} Big4中性市场(强度{big4_strength:.1f}), 禁止开仓")
                                continue
                        else:
                            logger.warning(f"[BIG4-ERROR] {symbol} Big4数据不可用, 跳过开仓")
                            continue
                    else:
                        logger.debug(f"[BIG4-DISABLED] {symbol} Big4过滤已禁用，跳过中性检查")

                    # ========== 只接受趋势信号 ==========
                    signal_type = opp.get('signal_type', '')

                    # 🔥 只做趋势单,不再做震荡市单
                    # 紧急反弹信号(Big4触底)优先级最高
                    if signal_type == 'EMERGENCY_BOUNCE':
                        logger.warning(f"🚀 [EMERGENCY-BOUNCE] {symbol} 反弹信号")
                    elif 'TREND' in signal_type:
                        logger.info(f"[TREND-SIGNAL] {symbol} 趋势信号")
                    else:
                        # 非趋势信号,跳过
                        logger.debug(f"[SKIP-NON-TREND] {symbol} 非趋势信号,跳过 (类型: {signal_type[:40]})")
                        continue

                    # Big4 趋势检测 - 应用到所有币种（可配置禁用）
                    if self.big4_filter_config.get('enabled', True):
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

                            # 🚫 Big4中性已在上面被阻止，这里不应该到达
                            if symbol_signal == 'NEUTRAL':
                                logger.error(f"[LOGIC-ERROR] {symbol} NEUTRAL信号不应到达此处,已在前面被阻止")
                                continue

                            # ========== 破位否决检查 ==========
                            # Big4强度>=12时，完全禁止逆向开仓
                            should_skip, veto_reason = self.breakout_booster.should_skip_opposite_signal(
                                new_side,
                                new_score
                            )
                            if should_skip:
                                logger.warning(f"💥 [BREAKOUT-VETO] {symbol} {veto_reason}")
                                continue
                            # ========== 破位否决结束 ==========

                            # 📝 注意：Big4方向过滤和加分已在scan_all()中提前处理
                            # 这里只记录Big4状态信息，不再重复过滤和加分
                            logger.debug(f"[BIG4-INFO] {symbol} {new_side} | Big4: {symbol_signal}({signal_strength:.1f})")

                            # 更新机会的Big4状态信息 (用于后续记录)
                            opp['big4_adjusted'] = True
                            opp['big4_signal'] = symbol_signal
                            opp['big4_strength'] = signal_strength

                        except Exception as e:
                            logger.error(f"[BIG4-ERROR] {symbol} Big4检测失败: {e}")
                            # 失败不影响正常交易流程

                        # 🔥 紧急干预检查: 触底/触顶反转保护 (实时判断)
                        try:
                            emergency = big4_result.get('emergency_intervention', {})

                            # 🔥 新增: 实时检查市场恢复状态，绕过Big4检测器的15分钟缓存
                            should_block_long = emergency.get('block_long', False)
                            should_block_short = emergency.get('block_short', False)

                            # 如果有做空限制，实时检查是否已反弹3%+ (不依赖bottom_detected字段)
                            if should_block_short and new_side == 'SHORT':
                                # 快速检查: 查询最近4根1H K线，判断是否已反弹
                                try:
                                    conn_check = self._get_connection()
                                    cursor_check = conn_check.cursor(pymysql.cursors.DictCursor)

                                    # 检查Big4是否已完成3%反弹
                                    all_recovered = True
                                    for big4_symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
                                        cursor_check.execute("""
                                            SELECT low_price, close_price
                                            FROM kline_data
                                            WHERE symbol = %s
                                            AND timeframe = '1h'
                                            AND exchange = 'binance_futures'
                                            ORDER BY open_time DESC
                                            LIMIT 4
                                        """, (big4_symbol,))

                                        recent_klines = cursor_check.fetchall()
                                        if recent_klines:
                                            period_low = min([float(k['low_price']) for k in recent_klines])
                                            latest_close = float(recent_klines[0]['close_price'])
                                            recovery_pct = (latest_close - period_low) / period_low * 100

                                            if recovery_pct < 3.0:
                                                all_recovered = False
                                                break

                                    cursor_check.close()

                                    # 如果所有Big4都已反弹3%+，解除禁止做空
                                    if all_recovered:
                                        should_block_short = False
                                        logger.info(f"✅ [SMART-RELEASE] {symbol} 市场已反弹3%+，解除做空限制")

                                except Exception as check_error:
                                    logger.error(f"[SMART-RELEASE-ERROR] {symbol} 实时检查失败: {check_error}")

                            # 如果有做多限制，实时检查是否已回调3%+ (不依赖top_detected字段)
                            if should_block_long and new_side == 'LONG':
                                try:
                                    conn_check = self._get_connection()
                                    cursor_check = conn_check.cursor(pymysql.cursors.DictCursor)

                                    all_cooled = True
                                    for big4_symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
                                        cursor_check.execute("""
                                            SELECT high_price, close_price
                                            FROM kline_data
                                            WHERE symbol = %s
                                            AND timeframe = '1h'
                                            AND exchange = 'binance_futures'
                                            ORDER BY open_time DESC
                                            LIMIT 4
                                        """, (big4_symbol,))

                                        recent_klines = cursor_check.fetchall()
                                        if recent_klines:
                                            period_high = max([float(k['high_price']) for k in recent_klines])
                                            latest_close = float(recent_klines[0]['close_price'])
                                            cooldown_pct = (latest_close - period_high) / period_high * 100

                                            if cooldown_pct > -3.0:
                                                all_cooled = False
                                                break

                                    cursor_check.close()

                                    if all_cooled:
                                        should_block_long = False
                                        logger.info(f"✅ [SMART-RELEASE] {symbol} 市场已回调3%+，解除做多限制")

                                except Exception as check_error:
                                    logger.error(f"[SMART-RELEASE-ERROR] {symbol} 实时检查失败: {check_error}")

                            # 执行最终的阻止判断
                            if should_block_long and new_side == 'LONG':
                                logger.warning(f"🚨 [EMERGENCY-BLOCK] {symbol} 触顶反转风险,禁止做多 | {emergency.get('details', '')}")
                                continue
                            if should_block_short and new_side == 'SHORT':
                                logger.warning(f"🚨 [EMERGENCY-BLOCK] {symbol} 触底反弹风险,禁止做空 | {emergency.get('details', '')}")
                                continue

                        except Exception as e:
                            logger.error(f"[EMERGENCY-ERROR] {symbol} 紧急干预检查失败: {e}")
                            # 检查失败不影响正常交易

                    else:
                        # Big4过滤已禁用（测试模式）
                        logger.debug(f"[BIG4-DISABLED] {symbol} Big4过滤已禁用，直接使用原始信号 (测试模式)")

                    # 🔥 已移除"同方向只能1个持仓"的限制，支持分批建仓（多个独立持仓）
                    # 每批建仓都是独立的持仓记录，可以有多个同方向持仓
                    # if self.has_position(symbol, new_side):
                    #     logger.info(f"[SKIP] {symbol} {new_side}方向已有持仓")
                    #     continue

                    # 🔥 限制：同一交易对同方向只能有1个持仓
                    position_count = self.count_positions(symbol, new_side)
                    if position_count >= 1:
                        logger.info(f"[SKIP] {symbol} {new_side}方向已有{position_count}个持仓，达到上限(1)")
                        continue

                    # 检查是否刚刚平仓(15分钟冷却期)
                    if self.check_recent_close(symbol, new_side, cooldown_minutes=15):
                        logger.info(f"[SKIP] {symbol} {new_side}方向15分钟内刚平仓,冷却中")
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

    # 初始化智能平仓监控
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
