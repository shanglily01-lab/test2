#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查当前Big4评分（实时K线版本）"""
import sys
import os
from dotenv import load_dotenv

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

# 加载环境变量
load_dotenv()

from app.services.big4_trend_detector import Big4TrendDetector
from datetime import datetime

print('正在从数据库获取K线数据...')
detector = Big4TrendDetector()
result = detector.detect_market_trend()

print('=' * 100)
print(f'Big4当前评分 - {datetime.now().strftime("%Y-%m-%d %H:%M")}')
print('=' * 100)
print()

# Big4整体
print(f'整体信号: {result["overall_signal"]}')
print(f'信号强度: {result["signal_strength"]:.0f}')
print(f'多头权重: {result["bullish_weight"]*100:.0f}%')
print(f'空头权重: {result["bearish_weight"]*100:.0f}%')
print(f'建议: {result["recommendation"]}')
print()
print('数据来源: 数据库 (binance_futures合约数据, 延迟5-15分钟)')
print('判断逻辑: BTC必须配合ETH/BNB/SOL任一同向 + 权重>=50% 才触发信号')
print('评分规则: 1H(强40/中30) + 15M(强30/中20) + 5M反向(3根10/2根5) >= 50分')
print('5M反向: 多头趋势+5M阴线回调加分 / 空头趋势+5M阳线反弹加分')
print()

# 各币种详情
for symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
    detail = result['details'][symbol]
    print(f'{symbol}:')
    print(f'  信号: {detail["signal"]} (强度: {detail["strength"]:.0f})')

    # 1H
    h1 = detail['1h_analysis']
    print(f'  1H: {h1["bullish_count"]}阳 {h1["bearish_count"]}阴 = {h1["score"]:+d}分 ({h1["level"]})')

    # 15M
    m15 = detail['15m_analysis']
    print(f'  15M: {m15["bullish_count"]}阳 {m15["bearish_count"]}阴 = {m15["score"]:+d}分 ({m15["level"]})')

    # 5M
    m5 = detail.get('5m_analysis', {})
    if m5:
        print(f'  5M: {m5["bullish_count"]}阳 {m5["bearish_count"]}阴 = {m5["score"]:+d}分 ({m5["level"]})')

        # 判断是否为反向评分
        main_trend = h1["score"] + m15["score"]
        if main_trend > 0 and m5["score"] < 0:
            print(f'      -> 反向回调! 多头趋势+阴线回调，加分{abs(m5["score"])}分')
        elif main_trend < 0 and m5["score"] > 0:
            print(f'      -> 反向反弹! 空头趋势+阳线反弹，加分{abs(m5["score"])}分')
        elif m5["score"] != 0:
            print(f'      -> 同向K线，无加分')

    # 总分说明（1H + 15M + 5M反向）
    h1_score = h1["score"]
    m15_score = m15["score"]
    main_trend_score = h1_score + m15_score

    # 计算5M反向加分
    reverse_bonus = 0
    if m5:
        m5_score = m5["score"]
        if main_trend_score > 0 and m5_score < 0:
            reverse_bonus = abs(m5_score)
        elif main_trend_score < 0 and m5_score > 0:
            reverse_bonus = abs(m5_score)

    if reverse_bonus > 0:
        if main_trend_score > 0:
            final_score = main_trend_score + reverse_bonus
            print(f'  总分: {h1_score} + {m15_score} + {reverse_bonus}(5M反向) = {final_score}分')
        else:
            final_score = main_trend_score - reverse_bonus
            print(f'  总分: {h1_score} + {m15_score} + {reverse_bonus}(5M反向) = {final_score}分')
    else:
        print(f'  总分: {h1_score} + {m15_score} = {main_trend_score}分')

    # 原因（简化版，避免特殊字符）
    reason = detail['reason'].replace('✅', '[OK]').replace('⚠️', '[WARN]')
    print(f'  原因: {reason}')
    print()

print('=' * 100)
