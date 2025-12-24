#!/usr/bin/env python3
"""
复盘合约(24H) API
Futures Trading Review API

提供24小时模拟合约交易复盘数据：
- 统计摘要
- 成交订单列表
- 取消订单分析
- 开仓/平仓原因分析
- 策略优化建议
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, HTTPException, Query
from typing import Dict, List, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql

# 创建 Router
router = APIRouter(prefix='/api/futures/review', tags=['futures-review'])

# 加载配置
from app.utils.config_loader import load_config
config = load_config()
db_config = config['database']['mysql']

# 平仓原因中英文映射
CLOSE_REASON_MAP = {
    'hard_stop_loss': '硬止损',
    'trailing_stop_loss': '移动止损',
    'max_take_profit': '最大止盈',
    'trailing_take_profit': '移动止盈',
    'ema_diff_narrowing_tp': 'EMA差值收窄止盈',
    'death_cross_reversal': '死叉反转平仓',
    'golden_cross_reversal': '金叉反转平仓',
    '5m_death_cross_sl': '5分钟死叉止损',
    '5m_golden_cross_sl': '5分钟金叉止损',
    'manual': '手动平仓',
    'liquidation': '强制平仓',
    'sync_close': '同步平仓',
}

# 开仓原因中英文映射
ENTRY_REASON_MAP = {
    'golden_cross': '金叉信号',
    'death_cross': '死叉信号',
    'sustained_trend': '持续趋势',
    'ema_trend': 'EMA趋势',
    'limit_order': '限价单',
    'manual': '手动开仓',
}

# 取消原因中英文映射
CANCEL_REASON_MAP = {
    'timeout': '超时取消',
    'validation_failed': '自检未通过',
    'trend_reversal': '趋势转向',
    'ema_direction_changed': 'EMA方向变化',
    'price_invalid': '价格无效',
    'manual': '手动取消',
    'trend_end': '趋势结束',
    'min_ema_diff': 'EMA差值不足',
}


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(**db_config, autocommit=True)


def parse_close_reason(notes: str) -> str:
    """解析平仓原因代码"""
    if not notes:
        return 'unknown'
    # 格式: reason_code|param1:value|param2:value
    return notes.split('|')[0] if '|' in notes else notes


def parse_entry_reason(entry_reason: str) -> str:
    """解析开仓原因代码"""
    if not entry_reason:
        return 'unknown'
    # 格式可能包含详细信息，取第一部分
    return entry_reason.split('|')[0] if '|' in entry_reason else entry_reason


def parse_cancel_reason(reason: str) -> str:
    """解析取消原因代码"""
    if not reason:
        return 'unknown'
    return reason.split('|')[0] if '|' in reason else reason


@router.get("/summary")
async def get_review_summary(
    hours: int = Query(default=24, ge=1, le=168, description="统计时间范围（小时）"),
    account_id: int = Query(default=2, description="账户ID")
):
    """
    获取24H交易统计摘要

    返回:
    - 总订单数、成交数、取消数、成功率
    - 已实现盈亏、未实现盈亏、手续费
    - 盈利单数、亏损单数、胜率、平均盈亏比
    - 最大单笔盈利、最大单笔亏损
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.now() - timedelta(hours=hours)

        # 订单统计
        cursor.execute("""
            SELECT
                COUNT(*) as total_orders,
                SUM(CASE WHEN status = 'FILLED' THEN 1 ELSE 0 END) as filled_orders,
                SUM(CASE WHEN status = 'CANCELLED' THEN 1 ELSE 0 END) as cancelled_orders,
                SUM(CASE WHEN status = 'PENDING' THEN 1 ELSE 0 END) as pending_orders,
                SUM(fee) as total_fee
            FROM futures_orders
            WHERE account_id = %s AND created_at >= %s
        """, (account_id, time_threshold))
        order_stats = cursor.fetchone()

        # 持仓统计（已平仓的）
        cursor.execute("""
            SELECT
                COUNT(*) as total_positions,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as break_even_trades,
                SUM(realized_pnl) as total_realized_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE NULL END) as avg_profit,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE NULL END) as avg_loss,
                MAX(realized_pnl) as max_profit,
                MIN(realized_pnl) as max_loss,
                AVG(holding_hours) as avg_holding_hours
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed' AND close_time >= %s
        """, (account_id, time_threshold))
        position_stats = cursor.fetchone()

        # 当前未平仓持仓的未实现盈亏
        cursor.execute("""
            SELECT SUM(unrealized_pnl) as total_unrealized_pnl
            FROM futures_positions
            WHERE account_id = %s AND status = 'open'
        """, (account_id,))
        unrealized = cursor.fetchone()

        cursor.close()
        conn.close()

        # 计算胜率和盈亏比
        total_closed = position_stats['total_positions'] or 0
        winning = position_stats['winning_trades'] or 0
        losing = position_stats['losing_trades'] or 0
        win_rate = (winning / total_closed * 100) if total_closed > 0 else 0

        avg_profit = float(position_stats['avg_profit'] or 0)
        avg_loss = abs(float(position_stats['avg_loss'] or 1))
        profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0

        total_orders = order_stats['total_orders'] or 0
        filled_orders = order_stats['filled_orders'] or 0
        success_rate = (filled_orders / total_orders * 100) if total_orders > 0 else 0

        return {
            "success": True,
            "data": {
                "time_range_hours": hours,
                "order_overview": {
                    "total_orders": total_orders,
                    "filled_orders": filled_orders,
                    "cancelled_orders": order_stats['cancelled_orders'] or 0,
                    "pending_orders": order_stats['pending_orders'] or 0,
                    "success_rate": round(success_rate, 1)
                },
                "pnl_summary": {
                    "realized_pnl": float(position_stats['total_realized_pnl'] or 0),
                    "unrealized_pnl": float(unrealized['total_unrealized_pnl'] or 0),
                    "total_fee": float(order_stats['total_fee'] or 0)
                },
                "win_loss_analysis": {
                    "total_closed_positions": total_closed,
                    "winning_trades": winning,
                    "losing_trades": losing,
                    "break_even_trades": position_stats['break_even_trades'] or 0,
                    "win_rate": round(win_rate, 1),
                    "avg_profit": round(avg_profit, 2),
                    "avg_loss": round(-abs(float(position_stats['avg_loss'] or 0)), 2),
                    "profit_loss_ratio": round(profit_loss_ratio, 2)
                },
                "extremes": {
                    "max_profit": float(position_stats['max_profit'] or 0),
                    "max_loss": float(position_stats['max_loss'] or 0)
                },
                "avg_holding_hours": round(float(position_stats['avg_holding_hours'] or 0), 1)
            }
        }

    except Exception as e:
        logger.error(f"获取复盘摘要失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_review_trades(
    hours: int = Query(default=24, ge=1, le=168, description="统计时间范围（小时）"),
    account_id: int = Query(default=2, description="账户ID"),
    filter_type: str = Query(default="all", description="筛选类型: all/profit/loss"),
    sort_by: str = Query(default="time", description="排序方式: time/pnl")
):
    """
    获取24H成交订单列表

    包含: 时间、交易对、方向、开仓价、平仓价、数量、杠杆、盈亏、盈亏%、持仓时长、开仓原因、平仓原因
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.now() - timedelta(hours=hours)

        # 构建筛选条件
        filter_condition = ""
        if filter_type == "profit":
            filter_condition = "AND realized_pnl > 0"
        elif filter_type == "loss":
            filter_condition = "AND realized_pnl < 0"

        # 构建排序
        order_by = "close_time DESC" if sort_by == "time" else "realized_pnl DESC"

        cursor.execute(f"""
            SELECT
                id, symbol, position_side, leverage,
                quantity, entry_price, mark_price as close_price,
                realized_pnl, unrealized_pnl_pct as pnl_pct,
                holding_hours, entry_reason, notes as close_reason,
                open_time, close_time, entry_signal_type, status
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed' AND close_time >= %s
            {filter_condition}
            ORDER BY {order_by}
            LIMIT 100
        """, (account_id, time_threshold))

        positions = cursor.fetchall()

        cursor.close()
        conn.close()

        # 处理数据，添加中文映射
        trades = []
        for pos in positions:
            close_reason_code = parse_close_reason(pos['close_reason'])
            entry_reason_code = parse_entry_reason(pos['entry_reason']) if pos['entry_reason'] else (pos['entry_signal_type'] or 'unknown')

            trades.append({
                "id": pos['id'],
                "symbol": pos['symbol'],
                "position_side": pos['position_side'],
                "position_side_cn": "做多" if pos['position_side'] == 'LONG' else "做空",
                "leverage": pos['leverage'],
                "quantity": float(pos['quantity']),
                "entry_price": float(pos['entry_price']),
                "close_price": float(pos['close_price']) if pos['close_price'] else None,
                "realized_pnl": float(pos['realized_pnl'] or 0),
                "pnl_pct": float(pos['pnl_pct'] or 0),
                "holding_hours": pos['holding_hours'] or 0,
                "entry_reason_code": entry_reason_code,
                "entry_reason_cn": ENTRY_REASON_MAP.get(entry_reason_code, entry_reason_code),
                "close_reason_code": close_reason_code,
                "close_reason_cn": CLOSE_REASON_MAP.get(close_reason_code, close_reason_code),
                "close_reason_detail": pos['close_reason'],
                "open_time": pos['open_time'].isoformat() if pos['open_time'] else None,
                "close_time": pos['close_time'].isoformat() if pos['close_time'] else None
            })

        return {
            "success": True,
            "data": {
                "trades": trades,
                "count": len(trades),
                "filter": filter_type,
                "sort_by": sort_by
            }
        }

    except Exception as e:
        logger.error(f"获取成交列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/cancelled")
async def get_cancelled_orders(
    hours: int = Query(default=24, ge=1, le=168, description="统计时间范围（小时）"),
    account_id: int = Query(default=2, description="账户ID")
):
    """
    获取24H取消订单列表及原因分析

    返回:
    - 取消总数
    - 各取消原因统计
    - 取消订单详情列表
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.now() - timedelta(hours=hours)

        # 获取取消订单
        cursor.execute("""
            SELECT
                id, order_id, symbol, side, order_type, leverage,
                price, quantity, margin, cancellation_reason, notes,
                created_at, canceled_at
            FROM futures_orders
            WHERE account_id = %s AND status = 'CANCELLED' AND created_at >= %s
            ORDER BY canceled_at DESC
            LIMIT 200
        """, (account_id, time_threshold))

        orders = cursor.fetchall()

        cursor.close()
        conn.close()

        # 统计取消原因分布
        reason_stats = {}
        cancelled_list = []

        for order in orders:
            reason_code = parse_cancel_reason(order['cancellation_reason'])
            reason_cn = CANCEL_REASON_MAP.get(reason_code, reason_code)

            if reason_code not in reason_stats:
                reason_stats[reason_code] = {
                    "code": reason_code,
                    "name_cn": reason_cn,
                    "count": 0
                }
            reason_stats[reason_code]["count"] += 1

            cancelled_list.append({
                "id": order['id'],
                "order_id": order['order_id'],
                "symbol": order['symbol'],
                "side": order['side'],
                "side_cn": "做多" if "LONG" in order['side'] else "做空",
                "order_type": order['order_type'],
                "leverage": order['leverage'],
                "price": float(order['price']) if order['price'] else None,
                "quantity": float(order['quantity']) if order['quantity'] else None,
                "margin": float(order['margin']) if order['margin'] else None,
                "cancel_reason_code": reason_code,
                "cancel_reason_cn": reason_cn,
                "cancel_reason_detail": order['cancellation_reason'],
                "notes": order['notes'],
                "created_at": order['created_at'].isoformat() if order['created_at'] else None,
                "canceled_at": order['canceled_at'].isoformat() if order['canceled_at'] else None
            })

        # 计算占比
        total_cancelled = len(orders)
        reason_distribution = []
        for code, stats in sorted(reason_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            stats["percentage"] = round(stats["count"] / total_cancelled * 100, 1) if total_cancelled > 0 else 0
            reason_distribution.append(stats)

        return {
            "success": True,
            "data": {
                "total_cancelled": total_cancelled,
                "reason_distribution": reason_distribution,
                "cancelled_orders": cancelled_list
            }
        }

    except Exception as e:
        logger.error(f"获取取消订单分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/analysis")
async def get_reason_analysis(
    hours: int = Query(default=24, ge=1, le=168, description="统计时间范围（小时）"),
    account_id: int = Query(default=2, description="账户ID")
):
    """
    获取开仓/平仓原因分析统计

    返回:
    - 开仓信号类型分布及各类型胜率
    - 平仓原因分布及各原因平均盈亏
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.now() - timedelta(hours=hours)

        # 获取已平仓持仓
        cursor.execute("""
            SELECT
                entry_reason, entry_signal_type, notes as close_reason,
                realized_pnl, position_side
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed' AND close_time >= %s
        """, (account_id, time_threshold))

        positions = cursor.fetchall()

        cursor.close()
        conn.close()

        # 统计开仓原因
        entry_stats = {}
        # 统计平仓原因
        close_stats = {}
        # 统计方向
        direction_stats = {
            'LONG': {'count': 0, 'wins': 0, 'total_pnl': 0},
            'SHORT': {'count': 0, 'wins': 0, 'total_pnl': 0}
        }

        for pos in positions:
            pnl = float(pos['realized_pnl'] or 0)
            is_profit = pnl > 0

            # 开仓原因统计
            entry_code = parse_entry_reason(pos['entry_reason']) if pos['entry_reason'] else (pos['entry_signal_type'] or 'unknown')
            if entry_code not in entry_stats:
                entry_stats[entry_code] = {
                    "code": entry_code,
                    "name_cn": ENTRY_REASON_MAP.get(entry_code, entry_code),
                    "count": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_pnl": 0
                }
            entry_stats[entry_code]["count"] += 1
            entry_stats[entry_code]["total_pnl"] += pnl
            if is_profit:
                entry_stats[entry_code]["wins"] += 1
            else:
                entry_stats[entry_code]["losses"] += 1

            # 平仓原因统计
            close_code = parse_close_reason(pos['close_reason'])
            if close_code not in close_stats:
                close_stats[close_code] = {
                    "code": close_code,
                    "name_cn": CLOSE_REASON_MAP.get(close_code, close_code),
                    "count": 0,
                    "total_pnl": 0,
                    "pnl_list": []
                }
            close_stats[close_code]["count"] += 1
            close_stats[close_code]["total_pnl"] += pnl
            close_stats[close_code]["pnl_list"].append(pnl)

            # 方向统计
            side = pos['position_side']
            if side in direction_stats:
                direction_stats[side]['count'] += 1
                direction_stats[side]['total_pnl'] += pnl
                if is_profit:
                    direction_stats[side]['wins'] += 1

        # 计算开仓原因胜率
        entry_analysis = []
        for code, stats in sorted(entry_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            win_rate = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
            entry_analysis.append({
                "code": stats['code'],
                "name_cn": stats['name_cn'],
                "count": stats['count'],
                "wins": stats['wins'],
                "losses": stats['losses'],
                "win_rate": round(win_rate, 1),
                "total_pnl": round(stats['total_pnl'], 2),
                "avg_pnl": round(stats['total_pnl'] / stats['count'], 2) if stats['count'] > 0 else 0
            })

        # 计算平仓原因平均盈亏
        close_analysis = []
        for code, stats in sorted(close_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            avg_pnl = stats['total_pnl'] / stats['count'] if stats['count'] > 0 else 0
            close_analysis.append({
                "code": stats['code'],
                "name_cn": stats['name_cn'],
                "count": stats['count'],
                "total_pnl": round(stats['total_pnl'], 2),
                "avg_pnl": round(avg_pnl, 2)
            })

        # 计算方向胜率
        direction_analysis = []
        for side, stats in direction_stats.items():
            win_rate = (stats['wins'] / stats['count'] * 100) if stats['count'] > 0 else 0
            direction_analysis.append({
                "side": side,
                "side_cn": "做多" if side == 'LONG' else "做空",
                "count": stats['count'],
                "wins": stats['wins'],
                "win_rate": round(win_rate, 1),
                "total_pnl": round(stats['total_pnl'], 2)
            })

        return {
            "success": True,
            "data": {
                "entry_analysis": entry_analysis,
                "close_analysis": close_analysis,
                "direction_analysis": direction_analysis
            }
        }

    except Exception as e:
        logger.error(f"获取原因分析失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/suggestions")
async def get_strategy_suggestions(
    hours: int = Query(default=24, ge=1, le=168, description="统计时间范围（小时）"),
    account_id: int = Query(default=2, description="账户ID")
):
    """
    获取策略优化建议

    基于24H数据自动生成优化建议
    """
    try:
        # 先获取分析数据
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.now() - timedelta(hours=hours)

        # 获取已平仓持仓
        cursor.execute("""
            SELECT
                entry_reason, entry_signal_type, notes as close_reason,
                realized_pnl, unrealized_pnl_pct, position_side,
                stop_loss_pct, take_profit_pct, max_profit_pct
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed' AND close_time >= %s
        """, (account_id, time_threshold))
        positions = cursor.fetchall()

        # 获取取消订单
        cursor.execute("""
            SELECT cancellation_reason, COUNT(*) as count
            FROM futures_orders
            WHERE account_id = %s AND status = 'CANCELLED' AND created_at >= %s
            GROUP BY cancellation_reason
        """, (account_id, time_threshold))
        cancel_stats = cursor.fetchall()

        # 获取总订单数
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM futures_orders
            WHERE account_id = %s AND created_at >= %s
        """, (account_id, time_threshold))
        total_orders = cursor.fetchone()['total']

        cursor.close()
        conn.close()

        suggestions = []

        # 分析止损触发情况
        stop_loss_count = 0
        trailing_stop_count = 0
        max_tp_count = 0
        trailing_tp_count = 0
        cross_reversal_count = 0
        five_m_sl_count = 0

        long_stats = {'count': 0, 'wins': 0}
        short_stats = {'count': 0, 'wins': 0}

        trailing_tp_drawdowns = []

        for pos in positions:
            close_code = parse_close_reason(pos['close_reason'])
            pnl = float(pos['realized_pnl'] or 0)

            if close_code == 'hard_stop_loss':
                stop_loss_count += 1
            elif close_code == 'trailing_stop_loss':
                trailing_stop_count += 1
            elif close_code == 'max_take_profit':
                max_tp_count += 1
            elif close_code == 'trailing_take_profit':
                trailing_tp_count += 1
                # 计算回撤幅度
                max_profit = float(pos['max_profit_pct'] or 0)
                final_pnl_pct = float(pos['unrealized_pnl_pct'] or 0)
                if max_profit > 0:
                    drawdown = max_profit - final_pnl_pct
                    trailing_tp_drawdowns.append(drawdown)
            elif close_code in ['death_cross_reversal', 'golden_cross_reversal']:
                cross_reversal_count += 1
            elif close_code in ['5m_death_cross_sl', '5m_golden_cross_sl']:
                five_m_sl_count += 1

            # 方向统计
            if pos['position_side'] == 'LONG':
                long_stats['count'] += 1
                if pnl > 0:
                    long_stats['wins'] += 1
            else:
                short_stats['count'] += 1
                if pnl > 0:
                    short_stats['wins'] += 1

        total_positions = len(positions)

        # 生成建议

        # 1. 止损建议
        if total_positions > 0 and stop_loss_count / total_positions > 0.3:
            suggestions.append({
                "type": "warning",
                "category": "止损",
                "message": f"硬止损触发过多（{stop_loss_count}次，占比{round(stop_loss_count/total_positions*100)}%），建议适当放宽止损幅度或优化入场时机"
            })

        # 2. 5M止损建议
        if five_m_sl_count > 0:
            suggestions.append({
                "type": "info",
                "category": "5M止损",
                "message": f"5分钟信号止损触发{five_m_sl_count}次，该功能可及时避免更大亏损"
            })

        # 3. 移动止盈回撤建议
        if trailing_tp_drawdowns:
            avg_drawdown = sum(trailing_tp_drawdowns) / len(trailing_tp_drawdowns)
            if avg_drawdown > 1.5:
                suggestions.append({
                    "type": "warning",
                    "category": "移动止盈",
                    "message": f"移动止盈激活后平均回撤{round(avg_drawdown, 1)}%，建议调整回撤阈值"
                })

        # 4. 取消订单建议
        total_cancelled = sum(s['count'] for s in cancel_stats)
        if total_orders > 0 and total_cancelled / total_orders > 0.3:
            # 找出主要取消原因
            main_reason = max(cancel_stats, key=lambda x: x['count']) if cancel_stats else None
            if main_reason:
                reason_cn = CANCEL_REASON_MAP.get(parse_cancel_reason(main_reason['cancellation_reason']), main_reason['cancellation_reason'])
                suggestions.append({
                    "type": "warning",
                    "category": "订单取消",
                    "message": f"订单取消率较高（{round(total_cancelled/total_orders*100)}%），主要原因：{reason_cn}（{main_reason['count']}次）"
                })

        # 5. 方向胜率建议
        if long_stats['count'] >= 3 and short_stats['count'] >= 3:
            long_wr = long_stats['wins'] / long_stats['count'] * 100
            short_wr = short_stats['wins'] / short_stats['count'] * 100

            if abs(long_wr - short_wr) > 20:
                if long_wr > short_wr:
                    suggestions.append({
                        "type": "info",
                        "category": "方向分析",
                        "message": f"做多胜率（{round(long_wr)}%）明显高于做空（{round(short_wr)}%），当前市场偏多头"
                    })
                else:
                    suggestions.append({
                        "type": "info",
                        "category": "方向分析",
                        "message": f"做空胜率（{round(short_wr)}%）明显高于做多（{round(long_wr)}%），当前市场偏空头"
                    })

        # 6. 趋势反转平仓建议
        if cross_reversal_count > 0 and total_positions > 0:
            reversal_ratio = cross_reversal_count / total_positions
            if reversal_ratio > 0.2:
                suggestions.append({
                    "type": "info",
                    "category": "趋势反转",
                    "message": f"交叉反转平仓占比{round(reversal_ratio*100)}%（{cross_reversal_count}次），趋势切换频繁"
                })

        # 7. 如果没有足够数据
        if total_positions < 3:
            suggestions.append({
                "type": "info",
                "category": "数据不足",
                "message": f"24小时内仅有{total_positions}笔已平仓交易，建议积累更多数据后再做分析"
            })

        # 8. 胜率建议
        if total_positions >= 5:
            total_wins = long_stats['wins'] + short_stats['wins']
            win_rate = total_wins / total_positions * 100
            if win_rate < 40:
                suggestions.append({
                    "type": "danger",
                    "category": "胜率",
                    "message": f"整体胜率偏低（{round(win_rate)}%），建议优化入场条件或止盈止损策略"
                })
            elif win_rate > 60:
                suggestions.append({
                    "type": "success",
                    "category": "胜率",
                    "message": f"整体胜率良好（{round(win_rate)}%），策略表现稳定"
                })

        return {
            "success": True,
            "data": {
                "suggestions": suggestions,
                "stats_summary": {
                    "total_positions": total_positions,
                    "stop_loss_count": stop_loss_count,
                    "trailing_stop_count": trailing_stop_count,
                    "five_m_sl_count": five_m_sl_count,
                    "max_tp_count": max_tp_count,
                    "trailing_tp_count": trailing_tp_count,
                    "cross_reversal_count": cross_reversal_count,
                    "total_cancelled": total_cancelled,
                    "total_orders": total_orders
                }
            }
        }

    except Exception as e:
        logger.error(f"获取策略建议失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
