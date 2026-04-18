#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四策略历史回测脚本
用法: python backtest_multi_strategy.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import numpy as np
import pymysql
import pymysql.cursors
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from app.analyzers.technical_indicators import TechnicalIndicators
from app.utils.config_loader import load_config

# ─────────────────────────────────────────
# 配置
# ─────────────────────────────────────────
SYMBOLS = [
    'ORDI/USDT', 'ENJ/USDT', 'PNUT/USDT',
    'BTC/USDT',  'ETH/USDT', 'SOL/USDT',
]
DAYS = 60          # 回测天数
TIMEFRAME = '1h'   # 基础 K 线粒度

TI = TechnicalIndicators()

# ─────────────────────────────────────────
# DB
# ─────────────────────────────────────────

def get_conn():
    cfg = load_config().get('database', {}).get('mysql', {})
    return pymysql.connect(
        **cfg, charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor, autocommit=True,
    )


def load_klines(symbol: str, days: int = DAYS) -> Optional[pd.DataFrame]:
    """拉取近 N 天 1H K 线"""
    try:
        conn = get_conn()
        cur = conn.cursor()
        since_ms = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)
        cur.execute("""
            SELECT open_time, open_price, high_price, low_price, close_price, volume
            FROM kline_data
            WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures'
              AND open_time >= %s
            ORDER BY open_time ASC
        """, (symbol, TIMEFRAME, since_ms))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if len(rows) < 48:
            return None
        df = pd.DataFrame(rows)
        df = df.rename(columns={
            'open_price': 'open', 'high_price': 'high',
            'low_price': 'low', 'close_price': 'close',
        })
        for c in ('open', 'high', 'low', 'close', 'volume'):
            df[c] = pd.to_numeric(df[c], errors='coerce')
        df = df.reset_index(drop=True)
        return df
    except Exception as e:
        print(f"  [ERROR] 加载 {symbol} K 线失败: {e}")
        return None


def resample_to(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """将 1H DataFrame 重采样到 4H / 1D"""
    df2 = df.set_index(pd.to_datetime(df['open_time'], unit='ms'))
    rs = df2.resample(freq).agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum',
    }).dropna()
    return rs.reset_index(drop=True)


# ─────────────────────────────────────────
# 模拟平仓（用 1H OHLC 前向搜索）
# ─────────────────────────────────────────

def simulate_exit(df: pd.DataFrame, entry_idx: int, side: str,
                  entry_price: float, tp_pct: Optional[float],
                  sl_pct: Optional[float], hold_bars: int
                  ) -> Dict:
    """从 entry_idx+1 开始逐根 K 线检查 TP/SL/时间平仓"""
    if tp_pct:
        tp_price = entry_price * (1 + tp_pct) if side == 'LONG' else entry_price * (1 - tp_pct)
    else:
        tp_price = None
    if sl_pct:
        sl_price = entry_price * (1 - sl_pct) if side == 'LONG' else entry_price * (1 + sl_pct)
    else:
        sl_price = None

    end_idx = min(entry_idx + 1 + hold_bars, len(df) - 1)

    for i in range(entry_idx + 1, end_idx + 1):
        h = float(df.loc[i, 'high'])
        l = float(df.loc[i, 'low'])
        close = float(df.loc[i, 'close'])

        if side == 'LONG':
            if sl_price and l <= sl_price:
                exit_price = sl_price
                exit_reason = 'SL'
                break
            if tp_price and h >= tp_price:
                exit_price = tp_price
                exit_reason = 'TP'
                break
        else:  # SHORT
            if sl_price and h >= sl_price:
                exit_price = sl_price
                exit_reason = 'SL'
                break
            if tp_price and l <= tp_price:
                exit_price = tp_price
                exit_reason = 'TP'
                break
    else:
        exit_price = float(df.loc[end_idx, 'close'])
        exit_reason = 'TIME'

    if side == 'LONG':
        pnl_pct = (exit_price - entry_price) / entry_price
    else:
        pnl_pct = (entry_price - exit_price) / entry_price

    return {
        'exit_price': exit_price,
        'exit_reason': exit_reason,
        'pnl_pct': pnl_pct,
        'bars_held': i - entry_idx if 'i' in dir() else hold_bars,
    }


