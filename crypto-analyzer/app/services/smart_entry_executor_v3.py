#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
智能分批建仓执行器 V3.0
核心改进: 5M级别精准入场，等待价格确认后才建仓
"""

import asyncio
import pymysql
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()


class SmartEntryExecutorV3:
    """智能分批建仓执行器 V3.0"""

    def __init__(self, db_config: dict, account_id: int = 2):
        self.db_config = db_config
        self.account_id = account_id
        self.entry_timeout = 15  # 15分钟建仓时限 (等待最佳入场时机)
        self.check_interval = 30  # 每30秒检查一次

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            cursorclass=pymysql.cursors.DictCursor
        )

    async def execute_entry(
        self,
        signal: dict,
        symbol: str,
        position_side: str,
        total_margin: float,
        leverage: int = 10
    ) -> Optional[Dict]:
        """
        执行一次性精准入场 - 等待5M K线确认后直接建仓

        Args:
            signal: 信号字典
            symbol: 交易对
            position_side: 仓位方向 (LONG/SHORT)
            total_margin: 总保证金
            leverage: 杠杆倍数

        Returns:
            入场结果字典
        """
        start_time = datetime.now()

        print(f"\n{'='*80}")
        print(f"[入场执行V3] {symbol} {position_side}")
        print(f"信号评分: {signal.get('total_score', 0):.1f}/{signal.get('max_score', 42)}")
        print(f"保证金: ${total_margin:.2f}")
        print(f"杠杆: {leverage}x")
        print(f"建仓时限: {self.entry_timeout}分钟")
        print(f"{'='*80}\n")

        # 等待5M K线确认后一次性建仓
        print(f"[等待入场] 寻找最佳5M K线确认时机...")
        entry_result = await self.wait_for_best_entry(
            symbol=symbol,
            position_side=position_side,
            margin_amount=total_margin,
            leverage=leverage,
            start_time=start_time
        )

        if not entry_result:
            print(f"❌ 建仓失败: 超时或未找到合适入场点")
            return None

        # 创建持仓记录
        result = self._create_position_result(
            entry_result, position_side, symbol,
            leverage=leverage, total_margin=total_margin, signal=signal
        )

        print(f"\n{'='*80}")
        print(f"[建仓完成] {symbol} {position_side}")
        print(f"入场价: ${entry_result['price']:.4f}")
        print(f"数量: {entry_result['quantity']:.4f}")
        print(f"用时: {(datetime.now() - start_time).total_seconds() / 60:.1f}分钟")
        print(f"{'='*80}\n")

        return result

    async def wait_for_best_entry(
        self,
        symbol: str,
        position_side: str,
        margin_amount: float,
        leverage: int,
        start_time: datetime
    ) -> Optional[Dict]:
        """
        等待最佳5M K线确认后一次性入场

        Args:
            symbol: 交易对
            position_side: 仓位方向 (LONG/SHORT)
            margin_amount: 保证金
            leverage: 杠杆
            start_time: 开始时间

        Returns:
            入场结果字典 {'price': 100.5, 'quantity': 10, 'timestamp': datetime}
        """
        elapsed_checks = 0
        max_checks = (self.entry_timeout * 60) // self.check_interval

        while elapsed_checks < max_checks:
            # 检查超时
            if not self._is_within_timeout(start_time):
                print(f"⏰ 建仓超时 ({self.entry_timeout}分钟)")
                return None

            # 获取最新5M K线
            latest_5m = await self.get_latest_5m_kline(symbol)
            if not latest_5m:
                await asyncio.sleep(self.check_interval)
                elapsed_checks += 1
                continue

            current_price = latest_5m['close']
            is_bullish = latest_5m['close'] > latest_5m['open']
            is_bearish = latest_5m['close'] < latest_5m['open']

            should_enter = False

            # 做多: 等待阳线确认
            if position_side == 'LONG' and is_bullish:
                should_enter = True
                print(f"✓ 出现5M阳线，确认做多入场")

            # 做空: 等待阴线确认
            elif position_side == 'SHORT' and is_bearish:
                should_enter = True
                print(f"✓ 出现5M阴线，确认做空入场")

            if should_enter:
                # 下单
                side = 'BUY' if position_side == 'LONG' else 'SELL'
                order_result = await self.place_market_order(
                    symbol=symbol,
                    side=side,
                    margin_amount=margin_amount,
                    leverage=leverage,
                    current_price=current_price
                )
                return {
                    'price': current_price,
                    'quantity': order_result['quantity'],
                    'timestamp': datetime.now()
                }

            # 等待下一次检查
            await asyncio.sleep(self.check_interval)
            elapsed_checks += 1

        print(f"⏰ 等待超时 ({self.entry_timeout}分钟)，未找到合适入场点")
        return None

    async def get_latest_5m_kline(self, symbol: str) -> Optional[Dict]:
        """
        获取最新5M K线

        TODO: 实盘需对接交易所WebSocket或REST API
        这里返回模拟数据
        """
        # 模拟K线数据
        import random
        base_price = 100.0
        open_price = base_price + random.uniform(-1, 1)
        close_price = open_price + random.uniform(-0.5, 0.5)

        return {
            'symbol': symbol,
            'open': open_price,
            'close': close_price,
            'high': max(open_price, close_price) + random.uniform(0, 0.2),
            'low': min(open_price, close_price) - random.uniform(0, 0.2),
            'volume': random.uniform(1000, 10000),
            'timestamp': datetime.now()
        }

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        margin_amount: float,
        leverage: int,
        current_price: float
    ) -> Dict:
        """
        下市价单

        TODO: 实盘需对接交易所API
        """
        # 计算数量
        quantity = (margin_amount * leverage) / current_price

        print(f"[下单] {symbol} {side} 价格:${current_price:.4f} 数量:{quantity:.4f}")

        # 模拟下单成功
        return {
            'order_id': f"ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            'symbol': symbol,
            'side': side,
            'price': current_price,
            'quantity': quantity,
            'status': 'FILLED'
        }

    def _is_within_timeout(self, start_time: datetime) -> bool:
        """检查是否超过建仓时限"""
        elapsed_minutes = (datetime.now() - start_time).total_seconds() / 60
        return elapsed_minutes < self.entry_timeout

    def _create_position_result(
        self,
        entry: Dict,
        position_side: str,
        symbol: str,
        leverage: int = 10,
        total_margin: float = 0,
        signal: dict = None
    ) -> Dict:
        """创建持仓记录并插入数据库"""
        import json

        if not entry:
            return {
                'success': False,
                'error': 'No entry data'
            }

        entry_price = entry['price']
        quantity = entry['quantity']

        # 创建数据库持仓记录
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 获取止盈止损参数（默认3%止损，6%止盈）
            stop_loss_pct = 3.0
            take_profit_pct = 6.0

            if position_side == 'LONG':
                stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
                take_profit_price = entry_price * (1 + take_profit_pct / 100)
            else:  # SHORT
                stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
                take_profit_price = entry_price * (1 - take_profit_pct / 100)

            # 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 stop_loss_pct, take_profit_pct,
                 entry_signal_type, entry_score, signal_components,
                 entry_signal_time, source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW())
            """, (
                self.account_id,
                symbol,
                position_side,
                quantity,
                entry_price,
                entry_price,
                leverage,
                quantity * entry_price,  # notional_value
                total_margin,
                stop_loss_price,
                take_profit_price,
                stop_loss_pct,
                take_profit_pct,
                'v3_single_entry',  # entry_signal_type
                signal.get('total_score', 0) if signal else 0,  # entry_score
                json.dumps(signal.get('breakdown', {}) if signal else {}),  # signal_components
                datetime.now(),  # entry_signal_time
                'v3_executor'  # source
            ))

            position_id = cursor.lastrowid

            # 冻结保证金
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET current_balance = current_balance - %s,
                    frozen_balance = frozen_balance + %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (total_margin, total_margin, self.account_id))

            conn.commit()

            print(f"[数据库] 持仓记录已创建: ID={position_id}")

            return {
                'success': True,
                'position_id': position_id,
                'symbol': symbol,
                'position_side': position_side,
                'entry_price': entry_price,
                'quantity': quantity,
                'margin': total_margin,
                'stop_loss_price': stop_loss_price,
                'take_profit_price': take_profit_price,
                'created_at': datetime.now()
            }

        except Exception as e:
            if 'conn' in locals():
                conn.rollback()
            print(f"❌ 创建持仓记录失败: {e}")
            return {
                'success': False,
                'error': f'Database error: {e}'
            }
        finally:
            if 'cursor' in locals():
                cursor.close()
            if 'conn' in locals():
                conn.close()


# 测试代码
async def test_entry_executor():
    """测试入场执行器"""
    db_config = {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME')
    }

    executor = SmartEntryExecutorV3(db_config)

    # 模拟信号
    signal = {
        'signal_type': 'TREND_momentum_up + volume_surge',
        'score': 28.5
    }

    # 执行建仓
    result = await executor.execute_entry(
        signal=signal,
        symbol='BTC/USDT',
        position_side='LONG',
        total_margin=600.0,
        leverage=10
    )

    if result:
        print(f"\n建仓结果:")
        print(f"  交易对: {result['symbol']}")
        print(f"  方向: {result['position_side']}")
        print(f"  批次数: {result['batch_count']}/3")
        print(f"  总数量: {result['total_quantity']:.4f}")
        print(f"  平均价格: ${result['avg_entry_price']:.4f}")
    else:
        print(f"\n建仓失败")


if __name__ == '__main__':
    asyncio.run(test_entry_executor())
