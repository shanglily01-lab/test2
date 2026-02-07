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
        self.entry_timeout = 60  # 1小时建仓时限 (分钟)
        self.batch_config = [
            {'ratio': 0.30, 'name': '第1批', 'wait_minutes': 0},
            {'ratio': 0.30, 'name': '第2批', 'wait_minutes': 15},
            {'ratio': 0.40, 'name': '第3批', 'wait_minutes': 30}
        ]

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
        执行分批入场

        Args:
            signal: 信号字典
            symbol: 交易对
            position_side: 仓位方向 (LONG/SHORT)
            total_margin: 总保证金
            leverage: 杠杆倍数

        Returns:
            入场结果字典，包含各批次信息
        """
        start_time = datetime.now()
        batches_filled = []
        total_quantity = 0
        avg_entry_price = 0

        print(f"\n{'='*80}")
        print(f"[入场执行V3] {symbol} {position_side}")
        print(f"信号类型: {signal.get('signal_type', 'N/A')}")
        print(f"信号评分: {signal.get('score', 0):.1f}/40")
        print(f"总保证金: ${total_margin:.2f}")
        print(f"建仓时限: {self.entry_timeout}分钟")
        print(f"{'='*80}\n")

        # 第1批入场
        print(f"[第1批] 等待5M K线确认...")
        batch1_result = await self.wait_for_5m_confirmation_and_fill(
            symbol=symbol,
            position_side=position_side,
            batch_num=1,
            margin_amount=total_margin * self.batch_config[0]['ratio'],
            leverage=leverage,
            start_time=start_time,
            first_entry_price=None
        )

        if not batch1_result:
            print(f"❌ 第1批建仓失败或超时，放弃整个信号")
            return None

        batches_filled.append(batch1_result)
        total_quantity += batch1_result['quantity']
        avg_entry_price = batch1_result['price']

        print(f"✅ 第1批入场完成: ${batch1_result['price']:.4f}, 数量: {batch1_result['quantity']:.4f}")

        # 等待15分钟后尝试第2批
        await asyncio.sleep(15 * 60)

        if not self._is_within_timeout(start_time):
            print(f"⏰ 建仓超时，仅持有第1批 (30%)")
            return self._create_position_result(batches_filled, position_side, symbol, leverage, total_margin, signal)

        print(f"\n[第2批] 等待5M K线确认...")
        batch2_result = await self.wait_for_5m_confirmation_and_fill(
            symbol=symbol,
            position_side=position_side,
            batch_num=2,
            margin_amount=total_margin * self.batch_config[1]['ratio'],
            leverage=leverage,
            start_time=start_time,
            first_entry_price=batch1_result['price']
        )

        if batch2_result:
            batches_filled.append(batch2_result)
            total_quantity += batch2_result['quantity']
            # 重新计算平均入场价
            avg_entry_price = sum(b['price'] * b['quantity'] for b in batches_filled) / total_quantity
            print(f"✅ 第2批入场完成: ${batch2_result['price']:.4f}, 数量: {batch2_result['quantity']:.4f}")
        else:
            print(f"⏰ 第2批条件不满足或超时，持有第1批 (30%)")
            return self._create_position_result(batches_filled, position_side, symbol, leverage, total_margin, signal)

        # 再等待15分钟后尝试第3批
        await asyncio.sleep(15 * 60)

        if not self._is_within_timeout(start_time):
            print(f"⏰ 建仓超时，持有前2批 (60%)")
            return self._create_position_result(batches_filled, position_side, symbol, leverage, total_margin, signal)

        print(f"\n[第3批] 等待5M K线确认...")
        batch3_result = await self.wait_for_5m_confirmation_and_fill(
            symbol=symbol,
            position_side=position_side,
            batch_num=3,
            margin_amount=total_margin * self.batch_config[2]['ratio'],
            leverage=leverage,
            start_time=start_time,
            first_entry_price=batch1_result['price']
        )

        if batch3_result:
            batches_filled.append(batch3_result)
            total_quantity += batch3_result['quantity']
            avg_entry_price = sum(b['price'] * b['quantity'] for b in batches_filled) / total_quantity
            print(f"✅ 第3批入场完成: ${batch3_result['price']:.4f}, 数量: {batch3_result['quantity']:.4f}")
        else:
            print(f"⏰ 第3批条件不满足，持有前2批 (60%)")

        # 返回完整的持仓结果
        result = self._create_position_result(
            batches_filled, position_side, symbol,
            leverage=leverage, total_margin=total_margin, signal=signal
        )
        print(f"\n{'='*80}")
        print(f"[建仓完成] {symbol} {position_side}")
        print(f"总批次: {len(batches_filled)}/3")
        print(f"总数量: {total_quantity:.4f}")
        print(f"平均价格: ${avg_entry_price:.4f}")
        print(f"用时: {(datetime.now() - start_time).total_seconds() / 60:.1f}分钟")
        print(f"{'='*80}\n")

        return result

    async def wait_for_5m_confirmation_and_fill(
        self,
        symbol: str,
        position_side: str,
        batch_num: int,
        margin_amount: float,
        leverage: int,
        start_time: datetime,
        first_entry_price: Optional[float]
    ) -> Optional[Dict]:
        """
        等待5M K线确认并下单

        Args:
            symbol: 交易对
            position_side: 仓位方向
            batch_num: 批次号 (1/2/3)
            margin_amount: 本批次保证金
            leverage: 杠杆
            start_time: 建仓开始时间
            first_entry_price: 第一批入场价 (用于第2/3批判断)

        Returns:
            批次结果字典 {'batch': 1, 'price': 100.5, 'quantity': 10}
        """
        max_wait_minutes = 15  # 最多等待15分钟 (3根5M K线)
        check_interval = 30    # 每30秒检查一次

        elapsed_checks = 0
        max_checks = (max_wait_minutes * 60) // check_interval

        while elapsed_checks < max_checks:
            # 检查总超时
            if not self._is_within_timeout(start_time):
                print(f"⏰ 总建仓时间超时")
                return None

            # 获取最新5M K线
            latest_5m = await self.get_latest_5m_kline(symbol)
            if not latest_5m:
                await asyncio.sleep(check_interval)
                elapsed_checks += 1
                continue

            current_price = latest_5m['close']
            is_bullish = latest_5m['close'] > latest_5m['open']
            is_bearish = latest_5m['close'] < latest_5m['open']

            # 做多场景
            if position_side == 'LONG':
                should_enter = False

                # 第1批: 等待第一根阳线
                if batch_num == 1 and is_bullish:
                    should_enter = True
                    print(f"✓ 第1批: 出现5M阳线，确认入场")

                # 第2批: 价格高于第1批 OR 出现阳线
                elif batch_num == 2:
                    if current_price > first_entry_price:
                        should_enter = True
                        print(f"✓ 第2批: 价格突破第1批 (${first_entry_price:.4f} → ${current_price:.4f})")
                    elif is_bullish:
                        should_enter = True
                        print(f"✓ 第2批: 回调后出现5M阳线")

                # 第3批: 趋势持续确认
                elif batch_num == 3 and is_bullish:
                    should_enter = True
                    print(f"✓ 第3批: 趋势持续，出现5M阳线")

                if should_enter:
                    # 模拟下单 (实盘对接交易所API)
                    order_result = await self.place_market_order(
                        symbol=symbol,
                        side='BUY',
                        margin_amount=margin_amount,
                        leverage=leverage,
                        current_price=current_price
                    )
                    return {
                        'batch': batch_num,
                        'price': current_price,
                        'quantity': order_result['quantity'],
                        'timestamp': datetime.now()
                    }

            # 做空场景
            elif position_side == 'SHORT':
                should_enter = False

                # 第1批: 等待第一根阴线
                if batch_num == 1 and is_bearish:
                    should_enter = True
                    print(f"✓ 第1批: 出现5M阴线，确认入场")

                # 第2批: 价格低于第1批 OR 出现阴线
                elif batch_num == 2:
                    if current_price < first_entry_price:
                        should_enter = True
                        print(f"✓ 第2批: 价格突破第1批 (${first_entry_price:.4f} → ${current_price:.4f})")
                    elif is_bearish:
                        should_enter = True
                        print(f"✓ 第2批: 反弹后出现5M阴线")

                # 第3批: 趋势持续确认
                elif batch_num == 3 and is_bearish:
                    should_enter = True
                    print(f"✓ 第3批: 趋势持续，出现5M阴线")

                if should_enter:
                    order_result = await self.place_market_order(
                        symbol=symbol,
                        side='SELL',
                        margin_amount=margin_amount,
                        leverage=leverage,
                        current_price=current_price
                    )
                    return {
                        'batch': batch_num,
                        'price': current_price,
                        'quantity': order_result['quantity'],
                        'timestamp': datetime.now()
                    }

            # 等待下一次检查
            await asyncio.sleep(check_interval)
            elapsed_checks += 1

        print(f"⏰ 第{batch_num}批等待超时 ({max_wait_minutes}分钟)")
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
        batches: List[Dict],
        position_side: str,
        symbol: str,
        leverage: int = 10,
        total_margin: float = 0,
        signal: dict = None
    ) -> Dict:
        """创建持仓结果并插入数据库"""
        import json

        if not batches:
            return {
                'success': False,
                'error': 'No batches filled'
            }

        total_quantity = sum(b['quantity'] for b in batches)
        avg_price = sum(b['price'] * b['quantity'] for b in batches) / total_quantity

        # 创建数据库持仓记录
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 获取止盈止损参数（默认3%止损，6%止盈）
            stop_loss_pct = 3.0
            take_profit_pct = 6.0

            if position_side == 'LONG':
                stop_loss_price = avg_price * (1 - stop_loss_pct / 100)
                take_profit_price = avg_price * (1 + take_profit_pct / 100)
            else:  # SHORT
                stop_loss_price = avg_price * (1 + stop_loss_pct / 100)
                take_profit_price = avg_price * (1 - take_profit_pct / 100)

            # 准备批次JSON
            batch_plan_json = json.dumps({
                'batches': [
                    {'ratio': b['ratio'], 'timeout_minutes': b['wait_minutes']}
                    for b in self.batch_config
                ]
            })

            batch_filled_json = json.dumps({
                'batches': [
                    {
                        'batch_num': i + 1,
                        'ratio': self.batch_config[i]['ratio'],
                        'price': float(b['price']),
                        'quantity': float(b['quantity']),
                        'filled_at': datetime.now().isoformat()
                    }
                    for i, b in enumerate(batches)
                ]
            })

            # 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 stop_loss_pct, take_profit_pct,
                 entry_signal_type, entry_score, signal_components,
                 batch_plan, batch_filled, entry_signal_time,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW())
            """, (
                self.account_id,
                symbol,
                position_side,
                total_quantity,
                avg_price,  # entry_price
                avg_price,  # avg_entry_price
                leverage,
                total_quantity * avg_price,  # notional_value
                total_margin,
                stop_loss_price,
                take_profit_price,
                stop_loss_pct,
                take_profit_pct,
                'v3_batch_entry',  # entry_signal_type
                signal.get('total_score', 0) if signal else 0,  # entry_score
                json.dumps(signal.get('breakdown', {}) if signal else {}),  # signal_components
                batch_plan_json,
                batch_filled_json,
                datetime.now()  # entry_signal_time
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
                'batches': batches,
                'total_quantity': total_quantity,
                'avg_price': avg_price,
                'avg_entry_price': avg_price,
                'batch_count': len(batches),
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
