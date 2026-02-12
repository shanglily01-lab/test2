"""
现货交易 API 接口
提供现货持仓查询、交易历史等功能
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timedelta
import pymysql
from functools import lru_cache
from loguru import logger

router = APIRouter(prefix="/api/spot-trading", tags=["现货交易"])

# ==================== 依赖注入 ====================

@lru_cache()
def get_config():
    """缓存配置文件读取"""
    from app.utils.config_loader import load_config
    return load_config()

def get_db_config():
    """获取数据库配置"""
    config = get_config()
    return config.get('database', {}).get('mysql', {})

def get_db_connection():
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


# ==================== 响应模型 ====================

class SpotPosition(BaseModel):
    """现货持仓"""
    id: int
    symbol: str
    entry_price: float
    quantity: float
    total_cost: float
    current_price: Optional[float] = None
    current_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    signal_details: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SpotHistoryPosition(BaseModel):
    """现货历史记录"""
    id: int
    symbol: str
    entry_price: float
    exit_price: float
    quantity: float
    total_cost: float
    pnl: float
    pnl_pct: float
    close_reason: Optional[str] = None
    signal_details: Optional[str] = None
    created_at: datetime
    closed_at: datetime


class SpotSummary(BaseModel):
    """现货交易概览"""
    total_positions: int
    total_cost: float
    total_value: float
    total_unrealized_pnl: float
    total_unrealized_pnl_pct: float
    history_total_pnl: float
    history_win_count: int
    history_loss_count: int
    history_win_rate: float


# ==================== API 接口 ====================

@router.get("/positions", response_model=List[SpotPosition])
async def get_spot_positions():
    """
    获取当前现货持仓列表

    Returns:
        现货持仓列表
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 查询所有open状态的持仓
        cursor.execute("""
            SELECT
                id, symbol, entry_price, avg_entry_price, quantity, total_cost,
                take_profit_price, stop_loss_price, signal_details,
                created_at, updated_at
            FROM spot_positions
            WHERE status = 'open'
            ORDER BY created_at DESC
        """)

        positions = cursor.fetchall()

        # 获取当前价格（从WebSocket价格服务或数据库）
        result = []
        for pos in positions:
            current_price = await _get_current_price(cursor, pos['symbol'])

            current_value = float(pos['quantity']) * current_price if current_price else None
            unrealized_pnl = (current_value - float(pos['total_cost'])) if current_value else None
            unrealized_pnl_pct = (unrealized_pnl / float(pos['total_cost']) * 100) if unrealized_pnl else None

            result.append(SpotPosition(
                id=pos['id'],
                symbol=pos['symbol'],
                entry_price=float(pos['entry_price']),
                quantity=float(pos['quantity']),
                total_cost=float(pos['total_cost']),
                current_price=current_price,
                current_value=current_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                take_profit_price=float(pos['take_profit_price']) if pos['take_profit_price'] else None,
                stop_loss_price=float(pos['stop_loss_price']) if pos['stop_loss_price'] else None,
                signal_details=pos['signal_details'],
                created_at=pos['created_at'],
                updated_at=pos['updated_at']
            ))

        cursor.close()
        conn.close()

        return result

    except Exception as e:
        logger.error(f"获取现货持仓失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取持仓失败: {str(e)}")


