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

# 平仓原因中英文映射（基于数据库实际存储格式）
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
    'ema_direction_reversal_tp': 'EMA方向反转止盈',
    'manual': '手动平仓',
    'manual_close_all': '一键平仓',
    'liquidation': '强制平仓',
    'sync_close': '同步平仓',
    'reversal_warning': '反转预警平仓',
}

# 开仓原因中英文映射（基于 entry_signal_type 字段）
ENTRY_REASON_MAP = {
    'golden_cross': '金叉信号',
    'death_cross': '死叉信号',
    'sustained_trend': '持续趋势',
    'sustained_trend_FORWARD': '顺向持续趋势',
    'sustained_trend_REVERSE': '反转持续趋势',
    'sustained_trend_entry': '趋势入场',
    'ema_trend': 'EMA趋势',
    'limit_order': '限价单',
    'limit_order_trend': '趋势限价单',
    'manual': '手动开仓',
    # 超级大脑决策信号
    'SMART_BRAIN_20': '超级大脑(20分)',
    'SMART_BRAIN_35': '超级大脑(35分)',
    'SMART_BRAIN_40': '超级大脑(40分)',
    'SMART_BRAIN_45': '超级大脑(45分)',
    'SMART_BRAIN_60': '超级大脑(60分)',
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
    'rsi_filter': 'RSI过滤',
    'ema_diff_small': 'EMA差值过小',
    'position_exists': '持仓已存在',
    'execution_failed': '执行失败',
}


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(**db_config, autocommit=True)


def parse_close_reason(notes: str) -> tuple:
    """
    解析平仓原因，返回 (代码, 中文名称)

    数据库中的格式示例：
    - "死叉反转(EMA9 > EMA26)"
    - "金叉反转(EMA9 < EMA26)"
    - "manual_close_all"
    - "硬止损"
    - "移动止盈"
    - "5M EMA死叉止损(...)"
    - "移动止盈(距离2.00%，回撤0.79% >= 0.3%)"
    """
    if not notes:
        return 'unknown', '未知'

    notes_lower = notes.lower()

    # 超级大脑智能顶底识别 (优先处理)
    if notes.startswith('TOP_DETECTED('):
        # 提取参数: TOP_DETECTED(高点回落1.4%,盈利-0.4%)
        import re
        match = re.match(r'TOP_DETECTED\((.*?)\)$', notes)
        if match:
            params = match.group(1)
            return 'top_detected', f'智能顶部识别({params})'
        return 'top_detected', '智能顶部识别'

    if notes.startswith('BOTTOM_DETECTED('):
        # 提取参数: BOTTOM_DETECTED(低点反弹1.8%,盈利+1.1%)
        import re
        match = re.match(r'BOTTOM_DETECTED\((.*?)\)$', notes)
        if match:
            params = match.group(1)
            return 'bottom_detected', f'智能底部识别({params})'
        return 'bottom_detected', '智能底部识别'

    # 超级大脑止损止盈（大写格式）
    if notes == 'STOP_LOSS':
        return 'stop_loss', '止损'
    if notes == 'TAKE_PROFIT':
        return 'take_profit', '固定止盈'

    # 英文代码直接匹配
    if notes in CLOSE_REASON_MAP:
        return notes, CLOSE_REASON_MAP[notes]

    # 特殊处理一键平仓
    if 'manual_close_all' in notes:
        return 'manual_close_all', '一键平仓'

    # 中文关键字匹配 (按优先级从高到低)
    if '死叉反转' in notes:
        return 'death_cross_reversal', '死叉反转平仓'
    if '金叉反转' in notes:
        return 'golden_cross_reversal', '金叉反转平仓'
    if '硬止损' in notes:
        return 'hard_stop_loss', '硬止损'
    if '移动止损' in notes:
        return 'trailing_stop_loss', '移动止损'
    if '移动止盈' in notes:
        return 'trailing_take_profit', '移动止盈'
    if '最大止盈' in notes or '达到最大' in notes:
        return 'max_take_profit', '最大止盈'
    # 简单的止盈止损 (必须放在具体类型之后匹配)
    if notes == '止盈' or '止盈' in notes:
        return 'take_profit', '止盈'
    if notes == '止损' or '止损' in notes:
        return 'stop_loss', '止损'
    if '5M' in notes and ('死叉' in notes or '金叉' in notes):
        if '死叉' in notes:
            return '5m_death_cross_sl', '5分钟死叉止损'
        else:
            return '5m_golden_cross_sl', '5分钟金叉止损'
    if 'EMA' in notes and '收窄' in notes:
        return 'ema_diff_narrowing_tp', 'EMA差值收窄止盈'
    if '手动' in notes:
        return 'manual', '手动平仓'
    if '强平' in notes or '强制' in notes:
        return 'liquidation', '强制平仓'
    if '同步' in notes:
        return 'sync_close', '同步平仓'

    # 对冲止损平仓 (新增)
    if '|hedge_loss_cut' in notes or 'hedge_loss_cut' in notes:
        return 'hedge_loss_cut', '对冲止损平仓'

    # 反向信号平仓 (新增)
    if '|reverse_signal' in notes or 'reverse_signal' in notes:
        return 'reverse_signal', '反向信号平仓'

    # 无法识别，返回原始值（截取前20字符）
    display = notes[:20] + '...' if len(notes) > 20 else notes
    return 'other', display


