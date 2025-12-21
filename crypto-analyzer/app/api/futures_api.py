#!/usr/bin/env python3
"""
合约交易 API
Futures Trading API

提供合约交易的HTTP接口：开仓、平仓、查询持仓、基于信号自动开仓
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, HTTPException, Body, Request
from pydantic import BaseModel, Field
import yaml
from decimal import Decimal
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger
import pymysql

from app.trading.futures_trading_engine import FuturesTradingEngine

try:
    from app.trading.binance_futures_engine import BinanceFuturesEngine
except ImportError:
    BinanceFuturesEngine = None

# 创建 Router
router = APIRouter(prefix='/api/futures', tags=['futures'])

# 加载配置（支持环境变量）
from app.utils.config_loader import load_config
config = load_config()

db_config = config['database']['mysql']

# 全局数据库连接（复用连接，避免每次请求都重新建立）
_global_connection = None

def get_db_connection():
    """获取数据库连接（复用全局连接）"""
    global _global_connection
    try:
        # 检查连接是否有效
        if _global_connection and _global_connection.open:
            _global_connection.ping(reconnect=True)
            # 确保能读取最新数据（提交任何未完成的事务）
            _global_connection.commit()
            return _global_connection
    except Exception:
        pass

    # 创建新连接，启用自动提交
    _global_connection = pymysql.connect(**db_config, autocommit=True)
    return _global_connection

# 初始化Telegram通知服务
# 注意：模拟盘不需要TG通知，只有实盘需要
# from app.services.trade_notifier import init_trade_notifier
# trade_notifier = init_trade_notifier(config)

# 初始化实盘引擎（用于同步平仓）
live_engine = None
if BinanceFuturesEngine:
    try:
        live_engine = BinanceFuturesEngine(db_config)
        logger.info("✅ Futures API: 实盘引擎已初始化")
    except Exception as e:
        logger.warning(f"⚠️ Futures API: 实盘引擎初始化失败: {e}")

# 初始化交易引擎（模拟盘不传入trade_notifier，不发送TG通知，传入live_engine以便平仓同步）
engine = FuturesTradingEngine(db_config, trade_notifier=None, live_engine=live_engine)


# ==================== Pydantic Models ====================

class OpenPositionRequest(BaseModel):
    """开仓请求"""
    account_id: int = Field(default=2, description="账户ID")
    symbol: str = Field(..., description="交易对，如 BTC/USDT")
    position_side: str = Field(..., description="持仓方向: LONG 或 SHORT")
    quantity: float = Field(..., gt=0, description="数量")
    leverage: int = Field(default=1, ge=1, le=125, description="杠杆倍数")
    limit_price: Optional[float] = Field(None, description="限价价格（如果设置则创建限价单）")
    stop_loss_pct: Optional[float] = Field(None, description="止损百分比")
    take_profit_pct: Optional[float] = Field(None, description="止盈百分比")
    stop_loss_price: Optional[float] = Field(None, description="止损价格")
    take_profit_price: Optional[float] = Field(None, description="止盈价格")
    source: str = Field(default='manual', description="来源: manual, signal, auto")
    signal_id: Optional[int] = Field(None, description="信号ID")


class UpdateStopLossTakeProfitRequest(BaseModel):
    """更新止盈止损请求"""
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class ClosePositionRequest(BaseModel):
    """平仓请求"""
    close_quantity: Optional[float] = Field(None, description="平仓数量，不填则全部平仓")
    reason: str = Field(default='manual', description="原因: manual, stop_loss, take_profit, liquidation")


class AutoOpenRequest(BaseModel):
    """自动开仓请求"""
    account_id: int = Field(default=2, description="账户ID")
    symbols: Optional[List[str]] = Field(None, description="要处理的交易对列表")
    min_confidence: float = Field(default=75, description="最小置信度")
    leverage_map: Optional[Dict[str, int]] = Field(None, description="杠杆映射")
    position_size_map: Optional[Dict[str, float]] = Field(None, description="仓位大小映射")
    dry_run: bool = Field(default=False, description="是否仅模拟")


# ==================== 持仓管理 ====================

@router.get('/positions')
async def get_positions(account_id: int = 2, status: str = 'open'):
    """
    获取持仓列表

    - **account_id**: 账户ID（默认2）
    - **status**: 持仓状态（open/closed/all，默认open）
    """
    try:
        # 获取持仓
        if status == 'open':
            positions = engine.get_open_positions(account_id)
        else:
            # 查询所有持仓（包括已平仓）
            connection = get_db_connection()
            cursor = connection.cursor(pymysql.cursors.DictCursor)

            sql = """
            SELECT
                id as position_id,
                symbol,
                position_side,
                quantity,
                entry_price,
                mark_price as current_price,
                leverage,
                margin,
                unrealized_pnl,
                unrealized_pnl_pct,
                realized_pnl,
                liquidation_price,
                stop_loss_price,
                take_profit_price,
                status,
                open_time,
                close_time,
                source
            FROM futures_positions
            WHERE account_id = %s
            """

            if status != 'all':
                sql += " AND status = %s"
                cursor.execute(sql, (account_id, status))
            else:
                cursor.execute(sql, (account_id,))

            positions = cursor.fetchall()
            cursor.close()
            # connection.close()  # 复用连接，不关闭

            # 转换 Decimal 为 float
            for pos in positions:
                for key, value in pos.items():
                    if isinstance(value, Decimal):
                        pos[key] = float(value)

        return {
            'success': True,
            'data': positions
        }

    except Exception as e:
        logger.error(f"Failed to get positions: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put('/positions/{position_id}/stop-loss-take-profit')
async def update_stop_loss_take_profit(
    position_id: int,
    request: UpdateStopLossTakeProfitRequest
):
    """
    更新持仓的止损价和止盈价
    
    - **position_id**: 持仓ID
    - **request**: 请求体，包含以下可选字段：
        - **stop_loss_price**: 止损价格（可选，传入 null 表示清除）
        - **take_profit_price**: 止盈价格（可选，传入 null 表示清除）
        - **stop_loss_pct**: 止损百分比（可选，如果设置了价格则忽略）
        - **take_profit_pct**: 止盈百分比（可选，如果设置了价格则忽略）
    """
    logger.info(f"收到止盈止损更新请求: position_id={position_id}, request={request.dict()}")
    try:
        # 从请求体中提取参数
        stop_loss_price = request.stop_loss_price
        take_profit_price = request.take_profit_price
        stop_loss_pct = request.stop_loss_pct
        take_profit_pct = request.take_profit_pct
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # 先获取持仓信息
        cursor.execute("""
            SELECT id, symbol, position_side, entry_price, stop_loss_price, take_profit_price
            FROM futures_positions
            WHERE id = %s AND status = 'open'
        """, (position_id,))
        
        position = cursor.fetchone()
        if not position:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=404, detail=f'持仓 {position_id} 不存在或已平仓')
        
        # 计算止损价和止盈价
        entry_price = Decimal(str(position['entry_price']))
        position_side = position['position_side']
        
        # 简化逻辑：直接更新数据库
        logger.info(f"收到止盈止损更新请求: position_id={position_id}, stop_loss_price={stop_loss_price}, take_profit_price={take_profit_price}")
        
        # 获取请求中实际包含的字段（包括 None 值）
        request_dict = request.dict(exclude_unset=False)
        logger.info(f"请求字典内容: {request_dict}")
        
        # 构建更新字段
        update_fields = []
        params = []
        
        # 处理止损价：如果字段在请求中，就更新
        if 'stop_loss_price' in request_dict:
            logger.info(f"处理止损价: stop_loss_price={stop_loss_price}, 类型={type(stop_loss_price)}")
            if stop_loss_price is not None and stop_loss_price > 0:
                update_fields.append("stop_loss_price = %s")
                params.append(float(stop_loss_price))
                logger.info(f"添加止损价更新: {float(stop_loss_price)}")
            else:
                # None 或 <= 0 都视为清除
                update_fields.append("stop_loss_price = NULL")
                update_fields.append("stop_loss_pct = NULL")
                logger.info("清除止损价")
        else:
            logger.warning("请求中未包含 stop_loss_price 字段")
        
        # 处理止盈价：如果字段在请求中，就更新
        if 'take_profit_price' in request_dict:
            logger.info(f"处理止盈价: take_profit_price={take_profit_price}, 类型={type(take_profit_price)}")
            if take_profit_price is not None and take_profit_price > 0:
                update_fields.append("take_profit_price = %s")
                params.append(float(take_profit_price))
                logger.info(f"添加止盈价更新: {float(take_profit_price)}")
            else:
                # None 或 <= 0 都视为清除
                update_fields.append("take_profit_price = NULL")
                update_fields.append("take_profit_pct = NULL")
                logger.info("清除止盈价")
        else:
            logger.warning("请求中未包含 take_profit_price 字段")
        
        # 如果没有任何字段需要更新
        if not update_fields:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=400, detail='至少需要提供止损价或止盈价')
        
        update_fields.append("last_update_time = NOW()")
        params.append(position_id)
        
        # 构建 SQL 语句（单行，避免格式问题）
        sql = f"UPDATE futures_positions SET {', '.join(update_fields)} WHERE id = %s"
        
        logger.info(f"更新止盈止损 SQL: {sql}")
        logger.info(f"更新参数: {params}")
        logger.info(f"更新字段数量: {len(update_fields)}, 参数数量: {len(params)}")
        
        try:
            # 执行 SQL
            affected_rows = cursor.execute(sql, params)
            logger.info(f"SQL 执行完成，影响行数: {affected_rows}")
            
            if affected_rows == 0:
                cursor.close()
                # connection.close()  # 复用连接，不关闭
                logger.error(f"更新失败: 持仓 {position_id} 未找到或未更新任何行")
                raise HTTPException(status_code=404, detail=f'持仓 {position_id} 未找到或更新失败')
            
            # 提交事务
            connection.commit()
            logger.info(f"事务已提交: 持仓 {position_id}")
            
            # 验证更新是否成功 - 重新查询数据库
            verify_cursor = connection.cursor(pymysql.cursors.DictCursor)
            verify_cursor.execute("""
                SELECT stop_loss_price, take_profit_price 
                FROM futures_positions 
                WHERE id = %s
            """, (position_id,))
            updated_position = verify_cursor.fetchone()
            verify_cursor.close()
            
            logger.info(f"验证查询结果: {updated_position}")
            
            # 转换 Decimal 为 float
            if updated_position:
                for key in ['stop_loss_price', 'take_profit_price']:
                    if updated_position.get(key) is not None and isinstance(updated_position[key], Decimal):
                        updated_position[key] = float(updated_position[key])
            
            logger.info(f"验证更新结果: 持仓 {position_id}, 止损价: {updated_position.get('stop_loss_price')}, 止盈价: {updated_position.get('take_profit_price')}")
            
        except Exception as e:
            connection.rollback()
            logger.error(f"更新止盈止损时发生错误: {e}")
            import traceback
            logger.error(traceback.format_exc())
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=500, detail=f'更新失败: {str(e)}')
        
        return {
            'success': True,
            'message': '止损止盈价更新成功',
            'data': {
                'position_id': position_id,
                'stop_loss_price': float(updated_position['stop_loss_price']) if updated_position.get('stop_loss_price') else None,
                'take_profit_price': float(updated_position['take_profit_price']) if updated_position.get('take_profit_price') else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新止损止盈价失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/positions/{position_id}')
async def get_position(position_id: int):
    """获取单个持仓详情"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        sql = """
        SELECT
            id as position_id,
            account_id,
            symbol,
            position_side,
            quantity,
            entry_price,
            mark_price as current_price,
            leverage,
            margin,
            notional_value,
            unrealized_pnl,
            unrealized_pnl_pct,
            realized_pnl,
            liquidation_price,
            stop_loss_price,
            take_profit_price,
            stop_loss_pct,
            take_profit_pct,
            status,
            source,
            signal_id,
            open_time,
            close_time,
            holding_hours,
            notes
        FROM futures_positions
        WHERE id = %s
        """

        cursor.execute(sql, (position_id,))
        position = cursor.fetchone()
        cursor.close()
        # connection.close()  # 复用连接，不关闭

        if not position:
            raise HTTPException(status_code=404, detail=f'Position {position_id} not found')

        # 转换 Decimal
        for key, value in position.items():
            if isinstance(value, Decimal):
                position[key] = float(value)

        return {
            'success': True,
            'data': position
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get position {position_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 开仓 ====================

@router.post('/open')
async def open_position(request: OpenPositionRequest):
    """
    开仓

    开一个新的合约持仓，支持多头（LONG）和空头（SHORT）
    """
    try:
        # 验证请求参数
        if not request.symbol:
            raise HTTPException(status_code=400, detail="交易对不能为空")
        if not request.quantity or request.quantity <= 0:
            raise HTTPException(status_code=400, detail="数量必须大于0")
        if request.leverage < 1 or request.leverage > 125:
            raise HTTPException(status_code=400, detail="杠杆倍数必须在1-125之间")

        # ========== 熔断检查 ==========
        from app.services.market_regime_detector import get_circuit_breaker
        circuit_breaker = get_circuit_breaker(db_config)
        if circuit_breaker:
            direction = request.position_side.lower()
            is_sentinel, sentinel_desc = circuit_breaker.is_circuit_breaker_active(direction)
            if is_sentinel:
                logger.warning(f"🔒 [熔断] {request.symbol} {direction.upper()} 熔断中({sentinel_desc})，禁止开仓")
                raise HTTPException(
                    status_code=403,
                    detail=f"熔断模式中({sentinel_desc})，禁止开仓。请等待哨兵单恢复后再试。"
                )

        # 开仓
        result = engine.open_position(
            account_id=request.account_id,
            symbol=request.symbol,
            position_side=request.position_side,
            quantity=Decimal(str(request.quantity)),
            leverage=request.leverage,
            limit_price=Decimal(str(request.limit_price)) if request.limit_price else None,
            stop_loss_pct=Decimal(str(request.stop_loss_pct)) if request.stop_loss_pct else None,
            take_profit_pct=Decimal(str(request.take_profit_pct)) if request.take_profit_pct else None,
            stop_loss_price=Decimal(str(request.stop_loss_price)) if request.stop_loss_price else None,
            take_profit_price=Decimal(str(request.take_profit_price)) if request.take_profit_price else None,
            source=request.source,
            signal_id=request.signal_id
        )

        if result.get('success'):
            return {
                'success': True,
                'message': 'Position opened successfully',
                'data': result
            }
        else:
            error_message = result.get('message') or result.get('error') or '开仓失败'
            raise HTTPException(status_code=400, detail=error_message)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to open position: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 订单管理 ====================

@router.get('/orders')
async def get_orders(account_id: int = 2, status: str = 'PENDING'):
    """
    获取订单列表

    - **account_id**: 账户ID（默认2）
    - **status**: 订单状态（PENDING, FILLED, PARTIALLY_FILLED, CANCELLED, REJECTED, all, pending）
        - pending: 获取所有未成交订单（PENDING 和 PARTIALLY_FILLED）
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        sql = """
        SELECT
            id,
            order_id,
            account_id,
            position_id,
            symbol,
            side,
            order_type,
            leverage,
            price,
            quantity,
            executed_quantity,
            margin,
            total_value,
            executed_value,
            fee,
            status,
            avg_fill_price,
            fill_time,
            stop_price,
            stop_loss_price,
            take_profit_price,
            order_source,
            signal_id,
            realized_pnl,
            pnl_pct,
            notes,
            cancellation_reason,
            canceled_at,
            created_at,
            updated_at
        FROM futures_orders
        WHERE account_id = %s
        """

        params = [account_id]
        if status == 'pending':
            # 获取所有未成交订单（PENDING 和 PARTIALLY_FILLED）
            sql += " AND status IN ('PENDING', 'PARTIALLY_FILLED')"
        elif status != 'all':
            sql += " AND status = %s"
            params.append(status)

        sql += " ORDER BY created_at DESC LIMIT 100"

        cursor.execute(sql, params)
        orders = cursor.fetchall()
        cursor.close()
        # 使用复用连接，不关闭
        
        # 转换 Decimal 为 float
        for order in orders:
            for key, value in order.items():
                if isinstance(value, Decimal):
                    order[key] = float(value)
                elif isinstance(value, datetime):
                    order[key] = value.strftime('%Y-%m-%d %H:%M:%S')
        
        return {
            'success': True,
            'data': orders,
            'count': len(orders)
        }
        
    except Exception as e:
        logger.error(f"获取订单列表失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


class UpdateOrderStopLossTakeProfitRequest(BaseModel):
    """更新订单止盈止损请求"""
    order_id: str
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None


@router.put('/orders/stop-loss-take-profit')
async def update_order_stop_loss_take_profit(
    request: UpdateOrderStopLossTakeProfitRequest = Body(...),
    account_id: int = 2
):
    """
    更新未成交订单的止盈止损价格
    
    - **order_id**: 订单ID
    - **stop_loss_price**: 止损价格（可选）
    - **take_profit_price**: 止盈价格（可选）
    - **account_id**: 账户ID（默认2）
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # 检查订单是否存在且未成交
        cursor.execute(
            """SELECT order_id, status FROM futures_orders 
            WHERE order_id = %s AND account_id = %s 
            AND status IN ('PENDING', 'PARTIALLY_FILLED')""",
            (request.order_id, account_id)
        )
        order = cursor.fetchone()
        
        if not order:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=404, detail="订单不存在或已成交")
        
        # 更新止盈止损价格
        update_fields = []
        params = []
        
        if request.stop_loss_price is not None:
            update_fields.append("stop_loss_price = %s")
            params.append(Decimal(str(request.stop_loss_price)) if request.stop_loss_price > 0 else None)
        
        if request.take_profit_price is not None:
            update_fields.append("take_profit_price = %s")
            params.append(Decimal(str(request.take_profit_price)) if request.take_profit_price > 0 else None)
        
        if not update_fields:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=400, detail="至少需要提供一个价格参数")
        
        params.extend([request.order_id, account_id])
        cursor.execute(
            f"""UPDATE futures_orders 
            SET {', '.join(update_fields)}, updated_at = NOW()
            WHERE order_id = %s AND account_id = %s""",
            params
        )
        
        connection.commit()
        cursor.close()
        # connection.close()  # 复用连接，不关闭
        
        return {
            'success': True,
            'message': '止盈止损价格更新成功'
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新订单止盈止损失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/orders/{order_id}')
async def cancel_order(order_id: str, account_id: int = 2, reason: str = 'manual'):
    """
    撤销订单（同步撤销模拟盘和实盘订单）

    - **order_id**: 订单ID
    - **account_id**: 账户ID（默认2）
    - **reason**: 取消原因（manual=手动取消, strategy_signal=策略信号取消, risk_control=风控取消, system=系统取消, expired=订单过期）

    功能：
    1. 撤销模拟盘订单，释放冻结保证金
    2. 自动查找并撤销对应的实盘订单（通过 symbol, position_side, strategy_id 匹配）
    3. 调用币安API撤销实盘订单
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        # 检查订单是否存在且未成交，同时获取订单详情用于同步实盘撤单
        cursor.execute(
            """SELECT id, status, symbol, side, strategy_id FROM futures_orders
            WHERE order_id = %s AND account_id = %s""",
            (order_id, account_id)
        )
        order = cursor.fetchone()

        if not order:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=404, detail="订单不存在")

        if order['status'] not in ['PENDING', 'PARTIALLY_FILLED']:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=400, detail=f"订单状态为 {order['status']}，无法撤销")

        # 提取订单信息用于同步实盘撤单
        symbol = order['symbol']
        side = order['side']
        strategy_id = order.get('strategy_id')

        # 更新模拟盘订单状态和取消原因
        cursor.execute(
            """UPDATE futures_orders
            SET status = 'CANCELLED', cancellation_reason = %s, updated_at = NOW()
            WHERE order_id = %s AND account_id = %s""",
            (reason, order_id, account_id)
        )
        
        # 释放冻结的保证金和手续费
        cursor.execute(
            """SELECT margin, fee FROM futures_orders 
            WHERE order_id = %s AND account_id = %s""",
            (order_id, account_id)
        )
        order_info = cursor.fetchone()
        
        if order_info and order_info['margin']:
            # 计算总冻结金额（保证金 + 手续费）
            total_frozen = float(order_info['margin']) + float(order_info.get('fee', 0) or 0)
            
            # 释放保证金和手续费到可用余额
            cursor.execute(
                """UPDATE paper_trading_accounts 
                SET current_balance = current_balance + %s,
                    frozen_balance = frozen_balance - %s,
                    updated_at = NOW()
                WHERE id = %s""",
                (total_frozen, total_frozen, account_id)
            )
            
            # 更新总权益（余额 + 冻结余额 + 持仓未实现盈亏）
            cursor.execute(
                """UPDATE paper_trading_accounts a
                SET a.total_equity = a.current_balance + a.frozen_balance + COALESCE((
                    SELECT SUM(p.unrealized_pnl) 
                    FROM futures_positions p 
                    WHERE p.account_id = a.id AND p.status = 'open'
                ), 0)
                WHERE a.id = %s""",
                (account_id,)
            )
        
        connection.commit()

        # 同步撤销实盘订单
        live_cancel_result = None
        try:
            # 确定持仓方向 (BUY -> LONG, SELL -> SHORT)
            position_side = 'LONG' if side == 'BUY' else 'SHORT'

            # 查询对应的实盘待成交订单
            cursor.execute("""
                SELECT id, binance_order_id, symbol, position_side, quantity
                FROM live_futures_positions
                WHERE symbol = %s AND position_side = %s AND strategy_id = %s AND status = 'PENDING'
                ORDER BY created_at DESC LIMIT 1
            """, (symbol, position_side, strategy_id))
            live_position = cursor.fetchone()

            if live_position and live_position.get('binance_order_id'):
                live_position_id = live_position['id']
                binance_order_id = live_position['binance_order_id']

                logger.info(f"[撤单同步] 找到对应的实盘订单: {symbol} {position_side} (币安订单ID: {binance_order_id})")

                # 初始化实盘交易引擎
                live_engine = None
                if BinanceFuturesEngine:
                    try:
                        live_engine = BinanceFuturesEngine(db_config)
                    except Exception as engine_err:
                        logger.error(f"[撤单同步] 初始化实盘引擎失败: {engine_err}")

                if live_engine:
                    # 调用币安API撤销订单
                    binance_symbol = symbol.replace('/', '').upper()
                    cancel_result = live_engine._request('DELETE', '/fapi/v1/order', {
                        'symbol': binance_symbol,
                        'orderId': binance_order_id
                    })

                    if isinstance(cancel_result, dict) and not cancel_result.get('success') == False:
                        # 撤单成功，更新本地数据库
                        cursor.execute("""
                            UPDATE live_futures_positions
                            SET status = 'CANCELED', updated_at = NOW()
                            WHERE id = %s
                        """, (live_position_id,))
                        connection.commit()

                        logger.info(f"[撤单同步] ✅ 实盘订单撤销成功: {symbol} {position_side}")
                        live_cancel_result = {'success': True, 'message': '实盘订单已同步撤销'}
                    else:
                        error_msg = cancel_result.get('error', '未知错误') if isinstance(cancel_result, dict) else str(cancel_result)
                        logger.error(f"[撤单同步] ❌ 实盘订单撤销失败: {error_msg}")
                        live_cancel_result = {'success': False, 'error': error_msg}
                else:
                    logger.warning(f"[撤单同步] 实盘引擎未初始化，跳过同步撤单")
            else:
                logger.debug(f"[撤单同步] 未找到对应的实盘待成交订单: {symbol} {position_side} (策略ID: {strategy_id})")
        except Exception as sync_err:
            logger.error(f"[撤单同步] 同步撤销实盘订单异常: {sync_err}")
            import traceback
            traceback.print_exc()

        cursor.close()
        # connection.close()  # 复用连接，不关闭

        result = {
            'success': True,
            'message': '订单已撤销'
        }

        # 附加实盘撤单结果（如果有）
        if live_cancel_result:
            result['live_cancel'] = live_cancel_result

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"撤销订单失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 平仓 ====================

