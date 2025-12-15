#!/usr/bin/env python3
"""
V2策略信号测试脚本
测试开仓信号和平仓信号的检测逻辑
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
import pymysql
from pymysql.cursors import DictCursor

# 加载配置
import yaml
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

db_config = config['database']


def get_db_connection():
    return pymysql.connect(
        host=db_config['host'],
        port=db_config.get('port', 3306),
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        cursorclass=DictCursor
    )


def get_klines(symbol: str, timeframe: str, limit: int = 100):
    """获取K线数据"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT timestamp, open_price, high_price, low_price, close_price, volume
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
            ORDER BY timestamp DESC
            LIMIT %s
        """, (symbol, timeframe, limit))

        klines = cursor.fetchall()
        return list(reversed(klines))  # 按时间正序
    finally:
        cursor.close()
        conn.close()


def calculate_ema(prices: list, period: int) -> list:
    """计算EMA"""
    if len(prices) < period:
        return [None] * len(prices)

    multiplier = 2 / (period + 1)
    ema_values = [None] * (period - 1)

    # 初始EMA = 前period个价格的SMA
    sma = sum(prices[:period]) / period
    ema_values.append(sma)

    # 后续EMA
    for i in range(period, len(prices)):
        ema = (prices[i] - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(ema)

    return ema_values


def calculate_ma(prices: list, period: int) -> list:
    """计算MA"""
    if len(prices) < period:
        return [None] * len(prices)

    ma_values = [None] * (period - 1)
    for i in range(period - 1, len(prices)):
        ma = sum(prices[i - period + 1:i + 1]) / period
        ma_values.append(ma)

    return ma_values


def test_open_signals(symbol: str):
    """测试开仓信号"""
    print(f"\n{'='*60}")
    print(f"测试开仓信号 - {symbol}")
    print(f"{'='*60}")

    # 获取15分钟K线
    klines_15m = get_klines(symbol, '15m', 100)
    if len(klines_15m) < 30:
        print(f"❌ K线数据不足: {len(klines_15m)} 条")
        return

    # 提取收盘价
    closes = [float(k['close_price']) for k in klines_15m]

    # 计算EMA 9/26
    ema9 = calculate_ema(closes, 9)
    ema26 = calculate_ema(closes, 26)

    # 计算MA10和EMA10
    ma10 = calculate_ma(closes, 10)
    ema10 = calculate_ema(closes, 10)

    print(f"\n📊 最近5根K线的指标:")
    print("-" * 80)
    print(f"{'时间':<20} {'收盘价':<12} {'EMA9':<12} {'EMA26':<12} {'差值%':<10} {'MA10':<12} {'EMA10':<12}")
    print("-" * 80)

    for i in range(-5, 0):
        if ema9[i] and ema26[i]:
            diff_pct = (ema9[i] - ema26[i]) / ema26[i] * 100
            timestamp = klines_15m[i]['timestamp']
            if isinstance(timestamp, datetime):
                time_str = timestamp.strftime('%Y-%m-%d %H:%M')
            else:
                time_str = str(timestamp)[:16]

            ma10_str = f"{ma10[i]:.2f}" if ma10[i] else "N/A"
            ema10_str = f"{ema10[i]:.2f}" if ema10[i] else "N/A"

            print(f"{time_str:<20} {closes[i]:<12.2f} {ema9[i]:<12.2f} {ema26[i]:<12.2f} {diff_pct:<10.4f} {ma10_str:<12} {ema10_str:<12}")

    # 检测金叉/死叉
    print(f"\n🔍 信号检测:")
    print("-" * 60)

    # 检查最近的穿越
    for i in range(-10, -1):
        if ema9[i-1] and ema26[i-1] and ema9[i] and ema26[i]:
            prev_diff = ema9[i-1] - ema26[i-1]
            curr_diff = ema9[i] - ema26[i]

            # 金叉检测
            if prev_diff <= 0 and curr_diff > 0:
                strength = abs(curr_diff / ema26[i] * 100)
                timestamp = klines_15m[i]['timestamp']
                if isinstance(timestamp, datetime):
                    time_str = timestamp.strftime('%Y-%m-%d %H:%M')
                else:
                    time_str = str(timestamp)[:16]

                status = "✅ 有效" if strength >= 0.15 else "⚠️ 强度不足"
                print(f"🟢 金叉 @ {time_str} | 强度: {strength:.4f}% | {status}")

                # 检查MA方向一致性
                if ma10[i] and ma10[i-1] and ema10[i] and ema10[i-1]:
                    ma_up = ma10[i] > ma10[i-1]
                    ema_up = ema10[i] > ema10[i-1]
                    if ma_up and ema_up:
                        print(f"   └─ MA/EMA方向: ✅ 一致向上 (适合做多)")
                    else:
                        print(f"   └─ MA/EMA方向: ❌ 不一致 (MA↑={ma_up}, EMA↑={ema_up})")

            # 死叉检测
            if prev_diff >= 0 and curr_diff < 0:
                strength = abs(curr_diff / ema26[i] * 100)
                timestamp = klines_15m[i]['timestamp']
                if isinstance(timestamp, datetime):
                    time_str = timestamp.strftime('%Y-%m-%d %H:%M')
                else:
                    time_str = str(timestamp)[:16]

                status = "✅ 有效" if strength >= 0.15 else "⚠️ 强度不足"
                print(f"🔴 死叉 @ {time_str} | 强度: {strength:.4f}% | {status}")

                # 检查MA方向一致性
                if ma10[i] and ma10[i-1] and ema10[i] and ema10[i-1]:
                    ma_down = ma10[i] < ma10[i-1]
                    ema_down = ema10[i] < ema10[i-1]
                    if ma_down and ema_down:
                        print(f"   └─ MA/EMA方向: ✅ 一致向下 (适合做空)")
                    else:
                        print(f"   └─ MA/EMA方向: ❌ 不一致 (MA↓={ma_down}, EMA↓={ema_down})")

    # 当前趋势状态
    print(f"\n📈 当前趋势状态:")
    print("-" * 60)

    if ema9[-1] and ema26[-1]:
        curr_diff = ema9[-1] - ema26[-1]
        curr_diff_pct = curr_diff / ema26[-1] * 100

        if curr_diff > 0:
            print(f"趋势: 🟢 多头 (EMA9 > EMA26)")
        else:
            print(f"趋势: 🔴 空头 (EMA9 < EMA26)")

        print(f"当前差值: {curr_diff_pct:.4f}%")
        print(f"信号强度阈值: 0.15% (最小) / 0.5% (高强度)")

        if abs(curr_diff_pct) >= 0.5:
            print(f"状态: ✅ 高强度信号")
        elif abs(curr_diff_pct) >= 0.15:
            print(f"状态: ✅ 有效信号")
        else:
            print(f"状态: ⚠️ 信号强度不足")


def test_close_signals(symbol: str):
    """测试平仓信号"""
    print(f"\n{'='*60}")
    print(f"测试平仓信号 - {symbol}")
    print(f"{'='*60}")

    # 获取当前持仓
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT id, symbol, direction, entry_price, quantity, unrealized_pnl_pct,
                   max_profit_pct, trailing_stop_activated, created_at
            FROM futures_positions
            WHERE symbol = %s AND status = 'open'
            ORDER BY created_at DESC
        """, (symbol,))

        positions = cursor.fetchall()

        if not positions:
            print(f"\n⚠️ 没有 {symbol} 的持仓")

            # 模拟一个持仓来测试
            print(f"\n📋 模拟持仓测试:")
            print("-" * 60)

            # 获取当前价格
            klines = get_klines(symbol, '15m', 5)
            if klines:
                current_price = float(klines[-1]['close_price'])

                # 模拟做多持仓
                entry_price = current_price * 0.98  # 假设入场价低2%
                pnl_pct = (current_price - entry_price) / entry_price * 100

                print(f"模拟做多:")
                print(f"  入场价: {entry_price:.2f}")
                print(f"  当前价: {current_price:.2f}")
                print(f"  浮盈: {pnl_pct:.2f}%")

                # 测试止损
                print(f"\n止损检测 (硬止损 -2.5%):")
                if pnl_pct <= -2.5:
                    print(f"  🔴 触发止损! 浮盈 {pnl_pct:.2f}% <= -2.5%")
                else:
                    print(f"  ✅ 未触发止损 (浮盈 {pnl_pct:.2f}% > -2.5%)")

                # 测试移动止盈
                print(f"\n移动止盈检测:")
                if pnl_pct >= 1.5:
                    print(f"  ✅ 移动止盈已激活 (浮盈 {pnl_pct:.2f}% >= 1.5%)")

                    # 假设最高盈利
                    max_profit = pnl_pct + 0.5  # 假设最高盈利比当前高0.5%
                    drawdown = max_profit - pnl_pct
                    print(f"  最高盈利: {max_profit:.2f}%")
                    print(f"  当前回撤: {drawdown:.2f}%")

                    if drawdown >= 1.0:
                        print(f"  🔴 触发移动止盈! 回撤 {drawdown:.2f}% >= 1%")
                    else:
                        print(f"  ✅ 未触发移动止盈 (回撤 {drawdown:.2f}% < 1%)")
                else:
                    print(f"  ⚠️ 移动止盈未激活 (浮盈 {pnl_pct:.2f}% < 1.5%)")

                # 测试最大止盈
                print(f"\n最大止盈检测 (+8%):")
                if pnl_pct >= 8:
                    print(f"  🟢 触发最大止盈! 浮盈 {pnl_pct:.2f}% >= 8%")
                else:
                    print(f"  ✅ 未触发最大止盈 (浮盈 {pnl_pct:.2f}% < 8%)")

            return

        # 显示实际持仓
        print(f"\n📋 当前持仓:")
        print("-" * 80)

        for pos in positions:
            direction = pos['direction']
            entry_price = float(pos['entry_price'])
            pnl_pct = float(pos['unrealized_pnl_pct']) if pos['unrealized_pnl_pct'] else 0
            max_profit = float(pos['max_profit_pct']) if pos['max_profit_pct'] else 0
            trailing_activated = pos['trailing_stop_activated']

            print(f"\n持仓 #{pos['id']}:")
            print(f"  方向: {'做多 🟢' if direction == 'long' else '做空 🔴'}")
            print(f"  入场价: {entry_price:.4f}")
            print(f"  数量: {pos['quantity']}")
            print(f"  浮盈: {pnl_pct:.2f}%")
            print(f"  最高盈利: {max_profit:.2f}%")
            print(f"  移动止盈激活: {'是' if trailing_activated else '否'}")

            # 检查平仓条件
            print(f"\n  平仓条件检查:")

            # 1. 硬止损
            if pnl_pct <= -2.5:
                print(f"    🔴 [触发] 硬止损: 浮盈 {pnl_pct:.2f}% <= -2.5%")
            else:
                print(f"    ✅ [未触发] 硬止损: 浮盈 {pnl_pct:.2f}% > -2.5%")

            # 2. 移动止盈
            if pnl_pct >= 1.5:
                if not trailing_activated:
                    print(f"    📌 [应激活] 移动止盈: 浮盈 {pnl_pct:.2f}% >= 1.5%")
                else:
                    drawdown = max_profit - pnl_pct
                    if drawdown >= 1.0:
                        print(f"    🟢 [触发] 移动止盈: 从最高点 {max_profit:.2f}% 回撤 {drawdown:.2f}%")
                    else:
                        print(f"    ✅ [未触发] 移动止盈: 回撤 {drawdown:.2f}% < 1%")
            else:
                print(f"    ⚠️ [未激活] 移动止盈: 浮盈 {pnl_pct:.2f}% < 1.5%")

            # 3. 最大止盈
            if pnl_pct >= 8:
                print(f"    🟢 [触发] 最大止盈: 浮盈 {pnl_pct:.2f}% >= 8%")
            else:
                print(f"    ✅ [未触发] 最大止盈: 浮盈 {pnl_pct:.2f}% < 8%")

    finally:
        cursor.close()
        conn.close()


