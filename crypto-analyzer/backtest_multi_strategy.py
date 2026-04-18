#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
六策略历史回测脚本
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
LARGE_CAP_SYMBOLS = {'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT'}
SMALL_CAP_SYMBOLS = set(SYMBOLS) - LARGE_CAP_SYMBOLS
DAYS = 7           # 回测天数
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
    """S1 早期做多: RSI 25-52 + 4H MACD连续向上 + 价格在MA20 80-105% + 量能回升"""
    if i < 50:
        return False

    # 1H RSI 25-52，且最近2根在上升
    window_1h = df_1h.iloc[max(0, i - 29): i + 1].copy().reset_index(drop=True)
    rsi = TI.calculate_rsi(window_1h)
    if len(rsi) < 3:
        return False
    last_rsi = float(rsi.iloc[-1])
    if not (25 <= last_rsi <= 52):
        return False
    if float(rsi.iloc[-2]) >= last_rsi:  # RSI必须在上升
        return False

    # 4H MACD histogram 连续2根向上，且前一根 < 0.002（还在低位）
    window_4h_raw = df_1h.iloc[max(0, i - 159): i + 1].copy().reset_index(drop=True)
    df_4h = resample_to(window_4h_raw, '4h')
    if len(df_4h) < 30:
        return False
    _, _, hist = TI.calculate_macd(df_4h)
    if len(hist) < 3:
        return False
    h1, h2, h3 = float(hist.iloc[-3]), float(hist.iloc[-2]), float(hist.iloc[-1])
    if not (h2 > h1 and h3 > h2 and h1 < 0.002):
        return False

    # 价格在MA20的 80-105% 区间（放宽，不再要求紧贴）
    window_1d_raw = df_1h.iloc[max(0, i - 24 * 25): i + 1].copy().reset_index(drop=True)
    df_1d = resample_to(window_1d_raw, '1D')
    if len(df_1d) < 21:
        return False
    ma20 = float(df_1d['close'].rolling(20).mean().iloc[-1])
    cur_price = float(df_1d['close'].iloc[-1])
    if not (ma20 * 0.80 <= cur_price <= ma20 * 1.05):
        return False

    # 量能回升（放宽到 0.9 倍均量即可）
    if len(df_1d) >= 8:
        vol_today = float(df_1d['volume'].iloc[-1])
        vol_avg7 = float(df_1d['volume'].iloc[-8:-1].mean())
        if vol_avg7 > 0 and vol_today < vol_avg7 * 0.9:
            return False

    return True


def check_s2(df_1h: pd.DataFrame, i: int) -> bool:
    """S2 回调做多: 48H涨>12% + 回调15-38% + RSI 30-58企稳（去掉无量条件）"""
    if i < 52:
        return False
    window = df_1h.iloc[i - 51: i + 1].copy().reset_index(drop=True)
    closes = window['close'].values

    recent_high = float(closes[-48:].max())
    recent_low = float(closes[-48:].min())
    cur = float(closes[-1])

    if recent_low <= 0:
        return False
    # 48H内曾有超过12%的价格区间
    price_range = (recent_high - recent_low) / recent_low
    if price_range < 0.12:
        return False

    # 从48H高点回调15-38%
    drawdown = (recent_high - cur) / recent_high if recent_high > 0 else 0
    if not (0.15 <= drawdown <= 0.38):
        return False

    # RSI 30-58 且最后2根上升（企稳迹象）
    rsi = TI.calculate_rsi(window.iloc[-20:].reset_index(drop=True))
    if len(rsi) < 3:
        return False
    r2, r3 = float(rsi.iloc[-2]), float(rsi.iloc[-1])
    if not (30 <= r3 <= 58 and r3 > r2):
        return False

    return True


