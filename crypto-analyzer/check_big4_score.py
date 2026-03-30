#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检查当前Big4评分（15M K线阴阳线计数版本）"""
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
print(f'多头权重: {result["bullish_weight"]*100:.0f}%  (含强多头: {result.get("strong_bullish_weight", 0)*100:.0f}%)')
print(f'空头权重: {result["bearish_weight"]*100:.0f}%  (含强空头: {result.get("strong_bearish_weight", 0)*100:.0f}%)')
print(f'建议: {result["recommendation"]}')
print()
print('数据来源: 数据库 (binance_futures合约数据)')
print('评分规则: 最近16根15M K线')
print('  阳线>=11 且涨幅>=0.5% -> STRONG_BULLISH')
print('  阳线9~10 且涨幅>=0.3% -> BULLISH')
print('  阴线9~10 且跌幅>=0.3% -> BEARISH')
print('  阴线>=11 且跌幅>=0.5% -> STRONG_BEARISH')
print('触发条件 (BTC50%+ETH30%+BNB10%+SOL10%):')
print('  strong_bullish_weight>60% -> STRONG_BULLISH')
print('  bullish_weight>60%        -> BULLISH')
print('  bearish_weight>60%        -> BEARISH')
print('  strong_bearish_weight>60% -> STRONG_BEARISH')
print()

for symbol in ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']:
    detail = result['details'][symbol]
    print(f'{symbol}:')
    print(f'  信号: {detail["signal"]} (强度: {detail["strength"]:.0f}, raw_score: {detail.get("raw_score", "?")})')
    reason = detail.get('reason', '')
    print(f'  原因: {reason}')
    print()

print('=' * 100)