def test_cross_reversal(symbol: str):
    """测试穿越反转平仓信号"""
    print(f"\n{'='*60}")
    print(f"测试穿越反转平仓信号 - {symbol}")
    print(f"{'='*60}")

    # 获取K线
    klines = get_klines(symbol, '15m', 50)
    if len(klines) < 30:
        print(f"❌ K线数据不足")
        return

    closes = [float(k['close_price']) for k in klines]
    ema9 = calculate_ema(closes, 9)
    ema26 = calculate_ema(closes, 26)

    print(f"\n说明: 持有多头时检测死叉，持有空头时检测金叉")
    print("-" * 60)

    # 查找最近的穿越
    cross_found = False
    for i in range(-15, -1):
        if ema9[i-1] and ema26[i-1] and ema9[i] and ema26[i]:
            prev_diff = ema9[i-1] - ema26[i-1]
            curr_diff = ema9[i] - ema26[i]

            timestamp = klines[i]['timestamp']
            if isinstance(timestamp, datetime):
                time_str = timestamp.strftime('%Y-%m-%d %H:%M')
            else:
                time_str = str(timestamp)[:16]

            # 死叉 (多头平仓信号)
            if prev_diff >= 0 and curr_diff < 0:
                print(f"🔴 死叉 @ {time_str}")
                print(f"   └─ 多头持仓应立即平仓 (不检查信号强度)")
                cross_found = True

            # 金叉 (空头平仓信号)
            if prev_diff <= 0 and curr_diff > 0:
                print(f"🟢 金叉 @ {time_str}")
                print(f"   └─ 空头持仓应立即平仓 (不检查信号强度)")
                cross_found = True

    if not cross_found:
        print("⚠️ 最近15根K线内没有检测到穿越信号")

    # 当前状态
    if ema9[-1] and ema26[-1]:
        curr_diff = ema9[-1] - ema26[-1]
        print(f"\n当前状态: {'多头趋势 (EMA9>EMA26)' if curr_diff > 0 else '空头趋势 (EMA9<EMA26)'}")


def main():
    """主函数"""
    print("\n" + "=" * 60)
    print("V2 策略信号测试")
    print("=" * 60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 测试的交易对
    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

    for symbol in symbols:
        try:
            # 测试开仓信号
            test_open_signals(symbol)

            # 测试平仓信号
            test_close_signals(symbol)

            # 测试穿越反转
            test_cross_reversal(symbol)

        except Exception as e:
            print(f"\n❌ 测试 {symbol} 时出错: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == '__main__':
    main()