# ─────────────────────────────────────────
# 策略信号检测
# ─────────────────────────────────────────

def check_s1(df_1h: pd.DataFrame, i: int) -> bool:
    """S1 早期做多: 1H RSI 28-45 + 4H MACD金叉 + 价格在20日MA下方"""
    if i < 50:
        return False
    window_1h = df_1h.iloc[max(0, i - 29): i + 1].copy().reset_index(drop=True)
    rsi = TI.calculate_rsi(window_1h)
    last_rsi = float(rsi.iloc[-1])
    if not (28 <= last_rsi <= 45):
        return False

    # 4H MACD（从1H重采样）
    window_4h_raw = df_1h.iloc[max(0, i - 159): i + 1].copy().reset_index(drop=True)
    df_4h = resample_to(window_4h_raw, '4h')
    if len(df_4h) < 35:
        return False
    _, _, hist = TI.calculate_macd(df_4h)
    if len(hist) < 2:
        return False
    if not (float(hist.iloc[-2]) < 0 and float(hist.iloc[-1]) > -0.000001):
        return False

    # 20日MA（从1H重采样）
    window_1d_raw = df_1h.iloc[max(0, i - 24 * 25): i + 1].copy().reset_index(drop=True)
    df_1d = resample_to(window_1d_raw, '1D')
    if len(df_1d) < 21:
        return False
    ma20 = float(df_1d['close'].rolling(20).mean().iloc[-1])
    cur_price = float(df_1d['close'].iloc[-1])
    if not (ma20 * 0.95 <= cur_price <= ma20 * 1.01):
        return False

    # 量能回升
    if len(df_1d) >= 8:
        vol_today = float(df_1d['volume'].iloc[-1])
        vol_avg7 = float(df_1d['volume'].iloc[-8:-1].mean())
        if vol_avg7 > 0 and vol_today < vol_avg7 * 1.1:
            return False

    return True


def check_s2(df_1h: pd.DataFrame, i: int) -> bool:
    """S2 无量回调做多: 48H涨>15% + 无量 + 回调25-40% + 企稳"""
    if i < 52:
        return False
    window = df_1h.iloc[i - 51: i + 1].copy().reset_index(drop=True)
    closes = window['close'].values
    volumes = window['volume'].values

    recent_high = float(closes[-48:].max())
    recent_low = float(closes[-48:].min())
    cur = float(closes[-1])

    if recent_low <= 0:
        return False
    price_range = (recent_high - recent_low) / recent_low
    if price_range < 0.15:
        return False

    drawdown = (recent_high - cur) / recent_high if recent_high > 0 else 0
    if not (0.25 <= drawdown <= 0.40):
        return False

    # 1D 均量估算（7日）
    window_1d = resample_to(window, '1D')
    if len(window_1d) < 3:
        return False
    vol_avg_daily = float(window_1d['volume'].mean())
    vol_avg_hourly = vol_avg_daily / 24

    high_idx = int(window['close'].iloc[-48:].values.argmax())
    start = max(0, high_idx - 12)
    rise_vols = volumes[start: high_idx] if high_idx > 0 else volumes[:12]
    avg_rise_vol = float(rise_vols.mean()) if len(rise_vols) > 0 else vol_avg_hourly
    if vol_avg_hourly > 0 and avg_rise_vol > vol_avg_hourly * 1.2:
        return False

    # RSI 15m 近似（用1H RSI 35-52 上升）
    rsi = TI.calculate_rsi(window.iloc[-20:].reset_index(drop=True))
    if len(rsi) < 3:
        return False
    r1, r2, r3 = float(rsi.iloc[-3]), float(rsi.iloc[-2]), float(rsi.iloc[-1])
    if not (35 <= r3 <= 52 and r1 < r2 < r3):
        return False

    return True


