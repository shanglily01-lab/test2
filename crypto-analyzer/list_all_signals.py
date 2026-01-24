#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
列出所有开仓信号及其权重
"""

import sys
import io
import pymysql

# 设置标准输出编码为UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def list_all_signals():
    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    print("=" * 120)
    print("超级大脑所有开仓信号配置")
    print("=" * 120)

    cursor.execute("""
        SELECT signal_component, weight_long, weight_short, base_weight,
               performance_score, adjustment_count, description
        FROM signal_scoring_weights
        WHERE is_active = TRUE
        ORDER BY performance_score DESC
    """)

    signals = cursor.fetchall()

    print("\n开仓阈值: 白名单30分 | 黑名单1级35分 | 黑名单2级40分\n")

    # 按性能评分排序显示
    print(f"{'信号名称':<30} {'做多':<8} {'做空':<8} {'基础':<8} {'性能评分':<12} {'调整次数':<10} {'说明':<40}")
    print("-" * 120)

    for s in signals:
        perf = f"{float(s['performance_score']):.2f}" if s['performance_score'] else "N/A"
        adj = s['adjustment_count'] if s['adjustment_count'] else 0
        desc = s['description'] if s['description'] else ""

        long_w = float(s['weight_long'])
        short_w = float(s['weight_short'])
        base_w = float(s['base_weight'])

        # 标记表现好的(绿色)和差的(红色)
        status = ""
        if s['performance_score']:
            if float(s['performance_score']) > 0:
                status = " ✅"
            elif float(s['performance_score']) < -50:
                status = " ❌"

        print(f"{s['signal_component']:<30} {long_w:<8.1f} {short_w:<8.1f} {base_w:<8.1f} {perf:<12} {adj:<10} {desc:<40}{status}")

    # 总结
    print("\n" + "=" * 120)
    print("信号组合逻辑:")
    print("=" * 120)
    print("""
1. 位置评分 (使用72小时高低点):
   - position_low (<30%):  做多加分
   - position_mid (30-70%): 做多做空都加分
   - position_high (>70%):  做空加分 ✅ (唯一表现好的信号!)

2. 短期动量 (最近24小时涨跌幅):
   - momentum_down_3pct (跌>3%): 做多加分
   - momentum_up_3pct (涨>3%):   做空加分

3. 1小时趋势 (最近48根K线,2天):
   - trend_1h_bull (>62.5%阳线): 做多加分
   - trend_1h_bear (>62.5%阴线): 做空加分

4. 1日趋势 (最近30根K线,30天):
   - trend_1d_bull (>60%阳线): 做多加分
   - trend_1d_bear (>60%阴线): 做空加分

5. 连续趋势强化 (最近10根1h K线):
   - consecutive_bull (7+阳线,涨幅<5%): 做多做空都加分
   - consecutive_bear (7+阴线,跌幅<5%): 做多做空都加分

6. 波动率 (最近24小时振幅):
   - volatility_high (>5%): 哪边分高加哪边

最终评分 = 各信号得分相加, ≥30分开仓(白名单)
    """)

    # 问题分析
    print("=" * 120)
    print("潜在问题分析:")
    print("=" * 120)
    print("""
⚠️ 发现的问题:

1. consecutive_bull/bear 逻辑矛盾:
   - "连续看涨后做多做空都加分" 这个逻辑很奇怪
   - 应该是: 连续看涨后回调时做多,或突破后做空
   - 性能评分: -166.26 (最差!) ❌

2. 趋势跟随效果差:
   - trend_1d_bull/bear 都是负评分
   - 可能是因为趋势已经走完,进场太晚

3. 位置评分过于简单:
   - 只看72小时高低点,没考虑更长期的支撑阻力
   - 但 position_high(高位做空) 是唯一赚钱的信号 ✅

4. 缺少关键信号:
   - 没有成交量分析
   - 没有RSI/MACD等技术指标
   - 没有资金流向分析
   - 没有突破/假突破判断

5. 信号权重已被大幅下调:
   - 大部分从15-20分降到5分
   - 说明自适应系统已经识别出问题
   - 但无法完全修复根本缺陷
    """)

    cursor.close()
    conn.close()

    print("=" * 120)

if __name__ == '__main__':
    list_all_signals()