@router.get("/history", response_model=List[SpotHistoryPosition])
async def get_spot_history(
    limit: int = Query(100, ge=1, le=500, description="返回记录数"),
    offset: int = Query(0, ge=0, description="跳过记录数"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD")
):
    """
    获取现货交易历史记录

    Args:
        limit: 返回记录数（默认100，最大500）
        offset: 跳过记录数（用于分页）
        start_date: 开始日期（可选）
        end_date: 结束日期（可选）

    Returns:
        历史交易记录列表
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 构建查询条件
        where_conditions = ["status = 'closed'"]
        params = []

        if start_date:
            where_conditions.append("DATE(closed_at) >= %s")
            params.append(start_date)

        if end_date:
            where_conditions.append("DATE(closed_at) <= %s")
            params.append(end_date)

        where_clause = " AND ".join(where_conditions)

        # 查询历史记录
        query = f"""
            SELECT
                id, symbol, entry_price, exit_price, quantity, total_cost,
                pnl, pnl_pct, close_reason, signal_details,
                created_at, closed_at
            FROM spot_positions
            WHERE {where_clause}
            ORDER BY closed_at DESC
            LIMIT %s OFFSET %s
        """

        params.extend([limit, offset])
        cursor.execute(query, params)

        history = cursor.fetchall()

        result = []
        for rec in history:
            result.append(SpotHistoryPosition(
                id=rec['id'],
                symbol=rec['symbol'],
                entry_price=float(rec['entry_price']),
                exit_price=float(rec['exit_price']),
                quantity=float(rec['quantity']),
                total_cost=float(rec['total_cost']),
                pnl=float(rec['pnl']),
                pnl_pct=float(rec['pnl_pct']),
                close_reason=rec['close_reason'],
                signal_details=rec['signal_details'],
                created_at=rec['created_at'],
                closed_at=rec['closed_at']
            ))

        cursor.close()
        conn.close()

        return result

    except Exception as e:
        logger.error(f"获取现货历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")


@router.get("/summary", response_model=SpotSummary)
async def get_spot_summary():
    """
    获取现货交易概览统计

    Returns:
        现货交易统计数据
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. 统计当前持仓
        cursor.execute("""
            SELECT
                COUNT(*) as total_positions,
                SUM(total_cost) as total_cost,
                SUM(quantity) as total_quantity
            FROM spot_positions
            WHERE status = 'open'
        """)
        open_stats = cursor.fetchone()

        # 2. 获取当前持仓的市值和未实现盈亏
        cursor.execute("""
            SELECT id, symbol, quantity, total_cost
            FROM spot_positions
            WHERE status = 'open'
        """)
        open_positions = cursor.fetchall()

        total_value = 0
        total_unrealized_pnl = 0

        for pos in open_positions:
            current_price = await _get_current_price(cursor, pos['symbol'])
            if current_price:
                value = float(pos['quantity']) * current_price
                total_value += value
                total_unrealized_pnl += (value - float(pos['total_cost']))

        total_cost = float(open_stats['total_cost']) if open_stats['total_cost'] else 0
        total_unrealized_pnl_pct = (total_unrealized_pnl / total_cost * 100) if total_cost > 0 else 0

        # 3. 统计历史交易
        cursor.execute("""
            SELECT
                SUM(pnl) as total_pnl,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
                COUNT(*) as total_count
            FROM spot_positions
            WHERE status = 'closed'
        """)
        history_stats = cursor.fetchone()

        history_total_pnl = float(history_stats['total_pnl']) if history_stats['total_pnl'] else 0
        win_count = history_stats['win_count'] or 0
        loss_count = history_stats['loss_count'] or 0
        total_count = history_stats['total_count'] or 0
        win_rate = (win_count / total_count * 100) if total_count > 0 else 0

        cursor.close()
        conn.close()

        return SpotSummary(
            total_positions=open_stats['total_positions'] or 0,
            total_cost=total_cost,
            total_value=total_value,
            total_unrealized_pnl=total_unrealized_pnl,
            total_unrealized_pnl_pct=total_unrealized_pnl_pct,
            history_total_pnl=history_total_pnl,
            history_win_count=win_count,
            history_loss_count=loss_count,
            history_win_rate=win_rate
        )

    except Exception as e:
        logger.error(f"获取现货概览失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取概览失败: {str(e)}")


# ==================== 辅助函数 ====================

async def _get_current_price(cursor, symbol: str) -> Optional[float]:
    """
    获取币种当前价格
    优先从WebSocket价格服务获取，失败则从数据库获取
    """
    try:
        # 尝试从价格缓存获取（如果有WebSocket服务）
        from app.services.price_cache_service import get_global_price_cache
        price_cache = get_global_price_cache()
        cached_price = price_cache.get_price(symbol)
        if cached_price:
            return cached_price
    except Exception:
        pass

    # 从数据库获取最新价格
    try:
        binance_symbol = symbol.replace('/', '')
        cursor.execute("""
            SELECT close_price
            FROM kline_data
            WHERE symbol = %s AND timeframe = '1m' AND exchange = 'binance'
            ORDER BY open_time DESC
            LIMIT 1
        """, (binance_symbol,))

        result = cursor.fetchone()
        if result:
            return float(result['close_price'])
    except Exception as e:
        logger.warning(f"获取价格失败 {symbol}: {e}")

    return None
