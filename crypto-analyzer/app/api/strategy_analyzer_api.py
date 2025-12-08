# -*- coding: utf-8 -*-
"""
策略分析API
提供48小时K线分析、EMA信号分析和参数优化建议
"""

from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List, Dict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import pymysql
import yaml
from pathlib import Path
from loguru import logger
import pandas as pd
import numpy as np

router = APIRouter(prefix="/api/strategy-analyzer", tags=["策略分析"])

# 加载配置
def get_db_config():
    from app.utils.config_loader import load_config
    config = load_config()
    return config.get('database', {}).get('mysql', {})


def get_connection():
    """获取数据库连接"""
    db_config = get_db_config()
    return pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


@router.get("/symbols")
async def get_available_symbols():
    """获取可用的交易对列表"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 获取有K线数据的交易对
        cursor.execute("""
            SELECT DISTINCT symbol
            FROM kline_data
            WHERE timestamp > DATE_SUB(NOW(), INTERVAL 48 HOUR)
            ORDER BY symbol
        """)
        symbols = [row['symbol'] for row in cursor.fetchall()]

        conn.close()
        return {"success": True, "data": symbols}
    except Exception as e:
        logger.error(f"获取交易对列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analyze")
async def analyze_symbol(
    symbol: str = Query(..., description="交易对，如 BTC/USDT"),
    timeframe: str = Query(default="15m", description="K线周期"),
    hours: int = Query(default=48, description="分析时间范围（小时）"),
    ema_short: int = Query(default=9, description="短期EMA周期"),
    ema_long: int = Query(default=26, description="长期EMA周期"),
    stop_loss_pct: float = Query(default=2.5, description="止损百分比"),
    take_profit_pct: float = Query(default=4.0, description="止盈百分比"),
    # 持续趋势模式参数
    sustained_trend_enabled: bool = Query(default=True, description="启用持续趋势模式"),
    sustained_trend_min_strength: float = Query(default=0.3, description="趋势最小强度(%)"),
    sustained_trend_max_strength: float = Query(default=2.0, description="趋势最大强度(%)"),
    sustained_trend_require_ma10_confirm: bool = Query(default=True, description="要求MA10/EMA10确认"),
    sustained_trend_require_price_confirm: bool = Query(default=True, description="要求价格确认"),
    sustained_trend_min_bars: int = Query(default=3, description="趋势持续最小K线数")
):
    """
    分析指定交易对的K线和EMA信号

    返回:
    - K线数据和EMA计算结果
    - 信号统计（多少次金叉/死叉）
    - 模拟交易结果
    - 参数优化建议
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 1. 获取K线数据
        cursor.execute("""
            SELECT timestamp, open_price as open, high_price as high,
                   low_price as low, close_price as close, volume
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s
              AND timestamp > DATE_SUB(NOW(), INTERVAL %s HOUR)
            ORDER BY timestamp ASC
        """, (symbol, timeframe, hours))

        klines = cursor.fetchall()

        if not klines or len(klines) < ema_long + 5:
            return {
                "success": False,
                "error": f"K线数据不足，需要至少 {ema_long + 5} 条，实际获取 {len(klines)} 条"
            }

        # 2. 转换为DataFrame并计算EMA
        df = pd.DataFrame(klines)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df['close'] = df['close'].astype(float)
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['volume'] = df['volume'].astype(float)

        # 计算EMA
        df['ema_short'] = df['close'].ewm(span=ema_short, adjust=False).mean()
        df['ema_long'] = df['close'].ewm(span=ema_long, adjust=False).mean()

        # 计算MA10和EMA10（用于趋势确认）
        df['ma10'] = df['close'].rolling(window=10).mean()
        df['ema10'] = df['close'].ewm(span=10, adjust=False).mean()

        # 计算EMA差值百分比（绝对值用于趋势强度判断）
        df['ema_diff_pct'] = ((df['ema_short'] - df['ema_long']) / df['ema_long'] * 100).round(4)
        df['ema_strength_pct'] = df['ema_diff_pct'].abs()  # 趋势强度（绝对值）

        # 3. 检测金叉/死叉信号
        df['signal'] = 0
        df['signal_type'] = ''

        for i in range(1, len(df)):
            prev_short = df.iloc[i-1]['ema_short']
            prev_long = df.iloc[i-1]['ema_long']
            curr_short = df.iloc[i]['ema_short']
            curr_long = df.iloc[i]['ema_long']

            # 金叉：短期EMA从下穿越长期EMA
            if prev_short <= prev_long and curr_short > curr_long:
                df.at[df.index[i], 'signal'] = 1
                df.at[df.index[i], 'signal_type'] = 'GOLDEN_CROSS'
            # 死叉：短期EMA从上穿越长期EMA
            elif prev_short >= prev_long and curr_short < curr_long:
                df.at[df.index[i], 'signal'] = -1
                df.at[df.index[i], 'signal_type'] = 'DEATH_CROSS'

        # 4. 模拟交易 - 两种模式对比
        # 模式1: 固定止盈止损（传统模式）
        trades_fixed = []
        position = None

        for i, row in df.iterrows():
            if row['signal'] == 1 and position is None:
                position = {
                    'type': 'LONG',
                    'entry_time': row['timestamp'],
                    'entry_price': row['close'],
                    'stop_loss': row['close'] * (1 - stop_loss_pct / 100),
                    'take_profit': row['close'] * (1 + take_profit_pct / 100)
                }
            elif row['signal'] == -1 and position is None:
                position = {
                    'type': 'SHORT',
                    'entry_time': row['timestamp'],
                    'entry_price': row['close'],
                    'stop_loss': row['close'] * (1 + stop_loss_pct / 100),
                    'take_profit': row['close'] * (1 - take_profit_pct / 100)
                }

            if position:
                exit_reason = None
                exit_price = None

                if position['type'] == 'LONG':
                    if row['low'] <= position['stop_loss']:
                        exit_reason = 'STOP_LOSS'
                        exit_price = position['stop_loss']
                    elif row['high'] >= position['take_profit']:
                        exit_reason = 'TAKE_PROFIT'
                        exit_price = position['take_profit']
                    elif row['signal'] == -1:
                        exit_reason = 'SIGNAL_EXIT'
                        exit_price = row['close']
                else:
                    if row['high'] >= position['stop_loss']:
                        exit_reason = 'STOP_LOSS'
                        exit_price = position['stop_loss']
                    elif row['low'] <= position['take_profit']:
                        exit_reason = 'TAKE_PROFIT'
                        exit_price = position['take_profit']
                    elif row['signal'] == 1:
                        exit_reason = 'SIGNAL_EXIT'
                        exit_price = row['close']

                if exit_reason:
                    if position['type'] == 'LONG':
                        pnl_pct = (exit_price - position['entry_price']) / position['entry_price'] * 100
                    else:
                        pnl_pct = (position['entry_price'] - exit_price) / position['entry_price'] * 100

                    trades_fixed.append({
                        'type': position['type'],
                        'entry_time': position['entry_time'].strftime('%Y-%m-%d %H:%M'),
                        'entry_price': round(position['entry_price'], 4),
                        'exit_time': row['timestamp'].strftime('%Y-%m-%d %H:%M'),
                        'exit_price': round(exit_price, 4),
                        'exit_reason': exit_reason,
                        'pnl_pct': round(pnl_pct, 2)
                    })
                    position = None

        # 模式2: 趋势跟踪模式（只在趋势反转时出场，止损仅作保护）
        trades_trend = []
        position = None

        for i, row in df.iterrows():
            if row['signal'] == 1 and position is None:
                position = {
                    'type': 'LONG',
                    'entry_time': row['timestamp'],
                    'entry_price': row['close'],
                    'stop_loss': row['close'] * (1 - stop_loss_pct / 100),  # 保护性止损
                    'max_price': row['close']  # 跟踪最高价
                }
            elif row['signal'] == -1 and position is None:
                position = {
                    'type': 'SHORT',
                    'entry_time': row['timestamp'],
                    'entry_price': row['close'],
                    'stop_loss': row['close'] * (1 + stop_loss_pct / 100),
                    'min_price': row['close']  # 跟踪最低价
                }

            if position:
                exit_reason = None
                exit_price = None

                if position['type'] == 'LONG':
                    # 更新最高价（用于移动止损）
                    if row['high'] > position.get('max_price', position['entry_price']):
                        position['max_price'] = row['high']
                        # 移动止损：最高价回撤止损比例时触发
                        position['trailing_stop'] = position['max_price'] * (1 - stop_loss_pct / 100)

                    trailing_stop = position.get('trailing_stop', position['stop_loss'])

                    # 只在趋势反转（死叉）或触及移动止损时出场
                    if row['signal'] == -1:
                        exit_reason = 'TREND_REVERSE'  # 趋势反转
                        exit_price = row['close']
                    elif row['low'] <= trailing_stop:
                        exit_reason = 'TRAILING_STOP'  # 移动止损
                        exit_price = trailing_stop
                else:  # SHORT
                    if row['low'] < position.get('min_price', position['entry_price']):
                        position['min_price'] = row['low']
                        position['trailing_stop'] = position['min_price'] * (1 + stop_loss_pct / 100)

                    trailing_stop = position.get('trailing_stop', position['stop_loss'])

                    if row['signal'] == 1:
                        exit_reason = 'TREND_REVERSE'
                        exit_price = row['close']
                    elif row['high'] >= trailing_stop:
                        exit_reason = 'TRAILING_STOP'
                        exit_price = trailing_stop

                if exit_reason:
                    if position['type'] == 'LONG':
                        pnl_pct = (exit_price - position['entry_price']) / position['entry_price'] * 100
                    else:
                        pnl_pct = (position['entry_price'] - exit_price) / position['entry_price'] * 100

                    trades_trend.append({
                        'type': position['type'],
                        'entry_time': position['entry_time'].strftime('%Y-%m-%d %H:%M'),
                        'entry_price': round(position['entry_price'], 4),
                        'exit_time': row['timestamp'].strftime('%Y-%m-%d %H:%M'),
                        'exit_price': round(exit_price, 4),
                        'exit_reason': exit_reason,
                        'pnl_pct': round(pnl_pct, 2),
                        'max_profit': round((position.get('max_price', position['entry_price']) - position['entry_price']) / position['entry_price'] * 100, 2) if position['type'] == 'LONG' else round((position['entry_price'] - position.get('min_price', position['entry_price'])) / position['entry_price'] * 100, 2)
                    })
                    position = None

        # 5. 统计分析 - 两种模式分别计算
        golden_crosses = len(df[df['signal'] == 1])
        death_crosses = len(df[df['signal'] == -1])

        def calc_stats(trades_list, mode_name):
            """计算交易统计"""
            if not trades_list:
                return {
                    "mode": mode_name,
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0,
                    "total_pnl_pct": 0,
                    "avg_win_pct": 0,
                    "avg_loss_pct": 0,
                    "stop_loss_exits": 0,
                    "take_profit_exits": 0,
                    "signal_exits": 0,
                    "trend_reverse_exits": 0,
                    "trailing_stop_exits": 0
                }

            winning = [t for t in trades_list if t['pnl_pct'] > 0]
            losing = [t for t in trades_list if t['pnl_pct'] <= 0]

            return {
                "mode": mode_name,
                "total_trades": len(trades_list),
                "winning_trades": len(winning),
                "losing_trades": len(losing),
                "win_rate": round(len(winning) / len(trades_list) * 100, 1),
                "total_pnl_pct": round(sum(t['pnl_pct'] for t in trades_list), 2),
                "avg_win_pct": round(np.mean([t['pnl_pct'] for t in winning]), 2) if winning else 0,
                "avg_loss_pct": round(np.mean([t['pnl_pct'] for t in losing]), 2) if losing else 0,
                "stop_loss_exits": len([t for t in trades_list if t['exit_reason'] == 'STOP_LOSS']),
                "take_profit_exits": len([t for t in trades_list if t['exit_reason'] == 'TAKE_PROFIT']),
                "signal_exits": len([t for t in trades_list if t['exit_reason'] == 'SIGNAL_EXIT']),
                "trend_reverse_exits": len([t for t in trades_list if t['exit_reason'] == 'TREND_REVERSE']),
                "trailing_stop_exits": len([t for t in trades_list if t['exit_reason'] == 'TRAILING_STOP'])
            }

        stats_fixed = calc_stats(trades_fixed, "固定止盈止损")
        stats_trend = calc_stats(trades_trend, "趋势跟踪")

        # 主统计使用趋势跟踪模式
        trades = trades_trend
        winning_trades = [t for t in trades if t['pnl_pct'] > 0]
        losing_trades = [t for t in trades if t['pnl_pct'] <= 0]
        win_rate = stats_trend['win_rate']
        total_pnl = stats_trend['total_pnl_pct']
        avg_win = stats_trend['avg_win_pct']
        avg_loss = stats_trend['avg_loss_pct']

        # 6. 行情分析
        price_change = (df.iloc[-1]['close'] - df.iloc[0]['close']) / df.iloc[0]['close'] * 100
        volatility = df['close'].pct_change().std() * 100

        # 判断行情类型
        if abs(price_change) < 2 and volatility < 1:
            regime = 'ranging'
            regime_name = '震荡'
        elif price_change > 3:
            regime = 'strong_uptrend' if price_change > 6 else 'weak_uptrend'
            regime_name = '强趋势上涨' if price_change > 6 else '弱趋势上涨'
        elif price_change < -3:
            regime = 'strong_downtrend' if price_change < -6 else 'weak_downtrend'
            regime_name = '强趋势下跌' if price_change < -6 else '弱趋势下跌'
        else:
            regime = 'ranging'
            regime_name = '震荡'

        # 7. 优化建议
        suggestions = []
        optimized_params = {
            'ema_short': ema_short,
            'ema_long': ema_long,
            'stop_loss_pct': stop_loss_pct,
            'take_profit_pct': take_profit_pct
        }

        # 根据胜率给建议
        if win_rate < 40:
            suggestions.append({
                'type': 'warning',
                'title': '胜率偏低',
                'content': f'当前胜率仅 {win_rate:.1f}%，建议增加趋势确认条件（如MA10确认）',
                'param_change': None
            })

        # 根据止损触发次数（趋势跟踪模式）
        trailing_stop_count = stats_trend['trailing_stop_exits']
        trend_reverse_count = stats_trend['trend_reverse_exits']
        if trailing_stop_count > len(trades_trend) * 0.5 and trades_trend:
            new_sl = round(stop_loss_pct + 0.5, 1)
            suggestions.append({
                'type': 'warning',
                'title': '移动止损触发频繁',
                'content': f'移动止损触发 {trailing_stop_count}/{len(trades_trend)} 次，建议适当放宽止损比例（当前 {stop_loss_pct}% → 建议 {new_sl}%）',
                'param_change': {'stop_loss_pct': new_sl}
            })
            optimized_params['stop_loss_pct'] = new_sl

        # 根据止盈触发情况（固定模式）
        if stats_fixed['take_profit_exits'] == 0 and stats_fixed['total_trades'] > 2:
            # 没有触发止盈，可能止盈设置过高
            new_tp = round(take_profit_pct * 0.8, 1)
            suggestions.append({
                'type': 'warning',
                'title': '止盈未触发',
                'content': f'固定模式下止盈从未触发，建议降低止盈目标（当前 {take_profit_pct}% → 建议 {new_tp}%）',
                'param_change': {'take_profit_pct': new_tp}
            })
            optimized_params['take_profit_pct'] = new_tp

        # 模式对比建议
        if stats_trend['total_pnl_pct'] > stats_fixed['total_pnl_pct']:
            pnl_diff = stats_trend['total_pnl_pct'] - stats_fixed['total_pnl_pct']
            suggestions.append({
                'type': 'success',
                'title': '趋势跟踪模式更优',
                'content': f'趋势跟踪模式收益 {stats_trend["total_pnl_pct"]:.2f}% 高于固定止盈止损 {stats_fixed["total_pnl_pct"]:.2f}%，多赚 {pnl_diff:.2f}%',
                'param_change': {'exit_mode': 'trend'}
            })
            optimized_params['exit_mode'] = 'trend'
        elif stats_fixed['total_pnl_pct'] > stats_trend['total_pnl_pct']:
            pnl_diff = stats_fixed['total_pnl_pct'] - stats_trend['total_pnl_pct']
            suggestions.append({
                'type': 'info',
                'title': '固定止盈止损模式更优',
                'content': f'固定模式收益 {stats_fixed["total_pnl_pct"]:.2f}% 高于趋势跟踪 {stats_trend["total_pnl_pct"]:.2f}%，多赚 {pnl_diff:.2f}%。当前行情可能更适合固定目标',
                'param_change': {'exit_mode': 'fixed'}
            })
            optimized_params['exit_mode'] = 'fixed'

        # 根据行情类型给建议
        if regime == 'ranging':
            suggestions.append({
                'type': 'info',
                'title': '震荡行情',
                'content': '当前处于震荡行情，EMA信号容易假突破，建议启用sustainedTrend过滤或暂停交易',
                'param_change': {'sustainedTrend_enabled': True}
            })
            optimized_params['sustainedTrend_enabled'] = True
        elif regime in ['strong_uptrend', 'strong_downtrend']:
            # 强趋势时可以放大止盈
            new_tp = round(take_profit_pct * 1.2, 1)
            suggestions.append({
                'type': 'success',
                'title': '强趋势行情',
                'content': f'当前处于强趋势行情，适合EMA策略，可以适当提高止盈目标（当前 {take_profit_pct}% → 建议 {new_tp}%）',
                'param_change': {'take_profit_pct': new_tp}
            })
            optimized_params['take_profit_pct'] = new_tp

        # 根据信号频率
        hours_per_signal = hours / (golden_crosses + death_crosses) if (golden_crosses + death_crosses) > 0 else hours
        if hours_per_signal < 2:
            new_ema_short = ema_short + 2
            new_ema_long = ema_long + 4
            suggestions.append({
                'type': 'warning',
                'title': '信号过于频繁',
                'content': f'平均每 {hours_per_signal:.1f} 小时产生一次信号，建议增加EMA周期（短期 {ema_short} → {new_ema_short}，长期 {ema_long} → {new_ema_long}）或启用minEmaCrossStrength过滤',
                'param_change': {'ema_short': new_ema_short, 'ema_long': new_ema_long}
            })
            optimized_params['ema_short'] = new_ema_short
            optimized_params['ema_long'] = new_ema_long

        # 8. 数据统计信息
        data_stats = {
            "total_klines": len(df),  # 实际加载的K线数量
            "start_time": df.iloc[0]['timestamp'].strftime('%Y-%m-%d %H:%M'),
            "end_time": df.iloc[-1]['timestamp'].strftime('%Y-%m-%d %H:%M'),
            "hours_covered": round((df.iloc[-1]['timestamp'] - df.iloc[0]['timestamp']).total_seconds() / 3600, 1),
            "expected_klines": hours * 4 if timeframe == '15m' else hours,  # 15分钟周期48小时约192条
            "data_completeness": round(len(df) / (hours * 4 if timeframe == '15m' else hours) * 100, 1)
        }

        # 9. 准备K线数据（只返回最近100条用于绘图）
        kline_data = []
        for _, row in df.tail(100).iterrows():
            kline_data.append({
                'timestamp': row['timestamp'].strftime('%Y-%m-%d %H:%M'),
                'open': round(float(row['open']), 4),
                'high': round(float(row['high']), 4),
                'low': round(float(row['low']), 4),
                'close': round(float(row['close']), 4),
                'volume': round(float(row['volume']), 2),
                'ema_short': round(float(row['ema_short']), 4),
                'ema_long': round(float(row['ema_long']), 4),
                'ma10': round(float(row['ma10']), 4) if not pd.isna(row['ma10']) else None,
                'ema_diff_pct': round(float(row['ema_diff_pct']), 4),
                'signal': int(row['signal']),
                'signal_type': row['signal_type']
            })

        conn.close()

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "timeframe": timeframe,
                "hours": hours,
                "data_stats": data_stats,  # 数据统计信息
                "klines": kline_data,
                "signals": {
                    "golden_crosses": golden_crosses,
                    "death_crosses": death_crosses,
                    "total": golden_crosses + death_crosses
                },
                "trades": trades_trend,  # 主要显示趋势跟踪模式
                "trades_fixed": trades_fixed,  # 固定止盈止损模式
                "trades_trend": trades_trend,  # 趋势跟踪模式
                "statistics": stats_trend,  # 主统计使用趋势跟踪
                "statistics_fixed": stats_fixed,  # 固定模式统计
                "statistics_trend": stats_trend,  # 趋势跟踪统计
                "mode_comparison": {
                    "fixed": {
                        "name": "固定止盈止损",
                        "description": "到达固定止盈/止损价格或反向信号时出场",
                        "total_pnl": stats_fixed['total_pnl_pct'],
                        "win_rate": stats_fixed['win_rate'],
                        "trades": stats_fixed['total_trades']
                    },
                    "trend": {
                        "name": "趋势跟踪",
                        "description": "只在趋势反转时出场，移动止损仅作保护",
                        "total_pnl": stats_trend['total_pnl_pct'],
                        "win_rate": stats_trend['win_rate'],
                        "trades": stats_trend['total_trades']
                    },
                    "better_mode": "trend" if stats_trend['total_pnl_pct'] >= stats_fixed['total_pnl_pct'] else "fixed",
                    "pnl_diff": round(abs(stats_trend['total_pnl_pct'] - stats_fixed['total_pnl_pct']), 2)
                },
                "regime": {
                    "type": regime,
                    "name": regime_name,
                    "price_change_pct": round(price_change, 2),
                    "volatility": round(volatility, 3)
                },
                "current_params": {
                    "ema_short": ema_short,
                    "ema_long": ema_long,
                    "stop_loss_pct": stop_loss_pct,
                    "take_profit_pct": take_profit_pct
                },
                "optimized_params": optimized_params,
                "suggestions": suggestions
            }
        }

    except Exception as e:
        logger.error(f"分析失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/optimize")
