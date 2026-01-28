#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货短线交易服务 V2 - 动态价格采样策略
核心逻辑:
1. 建仓: 3小时内完成5批次,第1小时采样,第2-3小时动态建仓
2. 持仓: 建仓完成后4小时内让利润奔跑
3. 平仓: 持仓4H后开始采集平仓样本,剩余3H内寻找最优价格平仓
4. 总时长: 8小时 (3H建仓 + 4H持仓 + 1H平仓采样)
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
import json

# 导入 WebSocket 价格服务
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.services.binance_ws_price import get_ws_price_service

load_dotenv()


class PriceSampler:
    """价格采样器 - 动态寻找最优建仓/平仓价格"""

    def __init__(self):
        self.samples = {}  # {symbol: [{'price': x, 'time': t, 'volume': v}, ...]}

    def add_sample(self, symbol: str, price: float, volume: float = 0):
        """添加价格样本"""
        if symbol not in self.samples:
            self.samples[symbol] = []

        self.samples[symbol].append({
            'price': price,
            'time': datetime.utcnow(),
            'volume': volume
        })

        # 只保留最近100个样本
        if len(self.samples[symbol]) > 100:
            self.samples[symbol] = self.samples[symbol][-100:]

    def get_optimal_buy_price(self, symbol: str, current_price: float) -> float:
        """
        获取最优买入价格
        策略: 最近1小时内的价格样本,取较低的20%分位数
        """
        if symbol not in self.samples or len(self.samples[symbol]) < 10:
            return current_price

        # 过滤最近1小时的样本
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_samples = [
            s for s in self.samples[symbol]
            if s['time'] >= one_hour_ago
        ]

        if len(recent_samples) < 5:
            return current_price

        # 价格排序,取20%分位数 (偏低价格)
        prices = sorted([s['price'] for s in recent_samples])
        percentile_20 = int(len(prices) * 0.2)
        optimal_price = prices[percentile_20]

        # 如果当前价格已经低于最优价格,使用当前价格
        return min(current_price, optimal_price)

    def get_optimal_sell_price(self, symbol: str, current_price: float, entry_price: float) -> Tuple[bool, float]:
        """
        获取最优卖出价格
        策略: 最近1小时内的价格样本,取较高的80%分位数
        返回: (是否应该卖出, 最优价格)
        """
        if symbol not in self.samples or len(self.samples[symbol]) < 10:
            return False, current_price

        # 过滤最近1小时的样本
        one_hour_ago = datetime.utcnow() - timedelta(hours=1)
        recent_samples = [
            s for s in self.samples[symbol]
            if s['time'] >= one_hour_ago
        ]

        if len(recent_samples) < 5:
            return False, current_price

        # 价格排序,取80%分位数 (偏高价格)
        prices = sorted([s['price'] for s in recent_samples])
        percentile_80 = int(len(prices) * 0.8)
        optimal_price = prices[percentile_80]

        # 当前价格是否接近最优价格 (在3%范围内)
        profit_pct = (current_price - entry_price) / entry_price
        optimal_profit_pct = (optimal_price - entry_price) / entry_price

        # 如果当前价格 >= 80%分位数的97%,认为是好的卖出时机
        if current_price >= optimal_price * 0.97 and profit_pct > 0:
            return True, current_price

        return False, optimal_price