@router.post('/close/{position_id}')
async def close_position(
    position_id: int, 
    request: Optional[ClosePositionRequest] = Body(None)
):
    """
    平仓

    关闭指定的持仓，可以全部平仓或部分平仓
    """
    try:
        # 如果请求体为空或None，使用默认值
        if request is None:
            request = ClosePositionRequest()

        close_quantity = Decimal(str(request.close_quantity)) if request.close_quantity else None

        # 平仓模拟盘（内部会自动同步实盘，无需额外处理）
        result = engine.close_position(
            position_id=position_id,
            close_quantity=close_quantity,
            reason=request.reason or 'manual'
        )

        if result['success']:
            return {
                'success': True,
                'message': 'Position closed successfully',
                'data': result
            }
        else:
            raise HTTPException(status_code=400, detail=result['message'])

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to close position {position_id}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 基于投资建议自动开仓 ====================

@router.post('/auto-open')
async def auto_open_from_signals(request: AutoOpenRequest):
    """
    基于投资建议自动开仓

    根据数据库中的投资建议自动创建合约持仓
    """
    try:
        account_id = request.account_id
        target_symbols = request.symbols or config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
        min_confidence = request.min_confidence
        dry_run = request.dry_run

        # 杠杆映射
        leverage_map = request.leverage_map or {
            '强烈买入': 10,
            '买入': 5,
            '持有': 0,
            '卖出': 5,
            '强烈卖出': 10
        }

        # 仓位大小映射
        default_position_sizes = {
            'BTC/USDT': 0.01,
            'ETH/USDT': 0.1,
            'SOL/USDT': 1.0,
            'BNB/USDT': 0.5
        }
        position_size_map = request.position_size_map or default_position_sizes
        position_size_map = {k: Decimal(str(v)) for k, v in position_size_map.items()}

        # 获取投资建议
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        sql = """
        SELECT
            symbol,
            recommendation,
            confidence,
            reasoning
        FROM investment_recommendations
        WHERE symbol IN ({})
        AND updated_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        ORDER BY updated_at DESC
        """.format(','.join(['%s'] * len(target_symbols)))

        cursor.execute(sql, target_symbols)
        recommendations = cursor.fetchall()
        cursor.close()
        # connection.close()  # 复用连接，不关闭

        logger.info(f"Found {len(recommendations)} recommendations")

        # 处理每个建议
        results = {
            'processed': 0,
            'opened': 0,
            'skipped': 0,
            'failed': 0,
            'details': []
        }

        for rec in recommendations:
            results['processed'] += 1

            symbol = rec['symbol']
            recommendation = rec['recommendation']
            confidence = float(rec['confidence'])

            detail = {
                'symbol': symbol,
                'recommendation': recommendation,
                'confidence': confidence
            }

            # 检查置信度
            if confidence < min_confidence:
                detail['status'] = 'skipped'
                detail['reason'] = f'Confidence {confidence:.1f}% < {min_confidence}%'
                results['skipped'] += 1
                results['details'].append(detail)
                continue

            # 确定开仓方向和杠杆
            if recommendation in ['强烈买入', '买入']:
                position_side = 'LONG'
                leverage = leverage_map.get(recommendation, 5)
            elif recommendation in ['强烈卖出', '卖出']:
                position_side = 'SHORT'
                leverage = leverage_map.get(recommendation, 5)
            else:
                # 持有 - 不操作
                detail['status'] = 'skipped'
                detail['reason'] = 'Recommendation is HOLD'
                results['skipped'] += 1
                results['details'].append(detail)
                continue

            # 获取仓位大小
            quantity = position_size_map.get(symbol, Decimal('0.01'))

            # 计算止盈止损（基于置信度调整）
            if confidence >= 85:
                stop_loss_pct = Decimal('5')
                take_profit_pct = Decimal('20')
            elif confidence >= 75:
                stop_loss_pct = Decimal('5')
                take_profit_pct = Decimal('15')
            else:
                stop_loss_pct = Decimal('5')
                take_profit_pct = Decimal('10')

            detail['position_side'] = position_side
            detail['leverage'] = leverage
            detail['quantity'] = float(quantity)
            detail['stop_loss_pct'] = float(stop_loss_pct)
            detail['take_profit_pct'] = float(take_profit_pct)

            # 干运行模式
            if dry_run:
                detail['status'] = 'dry_run'
                detail['message'] = 'Would open position (dry run mode)'
                results['skipped'] += 1
                results['details'].append(detail)
                continue

            # 检查是否已有持仓
            existing = engine.get_open_positions(account_id)
            has_position = any(p['symbol'] == symbol for p in existing)

            if has_position:
                detail['status'] = 'skipped'
                detail['reason'] = 'Position already exists'
                results['skipped'] += 1
                results['details'].append(detail)
                continue

            # 实际开仓
            try:
                result = engine.open_position(
                    account_id=account_id,
                    symbol=symbol,
                    position_side=position_side,
                    quantity=quantity,
                    leverage=leverage,
                    stop_loss_pct=stop_loss_pct,
                    take_profit_pct=take_profit_pct,
                    source='signal'
                )

                if result['success']:
                    detail['status'] = 'opened'
                    detail['position_id'] = result['position_id']
                    detail['entry_price'] = result['entry_price']
                    detail['margin'] = result['margin']
                    results['opened'] += 1
                else:
                    detail['status'] = 'failed'
                    detail['error'] = result['message']
                    results['failed'] += 1

            except Exception as e:
                logger.error(f"Failed to open position for {symbol}: {e}")
                detail['status'] = 'failed'
                detail['error'] = str(e)
                results['failed'] += 1

            results['details'].append(detail)

        return {
            'success': True,
            'message': f"Auto-open completed: {results['opened']} opened, {results['skipped']} skipped, {results['failed']} failed",
            'data': results
        }

    except Exception as e:
        logger.error(f"Auto-open failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 账户信息 ====================

@router.get('/account/{account_id}')
async def get_account(account_id: int):
    """获取账户信息"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        sql = """
        SELECT
            id as account_id,
            account_name,
            account_type,
            initial_balance,
            current_balance,
            frozen_balance,
            unrealized_pnl,
            realized_pnl,
            total_equity,
            total_profit_loss_pct,
            total_trades,
            win_rate,
            status
        FROM paper_trading_accounts
        WHERE id = %s
        """

        cursor.execute(sql, (account_id,))
        account = cursor.fetchone()
        cursor.close()
        # connection.close()  # 复用连接，不关闭

        if not account:
            raise HTTPException(status_code=404, detail=f'Account {account_id} not found')

        # 转换 Decimal
        for key, value in account.items():
            if isinstance(value, Decimal):
                account[key] = float(value)

        # 计算可用余额
        account['available_balance'] = account['current_balance'] - account['frozen_balance']

        return {
            'success': True,
            'data': account
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get account {account_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 价格查询 ====================

@router.get('/price/{symbol:path}')
async def get_futures_price(symbol: str):
    """
    获取合约价格
    
    - **symbol**: 交易对，如 BTC/USDT 或 BTCUSDT
    使用 {symbol:path} 以支持URL中包含斜杠的符号
    """
    try:
        import aiohttp
        from aiohttp import ClientTimeout
        
        # 标准化交易对格式（处理URL编码的斜杠）
        symbol_clean = symbol.replace('/', '').replace('%2F', '').upper()
        
        price = None
        source = None
        
        # 使用较短的超时时间，快速失败并回退
        quick_timeout = ClientTimeout(total=2)  # 2秒快速超时
        
        # 1. 优先从Binance合约API获取（快速）
        try:
            async with aiohttp.ClientSession(timeout=quick_timeout) as session:
                async with session.get(
                    'https://fapi.binance.com/fapi/v1/ticker/price',
                    params={'symbol': symbol_clean}
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and 'price' in data:
                            price = float(data['price'])
                            source = 'binance_futures'
                            logger.debug(f"从Binance合约API获取 {symbol} 价格: {price}")
                            # 成功获取，直接返回
                            return {
                                'success': True,
                                'symbol': symbol,
                                'price': price,
                                'source': source
                            }
        except (aiohttp.ClientError, aiohttp.ServerTimeoutError, TimeoutError) as e:
            logger.debug(f"Binance合约API超时或失败: {symbol}, {e}")
        except Exception as e:
            logger.debug(f"Binance合约API获取失败: {e}")
        
        # 2. 如果Binance失败，尝试从Gate.io合约API获取（仅对HYPE/USDT）
        if not price and symbol.upper() == 'HYPE/USDT':
            try:
                gate_symbol = symbol.replace('/', '_')
                async with aiohttp.ClientSession(timeout=quick_timeout) as session:
                    async with session.get(
                        'https://api.gateio.ws/api/v4/futures/usdt/tickers',
                        params={'contract': gate_symbol}
                    ) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data and len(data) > 0 and 'last' in data[0]:
                                price = float(data[0]['last'])
                                source = 'gateio_futures'
                                logger.debug(f"从Gate.io合约API获取 {symbol} 价格: {price}")
                                return {
                                    'success': True,
                                    'symbol': symbol,
                                    'price': price,
                                    'source': source
                                }
            except (aiohttp.ClientError, aiohttp.ServerTimeoutError, TimeoutError) as e:
                logger.debug(f"Gate.io合约API超时或失败: {symbol}, {e}")
            except Exception as e:
                logger.debug(f"Gate.io合约API获取失败: {e}")
        
        # 3. 快速回退：从数据库获取最新价格（现货价格作为fallback，更快）
        if not price:
            try:
                from app.database.db_service import DatabaseService
                db_service = DatabaseService(config.get('database', {}))
                latest_kline = db_service.get_latest_kline(symbol, '1m')
                if latest_kline:
                    price = float(latest_kline.close_price)
                    source = 'database_spot'
                    logger.debug(f"从数据库获取 {symbol} 价格（现货）: {price}")
            except Exception as e:
                logger.debug(f"从数据库获取价格失败: {e}")
        
        if price and price > 0:
            return {
                'success': True,
                'symbol': symbol,
                'price': price,
                'source': source
            }
        else:
            raise HTTPException(status_code=404, detail=f'无法获取 {symbol} 的合约价格')

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取合约价格失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取合约价格失败: {str(e)}")


@router.post('/prices/batch')
async def get_futures_prices_batch(symbols: List[str] = Body(..., embed=True)):
    """
    批量获取合约价格（优化性能）

    - **symbols**: 交易对列表，如 ["BTC/USDT", "ETH/USDT"]

    一次API调用获取所有交易对价格，避免多次网络请求
    """
    import aiohttp
    from aiohttp import ClientTimeout

    if not symbols:
        return {'success': True, 'prices': {}}

    # 标准化交易对格式
    symbol_map = {}  # 原始symbol -> 标准化symbol
    for s in symbols:
        clean = s.replace('/', '').replace('%2F', '').upper()
        symbol_map[clean] = s

    prices = {}
    quick_timeout = ClientTimeout(total=3)  # 3秒超时

    try:
        # 1. 从Binance批量获取所有合约价格（单次请求）
        async with aiohttp.ClientSession(timeout=quick_timeout) as session:
            async with session.get('https://fapi.binance.com/fapi/v1/ticker/price') as response:
                if response.status == 200:
                    all_prices = await response.json()
                    # 构建价格映射
                    price_map = {item['symbol']: float(item['price']) for item in all_prices}

                    for clean_symbol, original_symbol in symbol_map.items():
                        if clean_symbol in price_map:
                            prices[original_symbol] = {
                                'price': price_map[clean_symbol],
                                'source': 'binance_futures'
                            }
    except Exception as e:
        logger.debug(f"批量获取Binance价格失败: {e}")

    # 2. 对于没有获取到的symbol，尝试其他来源
    missing_symbols = [s for s in symbols if s not in prices]
    if missing_symbols:
        try:
            from app.database.db_service import DatabaseService
            db_service = DatabaseService(config.get('database', {}))

            for symbol in missing_symbols:
                try:
                    latest_kline = db_service.get_latest_kline(symbol, '1m')
                    if latest_kline:
                        prices[symbol] = {
                            'price': float(latest_kline.close_price),
                            'source': 'database_spot'
                        }
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"从数据库获取价格失败: {e}")

    return {
        'success': True,
        'prices': prices,
        'count': len(prices)
    }


# ==================== 健康检查 ====================

@router.get('/trades')
async def get_trades(account_id: int = 2, limit: int = 50, page: int = 1, page_size: int = 10):
    """
    获取交易历史记录

    - **account_id**: 账户ID（默认2）
    - **limit**: 返回记录数（默认50，用于兼容旧代码）
    - **page**: 页码（默认1）
    - **page_size**: 每页记录数（默认10）
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        # 先获取总数（只统计平仓交易）
        count_sql = """
        SELECT COUNT(*) as total
        FROM futures_trades t
        WHERE t.account_id = %s AND t.side IN ('CLOSE_LONG', 'CLOSE_SHORT')
        """
        cursor.execute(count_sql, (account_id,))
        total_count = cursor.fetchone()['total']

        # 计算分页
        if page_size > 0:
            offset = (page - 1) * page_size
            actual_limit = page_size
        else:
            # 兼容旧代码，使用limit参数
            offset = 0
            actual_limit = limit

        sql = """
        SELECT
            t.id,
            t.trade_id,
            t.position_id,
            t.order_id,
            t.symbol,
            t.side,
            t.price,
            t.quantity,
            t.notional_value,
            t.leverage,
            t.margin,
            t.fee,
            t.realized_pnl,
            t.pnl_pct,
            t.roi,
            t.entry_price,
            t.trade_time,
            COALESCE(o.order_source, 'manual') as order_source,
            p.stop_loss_price,
            p.take_profit_price,
            p.open_time,
            p.close_time
        FROM futures_trades t
        LEFT JOIN futures_orders o ON t.order_id = o.order_id
        LEFT JOIN futures_positions p ON t.position_id = p.id
        WHERE t.account_id = %s AND t.side IN ('CLOSE_LONG', 'CLOSE_SHORT')
        ORDER BY t.trade_time DESC
        LIMIT %s OFFSET %s
        """

        cursor.execute(sql, (account_id, actual_limit, offset))
        trades = cursor.fetchall()
        cursor.close()
        # connection.close()  # 复用连接，不关闭

        # 转换 Decimal 为 float，datetime 为字符串
        for trade in trades:
            for key, value in trade.items():
                if isinstance(value, Decimal):
                    trade[key] = float(value)
                elif isinstance(value, datetime):
                    # 将 datetime 转换为字符串（本地时间格式）
                    # 如果数据库存储的是UTC时间，需要转换为本地时间（UTC+8）
                    # 假设数据库存储的是本地时间（UTC+8），直接格式化
                    trade[key] = value.strftime('%Y-%m-%d %H:%M:%S')

        # 计算总页数
        total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 1

        return {
            'success': True,
            'data': trades,
            'count': len(trades),
            'total_count': total_count,
            'page': page,
            'page_size': page_size if page_size > 0 else limit,
            'total_pages': total_pages
        }

    except Exception as e:
        logger.error(f"获取交易历史失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/symbols')
async def get_symbols():
    """
    获取可交易的币种列表（从配置文件读取）

    Returns:
        交易对列表
    """
    try:
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
        return {
            "success": True,
            "symbols": symbols,
            "total": len(symbols)
        }
    except Exception as e:
        logger.error(f"获取交易对列表失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/health')
async def health():
    """健康检查"""
    return {
        'success': True,
        'service': 'futures-api',
        'status': 'running'
    }


# ==================== 策略配置管理 ====================

@router.get('/strategies')
async def get_futures_strategies():
    """
    获取所有合约交易策略配置（从数据库读取）
    
    Returns:
        策略配置列表
    """
    try:
        import json
        
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            # 检查 sync_live 列是否存在
            cursor.execute("""
                SELECT COUNT(*) as cnt FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = DATABASE()
                AND TABLE_NAME = 'trading_strategies'
                AND COLUMN_NAME = 'sync_live'
            """)
            has_sync_live = cursor.fetchone()['cnt'] > 0

            # 从数据库读取策略配置
            if has_sync_live:
                cursor.execute("""
                    SELECT id, name, description, account_id, enabled, config,
                           sync_live, live_quantity_pct, created_at, updated_at
                    FROM trading_strategies
                    ORDER BY id ASC
                """)
            else:
                cursor.execute("""
                    SELECT id, name, description, account_id, enabled, config,
                           FALSE as sync_live, 100.00 as live_quantity_pct, created_at, updated_at
                    FROM trading_strategies
                    ORDER BY id ASC
                """)
            rows = cursor.fetchall()
            
            # 转换为前端需要的格式
            strategies = []
            for row in rows:
                strategy = {
                    'id': row['id'],
                    'name': row['name'],
                    'description': row.get('description', ''),
                    'account_id': row.get('account_id', 2),
                    'enabled': bool(row.get('enabled', 0)),
                    'syncLive': bool(row.get('sync_live', 0)),
                    'liveQuantityPct': float(row.get('live_quantity_pct', 100) or 100),
                    'created_at': row.get('created_at').isoformat() if row.get('created_at') else None,
                    'updated_at': row.get('updated_at').isoformat() if row.get('updated_at') else None
                }
                
                # 解析 config JSON 字段
                if row.get('config'):
                    try:
                        config = json.loads(row['config']) if isinstance(row['config'], str) else row['config']
                        strategy.update(config)  # 合并配置到策略对象
                    except Exception as e:
                        logger.warning(f"解析策略配置失败 (ID: {row['id']}): {e}")
                
                strategies.append(strategy)
            
            return {
                'success': True,
                'data': strategies,
                'count': len(strategies)
            }
            
        finally:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
        
    except Exception as e:
        logger.error(f"获取策略配置失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/strategies')
async def save_futures_strategies(strategies: List[Dict] = Body(...)):
    """
    保存合约交易策略配置（保存到数据库）
    
    - **strategies**: 策略配置列表
    """
    try:
        import json
        
        # 验证策略数据
        if not isinstance(strategies, list):
            raise HTTPException(status_code=400, detail="策略配置必须是列表格式")
        
        connection = get_db_connection()
        cursor = connection.cursor()
        
        try:
            saved_count = 0
            updated_count = 0
            
            for strategy in strategies:
                # 提取基本信息
                strategy_id = strategy.get('id')
                name = strategy.get('name', '未命名策略')
                description = strategy.get('description', '')
                account_id = strategy.get('account_id', 2)
                enabled = 1 if strategy.get('enabled', False) else 0
                
                # 提取配置信息（排除基本信息字段）
                config_fields = {k: v for k, v in strategy.items() 
                               if k not in ['id', 'name', 'description', 'account_id', 'enabled', 'created_at', 'updated_at']}
                config_json = json.dumps(config_fields, ensure_ascii=False) if config_fields else None
                
                # 检查策略是否存在
                if strategy_id:
                    cursor.execute("""
                        SELECT id FROM trading_strategies WHERE id = %s
                    """, (strategy_id,))
                    exists = cursor.fetchone()
                    
                    if exists:
                        # 更新现有策略
                        cursor.execute("""
                            UPDATE trading_strategies
                            SET name = %s, description = %s, account_id = %s, 
                                enabled = %s, config = %s, updated_at = NOW()
                            WHERE id = %s
                        """, (name, description, account_id, enabled, config_json, strategy_id))
                        updated_count += 1
                    else:
                        # 插入新策略（使用指定的ID）
                        cursor.execute("""
                            INSERT INTO trading_strategies 
                            (id, name, description, account_id, enabled, config)
                            VALUES (%s, %s, %s, %s, %s, %s)
                        """, (strategy_id, name, description, account_id, enabled, config_json))
                        saved_count += 1
                else:
                    # 插入新策略（自动生成ID）
                    cursor.execute("""
                        INSERT INTO trading_strategies 
                        (name, description, account_id, enabled, config)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (name, description, account_id, enabled, config_json))
                    saved_count += 1
            
            connection.commit()
            
            logger.info(f"策略配置已保存到数据库，新增 {saved_count} 个，更新 {updated_count} 个，共 {len(strategies)} 个策略")
            
            return {
                'success': True,
                'message': f'策略配置保存成功，新增 {saved_count} 个，更新 {updated_count} 个',
                'count': len(strategies),
                'saved': saved_count,
                'updated': updated_count
            }
            
        except Exception as e:
            connection.rollback()
            raise
        finally:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"保存策略配置失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete('/strategies/{strategy_id}')
async def delete_futures_strategy(strategy_id: int):
    """
    删除合约交易策略配置
    
    - **strategy_id**: 策略ID
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        try:
            # 检查策略是否存在
            cursor.execute("SELECT id, name FROM trading_strategies WHERE id = %s", (strategy_id,))
            strategy = cursor.fetchone()
            
            if not strategy:
                raise HTTPException(status_code=404, detail=f"策略 ID {strategy_id} 不存在")
            
            # 删除策略
            cursor.execute("DELETE FROM trading_strategies WHERE id = %s", (strategy_id,))
            connection.commit()
            
            logger.info(f"策略已删除: ID={strategy_id}, Name={strategy[1]}")
            
            return {
                'success': True,
                'message': f'策略已删除',
                'id': strategy_id
            }
            
        except HTTPException:
            raise
        except Exception as e:
            connection.rollback()
            raise
        finally:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除策略配置失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch('/strategies/{strategy_id}/toggle')
async def toggle_futures_strategy(strategy_id: int):
    """
    切换策略启用/禁用状态
    
    - **strategy_id**: 策略ID
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        try:
            # 获取当前状态
            cursor.execute("SELECT id, name, enabled FROM trading_strategies WHERE id = %s", (strategy_id,))
            strategy = cursor.fetchone()
            
            if not strategy:
                raise HTTPException(status_code=404, detail=f"策略 ID {strategy_id} 不存在")
            
            # 切换状态
            new_enabled = 1 if strategy['enabled'] == 0 else 0
            cursor.execute("""
                UPDATE trading_strategies 
                SET enabled = %s, updated_at = NOW()
                WHERE id = %s
            """, (new_enabled, strategy_id))
            connection.commit()
            
            status_text = '启用' if new_enabled else '禁用'
            logger.info(f"策略状态已切换: ID={strategy_id}, Name={strategy['name']}, Status={status_text}")
            
            return {
                'success': True,
                'message': f'策略已{status_text}',
                'id': strategy_id,
                'enabled': bool(new_enabled)
            }
            
        except HTTPException:
            raise
        except Exception as e:
            connection.rollback()
            raise
        finally:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换策略状态失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.patch('/strategies/{strategy_id}/sync-live')
async def toggle_strategy_sync_live(strategy_id: int, request: Request):
    """
    切换策略的实盘同步状态

    - **strategy_id**: 策略ID
    - **sync_live**: 是否同步实盘交易
    """
    try:
        body = await request.json()
        sync_live = body.get('sync_live', False)

        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        try:
            # 检查策略是否存在
            cursor.execute("SELECT id, name, config FROM trading_strategies WHERE id = %s", (strategy_id,))
            strategy = cursor.fetchone()

            if not strategy:
                raise HTTPException(status_code=404, detail=f"策略 ID {strategy_id} 不存在")

            # 更新同步状态（同时更新sync_live列和config JSON）
            cursor.execute("""
                UPDATE trading_strategies
                SET sync_live = %s,
                    config = JSON_SET(config, '$.syncLive', %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (sync_live, sync_live, strategy_id))
            connection.commit()

            status_text = '启用' if sync_live else '关闭'
            logger.info(f"策略实盘同步已{status_text}: ID={strategy_id}, Name={strategy['name']}")

            return {
                'success': True,
                'message': f'已{status_text}实盘同步',
                'id': strategy_id,
                'sync_live': sync_live
            }

        except HTTPException:
            raise
        except Exception as e:
            connection.rollback()
            raise
        finally:
            cursor.close()
            # connection.close()  # 复用连接，不关闭

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"切换实盘同步状态失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/pending-positions")
async def get_pending_positions():
    """获取待检查订单列表"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        cursor.execute("""
            SELECT
                pp.id,
                pp.strategy_id,
                pp.symbol,
                pp.direction as side,
                pp.signal_price,
                pp.signal_type,
                pp.status,
                pp.validation_count as check_count,
                pp.last_validation_time as last_check_at,
                pp.created_at,
                pp.signal_ema_diff_pct,
                pp.rejection_reason,
                ts.name as strategy_name
            FROM pending_positions pp
            LEFT JOIN trading_strategies ts ON pp.strategy_id = ts.id
            WHERE pp.status = 'pending'
            ORDER BY pp.created_at DESC
            LIMIT 100
        """)
        pending_list = cursor.fetchall()

        cursor.close()
        # connection.close()  # 复用连接，不关闭

        # 转换datetime为字符串
        for item in pending_list:
            if item.get('created_at'):
                item['created_at'] = item['created_at'].isoformat()
            if item.get('last_check_at'):
                item['last_check_at'] = item['last_check_at'].isoformat()

        return {
            'status': 'success',
            'data': pending_list
        }

    except Exception as e:
        logger.error(f"获取待检查订单失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/pending-positions/{position_id}")
async def delete_pending_position(position_id: int):
    """删除/取消待检查订单"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(pymysql.cursors.DictCursor)

        # 检查是否存在
        cursor.execute("SELECT id, status FROM pending_positions WHERE id = %s", (position_id,))
        position = cursor.fetchone()

        if not position:
            cursor.close()
            # connection.close()  # 复用连接，不关闭
            raise HTTPException(status_code=404, detail="待检查订单不存在")

        # 更新状态为已取消
        cursor.execute("""
            UPDATE pending_positions
            SET status = 'cancelled', updated_at = NOW()
            WHERE id = %s
        """, (position_id,))
        connection.commit()

        cursor.close()
        # connection.close()  # 复用连接，不关闭

        logger.info(f"已取消待检查订单: ID={position_id}")

        return {
            'status': 'success',
            'message': '已取消待检查订单'
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消待检查订单失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))