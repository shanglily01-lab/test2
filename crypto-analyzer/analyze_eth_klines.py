#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析ETH的K线,理解为什么被拒绝做空
"""

import ccxt
from datetime import datetime, timedelta
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def analyze_eth_klines():
    exchange = ccxt.binance()

    print("=" * 80)
    print("分析ETH/USDT的K线 - 2026-01-27 02:04时刻")
    print("=" * 80)

    # 获取1小时K线(最近48根,即2天)
    klines_1h = exchange.fetch_ohlcv('ETH/USDT', '1h', limit=48)

    # 获取15分钟K线(最近96根,即1天)
    klines_15m = exchange.fetch_ohlcv('ETH/USDT', '15m', limit=96)

    print("\n📊 1小时K线分析 (最近48根):")
    print("=" * 80)

    # 统计多空比
    bullish_1h = sum(1 for k in klines_1h if k[4] > k[1])  # close > open
    bearish_1h = len(klines_1h) - bullish_1h

    print(f"阳线(多头): {bullish_1h} 根 ({bullish_1h/len(klines_1h)*100:.1f}%)")
    print(f"阴线(空头): {bearish_1h} 根 ({bearish_1h/len(klines_1h)*100:.1f}%)")

    # 最近24小时的涨跌幅
    price_24h_ago = klines_1h[-24][4]
    current_price = klines_1h[-1][4]
    gain_24h = (current_price - price_24h_ago) / price_24h_ago * 100

    print(f"\n24小时前价格: ${price_24h_ago:.2f}")
    print(f"当前价格: ${current_price:.2f}")
    print(f"24小时涨幅: {gain_24h:.2f}%")

    if gain_24h > 3:
        print(f"  → 触发 momentum_up_3pct ✓")

    print("\n📊 15分钟K线分析 (最近96根):")
    print("=" * 80)

    bullish_15m = sum(1 for k in klines_15m if k[4] > k[1])
    bearish_15m = len(klines_15m) - bullish_15m

    print(f"阳线(多头): {bullish_15m} 根 ({bullish_15m/len(klines_15m)*100:.1f}%)")
    print(f"阴线(空头): {bearish_15m} 根 ({bearish_15m/len(klines_15m)*100:.1f}%)")

    # 最近12根15分钟K线 (3小时)
    recent_15m_12 = klines_15m[-12:]
    bullish_recent = sum(1 for k in recent_15m_12 if k[4] > k[1])

    print(f"\n最近3小时(12根15分钟K线):")
    print(f"阳线: {bullish_recent}/12 ({bullish_recent/12*100:.1f}%)")

    print("\n" + "=" * 80)
    print("🔍 详细K线走势 (最近24根1小时K线):")
    print("=" * 80)

    print(f"\n{'时间':<20} {'开盘':<10} {'收盘':<10} {'涨跌':<8} {'类型':<6}")
    print("-" * 80)

    for i, k in enumerate(klines_1h[-24:]):
        timestamp = datetime.fromtimestamp(k[0] / 1000)
        open_price = k[1]
        close_price = k[4]
        change = (close_price - open_price) / open_price * 100
        candle_type = "🟢阳线" if close_price > open_price else "🔴阴线"

        print(f"{timestamp.strftime('%m-%d %H:%M'):<20} ${open_price:<9.2f} ${close_price:<9.2f} {change:>6.2f}% {candle_type}")

    print("\n" + "=" * 80)
    print("💡 分析结论:")
    print("=" * 80)

    # 判断趋势
    if bullish_1h > 30:
        print(f"✅ 1小时多头强势: {bullish_1h}/48 ({bullish_1h/48*100:.1f}%) 阳线")
    else:
        print(f"⚠️ 1小时多空均衡: {bullish_1h}/48 ({bullish_1h/48*100:.1f}%) 阳线")

    if bullish_15m > 60:
        print(f"✅ 15分钟多头强势: {bullish_15m}/96 ({bullish_15m/96*100:.1f}%) 阳线")
    else:
        print(f"⚠️ 15分钟多空均衡: {bullish_15m}/96 ({bullish_15m/96*100:.1f}%) 阳线")

    print(f"\n当前策略判断:")
    print(f"  - position_high (价格在高位) → 想做空")
    print(f"  - momentum_up_3pct (24H涨{gain_24h:.2f}%) → 不能做空")
    print(f"  - 1H多头比例 {bullish_1h/48*100:.1f}% → 应该做多!")
    print(f"  - 15M多头比例 {bullish_15m/96*100:.1f}% → 应该做多!")

    print("\n" + "=" * 80)
    print("🎯 问题所在:")
    print("=" * 80)
    print("""
超级大脑的逻辑缺陷:
1. 只看 position_high/low (价格位置)
2. 看了 momentum_up_3pct (24H涨幅)
3. 但完全忽略了 K线多空比!

K线多空比才是最重要的趋势指标:
- 如果1H阳线 > 62.5% (30/48) → 强势多头,应该做多
- 如果15M阳线 > 60% → 短期强势,应该做多
- 即使价格在高位,强势突破时也应该追多

你说得对:
"3%涨幅其实已经是从底部上来的,已经算很小了"
关键不是涨了多少,而是K线多空比显示的趋势方向!

修复方案:
添加K线多空比作为做多信号:
- 如果 1H阳线 > 60% + 15M阳线 > 60% → 强势做多信号
- 不要只依赖 position_low,突破时也要追多
""")

if __name__ == "__main__":
    analyze_eth_klines()