class DynamicPositionManager:
    """动态仓位管理器 - 支持分阶段建仓和平仓"""

    # 5批次建仓比例
    BATCH_RATIOS = [0.15, 0.20, 0.20, 0.20, 0.25]

    def __init__(self, db_config: dict, total_capital: float = 50000, per_coin_capital: float = 2000):
        self.db_config = db_config
        self.connection = None

        self.total_capital = total_capital
        self.per_coin_capital = per_coin_capital  # 单币2000 USDT
        self.max_positions = 15  # 最多15个持仓

        # 短线风险管理
        self.take_profit_pct = 0.15  # 15% 止盈
        self.stop_loss_pct = 0.05    # 5% 止损

        # 时间参数
        self.sampling_duration = 3600  # 1小时采样期
        self.building_duration = 7200  # 2小时建仓期
        self.holding_duration = 14400  # 4小时持仓期 (让利润奔跑)
        self.exit_sampling_duration = 3600  # 1小时平仓采样期
        self.total_duration = 28800  # 8小时总时长

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

    def create_position(self, symbol: str, entry_price: float, signal_strength: float):
        """
        创建新持仓 (初始状态: 采样阶段)
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.utcnow()

            cursor.execute("""
                INSERT INTO spot_positions_v2
                (symbol, status, phase, signal_strength,
                 sampling_start_time, building_start_time,
                 target_entry_price, current_batch, total_batches,
                 total_quantity, total_cost, avg_entry_price,
                 take_profit_price, stop_loss_price,
                 created_at, updated_at)
                VALUES
                (%s, 'active', 'sampling', %s,
                 %s, %s,
                 %s, 0, 5,
                 0, 0, 0,
                 0, 0,
                 %s, %s)
            """, (
                symbol, signal_strength,
                now, now + timedelta(seconds=self.sampling_duration),
                entry_price, now, now
            ))

            conn.commit()
            cursor.close()

            logger.info(f"✅ 创建持仓: {symbol} @ {entry_price:.4f} (开始采样阶段)")

        except Exception as e:
            logger.error(f"创建持仓失败 {symbol}: {e}")

    def add_batch(self, position: Dict, batch_index: int, price: float, quantity: float):
        """添加一批建仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            pos_id = position['id']
            old_qty = float(position['total_quantity'])
            old_cost = float(position['total_cost'])

            new_qty = old_qty + quantity
            new_cost = old_cost + (price * quantity)
            new_avg = new_cost / new_qty if new_qty > 0 else 0

            # 更新止盈止损
            take_profit = new_avg * (1 + self.take_profit_pct)
            stop_loss = new_avg * (1 - self.stop_loss_pct)

            cursor.execute("""
                UPDATE spot_positions_v2
                SET current_batch = %s,
                    total_quantity = %s,
                    total_cost = %s,
                    avg_entry_price = %s,
                    take_profit_price = %s,
                    stop_loss_price = %s,
                    updated_at = %s
                WHERE id = %s
            """, (
                batch_index + 1, new_qty, new_cost, new_avg,
                take_profit, stop_loss,
                datetime.utcnow(), pos_id
            ))

            conn.commit()
            cursor.close()

            logger.info(f"✅ {position['symbol']} 完成第{batch_index + 1}批建仓 @ {price:.4f}, "
                       f"数量: {quantity:.4f}, 新均价: {new_avg:.4f}")

            # 如果完成5批,进入持仓阶段
            if batch_index == 4:
                self._enter_holding_phase(pos_id)

        except Exception as e:
            logger.error(f"添加批次失败: {e}")

    def _enter_holding_phase(self, position_id: int):
        """进入持仓阶段 (让利润奔跑)"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            now = datetime.utcnow()
            holding_end = now + timedelta(seconds=self.holding_duration)

            cursor.execute("""
                UPDATE spot_positions_v2
                SET phase = 'holding',
                    holding_start_time = %s,
                    exit_sampling_start_time = %s,
                    updated_at = %s
                WHERE id = %s
            """, (now, holding_end, now, position_id))

            conn.commit()
            cursor.close()

            logger.info(f"✅ 持仓 #{position_id} 进入持仓阶段 (4小时让利润奔跑)")

        except Exception as e:
            logger.error(f"进入持仓阶段失败: {e}")

    def close_position(self, position: Dict, price: float, reason: str):
        """平仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            total_cost = float(position['total_cost'])
            total_qty = float(position['total_quantity'])
            revenue = price * total_qty
            pnl = revenue - total_cost
            pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

            cursor.execute("""
                UPDATE spot_positions_v2
                SET status = 'closed',
                    phase = 'closed',
                    exit_price = %s,
                    exit_time = %s,
                    realized_pnl = %s,
                    realized_pnl_pct = %s,
                    close_reason = %s,
                    updated_at = %s
                WHERE id = %s
            """, (
                price, datetime.utcnow(), pnl, pnl_pct, reason,
                datetime.utcnow(), position['id']
            ))

            conn.commit()
            cursor.close()

            pnl_sign = '+' if pnl >= 0 else ''
            logger.info(f"✅ 平仓: {position['symbol']} @ {price:.4f}, "
                       f"盈亏: {pnl_sign}{pnl:.2f} USDT ({pnl_sign}{pnl_pct:.2f}%), 原因: {reason}")

        except Exception as e:
            logger.error(f"平仓失败: {e}")

    def get_positions(self, status: str = 'active') -> List[Dict]:
        """获取持仓列表"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM spot_positions_v2
                WHERE status = %s
                ORDER BY created_at DESC
            """, (status,))

            positions = cursor.fetchall()
            cursor.close()
            return positions

        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []


