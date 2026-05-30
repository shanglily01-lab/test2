"""
现货交易 API 接口
提供现货持仓查询、交易历史等功能
"""

from app.utils.config_loader import get_db_config
from fastapi import APIRouter, HTTPException, Depends, Query, Body
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime, timedelta
import pymysql
from loguru import logger

router = APIRouter(prefix="/api/spot-trading", tags=["现货交易"])

# ==================== 依赖注入 ====================

def get_db_connection():
    """获取数据库连接（直接从环境变量）"""
    import os
    from dotenv import load_dotenv
    load_dotenv()

    return pymysql.connect(
        **get_db_config(),
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
    current_balance: float
    history_total_pnl: float
    history_win_count: int
    history_loss_count: int
    history_win_rate: float


# ==================== API 接口 ====================

@router.get("/positions", response_model=List[SpotPosition])
async def get_spot_positions():
    """
    获取当前现货持仓列表 (合并 DCA 策略自动仓 + 手动仓)

    Returns:
        现货持仓列表
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 查询 DCA 策略自动仓 (spot_positions 表)
        cursor.execute("""
            SELECT
                id, symbol, entry_price, quantity,
                cost_basis AS total_cost,
                take_profit_price, stop_loss_price, entry_reason AS signal_details,
                open_time AS created_at, updated_at
            FROM spot_positions
            WHERE status = 'open' AND account_id = %s
            ORDER BY open_time DESC
        """, (3,))

        auto_positions = cursor.fetchall()

        # 查询手动持仓 (paper_trading_positions 表)
        cursor.execute("""
            SELECT
                id, symbol, avg_entry_price AS entry_price, avg_entry_price,
                quantity, total_cost,
                take_profit_price, stop_loss_price, '' AS signal_details,
                created_at, updated_at
            FROM paper_trading_positions
            WHERE status = 'open' AND account_id = 1
            ORDER BY created_at DESC
        """)

        manual_positions = cursor.fetchall()

        # 合并
        all_positions = list(auto_positions) + list(manual_positions)

        # 获取当前价格
        result = []
        for pos in all_positions:
            current_price = await _get_current_price(cursor, pos['symbol'])

            # 如果 auto 仓 cost_basis 缺失，用 quantity * entry_price
            cost = float(pos.get('total_cost') or 0)
            if cost <= 0:
                cost = float(pos['quantity']) * float(pos['entry_price'])

            current_value = float(pos['quantity']) * float(current_price) if current_price else None
            unrealized_pnl = (current_value - cost) if current_value else None
            unrealized_pnl_pct = (unrealized_pnl / cost * 100) if unrealized_pnl and cost else None

            result.append(SpotPosition(
                id=pos['id'],
                symbol=pos['symbol'],
                entry_price=float(pos['entry_price']),
                quantity=float(pos['quantity']),
                total_cost=cost,
                current_price=current_price,
                current_value=current_value,
                unrealized_pnl=unrealized_pnl,
                unrealized_pnl_pct=unrealized_pnl_pct,
                take_profit_price=float(pos['take_profit_price']) if pos['take_profit_price'] else None,
                stop_loss_price=float(pos['stop_loss_price']) if pos['stop_loss_price'] else None,
                signal_details=pos.get('signal_details') or '',
                created_at=pos['created_at'],
                updated_at=pos['updated_at']
            ))

        cursor.close()
        conn.close()

        return result

    except Exception as e:
        import traceback
        logger.error(f"获取现货持仓失败: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=f"获取持仓失败: {str(e)}")


@router.get("/history", response_model=List[SpotHistoryPosition])
async def get_spot_history(
    limit: int = Query(100, ge=1, le=500, description="返回记录数"),
    offset: int = Query(0, ge=0, description="跳过记录数"),
    start_date: Optional[str] = Query(None, description="开始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD")
):
    """
    获取现货交易历史记录 (DCA 策略自动平仓 + 手动平仓)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # DCA 策略历史 (spot_positions closed)
        dca_where = ["account_id = 3", "status = 'closed'"]
        dca_params = []
        if start_date:
            dca_where.append("DATE(close_time) >= %s")
            dca_params.append(start_date)
        if end_date:
            dca_where.append("DATE(close_time) <= %s")
            dca_params.append(end_date)
        dca_clause = " AND ".join(dca_where)

        cursor.execute(f"""
            SELECT id, symbol, entry_price, close_price, quantity, cost_basis,
                   realized_pnl, close_reason, entry_reason AS signal_details,
                   open_time, close_time
            FROM spot_positions
            WHERE {dca_clause}
            ORDER BY close_time DESC
            LIMIT %s OFFSET %s
        """, dca_params + [limit, offset])
        dca_records = cursor.fetchall()

        # 手动历史 (paper_trading_trades SELL)
        manual_where = ["t.account_id = 1", "t.side = 'SELL'"]
        manual_params = []
        if start_date:
            manual_where.append("DATE(t.trade_time) >= %s")
            manual_params.append(start_date)
        if end_date:
            manual_where.append("DATE(t.trade_time) <= %s")
            manual_params.append(end_date)
        manual_clause = " AND ".join(manual_where)

        cursor.execute(f"""
            SELECT
                t.id, t.symbol,
                COALESCE(t.cost_price, t.price) AS entry_price,
                t.price AS exit_price,
                t.quantity, t.total_amount AS total_cost,
                COALESCE(t.realized_pnl, 0) AS pnl,
                COALESCE(t.pnl_pct, 0) AS pnl_pct,
                '' AS close_reason, '' AS signal_details,
                t.trade_time AS created_at, t.trade_time AS closed_at
            FROM paper_trading_trades t
            WHERE {manual_clause}
            ORDER BY t.trade_time DESC
            LIMIT %s OFFSET %s
        """, manual_params + [limit, offset])
        manual_records = cursor.fetchall()

        # 合并两种来源，按时间降序排序
        result = []

        # DCA 记录
        for rec in dca_records:
            pnl = float(rec['realized_pnl'] or 0)
            cost = float(rec['cost_basis'] or 0)
            pnl_pct = (pnl / cost * 100) if cost > 0 else 0
            result.append(SpotHistoryPosition(
                id=rec['id'],
                symbol=rec['symbol'],
                entry_price=float(rec['entry_price'] or 0),
                exit_price=float(rec['close_price'] or 0),
                quantity=float(rec['quantity'] or 0),
                total_cost=cost,
                pnl=pnl,
                pnl_pct=pnl_pct,
                close_reason=rec['close_reason'] or '',
                signal_details=rec.get('signal_details', rec.get('entry_reason', '')) or '',
                created_at=rec.get('open_time', datetime.now()),
                closed_at=rec.get('close_time', datetime.now())
            ))

        # 手动记录
        for rec in manual_records:
            result.append(SpotHistoryPosition(
                id=rec['id'],
                symbol=rec['symbol'],
                entry_price=float(rec['entry_price'] or 0),
                exit_price=float(rec['exit_price'] or 0),
                quantity=float(rec['quantity'] or 0),
                total_cost=float(rec['total_cost'] or 0),
                pnl=float(rec['pnl'] or 0),
                pnl_pct=float(rec['pnl_pct'] or 0),
                close_reason=rec.get('close_reason', '') or '',
                signal_details=rec.get('signal_details', '') or '',
                created_at=rec.get('created_at', datetime.now()),
                closed_at=rec.get('closed_at', datetime.now())
            ))

        # 按闭仓时间降序
        result.sort(key=lambda x: x.closed_at, reverse=True)

        cursor.close()
        conn.close()

        return result[:limit]

    except Exception as e:
        logger.error(f"获取现货历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")


@router.get("/summary", response_model=SpotSummary)
async def get_spot_summary():
    """
    获取现货交易概览统计 (含 DCA 策略 + 手动)
    """
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # ====== 1. 当前持仓统计 ======
        # DCA 策略仓 (spot_positions)
        cursor.execute("""
            SELECT
                COUNT(*) as total_positions,
                COALESCE(SUM(cost_basis), 0) as total_cost,
                SUM(quantity) as total_quantity
            FROM spot_positions
            WHERE status = 'open' AND account_id = 3
        """)
        dca_open = cursor.fetchone()

        cursor.execute("""
            SELECT id, symbol, quantity, cost_basis
            FROM spot_positions
            WHERE status = 'open' AND account_id = 3
        """)
        dca_open_positions = cursor.fetchall()

        # 手动仓 (paper_trading_positions)
        cursor.execute("""
            SELECT
                COUNT(*) as total_positions,
                COALESCE(SUM(total_cost), 0) as total_cost
            FROM paper_trading_positions
            WHERE status = 'open' AND account_id = 1
        """)
        manual_open = cursor.fetchone()

        cursor.execute("""
            SELECT id, symbol, quantity, total_cost
            FROM paper_trading_positions
            WHERE status = 'open' AND account_id = 1
        """)
        manual_open_positions = cursor.fetchall()

        # 合并统计
        all_open = list(dca_open_positions) + list(manual_open_positions)
        total_positions = (dca_open['total_positions'] or 0) + (manual_open['total_positions'] or 0)
        total_cost = float(dca_open['total_cost'] or 0) + float(manual_open['total_cost'] or 0)

        total_value = 0
        total_unrealized_pnl = 0
        for pos in all_open:
            current_price = await _get_current_price(cursor, pos['symbol'])
            if current_price:
                cost = float(pos.get('cost_basis') or pos.get('total_cost') or 0)
                if cost <= 0:
                    cost = 0
                qty = float(pos['quantity']) if pos['quantity'] else 0
                value = qty * float(current_price)
                total_value += value
                total_unrealized_pnl += (value - cost)

        total_unrealized_pnl_pct = (total_unrealized_pnl / total_cost * 100) if total_cost > 0 else 0

        # ====== 2. 历史统计 ======
        # DCA 平仓历史
        cursor.execute("""
            SELECT
                COALESCE(SUM(realized_pnl), 0) as total_pnl,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
                COUNT(*) as total_count
            FROM spot_positions
            WHERE status = 'closed' AND account_id = 3
        """)
        dca_history = cursor.fetchone()

        # 手动平仓历史 (paper_trading_positions)
        cursor.execute("""
            SELECT
                COALESCE(SUM(unrealized_pnl), 0) as total_pnl,
                SUM(CASE WHEN unrealized_pnl > 0 THEN 1 ELSE 0 END) as win_count,
                SUM(CASE WHEN unrealized_pnl <= 0 THEN 1 ELSE 0 END) as loss_count,
                COUNT(*) as total_count
            FROM paper_trading_positions
            WHERE status = 'closed' AND account_id = 1
        """)
        manual_history = cursor.fetchone()

        total_pnl = float(dca_history['total_pnl'] or 0) + float(manual_history['total_pnl'] or 0)
        win_count = (dca_history['win_count'] or 0) + (manual_history['win_count'] or 0)
        loss_count = (dca_history['loss_count'] or 0) + (manual_history['loss_count'] or 0)
        total_count = (dca_history['total_count'] or 0) + (manual_history['total_count'] or 0)
        win_rate = (win_count / total_count * 100) if total_count > 0 else 0

        # ====== 3. 账户余额 (现货手动账户) ======
        cursor.execute("""
            SELECT current_balance FROM paper_trading_accounts
            WHERE account_id = 1
        """)
        balance_row = cursor.fetchone()
        current_balance = float(balance_row['current_balance']) if balance_row else 0

        cursor.close()
        conn.close()

        return SpotSummary(
            total_positions=total_positions,
            total_cost=total_cost,
            total_value=total_value,
            total_unrealized_pnl=total_unrealized_pnl,
            total_unrealized_pnl_pct=total_unrealized_pnl_pct,
            current_balance=current_balance,
            history_total_pnl=total_pnl,
            history_win_count=win_count,
            history_loss_count=loss_count,
            history_win_rate=win_rate
        )

    except Exception as e:
        logger.error(f"获取现货概览失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取概览失败: {str(e)}")


class SellRequest(BaseModel):
    position_id: int
    symbol: str


@router.post("/sell")
async def sell_spot_position(req: SellRequest):
    """
    市价卖出现货持仓（全仓）
    1. 从 Binance Spot 获取当前价
    2. 计算盈亏
    3. 更新 paper_trading_positions -> status=closed
    4. 写入 paper_trading_trades 记录
    """
    import aiohttp
    from aiohttp import ClientTimeout
    from datetime import datetime as dt
    import uuid

    # 1. 获取实时价格 - paper trading 用合约同 symbol 参考价 (DataHub 进程内缓存)
    exit_price = None
    try:
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is not None:
            p = await hub.get_price(req.symbol)
            if p is not None and p > 0:
                exit_price = float(p)
    except Exception as e:
        logger.warning(f"DataHub 取价失败 (paper exit): {e}")

    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 2. 查询持仓
        cursor.execute(
            "SELECT id, symbol, avg_entry_price, quantity, total_cost, account_id "
            "FROM paper_trading_positions WHERE id = %s AND status = 'open'",
            (req.position_id,)
        )
        pos = cursor.fetchone()
        if not pos:
            raise HTTPException(status_code=404, detail="持仓不存在或已平仓")

        avg_cost = float(pos["avg_entry_price"])
        qty      = float(pos["quantity"])
        total_cost = float(pos["total_cost"])
        account_id = pos["account_id"]

        if exit_price is None:
            # fallback: use latest kline price
            cursor.execute(
                "SELECT close_price FROM kline_data WHERE symbol=%s ORDER BY open_time DESC LIMIT 1",
                (req.symbol,)
            )
            row = cursor.fetchone()
            exit_price = float(row["close_price"]) if row else avg_cost

        sell_amount  = exit_price * qty
        realized_pnl = sell_amount - total_cost
        pnl_pct      = (realized_pnl / total_cost * 100) if total_cost > 0 else 0
        now          = dt.now()
        trade_id     = str(uuid.uuid4())[:16]
        order_id     = f"MANUAL_SELL_{req.position_id}_{int(now.timestamp())}"

        # 3. 关闭持仓
        cursor.execute(
            "UPDATE paper_trading_positions SET status='closed', updated_at=%s WHERE id=%s",
            (now, req.position_id)
        )

        # 4. 写交易记录
        cursor.execute(
            """INSERT INTO paper_trading_trades
               (account_id, order_id, trade_id, symbol, side, price, quantity,
                total_amount, fee, cost_price, realized_pnl, pnl_pct, trade_time)
               VALUES (%s,%s,%s,%s,'SELL',%s,%s,%s,0,%s,%s,%s,%s)""",
            (account_id, order_id, trade_id, req.symbol,
             exit_price, qty, sell_amount, avg_cost,
             realized_pnl, pnl_pct, now)
        )
        conn.commit()
        cursor.close()

        return {
            "success": True,
            "symbol": req.symbol,
            "exit_price": exit_price,
            "quantity": qty,
            "realized_pnl": round(realized_pnl, 4),
            "pnl_pct": round(pnl_pct, 2),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"卖出持仓失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()


@router.post("/prices/batch")
async def get_spot_prices_batch(symbols: List[str] = Body(..., embed=True)):
    """批量获取现货实时价格（从 BinanceDataHub WebSocket），body: {"symbols": [...]}"""
    if not symbols:
        return {"success": True, "prices": {}}

    symbol_map = {}
    for s in symbols:
        clean = s.replace("/", "").replace("%2F", "").upper()
        symbol_map[clean] = s

    prices = {}
    try:
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is not None:
            for clean, original in symbol_map.items():
                price = hub.get_price_sync(original, max_age_seconds=90)
                if price is not None and price > 0:
                    prices[original] = {"price": float(price), "source": "binance_hub"}
    except Exception as e:
        logger.warning(f"DataHub 批量取价失败 (paper batch): {e}")

    return {"success": True, "prices": prices}


# ==================== 辅助函数 ====================

async def _get_current_price(cursor, symbol: str) -> Optional[float]:
    """
    获取币种当前价格
    优先从 BinanceDataHub WebSocket 获取，失败则从数据库 kline 获取
    """
    # 1. DataHub WebSocket 实时价 (最快)
    try:
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is not None:
            price = hub.get_price_sync(symbol, max_age_seconds=90)
            if price is not None and price > 0:
                return float(price)
    except Exception:
        pass

    # 2. 从数据库获取最新K线收盘价（兜底）
    try:
        cursor.execute("""
            SELECT close_price
            FROM kline_data
            WHERE symbol = %s AND timeframe = '1h' AND exchange = 'binance'
            ORDER BY open_time DESC
            LIMIT 1
        """, (symbol,))

        result = cursor.fetchone()
        if result:
            return float(result['close_price'])
    except Exception as e:
        logger.warning(f"[现货] 数据库取价失败 {symbol}: {e}")

    return None
