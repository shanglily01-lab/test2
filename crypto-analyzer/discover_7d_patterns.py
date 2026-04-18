#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
近7天多模式探索脚本
对所有USDT交易对测试多种进场规则，找出实际有效的规律
每个规则: 统计触发后4H/8H最大涨幅及胜率
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from collections import defaultdict

from app.analyzers.technical_indicators import TechnicalIndicators
from app.utils.config_loader import load_config

TI = TechnicalIndicators()
LARGE_CAPS = {
    'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'XRP/USDT',
    'DOGE/USDT', 'ADA/USDT', 'AVAX/USDT', 'TRX/USDT', 'LINK/USDT',
    'TON/USDT', 'DOT/USDT', 'MATIC/USDT', 'SHIB/USDT', 'LTC/USDT',
}


def mkconn():
    cfg = load_config().get('database', {}).get('mysql', {})
    return pymysql.connect(**cfg, charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)


def load_symbols():
    since_ms = int((datetime.utcnow() - timedelta(days=10)).timestamp() * 1000)
    c = mkconn(); cur = c.cursor()
    cur.execute(
        "SELECT DISTINCT symbol FROM kline_data"
        " WHERE timeframe='1h' AND exchange='binance_futures'"
        " AND symbol LIKE %s AND open_time>=%s"
        " GROUP BY symbol HAVING COUNT(*)>=100",
        ('%/USDT', since_ms)
    )
    symbols = [r['symbol'] for r in cur.fetchall()]
    cur.close(); c.close()
    return symbols


def load_df(symbol):
    since_ms = int((datetime.utcnow() - timedelta(days=12)).timestamp() * 1000)
    c = mkconn(); cur = c.cursor()
    cur.execute("""
        SELECT open_time,
               open_price+0  AS open,
               high_price+0  AS high,
               low_price+0   AS low,
               close_price+0 AS close,
               volume+0      AS volume
        FROM kline_data
        WHERE symbol=%s AND timeframe='1h' AND exchange='binance_futures'
          AND open_time>=%s
        ORDER BY open_time ASC
    """, (symbol, since_ms))
    rows = cur.fetchall()
    cur.close(); c.close()
    if len(rows) < 50:
        return None
    df = pd.DataFrame(rows)
    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col])
    return df.reset_index(drop=True)


def future_gain(df, i, hours):
    """i棒之后hours小时内最大涨幅"""
    end = min(i + hours + 1, len(df))
    if end <= i + 1:
        return 0.0
    close_v = float(df['close'].iloc[i])
    if close_v <= 0:
        return 0.0
    fut_high = float(df['high'].iloc[i+1:end].max())
    return (fut_high - close_v) / close_v * 100


# ─────────────────────────────────────────
# 规则定义: 返回 True/False，以及该bar是否在近7天窗口内
# ─────────────────────────────────────────

RULES = {}


def rule(name):
    def decorator(fn):
        RULES[name] = fn
        return fn
    return decorator


@rule('RSI超卖回升(1H<35,上升)')
def r_rsi_oversold(df, i):
    if i < 20:
        return False
    rsi = TI.calculate_rsi(df.iloc[max(0, i-19):i+1].reset_index(drop=True))
    if len(rsi) < 3:
        return False
    r1, r2 = float(rsi.iloc[-2]), float(rsi.iloc[-1])
    return r2 < 35 and r2 > r1


@rule('RSI超卖回升(1H<30,上升)')
def r_rsi_deep(df, i):
    if i < 20:
        return False
    rsi = TI.calculate_rsi(df.iloc[max(0, i-19):i+1].reset_index(drop=True))
    if len(rsi) < 3:
        return False
    r1, r2 = float(rsi.iloc[-2]), float(rsi.iloc[-1])
    return r2 < 30 and r2 > r1


@rule('MACD_hist由负转正(1H)')
def r_macd_cross(df, i):
    if i < 30:
        return False
    _, _, hist = TI.calculate_macd(df.iloc[max(0, i-29):i+1].reset_index(drop=True))
    if len(hist) < 3:
        return False
    return float(hist.iloc[-2]) < 0 < float(hist.iloc[-1])


@rule('量能放大+RSI低位(量>2x,RSI<45)')
def r_vol_rsi(df, i):
    if i < 25:
        return False
    vol_base = float(df['volume'].iloc[max(0, i-24):i].mean())
    if vol_base <= 0:
        return False
    cur_vol = float(df['volume'].iloc[i])
    if cur_vol < vol_base * 2.0:
        return False
    rsi = TI.calculate_rsi(df.iloc[max(0, i-19):i+1].reset_index(drop=True))
    if len(rsi) < 2:
        return False
    return float(rsi.iloc[-1]) < 45


