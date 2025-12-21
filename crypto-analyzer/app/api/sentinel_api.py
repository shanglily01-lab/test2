"""
哨兵单 API
提供哨兵单列表查询等功能
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, List
from datetime import datetime
import logging
import pymysql

from app.utils.config_loader import load_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/sentinel', tags=['Sentinel Orders'])

# 加载数据库配置
def get_db_config():
    """获取数据库配置"""
    try:
        config = load_config()
        return config.get('database', {}).get('mysql', {})
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        return {}


def get_db_connection():
    """获取数据库连接"""
    db_config = get_db_config()
    return pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=int(db_config.get('port', 3306)),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )


@router.get('/list')
async def get_sentinel_list(
    direction: Optional[str] = Query(None, description='方向过滤 (long/short)'),
    status: Optional[str] = Query(None, description='状态过滤 (open/win/loss)'),
    limit: int = Query(50, ge=1, le=200, description='返回数量限制')
):
    """
    获取哨兵单列表

    Args:
        direction: 方向过滤
        status: 状态过滤
        limit: 返回数量限制

    Returns:
        哨兵单列表
    """
    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            # 构建查询条件
            conditions = []
            params = []

            if direction:
                conditions.append("direction = %s")
                params.append(direction)

            if status:
                conditions.append("status = %s")
                params.append(status)

            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

            # 查询哨兵单
            query = f"""
                SELECT
                    id,
                    direction,
                    symbol,
                    entry_price,
                    stop_loss_price,
                    take_profit_price,
                    stop_loss_pct,
                    take_profit_pct,
                    status,
                    close_price,
                    close_reason,
                    created_at,
                    closed_at,
                    strategy_id
                FROM sentinel_orders
                {where_clause}
                ORDER BY
                    CASE WHEN status = 'open' THEN 0 ELSE 1 END,
                    created_at DESC
                LIMIT %s
            """
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

            # 获取当前价格（用于计算浮动盈亏）
            open_symbols = list(set(row['symbol'] for row in rows if row['status'] == 'open'))
            current_prices = {}

            if open_symbols:
                # 尝试从 WebSocket 获取价格
                try:
                    from app.services.binance_ws_price import get_ws_price_service
                    ws_service = get_ws_price_service()
                    if ws_service.is_healthy():
                        for symbol in open_symbols:
                            price = ws_service.get_price(symbol)
                            if price and price > 0:
                                current_prices[symbol] = price
                except Exception as e:
                    logger.warning(f"从 WebSocket 获取价格失败: {e}")

                # 从数据库获取缺失的价格
                missing_symbols = [s for s in open_symbols if s not in current_prices]
                if missing_symbols:
                    for symbol in missing_symbols:
                        cursor.execute("""
                            SELECT price FROM price_data
                            WHERE symbol = %s
                            ORDER BY timestamp DESC LIMIT 1
                        """, (symbol,))
                        price_row = cursor.fetchone()
                        if price_row:
                            current_prices[symbol] = float(price_row['price'])

            # 处理结果
            result = []
            for row in rows:
                item = {
                    'id': row['id'],
                    'direction': row['direction'],
                    'symbol': row['symbol'],
                    'entry_price': float(row['entry_price']) if row['entry_price'] else None,
                    'stop_loss_price': float(row['stop_loss_price']) if row['stop_loss_price'] else None,
                    'take_profit_price': float(row['take_profit_price']) if row['take_profit_price'] else None,
                    'stop_loss_pct': float(row['stop_loss_pct']) if row['stop_loss_pct'] else None,
                    'take_profit_pct': float(row['take_profit_pct']) if row['take_profit_pct'] else None,
                    'status': row['status'],
                    'close_price': float(row['close_price']) if row['close_price'] else None,
                    'close_reason': row['close_reason'],
                    'created_at': row['created_at'].isoformat() if row['created_at'] else None,
                    'closed_at': row['closed_at'].isoformat() if row['closed_at'] else None,
                    'strategy_id': row['strategy_id'],
                    'current_price': current_prices.get(row['symbol'])
                }
                result.append(item)

        connection.close()

        return {
            'success': True,
            'data': result,
            'total': len(result)
        }

    except Exception as e:
        logger.error(f"获取哨兵单列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/stats')
async def get_sentinel_stats():
    """
    获取哨兵单统计信息

    Returns:
        各方向的哨兵单统计
    """
    try:
        connection = get_db_connection()

        with connection.cursor() as cursor:
            # 统计各方向、各状态的数量
            cursor.execute("""
                SELECT
                    direction,
                    status,
                    COUNT(*) as count
                FROM sentinel_orders
                GROUP BY direction, status
            """)
            rows = cursor.fetchall()

        connection.close()

        # 组织统计数据
        stats = {
            'long': {'open': 0, 'win': 0, 'loss': 0, 'total': 0},
            'short': {'open': 0, 'win': 0, 'loss': 0, 'total': 0}
        }

        for row in rows:
            direction = row['direction']
            status = row['status']
            count = row['count']

            if direction in stats and status in stats[direction]:
                stats[direction][status] = count
                stats[direction]['total'] += count

        return {
            'success': True,
            'data': stats
        }

    except Exception as e:
        logger.error(f"获取哨兵单统计失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
