#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查当前Big4评分（MA8/MA20 + 4H动量版本）"""
import sys
import os
from dotenv import load_dotenv

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.dirname(__file__))
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

print(f'整体信号: {result["overall_signal"]}')
print(f'信号强度: {result["signal_strength"]:.0f}')
print(f'多头权重: {result["bullish_weight"]*100:.0f}%')
print(f'空头权重: {result["bearish_weight"]*100:.0f}%')
nws = result.get('net_weighted_score', 'N/A')
nb  = result.get('neutral_bias', 'N/A')
print(f'净加权分: {nws}  neutral_bias: {nb}')
print(f'建议: {result["recommendation"]}')
print()
print('数据来源: 数据库 (binance_futures合约数据)')
print('评分规则: MA8/MA20偏差(最大±40) + 4H动量(最大±30)，总分>=50触发信号')
print()

for symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
    detail = result['details'][symbol]
    print(f'{symbol}:')
    print(f'  信号: {detail["signal"]} (强度: {detail["strength"]:.0f}, raw_score: {detail.get("raw_score", "?")})')

    ma = detail.get('ma', {})
    if ma:
        print(f'  MA趋势: MA8={ma.get("ma8", 0):.2f} MA20={ma.get("ma20", 0):.2f} '
              f'偏差={ma.get("deviation", 0):+.2f}%  => {ma.get("score", 0):+d}分 ({ma.get("level", "?")})')

    mom = detail.get('momentum_4h', {})
    if mom:
        print(f'  4H动量: 变化={mom.get("change", 0):+.2f}%  => {mom.get("score", 0):+d}分 ({mom.get("level", "?")})')

    reason = detail.get('reason', '').replace('✅', '[OK]').replace('⚠️', '[WARN]')
    print(f'  原因: {reason}')
    print()

print('=' * 100)
