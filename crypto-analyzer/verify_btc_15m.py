#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证BTC 15M K线阴阳线统计"""
import sys
import os

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

from app.services.big4_trend_detector import Big4TrendDetector
from datetime import datetime
import ccxt

# 初始化交易所
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# 获取最近16根15M K线
symbol = 'BTC/USDT'
ohlcv = exchange.fetch_ohlcv(symbol, '15m', limit=16)

print('=' * 100)
print(f'BTC/USDT 最近16根15M K线验证')
print('=' * 100)
print()

bullish = 0
bearish = 0

for i, candle in enumerate(ohlcv, 1):
    timestamp, open_price, high, low, close, volume = candle
    dt = datetime.fromtimestamp(timestamp / 1000)

    # 判断阴阳线
    is_bullish = close > open_price
    candle_type = '阳线' if is_bullish else '阴线'

    if is_bullish:
        bullish += 1
    else:
        bearish += 1

    # 计算涨跌幅
    change_pct = ((close - open_price) / open_price) * 100

    print(f'{i:2d}. {dt.strftime("%m-%d %H:%M")} | '
          f'O:{open_price:>10.2f} H:{high:>10.2f} L:{low:>10.2f} C:{close:>10.2f} | '
          f'{candle_type} {change_pct:+.2f}%')

print()
print('=' * 100)
print(f'统计结果: {bullish}阳 {bearish}阴')
print('=' * 100)

# 同时检查Big4检测器的结果
print()
print('Big4检测器的统计结果:')
detector = Big4TrendDetector()

# 获取15M分析
from app.services.signal_analysis_service import SignalAnalysisService
import os
from dotenv import load_dotenv

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME')
}

analyzer = SignalAnalysisService(db_config)
kline_15m = analyzer.analyze_kline_strength('BTC/USDT', '15m', 4)  # 最近4小时，即16根15M

if kline_15m:
    print(f"Big4检测器统计: {kline_15m.get('bull', 0)}阳 {kline_15m.get('bear', 0)}阴")
    print(f"详细数据: {kline_15m}")
