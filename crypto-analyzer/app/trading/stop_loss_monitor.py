#!/usr/bin/env python3
"""
止盈止损监控系统
Stop-Loss/Take-Profit Monitoring System

自动监控所有持仓，触发止盈、止损、强平
Automatically monitors all positions and triggers stop-loss, take-profit, and liquidation
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
from decimal import Decimal
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger
import time

from app.trading.futures_trading_engine import FuturesTradingEngine


class StopLossMonitor:
    """止盈止损监控器"""

    def __init__(self, db_config: dict):
        """
        初始化监控器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = pymysql.connect(**db_config)
        self.engine = FuturesTradingEngine(db_config)

        logger.info("StopLossMonitor initialized")

    def get_open_positions(self) -> List[Dict]:
        """
        获取所有持仓中的合约

        Returns:
            持仓列表
        """
        cursor = self.connection.cursor(pymysql.cursors.DictCursor)

        sql = """
        SELECT
            id,
            account_id,
            symbol,
            position_side,
            quantity,
            entry_price,
            leverage,
            margin,
            stop_loss_price,
            take_profit_price,
            liquidation_price,
            unrealized_pnl,
            open_time
        FROM futures_positions
        WHERE status = 'open'
        ORDER BY open_time ASC
        """

        cursor.execute(sql)
        positions = cursor.fetchall()
        cursor.close()

        return positions

    def get_current_price(self, symbol: str) -> Optional[Decimal]:
        """
        获取当前市场价格（从K线数据）

        Args:
            symbol: 交易对（如 BTC/USDT）

        Returns:
            当前价格，如果没有数据返回 None
        """
        cursor = self.connection.cursor(pymysql.cursors.DictCursor)

        # 转换交易对格式: BTC/USDT -> BTCUSDT
        binance_symbol = symbol.replace('/', '')

        sql = """
        SELECT close_price
        FROM klines_1h
        WHERE symbol = %s
        ORDER BY close_time DESC
        LIMIT 1
        """

        cursor.execute(sql, (binance_symbol,))
        result = cursor.fetchone()
        cursor.close()

        if result:
            return Decimal(str(result['close_price']))
        else:
            logger.warning(f"No price data found for {symbol}")
            return None

    def should_trigger_stop_loss(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发止损

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发止损
        """
        if not position['stop_loss_price']:
            return False

        stop_loss_price = Decimal(str(position['stop_loss_price']))
        position_side = position['position_side']

        if position_side == 'LONG':
            # 多头：当前价格 <= 止损价
            if current_price <= stop_loss_price:
                logger.info(f"Stop-loss triggered for LONG position #{position['id']}: "
                          f"current={current_price:.2f}, stop_loss={stop_loss_price:.2f}")
                return True
        else:  # SHORT
            # 空头：当前价格 >= 止损价
            if current_price >= stop_loss_price:
                logger.info(f"Stop-loss triggered for SHORT position #{position['id']}: "
                          f"current={current_price:.2f}, stop_loss={stop_loss_price:.2f}")
                return True

        return False

    def should_trigger_take_profit(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发止盈

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发止盈
        """
        if not position['take_profit_price']:
            return False

        take_profit_price = Decimal(str(position['take_profit_price']))
        position_side = position['position_side']

        if position_side == 'LONG':
            # 多头：当前价格 >= 止盈价
            if current_price >= take_profit_price:
                logger.info(f"Take-profit triggered for LONG position #{position['id']}: "
                          f"current={current_price:.2f}, take_profit={take_profit_price:.2f}")
                return True
        else:  # SHORT
            # 空头：当前价格 <= 止盈价
            if current_price <= take_profit_price:
                logger.info(f"Take-profit triggered for SHORT position #{position['id']}: "
                          f"current={current_price:.2f}, take_profit={take_profit_price:.2f}")
                return True

        return False

    def should_trigger_liquidation(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发强平

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发强平
        """
        if not position['liquidation_price']:
            return False

        liquidation_price = Decimal(str(position['liquidation_price']))
        position_side = position['position_side']

        if position_side == 'LONG':
            # 多头：当前价格 <= 强平价
            if current_price <= liquidation_price:
                logger.warning(f"⚠️ LIQUIDATION triggered for LONG position #{position['id']}: "
                             f"current={current_price:.2f}, liquidation={liquidation_price:.2f}")
                return True
        else:  # SHORT
            # 空头：当前价格 >= 强平价
            if current_price >= liquidation_price:
                logger.warning(f"⚠️ LIQUIDATION triggered for SHORT position #{position['id']}: "
                             f"current={current_price:.2f}, liquidation={liquidation_price:.2f}")
                return True

        return False

    def update_unrealized_pnl(self, position: Dict, current_price: Decimal):
        """
        更新未实现盈亏

        Args:
            position: 持仓信息
            current_price: 当前价格
        """
        entry_price = Decimal(str(position['entry_price']))
        quantity = Decimal(str(position['quantity']))
        position_side = position['position_side']

        # 计算未实现盈亏
        if position_side == 'LONG':
            unrealized_pnl = (current_price - entry_price) * quantity
        else:  # SHORT
            unrealized_pnl = (entry_price - current_price) * quantity

        # 计算收益率
        unrealized_pnl_pct = (unrealized_pnl / Decimal(str(position['margin']))) * 100

        # 更新数据库
        cursor = self.connection.cursor()

        sql = """
        UPDATE futures_positions
        SET
            mark_price = %s,
            unrealized_pnl = %s,
            unrealized_pnl_pct = %s,
            last_update_time = NOW()
        WHERE id = %s
        """

        try:
            cursor.execute(sql, (
                float(current_price),
                float(unrealized_pnl),
                float(unrealized_pnl_pct),
                position['id']
            ))
            self.connection.commit()
        except Exception as e:
            logger.error(f"Failed to update unrealized PnL for position #{position['id']}: {e}")
            self.connection.rollback()
        finally:
            cursor.close()

    def monitor_position(self, position: Dict) -> Dict:
        """
        监控单个持仓

        Args:
            position: 持仓信息

        Returns:
            监控结果
        """
        symbol = position['symbol']
        position_id = position['id']

        # 获取当前价格
        current_price = self.get_current_price(symbol)

        if not current_price:
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'no_price',
                'message': 'No price data available'
            }

        # 更新未实现盈亏
        self.update_unrealized_pnl(position, current_price)

        # 优先级1: 检查强平
        if self.should_trigger_liquidation(position, current_price):
            logger.warning(f"🚨 Liquidating position #{position_id} {symbol}")
            result = self.engine.close_position(
                position_id=position_id,
                reason='liquidation'
            )
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'liquidated',
                'current_price': float(current_price),
                'result': result
            }

        # 优先级2: 检查止损
        if self.should_trigger_stop_loss(position, current_price):
            logger.info(f"🛑 Stop-loss triggered for position #{position_id} {symbol}")
            result = self.engine.close_position(
                position_id=position_id,
                reason='stop_loss'
            )
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'stop_loss',
                'current_price': float(current_price),
                'result': result
            }

        # 优先级3: 检查止盈
        if self.should_trigger_take_profit(position, current_price):
            logger.info(f"✅ Take-profit triggered for position #{position_id} {symbol}")
            result = self.engine.close_position(
                position_id=position_id,
                reason='take_profit'
            )
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'take_profit',
                'current_price': float(current_price),
                'result': result
            }

        # 无触发
        return {
            'position_id': position_id,
            'symbol': symbol,
            'status': 'monitoring',
            'current_price': float(current_price),
            'unrealized_pnl': float(position.get('unrealized_pnl', 0))
        }

    def monitor_all_positions(self) -> Dict:
        """
        监控所有持仓

        Returns:
            监控结果统计
        """
        logger.info("=" * 60)
        logger.info("Starting position monitoring cycle")
        logger.info("=" * 60)

        # 获取所有持仓
        positions = self.get_open_positions()

        if not positions:
            logger.info("No open positions to monitor")
            return {
                'total_positions': 0,
                'monitoring': 0,
                'stop_loss': 0,
                'take_profit': 0,
                'liquidated': 0,
                'no_price': 0
            }

        logger.info(f"Found {len(positions)} open positions")

        # 监控每个持仓
        results = {
            'total_positions': len(positions),
            'monitoring': 0,
            'stop_loss': 0,
            'take_profit': 0,
            'liquidated': 0,
            'no_price': 0,
            'details': []
        }

        for position in positions:
            result = self.monitor_position(position)
            results['details'].append(result)

            # 统计
            status = result['status']
            if status in results:
                results[status] += 1

        # 输出统计
        logger.info("=" * 60)
        logger.info(f"Monitoring cycle completed:")
        logger.info(f"  Total positions: {results['total_positions']}")
        logger.info(f"  Still monitoring: {results['monitoring']}")
        logger.info(f"  Stop-loss triggered: {results['stop_loss']}")
        logger.info(f"  Take-profit triggered: {results['take_profit']}")
        logger.info(f"  Liquidated: {results['liquidated']}")
        logger.info(f"  No price data: {results['no_price']}")
        logger.info("=" * 60)

        return results

    def run_continuous(self, interval_seconds: int = 60):
        """
        持续运行监控（每N秒检查一次）

        Args:
            interval_seconds: 检查间隔（秒），默认60秒
        """
        logger.info(f"Starting continuous monitoring (interval: {interval_seconds}s)")
        logger.info("Press Ctrl+C to stop")

        try:
            while True:
                try:
                    self.monitor_all_positions()
                except Exception as e:
                    logger.error(f"Error in monitoring cycle: {e}", exc_info=True)

                # 等待下一个周期
                time.sleep(interval_seconds)

        except KeyboardInterrupt:
            logger.info("Monitoring stopped by user")
        finally:
            self.close()

    def close(self):
        """关闭数据库连接"""
        if hasattr(self, 'connection') and self.connection:
            self.connection.close()
            logger.info("Database connection closed")

        if hasattr(self, 'engine'):
            self.engine.close()


def main():
    """主函数 - 用于直接运行监控器"""
    import yaml
    from pathlib import Path

    # 加载配置
    config_path = Path(__file__).parent.parent.parent / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config['database']['mysql']

    # 创建监控器
    monitor = StopLossMonitor(db_config)

    # 持续运行（每60秒检查一次）
    monitor.run_continuous(interval_seconds=60)


if __name__ == '__main__':
    main()