def parse_entry_reason(entry_reason: str, entry_signal_type: str) -> tuple:
    """
    解析开仓原因，返回 (代码, 中文名称)

    优先使用 entry_signal_type 字段，如果为空则解析 entry_reason
    """
    # 优先使用 entry_signal_type
    if entry_signal_type:
        signal_type = entry_signal_type.strip()

        # 直接匹配
        if signal_type in ENTRY_REASON_MAP:
            return signal_type, ENTRY_REASON_MAP[signal_type]

        # 超级大脑信号类型匹配 (支持整数和浮点数格式)
        if 'SMART_BRAIN_' in signal_type:
            import re
            # 提取分数 (支持 SMART_BRAIN_30 和 SMART_BRAIN_30.0 格式)
            match = re.search(r'SMART_BRAIN[_-]?(\d+(?:\.\d+)?)', signal_type)
            if match:
                score = float(match.group(1))
                score_int = int(score)
                return f'SMART_BRAIN_{score_int}', f'超级大脑({score_int}分)'

        # 包含匹配
        if 'sustained_trend' in signal_type:
            if 'FORWARD' in signal_type:
                return 'sustained_trend_FORWARD', '顺向持续趋势'
            elif 'REVERSE' in signal_type:
                return 'sustained_trend_REVERSE', '反转持续趋势'
            else:
                return 'sustained_trend', '持续趋势'
        if 'golden_cross' in signal_type.lower():
            return 'golden_cross', '金叉信号'
        if 'death_cross' in signal_type.lower():
            return 'death_cross', '死叉信号'

    # 解析 entry_reason
    if entry_reason:
        reason = entry_reason.strip()

        if '金叉' in reason:
            return 'golden_cross', '金叉信号'
        if '死叉' in reason:
            return 'death_cross', '死叉信号'
        if 'sustained' in reason.lower() or '持续' in reason:
            if 'FORWARD' in reason or '顺向' in reason:
                return 'sustained_trend_FORWARD', '顺向持续趋势'
            elif 'REVERSE' in reason or '反转' in reason:
                return 'sustained_trend_REVERSE', '反转持续趋势'
            return 'sustained_trend', '持续趋势'
        if '手动' in reason or 'manual' in reason.lower():
            return 'manual', '手动开仓'
        if '限价' in reason or 'limit' in reason.lower():
            return 'limit_order', '限价单'

    return 'unknown', '未知'