@rule('量能放大+RSI中位(量>2x,RSI 45-60)')
def r_vol_rsi_mid(df, i):
    if i < 25:
        return False
    vol_base = float(df['volume'].iloc[max(0, i-24):i].mean())
    if vol_base <= 0:
        return False
    cur_vol = float(df['volume'].iloc[i])
    if cur_vol < vol_base * 2.0:
        return False
    rsi = TI.calculate_rsi(df.iloc[max(0, i-19):i+1].reset_index(drop=True))
    if len(rsi) < 2:
        return False
    return 45 <= float(rsi.iloc[-1]) <= 60


@rule('12H量峰>3x+当前量>1.5x(量能异动V2)')
def r_vol_spike_v2(df, i):
    if i < 35:
        return False
    vol_base = float(df['volume'].iloc[max(0, i-48):i].mean())
    if vol_base <= 0:
        return False
    cur_vol = float(df['volume'].iloc[i])
    if not (1.5 <= cur_vol / vol_base <= 6.0):
        return False
    max_12h = float(df['volume'].iloc[max(0, i-11):i+1].max())
    return max_12h / vol_base >= 3.0


@rule('价格突破近72H高点')
def r_breakout_72h(df, i):
    if i < 73:
        return False
    prev_high = float(df['close'].iloc[i-72:i].max())
    return float(df['close'].iloc[i]) > prev_high


@rule('价格突破近24H高点+量放大')
def r_breakout_24h_vol(df, i):
    if i < 25:
        return False
    prev_high = float(df['high'].iloc[i-24:i].max())
    if float(df['close'].iloc[i]) <= prev_high:
        return False
    vol_base = float(df['volume'].iloc[max(0, i-24):i].mean())
    if vol_base <= 0:
        return False
    return float(df['volume'].iloc[i]) > vol_base * 1.5


@rule('MA5上穿MA20(1H金叉)')
def r_ma_cross(df, i):
    if i < 22:
        return False
    closes = df['close'].iloc[max(0, i-21):i+1]
    ma5_prev = float(closes.iloc[-6:-1].mean())
    ma5_cur = float(closes.iloc[-5:].mean())
    ma20_prev = float(closes.iloc[-21:-1].mean())
    ma20_cur = float(closes.iloc[-20:].mean())
    return ma5_prev <= ma20_prev and ma5_cur > ma20_cur


@rule('连跌3根后阳线反转(止跌信号)')
def r_reversal_bar(df, i):
    if i < 5:
        return False
    # 前3根都是阴线
    bearish = all(
        float(df['close'].iloc[i-k]) < float(df['open'].iloc[i-k])
        for k in range(3, 0, -1)
    )
    if not bearish:
        return False
    # 当前是阳线且涨幅>0.5%
    cur_close = float(df['close'].iloc[i])
    cur_open = float(df['open'].iloc[i])
    return cur_close > cur_open and (cur_close - cur_open) / cur_open > 0.005


@rule('价格跌至MA20的85%以内+反弹')
def r_ma_support(df, i):
    if i < 22:
        return False
    ma20 = float(df['close'].iloc[i-20:i].mean())
    if ma20 <= 0:
        return False
    close_v = float(df['close'].iloc[i])
    prev_close = float(df['close'].iloc[i-1])
    ratio = close_v / ma20
    return 0.82 <= ratio <= 0.95 and close_v > prev_close


@rule('缩量整理后放量(今量>昨量1.8x且前3根缩量)')
def r_vol_expansion(df, i):
    if i < 10:
        return False
    vols = [float(df['volume'].iloc[i-k]) for k in range(5)]
    # 前3根成交量递减（整理期）
    if not (vols[3] > vols[2] > vols[1]):
        return False
    # 当前放量
    return vols[0] > vols[1] * 1.8


@rule('RSI 40-50区间+量能回升(轻仓蓄势)')
def r_accumulation(df, i):
    if i < 30:
        return False
    rsi = TI.calculate_rsi(df.iloc[max(0, i-19):i+1].reset_index(drop=True))
    if len(rsi) < 2:
        return False
    r2 = float(rsi.iloc[-1])
    if not (40 <= r2 <= 52):
        return False
    vol_base = float(df['volume'].iloc[max(0, i-24):i].mean())
    if vol_base <= 0:
        return False
    return float(df['volume'].iloc[i]) > vol_base * 1.3


# ─────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────

