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

        self.threshold = 30  # 开仓阈值与超时配置对齐 (30/35/40/45分档)

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

            cursor.execute("""
                SELECT symbol FROM trading_blacklist
                WHERE is_active = TRUE
                ORDER BY created_at DESC
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
                    'position_mid': {'long': 5, 'short': 5},
                    'position_high': {'long': 0, 'short': 20},
                    'momentum_down_3pct': {'long': 15, 'short': 0},
                    'momentum_up_3pct': {'long': 0, 'short': 15},
                    'trend_1h_bull': {'long': 20, 'short': 0},
                    'trend_1h_bear': {'long': 0, 'short': 20},
                    'volatility_high': {'long': 10, 'short': 10},
                    'consecutive_bull': {'long': 15, 'short': 0},
                    'consecutive_bear': {'long': 0, 'short': 15},
                    'trend_1d_bull': {'long': 10, 'short': 0},
                    'trend_1d_bear': {'long': 0, 'short': 10}
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

    def analyze(self, symbol: str):
        """分析并决策 - 支持做多和做空 (主要使用1小时K线)"""
        if symbol not in self.whitelist:
            return None

        try:
            klines_1d = self.load_klines(symbol, '1d', 50)
            klines_1h = self.load_klines(symbol, '1h', 100)

            if len(klines_1d) < 30 or len(klines_1h) < 72:  # 至少需要72小时(3天)数据
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

            # 低位做多，高位做空
            if position_pct < 30:
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
            if gain_24h < -3:  # 24小时跌超过3%
                weight = self.scoring_weights.get('momentum_down_3pct', {'long': 15, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['momentum_down_3pct'] = weight['long']
            elif gain_24h > 3:  # 24小时涨超过3%
                weight = self.scoring_weights.get('momentum_up_3pct', {'long': 0, 'short': 15})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['momentum_up_3pct'] = weight['short']

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

            # ========== 1天K线确认 (辅助) ==========

            # 大趋势确认: 如果30天趋势与1小时趋势一致，加分
            bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
            bearish_1d = 30 - bullish_1d

            if bullish_1d > 18 and long_score > short_score:  # 大趋势上涨且1小时也看多
                weight = self.scoring_weights.get('trend_1d_bull', {'long': 10, 'short': 0})
                long_score += weight['long']
                if weight['long'] > 0:
                    signal_components['trend_1d_bull'] = weight['long']
            elif bearish_1d > 18 and short_score > long_score:  # 大趋势下跌且1小时也看空
                weight = self.scoring_weights.get('trend_1d_bear', {'long': 0, 'short': 10})
                short_score += weight['short']
                if weight['short'] > 0:
                    signal_components['trend_1d_bear'] = weight['short']

            # 选择得分更高的方向 (只要达到阈值就可以)
            if long_score >= self.threshold or short_score >= self.threshold:
                if long_score >= short_score:
                    side = 'LONG'
                    score = long_score
                else:
                    side = 'SHORT'
                    score = short_score

                # 检查信号黑名单
                signal_key = f"SMART_BRAIN_{score}_{side}"
                if signal_key in self.signal_blacklist:
                    logger.debug(f"{symbol} 信号 {signal_key} 在黑名单中，跳过")
                    return None

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

    def get_current_price(self, symbol: str):
        """获取当前价格 - 带数据新鲜度检查 (使用5m K线)"""
        try:
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
                # 检查特定方向的持仓
                cursor.execute("""
                    SELECT COUNT(*) FROM futures_positions
                    WHERE symbol = %s AND position_side = %s AND status = 'open' AND account_id = %s
                """, (symbol, side, self.account_id))
            else:
                # 检查任意方向的持仓
                cursor.execute("""
                    SELECT COUNT(*) FROM futures_positions
                    WHERE symbol = %s AND status = 'open' AND account_id = %s
                """, (symbol, self.account_id))

            result = cursor.fetchone()
            cursor.close()
            return result[0] > 0 if result else False
        except:
            return False

    def open_position(self, opp: dict):
        """开仓 - 支持做多和做空，使用 WebSocket 实时价格"""
        symbol = opp['symbol']
        side = opp['side']  # 'LONG' 或 'SHORT'

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
            if is_reversal and 'original_margin' in opp:
                # 反转开仓: 使用原仓位相同的保证金
                adjusted_position_size = opp['original_margin']
                logger.info(f"[REVERSAL_MARGIN] {symbol} 反转开仓, 使用原仓位保证金: ${adjusted_position_size:.2f}")

                # 仍需获取自适应参数用于止损止盈
                if side == 'LONG':
                    adaptive_params = self.brain.adaptive_long
                else:  # SHORT
                    adaptive_params = self.brain.adaptive_short
            else:
                # 正常开仓流程
                # 问题2优化: 使用3级评级制度替代简单黑名单
                rating_level = self.opt_config.get_symbol_rating_level(symbol)
                rating_config = self.opt_config.get_blacklist_config(rating_level)

                # Level 3 = 永久禁止
                if rating_level == 3:
                    logger.warning(f"[BLACKLIST_LEVEL3] {symbol} 已被永久禁止交易")
                    return False

                # 获取评级对应的保证金倍数
                rating_margin_multiplier = rating_config['margin_multiplier']
                base_position_size = self.position_size_usdt * rating_margin_multiplier

                # 记录评级信息
                rating_tag = f"[Level{rating_level}]" if rating_level > 0 else "[白名单]"
                logger.info(f"{rating_tag} {symbol} 保证金倍数: {rating_margin_multiplier:.2f}")

                # 使用自适应参数调整仓位大小
                if side == 'LONG':
                    position_multiplier = self.brain.adaptive_long.get('position_size_multiplier', 1.0)
                    adaptive_params = self.brain.adaptive_long
                else:  # SHORT
                    position_multiplier = self.brain.adaptive_short.get('position_size_multiplier', 1.0)
                    adaptive_params = self.brain.adaptive_short

                # 应用仓位倍数
                adjusted_position_size = base_position_size * position_multiplier

                # 问题3优化: 检查是否为对冲开仓,如果是则应用对冲保证金倍数
                opposite_side = 'SHORT' if side == 'LONG' else 'LONG'
                is_hedge = self.has_position(symbol, opposite_side)
                if is_hedge:
                    hedge_multiplier = self.opt_config.get_hedge_margin_multiplier()
                    adjusted_position_size = adjusted_position_size * hedge_multiplier
                    logger.info(f"[HEDGE_MARGIN] {symbol} 对冲开仓, 保证金缩减到{hedge_multiplier*100:.0f}%")

            quantity = adjusted_position_size * self.leverage / current_price
            notional_value = quantity * current_price
            margin = adjusted_position_size

            # 使用自适应参数计算止损
            stop_loss_pct = adaptive_params.get('stop_loss_pct', 0.03)

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

            # 问题1优化: 计算动态超时时间
            base_timeout_minutes = self.opt_config.get_timeout_by_score(entry_score)
            # 计算超时时间点 (UTC时间)
            from datetime import datetime, timedelta
            timeout_at = datetime.utcnow() + timedelta(minutes=base_timeout_minutes)

            # 插入持仓记录 (包含动态超时字段)
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_score, signal_components, max_hold_minutes, timeout_at,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, 'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, side, quantity, current_price, self.leverage,
                notional_value, margin, stop_loss, take_profit,
                f"SMART_BRAIN_{opp['score']}", entry_score, signal_components_json,
                base_timeout_minutes, timeout_at
            ))

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

            logger.info(
                f"[SUCCESS] {symbol} {side}开仓成功{rating_tag}{hedge_tag} | "
                f"止损: ${stop_loss:.4f} ({sl_pct}) | 止盈: ${take_profit:.4f} ({tp_pct}) | "
                f"仓位: ${margin:.0f} (x{position_multiplier:.1f}) | 超时: {base_timeout_minutes}分钟"
            )
            return True

        except Exception as e:
            logger.error(f"[ERROR] {symbol} 开仓失败: {e}")
            return False

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

    def check_stop_loss_take_profit(self):
        """检查止盈止损 + 智能趋势监控"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 获取所有持仓
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price,
                       stop_loss_price, take_profit_price, open_time
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (self.account_id,))

            positions = cursor.fetchall()

            for pos in positions:
                pos_id, symbol, position_side, quantity, entry_price, stop_loss, take_profit, open_time = pos
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                should_close = False
                close_reason = None
                top_bottom_reversal_signal = None  # 用于记录顶底反转信号

                # 0. 检查最小持仓时间 (自适应参数)
                from datetime import datetime
                now = datetime.utcnow()
                holding_minutes = (now - open_time).total_seconds() / 60

                # 获取该方向的最小持仓时间
                if position_side == 'LONG':
                    min_holding_minutes = self.brain.adaptive_long.get('min_holding_minutes', 60)
                else:  # SHORT
                    min_holding_minutes = self.brain.adaptive_short.get('min_holding_minutes', 60)

                # 如果未达到最小持仓时间，跳过止损检查（但仍允许止盈）
                below_min_holding = holding_minutes < min_holding_minutes

                # 1. 固定止损检查 (保底风控) - 但要考虑最小持仓时间
                if not below_min_holding:  # 只有达到最小持仓时间才允许止损
                    if position_side == 'LONG':
                        if stop_loss and current_price <= float(stop_loss):
                            should_close = True
                            close_reason = 'STOP_LOSS'
                    elif position_side == 'SHORT':
                        if stop_loss and current_price >= float(stop_loss):
                            should_close = True
                            close_reason = 'STOP_LOSS'

                # 2. 智能顶底识别 (优先于固定止盈) - 要求至少持仓2小时
                if not should_close and holding_minutes >= 120:  # 至少持仓2小时才检查顶底
                    is_top_bottom, tb_reason = self.check_top_bottom(symbol, position_side, float(entry_price))
                    if is_top_bottom:
                        should_close = True
                        close_reason = tb_reason
                        # 记录顶底识别标志,用于后续反转开仓
                        top_bottom_reversal_signal = (symbol, position_side, tb_reason, float(margin))

                # 3. 固定止盈作为兜底 (如果顶底识别没触发)
                if not should_close:
                    if position_side == 'LONG':
                        if take_profit and current_price >= float(take_profit):
                            should_close = True
                            close_reason = 'TAKE_PROFIT'
                    elif position_side == 'SHORT':
                        if take_profit and current_price <= float(take_profit):
                            should_close = True
                            close_reason = 'TAKE_PROFIT'

                if should_close:
                    # Calculate PnL percentage
                    pnl_pct = (current_price - float(entry_price)) / float(entry_price) * 100
                    if position_side == 'SHORT':
                        pnl_pct = -pnl_pct

                    # Calculate realized PnL in USDT
                    if position_side == 'LONG':
                        realized_pnl = (current_price - float(entry_price)) * float(quantity)
                    else:  # SHORT
                        realized_pnl = (float(entry_price) - current_price) * float(quantity)

                    logger.info(
                        f"[{close_reason}] {symbol} {position_side} | "
                        f"开仓: ${entry_price:.4f} | 平仓: ${current_price:.4f} | "
                        f"盈亏: {pnl_pct:+.2f}% ({realized_pnl:+.2f} USDT)"
                    )

                    # Get leverage and margin for ROI calculation
                    cursor.execute("""
                        SELECT leverage, margin FROM futures_positions WHERE id = %s
                    """, (pos_id,))
                    pos_detail = cursor.fetchone()
                    leverage = pos_detail[0] if pos_detail else 1
                    margin = float(pos_detail[1]) if pos_detail else 0.0
                    roi = (realized_pnl / margin) * 100 if margin > 0 else 0

                    cursor.execute("""
                        UPDATE futures_positions
                        SET status = 'closed', mark_price = %s,
                            realized_pnl = %s,
                            notes = %s,
                            close_time = NOW(), updated_at = NOW()
                        WHERE id = %s
                    """, (current_price, realized_pnl, close_reason, pos_id))

                    # Calculate values for orders and trades
                    import uuid
                    close_side = 'CLOSE_LONG' if position_side == 'LONG' else 'CLOSE_SHORT'
                    notional_value = current_price * float(quantity)
                    fee = notional_value * 0.0004  # 0.04% taker fee
                    order_id = f'CLOSE-{pos_id}'
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
                        self.account_id, order_id, pos_id, symbol,
                        close_side, leverage,
                        current_price, quantity, quantity,
                        notional_value, notional_value,
                        fee, 0.0004,
                        current_price, datetime.utcnow(),
                        realized_pnl, pnl_pct, close_reason
                    ))

                    # Create futures_trades record for frontend display
                    cursor.execute("""
                        INSERT INTO futures_trades (
                            trade_id, position_id, account_id, symbol, side,
                            price, quantity, notional_value, leverage, margin,
                            fee, realized_pnl, pnl_pct, roi, entry_price,
                            order_id, trade_time, created_at
                        ) VALUES (
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s,
                            %s, %s, %s
                        )
                    """, (
                        trade_id, pos_id, self.account_id, symbol, close_side,
                        current_price, quantity, notional_value, leverage, margin,
                        fee, realized_pnl, pnl_pct, roi, entry_price,
                        f'CLOSE-{pos_id}', datetime.utcnow(), datetime.utcnow()
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

                    # Update win rate
                    cursor.execute("""
                        UPDATE futures_trading_accounts
                        SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                        WHERE id = %s
                    """, (self.account_id,))

                    # 5. 顶底反转开仓逻辑
                    if top_bottom_reversal_signal:
                        rev_symbol, rev_old_side, rev_reason, rev_margin = top_bottom_reversal_signal

                        # 确定反向方向(识别到顶部就做空,识别到底部就做多)
                        if 'TOP_DETECTED' in rev_reason and rev_old_side == 'LONG':
                            reverse_side = 'SHORT'
                        elif 'BOTTOM_DETECTED' in rev_reason and rev_old_side == 'SHORT':
                            reverse_side = 'LONG'
                        else:
                            reverse_side = None

                        if reverse_side:
                            logger.info(
                                f"[REVERSAL] {rev_symbol} {rev_reason} | "
                                f"平{rev_old_side}后立即开{reverse_side} | "
                                f"保证金: ${rev_margin:.2f}"
                            )

                            # 获取当前价格
                            reverse_price = self.get_current_price(rev_symbol)
                            if not reverse_price:
                                logger.error(f"[ERROR] {rev_symbol} 反转开仓失败: 无法获取价格")
                            else:
                                # 构造反向开仓机会(不需要评分,因为顶底识别本身就是强信号)
                                reverse_opp = {
                                    'symbol': rev_symbol,
                                    'side': reverse_side,
                                    'score': 99,  # 给一个高分,表示这是顶底反转信号
                                    'current_price': reverse_price,
                                    'signal_components': {'top_bottom_reversal': 99},
                                    'reversal_from': rev_reason,  # 标记这是反转开仓
                                    'original_margin': rev_margin  # 使用原仓位保证金
                                }

                                # 提交数据库更改,避免冲突
                                conn.commit()
                                cursor.close()

                                # 执行反向开仓
                                try:
                                    self.open_position(reverse_opp)
                                except Exception as e:
                                    logger.error(f"[ERROR] {rev_symbol} 反转开仓失败: {e}")

                                # 重新获取cursor以继续循环
                                cursor = conn.cursor()
                                cursor.execute("""
                                    SELECT id, symbol, position_side, quantity, entry_price,
                                           stop_loss_price, take_profit_price, open_time
                                    FROM futures_positions
                                    WHERE status = 'open' AND account_id = %s
                                """, (self.account_id,))
                                positions = cursor.fetchall()

            cursor.close()

        except Exception as e:
            logger.error(f"[ERROR] 检查止盈止损失败: {e}")

    def close_old_positions(self):
        """
        问题1优化: 关闭超时持仓 (动态超时 + 分阶段超时)
        - 动态超时: 根据entry_score和当前盈亏调整超时时间
        - 分阶段超时: 1h/2h/3h/4h检查不同的亏损阈值
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 先检查有多少开仓持仓
            cursor.execute("""
                SELECT COUNT(*) FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (self.account_id,))
            total_open = cursor.fetchone()[0]

            # 查询所有开仓持仓 (包含动态超时字段)
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price, margin, leverage,
                       created_at, entry_score, max_hold_minutes, timeout_at,
                       TIMESTAMPDIFF(MINUTE, created_at, NOW()) as minutes_old
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (self.account_id,))

            open_positions = cursor.fetchall()

            if not open_positions:
                cursor.close()
                return

            # 获取分阶段超时阈值
            staged_thresholds = self.opt_config.get_staged_timeout_thresholds()

            timeout_positions = []  # 需要超时平仓的持仓

            for pos in open_positions:
                pos_id, symbol, position_side, quantity, entry_price, margin, leverage, \
                created_at, entry_score, max_hold_minutes, timeout_at, minutes_old = pos

                # 获取当前价格
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                # 计算当前盈亏
                if position_side == 'LONG':
                    realized_pnl = (current_price - float(entry_price)) * float(quantity)
                else:  # SHORT
                    realized_pnl = (float(entry_price) - current_price) * float(quantity)

                pnl_pct = (realized_pnl / (float(entry_price) * float(quantity))) if float(quantity) > 0 else 0
                hours_old = minutes_old / 60

                # 方案1: 动态超时 - 检查是否达到timeout_at
                if timeout_at:
                    from datetime import datetime
                    now_utc = datetime.utcnow()
                    if now_utc >= timeout_at:
                        timeout_positions.append((pos_id, symbol, position_side, quantity, entry_price,
                                                margin, leverage, current_price, realized_pnl, pnl_pct,
                                                hours_old, f"DYNAMIC_TIMEOUT({max_hold_minutes}min)"))
                        continue

                # 方案2: 分阶段超时 - 检查不同时间节点的亏损阈值
                for hour_checkpoint, loss_threshold in sorted(staged_thresholds.items()):
                    if hours_old >= hour_checkpoint:
                        # 检查是否达到该阶段的亏损阈值
                        if pnl_pct < loss_threshold:
                            timeout_positions.append((pos_id, symbol, position_side, quantity, entry_price,
                                                    margin, leverage, current_price, realized_pnl, pnl_pct,
                                                    hours_old, f"STAGED_TIMEOUT_{hour_checkpoint}H(亏损{pnl_pct*100:.1f}%>{loss_threshold*100:.1f}%)"))
                            break  # 找到就退出,避免重复

            logger.info(f"[TIMEOUT_CHECK] 总持仓: {total_open}, 超时持仓: {len(timeout_positions)}")

            if not timeout_positions:
                cursor.close()
                return

            # 执行超时平仓
            for pos_data in timeout_positions:
                pos_id, symbol, position_side, quantity, entry_price, margin, leverage, \
                current_price, realized_pnl, pnl_pct, hours_old, close_reason = pos_data

                logger.info(f"[CLOSE_TIMEOUT] {symbol} {position_side} 超时平仓 | "
                          f"价格: ${current_price:.4f} | 盈亏: {realized_pnl:+.2f} USDT ({pnl_pct*100:+.2f}%) | "
                          f"原因: {close_reason}")

                roi = (realized_pnl / float(margin)) * 100 if margin and float(margin) > 0 else 0

                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'closed', mark_price = %s,
                        realized_pnl = %s,
                        notes = %s,
                        close_time = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (current_price, realized_pnl, close_reason, pos_id))

                # Calculate values for orders and trades
                import uuid
                close_side = 'CLOSE_LONG' if position_side == 'LONG' else 'CLOSE_SHORT'
                notional_value = current_price * float(quantity)
                fee = notional_value * 0.0004  # 0.04% taker fee
                order_id = f'TIMEOUT-{pos_id}'
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
                    self.account_id, order_id, pos_id, symbol,
                    close_side, leverage,
                    current_price, quantity, quantity,
                    notional_value, notional_value,
                    fee, 0.0004,
                    current_price, datetime.utcnow(),
                    realized_pnl, pnl_pct, close_reason
                ))

                # Create futures_trades record for frontend display

                cursor.execute("""
                    INSERT INTO futures_trades (
                        trade_id, position_id, account_id, symbol, side,
                        price, quantity, notional_value, leverage, margin,
                        fee, realized_pnl, pnl_pct, roi, entry_price,
                        order_id, trade_time, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s
                    )
                """, (
                    trade_id, pos_id, self.account_id, symbol, close_side,
                    current_price, quantity, notional_value, leverage, margin,
                    fee, realized_pnl, pnl_pct, roi, entry_price,
                    f'TIMEOUT-{pos_id}', datetime.utcnow(), datetime.utcnow()
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

                # Update win rate
                cursor.execute("""
                    UPDATE futures_trading_accounts
                    SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
                    WHERE id = %s
                """, (self.account_id,))

            cursor.close()

        except Exception as e:
            logger.error(f"[ERROR] 关闭超时持仓失败: {e}")

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
                                    order_id, trade_time, created_at
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s
                                )
                            """, (
                                trade_id, long_pos['id'], self.account_id, symbol, 'CLOSE_LONG',
                                current_price, long_pos['quantity'], notional_value, leverage, margin,
                                fee, long_pos['realized_pnl'], long_pos['pnl_pct'], roi, long_pos['entry_price'],
                                f"HEDGE-{long_pos['id']}", datetime.utcnow(), datetime.utcnow()
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
                                    order_id, trade_time, created_at
                                ) VALUES (
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s, %s, %s,
                                    %s, %s, %s
                                )
                            """, (
                                trade_id, short_pos['id'], self.account_id, symbol, 'CLOSE_SHORT',
                                current_price, short_pos['quantity'], notional_value, leverage, margin,
                                fee, short_pos['realized_pnl'], short_pos['pnl_pct'], roi, short_pos['entry_price'],
                                order_id, datetime.utcnow(), datetime.utcnow()
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

    def check_recent_close(self, symbol: str, side: str, cooldown_minutes: int = 10):
        """
        检查指定交易对和方向是否在冷却期内(刚刚平仓)
        返回True表示在冷却期,不应该开仓
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
                        order_id, trade_time, created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s
                    )
                """, (
                    trade_id, pos['id'], self.account_id, symbol, close_side,
                    current_price, quantity, notional_value, leverage, margin,
                    fee, realized_pnl, pnl_pct, roi, entry_price,
                    order_id, datetime.utcnow(), datetime.utcnow()
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

    def run(self):
        """主循环"""
        while self.running:
            try:
                # 0. 检查是否需要运行每日自适应优化 (凌晨2点)
                self.check_and_run_daily_optimization()

                # 1. 检查止盈止损
                self.check_stop_loss_take_profit()

                # 2. 检查对冲持仓(平掉亏损方向)
                self.check_hedge_positions()

                # 3. 关闭超时持仓
                self.close_old_positions()

                # 4. 检查持仓
                current_positions = self.get_open_positions_count()
                logger.info(f"[STATUS] 持仓: {current_positions}/{self.max_positions}")

                if current_positions >= self.max_positions:
                    logger.info("[SKIP] 已达最大持仓,跳过扫描")
                    time.sleep(self.scan_interval)
                    continue

                # 5. 扫描机会
                logger.info(f"[SCAN] 扫描 {len(self.brain.whitelist)} 个币种...")
                opportunities = self.brain.scan_all()

                if not opportunities:
                    logger.info("[SCAN] 无交易机会")
                    time.sleep(self.scan_interval)
                    continue

                # 6. 执行交易
                logger.info(f"[EXECUTE] 找到 {len(opportunities)} 个机会")

                for opp in opportunities:
                    if self.get_open_positions_count() >= self.max_positions:
                        break

                    symbol = opp['symbol']
                    new_side = opp['side']
                    new_score = opp['score']
                    opposite_side = 'SHORT' if new_side == 'LONG' else 'LONG'

                    # 检查同方向是否已有持仓
                    if self.has_position(symbol, new_side):
                        logger.info(f"[SKIP] {symbol} {new_side}方向已有持仓")
                        continue

                    # 检查是否刚刚平仓(10分钟冷却期)
                    if self.check_recent_close(symbol, new_side, cooldown_minutes=10):
                        logger.info(f"[SKIP] {symbol} {new_side}方向10分钟内刚平仓,冷却中")
                        continue

                    # 检查是否有反向持仓
                    if self.has_position(symbol, opposite_side):
                        # 获取反向持仓的开仓得分
                        old_score = self.get_position_score(symbol, opposite_side)

                        # 问题2+3优化: 综合反转阈值 = 基础阈值(15分) + 评级额外阈值
                        # 黑名单等级越高,反转阈值越高,更难反转
                        rating_level = self.opt_config.get_symbol_rating_level(symbol)
                        rating_config = self.opt_config.get_blacklist_config(rating_level)
                        base_reversal_threshold = self.opt_config.get_hedge_reversal_threshold()
                        rating_reversal_threshold = rating_config['reversal_threshold']

                        # 使用两者中的较大值
                        reversal_threshold = max(base_reversal_threshold, rating_reversal_threshold - old_score)
                        if reversal_threshold < base_reversal_threshold:
                            reversal_threshold = base_reversal_threshold

                        # 如果新信号比旧信号强(反转阈值)以上 -> 主动反向平仓
                        if new_score > old_score + reversal_threshold:
                            logger.info(
                                f"[REVERSE] {symbol} 检测到强反向信号! "
                                f"原{opposite_side}得分{old_score}, 新{new_side}得分{new_score} "
                                f"(差距{new_score-old_score}分 > 阈值{reversal_threshold}分)"
                            )

                            # 平掉反向持仓
                            self.close_position_by_side(
                                symbol,
                                opposite_side,
                                reason=f"reverse_signal|new_{new_side}_score:{new_score}|old_score:{old_score}|threshold:{reversal_threshold}"
                            )

                            # 开新方向
                            self.open_position(opp)
                            time.sleep(2)
                            continue

                        # 反向信号不够强,允许对冲
                        logger.info(
                            f"[HEDGE] {symbol} 已有{opposite_side}(得分{old_score})持仓, "
                            f"新{new_side}得分{new_score}未达反转阈值(需>{old_score+reversal_threshold:.0f}), 允许对冲"
                        )

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

    # 初始化 WebSocket 服务
    await service.init_ws_service()

    # 在事件循环中运行同步的主循环
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, service.run)


if __name__ == '__main__':
    try:
        # 运行异步主函数
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