class SpotTraderV2:
    """现货短线交易服务 V2"""

    def __init__(self):
        self.db_config = {
            'host': os.getenv('DB_HOST', '13.212.252.171'),
            'port': int(os.getenv('DB_PORT', '3306')),
            'user': os.getenv('DB_USER', 'admin'),
            'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
            'database': 'binance-data'
        }

        self.position_manager = DynamicPositionManager(self.db_config)
        self.price_sampler = PriceSampler()
        self.ws_price_service = get_ws_price_service(is_futures=False)

        # 监控币种 (从24H强势信号中筛选)
        self.symbols = self._get_strong_signals()

        logger.info(f"初始化完成, 监控币种: {len(self.symbols)}个")

    def _get_strong_signals(self) -> List[str]:
        """获取24H强势做多信号的币种"""
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol
                FROM price_stats_24h
                WHERE change_24h > 3.0
                  AND quote_volume_24h > 5000000
                  AND trend IN ('STRONG_UP', 'UP')
                ORDER BY change_24h DESC
                LIMIT 30
            """)

            results = cursor.fetchall()
            cursor.close()
            conn.close()

            symbols = [r['symbol'] for r in results]
            logger.info(f"发现 {len(symbols)} 个强势信号币种")

            return symbols

        except Exception as e:
            logger.error(f"获取强势信号失败: {e}")
            return []

    def collect_price_samples(self):
        """采集价格样本 (每个周期都执行)"""
        for symbol in self.symbols:
            price = self.ws_price_service.get_price(symbol)
            if price:
                self.price_sampler.add_sample(symbol, price)

    def check_new_opportunities(self):
        """检查新开仓机会"""
        # 检查当前持仓数
        active_positions = self.position_manager.get_positions('active')
        if len(active_positions) >= self.position_manager.max_positions:
            logger.info(f"已达到最大持仓数 {self.position_manager.max_positions}, 跳过新开仓")
            return

        # 检查是否已持仓
        active_symbols = {pos['symbol'] for pos in active_positions}

        # 扫描强势信号
        for symbol in self.symbols:
            if symbol in active_symbols:
                continue

            price = self.ws_price_service.get_price(symbol)
            if not price:
                continue

            # 简单信号判断: 24H涨幅 + 趋势
            signal_strength = self._calculate_signal_strength(symbol)

            if signal_strength >= 40:  # 阈值40分
                logger.info(f"🎯 发现机会: {symbol}, 信号强度: {signal_strength:.0f}, 价格: {price:.4f}")
                self.position_manager.create_position(symbol, price, signal_strength)
                break  # 每次只开1个新仓

    def _calculate_signal_strength(self, symbol: str) -> float:
        """计算信号强度 (简化版)"""
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT change_24h, trend, quote_volume_24h
                FROM price_stats_24h
                WHERE symbol = %s
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result:
                return 0

            change_24h = float(result['change_24h'] or 0)
            trend = result['trend']

            score = 0

            # 24H涨幅评分
            if change_24h >= 10:
                score += 40
            elif change_24h >= 5:
                score += 30
            elif change_24h >= 3:
                score += 20

            # 趋势评分
            if trend == 'STRONG_UP':
                score += 20
            elif trend == 'UP':
                score += 10

            return score

        except Exception as e:
            logger.error(f"计算信号强度失败: {e}")
            return 0

    def manage_positions(self):
        """管理现有持仓 (核心逻辑)"""
        positions = self.position_manager.get_positions('active')

        for pos in positions:
            symbol = pos['symbol']
            phase = pos['phase']
            current_price = self.ws_price_service.get_price(symbol)

            if not current_price:
                continue

            avg_price = float(pos['avg_entry_price']) if pos['avg_entry_price'] else 0
            current_batch = pos['current_batch']

            # 阶段1: 采样阶段 (第1小时)
            if phase == 'sampling':
                self._handle_sampling_phase(pos, current_price)

            # 阶段2: 建仓阶段 (第2-3小时)
            elif phase == 'building':
                self._handle_building_phase(pos, current_price)

            # 阶段3: 持仓阶段 (4小时让利润奔跑)
            elif phase == 'holding':
                self._handle_holding_phase(pos, current_price, avg_price)

            # 阶段4: 平仓采样阶段
            elif phase == 'exit_sampling':
                self._handle_exit_sampling_phase(pos, current_price, avg_price)

            # 阶段5: 平仓阶段
            elif phase == 'exit_ready':
                self._handle_exit_phase(pos, current_price, avg_price)

    def _handle_sampling_phase(self, pos: Dict, current_price: float):
        """处理采样阶段"""
        # 检查是否采样完成
        building_start = pos['building_start_time']
        if datetime.utcnow() >= building_start:
            # 进入建仓阶段
            try:
                conn = self.position_manager._get_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE spot_positions_v2
                    SET phase = 'building',
                        building_start_time = %s,
                        updated_at = %s
                    WHERE id = %s
                """, (datetime.utcnow(), datetime.utcnow(), pos['id']))

                conn.commit()
                cursor.close()

                logger.info(f"✅ {pos['symbol']} 采样完成, 进入建仓阶段")

            except Exception as e:
                logger.error(f"更新阶段失败: {e}")

    def _handle_building_phase(self, pos: Dict, current_price: float):
        """处理建仓阶段 (动态寻找最优价格)"""
        symbol = pos['symbol']
        current_batch = pos['current_batch']

        if current_batch >= 5:
            return  # 已完成建仓

        # 获取最优买入价格
        optimal_price = self.price_sampler.get_optimal_buy_price(symbol, current_price)

        # 如果当前价格接近最优价格 (在2%范围内), 执行建仓
        price_diff_pct = abs(current_price - optimal_price) / optimal_price

        if price_diff_pct <= 0.02 or current_price <= optimal_price:
            # 计算本批次金额
            batch_amount = self.position_manager.per_coin_capital * self.position_manager.BATCH_RATIOS[current_batch]
            quantity = batch_amount / current_price

            self.position_manager.add_batch(pos, current_batch, current_price, quantity)

        # 检查建仓超时 (2小时内必须完成)
        building_start = pos['building_start_time']
        elapsed = (datetime.utcnow() - building_start).total_seconds()

        if elapsed > self.position_manager.building_duration:
            # 强制完成剩余批次
            logger.warning(f"⚠️ {symbol} 建仓超时, 强制完成剩余批次")
            for batch_idx in range(current_batch, 5):
                batch_amount = self.position_manager.per_coin_capital * self.position_manager.BATCH_RATIOS[batch_idx]
                quantity = batch_amount / current_price
                self.position_manager.add_batch(pos, batch_idx, current_price, quantity)

    def _handle_holding_phase(self, pos: Dict, current_price: float, avg_price: float):
        """处理持仓阶段 (让利润奔跑, 只检查止盈止损)"""
        symbol = pos['symbol']
        take_profit = float(pos['take_profit_price'])
        stop_loss = float(pos['stop_loss_price'])

        # 止盈
        if current_price >= take_profit:
            self.position_manager.close_position(pos, current_price, '止盈15%')
            return

        # 止损
        if current_price <= stop_loss:
            self.position_manager.close_position(pos, current_price, '止损5%')
            return

        # 检查是否进入平仓采样阶段 (持仓4小时后)
        exit_sampling_start = pos['exit_sampling_start_time']
        if datetime.utcnow() >= exit_sampling_start:
            try:
                conn = self.position_manager._get_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE spot_positions_v2
                    SET phase = 'exit_sampling',
                        updated_at = %s
                    WHERE id = %s
                """, (datetime.utcnow(), pos['id']))

                conn.commit()
                cursor.close()

                logger.info(f"✅ {symbol} 进入平仓采样阶段 (寻找最优平仓价格)")

            except Exception as e:
                logger.error(f"更新阶段失败: {e}")

    def _handle_exit_sampling_phase(self, pos: Dict, current_price: float, avg_price: float):
        """处理平仓采样阶段 (采集1小时价格样本)"""
        symbol = pos['symbol']

        # 检查止盈止损
        take_profit = float(pos['take_profit_price'])
        stop_loss = float(pos['stop_loss_price'])

        if current_price >= take_profit:
            self.position_manager.close_position(pos, current_price, '止盈15%')
            return

        if current_price <= stop_loss:
            self.position_manager.close_position(pos, current_price, '止损5%')
            return

        # 检查是否采样完成 (1小时)
        exit_sampling_start = pos['exit_sampling_start_time']
        elapsed = (datetime.utcnow() - exit_sampling_start).total_seconds()

        if elapsed >= self.position_manager.exit_sampling_duration:
            # 进入平仓阶段
            try:
                conn = self.position_manager._get_connection()
                cursor = conn.cursor()

                cursor.execute("""
                    UPDATE spot_positions_v2
                    SET phase = 'exit_ready',
                        updated_at = %s
                    WHERE id = %s
                """, (datetime.utcnow(), pos['id']))

                conn.commit()
                cursor.close()

                logger.info(f"✅ {symbol} 平仓采样完成, 进入平仓阶段")

            except Exception as e:
                logger.error(f"更新阶段失败: {e}")

    def _handle_exit_phase(self, pos: Dict, current_price: float, avg_price: float):
        """处理平仓阶段 (动态寻找最优平仓价格)"""
        symbol = pos['symbol']

        # 检查止盈止损
        take_profit = float(pos['take_profit_price'])
        stop_loss = float(pos['stop_loss_price'])

        if current_price >= take_profit:
            self.position_manager.close_position(pos, current_price, '止盈15%')
            return

        if current_price <= stop_loss:
            self.position_manager.close_position(pos, current_price, '止损5%')
            return

        # 检查最优卖出价格
        should_sell, optimal_price = self.price_sampler.get_optimal_sell_price(symbol, current_price, avg_price)

        if should_sell:
            profit_pct = (current_price - avg_price) / avg_price * 100
            self.position_manager.close_position(pos, current_price, f'最优平仓({profit_pct:+.2f}%)')
            return

        # 检查总时长超时 (8小时)
        created_at = pos['created_at']
        elapsed = (datetime.utcnow() - created_at).total_seconds()

        if elapsed > self.position_manager.total_duration:
            profit_pct = (current_price - avg_price) / avg_price * 100
            self.position_manager.close_position(pos, current_price, f'8H超时平仓({profit_pct:+.2f}%)')

    async def run_forever(self):
        """主循环"""
        # 启动 WebSocket 价格服务
        asyncio.create_task(self.ws_price_service.start(self.symbols))
        await asyncio.sleep(5)

        logger.info("✅ WebSocket 价格服务已启动")

        cycle = 0
        while True:
            try:
                cycle += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 短线交易周期 #{cycle} - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                # 1. 采集价格样本 (每个周期都执行)
                self.collect_price_samples()

                # 2. 管理现有持仓 (核心逻辑)
                self.manage_positions()

                # 3. 检查新开仓机会
                self.check_new_opportunities()

                # 4. 每30秒一个周期
                await asyncio.sleep(30)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                await asyncio.sleep(60)


def main():
    """主函数"""
    service = SpotTraderV2()
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
