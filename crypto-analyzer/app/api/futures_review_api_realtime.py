#!/usr/bin/env python3
"""
实时机会分析 API
Real-time Opportunity Analysis API

提供实时的信号分析和持仓对比：
- 当前信号评分
- 已开仓的信号
- 错过的强信号
- 错过原因分析
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
from app.services.signal_analysis_service import SignalAnalysisService

config = load_config()
db_config = config['database']['mysql']


def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


@router.get("/realtime-opportunity-analysis")
async def get_realtime_opportunity_analysis(
    account_id: int = Query(2, description="账户ID: 1=实盘, 2=模拟")
):
    """
    实时机会分析API - 展示当前信号评分和持仓对比

    返回数据包括:
    - 当前所有交易对的信号评分
    - 已开仓的信号（捕获到的机会）
    - 未开仓的强信号（错过的机会）
    - 错过原因分析（黑名单、评分不够、资金不足等）
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 初始化信号分析服务
        signal_service = SignalAnalysisService(db_config)

        # 1. 获取监控列表中的所有交易对
        cursor.execute("""
            SELECT symbol
            FROM monitored_symbols
            WHERE is_active = 1
            ORDER BY symbol
        """)
        monitored_symbols = [row['symbol'] for row in cursor.fetchall()]

        if not monitored_symbols:
            return {
                "success": True,
                "data": {
                    "has_data": False,
                    "message": "暂无监控交易对"
                }
            }

        # 2. 获取当前所有持仓
        cursor.execute("""
            SELECT
                symbol,
                position_side,
                margin,
                quantity,
                entry_price,
                unrealized_pnl,
                created_at,
                entry_reason
            FROM futures_positions
            WHERE account_id = %s
            AND status IN ('open', 'building')
        """, (account_id,))

        current_positions = cursor.fetchall()
        position_symbols = {pos['symbol']: pos for pos in current_positions}

        # 3. 获取信号黑名单
        cursor.execute("""
            SELECT symbol, reason, created_at
            FROM signal_blacklist
            WHERE is_active = 1
        """)
        blacklist = {row['symbol']: row['reason'] for row in cursor.fetchall()}

        # 4. 获取账户余额
        cursor.execute("""
            SELECT current_balance
            FROM futures_trading_accounts
            WHERE id = %s
        """, (account_id,))
        account_balance = cursor.fetchone()
        available_balance = float(account_balance['current_balance']) if account_balance else 0

        # 5. 分析每个交易对的信号强度
        all_signals = []
        captured_signals = []
        missed_opportunities = []

        for symbol in monitored_symbols:
            # 分析K线强度
            strength_1h = signal_service.analyze_kline_strength(symbol, '1h', 24)
            strength_15m = signal_service.analyze_kline_strength(symbol, '15m', 24)
            strength_5m = signal_service.analyze_kline_strength(symbol, '5m', 24)

            if not all([strength_1h, strength_15m, strength_5m]):
                continue

            # 计算综合信号强度
            net_power_1h = strength_1h['net_power']
            net_power_15m = strength_15m['net_power']
            net_power_5m = strength_5m['net_power']

            # 判断信号方向和强度
            signal_direction = None
            signal_strength = 0
            signal_quality = "弱"

            # 强多信号：1H和15M都看多
            if net_power_1h >= 3 and net_power_15m >= 2:
                signal_direction = 'LONG'
                signal_strength = abs(net_power_1h) + abs(net_power_15m) * 0.5
                if net_power_1h >= 5 and net_power_15m >= 3:
                    signal_quality = "强"
                else:
                    signal_quality = "中"

            # 强空信号：1H和15M都看空
            elif net_power_1h <= -3 and net_power_15m <= -2:
                signal_direction = 'SHORT'
                signal_strength = abs(net_power_1h) + abs(net_power_15m) * 0.5
                if net_power_1h <= -5 and net_power_15m <= -3:
                    signal_quality = "强"
                else:
                    signal_quality = "中"

            signal_data = {
                'symbol': symbol,
                'signal_direction': signal_direction,
                'signal_strength': signal_strength,
                'signal_quality': signal_quality,
                'net_power_1h': net_power_1h,
                'net_power_15m': net_power_15m,
                'net_power_5m': net_power_5m,
                'kline_1h': strength_1h,
                'kline_15m': strength_15m,
                'kline_5m': strength_5m,
                'has_position': symbol in position_symbols,
                'position_info': position_symbols.get(symbol),
                'in_blacklist': symbol in blacklist,
                'blacklist_reason': blacklist.get(symbol)
            }

            all_signals.append(signal_data)

            # 6. 判断是否捕获或错过
            if signal_direction:  # 有明确信号
                if symbol in position_symbols:
                    # 已开仓
                    pos = position_symbols[symbol]
                    is_correct_direction = (
                        (signal_direction == 'LONG' and pos['position_side'] == 'LONG') or
                        (signal_direction == 'SHORT' and pos['position_side'] == 'SHORT')
                    )

                    captured_signals.append({
                        **signal_data,
                        'captured': True,
                        'correct_direction': is_correct_direction,
                        'status': '✅ 正确捕获' if is_correct_direction else '⚠️ 方向错误'
                    })
                else:
                    # 未开仓，分析原因
                    miss_reasons = []

                    if symbol in blacklist:
                        miss_reasons.append(f'黑名单: {blacklist[symbol]}')

                    if signal_quality == "弱":
                        miss_reasons.append('信号强度不足')
                    elif signal_quality == "中" and signal_strength < 8:
                        miss_reasons.append('评分未达开仓阈值')

                    if available_balance < 100:
                        miss_reasons.append('资金不足')

                    if not miss_reasons:
                        miss_reasons.append('未产生开仓信号或系统未识别')

                    # 只记录强信号和中信号
                    if signal_quality in ["强", "中"]:
                        missed_opportunities.append({
                            **signal_data,
                            'captured': False,
                            'miss_reasons': miss_reasons,
                            'main_reason': miss_reasons[0]
                        })

        # 7. 按信号强度排序
        all_signals.sort(key=lambda x: abs(x['signal_strength']), reverse=True)
        captured_signals.sort(key=lambda x: abs(x['signal_strength']), reverse=True)
        missed_opportunities.sort(key=lambda x: abs(x['signal_strength']), reverse=True)

        # 8. 统计错过原因
        miss_reason_stats = {}
        for missed in missed_opportunities:
            for reason in missed['miss_reasons']:
                if reason not in miss_reason_stats:
                    miss_reason_stats[reason] = {
                        'count': 0,
                        'examples': []
                    }
                miss_reason_stats[reason]['count'] += 1
                if len(miss_reason_stats[reason]['examples']) < 3:
                    miss_reason_stats[reason]['examples'].append({
                        'symbol': missed['symbol'],
                        'direction': missed['signal_direction'],
                        'strength': round(missed['signal_strength'], 1),
                        'quality': missed['signal_quality']
                    })

        # 9. 返回数据
        cursor.close()
        conn.close()

        return {
            "success": True,
            "data": {
                "has_data": True,
                "analysis_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "summary": {
                    "total_monitored": len(monitored_symbols),
                    "total_signals": len([s for s in all_signals if s['signal_direction']]),
                    "strong_signals": len([s for s in all_signals if s['signal_quality'] == '强']),
                    "captured": len(captured_signals),
                    "correct_captures": len([s for s in captured_signals if s.get('correct_direction', False)]),
                    "wrong_direction": len([s for s in captured_signals if not s.get('correct_direction', True)]),
                    "missed": len(missed_opportunities),
                    "capture_rate": round(len(captured_signals) / len([s for s in all_signals if s['signal_direction']]) * 100, 1) if [s for s in all_signals if s['signal_direction']] else 0,
                    "available_balance": available_balance,
                    "blacklist_count": len(blacklist)
                },
                "all_signals": all_signals[:30],  # 前30个信号
                "captured_signals": captured_signals,
                "missed_opportunities": missed_opportunities[:20],  # 前20个错过的机会
                "miss_reason_stats": miss_reason_stats
            }
        }

    except Exception as e:
        logger.error(f"获取实时机会分析失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
