#!/usr/bin/env python3
"""
添加现货V2持仓API端点到 paper_trading_api.py
"""

# 要添加到 paper_trading_api.py 末尾的代码
spot_v2_api_code = '''

# ==================== 现货V2 (动态价格采样策略) API ====================

@router.get("/spot-v2/positions")
async def get_spot_v2_positions():
    """
    获取现货V2持仓列表 (动态价格采样策略)

    Returns:
        持仓列表和统计信息
    """
    import pymysql

    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        # 查询活跃持仓
        cursor.execute("""
            SELECT
                id,
                symbol,
                status,
                phase,
                current_batch,
                total_batches,
                total_quantity,
                total_cost,
                avg_entry_price,
                take_profit_price,
                stop_loss_price,
                sampling_start_time,
                building_start_time,
                holding_start_time,
                exit_sampling_start_time,
                exit_time,
                exit_price,
                realized_pnl,
                realized_pnl_pct,
                close_reason,
                created_at,
                updated_at
            FROM spot_positions_v2
            WHERE status = 'active'
            ORDER BY created_at DESC
        """)

        active_positions = cursor.fetchall()

        # 获取价格服务
        price_cache = get_global_price_cache()

        # 处理持仓数据
        positions = []
        for pos in active_positions:
            # 获取当前价格
            current_price = price_cache.get(pos['symbol']) if price_cache else None

            # 计算未实现盈亏
            if current_price and pos['avg_entry_price']:
                unrealized_pnl_pct = (current_price - float(pos['avg_entry_price'])) / float(pos['avg_entry_price']) * 100
                unrealized_pnl = (current_price - float(pos['avg_entry_price'])) * float(pos['total_quantity'])
            else:
                unrealized_pnl_pct = 0
                unrealized_pnl = 0

            # 计算持仓时长
            if pos['created_at']:
                hold_minutes = int((datetime.now() - pos['created_at']).total_seconds() / 60)
            else:
                hold_minutes = 0

            positions.append({
                "id": pos['id'],
                "symbol": pos['symbol'],
                "status": pos['status'],
                "phase": pos['phase'],
                "current_batch": pos['current_batch'],
                "total_batches": pos['total_batches'],
                "total_quantity": float(pos['total_quantity']) if pos['total_quantity'] else 0,
                "total_cost": float(pos['total_cost']) if pos['total_cost'] else 0,
                "avg_entry_price": float(pos['avg_entry_price']) if pos['avg_entry_price'] else 0,
                "current_price": current_price,
                "unrealized_pnl": unrealized_pnl,
                "unrealized_pnl_pct": unrealized_pnl_pct,
                "take_profit_price": float(pos['take_profit_price']) if pos['take_profit_price'] else None,
                "stop_loss_price": float(pos['stop_loss_price']) if pos['stop_loss_price'] else None,
                "hold_minutes": hold_minutes,
                "created_at": pos['created_at'].strftime('%Y-%m-%d %H:%M:%S') if pos['created_at'] else None,
                "sampling_start_time": pos['sampling_start_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['sampling_start_time'] else None,
                "building_start_time": pos['building_start_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['building_start_time'] else None,
                "holding_start_time": pos['holding_start_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['holding_start_time'] else None,
            })

        # 查询已平仓记录（最近10条）
        cursor.execute("""
            SELECT
                id,
                symbol,
                avg_entry_price,
                exit_price,
                total_quantity,
                total_cost,
                realized_pnl,
                realized_pnl_pct,
                close_reason,
                created_at,
                exit_time
            FROM spot_positions_v2
            WHERE status = 'closed'
            ORDER BY exit_time DESC
            LIMIT 10
        """)

        closed_positions = cursor.fetchall()

        # 处理已平仓数据
        closed = []
        for pos in closed_positions:
            hold_minutes = int((pos['exit_time'] - pos['created_at']).total_seconds() / 60) if pos['exit_time'] and pos['created_at'] else 0

            closed.append({
                "id": pos['id'],
                "symbol": pos['symbol'],
                "avg_entry_price": float(pos['avg_entry_price']) if pos['avg_entry_price'] else 0,
                "exit_price": float(pos['exit_price']) if pos['exit_price'] else 0,
                "total_quantity": float(pos['total_quantity']) if pos['total_quantity'] else 0,
                "total_cost": float(pos['total_cost']) if pos['total_cost'] else 0,
                "realized_pnl": float(pos['realized_pnl']) if pos['realized_pnl'] else 0,
                "realized_pnl_pct": float(pos['realized_pnl_pct']) if pos['realized_pnl_pct'] else 0,
                "close_reason": pos['close_reason'],
                "hold_minutes": hold_minutes,
                "created_at": pos['created_at'].strftime('%Y-%m-%d %H:%M:%S') if pos['created_at'] else None,
                "exit_time": pos['exit_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['exit_time'] else None,
            })

        # 统计信息
        cursor.execute("""
            SELECT
                COUNT(*) as total_positions,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active_count,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_count,
                SUM(CASE WHEN status = 'closed' AND realized_pnl > 0 THEN 1 ELSE 0 END) as winning_count,
                SUM(CASE WHEN status = 'closed' AND realized_pnl < 0 THEN 1 ELSE 0 END) as losing_count,
                SUM(CASE WHEN status = 'closed' THEN realized_pnl ELSE 0 END) as total_pnl
            FROM spot_positions_v2
        """)

        stats = cursor.fetchone()

        cursor.close()
        conn.close()

        return {
            "success": True,
            "positions": positions,
            "closed_positions": closed,
            "stats": {
                "total_positions": stats['total_positions'] or 0,
                "active_count": stats['active_count'] or 0,
                "closed_count": stats['closed_count'] or 0,
                "winning_count": stats['winning_count'] or 0,
                "losing_count": stats['losing_count'] or 0,
                "win_rate": (stats['winning_count'] / stats['closed_count'] * 100) if stats['closed_count'] and stats['closed_count'] > 0 else 0,
                "total_pnl": float(stats['total_pnl']) if stats['total_pnl'] else 0
            }
        }

    except Exception as e:
        logger.error(f"获取现货V2持仓失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/spot-v2/stats")
async def get_spot_v2_stats():
    """
    获取现货V2统计信息

    Returns:
        今日、本周、本月统计
    """
    import pymysql

    try:
        db_config = get_db_config()
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        # 今日统计
        cursor.execute("""
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_count,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl_pct) as avg_pnl_pct
            FROM spot_positions_v2
            WHERE status = 'closed'
                AND DATE(exit_time) = CURDATE()
        """)
        today = cursor.fetchone()

        # 本周统计
        cursor.execute("""
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_count,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl_pct) as avg_pnl_pct
            FROM spot_positions_v2
            WHERE status = 'closed'
                AND YEARWEEK(exit_time, 1) = YEARWEEK(CURDATE(), 1)
        """)
        week = cursor.fetchone()

        # 本月统计
        cursor.execute("""
            SELECT
                COUNT(*) as trade_count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_count,
                SUM(realized_pnl) as total_pnl,
                AVG(realized_pnl_pct) as avg_pnl_pct
            FROM spot_positions_v2
            WHERE status = 'closed'
                AND YEAR(exit_time) = YEAR(CURDATE())
                AND MONTH(exit_time) = MONTH(CURDATE())
        """)
        month = cursor.fetchone()

        cursor.close()
        conn.close()

        def process_stats(stats):
            if not stats or not stats['trade_count']:
                return {
                    "trade_count": 0,
                    "winning_count": 0,
                    "win_rate": 0,
                    "total_pnl": 0,
                    "avg_pnl_pct": 0
                }
            return {
                "trade_count": stats['trade_count'],
                "winning_count": stats['winning_count'] or 0,
                "win_rate": (stats['winning_count'] / stats['trade_count'] * 100) if stats['trade_count'] > 0 else 0,
                "total_pnl": float(stats['total_pnl']) if stats['total_pnl'] else 0,
                "avg_pnl_pct": float(stats['avg_pnl_pct']) if stats['avg_pnl_pct'] else 0
            }

        return {
            "success": True,
            "today": process_stats(today),
            "week": process_stats(week),
            "month": process_stats(month)
        }

    except Exception as e:
        logger.error(f"获取现货V2统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
'''

print("=" * 100)
print("添加现货V2 API端点代码")
print("=" * 100)
print()
print("将以下代码添加到 app/api/paper_trading_api.py 文件末尾:")
print()
print(spot_v2_api_code)
print()
print("=" * 100)
print("然后重启服务即可")
print("=" * 100)
