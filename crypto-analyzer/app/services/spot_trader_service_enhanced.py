#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易服务 - 增强版 (底部反转抄底策略)
基于对超级大脑的分析,专注捕捉触底反弹机会 (现货做多)
核心策略: 识别明显下跌后的底部反转信号,任何时间都可触发
"""

import time
import sys
import os
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pymysql
from dotenv import load_dotenv
import yaml

# 导入 WebSocket 价格服务 (现货模式)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.binance_ws_price import get_ws_price_service

# 加载环境变量
load_dotenv()


class EnhancedSpotSignalGenerator:
    """增强版现货信号生成器 - 专注底部反转抄底"""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self.connection = None

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        return self.connection

    def _get_kline_data(self, symbol: str, timeframe: str = '5m', limit: int = 100) -> List[dict]:
        """获取K线数据"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 转换为币安格式: BTC/USDT -> BTCUSDT
            binance_symbol = symbol.replace('/', '')

            cursor.execute("""
                SELECT open_time, open_price, high_price, low_price, close_price, volume
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY open_time DESC
                LIMIT %s
            """, (binance_symbol, timeframe, limit))

            results = cursor.fetchall()
            cursor.close()

            # 反转顺序，使最新的在最后
            return list(reversed(results)) if results else []
        except Exception as e:
            logger.error(f"获取K线数据失败 {symbol}: {e}")
            return []

    def _calculate_rsi(self, prices: List[float], period: int = 14) -> float:
        """计算RSI指标"""
        if len(prices) < period + 1:
            return 50.0

        deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def _calculate_ema(self, prices: List[float], period: int) -> float:
        """计算EMA指标"""
        if len(prices) < period:
            return sum(prices) / len(prices)

        multiplier = 2 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price - ema) * multiplier + ema

        return ema

    def _calculate_bollinger_bands(self, prices: List[float], period: int = 20, std_multiplier: float = 2.0) -> Tuple[float, float, float]:
        """计算布林带 (上轨, 中轨, 下轨)"""
        if len(prices) < period:
            avg = sum(prices) / len(prices)
            return avg, avg, avg

        recent_prices = prices[-period:]
        middle = sum(recent_prices) / period

        variance = sum((p - middle) ** 2 for p in recent_prices) / period
        std = variance ** 0.5

        upper = middle + std_multiplier * std
        lower = middle - std_multiplier * std

        return upper, middle, lower

    def _check_recent_dump(self, klines: List[dict]) -> Tuple[bool, float]:
        """
        检查最近是否有明显下跌 (触底信号)
        返回: (是否有下跌, 下跌幅度%)
        """
        if len(klines) < 20:
            return False, 0.0

        # 检查最近10根K线的最高价和最低价
        recent_klines = klines[-10:]
        highs = [float(k['high_price']) for k in recent_klines]
        lows = [float(k['low_price']) for k in recent_klines]

        max_high = max(highs)
        min_low = min(lows)
        current_price = float(klines[-1]['close_price'])

        # 计算从高点的跌幅
        drop_pct = (max_high - current_price) / max_high * 100

        # 如果跌幅 >= 2%,认为是明显下跌
        if drop_pct >= 2:
            return True, drop_pct

        return False, 0.0

    def _detect_hammer_candle(self, klines: List[dict], index: int = -1) -> Tuple[bool, float]:
        """
        检测锤头线 (Hammer) - 底部反转信号
        返回: (是否锤头线, 强度分数)
        """
        if len(klines) < abs(index) + 1:
            return False, 0.0

        k = klines[index]
        open_price = float(k['open_price'])
        close_price = float(k['close_price'])
        high_price = float(k['high_price'])
        low_price = float(k['low_price'])

        # 计算实体和影线
        body = abs(close_price - open_price)
        lower_shadow = min(open_price, close_price) - low_price
        upper_shadow = high_price - max(open_price, close_price)
        total_range = high_price - low_price

        if total_range == 0:
            return False, 0.0

        # 锤头线特征:
        # 1. 下影线长度 >= 实体的2倍
        # 2. 上影线很短 (< 实体)
        # 3. 收盘价接近最高价
        is_hammer = (
            lower_shadow >= body * 2 and
            upper_shadow < body and
            close_price >= open_price  # 阳线更好
        )

        if is_hammer:
            # 计算强度 (下影线越长,信号越强)
            strength = min(100, (lower_shadow / total_range) * 100)
            return True, strength

        return False, 0.0

    def strategy_bottom_reversal(self, symbol: str) -> Tuple[float, str]:
        """
        策略E: 底部反转抄底 (现货专用 - 任何时间)

        核心逻辑:
        - 最近出现明显下跌 (触底前提)
        - RSI < 35 (深度超卖)
        - 价格触及或跌破布林带下轨
        - 出现锤头线/晨星等反转形态
        - 成交量放大确认反转

        返回: (信号强度 0-100, 描述)
        """
        klines = self._get_kline_data(symbol, timeframe='5m', limit=100)
        if len(klines) < 50:
            return 0.0, "数据不足"

        closes = [float(k['close_price']) for k in klines]
        volumes = [float(k['volume']) for k in klines]
        current_price = closes[-1]

        # 1. 检查最近是否有下跌 (触底前提)
        has_dump, dump_pct = self._check_recent_dump(klines)

        # 2. 计算技术指标
        rsi = self._calculate_rsi(closes, period=14)
        upper, middle, lower = self._calculate_bollinger_bands(closes, period=20)

        # 3. 检测锤头线
        is_hammer, hammer_strength = self._detect_hammer_candle(klines)

        # 4. 成交量分析
        avg_volume = sum(volumes[-20:-1]) / 19 if len(volumes) > 20 else sum(volumes) / len(volumes)
        current_volume = volumes[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

        # 5. 检查是否出现反弹 (价格开始上涨)
        price_change = (current_price - closes[-2]) / closes[-2] * 100

        score = 0.0
        reasons = []

        # 6. 评分系统

        # 6.1 先决条件: 必须有下跌才能抄底 (否则不是底部)
        if not has_dump:
            return 0.0, "无明显下跌,不触发抄底"

        # 6.2 下跌幅度加分 (最高20分) - 跌得越多,反弹空间越大
        if dump_pct >= 5:
            score += 20
            reasons.append(f"深度下跌{dump_pct:.1f}%")
        elif dump_pct >= 3:
            score += 15
            reasons.append(f"明显下跌{dump_pct:.1f}%")
        elif dump_pct >= 2:
            score += 10
            reasons.append(f"小幅下跌{dump_pct:.1f}%")

        # 6.3 RSI超卖 (最高35分)
        if rsi < 25:
            score += 35
            reasons.append(f"极度超卖RSI{rsi:.1f}")
        elif rsi < 30:
            oversold_score = (30 - rsi) / 30 * 30
            score += oversold_score
            reasons.append(f"深度超卖RSI{rsi:.1f}")
        elif rsi < 40:
            score += 15
            reasons.append(f"超卖RSI{rsi:.1f}")

        # 6.4 布林带下轨 (最高25分)
        distance_to_lower = (current_price - lower) / lower * 100
        if distance_to_lower < -2:  # 深度跌破下轨
            score += 25
            reasons.append(f"深跌下轨{abs(distance_to_lower):.1f}%")
        elif distance_to_lower < -1:  # 跌破下轨
            score += 20
            reasons.append(f"跌破下轨{abs(distance_to_lower):.1f}%")
        elif distance_to_lower < 1:  # 触及下轨
            score += 15
            reasons.append("触及下轨")

        # 6.5 锤头线形态 (最高30分)
        if is_hammer:
            score += min(30, hammer_strength * 0.3)
            reasons.append(f"锤头线反转{hammer_strength:.0f}")

        # 6.6 价格反弹确认 (最高15分)
        if price_change > 0.5:
            score += min(15, price_change * 5)
            reasons.append(f"反弹{price_change:.2f}%")

        # 6.7 成交量放大确认 (最高15分)
        if volume_ratio > 1.5:
            score += min(15, (volume_ratio - 1) * 10)
            reasons.append(f"量能{volume_ratio:.1f}x")

        desc = f"底部抄底: {', '.join(reasons)}" if reasons else "无信号"
        return min(100, score), desc

    def strategy_a_trend_breakout(self, symbol: str) -> Tuple[float, str]:
        """策略A: 趋势突破"""
        klines = self._get_kline_data(symbol, timeframe='5m', limit=100)
        if len(klines) < 50:
            return 0.0, "数据不足"

        closes = [float(k['close_price']) for k in klines]
        volumes = [float(k['volume']) for k in klines]

        current_price = closes[-1]
        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)
        upper, middle, lower = self._calculate_bollinger_bands(closes, period=20)

        avg_volume = sum(volumes[-20:-1]) / 19
        current_volume = volumes[-1]

        score = 0.0
        reasons = []

        if current_price > upper:
            distance = (current_price - upper) / upper * 100
            score += min(40, distance * 10)
            reasons.append(f"突破上轨{distance:.2f}%")

        if ema9 > ema21:
            trend_strength = (ema9 - ema21) / ema21 * 100
            score += min(30, trend_strength * 5)
            reasons.append(f"上升趋势{trend_strength:.2f}%")

        if current_volume > avg_volume * 1.5:
            volume_ratio = current_volume / avg_volume
            score += min(30, (volume_ratio - 1) * 15)
            reasons.append(f"量能{volume_ratio:.1f}x")

        desc = f"趋势突破: {', '.join(reasons)}" if reasons else "无信号"
        return score, desc

    def strategy_b_oversold_bounce(self, symbol: str) -> Tuple[float, str]:
        """策略B: 超卖反弹"""
        klines = self._get_kline_data(symbol, timeframe='5m', limit=100)
        if len(klines) < 50:
            return 0.0, "数据不足"

        closes = [float(k['close_price']) for k in klines]
        current_price = closes[-1]
        prev_price = closes[-2]

        rsi = self._calculate_rsi(closes, period=14)
        upper, middle, lower = self._calculate_bollinger_bands(closes, period=20)

        score = 0.0
        reasons = []

        if rsi < 30:
            oversold_strength = (30 - rsi) / 30 * 100
            score += min(50, oversold_strength * 0.5)
            reasons.append(f"RSI超卖{rsi:.1f}")
        elif rsi < 40:
            score += 20
            reasons.append(f"RSI偏低{rsi:.1f}")

        distance_to_lower = (current_price - lower) / lower * 100
        if distance_to_lower < 2:
            score += min(30, (2 - distance_to_lower) * 15)
            reasons.append(f"触及下轨")

        if current_price > prev_price:
            bounce_strength = (current_price - prev_price) / prev_price * 100
            score += min(20, bounce_strength * 10)
            reasons.append(f"反转上涨{bounce_strength:.2f}%")

        desc = f"超卖反弹: {', '.join(reasons)}" if reasons else "无信号"
        return score, desc

    def generate_signal(self, symbol: str) -> Dict:
        """
        生成买入信号 (底部反转优先)
        现货只做多,专注抄底反弹
        """
        try:
            # E策略 - 底部反转抄底 (现货核心策略)
            score_e, desc_e = self.strategy_bottom_reversal(symbol)

            # B策略 - 超卖反弹 (辅助)
            score_b, desc_b = self.strategy_b_oversold_bounce(symbol)

            # A策略 - 趋势突破 (保留,但权重较低)
            score_a, desc_a = self.strategy_a_trend_breakout(symbol)

            # E策略权重最高
            scores = {
                'E_底部抄底': score_e,
                'B_超卖反弹': score_b,
                'A_趋势突破': score_a
            }

            best_strategy = max(scores, key=scores.get)
            best_score = scores[best_strategy]

            # 组合描述
            details = []
            if score_e > 20:
                details.append(f"E:{score_e:.0f}⭐")  # ⭐标记核心策略
            if score_b > 20:
                details.append(f"B:{score_b:.0f}")
            if score_a > 20:
                details.append(f"A:{score_a:.0f}")

            return {
                'symbol': symbol,
                'signal_strength': best_score,
                'best_strategy': best_strategy,
                'all_scores': scores,
                'details': ' | '.join(details) if details else '无信号',
                'timestamp': datetime.utcnow(),
                'is_bottom_reversal': score_e >= 60  # 标记是否为底部反转信号
            }
        except Exception as e:
            logger.error(f"生成信号失败 {symbol}: {e}")
            return {
                'symbol': symbol,
                'signal_strength': 0.0,
                'best_strategy': 'ERROR',
                'all_scores': {},
                'details': str(e),
                'timestamp': datetime.utcnow(),
                'is_bottom_reversal': False
            }


