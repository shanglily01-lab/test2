#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U本位「破位策略」服务 - 复刻币本位策略逻辑到 USDT 标的
- 信号评分：14个组件，从 signal_scoring_weights(strategy_type='u_coin_style') 读取
- 破位系统：BreakoutSystem（检测 + 加权 + 持仓处理）
- 平仓：SmartExitOptimizer（止损/止盈/移动止盈/K线止盈）
- 实盘同步：BinanceFuturesEngine（live_trading_enabled=1 时）
- 开关：system_settings.u_coin_style_enabled
- 账户：account_id=2（U本位模拟），source='U_COIN_STYLE'
"""

import time
import sys
import os
import asyncio
import json
from datetime import datetime, timedelta
from decimal import Decimal
from loguru import logger
import pymysql
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.binance_ws_price import get_ws_price_service, BinanceWSPriceService
from app.services.adaptive_optimizer import AdaptiveOptimizer
from app.services.optimization_config import OptimizationConfig
from app.services.symbol_rating_manager import SymbolRatingManager
from app.services.volatility_profile_updater import VolatilityProfileUpdater
from app.services.smart_exit_optimizer import SmartExitOptimizer
from app.services.big4_trend_detector import Big4TrendDetector
from app.services.signal_blacklist_checker import SignalBlacklistChecker
from app.services.breakout_system import BreakoutSystem

load_dotenv()

# ──────────────────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────────────────
logger.remove()
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
    level="INFO"
)
logger.add(
    "logs/u_coin_style_trader_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
    level="INFO"
)

STRATEGY_TYPE = 'u_coin_style'
SOURCE_TAG    = 'U_COIN_STYLE'
ACCOUNT_ID    = 2
SWITCH_KEY    = 'u_coin_style_enabled'

# ──────────────────────────────────────────────────────────
# DatabaseExchangeAdapter（复用，K线从 DB 读取）
# ──────────────────────────────────────────────────────────

class DatabaseExchangeAdapter:
    """将数据库 K 线查询包装成类似 CCXT exchange 的接口，供 BreakoutSystem 使用"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.connection = None

    def _get_connection(self):
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config, charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                connect_timeout=10, read_timeout=30, write_timeout=30
            )
            with self.connection.cursor() as c:
                c.execute("SET SESSION innodb_lock_wait_timeout = 5")
        else:
            try:
                self.connection.ping(reconnect=True)
            except Exception:
                self.connection = pymysql.connect(
                    **self.db_config, charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                    connect_timeout=10, read_timeout=30, write_timeout=30
                )
                with self.connection.cursor() as c:
                    c.execute("SET SESSION innodb_lock_wait_timeout = 5")
        return self.connection

    def fetch_ohlcv(self, symbol: str, timeframe: str = '5m', limit: int = 288):
        """返回 CCXT 格式 K 线：[[ts, o, h, l, c, v], ...]"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT open_time, open_price, high_price, low_price, close_price, volume "
                "FROM kline_data "
                "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT %s",
                (symbol, timeframe, limit)
            )
            rows = cursor.fetchall()
            cursor.close()
            if not rows:
                return []
            klines = []
            for row in reversed(rows):
                klines.append([
                    int(row['open_time']),
                    float(row['open_price']),
                    float(row['high_price']),
                    float(row['low_price']),
                    float(row['close_price']),
                    float(row['volume'])
                ])
            return klines
        except Exception as e:
            logger.error(f"[DB-Adapter] 获取K线失败 {symbol} {timeframe}: {e}")
            return []


# ──────────────────────────────────────────────────────────
# U本位破位策略 决策大脑
# ──────────────────────────────────────────────────────────

class UCoinStyleBrain:
    """信号评分 + 破位系统，逻辑与币本位 CoinFuturesDecisionBrain 完全一致，
    仅 strategy_type、账号和 symbol 格式不同"""

    THRESHOLD = 60   # 开仓基础阈值

    # 多头/空头信号分类（用于方向清洗）
    BULLISH_SIGNALS = {
        'position_low', 'breakout_long', 'volume_power_bull',
        'volume_power_1h_bull', 'trend_1h_bull', 'momentum_up_3pct', 'consecutive_bull'
    }
    BEARISH_SIGNALS = {
        'position_high', 'breakdown_short', 'volume_power_bear',
        'volume_power_1h_bear', 'trend_1h_bear', 'momentum_down_3pct', 'consecutive_bear'
    }
    NEUTRAL_SIGNALS = {'position_mid', 'volatility_high'}

    def __init__(self, db_config: dict, trader_service=None):
        self.db_config   = db_config
        self.connection  = None
        self.trader_service = trader_service
        self._load_config()

        # 破位系统
        try:
            self.breakout_system = BreakoutSystem(DatabaseExchangeAdapter(db_config))
            logger.info("[U破位] 破位系统初始化成功")
        except Exception as e:
            logger.warning(f"[U破位] 破位系统初始化失败: {e}")
            self.breakout_system = None

        self.blacklist_checker = SignalBlacklistChecker(db_config, cache_minutes=5)

    # ── DB 连接 ───────────────────────────────────────────

    def _get_connection(self):
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config, charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=10, read_timeout=30, write_timeout=30
            )
            with self.connection.cursor() as c:
                c.execute("SET SESSION innodb_lock_wait_timeout = 5")
        else:
            try:
                self.connection.ping(reconnect=True)
            except Exception:
                self.connection = pymysql.connect(
                    **self.db_config, charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=10, read_timeout=30, write_timeout=30
                )
                with self.connection.cursor() as c:
                    c.execute("SET SESSION innodb_lock_wait_timeout = 5")
        return self.connection

    # ── 配置加载 ──────────────────────────────────────────

    def _load_config(self):
        try:
            import yaml
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)

            conn   = self._get_connection()
            cursor = conn.cursor()

            # 交易对白名单：TOP50 盈利榜
            cursor.execute(
                "SELECT symbol FROM top_performing_symbols ORDER BY rank_score DESC LIMIT 50"
            )
            top50 = [r['symbol'] for r in cursor.fetchall()]

            # Level3 永久禁止
            cursor.execute(
                "SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 3"
            )
            banned = {r['symbol'] for r in cursor.fetchall()}
            self.whitelist = [s for s in top50 if s not in banned]

            # 黑名单（小仓位）
            cursor.execute(
                "SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 1"
            )
            self.blacklist = [r['symbol'] for r in cursor.fetchall() if r['symbol'] not in banned]

            # 自适应参数
            cursor.execute("SELECT param_key, param_value FROM adaptive_params WHERE param_type='long'")
            lp = {r['param_key']: float(r['param_value']) for r in cursor.fetchall()}
            cursor.execute("SELECT param_key, param_value FROM adaptive_params WHERE param_type='short'")
            sp = {r['param_key']: float(r['param_value']) for r in cursor.fetchall()}

            self.adaptive_long  = {
                'stop_loss_pct':   lp.get('long_stop_loss_pct', 0.03),
                'take_profit_pct': lp.get('long_take_profit_pct', 0.02),
            }
            self.adaptive_short = {
                'stop_loss_pct':   sp.get('short_stop_loss_pct', 0.03),
                'take_profit_pct': sp.get('short_take_profit_pct', 0.02),
            }

            # 评分权重（strategy_type='u_coin_style'）
            cursor.execute(
                "SELECT signal_component, weight_long, weight_short "
                "FROM signal_scoring_weights "
                "WHERE is_active=1 AND strategy_type=%s",
                (STRATEGY_TYPE,)
            )
            rows = cursor.fetchall()
            self.scoring_weights = {
                r['signal_component']: {
                    'long':  float(r['weight_long']),
                    'short': float(r['weight_short'])
                }
                for r in rows
            }
            cursor.close()

            logger.info(f"[U破位] 白名单: {len(self.whitelist)}个 | 评分组件: {len(self.scoring_weights)}个")

        except Exception as e:
            logger.error(f"[U破位] 配置加载失败: {e}，使用内置默认值")
            self.whitelist  = []
            self.blacklist  = []
            self.scoring_weights = {}
            self.adaptive_long  = {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02}
            self.adaptive_short = {'stop_loss_pct': 0.03, 'take_profit_pct': 0.02}

    def reload_config(self):
        self._load_config()
        if hasattr(self, 'blacklist_checker'):
            self.blacklist_checker.force_reload()

    # ── K线加载 ──────────────────────────────────────────

    def load_klines(self, symbol: str, timeframe: str, limit: int = 100):
        """从数据库加载 K 线，返回字段：open/high/low/close/volume"""
        conn   = self._get_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        cursor.execute(
            "SELECT open_price as `open`, high_price as high, low_price as low, "
            "close_price as close, volume "
            "FROM kline_data "
            "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
            "AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 60 DAY))*1000 "
            "ORDER BY open_time DESC LIMIT %s",
            (symbol, timeframe, limit)
        )
        klines = list(cursor.fetchall())
        cursor.close()
        klines.reverse()
        for k in klines:
            for field in ('open', 'high', 'low', 'close', 'volume'):
                k[field] = float(k[field])
        return klines

    # ── 信号分析 ──────────────────────────────────────────

    def analyze(self, symbol: str, big4_result: dict = None):
        """对单个交易对评分，返回 {'symbol','side','score','current_price','signal_components','breakout_boost'} 或 None"""
        if symbol not in self.whitelist:
            return None
        try:
            klines_1d  = self.load_klines(symbol, '1d',  50)
            klines_1h  = self.load_klines(symbol, '1h', 100)
            klines_15m = self.load_klines(symbol, '15m', 96)

            if len(klines_1d) < 30 or len(klines_1h) < 72 or len(klines_15m) < 48:
                logger.debug(f"[U破位] {symbol} K线不足 1D:{len(klines_1d)} 1H:{len(klines_1h)} 15M:{len(klines_15m)}")
                return None

            current = klines_1h[-1]['close']
            long_score = short_score = 0
            comps: dict = {}
            w = self.scoring_weights  # 简写

            def W(key, default_l=0, default_s=0):
                return w.get(key, {'long': default_l, 'short': default_s})

            # 1. 位置评分（24H 高低点）
            high_24h = max(k['high'] for k in klines_1h[-24:])
            low_24h  = min(k['low']  for k in klines_1h[-24:])
            position_pct = (current - low_24h) / (high_24h - low_24h) * 100 if high_24h != low_24h else 50

            # 提前计算 1H 量能
            vols_1h = [k['volume'] for k in klines_1h[-24:]]
            avg_vol_1h = sum(vols_1h) / len(vols_1h) if vols_1h else 1
            bull_1h = sum(1 for k in klines_1h[-24:] if k['close'] > k['open'] and k['volume'] > avg_vol_1h * 1.2)
            bear_1h = sum(1 for k in klines_1h[-24:] if k['close'] <= k['open'] and k['volume'] > avg_vol_1h * 1.2)
            net_1h  = bull_1h - bear_1h

            if position_pct < 30:
                if net_1h > -2:
                    wt = W('position_low', 20, 0)
                    long_score += wt['long']
                    if wt['long']: comps['position_low'] = wt['long']
            elif position_pct > 70:
                big4_bullish = (big4_result and
                                big4_result.get('overall_signal') in ('BULLISH', 'STRONG_BULLISH') and
                                big4_result.get('signal_strength', 0) >= 50)
                if not big4_bullish:
                    wt = W('position_high', 0, 20)
                    short_score += wt['short']
                    if wt['short']: comps['position_high'] = wt['short']
            else:
                wt = W('position_mid', 5, 5)
                long_score  += wt['long']
                short_score += wt['short']
                if wt['long']: comps['position_mid'] = wt['long']

            # 2. 24H 动量
            gain_24h = (current - klines_1h[-24]['close']) / klines_1h[-24]['close'] * 100
            if gain_24h < -3:
                wt = W('momentum_down_3pct', 0, 15)
                short_score += wt['short']
                if wt['short']: comps['momentum_down_3pct'] = wt['short']
            elif gain_24h > 3:
                wt = W('momentum_up_3pct', 15, 0)
                long_score += wt['long']
                if wt['long']: comps['momentum_up_3pct'] = wt['long']

            # 3. 1H 趋势（24根K线）
            bull_1h_cnt = sum(1 for k in klines_1h[-24:] if k['close'] > k['open'])
            bear_1h_cnt = 24 - bull_1h_cnt
            if bull_1h_cnt >= 13:
                wt = W('trend_1h_bull', 20, 0)
                long_score += wt['long']
                if wt['long']: comps['trend_1h_bull'] = wt['long']
            elif bear_1h_cnt >= 13:
                wt = W('trend_1h_bear', 0, 20)
                short_score += wt['short']
                if wt['short']: comps['trend_1h_bear'] = wt['short']

            # 4. 波动率
            volatility = (max(k['high'] for k in klines_1h[-24:]) - min(k['low'] for k in klines_1h[-24:])) / current * 100
            if volatility > 5:
                wt = W('volatility_high', 10, 10)
                if long_score >= short_score:
                    long_score += wt['long']
                    if wt['long']: comps['volatility_high'] = wt['long']
                else:
                    short_score += wt['short']
                    if wt['short']: comps['volatility_high'] = wt['short']

            # 5. 连续趋势（10H）
            recent_10 = klines_1h[-10:]
            bull_10 = sum(1 for k in recent_10 if k['close'] > k['open'])
            bear_10 = 10 - bull_10
            gain_10h = (current - recent_10[0]['close']) / recent_10[0]['close'] * 100
            if bull_10 >= 7 and gain_10h < 5 and position_pct < 70:
                wt = W('consecutive_bull', 15, 0)
                long_score += wt['long']
                if wt['long']: comps['consecutive_bull'] = wt['long']
            elif bear_10 >= 7 and gain_10h > -5 and position_pct > 30:
                wt = W('consecutive_bear', 0, 15)
                short_score += wt['short']
                if wt['short']: comps['consecutive_bear'] = wt['short']

            # 6. 15M 量能
            vols_15m = [k['volume'] for k in klines_15m[-24:]]
            avg_vol_15m = sum(vols_15m) / len(vols_15m) if vols_15m else 1
            bull_15m = sum(1 for k in klines_15m[-24:] if k['close'] > k['open'] and k['volume'] > avg_vol_15m * 1.2)
            bear_15m = sum(1 for k in klines_15m[-24:] if k['close'] <= k['open'] and k['volume'] > avg_vol_15m * 1.2)
            net_15m  = bull_15m - bear_15m

            if net_1h >= 2 and net_15m >= 2:
                wt = W('volume_power_bull', 25, 0)
                long_score += wt['long']
                if wt['long']: comps['volume_power_bull'] = wt['long']
            elif net_1h <= -2 and net_15m <= -2:
                wt = W('volume_power_bear', 0, 25)
                short_score += wt['short']
                if wt['short']: comps['volume_power_bear'] = wt['short']
            elif net_1h >= 3:
                wt = W('volume_power_1h_bull', 15, 0)
                long_score += wt['long']
                if wt['long']: comps['volume_power_1h_bull'] = wt['long']
            elif net_1h <= -3:
                wt = W('volume_power_1h_bear', 0, 15)
                short_score += wt['short']
                if wt['short']: comps['volume_power_1h_bear'] = wt['short']

            # 7. 突破追涨 / 破位追空
            if position_pct > 70 and net_1h >= 2:
                can_breakout = True
                # 过滤：长上影线
                for k in klines_1h[-3:]:
                    us = (k['high'] - max(k['open'], k['close'])) / k['close'] if k['close'] else 0
                    if us > 0.015:
                        can_breakout = False
                        break
                # 过滤：连续4天上涨
                if sum(1 for k in klines_1d[-5:] if k['close'] > k['open']) >= 4:
                    can_breakout = False
                if can_breakout:
                    wt = W('breakout_long', 20, 0)
                    long_score += wt['long']
                    if wt['long']: comps['breakout_long'] = wt['long']
            elif position_pct < 30 and net_1h <= -2:
                wt = W('breakdown_short', 0, 20)
                short_score += wt['short']
                if wt['short']: comps['breakdown_short'] = wt['short']

            # ── 动态阈值 ──────────────────────────────────
            big4_bullish = (big4_result and
                            big4_result.get('overall_signal') in ('BULLISH', 'STRONG_BULLISH') and
                            big4_result.get('signal_strength', 0) >= 50)
            if big4_bullish:
                long_threshold = 50
            else:
                nb = big4_result.get('neutral_bias', 'FLAT') if big4_result else 'FLAT'
                long_threshold = self.THRESHOLD + 8 if nb == 'DOWN' else self.THRESHOLD

            # ── 方向决策 ──────────────────────────────────
            long_ok  = long_score  >= long_threshold
            short_ok = short_score >= self.THRESHOLD

            if not (long_ok or short_ok):
                return None

            side  = ('LONG' if long_score >= short_score else 'SHORT') if (long_ok and short_ok) else ('LONG' if long_ok else 'SHORT')
            score = long_score if side == 'LONG' else short_score

            # 清洗：只保留与最终方向一致的信号
            cleaned = {}
            for sig, val in comps.items():
                if sig in self.NEUTRAL_SIGNALS:
                    cleaned[sig] = val
                elif side == 'LONG'  and sig in self.BULLISH_SIGNALS:
                    cleaned[sig] = val
                elif side == 'SHORT' and sig in self.BEARISH_SIGNALS:
                    cleaned[sig] = val
            comps = cleaned

            if len(comps) < 2:
                return None
            if 'position_mid' in comps and len(comps) < 3:
                return None

            # 方向矛盾检查
            if side == 'LONG'  and 'position_high' in comps:
                return None
            if side == 'SHORT' and 'position_low' in comps:
                return None

            # 信号黑名单
            combo_key = " + ".join(sorted(comps.keys()))
            blocked, reason = self.blacklist_checker.is_blacklisted(combo_key, side)
            if blocked:
                logger.info(f"[U破位] {symbol} 信号黑名单阻止: {reason}")
                return None

            # Big4 紧急干预
            if big4_result:
                emg = big4_result.get('emergency_intervention', {})
                if emg.get('block_long') and side == 'LONG':
                    return None
                if emg.get('block_short') and side == 'SHORT':
                    return None

            # 破位系统加权
            breakout_boost = 0
            if self.breakout_system:
                try:
                    sr = self.breakout_system.calculate_signal_score(
                        symbol=symbol, base_score=score,
                        signal_direction=side, current_price=current
                    )
                    if sr.get('should_skip'):
                        logger.info(f"[U破位] {symbol} 破位系统阻止: {sr.get('skip_reason')}")
                        return None
                    breakout_boost = sr.get('boost_score', 0)
                    if breakout_boost > 0 and sr.get('should_generate'):
                        self.breakout_system.record_opening(symbol)
                    score = sr.get('total_score', score)
                except Exception as e:
                    logger.warning(f"[U破位] {symbol} 破位加权失败: {e}")

            logger.info(f"[U破位] {symbol} {side} 评分{score}(+破位{breakout_boost}) 信号:[{combo_key}]")
            return {
                'symbol': symbol, 'side': side, 'score': score,
                'current_price': current, 'signal_components': comps,
                'breakout_boost': breakout_boost
            }

        except Exception as e:
            logger.error(f"[U破位] {symbol} 分析失败: {e}")
            return None

    def scan_all(self, big4_result: dict = None):
        logger.info(f"\n{'='*70}")
        logger.info(f"[U破位] 扫描 {len(self.whitelist)} 个交易对 | 阈值: {self.THRESHOLD}分")
        opps = [r for sym in self.whitelist if (r := self.analyze(sym, big4_result))]
        logger.info(f"[U破位] 合格信号: {len(opps)} 个")
        logger.info(f"{'='*70}\n")
        return opps


# ──────────────────────────────────────────────────────────
# 主服务类
# ──────────────────────────────────────────────────────────

class UCoinStyleTraderService:
    """U本位破位策略交易服务"""

    MAX_POSITIONS  = 20   # 总持仓上限（模拟盘不限得太死）
    MARGIN_DEFAULT = 400  # 默认单笔保证金（U）
    MARGIN_L1      = 100
    MARGIN_L2      = 50
    LEVERAGE       = 5
    SCAN_INTERVAL  = 300  # 秒

    def __init__(self):
        self.db_config = {
            'host':     os.getenv('DB_HOST', 'localhost'),
            'port':     int(os.getenv('DB_PORT', '3306')),
            'user':     os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'binance-data'),
        }
        self.account_id = ACCOUNT_ID
        self.connection = None
        self.running    = True
        self.event_loop = None

        self.brain         = UCoinStyleBrain(self.db_config, trader_service=self)
        self.ws_service    = get_ws_price_service(market_type='futures')
        self.opt_config    = OptimizationConfig(self.db_config)
        self.big4_detector = Big4TrendDetector()
        self.big4_symbols  = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f)
            smart_exit_cfg = cfg.get('signals', {}).get('smart_exit', {'enabled': False})

        self.smart_exit_optimizer = SmartExitOptimizer(
            db_config=self.db_config,
            live_engine=self,
            price_service=self.ws_service,
            account_id=self.account_id
        ) if smart_exit_cfg.get('enabled') else None

        from app.services.trade_notifier import TradeNotifier as _TN
        self.telegram_notifier = _TN({'notifications': {'telegram': {
            'enabled':       bool(os.getenv('TELEGRAM_BOT_TOKEN')),
            'bot_token':     os.getenv('TELEGRAM_BOT_TOKEN', ''),
            'chat_id':       os.getenv('TELEGRAM_CHAT_ID', ''),
            'notify_events': ['all']
        }}})

        logger.info("=" * 60)
        logger.info("U本位破位策略服务已启动")
        logger.info(f"账户ID={self.account_id} | 杠杆={self.LEVERAGE}x | 扫描间隔={self.SCAN_INTERVAL}s")
        logger.info(f"白名单: {len(self.brain.whitelist)} 个交易对 | 评分组件: {len(self.brain.scoring_weights)}")
        logger.info("=" * 60)

    # ── DB 连接 ───────────────────────────────────────────

    def _get_connection(self):
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config, autocommit=True,
                connect_timeout=10, read_timeout=30, write_timeout=30
            )
            with self.connection.cursor() as c:
                c.execute("SET SESSION innodb_lock_wait_timeout = 5")
        else:
            try:
                self.connection.ping(reconnect=True)
            except Exception:
                self.connection = pymysql.connect(
                    **self.db_config, autocommit=True,
                    connect_timeout=10, read_timeout=30, write_timeout=30
                )
                with self.connection.cursor() as c:
                    c.execute("SET SESSION innodb_lock_wait_timeout = 5")
        return self.connection

    def _read_setting(self, key: str, default: str = '0') -> str:
        try:
            conn   = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)
            cursor.execute("SELECT setting_value FROM system_settings WHERE setting_key=%s", (key,))
            row = cursor.fetchone()
            cursor.close()
            return row['setting_value'] if row else default
        except Exception:
            return default

    # ── 系统开关 ──────────────────────────────────────────

    def is_enabled(self) -> bool:
        val = self._read_setting(SWITCH_KEY, '0')
        return str(val).strip() in ('1', 'true', 'True', 'yes')

    def is_live_trading_enabled(self) -> bool:
        val = self._read_setting('live_trading_enabled', '0')
        return str(val).strip() in ('1', 'true', 'True', 'yes')

    # ── Big4 ──────────────────────────────────────────────

    def get_big4_result(self) -> dict:
        try:
            return self.big4_detector.detect_market_trend()
        except Exception as e:
            logger.error(f"[U破位] Big4检测失败: {e}")
            return {'overall_signal': 'NEUTRAL', 'signal_strength': 0, 'details': {}, 'timestamp': datetime.now()}

    # ── 持仓查询 ──────────────────────────────────────────

    def get_open_count(self) -> int:
        try:
            conn = self._get_connection()
            cur  = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM futures_positions WHERE status='open' AND account_id=%s", (self.account_id,))
            r = cur.fetchone()
            cur.close()
            return r[0] if r else 0
        except Exception:
            return 0

    def has_position(self, symbol: str, side: str) -> bool:
        try:
            conn = self._get_connection()
            cur  = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM futures_positions "
                "WHERE symbol=%s AND position_side=%s AND status IN ('open','building') AND account_id=%s",
                (symbol, side, self.account_id)
            )
            r = cur.fetchone()
            cur.close()
            return (r[0] if r else 0) > 0
        except Exception:
            return False

    def check_recent_close(self, symbol: str, side: str, cooldown_minutes: int = 15) -> bool:
        try:
            conn = self._get_connection()
            cur  = conn.cursor()
            since = datetime.utcnow() - timedelta(minutes=cooldown_minutes)
            cur.execute(
                "SELECT COUNT(*) FROM futures_positions "
                "WHERE symbol=%s AND position_side=%s AND account_id=%s "
                "AND status='closed' AND close_time >= %s",
                (symbol, side, self.account_id, since)
            )
            r = cur.fetchone()
            cur.close()
            return (r[0] if r else 0) > 0
        except Exception:
            return False

    # ── 保证金计算 ────────────────────────────────────────

    def _get_margin(self, symbol: str) -> float:
        level = self.opt_config.get_symbol_rating_level(symbol)
        if level == 0:
            return float(self.MARGIN_DEFAULT)
        elif level == 1:
            return float(self.MARGIN_L1)
        elif level == 2:
            return float(self.MARGIN_L2)
        else:
            return 0.0  # Level3 禁止

    # ── 熔断检查 ──────────────────────────────────────────

    def _check_fuse(self) -> bool:
        """过去3H亏损超300U触发亏损熔断，返回True=触发"""
        attr = '_fuse_check_last'
        last = getattr(self, attr, None)
        if last and (datetime.utcnow() - last).total_seconds() < 1800:
            return False
        setattr(self, attr, datetime.utcnow())
        try:
            conn = self._get_connection()
            cur  = conn.cursor()
            since = datetime.utcnow() - timedelta(hours=3)
            cur.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) FROM futures_positions "
                "WHERE status='closed' AND account_id=%s AND close_time>=%s",
                (self.account_id, since)
            )
            pnl = float(cur.fetchone()[0])
            cur.close()
            if pnl <= -300:
                self._disable_self(f"亏损熔断: 过去3H亏损{pnl:.1f}U")
                return True
            return False
        except Exception as e:
            logger.error(f"[U破位] 熔断检查失败: {e}")
            return False

    def _disable_self(self, reason: str):
        try:
            conn = self._get_connection()
            cur  = conn.cursor()
            cur.execute(
                "UPDATE system_settings SET setting_value='0', description=%s, updated_at=NOW() "
                "WHERE setting_key=%s",
                (reason, SWITCH_KEY)
            )
            cur.close()
            logger.warning(f"[U破位] 自动停止: {reason}")
            try:
                self.telegram_notifier.send_message(f"[U破位策略] 自动停止\n{reason}")
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[U破位] 停止自身失败: {e}")

    # ── 价格获取 ──────────────────────────────────────────

    def get_price(self, symbol: str):
        if self.ws_service:
            p = self.ws_service.get_price(symbol)
            if p and p > 0:
                return float(p)
        try:
            conn = self._get_connection()
            cur  = conn.cursor()
            cur.execute(
                "SELECT close_price, open_time FROM kline_data "
                "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol,)
            )
            row = cur.fetchone()
            cur.close()
            if not row:
                return None
            age_min = (int(time.time() * 1000) - row[1]) / 1000 / 60
            if age_min > 15:
                return None
            return float(row[0])
        except Exception:
            return None

    # ── 实盘同步 ──────────────────────────────────────────

    def _sync_live(self, symbol: str, side: str, entry_price: float,
                   paper_pos_id: int, sl_pct: float, tp_pct: float):
        if not self.is_live_trading_enabled():
            return
        try:
            from app.services.api_key_service import APIKeyService
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            svc  = APIKeyService(self.db_config)
            keys = svc.get_all_active_api_keys('binance')
        except Exception as e:
            logger.error(f"[U破位] 获取实盘账号失败: {e}")
            return

        MAX_LIVE = 5
        for ak in keys:
            try:
                # 检查实盘持仓上限
                conn = self._get_connection()
                cur  = conn.cursor(pymysql.cursors.DictCursor)
                cur.execute(
                    "SELECT COUNT(*) as cnt FROM live_futures_positions WHERE account_id=%s AND status='OPEN'",
                    (ak['id'],)
                )
                cnt = (cur.fetchone() or {}).get('cnt', 0)
                cur.close()
                if cnt >= MAX_LIVE:
                    logger.info(f"[U破位] {ak['account_name']} 实盘已有{cnt}单，跳过 {symbol}")
                    continue

                margin = float(ak.get('max_position_value') or 100)
                lev    = int(ak.get('max_leverage') or 5)
                qty    = Decimal(str(round(margin * lev / entry_price, 6)))
                engine = BinanceFuturesEngine(
                    self.db_config,
                    api_key=ak['api_key'],
                    api_secret=ak['api_secret']
                )
                result = engine.open_position(
                    account_id=ak['id'], symbol=symbol, position_side=side,
                    quantity=qty, leverage=lev,
                    stop_loss_pct=Decimal(str(sl_pct * 100)),
                    take_profit_pct=Decimal(str(tp_pct * 100)),
                    source=SOURCE_TAG, paper_position_id=paper_pos_id
                )
                if result.get('success'):
                    logger.info(f"[U破位] 实盘下单成功 {ak['account_name']} {symbol} {side}")
                    try:
                        sl = round(entry_price * (1 - sl_pct), 4) if side == 'LONG' else round(entry_price * (1 + sl_pct), 4)
                        tp = round(entry_price * (1 + tp_pct), 4) if side == 'LONG' else round(entry_price * (1 - tp_pct), 4)
                        self.telegram_notifier.notify_open_position(
                            symbol=symbol, direction=side,
                            quantity=float(qty), entry_price=entry_price,
                            leverage=lev, stop_loss_price=sl, take_profit_price=tp,
                            margin=margin, strategy_name=f'U破位[{ak["account_name"]}]'
                        )
                    except Exception:
                        pass
                else:
                    logger.error(f"[U破位] 实盘下单失败 {ak['account_name']} {symbol}: {result.get('error','')}")
            except Exception as e:
                logger.error(f"[U破位] 实盘同步异常 {ak.get('account_name','')} {symbol}: {e}")

    # ── 开仓 ──────────────────────────────────────────────

    def open_position(self, opp: dict) -> bool:
        symbol = opp['symbol']
        side   = opp['side']

        # 只处理 /USDT 交易对
        if not symbol.endswith('/USDT'):
            logger.error(f"[U破位] {symbol} 不是U本位交易对，拒绝")
            return False

        signal_components = opp.get('signal_components', {})

        # 方向允许检查
        if not self.opt_config.is_direction_allowed(side):
            return False

        # 平仓冷却
        if self.check_recent_close(symbol, side, cooldown_minutes=15):
            return False

        # 反向持仓检查
        opp_side = 'SHORT' if side == 'LONG' else 'LONG'
        if self.has_position(symbol, opp_side):
            logger.info(f"[U破位] {symbol} 已有反向持仓，跳过")
            return False

        # 获取价格
        entry_price = self.get_price(symbol)
        if not entry_price:
            logger.warning(f"[U破位] {symbol} 获取价格失败")
            return False

        # 保证金
        margin = self._get_margin(symbol)
        if margin == 0:
            return False

        # Big4 动态保证金
        try:
            b4 = self.get_big4_result()
            sig = b4.get('overall_signal', 'NEUTRAL')
            if (sig in ('BULLISH', 'STRONG_BULLISH') and side == 'LONG') or \
               (sig in ('BEARISH', 'STRONG_BEARISH') and side == 'SHORT'):
                margin *= 1.2
        except Exception:
            pass

        # 自适应参数
        ap = self.brain.adaptive_long if side == 'LONG' else self.brain.adaptive_short
        sl_pct = ap.get('stop_loss_pct', 0.03)
        tp_pct = ap.get('take_profit_pct', 0.02)

        # 波动率自适应止损
        if 'volatility_high' in signal_components:
            sl_pct = sl_pct * 1.5

        quantity      = margin * self.LEVERAGE / entry_price
        notional      = quantity * entry_price
        stop_loss     = entry_price * (1 - sl_pct) if side == 'LONG' else entry_price * (1 + sl_pct)
        take_profit   = entry_price * (1 + tp_pct) if side == 'LONG' else entry_price * (1 - tp_pct)

        entry_score   = opp.get('score', 0)
        combo_key     = "TREND_" + " + ".join(sorted(signal_components.keys())) if signal_components else "TREND_unknown"
        timeout_min   = self.opt_config.get_timeout_by_score(entry_score)
        timeout_at    = datetime.utcnow() + timedelta(minutes=timeout_min)

        try:
            conn   = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, entry_score, signal_components, max_hold_minutes, timeout_at,
                 planned_close_time, source, status, created_at, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s,%s,%s,%s,
                        DATE_ADD(NOW(), INTERVAL %s MINUTE),%s,'open',NOW(),NOW())
            """, (
                self.account_id, symbol, side,
                round(quantity, 6), entry_price, entry_price,
                self.LEVERAGE, round(notional, 2), round(margin, 2),
                round(stop_loss, 8), round(take_profit, 8),
                combo_key, entry_score,
                json.dumps(signal_components) if signal_components else None,
                timeout_min, timeout_at, timeout_min,
                SOURCE_TAG
            ))
            position_id = cursor.lastrowid
            cursor.close()

            logger.info(
                f"[U破位] 开仓 {symbol} {side} @ {entry_price:.6g} "
                f"SL={stop_loss:.6g} TP={take_profit:.6g} 评分={entry_score}"
            )

            # 启动 SmartExitOptimizer 监控
            if self.smart_exit_optimizer and self.event_loop:
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.smart_exit_optimizer.start_monitoring_position(position_id),
                        self.event_loop
                    )
                except Exception as e:
                    logger.error(f"[U破位] 持仓{position_id}监控启动失败: {e}")

            # 实盘同步
            self._sync_live(symbol, side, entry_price, position_id, sl_pct, tp_pct)

            return True

        except Exception as e:
            logger.error(f"[U破位] {symbol} 开仓写入失败: {e}")
            return False

    # ── WebSocket 初始化 ──────────────────────────────────

    async def init_ws_service(self):
        if self.ws_service and hasattr(self.ws_service, 'start'):
            await self.ws_service.start()
            logger.info("[U破位] WebSocket 价格服务已启动")

    async def _start_smart_exit_monitoring(self):
        if self.smart_exit_optimizer and hasattr(self.smart_exit_optimizer, 'start_monitoring_existing'):
            await self.smart_exit_optimizer.start_monitoring_existing()

    def _check_and_restart_smart_exit_optimizer(self):
        if not self.smart_exit_optimizer:
            return
        if hasattr(self.smart_exit_optimizer, 'is_healthy') and not self.smart_exit_optimizer.is_healthy():
            logger.warning("[U破位] SmartExitOptimizer 异常，尝试重启")
            try:
                self.smart_exit_optimizer = SmartExitOptimizer(
                    db_config=self.db_config, live_engine=self,
                    price_service=self.ws_service, account_id=self.account_id
                )
                if self.event_loop:
                    asyncio.run_coroutine_threadsafe(
                        self._start_smart_exit_monitoring(), self.event_loop
                    )
            except Exception as e:
                logger.error(f"[U破位] SmartExitOptimizer 重启失败: {e}")

    # ── 主循环 ────────────────────────────────────────────

    def run(self):
        last_config_reload    = datetime.now()
        last_exit_check       = datetime.now()

        while self.running:
            try:
                now = datetime.now()

                # 每60秒检查 SmartExitOptimizer 健康
                if (now - last_exit_check).total_seconds() >= 60:
                    self._check_and_restart_smart_exit_optimizer()
                    last_exit_check = now

                # 每5分钟重载配置
                if (now - last_config_reload).total_seconds() >= 300:
                    self.brain.reload_config()
                    last_config_reload = now

                # 服务开关
                if not self.is_enabled():
                    logger.debug("[U破位] 策略未启用，等待...")
                    time.sleep(self.SCAN_INTERVAL)
                    continue

                # 熔断
                if self._check_fuse():
                    logger.warning("[U破位] 熔断已触发，停止开仓")
                    time.sleep(self.SCAN_INTERVAL)
                    continue

                # 持仓上限
                cnt = self.get_open_count()
                if cnt >= self.MAX_POSITIONS:
                    logger.info(f"[U破位] 持仓已满({cnt}/{self.MAX_POSITIONS})，跳过")
                    time.sleep(self.SCAN_INTERVAL)
                    continue

                # Big4
                big4_result = self.get_big4_result()

                # Big4 NEUTRAL 时停止开仓（与币本位保持一致）
                try:
                    from app.services.system_settings_loader import get_big4_filter_enabled
                    if get_big4_filter_enabled() and big4_result.get('overall_signal') == 'NEUTRAL':
                        logger.info(f"[U破位] Big4 NEUTRAL，跳过开仓")
                        time.sleep(self.SCAN_INTERVAL)
                        continue
                except Exception:
                    pass

                # 扫描
                opps = self.brain.scan_all(big4_result=big4_result)
                if not opps:
                    time.sleep(self.SCAN_INTERVAL)
                    continue

                # 执行
                for opp in opps:
                    if self.get_open_count() >= self.MAX_POSITIONS:
                        break

                    sym      = opp['symbol']
                    new_side = opp['side']
                    opp_side = 'SHORT' if new_side == 'LONG' else 'LONG'

                    # Big4 方向过滤
                    try:
                        sym_sig = big4_result.get('overall_signal', 'NEUTRAL')
                        sym_str = big4_result.get('signal_strength', 0)
                        if sym in self.big4_symbols:
                            detail  = big4_result.get('details', {}).get(sym, {})
                            sym_sig = detail.get('signal', 'NEUTRAL')
                            sym_str = detail.get('strength', 0)

                        is_bull = sym_sig in ('BULLISH', 'STRONG_BULLISH')
                        is_bear = sym_sig in ('BEARISH', 'STRONG_BEARISH')

                        if is_bear and new_side == 'LONG':
                            logger.debug(f"[U破位] {sym} Big4看空，禁止LONG")
                            continue
                        if is_bull and new_side == 'SHORT':
                            logger.debug(f"[U破位] {sym} Big4看多，禁止SHORT")
                            continue

                        # 同向加分
                        if (is_bull and new_side == 'LONG') or (is_bear and new_side == 'SHORT'):
                            opp['score'] += min(20, int(sym_str * 0.3))
                    except Exception:
                        pass

                    # 同方向持仓去重
                    if self.has_position(sym, new_side):
                        continue

                    # 已有反向仓
                    if self.has_position(sym, opp_side):
                        continue

                    # 冷却
                    if self.check_recent_close(sym, new_side, cooldown_minutes=15):
                        continue

                    self.open_position(opp)
                    time.sleep(2)

                time.sleep(self.SCAN_INTERVAL)

            except KeyboardInterrupt:
                logger.info("[U破位] 收到停止信号")
                self.running = False
                break
            except Exception as e:
                logger.error(f"[U破位] 主循环异常: {e}")
                time.sleep(60)

        logger.info("[U破位] 服务已停止")


# ──────────────────────────────────────────────────────────
# 异步入口
# ──────────────────────────────────────────────────────────

async def async_main():
    service = UCoinStyleTraderService()
    service.event_loop = asyncio.get_event_loop()
    await service.init_ws_service()
    if service.smart_exit_optimizer:
        await service._start_smart_exit_monitoring()
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, service.run)


if __name__ == '__main__':
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("U本位破位策略服务已停止")
