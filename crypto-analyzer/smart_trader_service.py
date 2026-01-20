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
from datetime import datetime
from decimal import Decimal
from loguru import logger
import pymysql
from dotenv import load_dotenv

# 导入 WebSocket 价格服务
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.binance_ws_price import get_ws_price_service, BinanceWSPriceService

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

        # 获取所有USDT交易对
        self.whitelist = self._get_all_symbols()
        self.threshold = 10  # 降低阈值,更容易找到交易机会

    def _get_all_symbols(self):
        """从config.yaml读取交易对列表"""
        try:
            import yaml
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                symbols = config.get('symbols', [])
                logger.info(f"从config.yaml加载了 {len(symbols)} 个交易对")
                return symbols
        except Exception as e:
            logger.error(f"读取config.yaml失败: {e}, 使用默认白名单")
            return [
                'BCH/USDT', 'LDO/USDT', 'ENA/USDT', 'WIF/USDT', 'TAO/USDT',
                'DASH/USDT', 'ETC/USDT', 'VIRTUAL/USDT', 'NEAR/USDT',
                'AAVE/USDT', 'SUI/USDT', 'UNI/USDT', 'ADA/USDT', 'SOL/USDT'
            ]

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
                   low_price as low, close_price as close
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
                long_score += 20
            elif position_pct > 70:
                short_score += 20
            else:
                long_score += 5
                short_score += 5

            # 2. 短期动量 - 最近24小时涨幅
            gain_24h = (current - klines_1h[-24]['close']) / klines_1h[-24]['close'] * 100
            if gain_24h < -3:  # 24小时跌超过3%
                long_score += 15
            elif gain_24h > 3:  # 24小时涨超过3%
                short_score += 15

            # 3. 1小时趋势评分 - 最近48根K线(2天)
            bullish_1h = sum(1 for k in klines_1h[-48:] if k['close'] > k['open'])
            bearish_1h = 48 - bullish_1h

            if bullish_1h > 30:  # 超过62.5%是阳线
                long_score += 20
            elif bearish_1h > 30:  # 超过62.5%是阴线
                short_score += 20

            # 4. 波动率评分 - 最近24小时
            recent_24h = klines_1h[-24:]
            volatility = (max(k['high'] for k in recent_24h) - min(k['low'] for k in recent_24h)) / current * 100

            # 高波动率更适合交易
            if volatility > 5:  # 波动超过5%
                if long_score > short_score:
                    long_score += 10
                else:
                    short_score += 10

            # 5. 连续趋势强化信号 - 最近10根1小时K线
            recent_10h = klines_1h[-10:]
            bullish_10h = sum(1 for k in recent_10h if k['close'] > k['open'])
            bearish_10h = 10 - bullish_10h

            # 计算最近10小时涨跌幅
            gain_10h = (current - recent_10h[0]['close']) / recent_10h[0]['close'] * 100

            # 连续阳线且上涨幅度适中(不在顶部) - 强做多信号
            if bullish_10h >= 7 and gain_10h < 5 and position_pct < 70:
                long_score += 15

            # 连续阴线且下跌幅度适中(不在底部) - 强做空信号
            elif bearish_10h >= 7 and gain_10h > -5 and position_pct > 30:
                short_score += 15

            # ========== 1天K线确认 (辅助) ==========

            # 大趋势确认: 如果30天趋势与1小时趋势一致，加分
            bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
            bearish_1d = 30 - bullish_1d

            if bullish_1d > 18 and long_score > short_score:  # 大趋势上涨且1小时也看多
                long_score += 10  # 趋势一致，加分
            elif bearish_1d > 18 and short_score > long_score:  # 大趋势下跌且1小时也看空
                short_score += 10

            # 选择得分更高的方向 (只要达到阈值就可以)
            if long_score >= self.threshold or short_score >= self.threshold:
                if long_score >= short_score:
                    return {
                        'symbol': symbol,
                        'side': 'LONG',
                        'score': long_score,
                        'current_price': current
                    }
                else:
                    return {
                        'symbol': symbol,
                        'side': 'SHORT',
                        'score': short_score,
                        'current_price': current
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
        self.position_size_usdt = 400
        self.max_positions = 999  # 不限制持仓数量
        self.leverage = 5
        self.scan_interval = 300

        self.brain = SmartDecisionBrain(self.db_config)
        self.connection = None
        self.running = True

        # WebSocket 价格服务
        self.ws_service: BinanceWSPriceService = get_ws_price_service()

        logger.info("=" * 60)
        logger.info("智能自动交易服务已启动")
        logger.info(f"账户ID: {self.account_id}")
        logger.info(f"仓位: ${self.position_size_usdt} | 杠杆: {self.leverage}x | 最大持仓: {self.max_positions}")
        logger.info(f"白名单: {len(self.brain.whitelist)}个币种 | 扫描间隔: {self.scan_interval}秒")
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
        """获取当前价格 - 带数据新鲜度检查"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT close_price, open_time
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()
            cursor.close()

            if not result:
                return None

            close_price, open_time = result

            # 检查数据新鲜度: K线时间不能超过5分钟前
            import time
            current_timestamp_ms = int(time.time() * 1000)
            data_age_minutes = (current_timestamp_ms - open_time) / 1000 / 60

            if data_age_minutes > 5:
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

            quantity = self.position_size_usdt * self.leverage / current_price
            notional_value = quantity * current_price
            margin = self.position_size_usdt

            # 基于实际开仓价格和方向计算止盈止损
            if side == 'LONG':
                stop_loss = current_price * 0.97   # 止损: 开仓价 -3%
                take_profit = current_price * 1.02  # 止盈: 开仓价 +2%
            else:  # SHORT
                stop_loss = current_price * 1.03   # 止损: 开仓价 +3%
                take_profit = current_price * 0.98  # 止盈: 开仓价 -2%

            logger.info(f"[OPEN] {symbol} {side} | 价格: ${current_price:.4f} ({price_source}) | 数量: {quantity:.2f}")

            conn = self._get_connection()
            cursor = conn.cursor()

            # 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 entry_signal_type, source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, 'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, side, quantity, current_price, self.leverage,
                notional_value, margin, stop_loss, take_profit,
                f"SMART_BRAIN_{opp['score']}"
            ))

            cursor.close()

            sl_pct = "-3%" if side == 'LONG' else "+3%"
            tp_pct = "+2%" if side == 'LONG' else "-2%"
            logger.info(
                f"[SUCCESS] {symbol} {side}开仓成功 | "
                f"止损: ${stop_loss:.4f} ({sl_pct}) | 止盈: ${take_profit:.4f} ({tp_pct})"
            )
            return True

        except Exception as e:
            logger.error(f"[ERROR] {symbol} 开仓失败: {e}")
            return False

    def check_top_bottom(self, symbol: str, position_side: str, entry_price: float):
        """智能识别顶部和底部 - 超级大脑动态监控"""
        try:
            # 使用15分钟K线分析
            klines_15m = self.brain.load_klines(symbol, '15m', 30)
            if len(klines_15m) < 30:
                return False, None

            current = klines_15m[-1]
            recent_10 = klines_15m[-10:]
            recent_5 = klines_15m[-5:]

            if position_side == 'LONG':
                # 做多持仓 - 寻找顶部信号

                # 1. 价格创新高后回落 (最高点在5-10根K线前)
                max_high = max(k['high'] for k in recent_10)
                max_high_idx = len(recent_10) - 1 - [k['high'] for k in reversed(recent_10)].index(max_high)
                is_peak = max_high_idx < 8  # 高点在前面,现在回落

                # 2. 当前价格已经从高点回落
                current_price = current['close']
                pullback_pct = (max_high - current_price) / max_high * 100

                # 3. 最近3根K线连续收阴或连续长上影线
                recent_3 = klines_15m[-3:]
                bearish_count = sum(1 for k in recent_3 if k['close'] < k['open'])
                long_upper_shadow = sum(1 for k in recent_3 if (k['high'] - max(k['open'], k['close'])) > (k['close'] - k['open']) * 2)

                # 见顶判断条件
                if is_peak and pullback_pct >= 1.0 and (bearish_count >= 2 or long_upper_shadow >= 2):
                    # 计算当前盈利
                    profit_pct = (current_price - entry_price) / entry_price * 100
                    return True, f"TOP_DETECTED(高点回落{pullback_pct:.1f}%,盈利{profit_pct:+.1f}%)"

            elif position_side == 'SHORT':
                # 做空持仓 - 寻找底部信号

                # 1. 价格创新低后反弹 (最低点在5-10根K线前)
                min_low = min(k['low'] for k in recent_10)
                min_low_idx = len(recent_10) - 1 - [k['low'] for k in reversed(recent_10)].index(min_low)
                is_bottom = min_low_idx < 8  # 低点在前面,现在反弹

                # 2. 当前价格已经从低点反弹
                current_price = current['close']
                bounce_pct = (current_price - min_low) / min_low * 100

                # 3. 最近3根K线连续收阳或连续长下影线
                recent_3 = klines_15m[-3:]
                bullish_count = sum(1 for k in recent_3 if k['close'] > k['open'])
                long_lower_shadow = sum(1 for k in recent_3 if (min(k['open'], k['close']) - k['low']) > (k['close'] - k['open']) * 2)

                # 见底判断条件
                if is_bottom and bounce_pct >= 1.0 and (bullish_count >= 2 or long_lower_shadow >= 2):
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
                SELECT id, symbol, position_side, entry_price,
                       stop_loss_price, take_profit_price
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (self.account_id,))

            positions = cursor.fetchall()

            for pos in positions:
                pos_id, symbol, position_side, entry_price, stop_loss, take_profit = pos
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                should_close = False
                close_reason = None

                # 1. 固定止损检查 (保底风控)
                if position_side == 'LONG':
                    if stop_loss and current_price <= float(stop_loss):
                        should_close = True
                        close_reason = 'STOP_LOSS'
                elif position_side == 'SHORT':
                    if stop_loss and current_price >= float(stop_loss):
                        should_close = True
                        close_reason = 'STOP_LOSS'

                # 2. 智能顶底识别 (优先于固定止盈)
                if not should_close:
                    is_top_bottom, tb_reason = self.check_top_bottom(symbol, position_side, float(entry_price))
                    if is_top_bottom:
                        should_close = True
                        close_reason = tb_reason

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

                    cursor.execute("""
                        UPDATE futures_positions
                        SET status = 'closed', mark_price = %s,
                            realized_pnl = %s,
                            close_time = NOW(), updated_at = NOW()
                        WHERE id = %s
                    """, (current_price, realized_pnl, pos_id))

            cursor.close()

        except Exception as e:
            logger.error(f"[ERROR] 检查止盈止损失败: {e}")

    def close_old_positions(self):
        """关闭超时持仓 (6小时后强制平仓)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
                AND created_at < DATE_SUB(NOW(), INTERVAL 6 HOUR)
            """, (self.account_id,))

            old_positions = cursor.fetchall()

            for pos in old_positions:
                pos_id, symbol, position_side, quantity, entry_price = pos
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                # Calculate realized PnL
                if position_side == 'LONG':
                    realized_pnl = (current_price - float(entry_price)) * float(quantity)
                else:  # SHORT
                    realized_pnl = (float(entry_price) - current_price) * float(quantity)

                logger.info(f"[CLOSE_TIMEOUT] {symbol} 超时平仓 | 价格: ${current_price:.4f} | 盈亏: {realized_pnl:+.2f} USDT")

                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'closed', mark_price = %s,
                        realized_pnl = %s,
                        close_time = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (current_price, realized_pnl, pos_id))

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
                            cursor.execute("""
                                UPDATE futures_positions
                                SET status = 'closed', mark_price = %s,
                                    realized_pnl = %s,
                                    close_time = NOW(), updated_at = NOW(),
                                    notes = CONCAT(IFNULL(notes, ''), '|hedge_loss_cut')
                                WHERE id = %s
                            """, (current_price, long_pos['realized_pnl'], long_pos['id']))

                        # SHORT亏损>1%, LONG盈利 -> 平掉SHORT
                        elif short_pos['pnl_pct'] < -1 and long_pos['pnl_pct'] > 0:
                            logger.info(
                                f"[HEDGE_CLOSE] {symbol} SHORT亏损{short_pos['pnl_pct']:.2f}% ({short_pos['realized_pnl']:+.2f} USDT), "
                                f"LONG盈利{long_pos['pnl_pct']:.2f}% -> 平掉SHORT"
                            )
                            cursor.execute("""
                                UPDATE futures_positions
                                SET status = 'closed', mark_price = %s,
                                    realized_pnl = %s,
                                    close_time = NOW(), updated_at = NOW(),
                                    notes = CONCAT(IFNULL(notes, ''), '|hedge_loss_cut')
                                WHERE id = %s
                            """, (current_price, short_pos['realized_pnl'], short_pos['id']))

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
                SELECT id, entry_price, quantity FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND status = 'open' AND account_id = %s
            """, (symbol, side, self.account_id))

            positions = cursor.fetchall()

            for pos in positions:
                entry_price = float(pos['entry_price'])
                quantity = float(pos['quantity'])
                pnl_pct = (current_price - entry_price) / entry_price * 100

                # Calculate realized PnL
                if side == 'LONG':
                    realized_pnl = (current_price - entry_price) * quantity
                    pnl_pct = (current_price - entry_price) / entry_price * 100
                else:  # SHORT
                    realized_pnl = (entry_price - current_price) * quantity
                    pnl_pct = (entry_price - current_price) / entry_price * 100

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

            cursor.close()
            return True

        except Exception as e:
            logger.error(f"[ERROR] 关闭{symbol} {side}持仓失败: {e}")
            return False

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

                        # 如果新信号比旧信号强20分以上 -> 主动反向平仓
                        if new_score > old_score + 20:
                            logger.info(
                                f"[REVERSE] {symbol} 检测到强反向信号! "
                                f"原{opposite_side}得分{old_score}, 新{new_side}得分{new_score} (差距{new_score-old_score}分)"
                            )

                            # 平掉反向持仓
                            self.close_position_by_side(
                                symbol,
                                opposite_side,
                                reason=f"reverse_signal|new_{new_side}_score:{new_score}|old_score:{old_score}"
                            )

                            # 开新方向
                            self.open_position(opp)
                            time.sleep(2)
                            continue

                        # 反向信号不够强,允许对冲
                        logger.info(
                            f"[HEDGE] {symbol} 已有{opposite_side}(得分{old_score})持仓, "
                            f"新{new_side}得分{new_score}未达反转阈值(需>{old_score+20}), 允许对冲"
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
