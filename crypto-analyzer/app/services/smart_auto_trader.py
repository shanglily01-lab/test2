#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能自动交易服务
基于决策大脑自动开单、管理仓位
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from decimal import Decimal
from loguru import logger
import pymysql

from app.services.smart_decision_brain import SmartDecisionBrain


class SmartAutoTrader:
    """智能自动交易执行器"""

    def __init__(self, db_config: dict, futures_engine, decision_brain: SmartDecisionBrain = None):
        """
        初始化自动交易器

        Args:
            db_config: 数据库配置
            futures_engine: 合约交易引擎
            decision_brain: 决策大脑实例(可选)
        """
        self.db_config = db_config
        self.futures_engine = futures_engine
        self.decision_brain = decision_brain or SmartDecisionBrain(db_config)
        self.connection = None
        self.running = False
        self.task = None

        # 交易参数
        self.position_size_usdt = 100  # 每笔交易100 USDT
        self.max_positions = 5  # 最大同时持仓数
        self.leverage = 3  # 3倍杠杆
        self.scan_interval = 300  # 5分钟扫描一次

        logger.info(f"✅ 智能自动交易器已初始化 | 仓位规模: ${self.position_size_usdt} | 最大持仓: {self.max_positions} | 杠杆: {self.leverage}x")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True
                )
            except Exception as e:
                logger.error(f"❌ 数据库连接失败: {e}")
                raise
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    host=self.db_config.get('host', 'localhost'),
                    port=self.db_config.get('port', 3306),
                    user=self.db_config.get('user', 'root'),
                    password=self.db_config.get('password', ''),
                    database=self.db_config.get('database', 'binance-data'),
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor,
                    autocommit=True
                )

        return self.connection

    def get_open_positions_count(self) -> int:
        """获取当前持仓数量"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (self.account_id,))

            result = cursor.fetchone()
            cursor.close()

            return result['count'] if result else 0

        except Exception as e:
            logger.error(f"❌ 获取持仓数量失败: {e}")
            return 0

    def has_position_for_symbol(self, symbol: str) -> bool:
        """检查是否已有该币种的持仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE symbol = %s
                AND status = 'open'
                AND account_id = %s
            """, (symbol, self.account_id))

            result = cursor.fetchone()
            cursor.close()

            return result['count'] > 0 if result else False

        except Exception as e:
            logger.error(f"❌ 检查持仓失败: {e}")
            return False

    def open_position(self, opportunity: Dict) -> bool:
        """
        开仓

        Args:
            opportunity: 交易机会

        Returns:
            是否成功
        """
        symbol = opportunity['symbol']
        direction = opportunity['direction']
        trade_params = opportunity['trade_params']

        try:
            # 获取当前价格
            current_price = self.futures_engine.get_current_price(symbol)
            if not current_price:
                logger.error(f"❌ {symbol} 无法获取价格")
                return False

            # 计算数量 (考虑杠杆)
            quantity = self.position_size_usdt * self.leverage / float(current_price)

            # 开仓
            logger.info(f"📈 {symbol} {direction} 开仓 | 价格: ${current_price:.4f} | 数量: {quantity:.4f}")

            result = self.futures_engine.open_position(
                symbol=symbol,
                side=direction,
                quantity=Decimal(str(quantity)),
                entry_price=Decimal(str(current_price)),
                leverage=self.leverage,
                stop_loss_price=Decimal(str(trade_params['stop_loss'])),
                take_profit_price=Decimal(str(trade_params['take_profit'])),
                entry_signal_type=f"SMART_BRAIN_SCORE_{opportunity['score']}"
            )

            if result:
                logger.info(f"✅ {symbol} 开仓成功 | 止损: ${trade_params['stop_loss']:.4f} | 止盈: ${trade_params['take_profit']:.4f}")
                return True
            else:
                logger.error(f"❌ {symbol} 开仓失败")
                return False

        except Exception as e:
            logger.error(f"❌ {symbol} 开仓异常: {e}")
            return False

    def check_and_close_old_positions(self):
        """检查并关闭超时仓位 (超过1小时)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询超过1小时的持仓
            cursor.execute("""
                SELECT id, symbol, position_side, quantity, entry_price, leverage
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
                AND created_at < DATE_SUB(NOW(), INTERVAL 1 HOUR)
            """, (self.account_id,))

            old_positions = cursor.fetchall()
            cursor.close()

            if old_positions:
                logger.info(f"⏰ 发现 {len(old_positions)} 个超时持仓,准备平仓")

                for pos in old_positions:
                    try:
                        # 获取当前价格
                        current_price = self.futures_engine.get_current_price(pos['symbol'])
                        if not current_price:
                            continue

                        # 平仓
                        logger.info(f"⏰ {pos['symbol']} 超时平仓 | 持仓时间 > 1小时")

                        self.futures_engine.close_position(
                            position_id=pos['id'],
                            close_price=Decimal(str(current_price)),
                            close_reason='MAX_HOLD_TIME'
                        )

                    except Exception as e:
                        logger.error(f"❌ {pos['symbol']} 超时平仓失败: {e}")
                        continue

        except Exception as e:
            logger.error(f"❌ 检查超时持仓失败: {e}")

    async def trading_loop(self):
        """交易主循环"""
        logger.info("🚀 智能自动交易服务已启动")

        while self.running:
            try:
                # 1. 检查并关闭超时持仓
                self.check_and_close_old_positions()

                # 2. 检查当前持仓数
                current_positions = self.get_open_positions_count()
                logger.info(f"📊 当前持仓: {current_positions}/{self.max_positions}")

                if current_positions >= self.max_positions:
                    logger.info(f"⚠️ 已达最大持仓数,跳过本轮扫描")
                    await asyncio.sleep(self.scan_interval)
                    continue

                # 3. 扫描交易机会
                opportunities = self.decision_brain.scan_all_symbols()

                if not opportunities:
                    logger.info("📊 本轮扫描无符合条件的交易机会")
                    await asyncio.sleep(self.scan_interval)
                    continue

                # 4. 处理交易机会
                logger.info(f"🎯 找到 {len(opportunities)} 个交易机会,开始执行...")

                for opp in opportunities:
                    # 检查持仓数限制
                    if self.get_open_positions_count() >= self.max_positions:
                        logger.info("⚠️ 已达最大持仓数,停止开仓")
                        break

                    # 检查是否已有该币种持仓
                    if self.has_position_for_symbol(opp['symbol']):
                        logger.info(f"⚠️ {opp['symbol']} 已有持仓,跳过")
                        continue

                    # 开仓
                    self.open_position(opp)

                    # 短暂延迟
                    await asyncio.sleep(2)

                # 5. 等待下一轮
                logger.info(f"💤 等待 {self.scan_interval}秒后进行下一轮扫描...")
                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                logger.error(f"❌ 交易循环异常: {e}")
                await asyncio.sleep(60)

        logger.info("🛑 智能自动交易服务已停止")

    async def start(self):
        """启动自动交易服务"""
        if self.running:
            logger.warning("⚠️ 自动交易服务已在运行")
            return

        self.running = True
        self.task = asyncio.create_task(self.trading_loop())
        logger.info("✅ 智能自动交易服务启动成功")

    async def stop(self):
        """停止自动交易服务"""
        if not self.running:
            logger.warning("⚠️ 自动交易服务未运行")
            return

        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

        logger.info("✅ 智能自动交易服务已停止")

    def get_status(self) -> Dict:
        """获取服务状态"""
        return {
            'running': self.running,
            'current_positions': self.get_open_positions_count(),
            'max_positions': self.max_positions,
            'position_size': self.position_size_usdt,
            'leverage': self.leverage,
            'scan_interval': self.scan_interval,
            'whitelist_count': len(self.decision_brain.whitelist_long)
        }
