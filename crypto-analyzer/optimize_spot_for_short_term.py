#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易短线优化 - 基于24H信号做短线
"""
import pymysql
import os
from dotenv import load_dotenv

load_dotenv()

conn = pymysql.connect(
    host=os.getenv('DB_HOST', '13.212.252.171'),
    port=int(os.getenv('DB_PORT', '3306')),
    user=os.getenv('DB_USER', 'admin'),
    password=os.getenv('DB_PASSWORD', 'Tonny@1000'),
    database='binance-data',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = conn.cursor()

print("=" * 100)
print("现货短线机会分析 - 基于24H数据")
print("=" * 100)
print()

# 查询24H强势做多信号
cursor.execute("""
    SELECT
        symbol,
        current_price,
        change_24h as price_change_pct_24h,
        volume_24h,
        high_24h,
        low_24h,
        trades_count_24h as trades_24h,
        quote_volume_24h,
        trend
    FROM price_stats_24h
    WHERE change_24h > 3.0  -- 24H涨幅 > 3%
      AND quote_volume_24h > 5000000  -- 成交额 > 500万 (流动性好)
      AND trend IN ('STRONG_UP', 'UP')  -- 趋势向上
    ORDER BY change_24h DESC
    LIMIT 30
""")

strong_signals = cursor.fetchall()

print(f"[+] 发现 {len(strong_signals)} 个强势做多信号 (24H涨幅>3% + 趋势向上)")
print()
print("=" * 100)
print(f"{'交易对':15} {'24H涨幅':>10} {'趋势':>12} {'成交额(M)':>12} {'当前价格':>12}")
print("=" * 100)

for sig in strong_signals[:15]:
    volume_m = float(sig['quote_volume_24h']) / 1_000_000
    print(f"{sig['symbol']:15} {sig['price_change_pct_24h']:>9.2f}% {sig['trend']:>12} "
          f"{volume_m:>11.1f}M ${sig['current_price']:>11.4f}")

print()
print("=" * 100)
print("短线交易建议:")
print("=" * 100)
print()

# 分级建议
tier_a = [s for s in strong_signals if s['price_change_pct_24h'] >= 8 and s['trend'] == 'STRONG_UP']
tier_b = [s for s in strong_signals if 5 <= s['price_change_pct_24h'] < 8]
tier_c = [s for s in strong_signals if 3 <= s['price_change_pct_24h'] < 5]

print(f"[A] A级机会 ({len(tier_a)}个): 涨幅>=8% + 强势上涨 (强势突破,可追涨)")
for sig in tier_a:
    volume_m = float(sig['quote_volume_24h']) / 1_000_000
    print(f"   {sig['symbol']:12} +{sig['price_change_pct_24h']:.1f}% {sig['trend']:12} 成交{volume_m:.0f}M")

print()
print(f"[B] B级机会 ({len(tier_b)}个): 涨幅5-8% (稳健上涨,主要目标)")
for sig in tier_b:
    volume_m = float(sig['quote_volume_24h']) / 1_000_000
    print(f"   {sig['symbol']:12} +{sig['price_change_pct_24h']:.1f}% {sig['trend']:12} 成交{volume_m:.0f}M")

print()
print(f"[C] C级机会 ({len(tier_c)}个): 涨幅3-5% (温和上涨,可观察)")
for sig in tier_c[:5]:
    volume_m = float(sig['quote_volume_24h']) / 1_000_000
    print(f"   {sig['symbol']:12} +{sig['price_change_pct_24h']:.1f}% 成交{volume_m:.0f}M")

print()
print("=" * 100)
print("短线参数建议:")
print("=" * 100)
print()
print("入场阈值: 40分 (降低至40分,捕捉更多机会)")
print("止盈目标: 15-20% (短线快进快出)")
print("止损保护: 5-7% (严格控制风险)")
print("持仓周期: 1-3天 (不追求长期持有)")
print("最大持仓: 10个 (分散风险)")
print("单币资金: 5,000 USDT (灵活配置)")
print()

# 查询K线数据验证短期趋势
print("=" * 100)
print("K线验证 (最近1小时走势):")
print("=" * 100)
print()

for sig in tier_a[:5]:  # 只验证A级前5个
    symbol = sig['symbol'].replace('/', '')

    cursor.execute("""
        SELECT open_price, close_price, high_price, low_price, volume
        FROM kline_data
        WHERE symbol = %s AND timeframe = '5m'
        ORDER BY open_time DESC
        LIMIT 12
    """, (symbol,))

    klines = cursor.fetchall()

    if klines:
        # 计算最近1小时趋势
        recent_change = ((float(klines[0]['close_price']) - float(klines[-1]['open_price']))
                        / float(klines[-1]['open_price']) * 100)
        avg_volume = sum([float(k['volume']) for k in klines]) / len(klines)

        print(f"{sig['symbol']:12} 1H变化: {recent_change:+.2f}% (验证短期趋势)")

print()

cursor.close()
conn.close()

print("=" * 100)
print("优化建议:")
print("=" * 100)
print("""
1. 修改 spot_trader_service.py:
   - 入场阈值: 60 → 40分
   - 止盈: 50% → 18%
   - 止损: 10% → 6%
   - 最大持仓: 5 → 10个

2. 集成24H信号:
   - 优先买入24H涨幅3-8%的币种
   - 要求成交量放大>20%
   - 成交额>500万(流动性)

3. 短线策略:
   - 快进快出,持仓1-3天
   - 达到15-20%止盈就获利了结
   - 严格执行6%止损

4. 加仓策略优化:
   - 第1批: 20% (加大初始仓位)
   - 第2批: 30% (主力仓位)
   - 第3批: 50% (确认趋势后加仓)
   - 取消4-5批(短线不需要过多批次)
""")