async def optimize_params(
    symbol: str = Query(..., description="交易对"),
    timeframe: str = Query(default="15m", description="K线周期"),
    hours: int = Query(default=48, description="分析时间范围")
):
    """
    参数优化 - 测试不同参数组合找出最佳配置
    """
    try:
        # 测试的参数组合
        param_combinations = [
            {"ema_short": 9, "ema_long": 26, "sl": 2.0, "tp": 3.0},
            {"ema_short": 9, "ema_long": 26, "sl": 2.5, "tp": 4.0},
            {"ema_short": 9, "ema_long": 26, "sl": 3.0, "tp": 5.0},
            {"ema_short": 5, "ema_long": 20, "sl": 2.5, "tp": 4.0},
            {"ema_short": 12, "ema_long": 26, "sl": 2.5, "tp": 4.0},
            {"ema_short": 9, "ema_long": 21, "sl": 2.5, "tp": 4.0},
        ]

        results = []

        for params in param_combinations:
            # 调用analyze接口
            result = await analyze_symbol(
                symbol=symbol,
                timeframe=timeframe,
                hours=hours,
                ema_short=params['ema_short'],
                ema_long=params['ema_long'],
                stop_loss_pct=params['sl'],
                take_profit_pct=params['tp']
            )

            if result.get('success'):
                stats = result['data']['statistics']
                results.append({
                    'params': params,
                    'win_rate': stats['win_rate'],
                    'total_pnl': stats['total_pnl_pct'],
                    'trades': stats['total_trades'],
                    'score': stats['win_rate'] * 0.4 + stats['total_pnl_pct'] * 0.6  # 综合评分
                })

        # 按评分排序
        results.sort(key=lambda x: x['score'], reverse=True)

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "timeframe": timeframe,
                "hours": hours,
                "results": results,
                "best_params": results[0] if results else None
            }
        }

    except Exception as e:
        logger.error(f"参数优化失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/regime-params")
