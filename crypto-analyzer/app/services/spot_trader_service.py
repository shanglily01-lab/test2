#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易服务 - Big4底部抄底顶部卖出策略

核心策略:
1. 监听Big4的底部/顶部检测信号
2. 底部时: 扫描所有币种，按跌幅排序，买入跌幅最大的币种（每笔800U）
3. 顶部时: 卖出所有持仓
4. 一次性买入，不分批
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

# 导入Big4趋势检测器和WebSocket价格服务
# 添加项目根目录到sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.services.big4_trend_detector import Big4TrendDetector
from app.services.binance_ws_price import get_ws_price_service

# 加载环境变量
load_dotenv()


class SpotBottomTopTrader:
    """
    现货双策略交易器

    策略1: 深V反转抄底（原有策略）
    策略2: Big4趋势跟随分批买入（新增）
    """

    def __init__(self):
        # 数据库配置
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'binance-data')
        }

        # Big4趋势检测器
        self.big4_detector = Big4TrendDetector()

        # WebSocket 现货价格服务
        self.ws_price_service = get_ws_price_service(market_type='spot')

        # 加载交易对列表
        self.symbols = self._load_symbols_from_config()

        # ========== 策略1: 深V反转配置 ==========
        self.AMOUNT_PER_TRADE = 800  # 每笔800 USDT
        self.MAX_POSITIONS = 30      # 最多30个持仓
        self.MIN_DROP_PCT = 3.0      # 最小跌幅3%才考虑买入

        # ========== 策略2: 趋势跟随配置 ==========
        self.TREND_TOTAL_AMOUNT = 3000      # 每个币种总仓位3000 USDT
        self.TREND_BATCH_COUNT = 3          # 分3批买入
        self.TREND_BATCH_INTERVAL = 3600    # 每批间隔1小时（秒）
        self.TREND_DIP_PCT = 0.005          # 逢低买入阈值0.5%
        self.TREND_TAKE_PROFIT = 0.25       # 止盈25%
        self.TREND_STOP_LOSS = 0.10         # 止损10%
        self.TREND_MAX_SYMBOLS = 5          # 最多同时跟踪5个币种

        # 止盈止损（通用）
        self.TAKE_PROFIT_PCT = 0.50  # 深V策略50%止盈
        self.STOP_LOSS_PCT = 0.10    # 防极端情况10%止损

        # ========== 状态追踪 ==========
        # 深V策略状态
        self.last_bottom_detected_at = None
        self.last_top_detected_at = None
        self.in_bottom_window = False

        # 趋势跟随状态 {symbol: {'batch': 1, 'prices': [price1], 'times': [time1], 'amounts': [amt1]}}
        self.trend_positions = {}
        self.last_big4_signal = 'NEUTRAL'

        logger.info("=" * 80)
        logger.info("🚀 现货双策略交易服务启动")
        logger.info("📊 策略1 - 深V反转: 每笔{} USDT, 最多{}仓".format(self.AMOUNT_PER_TRADE, self.MAX_POSITIONS))
        logger.info("📈 策略2 - 趋势跟随: 每币{}U分{}批, 3小时内逢低买入".format(
            self.TREND_TOTAL_AMOUNT, self.TREND_BATCH_COUNT))
        logger.info(f"止盈: 趋势{self.TREND_TAKE_PROFIT*100:.0f}% / 深V{self.TAKE_PROFIT_PCT*100:.0f}%")
        logger.info(f"止损: {self.STOP_LOSS_PCT*100:.0f}%")
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

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config['port'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def get_current_positions(self) -> List[Dict]:
        """获取当前持仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    id, symbol, position_side, quantity, available_quantity,
                    avg_entry_price, avg_entry_price AS entry_price,
                    total_cost, current_price, market_value,
                    unrealized_pnl, unrealized_pnl_pct,
                    stop_loss_price, take_profit_price,
                    first_buy_time, last_update_time,
                    status, created_at, updated_at
                FROM paper_trading_positions
                WHERE status = 'open' AND account_id = 1
                ORDER BY created_at DESC
            """)

            positions = cursor.fetchall()
            cursor.close()
            conn.close()

            return positions if positions else []
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def scan_drop_opportunities(self) -> List[Dict]:
        """
        扫描所有币种，找出跌幅最大的币种

        返回: [(symbol, drop_pct, current_price), ...]
        """
        opportunities = []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询所有币种的24H数据
            for symbol in self.symbols:
                binance_symbol = symbol.replace('/', '')

                cursor.execute("""
                    SELECT change_24h, quote_volume_24h
                    FROM price_stats_24h
                    WHERE symbol = %s
                """, (binance_symbol,))

                result = cursor.fetchone()
                if not result:
                    continue

                change_24h = float(result['change_24h'] or 0)
                volume_24h = float(result['quote_volume_24h'] or 0)

                # 只考虑下跌的币种
                if change_24h < -self.MIN_DROP_PCT:
                    # 流动性过滤（成交额至少100万）
                    if volume_24h >= 1_000_000:
                        current_price = self.ws_price_service.get_price(symbol)
                        if current_price:
                            opportunities.append({
                                'symbol': symbol,
                                'drop_pct': abs(change_24h),
                                'change_24h': change_24h,
                                'current_price': current_price,
                                'volume_24h': volume_24h
                            })

            cursor.close()
            conn.close()

            # 按跌幅降序排序
            opportunities.sort(key=lambda x: x['drop_pct'], reverse=True)

            return opportunities

        except Exception as e:
            logger.error(f"扫描跌幅机会失败: {e}")
            return []

    def execute_bottom_buy(self):
        """
        执行底部抄底买入

        逻辑:
        1. 扫描所有币种，按跌幅排序
        2. 选择跌幅最大的前N个币种
        3. 每个币种买入800 USDT
        4. 一次性买入，不分批
        """
        # 检查当前持仓数
        current_positions = self.get_current_positions()
        current_symbols = {pos['symbol'] for pos in current_positions}
        available_slots = self.MAX_POSITIONS - len(current_positions)

        if available_slots <= 0:
            logger.info(f"⏸️  已达最大持仓数 ({len(current_positions)}/{self.MAX_POSITIONS})")
            return

        logger.info(f"📊 可用仓位: {available_slots} 个")

        # 扫描跌幅机会
        opportunities = self.scan_drop_opportunities()

        if not opportunities:
            logger.info("💤 未发现跌幅机会（跌幅<3%或流动性不足）")
            return

        # 显示前10个机会
        logger.info(f"📉 发现 {len(opportunities)} 个下跌币种，显示前10:")
        for i, opp in enumerate(opportunities[:10], 1):
            logger.info(f"  {i:2d}. {opp['symbol']:12} 跌幅:{opp['drop_pct']:5.2f}% 价格:{opp['current_price']:.6f} 量:{opp['volume_24h']/1e6:.1f}M")

        # 选择跌幅最大且未持仓的币种
        bought_count = 0
        for opp in opportunities:
            if bought_count >= available_slots:
                break

            symbol = opp['symbol']
            if symbol in current_symbols:
                logger.info(f"  ⏭️  {symbol} 已持仓，跳过")
                continue

            # 执行买入
            success = self._execute_spot_buy(
                symbol=symbol,
                price=opp['current_price'],
                amount=self.AMOUNT_PER_TRADE,
                drop_pct=opp['drop_pct']
            )

            if success:
                bought_count += 1
                current_symbols.add(symbol)

        if bought_count > 0:
            logger.success(f"✅ 本轮抄底买入 {bought_count} 个币种")
        else:
            logger.info("💤 本轮未买入新币种")

    def _get_latest_price_from_db(self, symbol: str) -> Optional[float]:
        """
        从数据库获取最新价格（作为WebSocket价格的备用）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            binance_symbol = symbol.replace('/', '')

            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m' AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT 1
            """, (binance_symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return float(result['close_price'])
            return None

        except Exception as e:
            logger.error(f"从数据库获取价格失败 {symbol}: {e}")
            return None

    def _execute_spot_buy(self, symbol: str, price: float, amount: float, drop_pct: float) -> bool:
        """
        执行现货买入

        Args:
            symbol: 交易对
            price: 买入价格
            amount: 买入金额（USDT）
            drop_pct: 跌幅百分比
        """
        try:
            quantity = amount / price

            conn = self._get_connection()
            cursor = conn.cursor()

            # 计算止盈止损价格
            take_profit_price = price * (1 + self.TAKE_PROFIT_PCT)
            stop_loss_price = price * (1 - self.STOP_LOSS_PCT)

            cursor.execute("""
                INSERT INTO paper_trading_positions (
                    account_id, symbol, position_side, quantity, available_quantity,
                    avg_entry_price, total_cost,
                    take_profit_price, stop_loss_price,
                    status, created_at, updated_at
                ) VALUES (
                    1, %s, 'LONG', %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW()
                )
            """, (
                symbol,
                quantity,
                quantity,  # available_quantity = quantity
                price,  # avg_entry_price
                amount,  # total_cost
                take_profit_price,
                stop_loss_price
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 买入: {symbol} @ {price:.6f}, 金额: {amount:.0f} USDT, 数量: {quantity:.2f}, 跌幅: {drop_pct:.2f}%")
            return True

        except Exception as e:
            logger.error(f"买入失败 {symbol}: {e}")
            return False

    def execute_top_sell(self):
        """
        执行顶部卖出

        逻辑:
        卖出所有持仓（强制全部卖出，不允许跳过）
        """
        positions = self.get_current_positions()

        if not positions:
            logger.info("💼 当前无持仓，无需卖出")
            return

        logger.info(f"🔴 Big4触顶信号 - 强制卖出所有持仓 ({len(positions)}个)")

        sold_count = 0
        failed_symbols = []

        for pos in positions:
            symbol = pos['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            # 如果WebSocket价格缺失，尝试从数据库获取最新价格
            if not current_price:
                logger.warning(f"⚠️  {symbol} WebSocket价格缺失，尝试从数据库获取...")
                current_price = self._get_latest_price_from_db(symbol)

            # 如果仍然获取不到价格，使用入场价作为兜底
            if not current_price:
                logger.error(f"❌ {symbol} 无法获取价格，使用入场价强制卖出")
                current_price = float(pos['entry_price'])

            success = self._execute_spot_sell(pos, current_price, "Big4顶部信号")
            if success:
                sold_count += 1
            else:
                failed_symbols.append(symbol)

        if sold_count > 0:
            logger.success(f"✅ 顶部卖出 {sold_count}/{len(positions)} 个币种")
        if failed_symbols:
            logger.error(f"❌ 卖出失败的币种: {', '.join(failed_symbols)}")

    def _execute_spot_sell(self, position: Dict, exit_price: float, reason: str) -> bool:
        """
        执行现货卖出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            entry_price = float(position['entry_price'])
            quantity = float(position['quantity'])
            total_cost = float(position['total_cost'])

            # 计算盈亏
            exit_value = exit_price * quantity
            pnl = exit_value - total_cost
            pnl_pct = (exit_price - entry_price) / entry_price

            cursor.execute("""
                UPDATE paper_trading_positions
                SET status = 'closed',
                    current_price = %s,
                    unrealized_pnl = %s,
                    unrealized_pnl_pct = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                exit_price,
                pnl,
                pnl_pct,
                position['id']
            ))

            conn.commit()
            cursor.close()
            conn.close()

            profit_emoji = "📈" if pnl > 0 else "📉"
            logger.info(f"{profit_emoji} 卖出: {position['symbol']} @ {exit_price:.6f}, 盈亏: {pnl:.2f} USDT ({pnl_pct*100:.2f}%), 原因: {reason}")
            return True

        except Exception as e:
            logger.error(f"卖出失败 {position['symbol']}: {e}")
            return False

    # ========== 趋势跟随策略方法 ==========

    def execute_trend_follow_buy(self, big4_result: Dict):
        """
        执行趋势跟随分批买入

        策略: Big4 BULLISH时,选择最强势的币种,分3批3小时内逢低买入
        """
        signal = big4_result.get('overall_signal', 'NEUTRAL')
        strength = big4_result.get('signal_strength', 0)

        # 只在BULLISH且强度>=50时买入
        if signal != 'BULLISH' or strength < 50:
            return

        now = datetime.now()

        # 1. 检查现有趋势持仓,执行分批买入
        for symbol in list(self.trend_positions.keys()):
            position = self.trend_positions[symbol]
            batch_num = position['batch']
            last_time = position['times'][-1] if position['times'] else now
            last_price = position['prices'][-1] if position['prices'] else None

            # 如果还未完成3批
            if batch_num < self.TREND_BATCH_COUNT:
                time_diff = (now - last_time).total_seconds()
                next_batch_time = self.TREND_BATCH_INTERVAL

                current_price = self.ws_price_service.get_price(symbol)
                if not current_price:
                    continue

                should_buy = False
                reason = ""

                # 判断是否应该买入下一批
                if time_diff >= next_batch_time:
                    # 时间到了,检查是否逢低
                    if last_price and current_price < last_price * (1 - self.TREND_DIP_PCT):
                        should_buy = True
                        reason = f"逢低{(1 - current_price/last_price)*100:.2f}%"
                    elif time_diff >= next_batch_time * 1.2:
                        # 超时20%仍未跌,则按时间买入
                        should_buy = True
                        reason = "按时间买入"

                if should_buy:
                    batch_amount = self.TREND_TOTAL_AMOUNT / self.TREND_BATCH_COUNT
                    success = self._execute_spot_buy(symbol, batch_amount, f"趋势跟随第{batch_num+1}批({reason})")
                    if success:
                        position['batch'] += 1
                        position['prices'].append(current_price)
                        position['times'].append(now)
                        position['amounts'].append(batch_amount)
                        logger.success(f"📈 {symbol} 趋势跟随第{position['batch']}/3批买入完成 @ {current_price:.6f}")

        # 2. 如果Big4刚转为BULLISH,开始新的趋势跟随
        if self.last_big4_signal != 'BULLISH' and signal == 'BULLISH':
            # 选择最强势的币种(限制最多5个)
            if len(self.trend_positions) < self.TREND_MAX_SYMBOLS:
                candidates = self._select_trend_symbols(big4_result)
                for symbol in candidates[:self.TREND_MAX_SYMBOLS - len(self.trend_positions)]:
                    current_price = self.ws_price_service.get_price(symbol)
                    if not current_price:
                        continue

                    # 执行第1批买入
                    batch_amount = self.TREND_TOTAL_AMOUNT / self.TREND_BATCH_COUNT
                    success = self._execute_spot_buy(symbol, batch_amount, "趋势跟随第1批(Big4转多)")
                    if success:
                        self.trend_positions[symbol] = {
                            'batch': 1,
                            'prices': [current_price],
                            'times': [now],
                            'amounts': [batch_amount],
                            'entry_time': now
                        }
                        logger.success(f"🚀 {symbol} 开始趋势跟随 1/3批 @ {current_price:.6f}")

        self.last_big4_signal = signal

    def execute_trend_follow_sell(self, big4_result: Dict):
        """
        执行趋势跟随卖出

        条件:
        - Big4转BEARISH: 全部卖出
        - Big4转NEUTRAL: 卖出50%
        - 止盈: +25%
        - 止损: -10%
        """
        signal = big4_result.get('overall_signal', 'NEUTRAL')

        for symbol in list(self.trend_positions.keys()):
            position = self.trend_positions[symbol]

            # 获取平均成本
            avg_price = sum(position['prices']) / len(position['prices']) if position['prices'] else 0
            current_price = self.ws_price_service.get_price(symbol)

            if not current_price or not avg_price:
                continue

            pnl_pct = (current_price - avg_price) / avg_price

            sell_pct = 0
            reason = ""

            # 判断卖出条件
            if signal == 'BEARISH':
                sell_pct = 1.0
                reason = "Big4转空"
            elif signal == 'NEUTRAL' and self.last_big4_signal == 'BULLISH':
                sell_pct = 0.5
                reason = "Big4转中性"
            elif pnl_pct >= self.TREND_TAKE_PROFIT:
                sell_pct = 1.0
                reason = f"止盈{pnl_pct*100:.1f}%"
            elif pnl_pct <= -self.TREND_STOP_LOSS:
                sell_pct = 1.0
                reason = f"止损{pnl_pct*100:.1f}%"

            if sell_pct > 0:
                # 查询实际持仓
                spot_position = self._get_spot_position(symbol)
                if spot_position and float(spot_position['available_quantity']) > 0:
                    sell_qty = float(spot_position['available_quantity']) * sell_pct
                    success = self._execute_spot_sell(symbol, sell_qty, reason)
                    if success:
                        if sell_pct >= 1.0:
                            # 全部卖出,移除跟踪
                            del self.trend_positions[symbol]
                            logger.success(f"✅ {symbol} 趋势跟随已清仓,{reason}")
                        else:
                            logger.info(f"📉 {symbol} 减仓{sell_pct*100:.0f}%,{reason}")

    def _select_trend_symbols(self, big4_result: Dict) -> List[str]:
        """
        选择最适合趋势跟随的币种 (避免追高策略)

        筛选条件（A+C组合）:
        1. Big4 BULLISH (强度>=50) - 已由调用方检查
        2. 个币信号 BULLISH (评分>=50)
        3. 价格回调: 当前价格 < 1H最高价 * 0.98 (回调至少2%)
        4. 5M反向信号: 5M有阴线回调 (精准入场时机)
        """
        candidates = []

        try:
            # 初始化Big4检测器用于分析个币信号
            detector = Big4TrendDetector()
            conn = self._get_connection()

            for symbol in self.symbols:
                if symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
                    continue  # 跳过Big4本身

                # 1. 检查个币信号 (BULLISH且评分>=50)
                coin_signal = detector._analyze_symbol(conn, symbol)
                if coin_signal['signal'] != 'BULLISH' or coin_signal['strength'] < 50:
                    continue

                # 2. 获取当前价格
                current_price = self.ws_price_service.get_price(symbol)
                if not current_price:
                    continue

                # 3. 检查价格回调 (当前价 < 1H最高价 * 0.98)
                binance_symbol = symbol.replace('/', '')
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT MAX(high_price) as max_high
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = '1h' AND exchange = 'binance'
                        AND open_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
                """, (binance_symbol,))
                result = cursor.fetchone()
                cursor.close()

                if not result or not result['max_high']:
                    continue

                max_high_1h = float(result['max_high'])
                pullback_threshold = max_high_1h * 0.98

                if current_price >= pullback_threshold:
                    # 价格没有回调2%，跳过（避免追高）
                    continue

                # 4. 检查5M反向信号 (必须有阴线回调)
                if '5m_signal' in coin_signal:
                    m5 = coin_signal['5m_signal']
                    # 多头趋势，需要5M有阴线
                    if m5['bearish_count'] < 1:
                        continue  # 没有阴线回调，跳过

                # 通过所有过滤条件
                pullback_pct = (1 - current_price / max_high_1h) * 100
                candidates.append({
                    'symbol': symbol,
                    'signal_strength': coin_signal['strength'],
                    'pullback_pct': pullback_pct,
                    'price': current_price
                })

            conn.close()

            # 按信号强度排序，选择最强的
            candidates.sort(key=lambda x: x['signal_strength'], reverse=True)

            if candidates:
                logger.info(f"📊 筛选出 {len(candidates)} 个符合条件的币种（避免追高+5M确认）:")
                for i, c in enumerate(candidates[:10], 1):
                    logger.info(f"  {i}. {c['symbol']:12} 强度:{c['signal_strength']:3.0f} 回调:{c['pullback_pct']:4.1f}% 价格:{c['price']:.6f}")

            return [c['symbol'] for c in candidates[:10]]

        except Exception as e:
            logger.error(f"选择趋势币种失败: {e}")
            return []

    def _get_spot_position(self, symbol: str) -> Optional[Dict]:
        """获取现货持仓"""
        positions = self.get_current_positions()
        for pos in positions:
            if pos['symbol'] == symbol:
                return pos
        return None

    def _execute_spot_buy(self, symbol: str, amount_usdt: float, reason: str) -> bool:
        """执行现货买入(模拟)"""
        try:
            current_price = self.ws_price_service.get_price(symbol)
            if not current_price:
                logger.warning(f"无法获取{symbol}价格")
                return False

            quantity = amount_usdt / current_price

            # TODO: 这里应该调用实际的交易API
            # 目前只是模拟记录
            logger.info(f"🔵 模拟买入: {symbol} {quantity:.6f} @ {current_price:.6f} USDT ({amount_usdt:.2f}U) - {reason}")

            return True

        except Exception as e:
            logger.error(f"买入失败 {symbol}: {e}")
            return False

    def _execute_spot_sell(self, symbol: str, quantity: float, reason: str) -> bool:
        """执行现货卖出(模拟)"""
        try:
            current_price = self.ws_price_service.get_price(symbol)
            if not current_price:
                logger.warning(f"无法获取{symbol}价格")
                return False

            amount_usdt = quantity * current_price

            # TODO: 这里应该调用实际的交易API
            # 目前只是模拟记录
            logger.info(f"🔴 模拟卖出: {symbol} {quantity:.6f} @ {current_price:.6f} USDT ({amount_usdt:.2f}U) - {reason}")

            return True

        except Exception as e:
            logger.error(f"卖出失败 {symbol}: {e}")
            return False

    # ========== 原有方法 ==========

    def check_stop_profit_loss(self):
        """
        检查止盈止损（备用逻辑）

        主要在Big4信号之外提供保护
        """
        positions = self.get_current_positions()

        if not positions:
            return

        for pos in positions:
            symbol = pos['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            if not current_price:
                continue

            entry_price = float(pos['entry_price'])
            take_profit = float(pos['take_profit_price'])
            stop_loss = float(pos['stop_loss_price'])

            # 止盈
            if current_price >= take_profit:
                profit_pct = (current_price - entry_price) / entry_price * 100
                logger.info(f"🎯 触发止盈: {symbol} @ {current_price:.6f} (目标: {take_profit:.6f}, +{profit_pct:.1f}%)")
                self._execute_spot_sell(pos, current_price, f'止盈{profit_pct:.1f}%')

            # 止损
            elif current_price <= stop_loss:
                loss_pct = (current_price - entry_price) / entry_price * 100
                logger.warning(f"🛑 触发止损: {symbol} @ {current_price:.6f} (止损: {stop_loss:.6f}, {loss_pct:.1f}%)")
                self._execute_spot_sell(pos, current_price, f'止损{loss_pct:.1f}%')

    async def run_forever(self):
        """主循环"""
        # 启动 WebSocket 价格服务
        asyncio.create_task(self.ws_price_service.start(self.symbols))

        # 等待价格数据准备
        await asyncio.sleep(5)

        logger.info("✅ WebSocket 价格服务已启动")
        logger.info("📈 监听Big4底部/顶部信号中...")

        cycle = 0
        while True:
            try:
                cycle += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 现货交易周期 #{cycle} - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                # 1. 检测Big4信号
                big4_result = self.big4_detector.detect_market_trend()
                emergency = big4_result.get('emergency_intervention', {})

                bottom_detected = emergency.get('bottom_detected', False)
                top_detected = emergency.get('top_detected', False)

                logger.info(f"Big4状态: {big4_result['overall_signal']} | 强度: {big4_result['signal_strength']:.1f}")
                logger.info(f"紧急干预: 底部={bottom_detected}, 顶部={top_detected}")

                # 2. 底部检测 - 执行抄底
                if bottom_detected:
                    # 检查是否是新的底部信号
                    if self.last_bottom_detected_at is None or \
                       (datetime.now() - self.last_bottom_detected_at).total_seconds() > 3600:  # 1小时内不重复触发
                        logger.success("🟢 检测到Big4底部信号 - 开始抄底")
                        self.execute_bottom_buy()
                        self.last_bottom_detected_at = datetime.now()
                    else:
                        logger.info("⏸️  底部信号已在1小时内触发过，跳过")

                # 3. 顶部检测 - 执行卖出
                if top_detected:
                    # 检查是否是新的顶部信号
                    if self.last_top_detected_at is None or \
                       (datetime.now() - self.last_top_detected_at).total_seconds() > 3600:  # 1小时内不重复触发
                        logger.success("🔴 检测到Big4顶部信号 - 卖出所有持仓")
                        self.execute_top_sell()
                        self.last_top_detected_at = datetime.now()
                    else:
                        logger.info("⏸️  顶部信号已在1小时内触发过，跳过")

                # 4. 趋势跟随策略（新增）
                logger.info("📊 检查趋势跟随...")
                self.execute_trend_follow_buy(big4_result)
                self.execute_trend_follow_sell(big4_result)

                # 显示趋势跟随状态
                if self.trend_positions:
                    logger.info(f"📈 趋势跟随持仓 ({len(self.trend_positions)}个):")
                    for symbol, pos in self.trend_positions.items():
                        batch = pos['batch']
                        avg_price = sum(pos['prices']) / len(pos['prices'])
                        current_price = self.ws_price_service.get_price(symbol)
                        if current_price:
                            pnl_pct = (current_price - avg_price) / avg_price * 100
                            logger.info(f"  {symbol:12} 批次:{batch}/3 均价:{avg_price:.6f} 现价:{current_price:.6f} {pnl_pct:+.2f}%")

                # 5. 备用止盈止损检查
                self.check_stop_profit_loss()

                # 6. 显示当前持仓（所有策略）
                positions = self.get_current_positions()
                if positions:
                    logger.info(f"\n💼 当前持仓 ({len(positions)}个):")
                    for i, pos in enumerate(positions, 1):
                        symbol = pos['symbol']
                        entry_price = float(pos['entry_price'])
                        current_price = self.ws_price_service.get_price(symbol)

                        if current_price:
                            pnl_pct = (current_price - entry_price) / entry_price * 100
                            pnl_emoji = "📈" if pnl_pct > 0 else "📉"
                            logger.info(f"  {i:2d}. {symbol:12} 入:{entry_price:.6f} 现:{current_price:.6f} {pnl_emoji}{pnl_pct:+6.2f}%")
                else:
                    logger.info("\n💼 当前无持仓")

                # 7. 等待下一个周期 (5分钟)
                logger.info("⏳ 等待5分钟...")
                await asyncio.sleep(300)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await asyncio.sleep(60)


def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("🌟 现货双策略交易服务")
    logger.info("策略1: 深V反转抄底")
    logger.info("策略2: Big4趋势跟随(分3批逢低买入)")
    logger.info("=" * 80)

    service = SpotBottomTopTrader()
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
