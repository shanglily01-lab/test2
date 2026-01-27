#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析ETH的K线 + 成交量,找出有力量的阳线和阴线
"""

import ccxt
from datetime import datetime
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def analyze_eth_volume():
    exchange = ccxt.binance()

    print("=" * 80)
    print("分析ETH/USDT的K线 + 成交量 (有力量的K线)")
    print("=" * 80)

    # 获取1小时K线
    klines_1h = exchange.fetch_ohlcv('ETH/USDT', '1h', limit=48)

    # 获取15分钟K线
    klines_15m = exchange.fetch_ohlcv('ETH/USDT', '15m', limit=96)

    print("\n📊 1小时K线分析 (最近24根):")
    print("=" * 80)

    # 计算平均成交量
    volumes_1h = [k[5] for k in klines_1h[-24:]]
    avg_volume_1h = sum(volumes_1h) / len(volumes_1h)

    print(f"平均成交量: {avg_volume_1h:.0f} ETH")
    print(f"高量标准: {avg_volume_1h * 1.2:.0f} ETH (120%平均量)")

    print(f"\n{'时间':<20} {'开盘':<10} {'收盘':<10} {'涨跌':<8} {'成交量':<12} {'类型':<15} {'力量':<10}")
    print("-" * 120)

    strong_bull_count = 0  # 有力量的阳线
    weak_bull_count = 0    # 无力量的阳线
    strong_bear_count = 0  # 有力量的阴线
    weak_bear_count = 0    # 无力量的阴线

    for k in klines_1h[-24:]:
        timestamp = datetime.fromtimestamp(k[0] / 1000)
        open_price = k[1]
        close_price = k[4]
        volume = k[5]
        change = (close_price - open_price) / open_price * 100

        is_bull = close_price > open_price
        is_high_volume = volume > avg_volume_1h * 1.2

        if is_bull:
            if is_high_volume:
                candle_type = "🟢阳线"
                power = "💪强力"
                strong_bull_count += 1
            else:
                candle_type = "🟢阳线"
                power = "😐弱势"
                weak_bull_count += 1
        else:
            if is_high_volume:
                candle_type = "🔴阴线"
                power = "💪强力"
                strong_bear_count += 1
            else:
                candle_type = "🔴阴线"
                power = "😐弱势"
                weak_bear_count += 1

        volume_ratio = volume / avg_volume_1h
        volume_str = f"{volume:.0f} ({volume_ratio:.1f}x)"

        print(f"{timestamp.strftime('%m-%d %H:%M'):<20} ${open_price:<9.2f} ${close_price:<9.2f} "
              f"{change:>6.2f}% {volume_str:<12} {candle_type:<15} {power}")

    print("\n" + "=" * 80)
    print("📊 统计结果 (1小时K线):")
    print("=" * 80)
    print(f"💪 强力阳线: {strong_bull_count} 根 (大量+上涨)")
    print(f"😐 弱势阳线: {weak_bull_count} 根 (小量+上涨)")
    print(f"💪 强力阴线: {strong_bear_count} 根 (大量+下跌)")
    print(f"😐 弱势阴线: {weak_bear_count} 根 (小量+下跌)")

    net_power = strong_bull_count - strong_bear_count
    print(f"\n净力量: {net_power:+d} (强力阳线 - 强力阴线)")

    if net_power > 2:
        print(f"  → ✅ 多头力量强 (应该做多)")
    elif net_power < -2:
        print(f"  → ❌ 空头力量强 (应该做空)")
    else:
        print(f"  → ⚠️ 多空力量均衡")

    # 15分钟K线分析
    print("\n" + "=" * 80)
    print("📊 15分钟K线分析 (最近24根):")
    print("=" * 80)

    volumes_15m = [k[5] for k in klines_15m[-24:]]
    avg_volume_15m = sum(volumes_15m) / len(volumes_15m)

    print(f"平均成交量: {avg_volume_15m:.0f} ETH")

    strong_bull_15m = 0
    strong_bear_15m = 0

    print(f"\n{'时间':<20} {'涨跌':<8} {'成交量':<12} {'类型':<15} {'力量':<10}")
    print("-" * 80)

    for k in klines_15m[-24:]:
        timestamp = datetime.fromtimestamp(k[0] / 1000)
        open_price = k[1]
        close_price = k[4]
        volume = k[5]
        change = (close_price - open_price) / open_price * 100

        is_bull = close_price > open_price
        is_high_volume = volume > avg_volume_15m * 1.2

        if is_bull and is_high_volume:
            candle_type = "🟢阳线"
            power = "💪强力"
            strong_bull_15m += 1
        elif not is_bull and is_high_volume:
            candle_type = "🔴阴线"
            power = "💪强力"
            strong_bear_15m += 1
        else:
            candle_type = "🟢阳线" if is_bull else "🔴阴线"
            power = "😐弱势"

        volume_ratio = volume / avg_volume_15m
        volume_str = f"{volume:.0f} ({volume_ratio:.1f}x)"

        if is_high_volume:  # 只显示大量K线
            print(f"{timestamp.strftime('%m-%d %H:%M'):<20} {change:>6.2f}% "
                  f"{volume_str:<12} {candle_type:<15} {power}")

    net_power_15m = strong_bull_15m - strong_bear_15m
    print(f"\n15分钟净力量: {net_power_15m:+d} (强力阳线 - 强力阴线)")

    print("\n" + "=" * 80)
    print("💡 综合判断:")
    print("=" * 80)

    print(f"""
1小时K线:
  强力阳线: {strong_bull_count} 根
  强力阴线: {strong_bear_count} 根
  净力量: {net_power:+d}

15分钟K线:
  强力阳线: {strong_bull_15m} 根
  强力阴线: {strong_bear_15m} 根
  净力量: {net_power_15m:+d}

交易信号:
""")

    if net_power > 2 and net_power_15m > 2:
        print("  ✅ 强烈做多信号 (1H和15M都是强力多头)")
    elif net_power > 0 and net_power_15m > 0:
        print("  ✅ 做多信号 (多头有优势)")
    elif net_power < -2 and net_power_15m < -2:
        print("  ❌ 强烈做空信号 (1H和15M都是强力空头)")
    elif net_power < 0 and net_power_15m < 0:
        print("  ❌ 做空信号 (空头有优势)")
    else:
        print("  ⚠️ 观望 (多空力量均衡)")

    print("\n" + "=" * 80)
    print("🔧 超级大脑应该这样判断:")
    print("=" * 80)
    print("""
不要只看阳线/阴线数量,要看"有力量的K线":

1. 强力阳线 = 阳线 + 成交量 > 1.2倍平均量
   → 多头在用真金白银推高价格

2. 强力阴线 = 阴线 + 成交量 > 1.2倍平均量
   → 空头在用真金白银打压价格

3. 弱势K线 = 成交量小,无论涨跌都没意义
   → 散户在玩,大资金没进场

做多条件:
  强力阳线 - 强力阴线 > 2 (1H)
  AND
  强力阳线 - 强力阴线 > 2 (15M)
  → 多头有真实力量,可以做多

做空条件:
  强力阴线 - 强力阳线 > 2 (1H)
  AND
  强力阴线 - 强力阳线 > 2 (15M)
  → 空头有真实力量,可以做空
""")

if __name__ == "__main__":
    analyze_eth_volume()
