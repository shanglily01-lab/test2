#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易服务 - 超级大脑独立策略
完全独立于合约交易，使用ABCD多策略信号
"""

import time
import sys
import os
import asyncio
from datetime import datetime
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


class SpotSignalGenerator:
    """现货信号生成器 - ABCD多策略组合"""

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

    def strategy_a_trend_breakout(self, symbol: str) -> Tuple[float, str]:
        """
        策略A: 趋势突破
        - 价格突破上轨 + 成交量放大 = 买入信号
        - EMA9 > EMA21 = 上升趋势确认
        返回: (信号强度 0-100, 描述)
        """
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

        # 1. 价格突破上轨 (40分)
        if current_price > upper:
            distance = (current_price - upper) / upper * 100
            score += min(40, distance * 10)
            reasons.append(f"突破上轨{distance:.2f}%")

        # 2. EMA趋势向上 (30分)
        if ema9 > ema21:
            trend_strength = (ema9 - ema21) / ema21 * 100
            score += min(30, trend_strength * 5)
            reasons.append(f"上升趋势{trend_strength:.2f}%")

        # 3. 成交量放大 (30分)
        if current_volume > avg_volume * 1.5:
            volume_ratio = current_volume / avg_volume
            score += min(30, (volume_ratio - 1) * 15)
            reasons.append(f"量能{volume_ratio:.1f}x")

        desc = f"趋势突破: {', '.join(reasons)}" if reasons else "无信号"
        return score, desc

    def strategy_b_oversold_bounce(self, symbol: str) -> Tuple[float, str]:
        """
        策略B: 超卖反弹
        - RSI < 30 = 超卖
        - 价格接近下轨 = 支撑位
        - K线出现反转形态 = 买入信号
        返回: (信号强度 0-100, 描述)
        """
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

        # 1. RSI超卖 (50分)
        if rsi < 30:
            oversold_strength = (30 - rsi) / 30 * 100
            score += min(50, oversold_strength * 0.5)
            reasons.append(f"RSI超卖{rsi:.1f}")
        elif rsi < 40:
            score += 20
            reasons.append(f"RSI偏低{rsi:.1f}")

        # 2. 价格接近下轨 (30分)
        distance_to_lower = (current_price - lower) / lower * 100
        if distance_to_lower < 2:  # 距离下轨2%以内
            score += min(30, (2 - distance_to_lower) * 15)
            reasons.append(f"触及下轨")

        # 3. 出现反转K线 (20分)
        if current_price > prev_price:
            bounce_strength = (current_price - prev_price) / prev_price * 100
            score += min(20, bounce_strength * 10)
            reasons.append(f"反转上涨{bounce_strength:.2f}%")

        desc = f"超卖反弹: {', '.join(reasons)}" if reasons else "无信号"
        return score, desc

    def strategy_c_trend_following(self, symbol: str) -> Tuple[float, str]:
        """
        策略C: 趋势跟随
        - EMA9 > EMA21 > EMA50 = 强势上升趋势
        - 价格回踩EMA但未跌破 = 回调买入机会
        返回: (信号强度 0-100, 描述)
        """
        klines = self._get_kline_data(symbol, timeframe='15m', limit=100)
        if len(klines) < 60:
            return 0.0, "数据不足"

        closes = [float(k['close_price']) for k in klines]
        current_price = closes[-1]

        ema9 = self._calculate_ema(closes, 9)
        ema21 = self._calculate_ema(closes, 21)
        ema50 = self._calculate_ema(closes, 50)

        score = 0.0
        reasons = []

        # 1. 多头排列 (60分)
        if ema9 > ema21 > ema50:
            score += 60
            reasons.append("多头排列")

            # 额外加分：趋势强度
            trend_strength = (ema9 - ema50) / ema50 * 100
            if trend_strength > 2:
                score += min(20, (trend_strength - 2) * 5)
                reasons.append(f"强势{trend_strength:.1f}%")

        # 2. 价格回踩均线但未跌破 (20分)
        if ema21 <= current_price <= ema9:
            score += 20
            reasons.append("回踩支撑")

        desc = f"趋势跟随: {', '.join(reasons)}" if reasons else "无信号"
        return score, desc

    def strategy_d_multi_confirmation(self, symbol: str) -> Tuple[float, str]:
        """
        策略D: 多重确认
        综合A、B、C三个策略，多个信号共振时给出强信号
        """
        score_a, desc_a = self.strategy_a_trend_breakout(symbol)
        score_b, desc_b = self.strategy_b_oversold_bounce(symbol)
        score_c, desc_c = self.strategy_c_trend_following(symbol)

        # 计算共振得分
        active_strategies = sum([1 for s in [score_a, score_b, score_c] if s > 30])

        if active_strategies >= 2:
            # 多策略共振，取平均并加权
            avg_score = (score_a + score_b + score_c) / 3
            bonus = active_strategies * 15
            total_score = min(100, avg_score + bonus)

            active_descs = []
            if score_a > 30:
                active_descs.append("突破")
            if score_b > 30:
                active_descs.append("超卖")
            if score_c > 30:
                active_descs.append("趋势")

            desc = f"多重共振({active_strategies}个): {'+'.join(active_descs)}"
            return total_score, desc

        return 0.0, "共振不足"

    def generate_signal(self, symbol: str) -> Dict:
        """
        生成买入信号 (ABCD综合评分)
        返回信号强度和详细描述
        """
        try:
            score_a, desc_a = self.strategy_a_trend_breakout(symbol)
            score_b, desc_b = self.strategy_b_oversold_bounce(symbol)
            score_c, desc_c = self.strategy_c_trend_following(symbol)
            score_d, desc_d = self.strategy_d_multi_confirmation(symbol)

            # 取最高分作为最终信号
            scores = {
                'A_突破': score_a,
                'B_反弹': score_b,
                'C_趋势': score_c,
                'D_共振': score_d
            }

            best_strategy = max(scores, key=scores.get)
            best_score = scores[best_strategy]

            # 组合描述
            details = []
            if score_a > 20:
                details.append(f"A:{score_a:.0f}")
            if score_b > 20:
                details.append(f"B:{score_b:.0f}")
            if score_c > 20:
                details.append(f"C:{score_c:.0f}")
            if score_d > 20:
                details.append(f"D:{score_d:.0f}")

            return {
                'symbol': symbol,
                'signal_strength': best_score,
                'best_strategy': best_strategy,
                'all_scores': scores,
                'details': ' | '.join(details) if details else '无信号',
                'timestamp': datetime.now()
            }
        except Exception as e:
            logger.error(f"生成信号失败 {symbol}: {e}")
            return {
                'symbol': symbol,
                'signal_strength': 0.0,
                'best_strategy': 'ERROR',
                'all_scores': {},
                'details': str(e),
                'timestamp': datetime.now()
            }


class SpotPositionManager:
    """现货仓位管理器 - 5批次建仓"""

    # 5批次建仓比例
    BATCH_RATIOS = [0.10, 0.10, 0.20, 0.20, 0.40]

    def __init__(self, db_config: dict, total_capital: float = 50000, per_coin_capital: float = 10000):
        self.db_config = db_config
        self.connection = None

        self.total_capital = total_capital
        self.per_coin_capital = per_coin_capital
        self.reserve_ratio = 0.20  # 20% 现金储备
        self.max_positions = 5

        # 风险管理
        self.take_profit_pct = 0.50  # 50% 止盈
        self.stop_loss_pct = 0.10    # 10% 止损

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

    def get_position_by_symbol(self, symbol: str) -> Optional[Dict]:
        """获取指定币种的持仓"""
        positions = self.get_current_positions()
        for pos in positions:
            if pos['symbol'] == symbol:
                return pos
        return None

    def can_open_new_position(self) -> bool:
        """是否可以开新仓位"""
        current_positions = self.get_current_positions()
        return len(current_positions) < self.max_positions

    def calculate_batch_amount(self, signal_strength: float, batch_index: int) -> float:
        """
        根据信号强度和批次计算买入金额

        signal_strength: 0-100 信号强度
        batch_index: 0-4 批次索引
        """
        base_amount = self.per_coin_capital * self.BATCH_RATIOS[batch_index]

        # 根据信号强度调整 (信号越强，首批越大)
        if batch_index == 0 and signal_strength > 80:
            # 强信号时，首批加仓10%
            base_amount *= 1.1

        return base_amount

    def should_add_batch(self, position: Dict, current_price: float) -> Tuple[bool, int]:
        """
        判断是否应该加仓，以及加第几批

        加仓条件：
        1. 价格相比上一批下跌 >= 2%，或
        2. 价格相比上一批上涨 >= 3%，且信号依然强劲

        返回: (是否加仓, 下一批次索引)
        """
        current_batch = position.get('current_batch', 0)

        if current_batch >= 5:
            return False, -1  # 已完成5批

        last_buy_price = float(position['avg_entry_price'])
        price_change_pct = (current_price - last_buy_price) / last_buy_price

        # 规则1: 价格回调 >= 2%，加仓 (逢低加仓)
        if price_change_pct <= -0.02:
            return True, current_batch

        # 规则2: 价格上涨 >= 3%，且信号依然强劲 (追涨加仓)
        if price_change_pct >= 0.03:
            # 这里需要重新检查信号强度
            return True, current_batch

        return False, -1

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
                1,  # 第1批
                take_profit_price,
                stop_loss_price,
                signal_data['signal_strength'],
                signal_data['details']
            ))

            conn.commit()
            cursor.close()

            logger.info(f"✅ 创建持仓: {symbol} @ {entry_price:.4f}, 数量: {quantity:.2f}, 批次: 1/5")
            return True
        except Exception as e:
            logger.error(f"创建持仓失败: {e}")
            return False

    def add_batch_to_position(self, position: Dict, add_price: float, add_quantity: float, batch_index: int) -> bool:
        """向现有持仓加仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 计算新的平均成本
            old_cost = float(position['total_cost'])
            old_quantity = float(position['quantity'])

            new_total_quantity = old_quantity + add_quantity
            new_total_cost = old_cost + (add_price * add_quantity)
            new_avg_price = new_total_cost / new_total_quantity

            # 更新持仓
            cursor.execute("""
                UPDATE spot_positions
                SET quantity = %s,
                    total_cost = %s,
                    avg_entry_price = %s,
                    current_batch = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                new_total_quantity,
                new_total_cost,
                new_avg_price,
                batch_index + 1,
                position['id']
            ))

            conn.commit()
            cursor.close()

            logger.info(f"✅ 加仓: {position['symbol']} @ {add_price:.4f}, 数量: {add_quantity:.2f}, 批次: {batch_index + 1}/5")
            logger.info(f"   新平均成本: {new_avg_price:.4f}, 总数量: {new_total_quantity:.2f}")
            return True
        except Exception as e:
            logger.error(f"加仓失败: {e}")
            return False

    def close_position(self, position: Dict, exit_price: float, reason: str) -> bool:
        """平仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            quantity = float(position['quantity'])
            avg_cost = float(position['avg_entry_price'])

            # 计算盈亏
            exit_value = exit_price * quantity
            cost = avg_cost * quantity
            pnl = exit_value - cost
            pnl_pct = (exit_price - avg_cost) / avg_cost

            cursor.execute("""
                UPDATE spot_positions
                SET status = 'closed',
                    exit_price = %s,
                    pnl = %s,
                    pnl_pct = %s,
                    close_reason = %s,
                    closed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
            """, (
                exit_price,
                pnl,
                pnl_pct,
                reason,
                position['id']
            ))

            conn.commit()
            cursor.close()

            logger.info(f"✅ 平仓: {position['symbol']} @ {exit_price:.4f}, 盈亏: {pnl:.2f} USDT ({pnl_pct*100:.2f}%), 原因: {reason}")
            return True
        except Exception as e:
            logger.error(f"平仓失败: {e}")
            return False


class SpotTraderService:
    """现货交易服务主类"""

    def __init__(self):
        # 数据库配置
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'binance-data')
        }

        # 初始化组件
        self.signal_generator = SpotSignalGenerator(self.db_config)
        self.position_manager = SpotPositionManager(self.db_config)

        # WebSocket 现货价格服务
        self.ws_price_service = get_ws_price_service(market_type='spot')

        # 加载交易对列表
        self.symbols = self._load_symbols_from_config()

        logger.info("=" * 80)
        logger.info("🚀 现货交易服务启动")
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
        """扫描所有币种，寻找买入机会"""
        opportunities = []

        for symbol in self.symbols:
            signal = self.signal_generator.generate_signal(symbol)

            # 信号强度 >= 60 才考虑
            if signal['signal_strength'] >= 60:
                opportunities.append(signal)

        # 按信号强度排序
        opportunities.sort(key=lambda x: x['signal_strength'], reverse=True)

        return opportunities

    def check_risk_management(self):
        """检查所有持仓的止盈止损"""
        positions = self.position_manager.get_current_positions()

        for pos in positions:
            symbol = pos['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            if current_price is None:
                continue

            avg_cost = float(pos['avg_entry_price'])
            take_profit = float(pos['take_profit_price'])
            stop_loss = float(pos['stop_loss_price'])

            # 止盈
            if current_price >= take_profit:
                logger.info(f"🎯 触发止盈: {symbol} @ {current_price:.4f} (目标: {take_profit:.4f})")
                self.position_manager.close_position(pos, current_price, '止盈')

            # 止损
            elif current_price <= stop_loss:
                logger.warning(f"🛑 触发止损: {symbol} @ {current_price:.4f} (止损: {stop_loss:.4f})")
                self.position_manager.close_position(pos, current_price, '止损')

            # 检查加仓机会
            else:
                should_add, next_batch = self.position_manager.should_add_batch(pos, current_price)
                if should_add and next_batch >= 0:
                    # 重新生成信号确认
                    signal = self.signal_generator.generate_signal(symbol)
                    if signal['signal_strength'] >= 50:
                        amount = self.position_manager.calculate_batch_amount(signal['signal_strength'], next_batch)
                        quantity = amount / current_price

                        logger.info(f"📈 加仓信号: {symbol} @ {current_price:.4f}, 批次: {next_batch + 1}/5")
                        self.position_manager.add_batch_to_position(pos, current_price, quantity, next_batch)

    def execute_new_entries(self):
        """执行新开仓"""
        if not self.position_manager.can_open_new_position():
            logger.info("已达到最大持仓数，跳过新开仓")
            return

        # 扫描机会
        opportunities = self.scan_opportunities()

        if not opportunities:
            logger.info("未发现新机会")
            return

        # 检查是否已持仓
        current_symbols = {pos['symbol'] for pos in self.position_manager.get_current_positions()}

        for opp in opportunities:
            if opp['symbol'] in current_symbols:
                continue  # 已持仓，跳过

            # 执行开仓
            symbol = opp['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            if current_price is None:
                continue

            # 第1批买入
            amount = self.position_manager.calculate_batch_amount(opp['signal_strength'], 0)
            quantity = amount / current_price

            logger.info(f"🎯 新开仓机会: {symbol} @ {current_price:.4f}")
            logger.info(f"   信号强度: {opp['signal_strength']:.1f} | {opp['best_strategy']}")
            logger.info(f"   详情: {opp['details']}")

            self.position_manager.create_position(symbol, current_price, quantity, opp)

            # 每次只开1个新仓
            break

    async def run_forever(self):
        """主循环"""
        # 启动 WebSocket 价格服务
        asyncio.create_task(self.ws_price_service.start(self.symbols))

        # 等待价格数据准备
        await asyncio.sleep(5)

        logger.info("✅ WebSocket 价格服务已启动")

        cycle = 0
        while True:
            try:
                cycle += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 现货交易周期 #{cycle} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                # 1. 风险管理 (止盈止损 + 加仓检查)
                self.check_risk_management()

                # 2. 新开仓
                self.execute_new_entries()

                # 3. 等待下一个周期 (5分钟)
                await asyncio.sleep(300)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                await asyncio.sleep(60)


def main():
    """主函数"""
    service = SpotTraderService()
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
