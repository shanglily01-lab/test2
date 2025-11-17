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
        self._connection_created_at = time.time()  # 连接创建时间（Unix时间戳）
        self._connection_max_age = 300  # 连接最大存活时间（秒），5分钟
        self.engine = FuturesTradingEngine(db_config)

        logger.info("StopLossMonitor initialized")

    def _should_refresh_connection(self):
        """检查是否需要刷新连接（基于连接年龄）"""
        if self._connection_created_at is None:
            return True
        
        current_time = time.time()
        connection_age = current_time - self._connection_created_at
        
        # 如果连接年龄超过最大存活时间，需要刷新
        return connection_age > self._connection_max_age

    def _ensure_connection(self):
        """确保数据库连接有效（静默检查，不打印日志）"""
        # 检查连接年龄，如果超过最大存活时间则主动刷新
        if self._should_refresh_connection():
            logger.debug("连接已过期，主动刷新数据库连接（止损监控）")
            if self.connection and self.connection.open:
                try:
                    self.connection.close()
                except:
                    pass
            try:
                self.connection = pymysql.connect(**self.db_config)
                self._connection_created_at = time.time()
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
            return
        
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(**self.db_config)
                self._connection_created_at = time.time()
                # 只在首次创建连接时记录（DEBUG级别）
            except Exception as e:
                logger.error(f"❌ 创建数据库连接失败: {e}")
                raise
        else:
            # 静默检查连接是否还活着（不打印日志）
            try:
                self.connection.ping(reconnect=False)
            except Exception as e:
                # 只有在连接真正断开需要重连时才记录
                logger.warning(f"数据库连接已断开，尝试重连: {e}")
                try:
                    if self.connection and self.connection.open:
                        self.connection.close()
                    self.connection = pymysql.connect(**self.db_config)
                    self._connection_created_at = time.time()
                    logger.debug("✅ 数据库连接已重新建立（止损监控）")
                except Exception as e2:
                    logger.error(f"❌ 重连数据库失败: {e2}")
                    raise

    def get_open_positions(self, account_id: Optional[int] = None) -> List[Dict]:
        """
        获取所有持仓中的合约（每次查询都创建新连接，确保获取最新数据）

        Args:
            account_id: 账户ID（可选，如果为None则获取所有账户的持仓）

        Returns:
            持仓列表
        """
        # 每次查询都创建新连接，确保获取最新数据
        connection = pymysql.connect(
            **self.db_config,
            autocommit=True
        )
        
        try:
            cursor = connection.cursor(pymysql.cursors.DictCursor)

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
            """
            
            params = []
            if account_id is not None:
                sql += " AND account_id = %s"
                params.append(account_id)
            
            sql += " ORDER BY open_time ASC"

            cursor.execute(sql, tuple(params) if params else None)
            positions = cursor.fetchall()
            cursor.close()
            
            # 转换 Decimal 类型为 float，确保所有数值字段都能正确序列化
            for pos in positions:
                for key, value in pos.items():
                    if isinstance(value, Decimal):
                        pos[key] = float(value)
            
            return positions
        finally:
            connection.close()

    def get_current_price(self, symbol: str) -> Optional[Decimal]:
        """
        获取当前市场价格（每次查询都创建新连接，确保获取最新数据）

        Args:
            symbol: 交易对（如 BTC/USDT）

        Returns:
            当前价格，如果没有数据返回 None
        """
        # 每次查询都创建新连接，确保获取最新价格
        connection = pymysql.connect(
            **self.db_config,
            autocommit=True
        )
        
        try:
            cursor = connection.cursor(pymysql.cursors.DictCursor)

            # kline_data 表中的 symbol 格式是 BTC/USDT（带斜杠）
            # 优先使用1分钟K线（更及时），如果没有则使用5分钟K线
            sql = """
            SELECT close_price
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1m'
            AND exchange = 'binance'
            ORDER BY open_time DESC
            LIMIT 1
            """

            cursor.execute(sql, (symbol,))
            result = cursor.fetchone()
            
            # 如果1分钟K线没有数据，尝试5分钟K线
            if not result:
                sql = """
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '5m'
                AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol,))
                result = cursor.fetchone()
            
            # 如果5分钟K线也没有数据，尝试1小时K线（最后回退）
            if not result:
                sql = """
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT 1
                """
                cursor.execute(sql, (symbol,))
                result = cursor.fetchone()
            
            cursor.close()
            
            if result:
                return Decimal(str(result['close_price']))
            else:
                logger.warning(f"No price data found for {symbol}")
                return None
        finally:
            connection.close()

    def should_trigger_stop_loss(self, position: Dict, current_price: Decimal) -> bool:
        """
        判断是否触发止损

        Args:
            position: 持仓信息
            current_price: 当前价格

        Returns:
            是否触发止损
        """
        # 检查是否有止损价格
        stop_loss_price = position.get('stop_loss_price')
        if not stop_loss_price or stop_loss_price == 0:
            return False

        try:
            stop_loss_price = Decimal(str(stop_loss_price))
        except (ValueError, TypeError):
            logger.warning(f"Position #{position['id']} has invalid stop_loss_price: {position.get('stop_loss_price')}")
            return False

        position_side = position['position_side']
        symbol = position['symbol']
        position_id = position['id']

        if position_side == 'LONG':
            # 多头：当前价格 <= 止损价（价格跌破止损价）
            should_trigger = current_price <= stop_loss_price
            if should_trigger:
                logger.info(f"🛑 Stop-loss triggered for LONG position #{position_id} {symbol}: "
                          f"current={current_price:.8f}, stop_loss={stop_loss_price:.8f}")
                return True
            else:
                # 添加调试日志，帮助诊断为什么没有触发
                logger.debug(f"LONG #{position_id} {symbol}: 价格={current_price:.8f}, 止损={stop_loss_price:.8f}, "
                           f"差值={float(current_price - stop_loss_price):.8f}, 未触发")
        else:  # SHORT
            # 空头：当前价格 >= 止损价（价格涨破止损价）
            should_trigger = current_price >= stop_loss_price
            if should_trigger:
                logger.info(f"🛑 Stop-loss triggered for SHORT position #{position_id} {symbol}: "
                          f"current={current_price:.8f}, stop_loss={stop_loss_price:.8f}")
                return True
            else:
                # 添加调试日志，帮助诊断为什么没有触发
                logger.debug(f"SHORT #{position_id} {symbol}: 价格={current_price:.8f}, 止损={stop_loss_price:.8f}, "
                           f"差值={float(current_price - stop_loss_price):.8f}, 未触发")

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
        # 检查是否有止盈价格
        take_profit_price = position.get('take_profit_price')
        if not take_profit_price or take_profit_price == 0:
            return False

        try:
            take_profit_price = Decimal(str(take_profit_price))
        except (ValueError, TypeError):
            logger.warning(f"Position #{position['id']} has invalid take_profit_price: {position.get('take_profit_price')}")
            return False

        position_side = position['position_side']

        if position_side == 'LONG':
            # 多头：当前价格 >= 止盈价
            if current_price >= take_profit_price:
                logger.info(f"✅ Take-profit triggered for LONG position #{position['id']} {position['symbol']}: "
                          f"current={current_price:.8f}, take_profit={take_profit_price:.8f}")
                return True
        else:  # SHORT
            # 空头：当前价格 <= 止盈价
            if current_price <= take_profit_price:
                logger.info(f"✅ Take-profit triggered for SHORT position #{position['id']} {position['symbol']}: "
                          f"current={current_price:.8f}, take_profit={take_profit_price:.8f}")
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
            logger.warning(f"Position #{position_id} {symbol}: 无法获取当前价格")
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'no_price',
                'message': 'No price data available'
            }
        
        # 添加调试日志：显示持仓信息和价格对比
        stop_loss_price = position.get('stop_loss_price')
        take_profit_price = position.get('take_profit_price')
        position_side = position.get('position_side', 'UNKNOWN')
        entry_price = position.get('entry_price', 0)
        
        # 计算价格与止损价的关系
        if stop_loss_price:
            if position_side == 'LONG':
                # 多头：止损价应该低于开仓价，如果当前价低于止损价，应该触发
                price_to_stop_loss = float(current_price - Decimal(str(stop_loss_price)))
            else:  # SHORT
                # 空头：止损价应该高于开仓价，如果当前价高于止损价，应该触发
                price_to_stop_loss = float(current_price - Decimal(str(stop_loss_price)))

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

        # 优先级2: 检查止损（使用持仓中保存的止损价格）
        if self.should_trigger_stop_loss(position, current_price):
            stop_loss_price = Decimal(str(position.get('stop_loss_price', 0)))
            logger.info(f"🛑 Stop-loss triggered for position #{position_id} {symbol} @ {current_price:.8f} (stop_loss={stop_loss_price:.8f})")
            result = self.engine.close_position(
                position_id=position_id,
                reason='stop_loss',
                close_price=stop_loss_price  # 使用止损价格平仓
            )
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'stop_loss',
                'current_price': float(current_price),
                'stop_loss_price': float(stop_loss_price),
                'result': result
            }

        # 优先级3: 检查止盈（使用持仓中保存的止盈价格）
        if self.should_trigger_take_profit(position, current_price):
            take_profit_price = Decimal(str(position.get('take_profit_price', 0)))
            logger.info(f"✅ Take-profit triggered for position #{position_id} {symbol} @ {current_price:.8f} (take_profit={take_profit_price:.8f})")
            result = self.engine.close_position(
                position_id=position_id,
                reason='take_profit',
                close_price=take_profit_price  # 使用止盈价格平仓
            )
            return {
                'position_id': position_id,
                'symbol': symbol,
                'status': 'take_profit',
                'current_price': float(current_price),
                'take_profit_price': float(take_profit_price),
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
        # 使用DEBUG级别，避免频繁打印

        # 获取所有持仓
        positions = self.get_open_positions()

        if not positions:
            return {
                'total_positions': 0,
                'monitoring': 0,
                'stop_loss': 0,
                'take_profit': 0,
                'liquidated': 0,
                'no_price': 0
            }


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

        # 只在有重要事件时打印INFO，否则使用DEBUG
        has_important_events = (
            results['stop_loss'] > 0 or 
            results['take_profit'] > 0 or 
            results['liquidated'] > 0
        )
        
        if has_important_events:
            logger.info("=" * 60)
            logger.info(f"监控周期完成（有重要事件）:")
            logger.info(f"  总持仓: {results['total_positions']}")
            logger.info(f"  监控中: {results['monitoring']}")
            if results['stop_loss'] > 0:
                logger.info(f"  🛑 止损触发: {results['stop_loss']}")
            if results['take_profit'] > 0:
                logger.info(f"  ✅ 止盈触发: {results['take_profit']}")
            if results['liquidated'] > 0:
                logger.warning(f"  ⚠️  强平触发: {results['liquidated']}")
            logger.info("=" * 60)
        else:

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
            # 静默关闭，不打印日志

        if hasattr(self, 'engine'):
            # FuturesTradingEngine 没有 close 方法，不需要调用
            pass


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