def check_s3(df_1h: pd.DataFrame, i: int) -> bool:
    """S3 顶部做空: RSI>=72顶背离 + 价格从高点回落3-15% + 4H MACD开始下行 + 阴线确认"""
    if i < 96:  # 需要更多历史数据做4H分析
        return False
    window = df_1h.iloc[max(0, i - 51): i + 1].copy().reset_index(drop=True)

    rsi = TI.calculate_rsi(window)
    if len(rsi) < 6:
        return False
    last_rsi = float(rsi.iloc[-1])
    if last_rsi <= 70:  # 稍微放宽RSI下限
        return False

    # RSI顶背离：当前RSI不是最近6根的最高点
    recent_rsi_max = float(rsi.iloc[-6:].max())
    if last_rsi >= recent_rsi_max:
        return False

    # 价格已从48H内最高点回落3-15%（更大回落才更可信）
    cur_price = float(window['close'].iloc[-1])
    high_48h = float(window['high'].iloc[-48:].max()) if len(window) >= 48 else float(window['high'].max())
    retreat_pct = (high_48h - cur_price) / high_48h if high_48h > 0 else 0
    if not (0.03 <= retreat_pct <= 0.15):
        return False

    # 48H最高点相对48H前的涨幅 > 20%
    price_48h_ago = float(window['close'].iloc[-48]) if len(window) >= 48 else float(window['close'].iloc[0])
    gain_48h = (high_48h - price_48h_ago) / price_48h_ago if price_48h_ago > 0 else 0
    if gain_48h <= 0.20:
        return False

    # 4H MACD histogram 已经开始下行（多时间框架确认弱势）
    window_4h_raw = df_1h.iloc[max(0, i - 159): i + 1].copy().reset_index(drop=True)
    df_4h = resample_to(window_4h_raw, '4h')
    if len(df_4h) < 30:
        return False
    _, _, hist_4h = TI.calculate_macd(df_4h)
    if len(hist_4h) < 3:
        return False
    h4_1 = float(hist_4h.iloc[-3])
    h4_2 = float(hist_4h.iloc[-2])
    h4_3 = float(hist_4h.iloc[-1])
    # 4H MACD histogram 最近两段至少一段在下降
    if not (h4_3 < h4_2 or h4_2 < h4_1):
        return False

    # 近3根1H K线至少2根阴线（确认顶部压力）
    bearish_count = sum(
        1 for k in range(-3, 0)
        if float(window['close'].iloc[k]) < float(window['open'].iloc[k])
    )
    if bearish_count < 2:
        return False

    return True


def check_s4(df_1h: pd.DataFrame, i: int) -> bool:
    """S4 反弹做空: 反弹到14日高点40-90% + 且曾下跌>12% + MACD/RSI/量能三选二"""
    if i < 52:
        return False
    # 用更长的窗口捕捉14日高点
    window_1h = df_1h.iloc[max(0, i - 24 * 14 - 1): i + 1].copy().reset_index(drop=True)
    if len(window_1h) < 52:
        return False

    # 14日高点
    df_1d_w = resample_to(window_1h, '1D')
    if len(df_1d_w) < 5:
        return False
    two_week_high = float(df_1d_w['high'].max())
    cur_price = float(window_1h['close'].iloc[-1])

    # 当前价格在14日高点的40-90%（必须曾经有明显回落）
    rebound_pct = cur_price / two_week_high if two_week_high > 0 else 0
    if not (0.40 <= rebound_pct <= 0.90):
        return False

    # 当前价格在14日高点的50-85%（更合理区间）
    if not (0.50 <= rebound_pct <= 0.85):
        return False

    # 从14日高点到当前至少曾经下跌15%（有实质回落，不是一直跌）
    min_since_high = float(window_1h['low'].iloc[-24 * 7:].min())
    max_drop = (two_week_high - min_since_high) / two_week_high if two_week_high > 0 else 0
    if max_drop < 0.15:
        return False

    # 当前价格必须高于7日内最低点×1.05（即从低点已反弹5%以上，有真正反弹）
    low_7d = float(window_1h['low'].iloc[-24 * 7:].min())
    if cur_price < low_7d * 1.05:
        return False

    # 以最近52根1H K线做指标分析
    window_1h = window_1h.iloc[-52:].reset_index(drop=True)

    # 条件1: MACD histogram 最近任意一段在下降
    _, _, hist = TI.calculate_macd(window_1h)
    macd_bearish = False
    if len(hist) >= 3:
        h1, h2, h3 = float(hist.iloc[-3]), float(hist.iloc[-2]), float(hist.iloc[-1])
        if h3 < h2 or h2 < h1:
            macd_bearish = True

    # 条件2: RSI < 65 且最后一根在下降
    rsi = TI.calculate_rsi(window_1h)
    rsi_bearish = False
    if len(rsi) >= 3:
        r2, r3 = float(rsi.iloc[-2]), float(rsi.iloc[-1])
        if r3 < 65 and r3 < r2:
            rsi_bearish = True

    # 条件3: 上涨K均量 < 下跌K均量（量能萎缩）
    vol_shrink = False
    closes = window_1h['close'].values
    vols = window_1h['volume'].values
    up_vols = [vols[j] for j in range(-10, -1) if closes[j] > closes[j - 1]]
    dn_vols = [vols[j] for j in range(-10, -1) if closes[j] < closes[j - 1]]
    if len(up_vols) >= 2 and len(dn_vols) >= 2:
        avg_up = sum(up_vols) / len(up_vols)
        avg_dn = sum(dn_vols) / len(dn_vols)
        if avg_dn > 0 and avg_up < avg_dn:
            vol_shrink = True

    # 三选二
    return sum([macd_bearish, rsi_bearish, vol_shrink]) >= 2