def main():
    # 近7天窗口起点（毫秒）
    since_7d_ms = int((datetime.utcnow() - timedelta(days=7)).timestamp() * 1000)

    symbols = load_symbols()
    print(f'扫描 {len(symbols)} 个品种，近7天数据...')

    # 每个规则: {'hits4': [(gain4H,),...], 'hits8': [(gain8H,),...]}
    results = {name: {'g4': [], 'g8': [], 'syms': []} for name in RULES}
    # 大市值单独统计
    results_large = {name: {'g4': [], 'g8': []} for name in RULES}

    for idx, sym in enumerate(symbols):
        if idx % 100 == 0:
            print(f'  {idx}/{len(symbols)}...')
        try:
            df = load_df(sym)
            if df is None or len(df) < 50:
                continue

            is_large = sym in LARGE_CAPS

            for i in range(25, len(df) - 8):
                bar_ts = int(df['open_time'].iloc[i])
                if bar_ts < since_7d_ms:
                    continue  # 只看近7天的bar

                g4 = future_gain(df, i, 4)
                g8 = future_gain(df, i, 8)

                for name, fn in RULES.items():
                    try:
                        if fn(df, i):
                            if is_large:
                                results_large[name]['g4'].append(g4)
                                results_large[name]['g8'].append(g8)
                            else:
                                results[name]['g4'].append(g4)
                                results[name]['g8'].append(g8)
                                results[name]['syms'].append(sym)
                    except Exception:
                        pass
        except Exception as e:
            pass

    # ─── 输出 ───
    print()
    print('=' * 80)
    print('  近7天规律探索 - 小中市值 (排除大市值前15)')
    print(f'  {"规则":30s}  {"触发":>5s}  {"均4H":>7s}  {">3%":>5s}  {">5%":>5s}  {"均8H":>7s}  {">5%8H":>6s}')
    print('  ' + '-' * 75)

    # 按 >5% 胜率排序
    def sort_key(item):
        g8 = item[1]['g8']
        if len(g8) < 5:
            return -999
        return sum(1 for g in g8 if g > 5) / len(g8)

    for name, data in sorted(results.items(), key=sort_key, reverse=True):
        g4 = data['g4']; g8 = data['g8']
        if len(g4) < 3:
            print(f'  {name:30s}  {"<3":>5s}  (数据不足)')
            continue
        n = len(g4)
        avg4 = sum(g4) / n
        wr3_4 = sum(1 for g in g4 if g > 3) / n * 100
        wr5_4 = sum(1 for g in g4 if g > 5) / n * 100
        avg8 = sum(g8) / n
        wr5_8 = sum(1 for g in g8 if g > 5) / n * 100
        print(f'  {name:30s}  {n:>5d}  {avg4:>+6.2f}%  {wr3_4:>4.0f}%  {wr5_4:>4.0f}%  {avg8:>+6.2f}%  {wr5_8:>5.0f}%')

    print()
    print('=' * 80)
    print('  近7天规律探索 - 大市值 (BTC/ETH/SOL/BNB/XRP等)')
    print(f'  {"规则":30s}  {"触发":>5s}  {"均4H":>7s}  {">3%":>5s}  {">5%":>5s}  {"均8H":>7s}  {">5%8H":>6s}')
    print('  ' + '-' * 75)
    for name, data in sorted(results_large.items(), key=lambda x: (
        sum(1 for g in x[1]['g8'] if g > 3) / max(1, len(x[1]['g8']))
    ), reverse=True):
        g4 = data['g4']; g8 = data['g8']
        if len(g4) < 2:
            continue
        n = len(g4)
        avg4 = sum(g4) / n
        wr3_4 = sum(1 for g in g4 if g > 3) / n * 100
        wr5_4 = sum(1 for g in g4 if g > 5) / n * 100
        avg8 = sum(g8) / n
        wr5_8 = sum(1 for g in g8 if g > 5) / n * 100
        print(f'  {name:30s}  {n:>5d}  {avg4:>+6.2f}%  {wr3_4:>4.0f}%  {wr5_4:>4.0f}%  {avg8:>+6.2f}%  {wr5_8:>5.0f}%')

    # ─── 哪些符号在最优规则下命中最多 ───
    best_rule = max(
        results.items(),
        key=lambda x: sum(1 for g in x[1]['g8'] if g > 5) / max(1, len(x[1]['g8']))
        if len(x[1]['g8']) >= 5 else -1
    )
    print()
    print(f'  最佳规则: {best_rule[0]}')
    sym_cnt = defaultdict(int)
    for s in best_rule[1]['syms']:
        sym_cnt[s] += 1
    top_syms = sorted(sym_cnt.items(), key=lambda x: -x[1])[:20]
    print('  命中次数最多的品种:')
    for s, cnt in top_syms:
        print(f'    {s:20s}  {cnt}次')


if __name__ == '__main__':
    main()
