#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析SOMI/USDT追高被套案例
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
print("SOMI/USDT 追高被套案例分析")
print("=" * 100)
print()

# 查询SOMI的24H数据
cursor.execute("""
    SELECT symbol, change_24h, trend, quote_volume_24h,
           current_price, high_24h, low_24h
    FROM price_stats_24h
    WHERE symbol = 'SOMI/USDT'
""")

somi = cursor.fetchone()

if somi:
    print("SOMI/USDT 24H数据:")
    print(f"  当前价: ${float(somi['current_price']):.6f}")
    print(f"  24H涨跌: {float(somi['change_24h']):+.2f}%")
    print(f"  24H最高: ${float(somi['high_24h']):.6f}")
    print(f"  24H最低: ${float(somi['low_24h']):.6f}")
    print(f"  趋势: {somi['trend']}")
    print(f"  成交额: ${float(somi['quote_volume_24h'])/1000000:.2f}M")

    # 分析开仓价位置
    high = float(somi['high_24h'])
    low = float(somi['low_24h'])
    entry = 0.3129

    position_pct = (entry - low) / (high - low) * 100
    print()
    print("开仓位置分析:")
    print(f"  开仓价 $0.3129 在24H区间的 {position_pct:.1f}% 位置")

    if position_pct > 90:
        print(f"  ❌ 严重追高! 几乎在24H最高点开仓")
    elif position_pct > 80:
        print(f"  ⚠️ 追高! 在24H高位区域开仓")
    elif position_pct > 60:
        print(f"  ⚠️ 偏高位置开仓")
    else:
        print(f"  ✓ 相对合理的位置")

    print()
    print("=" * 100)
    print("问题诊断:")
    print("=" * 100)
    print()

    # 问题1: 追高
    print("1. 追高入场")
    print(f"   开仓价$0.3129距离24H最高${high:.6f}仅{(high-entry)/high*100:.2f}%")
    print(f"   开仓后无上涨空间,立即下跌")
    print()

    # 问题2: 止损过近
    print("2. 止损设置")
    print(f"   止损价: $0.301249 (-3.8%)")
    print(f"   5x杠杆下: -3.8% × 5 = -19% ROI")
    print(f"   实际触发: -3.02%, ROI -15.12%")
    print(f"   ⚠️ 对于波动较大的币种,止损过近容易被扫")
    print()

    # 问题3: 持仓时间
    print("3. 持仓时长")
    print(f"   仅19分钟就止损")
    print(f"   说明入场时机极差,价格立即下跌")
    print()

    # 问题4: 信号评分
    print("4. 信号质量")
    print(f"   入场评分: $0.94 (极低!)")
    print(f"   ⚠️ 评分不足1分的信号不应该交易")
    print()

else:
    print("未找到SOMI/USDT数据")

print("=" * 100)
print("优化建议:")
print("=" * 100)
print()

print("1. 禁止追高")
print("   - 开仓价不得高于24H区间的70%位置")
print("   - 即: price <= low + (high - low) × 0.7")
print()

print("2. 信号过滤")
print("   - 最低入场评分: 40分")
print("   - 当前$0.94评分的信号应该直接拒绝")
print()

print("3. 止损优化")
print("   - 对于波动大的币种,止损可以放宽到-5%")
print("   - 5x杠杆下: -5% × 5 = -25% ROI")
print()

print("4. 位置检查")
print("   - 在开仓前检查24H价格区间位置")
print("   - 高于80%位置拒绝做多")
print()

cursor.close()
conn.close()