async def get_regime_params(strategy_id: str = Query(..., description="策略ID")):
    """获取策略的行情参数配置"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT regime_type, enabled, params
            FROM strategy_regime_params
            WHERE strategy_id = %s
        """, (strategy_id,))

        rows = cursor.fetchall()

        import json
        regime_params = {}
        for row in rows:
            regime_params[row['regime_type']] = {
                'enabled': bool(row['enabled']),
                'params': json.loads(row['params']) if row['params'] else {}
            }

        conn.close()

        return {"success": True, "data": regime_params}

    except Exception as e:
        logger.error(f"获取行情参数失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/strategies")
async def get_strategies():
    """获取启用的策略列表"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT id, name, config
            FROM trading_strategies
            WHERE enabled = 1
        """)

        strategies = cursor.fetchall()

        import json
        result = []
        for s in strategies:
            config = json.loads(s['config']) if s['config'] else {}
            # symbols 从 config.symbols 中获取
            symbols = config.get('symbols', [])
            if isinstance(symbols, str):
                symbols = [symbols]
            result.append({
                'id': str(s['id']),
                'name': s['name'],
                'symbols': symbols,
                'stop_loss': config.get('stopLoss', 2.5),
                'take_profit': config.get('takeProfit', 4.0),
                'buy_timeframe': config.get('buyTimeframe', '15m'),
                'ema_short': config.get('emaShortPeriod', 9),
                'ema_long': config.get('emaLongPeriod', 26),
                'adaptive_regime': config.get('adaptiveRegime', False)
            })

        conn.close()

        return {"success": True, "data": result}

    except Exception as e:
        logger.error(f"获取策略列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
