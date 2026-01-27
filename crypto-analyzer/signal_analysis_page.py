#!/usr/bin/env python3
"""
信号分析页面 - 24H K线强度 + 信号捕捉分析
"""
import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from dotenv import load_dotenv
load_dotenv()

import pymysql
from datetime import datetime, timedelta
import yaml
from collections import defaultdict

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 加载交易对列表
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)
symbols = config.get('symbols', [])

conn = pymysql.connect(**db_config)
cursor = conn.cursor()

now = datetime.now()
past_24h = now - timedelta(hours=24)

print('=' * 120)
print(f'信号分析页面 - 24H K线强度 + 信号捕捉分析')
print('=' * 120)

def analyze_kline_strength(symbol, timeframe, hours=24):
    """分析K线强度"""
    cursor.execute('''
        SELECT
            timestamp,
            open_price,
            close_price,
            volume,
            high_price,
            low_price
        FROM kline_data
        WHERE symbol = %s
        AND timeframe = %s
        AND timestamp >= DATE_SUB(NOW(), INTERVAL %s HOUR)
        ORDER BY timestamp DESC
    ''', (symbol, timeframe, hours))

    klines = cursor.fetchall()

    if not klines:
        return None

    total_klines = len(klines)
    bull_klines = sum(1 for k in klines if float(k['close_price']) > float(k['open_price']))
    bear_klines = total_klines - bull_klines

    # 计算平均成交量
    volumes = [float(k['volume']) for k in klines]
    avg_volume = sum(volumes) / len(volumes) if volumes else 0

    # 强力K线（成交量>1.2倍均量）
    strong_bull = 0
    strong_bear = 0

    for k in klines:
        is_bull = float(k['close_price']) > float(k['open_price'])
        is_high_volume = float(k['volume']) > avg_volume * 1.2

        if is_bull and is_high_volume:
            strong_bull += 1
        elif not is_bull and is_high_volume:
            strong_bear += 1

    return {
        'total': total_klines,
        'bull': bull_klines,
        'bear': bear_klines,
        'bull_pct': (bull_klines / total_klines * 100) if total_klines > 0 else 0,
        'strong_bull': strong_bull,
        'strong_bear': strong_bear,
        'net_power': strong_bull - strong_bear,
        'avg_volume': avg_volume
    }

def check_signal_status(symbol):
    """检查信号状态"""
    # 查询24H内是否有开仓
    cursor.execute('''
        SELECT
            id,
            position_side,
            entry_signal_type,
            open_time,
            status
        FROM futures_positions
        WHERE symbol = %s
        AND open_time >= %s
        ORDER BY open_time DESC
        LIMIT 1
    ''', (symbol, past_24h))

    position = cursor.fetchone()

    # 查询是否有被拒绝的信号（从日志中无法查询，这里简化处理）
    # 实际应该从信号表或日志中查询

    return {
        'has_position': position is not None,
        'position': position
    }

# 分析所有交易对
print(f'\n分析 {len(symbols)} 个交易对的24H信号情况...\n')

results = []

for symbol in symbols:  # 分析所有交易对
    # K线强度分析
    strength_5m = analyze_kline_strength(symbol, '5m', 24)
    strength_15m = analyze_kline_strength(symbol, '15m', 24)
    strength_1h = analyze_kline_strength(symbol, '1h', 24)

    if not all([strength_5m, strength_15m, strength_1h]):
        continue

    # 信号状态
    signal_status = check_signal_status(symbol)

    results.append({
        'symbol': symbol,
        'strength_5m': strength_5m,
        'strength_15m': strength_15m,
        'strength_1h': strength_1h,
        'signal_status': signal_status
    })

# 按净力量排序
results.sort(key=lambda x: abs(x['strength_1h']['net_power']), reverse=True)

print('【K线强度 + 信号分析】')
print('=' * 120)

for r in results[:30]:  # 显示前30个
    s = r['symbol']
    s5m = r['strength_5m']
    s1h = r['strength_1h']
    sig = r['signal_status']

    # 判断多空倾向
    if s1h['net_power'] >= 3:
        trend = '强多'
        suggest_side = 'LONG'
    elif s1h['net_power'] <= -3:
        trend = '强空'
        suggest_side = 'SHORT'
    elif s1h['bull_pct'] > 55:
        trend = '偏多'
        suggest_side = 'LONG'
    elif s1h['bull_pct'] < 45:
        trend = '偏空'
        suggest_side = 'SHORT'
    else:
        trend = '震荡'
        suggest_side = None

    # 判断是否应该开仓
    should_trade = False
    reason = ''

    if abs(s1h['net_power']) >= 3:
        should_trade = True
        reason = f'1H净力量{s1h["net_power"]:+d}，{trend}明显'
    elif s1h['bull_pct'] > 60 or s1h['bull_pct'] < 40:
        should_trade = True
        reason = f'1H阳线{s1h["bull_pct"]:.0f}%，{trend}明显'

    # 检查是否已开仓
    has_pos = sig['has_position']
    pos_side = sig['position']['position_side'] if has_pos else None

    # 判断是否正确捕捉
    if has_pos and pos_side == suggest_side:
        status = '✓已捕捉'
    elif has_pos and pos_side != suggest_side:
        status = '⚠️方向错误'
    elif not has_pos and should_trade:
        status = '✗错过机会'
    else:
        status = '-无需交易'

    print(f'\n{s:15s} | {trend:4s} | {status}')
    print(f'  1H: 阳线{s1h["bull_pct"]:4.0f}% ({s1h["bull"]}/{s1h["total"]}) | '
          f'强阳{s1h["strong_bull"]} 强阴{s1h["strong_bear"]} | 净力量{s1h["net_power"]:+d}')
    print(f'  5M: 阳线{s5m["bull_pct"]:4.0f}% ({s5m["bull"]}/{s5m["total"]}) | '
          f'强阳{s5m["strong_bull"]} 强阴{s5m["strong_bear"]} | 净力量{s5m["net_power"]:+d}')

    if should_trade and not has_pos:
        print(f'  💡建议: 应该开{suggest_side}仓 - {reason}')
    elif has_pos:
        pos = sig['position']
        print(f'  ✓ 已开仓: {pos["position_side"]} | 信号:{pos["entry_signal_type"][:40]}')
    elif not should_trade:
        print(f'  - 无明显趋势，正确观望')

