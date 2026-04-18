#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量能异动信号全量验证脚本
规则: 12H内量比峰值>3.5x + 当前量比1.2-5x + RSI 28-55 + 价格在MA20的75-108%
      + 过去3H涨幅<5%（避免追高）
验证后续8H最大涨幅
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

def mkconn():
    cfg = load_config().get('database', {}).get('mysql', {})
    return pymysql.connect(**cfg, charset='utf8mb4',
                           cursorclass=pymysql.cursors.DictCursor, autocommit=True)

def main():
    since_ms = int((datetime.utcnow() - timedelta(days=8)).timestamp() * 1000)

    c = mkconn(); cur = c.cursor()
    cur.execute(
        "SELECT DISTINCT symbol FROM kline_data"
        " WHERE timeframe='1h' AND exchange='binance_futures'"
        " AND symbol LIKE '%%/USDT' AND open_time>=%s"
        " GROUP BY symbol HAVING COUNT(*)>=120",
        (since_ms,)
    )
    symbols = [r['symbol'] for r in cur.fetchall()]
    cur.close(); c.close()
    print(f'扫描 {len(symbols)} 个品种...')

    all_hits = []

    for idx, sym in enumerate(symbols):
        if idx % 50 == 0:
            print(f'  进度: {idx}/{len(symbols)}')
        try:
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
            """, (sym, since_ms))
            rows = cur.fetchall()
            cur.close(); c.close()

            if len(rows) < 60:
                continue

            df = pd.DataFrame(rows)
            for col in ('open', 'high', 'low', 'close', 'volume'):
                df[col] = pd.to_numeric(df[col])

            rsi_s = TI.calculate_rsi(df)
            ma20 = df['close'].rolling(20).mean()

            for i in range(30, len(df) - 8):
                vol_base = float(df['volume'].iloc[max(0, i - 47):i].mean())
                if vol_base <= 0:
                    continue

                cur_vol = float(df['volume'].iloc[i])
                vol_ratio = cur_vol / vol_base
                if not (1.2 <= vol_ratio <= 5.0):
                    continue

                max_vol_12h = float(df['volume'].iloc[max(0, i - 11):i + 1].max())
                max_ratio_12h = max_vol_12h / vol_base
                if max_ratio_12h < 3.5:
                    continue

                rsi_val = float(rsi_s.iloc[i]) if i < len(rsi_s) else 50.0
                if not (28 <= rsi_val <= 55):
                    continue

                ma_val = float(ma20.iloc[i]) if i < len(ma20) else 0.0
                if ma_val <= 0:
                    continue
                close_v = float(df['close'].iloc[i])
                price_ratio = close_v / ma_val
                if not (0.75 <= price_ratio <= 1.08):
                    continue

                # 过去3H涨幅<5%（避免追高）
                prev_close = float(df['close'].iloc[max(0, i - 3)])
                if prev_close > 0 and (close_v - prev_close) / prev_close > 0.05:
                    continue

                # 后续8H最大涨幅
                end = min(i + 9, len(df))
                future_high = float(df['high'].iloc[i + 1:end].max()) if end > i + 1 else close_v
                gain8 = (future_high - close_v) / close_v * 100

                ts = datetime.utcfromtimestamp(
                    int(df['open_time'].iloc[i]) / 1000
                ).strftime('%m-%d %H:%M')
                all_hits.append((sym, ts, vol_ratio, max_ratio_12h, rsi_val, price_ratio, gain8))

        except Exception as e:
            print(f'  [WARN] {sym}: {e}')

    # 汇总
    print()
    print('=' * 60)
    if not all_hits:
        print('  无命中')
        return

    gains = [h[6] for h in all_hits]
    total = len(all_hits)
    avg_gain = sum(gains) / total
    win3 = sum(1 for g in gains if g > 3)
    win5 = sum(1 for g in gains if g > 5)
    win8 = sum(1 for g in gains if g > 8)

    print(f'  总触发: {total} 次')
    print(f'  均后8H涨幅: {avg_gain:+.1f}%')
    print(f'  >3%: {win3/total*100:.0f}%  >5%: {win5/total*100:.0f}%  >8%: {win8/total*100:.0f}%')
    print()

    by_sym = defaultdict(list)
    for h in all_hits:
        by_sym[h[0]].append(h[6])

    sym_stats = sorted(
        [(s, len(gs), sum(gs) / len(gs), max(gs), sum(1 for g in gs if g > 5) / len(gs) * 100)
         for s, gs in by_sym.items()],
        key=lambda x: -x[2]
    )
    print('  命中品种TOP20（按均涨幅）:')
    print(f'  {"Symbol":22s} {"次数":>4s}  {"均涨幅":>7s}  {"最高":>7s}  {">5%":>5s}')
    print('  ' + '-' * 55)
    for sym, cnt, avg, mx, wr5 in sym_stats[:20]:
        print(f'  {sym:22s} {cnt:>4d}  {avg:>+6.1f}%  {mx:>+6.1f}%  {wr5:>4.0f}%')

    print()
    print('  命中次数最多 TOP10:')
    sym_by_cnt = sorted(by_sym.items(), key=lambda x: -len(x[1]))
    for sym, gs in sym_by_cnt[:10]:
        avg = sum(gs) / len(gs)
        wr5 = sum(1 for g in gs if g > 5) / len(gs) * 100
        print(f'  {sym:22s} {len(gs):>4d}次  均{avg:+.1f}%  >5%={wr5:.0f}%')


if __name__ == '__main__':
    main()
