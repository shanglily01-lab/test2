#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能自动交易服务 - 生产环境版本
直接在服务器后台运行
"""

import time
import sys
from datetime import datetime
from decimal import Decimal
from loguru import logger
import pymysql

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

        # 白名单 - 只做LONG
        self.whitelist = [
            'BCH/USDT', 'LDO/USDT', 'ENA/USDT', 'WIF/USDT', 'TAO/USDT',
            'DASH/USDT', 'ETC/USDT', 'VIRTUAL/USDT', 'NEAR/USDT',
            'AAVE/USDT', 'SUI/USDT', 'UNI/USDT', 'ADA/USDT', 'SOL/USDT'
        ]
        self.threshold = 30

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
        cursor = conn.cursor()

        query = """
            SELECT open_price as open, high_price as high,
                   low_price as low, close_price as close
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s
            AND open_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 60 DAY)) * 1000
            ORDER BY open_time DESC LIMIT %s
        """
        cursor.execute(query, (symbol, timeframe, limit))
        klines = cursor.fetchall()
        cursor.close()

        klines.reverse()
        for k in klines:
            k['open'] = float(k['open'])
            k['high'] = float(k['high'])
            k['low'] = float(k['low'])
            k['close'] = float(k['close'])

        return klines

    def analyze(self, symbol: str):
        """分析并决策"""
        if symbol not in self.whitelist:
            return None

        try:
            klines_1d = self.load_klines(symbol, '1d', 50)
            klines_1h = self.load_klines(symbol, '1h', 100)

            if len(klines_1d) < 30 or len(klines_1h) < 50:
                return None

            score = 0

            # 1. 位置评分
            high_30d = max(k['high'] for k in klines_1d[-30:])
            low_30d = min(k['low'] for k in klines_1d[-30:])
            current = klines_1d[-1]['close']

            if high_30d == low_30d:
                position_pct = 50
            else:
                position_pct = (current - low_30d) / (high_30d - low_30d) * 100

            if position_pct < 30:
                score += 20
            elif position_pct > 70:
                score -= 20
            else:
                score += 5

            # 7日涨幅
            if len(klines_1d) >= 7:
                gain_7d = (current - klines_1d[-7]['close']) / klines_1d[-7]['close'] * 100
                if gain_7d < 10:
                    score += 10
                elif gain_7d > 20:
                    score -= 10

            # 2. 趋势评分
            bullish_1d = sum(1 for k in klines_1d[-30:] if k['close'] > k['open'])
            if bullish_1d > 18:
                score += 20

            # 3. 支撑阻力
            recent = klines_1h[-100:] if len(klines_1h) >= 100 else klines_1h
            highs = [k['high'] for k in recent]
            lows = [k['low'] for k in recent]

            resistance = min([h for h in highs if h > current] or [max(highs)])
            support = max([l for l in lows if l < current] or [min(lows)])

            upside = (resistance - current) / current * 100
            downside = (current - support) / current * 100

            rr = upside / downside if downside > 0 else 0

            if rr >= 2:
                score += 30
            elif rr >= 1.5:
                score += 15
            else:
                score -= 10

            if score >= self.threshold:
                return {
                    'symbol': symbol,
                    'score': score,
                    'support': support,
                    'resistance': resistance,
                    'risk_reward': rr,
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
            'host': '13.212.252.171',
            'port': 3306,
            'user': 'admin',
            'password': 'Tonny@1000',
            'database': 'binance-data'
        }

        self.account_id = 1
        self.position_size_usdt = 400
        self.max_positions = 5
        self.leverage = 5
        self.scan_interval = 300

        self.brain = SmartDecisionBrain(self.db_config)
        self.connection = None
        self.running = True

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
        """获取当前价格"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()
            cursor.close()
            return float(result[0]) if result else None
        except:
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

    def has_position(self, symbol: str):
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
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
        """开仓"""
        symbol = opp['symbol']

        try:
            current_price = self.get_current_price(symbol)
            if not current_price:
                logger.error(f"{symbol} 无法获取价格")
                return False

            quantity = self.position_size_usdt * self.leverage / current_price
            notional_value = quantity * current_price

            logger.info(f"[OPEN] {symbol} LONG | 价格: ${current_price:.4f} | 数量: {quantity:.2f}")

            conn = self._get_connection()
            cursor = conn.cursor()

            # 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price,
                 leverage, notional_value, stop_loss_price, take_profit_price,
                 entry_signal_type, source, status, created_at, updated_at)
                VALUES (%s, %s, 'LONG', %s, %s, %s, %s, %s, %s, %s, 'smart_trader', 'open', NOW(), NOW())
            """, (
                self.account_id, symbol, quantity, current_price, self.leverage,
                notional_value, opp['support'], opp['resistance'],
                f"SMART_BRAIN_{opp['score']}"
            ))

            cursor.close()

            logger.info(
                f"[SUCCESS] {symbol} 开仓成功 | "
                f"止损: ${opp['support']:.4f} | 止盈: ${opp['resistance']:.4f} | "
                f"盈亏比: {opp['risk_reward']:.2f}:1"
            )
            return True

        except Exception as e:
            logger.error(f"[ERROR] {symbol} 开仓失败: {e}")
            return False

    def check_stop_loss_take_profit(self):
        """检查止盈止损"""
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

                if position_side == 'LONG':
                    # LONG: 价格<=止损 或 价格>=止盈
                    if stop_loss and current_price <= float(stop_loss):
                        should_close = True
                        close_reason = 'STOP_LOSS'
                    elif take_profit and current_price >= float(take_profit):
                        should_close = True
                        close_reason = 'TAKE_PROFIT'
                elif position_side == 'SHORT':
                    # SHORT: 价格>=止损 或 价格<=止盈
                    if stop_loss and current_price >= float(stop_loss):
                        should_close = True
                        close_reason = 'STOP_LOSS'
                    elif take_profit and current_price <= float(take_profit):
                        should_close = True
                        close_reason = 'TAKE_PROFIT'

                if should_close:
                    pnl_pct = (current_price - float(entry_price)) / float(entry_price) * 100
                    if position_side == 'SHORT':
                        pnl_pct = -pnl_pct

                    logger.info(
                        f"[{close_reason}] {symbol} {position_side} | "
                        f"开仓: ${entry_price:.4f} | 平仓: ${current_price:.4f} | "
                        f"盈亏: {pnl_pct:+.2f}%"
                    )

                    cursor.execute("""
                        UPDATE futures_positions
                        SET status = 'closed', close_price = %s,
                            close_reason = %s, closed_at = NOW(), updated_at = NOW()
                        WHERE id = %s
                    """, (current_price, close_reason, pos_id))

            cursor.close()

        except Exception as e:
            logger.error(f"[ERROR] 检查止盈止损失败: {e}")

    def close_old_positions(self):
        """关闭超时持仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, symbol FROM futures_positions
                WHERE status = 'open' AND account_id = %s
                AND created_at < DATE_SUB(NOW(), INTERVAL 1 HOUR)
            """, (self.account_id,))

            old_positions = cursor.fetchall()

            for pos in old_positions:
                pos_id, symbol = pos
                current_price = self.get_current_price(symbol)
                if not current_price:
                    continue

                logger.info(f"[CLOSE_TIMEOUT] {symbol} 超时平仓 | 价格: ${current_price:.4f}")

                cursor.execute("""
                    UPDATE futures_positions
                    SET status = 'closed', close_price = %s,
                        close_reason = 'MAX_HOLD_TIME', closed_at = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (current_price, pos_id))

            cursor.close()

        except Exception as e:
            logger.error(f"[ERROR] 关闭超时持仓失败: {e}")

    def run(self):
        """主循环"""
        while self.running:
            try:
                # 1. 检查止盈止损
                self.check_stop_loss_take_profit()

                # 2. 关闭超时持仓
                self.close_old_positions()

                # 3. 检查持仓
                current_positions = self.get_open_positions_count()
                logger.info(f"[STATUS] 持仓: {current_positions}/{self.max_positions}")

                if current_positions >= self.max_positions:
                    logger.info("[SKIP] 已达最大持仓,跳过扫描")
                    time.sleep(self.scan_interval)
                    continue

                # 4. 扫描机会
                logger.info(f"[SCAN] 扫描 {len(self.brain.whitelist)} 个币种...")
                opportunities = self.brain.scan_all()

                if not opportunities:
                    logger.info("[SCAN] 无交易机会")
                    time.sleep(self.scan_interval)
                    continue

                # 5. 执行交易
                logger.info(f"[EXECUTE] 找到 {len(opportunities)} 个机会")

                for opp in opportunities:
                    if self.get_open_positions_count() >= self.max_positions:
                        break

                    if self.has_position(opp['symbol']):
                        logger.info(f"[SKIP] {opp['symbol']} 已有持仓")
                        continue

                    self.open_position(opp)
                    time.sleep(2)

                # 6. 等待
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


if __name__ == '__main__':
    service = SmartTraderService()
    service.run()