def parse_cancel_reason(reason: str, notes: str = None) -> tuple:
    """
    解析取消原因，返回 (代码, 中文名称)

    会结合 notes 字段来提取详细的取消原因
    """
    if not reason:
        return 'unknown', '未知'

    reason_lower = reason.lower()

    # 从 notes 中提取详细原因
    detail = ''
    if notes:
        # notes 格式: " VALIDATION_FAILED: 趋势末端(差值缩小36.3%); 弱趋势(EMA差值0.048%<0.05%)"
        # 或: " TREND_REVERSAL: 死叉(做多): EMA9=5.7346 < EMA26=5.7347, 差值=0.00%"
        if 'VALIDATION_FAILED:' in notes:
            detail = notes.split('VALIDATION_FAILED:')[-1].strip()
        elif 'TREND_REVERSAL:' in notes:
            detail = notes.split('TREND_REVERSAL:')[-1].strip()
        elif 'RSI_FILTER:' in notes:
            detail = notes.split('RSI_FILTER:')[-1].strip()
        elif 'EMA_DIFF_SMALL:' in notes:
            detail = notes.split('EMA_DIFF_SMALL:')[-1].strip()
        elif 'TIMEOUT' in notes:
            detail = '超时'

    # 直接匹配英文代码
    if reason == 'validation_failed':
        # 解析详细原因
        if detail:
            # 尝试从英文关键词解析
            reasons = []
            if 'EMA' in detail:
                if '<' in detail or '>' in detail:
                    reasons.append('EMA方向不符')
                if '%<' in detail or '差值' in detail or 'diff' in detail.lower():
                    reasons.append('EMA差值过小')
            if '缩小' in detail or 'shrink' in detail.lower() or '末端' in detail:
                reasons.append('趋势末端')
            if '弱' in detail or 'weak' in detail.lower():
                reasons.append('弱趋势')
            if reasons:
                return 'validation_failed', '自检: ' + '+'.join(reasons)
        return 'validation_failed', '自检未通过'

    if reason == 'trend_reversal':
        if detail:
            if 'EMA9' in detail:
                # 解析EMA数值
                import re
                match = re.search(r'EMA9[=:]?\s*([\d.]+).*EMA26[=:]?\s*([\d.]+)', detail)
                if match:
                    ema9 = float(match.group(1))
                    ema26 = float(match.group(2))
                    if ema9 < ema26:
                        return 'trend_reversal', '死叉反转'
                    else:
                        return 'trend_reversal', '金叉反转'
        return 'trend_reversal', '趋势转向'

    if reason == 'timeout':
        return 'timeout', '超时取消'

    if reason == 'rsi_filter':
        if detail and 'RSI' in detail:
            import re
            match = re.search(r'RSI[=:]?\s*([\d.]+)', detail)
            if match:
                rsi = float(match.group(1))
                if rsi > 60:
                    return 'rsi_filter', f'RSI超买({rsi:.0f})'
                elif rsi < 40:
                    return 'rsi_filter', f'RSI超卖({rsi:.0f})'
        return 'rsi_filter', 'RSI过滤'

    if reason == 'ema_diff_small':
        return 'ema_diff_small', 'EMA差值过小'

    if reason == 'position_exists':
        return 'position_exists', '持仓已存在'

    if reason == 'execution_failed':
        return 'execution_failed', '执行失败'

    if reason == 'manual':
        return 'manual', '手动取消'

    # 关键字匹配（兼容旧数据）
    if 'timeout' in reason_lower:
        return 'timeout', '超时取消'
    if 'validation' in reason_lower or '自检' in reason:
        return 'validation_failed', '自检未通过'
    if 'reversal' in reason_lower or '转向' in reason:
        return 'trend_reversal', '趋势转向'
    if 'rsi' in reason_lower:
        return 'rsi_filter', 'RSI过滤'
    if 'ema_diff' in reason_lower or 'EMA差值' in reason:
        return 'ema_diff_small', 'EMA差值过小'
    if 'position' in reason_lower or '持仓' in reason:
        return 'position_exists', '持仓已存在'
    if 'manual' in reason_lower or '手动' in reason:
        return 'manual', '手动取消'

    # 无法识别
    display = reason[:20] + '...' if len(reason) > 20 else reason
    return 'other', display


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

        time_threshold = datetime.utcnow() - timedelta(hours=hours)

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
                AVG(TIMESTAMPDIFF(MINUTE, open_time, close_time)) as avg_holding_minutes
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

        # 按交易对统计胜负
        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN realized_pnl = 0 THEN 1 ELSE 0 END) as break_even,
                SUM(realized_pnl) as total_pnl,
                AVG(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE NULL END) as avg_win,
                AVG(CASE WHEN realized_pnl < 0 THEN realized_pnl ELSE NULL END) as avg_loss,
                MAX(realized_pnl) as max_win,
                MIN(realized_pnl) as max_loss
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed' AND close_time >= %s
            GROUP BY symbol
            ORDER BY total_pnl DESC
        """, (account_id, time_threshold))
        symbol_stats = cursor.fetchall()

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

        # 处理交易对统计数据
        symbol_performance = []
        for row in symbol_stats:
            total = row['total_trades'] or 0
            wins = row['wins'] or 0
            losses = row['losses'] or 0
            win_rate_sym = (wins / total * 100) if total > 0 else 0

            symbol_performance.append({
                "symbol": row['symbol'],
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "break_even": row['break_even'] or 0,
                "win_rate": round(win_rate_sym, 1),
                "total_pnl": round(float(row['total_pnl'] or 0), 2),
                "avg_win": round(float(row['avg_win'] or 0), 2),
                "avg_loss": round(float(row['avg_loss'] or 0), 2),
                "max_win": round(float(row['max_win'] or 0), 2),
                "max_loss": round(float(row['max_loss'] or 0), 2)
            })

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
                "avg_holding_minutes": round(float(position_stats['avg_holding_minutes'] or 0), 1),
                "symbol_performance": symbol_performance
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
    sort_by: str = Query(default="time", description="排序方式: time/pnl"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=100, ge=10, le=200, description="每页数量")
):
    """
    获取24H成交订单列表（分页）

    包含: 时间、交易对、方向、开仓价、平仓价、数量、杠杆、盈亏、盈亏%、持仓时长、开仓原因、平仓原因
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.utcnow() - timedelta(hours=hours)

        # 构建筛选条件
        filter_condition = ""
        if filter_type == "profit":
            filter_condition = "AND realized_pnl > 0"
        elif filter_type == "loss":
            filter_condition = "AND realized_pnl < 0"

        # 构建排序
        order_by = "close_time DESC" if sort_by == "time" else "realized_pnl DESC"

        # 获取总数
        cursor.execute(f"""
            SELECT COUNT(*) as total
            FROM futures_positions
            WHERE account_id = %s AND status = 'closed' AND close_time >= %s
            {filter_condition}
        """, (account_id, time_threshold))
        total_count = cursor.fetchone()['total']

        # 分页查询
        offset = (page - 1) * page_size
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
            LIMIT %s OFFSET %s
        """, (account_id, time_threshold, page_size, offset))

        positions = cursor.fetchall()

        cursor.close()
        conn.close()

        # 处理数据，添加中文映射
        trades = []
        for pos in positions:
            # 使用新的解析函数
            close_reason_code, close_reason_cn = parse_close_reason(pos['close_reason'])
            entry_reason_code, entry_reason_cn = parse_entry_reason(
                pos['entry_reason'],
                pos['entry_signal_type']
            )

            # 计算实际持仓时长（分钟）
            holding_minutes = 0
            if pos['open_time'] and pos['close_time']:
                delta = pos['close_time'] - pos['open_time']
                holding_minutes = int(delta.total_seconds() / 60)

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
                "holding_minutes": holding_minutes,
                "entry_reason_code": entry_reason_code,
                "entry_reason_cn": entry_reason_cn,
                "close_reason_code": close_reason_code,
                "close_reason_cn": close_reason_cn,
                "close_reason_detail": pos['close_reason'],
                "open_time": pos['open_time'].isoformat() if pos['open_time'] else None,
                "close_time": pos['close_time'].isoformat() if pos['close_time'] else None
            })

        # 计算分页信息
        total_pages = (total_count + page_size - 1) // page_size

        return {
            "success": True,
            "data": {
                "trades": trades,
                "count": len(trades),
                "total_count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
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
    account_id: int = Query(default=2, description="账户ID"),
    page: int = Query(default=1, ge=1, description="页码"),
    page_size: int = Query(default=100, ge=10, le=200, description="每页数量")
):
    """
    获取24H取消订单列表及原因分析（分页）

    返回:
    - 取消总数
    - 各取消原因统计
    - 取消订单详情列表（分页）
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        time_threshold = datetime.utcnow() - timedelta(hours=hours)

        # 获取总数
        cursor.execute("""
            SELECT COUNT(*) as total
            FROM futures_orders
            WHERE account_id = %s AND status = 'CANCELLED' AND created_at >= %s
        """, (account_id, time_threshold))
        total_cancelled = cursor.fetchone()['total']

        # 获取所有取消订单用于统计原因分布
        cursor.execute("""
            SELECT cancellation_reason, notes
            FROM futures_orders
            WHERE account_id = %s AND status = 'CANCELLED' AND created_at >= %s
        """, (account_id, time_threshold))
        all_reasons = cursor.fetchall()

        # 统计取消原因分布
        reason_stats = {}
        for row in all_reasons:
            reason_code, reason_cn = parse_cancel_reason(row['cancellation_reason'], row.get('notes'))
            if reason_code not in reason_stats:
                reason_stats[reason_code] = {
                    "code": reason_code,
                    "name_cn": reason_cn,
                    "count": 0
                }
            reason_stats[reason_code]["count"] += 1

        # 分页查询取消订单详情
        offset = (page - 1) * page_size
        cursor.execute("""
            SELECT
                id, order_id, symbol, side, order_type, leverage,
                price, quantity, margin, cancellation_reason, notes,
                created_at, canceled_at
            FROM futures_orders
            WHERE account_id = %s AND status = 'CANCELLED' AND created_at >= %s
            ORDER BY canceled_at DESC
            LIMIT %s OFFSET %s
        """, (account_id, time_threshold, page_size, offset))

        orders = cursor.fetchall()

        cursor.close()
        conn.close()

        # 处理订单列表
        cancelled_list = []
        for order in orders:
            reason_code, reason_cn = parse_cancel_reason(order['cancellation_reason'], order.get('notes'))

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
        reason_distribution = []
        for code, stats in sorted(reason_stats.items(), key=lambda x: x[1]['count'], reverse=True):
            stats["percentage"] = round(stats["count"] / total_cancelled * 100, 1) if total_cancelled > 0 else 0
            reason_distribution.append(stats)

        # 计算分页信息
        total_pages = (total_cancelled + page_size - 1) // page_size

        return {
            "success": True,
            "data": {
                "total_cancelled": total_cancelled,
                "reason_distribution": reason_distribution,
                "cancelled_orders": cancelled_list,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages
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

        time_threshold = datetime.utcnow() - timedelta(hours=hours)

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

            # 开仓原因统计（使用新的解析函数,区分多空方向）
            entry_code, entry_cn = parse_entry_reason(pos['entry_reason'], pos['entry_signal_type'])
            side = pos['position_side']
            side_cn = "做多" if side == 'LONG' else "做空"

            # 创建组合键: code_LONG 或 code_SHORT
            entry_key = f"{entry_code}_{side}"
            entry_display = f"{entry_cn}({side_cn})"

            if entry_key not in entry_stats:
                entry_stats[entry_key] = {
                    "code": entry_key,
                    "name_cn": entry_display,
                    "count": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_pnl": 0
                }
            entry_stats[entry_key]["count"] += 1
            entry_stats[entry_key]["total_pnl"] += pnl
            if is_profit:
                entry_stats[entry_key]["wins"] += 1
            else:
                entry_stats[entry_key]["losses"] += 1

            # 平仓原因统计（使用新的解析函数）
            close_code, close_cn = parse_close_reason(pos['close_reason'])
            if close_code not in close_stats:
                close_stats[close_code] = {
                    "code": close_code,
                    "name_cn": close_cn,
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

        time_threshold = datetime.utcnow() - timedelta(hours=hours)

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
            close_code, _ = parse_close_reason(pos['close_reason'])
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
                _, reason_cn = parse_cancel_reason(main_reason['cancellation_reason'])
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