def check_s3(df_1h: pd.DataFrame, i: int) -> bool:
    """S3 顶部做空: 1H RSI>75 + 布林上轨 + 48H涨>25%"""
    if i < 50:
        return False
    window = df_1h.iloc[max(0, i - 51): i + 1].copy().reset_index(drop=True)

    rsi = TI.calculate_rsi(window)
    if float(rsi.iloc[-1]) <= 75:
        return False

    upper, _, _ = TI.calculate_bollinger_bands(window)
    cur_price = float(window['close'].iloc[-1])
    if cur_price <= float(upper.iloc[-1]):
        return False

    price_48h_ago = float(window['close'].iloc[-48]) if len(window) >= 48 else float(window['close'].iloc[0])
    gain_48h = (cur_price - price_48h_ago) / price_48h_ago if price_48h_ago > 0 else 0
    if gain_48h <= 0.25:
        return False

    return True


def check_s4(df_1h: pd.DataFrame, i: int) -> bool:
    """S4 反弹做空: 反弹到7日高点62-82% + MACD/RSI顶背离 + 量能萎缩"""
    if i < 52:
        return False
    window_1h = df_1h.iloc[max(0, i - 51): i + 1].copy().reset_index(drop=True)

    # 7日高点
    df_1d_w = resample_to(window_1h, '1D')
    if len(df_1d_w) < 7:
        return False
    week_high = float(df_1d_w['high'].iloc[-7:].max())
    cur_price = float(window_1h['close'].iloc[-1])
    rebound_pct = cur_price / week_high if week_high > 0 else 0
    if not (0.62 <= rebound_pct <= 0.82):
        return False

    # 1H MACD 下降
    _, _, hist = TI.calculate_macd(window_1h)
    if len(hist) < 3:
        return False
    h1, h2, h3 = float(hist.iloc[-3]), float(hist.iloc[-2]), float(hist.iloc[-1])
    if not (h1 > h2 > h3):
        return False

    # 1H RSI < 62 且下降
    rsi = TI.calculate_rsi(window_1h)
    if len(rsi) < 3:
        return False
    r1, r2, r3 = float(rsi.iloc[-3]), float(rsi.iloc[-2]), float(rsi.iloc[-1])
    if r3 >= 62 or not (r1 > r2 > r3):
        return False

    # 量能萎缩
    closes = window_1h['close'].values
    vols = window_1h['volume'].values
    up_vols = [vols[j] for j in range(-9, -1) if closes[j] > closes[j - 1]]
    dn_vols = [vols[j] for j in range(-9, -1) if closes[j] < closes[j - 1]]
    if len(up_vols) >= 2 and len(dn_vols) >= 2:
        avg_up = sum(up_vols[-3:]) / len(up_vols[-3:])
        avg_dn = sum(dn_vols[-3:]) / len(dn_vols[-3:])
        if avg_dn > 0 and avg_up >= avg_dn * 0.75:
            return False

    return True


# ─────────────────────────────────────────
# 策略定义
# ─────────────────────────────────────────

STRATEGIES = [
    dict(name='S1-早期做多', check=check_s1, side='LONG',
         tp=0.20, sl=None,  hold_bars=24 * 7),
    dict(name='S2-无量回调', check=check_s2, side='LONG',
         tp=0.05, sl=0.02,  hold_bars=4),
    dict(name='S3-顶部做空', check=check_s3, side='SHORT',
         tp=0.15, sl=None,  hold_bars=24 * 3),
    dict(name='S4-反弹做空', check=check_s4, side='SHORT',
         tp=0.10, sl=0.03,  hold_bars=5),
]