def check_s5(df_1h: pd.DataFrame, i: int) -> bool:
    """S5 大币超卖反弹: 4H RSI<32 + RSI从低点回升(不再下降) + 价格低于日MA20"""
    if i < 96:
        return False

    # 4H RSI < 32 且最近一根RSI开始回升（反转确认，不是继续下坠）
    window_4h_raw = df_1h.iloc[max(0, i - 159): i + 1].copy().reset_index(drop=True)
    df_4h = resample_to(window_4h_raw, '4h')
    if len(df_4h) < 20:
        return False
    rsi_4h = TI.calculate_rsi(df_4h)
    if len(rsi_4h) < 4:
        return False
    r1 = float(rsi_4h.iloc[-2])
    r2 = float(rsi_4h.iloc[-1])
    if r2 >= 32:
        return False
    if r2 <= r1:  # RSI仍在下降，不开仓
        return False

    # 价格低于日线MA20
    window_1d_raw = df_1h.iloc[max(0, i - 24 * 25): i + 1].copy().reset_index(drop=True)
    df_1d = resample_to(window_1d_raw, '1D')
    if len(df_1d) < 21:
        return False
    ma20_1d = float(df_1d['close'].rolling(20).mean().iloc[-1])
    cur_price = float(df_1h['close'].iloc[i])
    return cur_price < ma20_1d


def check_s6(df_1h: pd.DataFrame, i: int) -> bool:
    """S6 小币量能异动: 12H量峰>3.5x均量 + 当前量1.2-5x + RSI 28-55 + 价格在MA20 75-108% + 3H涨<5%"""
    if i < 35:
        return False

    vol_base = float(df_1h['volume'].iloc[max(0, i - 48):i].mean())
    if vol_base <= 0:
        return False

    cur_vol = float(df_1h['volume'].iloc[i])
    vol_ratio_cur = cur_vol / vol_base
    if not (1.2 <= vol_ratio_cur <= 5.0):
        return False

    max_vol_12h = float(df_1h['volume'].iloc[max(0, i - 11):i + 1].max())
    if max_vol_12h / vol_base < 3.5:
        return False

    window = df_1h.iloc[max(0, i - 29):i + 1].copy().reset_index(drop=True)
    rsi_s = TI.calculate_rsi(window)
    if len(rsi_s) < 3:
        return False
    last_rsi = float(rsi_s.iloc[-1])
    if not (28 <= last_rsi <= 55):
        return False

    ma20 = float(df_1h['close'].iloc[max(0, i - 19):i + 1].mean())
    if ma20 <= 0:
        return False
    close_v = float(df_1h['close'].iloc[i])
    if not (0.75 <= close_v / ma20 <= 1.08):
        return False

    prev_close = float(df_1h['close'].iloc[max(0, i - 3)])
    if prev_close > 0 and (close_v - prev_close) / prev_close > 0.05:
        return False

    return True


# ─────────────────────────────────────────
# 策略定义
# ─────────────────────────────────────────

STRATEGIES = [
    dict(name='S1-早期做多', check=check_s1, side='LONG',
         tp=0.20, sl=None,  hold_bars=24 * 7,  margin=300, leverage=5,
         sym_filter=None),
    dict(name='S2-无量回调', check=check_s2, side='LONG',
         tp=0.05, sl=0.02,  hold_bars=4,         margin=300, leverage=10,
         sym_filter=None),
    dict(name='S3-顶部做空', check=check_s3, side='SHORT',
         tp=0.15, sl=None,  hold_bars=24 * 3,   margin=300, leverage=5,
         sym_filter=None),
    dict(name='S4-反弹做空', check=check_s4, side='SHORT',
         tp=0.10, sl=0.03,  hold_bars=5,         margin=300, leverage=5,
         sym_filter=None),
    dict(name='S5-大币超卖', check=check_s5, side='LONG',
         tp=0.05, sl=0.02,  hold_bars=48,        margin=200, leverage=5,
         sym_filter=LARGE_CAP_SYMBOLS),
    dict(name='S6-量能异动', check=check_s6, side='LONG',
         tp=0.08, sl=0.03,  hold_bars=8,         margin=200, leverage=5,
         sym_filter=SMALL_CAP_SYMBOLS),
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
        # 过滤不适用于该 symbol 的策略
        if strat.get('sym_filter') is not None and symbol not in strat['sym_filter']:
            continue

        trades = []

        next_open_bar = 55  # 真正的冷却期控制
        for i in range(55, len(df) - 1):
            if i < next_open_bar:
                continue  # 持仓冷却中

            try:
                if strat['check'](df, i):
                    entry_price = float(df.loc[i, 'close'])
                    result = simulate_exit(
                        df, i, strat['side'], entry_price,
                        strat['tp'], strat['sl'], strat['hold_bars'],
                    )
                    margin = strat['margin']
                    leverage = strat['leverage']
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
                    # 平仓后跳过整个持仓期（真正有效的冷却）
                    next_open_bar = i + result['bars_held'] + 1
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
    print("\n六策略历史回测")
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
