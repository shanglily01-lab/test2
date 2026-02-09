#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能自动交易服务 - V2专用版本
专门运行V2信号的独立服务
支持与V3并行: 同一交易对同方向允许一个V2和一个V3订单
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
from app.services.symbol_rating_manager import SymbolRatingManager
from app.services.volatility_profile_updater import VolatilityProfileUpdater

# 🔥 V2模块导入
from app.strategies.signal_scorer_v2 import SignalScorerV2

# 加载环境变量
load_dotenv()

# 配置日志
logger.remove()
logger.add(
    sys.stderr,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | V2-{message}",
    level="INFO"
)
logger.add(
    "logs/smart_trader_v2_{time:YYYY-MM-DD}.log",
    rotation="00:00",
    retention="30 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {message}",
    level="INFO"
)


class SmartTraderServiceV2:
    """智能交易服务 - V2专用版本"""

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
        self.scan_interval = 300  # 5分钟扫描一次

        self.connection = None
        self.running = True

        # WebSocket 价格服务
        self.ws_service: BinanceWSPriceService = get_ws_price_service()

        # 🔥 V2评分系统
        self.scorer_v2 = SignalScorerV2(self.db_config)
        logger.info("✅ V2评分系统初始化完成")

        # 交易对评级管理器 (3级黑名单制度)
        self.rating_manager = SymbolRatingManager(self.db_config)

        # 加载配置
        import yaml
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
            self.trading_symbols = config.get('trading', {}).get('symbols', [])

        logger.info("=" * 80)
        logger.info("🔥🔥🔥 智能自动交易服务 V2专用版 已启动 🔥🔥🔥")
        logger.info(f"账户ID: {self.account_id}")
        logger.info(f"仓位: 正常${self.position_size_usdt} / 黑名单${self.blacklist_position_size_usdt}")
        logger.info(f"杠杆: {self.leverage}x | 最大持仓: {self.max_positions}")
        logger.info(f"交易对: {len(self.trading_symbols)}个 | 扫描间隔: {self.scan_interval}秒")
        logger.info("📊 V2策略: 1H K线多维度评分 + 72H位置评分")
        logger.info("🔄 并行模式: 允许同一交易对同方向V2+V3各一单")
        logger.info("=" * 80)

    def _get_connection(self):
        """获取数据库连接"""
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

    def has_position(self, symbol: str, side: str, signal_version: str = 'v2'):
        """
        检查是否有V2持仓

        Args:
            symbol: 交易对
            side: 方向(LONG/SHORT)
            signal_version: 信号版本(默认v2)

        Returns:
            bool: True=已有持仓, False=无持仓

        🔥 关键逻辑: 只检查V2版本的持仓,不检查V3
        这样同一交易对同方向可以有V2和V3各一个持仓
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 只检查V2版本的持仓
            cursor.execute("""
                SELECT COUNT(*) FROM futures_positions
                WHERE symbol = %s AND position_side = %s AND signal_version = %s
                AND status IN ('open', 'building') AND account_id = %s
            """, (symbol, side, signal_version, self.account_id))

            count = cursor.fetchone()[0]
            cursor.close()

            return count > 0

        except Exception as e:
            logger.error(f"检查持仓失败: {e}")
            return False

    def get_open_positions_count(self):
        """获取当前持仓数量"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) FROM futures_positions
                WHERE status IN ('open', 'building') AND account_id = %s
            """, (self.account_id,))

            count = cursor.fetchone()[0]
            cursor.close()

            return count

        except Exception as e:
            logger.error(f"获取持仓数量失败: {e}")
            return 0

    def get_symbol_rating(self, symbol: str):
        """
        获取交易对评级

        Returns:
            dict: {'level': 0-3, 'margin_multiplier': 0.25-1.0}
            - L0: 白名单 (100%保证金)
            - L1: 黑名单1级 (25%保证金)
            - L2: 黑名单2级 (12.5%保证金)
            - L3: 禁止交易
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            cursor.execute("""
                SELECT rating_level, margin_multiplier
                FROM trading_symbol_rating
                WHERE symbol = %s
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()

            if result:
                return {
                    'level': result['rating_level'],
                    'margin_multiplier': float(result['margin_multiplier'])
                }
            else:
                # 默认白名单
                return {'level': 0, 'margin_multiplier': 1.0}

        except Exception as e:
            logger.error(f"获取交易对评级失败: {e}")
            return {'level': 0, 'margin_multiplier': 1.0}

    def open_position(self, symbol: str, side: str, score: float, score_details: dict):
        """
        开仓 - V2版本

        Args:
            symbol: 交易对
            side: 方向(LONG/SHORT)
            score: V2评分
            score_details: 评分详情

        Returns:
            bool: 成功/失败
        """
        try:
            # 获取交易对评级
            rating = self.get_symbol_rating(symbol)

            # L3禁止交易
            if rating['level'] == 3:
                logger.warning(f"[V2-OPEN] {symbol} {side} L3禁止交易,跳过")
                return False

            # 根据评级调整仓位
            base_size = self.position_size_usdt if rating['level'] == 0 else self.blacklist_position_size_usdt
            position_size = base_size * rating['margin_multiplier']

            # 获取当前价格
            current_price = self.ws_service.get_price(symbol)
            if not current_price:
                logger.error(f"[V2-OPEN] {symbol} 无法获取价格")
                return False

            # 计算数量
            quantity = (position_size * self.leverage) / current_price

            # 止损止盈设置
            stop_loss_pct = Decimal('3.0')  # 固定3%止损
            take_profit_pct = Decimal('6.0')  # 固定6%止盈

            if side == 'LONG':
                stop_loss_price = current_price * (1 - float(stop_loss_pct) / 100)
                take_profit_price = current_price * (1 + float(take_profit_pct) / 100)
            else:  # SHORT
                stop_loss_price = current_price * (1 + float(stop_loss_pct) / 100)
                take_profit_price = current_price * (1 - float(take_profit_pct) / 100)

            # 插入持仓记录
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, entry_price, quantity, leverage,
                 stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct,
                 status, open_time, signal_version, entry_score, signal_components)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)
            """, (
                self.account_id, symbol, side, current_price, quantity, self.leverage,
                stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct,
                'open', 'v2', score, str(score_details)
            ))

            conn.commit()
            cursor.close()

            logger.info(f"[V2-OPEN] ✅ {symbol} {side} | 评分:{score:.1f} | "
                       f"价格:{current_price:.6f} | 数量:{quantity:.4f} | "
                       f"仓位:${position_size:.0f} (L{rating['level']})")

            return True

        except Exception as e:
            logger.error(f"[V2-OPEN] 开仓失败 {symbol} {side}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def scan_v2_signals(self):
        """扫描V2信号并开仓"""
        try:
            logger.info("=" * 80)
            logger.info(f"[V2-SCAN] 开始扫描V2信号 [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")

            opportunities = 0
            opened = 0

            for symbol in self.trading_symbols:
                try:
                    # 检查是否已有V2持仓
                    for side in ['LONG', 'SHORT']:
                        if self.has_position(symbol, side, 'v2'):
                            continue

                        # V2评分
                        score, score_details, can_trade = self.scorer_v2.calculate_score(symbol, side)

                        if can_trade:
                            opportunities += 1
                            logger.info(f"[V2-SIGNAL] {symbol} {side} 评分:{score:.1f} 可开仓")

                            # 尝试开仓
                            if self.open_position(symbol, side, score, score_details):
                                opened += 1

                except Exception as e:
                    logger.error(f"[V2-SCAN] 处理 {symbol} 失败: {e}")
                    continue

            logger.info(f"[V2-SCAN] 扫描完成 | 机会:{opportunities}个 | 已开仓:{opened}个")
            logger.info("=" * 80)

        except Exception as e:
            logger.error(f"[V2-SCAN] 扫描失败: {e}")
            import traceback
            traceback.print_exc()

    def manage_v2_positions(self):
        """
        管理V2持仓

        V2使用现有的止损止盈机制:
        - 快速止损 (10分钟-2.5%, 30分钟-3.5%, 60分钟-4%)
        - 固定止损 (-3%)
        - 移动止盈 (1.5%回撤)

        这部分逻辑由 smart_trader_service.py 的持仓管理统一处理
        """
        pass

    def run(self):
        """主循环"""
        logger.info("[V2-SERVICE] 开始运行主循环")

        while self.running:
            try:
                # 1. 检查交易开关
                if not self.check_trading_enabled():
                    logger.info("[V2-SERVICE] ⏸️ 交易已停止，跳过扫描")
                    time.sleep(self.scan_interval)
                    continue

                # 2. 检查持仓数量
                current_positions = self.get_open_positions_count()
                if current_positions >= self.max_positions:
                    logger.info(f"[V2-SERVICE] 持仓已满 ({current_positions}/{self.max_positions})，跳过扫描")
                    time.sleep(self.scan_interval)
                    continue

                # 3. 扫描V2信号
                self.scan_v2_signals()

                # 4. 等待下一次扫描
                logger.info(f"[V2-SERVICE] 等待 {self.scan_interval} 秒后继续扫描...")
                time.sleep(self.scan_interval)

            except KeyboardInterrupt:
                logger.info("[V2-SERVICE] 收到停止信号，准备退出...")
                self.running = False
                break
            except Exception as e:
                logger.error(f"[V2-SERVICE] 主循环异常: {e}")
                import traceback
                traceback.print_exc()
                time.sleep(60)  # 发生错误等待1分钟后继续

        logger.info("[V2-SERVICE] 服务已停止")

    def stop(self):
        """停止服务"""
        logger.info("[V2-SERVICE] 正在停止服务...")
        self.running = False


def main():
    """主函数"""
    service = SmartTraderServiceV2()

    try:
        service.run()
    except KeyboardInterrupt:
        logger.info("\n收到中断信号，正在停止服务...")
        service.stop()
    except Exception as e:
        logger.error(f"服务异常退出: {e}")
        import traceback
        traceback.print_exc()
    finally:
        logger.info("V2交易服务已退出")


if __name__ == '__main__':
    main()