# ─────────────────────────────────────────
# 主回测
# ─────────────────────────────────────────

def backtest_symbol(symbol: str) -> List[Dict]:
    print(f"\n{'='*60}")
    print(f"  回测: {symbol}")
    print(f"{'='*60}")

    df = load_klines(symbol, days=DAYS + 5)
    if df is None or len(df) < 100:
        print(f"  数据不足，跳过")
        return []

    print(f"  K线条数: {len(df)}  ({DAYS}天 1H)")

    all_trades = []

    for strat in STRATEGIES:
        trades = []
        in_position = False
        entry_idx = -1

        for i in range(55, len(df) - 1):
            if in_position:
                continue  # 每策略同时只持1仓（回测简化）

            try:
                if strat['check'](df, i):
                    entry_price = float(df.loc[i, 'close'])
                    result = simulate_exit(
                        df, i, strat['side'], entry_price,
                        strat['tp'], strat['sl'], strat['hold_bars'],
                    )
                    margin = 300
                    leverage = 10 if strat['name'].startswith('S2') else 5
                    pnl_u = result['pnl_pct'] * margin * leverage

                    trade = {
                        'symbol': symbol,
                        'strategy': strat['name'],
                        'side': strat['side'],
                        'entry_bar': i,
                        'entry_price': entry_price,
                        'exit_price': result['exit_price'],
                        'exit_reason': result['exit_reason'],
                        'pnl_pct': result['pnl_pct'],
                        'pnl_u': pnl_u,
                        'bars_held': result['bars_held'],
                    }
                    trades.append(trade)
                    all_trades.append(trade)
                    in_position = True
                    entry_idx = i
                    # 冷却期：跳过持仓期后再允许下一单
                    i += result['bars_held']
                    in_position = False
            except Exception:
                pass

        # 打印该策略结果
        if trades:
            wins = [t for t in trades if t['pnl_pct'] > 0]
            total_pnl = sum(t['pnl_u'] for t in trades)
            wr = len(wins) / len(trades) * 100
            avg_pnl = total_pnl / len(trades)
            print(f"\n  [{strat['name']}]  信号数:{len(trades)}  "
                  f"胜率:{wr:.0f}%  总盈亏:{total_pnl:+.1f}U  均盈亏:{avg_pnl:+.1f}U")
            for t in trades[-5:]:  # 打印最近5笔
                bar_ts = datetime.utcfromtimestamp(
                    int(df.loc[min(t['entry_bar'], len(df)-1), 'open_time']) / 1000
                ).strftime('%m-%d %H:%M')
                print(f"    {bar_ts}  @{t['entry_price']:.4g} -> {t['exit_price']:.4g}"
                      f"  {t['exit_reason']:4s}  {t['pnl_u']:+.1f}U")
        else:
            print(f"\n  [{strat['name']}]  本段无触发信号")

    return all_trades


def main():
    print("\n四策略历史回测")
    print(f"标的: {SYMBOLS}")
    print(f"区间: 近 {DAYS} 天  |  粒度: 1H")

    all_trades = []
    for sym in SYMBOLS:
        trades = backtest_symbol(sym)
        all_trades.extend(trades)

    # 汇总
    print(f"\n\n{'='*60}")
    print("  汇总（所有标的合计）")
    print(f"{'='*60}")
    for strat in STRATEGIES:
        sname = strat['name']
        ts = [t for t in all_trades if t['strategy'] == sname]
        if not ts:
            print(f"  {sname}: 无信号")
            continue
        wins = [t for t in ts if t['pnl_pct'] > 0]
        total = sum(t['pnl_u'] for t in ts)
        wr = len(wins) / len(ts) * 100
        print(f"  {sname}: {len(ts)}笔  胜率{wr:.0f}%  "
              f"总盈亏{total:+.1f}U  均盈亏{total/len(ts):+.1f}U")

    print()


if __name__ == '__main__':
    main()