class EnhancedSpotPositionManager:
    """增强版现货仓位管理器"""

    # 调整建仓比例 - 现货更激进 (无爆仓风险)
    BATCH_RATIOS = [0.15, 0.15, 0.25, 0.25, 0.20]  # 前期更积极

    def __init__(self, db_config: dict, total_capital: float = 50000, per_coin_capital: float = 10000):
        self.db_config = db_config
        self.connection = None

        self.total_capital = total_capital
        self.per_coin_capital = per_coin_capital
        self.reserve_ratio = 0.20
        self.max_positions = 5

        # 风险管理 - 现货更宽松
        self.take_profit_pct = 0.30  # 30% 止盈 (降低,快速止盈)
        self.stop_loss_pct = 0.15    # 15% 止损 (放宽,允许波动)

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        return self.connection

    def get_current_positions(self) -> List[Dict]:
        """获取当前持仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM spot_positions
                WHERE status = 'active'
                ORDER BY created_at DESC
            """)

            positions = cursor.fetchall()
            cursor.close()

            return positions if positions else []
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def can_open_new_position(self) -> bool:
        """是否可以开新仓位"""
        current_positions = self.get_current_positions()
        return len(current_positions) < self.max_positions

    def calculate_batch_amount(self, signal_strength: float, batch_index: int, is_bottom_reversal: bool = False) -> float:
        """
        根据信号强度和信号类型计算买入金额

        底部反转信号: 首批加仓30% (更激进)
        """
        base_amount = self.per_coin_capital * self.BATCH_RATIOS[batch_index]

        # 强信号加仓
        if batch_index == 0 and signal_strength > 85:
            base_amount *= 1.15

        # 底部反转信号首批大幅加仓 (现货无爆仓风险)
        if batch_index == 0 and is_bottom_reversal:
            base_amount *= 1.3  # 底部抄底更激进

        return base_amount

    def create_position(self, symbol: str, entry_price: float, quantity: float, signal_data: Dict) -> bool:
        """创建新持仓记录"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 计算目标价格
            take_profit_price = entry_price * (1 + self.take_profit_pct)
            stop_loss_price = entry_price * (1 - self.stop_loss_pct)

            cursor.execute("""
                INSERT INTO spot_positions (
                    symbol, entry_price, avg_entry_price, quantity, total_cost,
                    current_batch, take_profit_price, stop_loss_price,
                    signal_strength, signal_details, status, created_at, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', NOW(), NOW()
                )
            """, (
                symbol,
                entry_price,
                entry_price,
                quantity,
                entry_price * quantity,
                1,
                take_profit_price,
                stop_loss_price,
                signal_data['signal_strength'],
                signal_data['details']
            ))

            conn.commit()
            cursor.close()

            reversal_tag = " [底部反转]" if signal_data.get('is_bottom_reversal') else ""
            logger.info(f"✅ 创建持仓{reversal_tag}: {symbol} @ {entry_price:.4f}, 数量: {quantity:.2f}, 批次: 1/5")
            return True
        except Exception as e:
            logger.error(f"创建持仓失败: {e}")
            return False

    # ... 其他方法保持不变 ...


class SpotTraderServiceEnhanced:
    """增强版现货交易服务主类 - 专注底部反转抄底"""

    def __init__(self):
        # 数据库配置
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'binance-data')
        }

        # 初始化组件 (使用增强版)
        self.signal_generator = EnhancedSpotSignalGenerator(self.db_config)
        self.position_manager = EnhancedSpotPositionManager(self.db_config)

        # WebSocket 现货价格服务
        self.ws_price_service = get_ws_price_service(market_type='spot')

        # 加载交易对列表
        self.symbols = self._load_symbols_from_config()

        logger.info("=" * 80)
        logger.info("🚀 增强版现货交易服务启动 (底部反转抄底专用)")
        logger.info(f"总资金: {self.position_manager.total_capital:,.0f} USDT")
        logger.info(f"单币资金: {self.position_manager.per_coin_capital:,.0f} USDT")
        logger.info(f"最大持仓: {self.position_manager.max_positions} 个")
        logger.info(f"止盈: {self.position_manager.take_profit_pct*100:.0f}%, 止损: {self.position_manager.stop_loss_pct*100:.0f}%")
        logger.info(f"监控币种: {len(self.symbols)} 个")
        logger.info("=" * 80)

    def _load_symbols_from_config(self) -> List[str]:
        """从config.yaml加载交易对列表"""
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                symbols = config.get('symbols', [])
            logger.info(f"✅ 从配置文件加载 {len(symbols)} 个交易对")
            return symbols
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return []

    def scan_opportunities(self) -> List[Dict]:
        """扫描所有币种，寻找底部反转买入机会"""
        opportunities = []
        all_signals = []

        for symbol in self.symbols:
            signal = self.signal_generator.generate_signal(symbol)
            if signal['signal_strength'] > 0:
                all_signals.append(signal)
                if signal['signal_strength'] >= 60:  # 底部反转信号阈值
                    opportunities.append(signal)

        # 按信号强度排序
        opportunities.sort(key=lambda x: x['signal_strength'], reverse=True)

        if all_signals:
            logger.info(f"📊 扫描信号: {len(all_signals)} 个, 机会: {len(opportunities)} 个")
            # 显示前5个最强信号
            for sig in all_signals[:5]:
                reversal_tag = " [底部反转]" if sig.get('is_bottom_reversal') else ""
                logger.info(f"  {sig['symbol']:12} 强度:{sig['signal_strength']:5.1f}{reversal_tag} - {sig['details']}")

        return opportunities

    def execute_new_entries(self):
        """执行新开仓"""
        # 检查当前持仓数
        active_positions = self.position_manager.get_active_positions()
        if len(active_positions) >= self.position_manager.max_positions:
            logger.info(f"⏸️  已达最大持仓数 ({len(active_positions)}/{self.position_manager.max_positions})")
            return

        # 扫描机会
        opportunities = self.scan_opportunities()
        if not opportunities:
            logger.info("💤 暂无底部反转机会")
            return

        # 检查哪些币种还没有持仓
        active_symbols = {p['symbol'] for p in active_positions}
        available_slots = self.position_manager.max_positions - len(active_positions)

        logger.info(f"🎯 可开仓位: {available_slots} 个")

        # 尝试开仓
        opened = 0
        for opp in opportunities:
            if opened >= available_slots:
                break

            symbol = opp['symbol']
            if symbol in active_symbols:
                continue

            # 获取当前价格
            current_price = self.ws_price_service.get_price(symbol)
            if not current_price:
                logger.warning(f"⚠️  {symbol} 价格缺失")
                continue

            # 计算买入金额 (首批)
            is_bottom_reversal = opp.get('is_bottom_reversal', False)
            amount = self.position_manager.calculate_batch_amount(
                opp['signal_strength'],
                batch_index=0,
                is_bottom_reversal=is_bottom_reversal
            )

            # 执行买入
            success = self.position_manager.execute_spot_buy(
                symbol=symbol,
                price=current_price,
                amount=amount,
                signal_data=opp
            )

            if success:
                opened += 1
                active_symbols.add(symbol)

        if opened > 0:
            logger.success(f"✅ 本轮新开 {opened} 个仓位")

    def check_risk_management(self):
        """风险管理: 止盈止损 + 加仓检查"""
        positions = self.position_manager.get_active_positions()

        if not positions:
            logger.info("💼 当前无持仓")
            return

        logger.info(f"💼 持仓管理: {len(positions)} 个")

        for pos in positions:
            symbol = pos['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            if not current_price:
                logger.warning(f"⚠️  {symbol} 价格缺失")
                continue

            # 检查止盈止损
            self.position_manager.check_take_profit_stop_loss(pos, current_price)

            # 检查加仓
            self.position_manager.check_and_add_position(pos, current_price)

    async def run_forever(self):
        """主循环 - 底部反转抄底"""
        # 启动 WebSocket 价格服务
        asyncio.create_task(self.ws_price_service.start(self.symbols))

        # 等待价格数据准备
        await asyncio.sleep(5)

        logger.info("✅ WebSocket 价格服务已启动")
        logger.info("📈 专注底部反转信号 - 随时捕捉抄底机会")

        cycle = 0
        while True:
            try:
                cycle += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 现货抄底周期 #{cycle} - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                # 1. 风险管理 (止盈止损 + 加仓检查)
                self.check_risk_management()

                # 2. 扫描底部反转机会并开仓
                self.execute_new_entries()

                # 3. 等待下一个周期 (5分钟)
                logger.info("⏳ 等待下一周期...")
                await asyncio.sleep(300)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await asyncio.sleep(60)


def main():
    """主函数 - 使用增强版"""
    logger.info("=" * 80)
    logger.info("🌟 增强版现货交易服务启动 - 底部反转抄底专用")
    logger.info("=" * 80)

    service = SpotTraderServiceEnhanced()
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