print('\n' + '=' * 120)

# 详细分析错过的机会
print(f'\n【错过机会详细分析】')
print('=' * 120)

missed_opportunities = []
for r in results:
    s = r['symbol']
    s1h = r['strength_1h']
    s5m = r['strength_5m']
    sig = r['signal_status']

    # 判断是否应该交易
    should_trade = abs(s1h['net_power']) >= 3 or s1h['bull_pct'] > 60 or s1h['bull_pct'] < 40

    if should_trade and not sig['has_position']:
        # 判断多空倾向
        if s1h['net_power'] >= 3:
            suggest_side = 'LONG'
            reason = f'1H净力量{s1h["net_power"]:+d}'
        elif s1h['net_power'] <= -3:
            suggest_side = 'SHORT'
            reason = f'1H净力量{s1h["net_power"]:+d}'
        elif s1h['bull_pct'] > 60:
            suggest_side = 'LONG'
            reason = f'1H阳线占比{s1h["bull_pct"]:.0f}%'
        elif s1h['bull_pct'] < 40:
            suggest_side = 'SHORT'
            reason = f'1H阳线占比{s1h["bull_pct"]:.0f}%'
        else:
            suggest_side = None
            reason = ''

        # 分析可能的原因
        possible_reasons = []

        # 检查5M是否有冲突信号
        if suggest_side == 'LONG' and s5m['net_power'] < -3:
            possible_reasons.append(f'5M净力量为{s5m["net_power"]}(空头)，与1H多头冲突')
        elif suggest_side == 'SHORT' and s5m['net_power'] > 3:
            possible_reasons.append(f'5M净力量为+{s5m["net_power"]}(多头)，与1H空头冲突')

        # 检查是否信号评分不够
        if not possible_reasons:
            possible_reasons.append('可能信号评分未达到开仓阈值(45分)')

        # 检查是否在黑名单
        possible_reasons.append('或交易对在黑名单/评级过低')

        # 检查是否已有相同方向持仓
        possible_reasons.append('或已有同向持仓未平')

        missed_opportunities.append({
            'symbol': s,
            'side': suggest_side,
            'reason': reason,
            'possible_reasons': possible_reasons,
            'net_power_1h': s1h['net_power'],
            'net_power_5m': s5m['net_power']
        })

if missed_opportunities:
    for i, opp in enumerate(missed_opportunities, 1):
        print(f'\n{i}. {opp["symbol"]:15s} | 建议{opp["side"]:5s} | {opp["reason"]}')
        print(f'   1H净力量: {opp["net_power_1h"]:+d} | 5M净力量: {opp["net_power_5m"]:+d}')
        print(f'   ❓可能原因:')
        for reason in opp['possible_reasons']:
            print(f'      - {reason}')
else:
    print('  无错过机会')

print('\n' + '=' * 120)

# 统计
total_analyzed = len(results)
has_position = sum(1 for r in results if r['signal_status']['has_position'])
should_trade = sum(1 for r in results if abs(r['strength_1h']['net_power']) >= 3 or
                   r['strength_1h']['bull_pct'] > 60 or r['strength_1h']['bull_pct'] < 40)
missed = sum(1 for r in results if not r['signal_status']['has_position'] and
             (abs(r['strength_1h']['net_power']) >= 3 or
              r['strength_1h']['bull_pct'] > 60 or r['strength_1h']['bull_pct'] < 40))

# 方向错误统计
wrong_direction = 0
for r in results:
    s1h = r['strength_1h']
    sig = r['signal_status']
    if not sig['has_position']:
        continue
    pos_side = sig['position']['position_side']

    # 判断建议方向
    if s1h['net_power'] >= 3 or s1h['bull_pct'] > 55:
        suggest_side = 'LONG'
    elif s1h['net_power'] <= -3 or s1h['bull_pct'] < 45:
        suggest_side = 'SHORT'
    else:
        suggest_side = None

    if suggest_side and pos_side != suggest_side:
        wrong_direction += 1

correct_captures = has_position - wrong_direction

print(f'\n【统计】')
print(f'  分析交易对: {total_analyzed}')
print(f'  有交易机会: {should_trade}')
print(f'  已开仓: {has_position} (正确{correct_captures}个, 方向错误{wrong_direction}个)')
print(f'  错过机会: {missed}')
print(f'  有效捕获率: {(correct_captures / should_trade * 100) if should_trade > 0 else 0:.1f}%')

cursor.close()
conn.close()
