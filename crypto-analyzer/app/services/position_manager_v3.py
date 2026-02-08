#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
持仓管理器 V3.0
核心功能: 移动止盈、固定止盈止损、动态监控
"""

import asyncio
import pymysql
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
from dotenv import load_dotenv

load_dotenv()


class PositionManagerV3:
    """持仓管理器 V3.0"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 移动止盈配置（阶梯式步进优化）
        self.trailing_threshold_usd = 40.0  # 40U开启移动止盈
        self.trailing_step_usd = 10.0       # 基础步进（动态调整）
        self.use_adaptive_step = True       # 启用自适应步进

        # 固定止盈止损配置
        self.fixed_stop_loss_pct = 0.03     # 固定止损3%
        self.fixed_take_profit_pct = 0.06   # 固定止盈6%

        # 🔥 动态反转止损配置
        self.reversal_stop_loss_pct = 0.01  # 反转止损-1%
        self.reversal_volume_ratio = 1.3    # 反转量能阈值1.3x
        self.reversal_price_change = 0.008  # 反转价格变化阈值0.8%
        self.reversal_profit_threshold = 0.01  # 浮盈>1%时不启用反转止损

        # 持仓时间配置
        self.max_holding_minutes = 300      # 🔥 优化: 最大持仓5小时 (从3小时延长，给趋势更多时间)

        # 🚨 Big4紧急干预配置
        self.big4_emergency_enabled = True
        self.big4_emergency_strength = 12   # Big4强度 >= 12触发紧急干预

        # 检查间隔
        self.check_interval_seconds = 30    # 每30秒检查一次

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor
        )

    def get_adaptive_trailing_step(self, unrealized_pnl_usd: float) -> float:
        """
        🔥 优化: 根据盈利金额动态调整移动止盈步进

        阶梯式步进策略:
        - 浮盈 < 100U: 5U步进（避免小利润快速回撤）
        - 浮盈 100-300U: 10U步进（平衡保护与盈利空间）
        - 浮盈 > 300U: 20U步进（大盈利给予更多空间）

        Args:
            unrealized_pnl_usd: 当前未实现盈亏（USD）

        Returns:
            动态步进值（USD）
        """
        if not self.use_adaptive_step:
            return self.trailing_step_usd

        if unrealized_pnl_usd < 100:
            return 5.0   # 小盈利时更保守，5U步进
        elif unrealized_pnl_usd < 300:
            return 10.0  # 中等盈利，标准10U步进
        else:
            return 20.0  # 大盈利时给予更多空间，20U步进

    async def manage_position(self, position: Dict) -> None:
        """
        持仓管理主循环

        Args:
            position: 持仓字典，包含:
                - id: 持仓ID
                - symbol: 交易对
                - position_side: LONG/SHORT
                - entry_price: 入场价
                - quantity: 数量
                - created_at: 创建时间
        """
        position_id = position['id']
        symbol = position['symbol']
        entry_price = position['entry_price']
        position_side = position['position_side']
        quantity = position['quantity']

        print(f"\n{'='*80}")
        print(f"[持仓管理V3] {symbol} {position_side}")
        print(f"持仓ID: {position_id}")
        print(f"入场价: ${entry_price:.4f}")
        print(f"数量: {quantity:.4f}")
        print(f"{'='*80}\n")

        # 初始化止损止盈
        if position_side == 'LONG':
            stop_loss_price = entry_price * (1 - self.fixed_stop_loss_pct)
            take_profit_price = entry_price * (1 + self.fixed_take_profit_pct)
        else:
            stop_loss_price = entry_price * (1 + self.fixed_stop_loss_pct)
            take_profit_price = entry_price * (1 - self.fixed_take_profit_pct)

        print(f"[初始设置]")
        print(f"  止损价: ${stop_loss_price:.4f} ({self.fixed_stop_loss_pct*100:.1f}%)")
        print(f"  止盈价: ${take_profit_price:.4f} ({self.fixed_take_profit_pct*100:.1f}%)")
        print(f"  移动止盈门槛: ${self.trailing_threshold_usd:.2f}")
        print()

        # 状态变量
        max_unrealized_pnl_usd = 0.0  # 最高浮盈 (USD)
        trailing_active = False        # 移动止盈是否激活
        last_trailing_level = 0        # 上次移动止盈的档位

        # 主循环
        while True:
            try:
                # 🚨 优先检查Big4紧急干预
                if self.big4_emergency_enabled:
                    big4_emergency = await self.check_big4_emergency(symbol, position_side)
                    if big4_emergency['should_close']:
                        print(f"\n🚨🚨🚨 [BIG4紧急干预] {big4_emergency['reason']}")
                        current_price = await self.get_current_price(symbol)
                        unrealized_pnl_usd = self.calculate_unrealized_pnl_usd(
                            entry_price, current_price, quantity, position_side
                        )
                        await self.close_position(
                            position_id, current_price,
                            f"Big4紧急干预: {big4_emergency['reason']}",
                            unrealized_pnl_usd
                        )
                        break

                # 获取当前价格
                current_price = await self.get_current_price(symbol)
                if not current_price:
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                # 计算未实现盈亏
                unrealized_pnl_usd = self.calculate_unrealized_pnl_usd(
                    entry_price, current_price, quantity, position_side
                )
                unrealized_pnl_pct = (unrealized_pnl_usd / (entry_price * quantity)) * 100

                # 更新最高浮盈
                if unrealized_pnl_usd > max_unrealized_pnl_usd:
                    max_unrealized_pnl_usd = unrealized_pnl_usd
                    print(f"[浮盈更新] 当前: ${unrealized_pnl_usd:.2f} ({unrealized_pnl_pct:.2f}%), "
                          f"最高: ${max_unrealized_pnl_usd:.2f}")

                # 检查是否达到移动止盈门槛
                if not trailing_active and unrealized_pnl_usd >= self.trailing_threshold_usd:
                    trailing_active = True
                    print(f"\n{'*'*80}")
                    print(f"[移动止盈激活] 浮盈达到${unrealized_pnl_usd:.2f}，激活移动止盈机制")
                    print(f"{'*'*80}\n")

                # 执行移动止盈
                if trailing_active:
                    # 🔥 优化: 使用动态步进（根据盈利大小调整）
                    dynamic_step = self.get_adaptive_trailing_step(max_unrealized_pnl_usd)
                    current_level = int(max_unrealized_pnl_usd // dynamic_step)
                    profit_to_protect = current_level * dynamic_step

                    # 只有当档位提升时才移动止损
                    if current_level > last_trailing_level:
                        if position_side == 'LONG':
                            # 做多: 止损价 = 入场价 + 保护利润/数量
                            new_stop_loss = entry_price + (profit_to_protect / quantity)
                            if new_stop_loss > stop_loss_price:
                                old_stop_loss = stop_loss_price
                                stop_loss_price = new_stop_loss
                                last_trailing_level = current_level
                                print(f"\n[移动止盈] 档位提升: {last_trailing_level-1} → {current_level}")
                                print(f"  止损价: ${old_stop_loss:.4f} → ${stop_loss_price:.4f}")
                                print(f"  保护利润: ${profit_to_protect:.2f}")
                                print(f"  当前浮盈: ${unrealized_pnl_usd:.2f}\n")
                        else:
                            # 做空: 止损价 = 入场价 - 保护利润/数量
                            new_stop_loss = entry_price - (profit_to_protect / quantity)
                            if new_stop_loss < stop_loss_price:
                                old_stop_loss = stop_loss_price
                                stop_loss_price = new_stop_loss
                                last_trailing_level = current_level
                                print(f"\n[移动止盈] 档位提升: {last_trailing_level-1} → {current_level}")
                                print(f"  止损价: ${old_stop_loss:.4f} → ${stop_loss_price:.4f}")
                                print(f"  保护利润: ${profit_to_protect:.2f}")
                                print(f"  当前浮盈: ${unrealized_pnl_usd:.2f}\n")

                # 🔥 检查反转止损（在固定止损之前）
                reversal = await self.check_reversal_signal(
                    symbol, position_side, entry_price, current_price
                )

                if reversal['detected'] and reversal['should_close']:
                    # 计算反转止损价
                    if position_side == 'LONG':
                        reversal_stop_price = entry_price * (1 - self.reversal_stop_loss_pct)
                        if current_price <= reversal_stop_price or unrealized_pnl_pct <= -self.reversal_stop_loss_pct * 100:
                            print(f"\n[触发反转止损-1%] {reversal['reason']}")
                            print(f"  当前价: ${current_price:.4f}")
                            print(f"  反转止损价: ${reversal_stop_price:.4f}")
                            print(f"  盈亏: ${unrealized_pnl_usd:.2f} ({unrealized_pnl_pct:.2f}%)")
                            await self.close_position(
                                position_id, current_price,
                                f"反转止损-1%: {reversal['reason']}",
                                unrealized_pnl_usd
                            )
                            break
                    else:
                        reversal_stop_price = entry_price * (1 + self.reversal_stop_loss_pct)
                        if current_price >= reversal_stop_price or unrealized_pnl_pct <= -self.reversal_stop_loss_pct * 100:
                            print(f"\n[触发反转止损-1%] {reversal['reason']}")
                            print(f"  当前价: ${current_price:.4f}")
                            print(f"  反转止损价: ${reversal_stop_price:.4f}")
                            print(f"  盈亏: ${unrealized_pnl_usd:.2f} ({unrealized_pnl_pct:.2f}%)")
                            await self.close_position(
                                position_id, current_price,
                                f"反转止损-1%: {reversal['reason']}",
                                unrealized_pnl_usd
                            )
                            break

                # 检查止损触发
                if position_side == 'LONG' and current_price <= stop_loss_price:
                    print(f"\n[触发止损] 价格${current_price:.4f} <= 止损价${stop_loss_price:.4f}")
                    close_reason = '移动止盈止损' if trailing_active else '固定止损'
                    await self.close_position(position_id, current_price, close_reason, unrealized_pnl_usd)
                    break

                if position_side == 'SHORT' and current_price >= stop_loss_price:
                    print(f"\n[触发止损] 价格${current_price:.4f} >= 止损价${stop_loss_price:.4f}")
                    close_reason = '移动止盈止损' if trailing_active else '固定止损'
                    await self.close_position(position_id, current_price, close_reason, unrealized_pnl_usd)
                    break

                # 检查止盈触发
                if position_side == 'LONG' and current_price >= take_profit_price:
                    print(f"\n[触发止盈] 价格${current_price:.4f} >= 止盈价${take_profit_price:.4f}")
                    await self.close_position(position_id, current_price, '固定止盈', unrealized_pnl_usd)
                    break

                if position_side == 'SHORT' and current_price <= take_profit_price:
                    print(f"\n[触发止盈] 价格${current_price:.4f} <= 止盈价${take_profit_price:.4f}")
                    await self.close_position(position_id, current_price, '固定止盈', unrealized_pnl_usd)
                    break

                # 检查超时
                holding_minutes = self.get_holding_minutes(position)
                if holding_minutes >= self.max_holding_minutes:
                    print(f"\n[超时平仓] 持仓时间{holding_minutes}分钟 >= {self.max_holding_minutes}分钟")
                    await self.close_position(position_id, current_price, '超时平仓', unrealized_pnl_usd)
                    break

                # 等待下一次检查
                await asyncio.sleep(self.check_interval_seconds)

            except Exception as e:
                print(f"[错误] 持仓管理异常: {e}")
                await asyncio.sleep(self.check_interval_seconds)

    def calculate_unrealized_pnl_usd(
        self,
        entry_price: float,
        current_price: float,
        quantity: float,
        position_side: str
    ) -> float:
        """
        计算未实现盈亏 (USD)

        Args:
            entry_price: 入场价
            current_price: 当前价
            quantity: 数量
            position_side: LONG/SHORT

        Returns:
            未实现盈亏 (USD)
        """
        if position_side == 'LONG':
            pnl = (current_price - entry_price) * quantity
        else:
            pnl = (entry_price - current_price) * quantity

        return pnl

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        从数据库获取最新价格 (使用最新1M K线的收盘价)

        Returns:
            当前价格，如果获取失败返回None
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 获取最新1M K线的收盘价
            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m'
                ORDER BY open_time DESC
                LIMIT 1
            """, (symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return float(result['close_price'])
            else:
                return None

        except Exception as e:
            print(f"[错误] 获取价格失败: {e}")
            return None

    async def close_position(
        self,
        position_id: int,
        close_price: float,
        close_reason: str,
        realized_pnl: float
    ) -> None:
        """
        平仓

        TODO: 实盘需对接交易所API并更新数据库
        """
        print(f"\n{'='*80}")
        print(f"[平仓执行]")
        print(f"  持仓ID: {position_id}")
        print(f"  平仓价: ${close_price:.4f}")
        print(f"  平仓原因: {close_reason}")
        print(f"  实现盈亏: ${realized_pnl:.2f}")
        print(f"  平仓时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*80}\n")

        # TODO: 更新数据库
        conn = self.get_db_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE futures_positions
                SET status = 'closed',
                    close_price = %s,
                    close_time = NOW(),
                    realized_pnl = %s,
                    close_reason = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (close_price, realized_pnl, close_reason, position_id))

            conn.commit()
            print(f"✅ 数据库更新成功")

        except Exception as e:
            print(f"❌ 数据库更新失败: {e}")
            conn.rollback()

        finally:
            cursor.close()
            conn.close()

    async def check_reversal_signal(
        self,
        symbol: str,
        position_side: str,
        entry_price: float,
        current_price: float
    ) -> Dict:
        """
        🔥 检测反转信号 - 防止"跟进去被反转"

        检测条件:
        1. 持仓LONG，但连续2根5M阴线 + 放量1.3x + 跌幅0.8%
        2. 持仓SHORT，但连续2根5M阳线 + 放量1.3x + 涨幅0.8%
        3. 当前浮盈 < 1%

        Returns:
            {
                'detected': True/False,
                'reason': '连续2根5M阴线+放量1.3x+跌幅0.85%',
                'should_close': True/False
            }
        """
        # 计算当前盈亏
        if position_side == 'LONG':
            pnl_pct = (current_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - current_price) / entry_price

        # 如果已经盈利>1%，不用反转止损（让利润奔跑）
        if pnl_pct > self.reversal_profit_threshold:
            return {'detected': False, 'reason': '浮盈>1%, 让利润奔跑'}

        try:
            # 获取最近5根5M K线
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT open_price, close_price, volume
                FROM kline_data
                WHERE symbol = %s AND timeframe = '5m'
                ORDER BY open_time DESC
                LIMIT 5
            """, (symbol,))

            klines = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(klines) < 5:
                return {'detected': False, 'reason': 'K线数据不足'}

            # 最近2根K线
            recent_2 = klines[:2]

            # 计算量能比率
            recent_volume = sum(k['volume'] for k in recent_2) / 2
            baseline_volume = sum(k['volume'] for k in klines[2:5]) / 3
            volume_ratio = float(recent_volume / baseline_volume) if baseline_volume > 0 else 0

            # 做多场景：检测连续阴线
            if position_side == 'LONG':
                # 检查是否都是阴线
                is_reversal = all(float(k['close_price']) < float(k['open_price']) for k in recent_2)

                if is_reversal and volume_ratio >= self.reversal_volume_ratio:
                    # 计算2根K线的跌幅
                    price_drop = sum(
                        (float(k['open_price']) - float(k['close_price'])) / float(k['open_price'])
                        for k in recent_2
                    )

                    if price_drop >= self.reversal_price_change:
                        return {
                            'detected': True,
                            'reason': f'连续2根5M阴线+放量{volume_ratio:.1f}x+跌幅{price_drop*100:.2f}%',
                            'should_close': True,
                            'volume_ratio': volume_ratio,
                            'price_change': -price_drop
                        }

            # 做空场景：检测连续阳线
            elif position_side == 'SHORT':
                is_reversal = all(float(k['close_price']) > float(k['open_price']) for k in recent_2)

                if is_reversal and volume_ratio >= self.reversal_volume_ratio:
                    price_rise = sum(
                        (float(k['close_price']) - float(k['open_price'])) / float(k['open_price'])
                        for k in recent_2
                    )

                    if price_rise >= self.reversal_price_change:
                        return {
                            'detected': True,
                            'reason': f'连续2根5M阳线+放量{volume_ratio:.1f}x+涨幅{price_rise*100:.2f}%',
                            'should_close': True,
                            'volume_ratio': volume_ratio,
                            'price_change': price_rise
                        }

        except Exception as e:
            print(f"[错误] 反转检测失败: {e}")

        return {'detected': False}

    async def check_big4_emergency(
        self,
        symbol: str,
        position_side: str
    ) -> Dict:
        """
        🚨 检测Big4紧急干预条件

        触发条件:
        1. Big4强度 >= 12 (强烈信号)
        2. Big4方向与持仓方向相反

        Returns:
            {
                'should_close': True/False,
                'reason': 'Big4 BEAR(15) vs 持仓LONG',
                'big4_signal': 'BEAR',
                'big4_strength': 15
            }
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 获取最新Big4信号
            cursor.execute("""
                SELECT signal, strength, created_at
                FROM big4_signals
                WHERE symbol = %s
                AND created_at >= NOW() - INTERVAL 10 MINUTE
                ORDER BY created_at DESC
                LIMIT 1
            """, (symbol,))

            big4 = cursor.fetchone()
            cursor.close()
            conn.close()

            if not big4:
                return {'should_close': False, 'reason': '无Big4信号'}

            big4_signal = big4['signal']
            big4_strength = big4['strength']

            # 检查强度是否达到阈值
            if big4_strength < self.big4_emergency_strength:
                return {
                    'should_close': False,
                    'reason': f'Big4强度{big4_strength}未达阈值{self.big4_emergency_strength}'
                }

            # 解析Big4方向
            if 'BULL' in big4_signal.upper():
                big4_direction = 'LONG'
            elif 'BEAR' in big4_signal.upper():
                big4_direction = 'SHORT'
            else:
                return {'should_close': False, 'reason': 'Big4方向NEUTRAL'}

            # 检查方向是否相反
            if big4_direction == position_side:
                return {
                    'should_close': False,
                    'reason': f'Big4方向{big4_direction}与持仓{position_side}相同'
                }

            # 🚨 触发紧急干预
            return {
                'should_close': True,
                'reason': f'Big4 {big4_signal}(强度{big4_strength}) vs 持仓{position_side}',
                'big4_signal': big4_signal,
                'big4_strength': big4_strength
            }

        except Exception as e:
            print(f"[错误] Big4紧急检测失败: {e}")
            return {'should_close': False, 'reason': f'检测异常: {e}'}

    def get_holding_minutes(self, position: Dict) -> int:
        """获取持仓时长 (分钟)"""
        created_at = position['created_at']
        if isinstance(created_at, str):
            created_at = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')

        elapsed = datetime.now() - created_at
        return int(elapsed.total_seconds() / 60)


# 测试代码
async def test_position_manager():
    """测试持仓管理器"""
    db_config = {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME')
    }

    manager = PositionManagerV3(db_config)

    # 模拟持仓
    position = {
        'id': 12345,
        'symbol': 'BTC/USDT',
        'position_side': 'LONG',
        'entry_price': 100.0,
        'quantity': 10.0,
        'created_at': datetime.now()
    }

    # 启动持仓管理
    await manager.manage_position(position)


if __name__ == '__main__':
    asyncio.run(test_position_manager())
