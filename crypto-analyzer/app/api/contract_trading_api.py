"""
模拟合约交易 API
Contract Trading API
"""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional, Dict, List
from pydantic import BaseModel, Field
from datetime import datetime

from app.trading.contract_trading_simulator import (
    ContractTradingSimulator,
    OrderSide,
    OrderType
)

router = APIRouter()

# 全局交易模拟器实例
simulator: Optional[ContractTradingSimulator] = None


# ==================== Request Models ====================

class CreateOrderRequest(BaseModel):
    """创建订单请求"""
    symbol: str = Field(..., description="交易对，如 BTC/USDT")
    side: str = Field(..., description="方向: LONG 或 SHORT")
    quantity: float = Field(..., gt=0, description="数量（张数）")
    order_type: str = Field(default="MARKET", description="订单类型: MARKET 或 LIMIT")
    price: Optional[float] = Field(None, description="限价单价格")
    leverage: int = Field(default=1, ge=1, le=125, description="杠杆倍数 (1-125)")
    stop_loss: Optional[float] = Field(None, description="止损价格")
    take_profit: Optional[float] = Field(None, description="止盈价格")


class ExecuteOrderRequest(BaseModel):
    """执行订单请求"""
    order_id: str = Field(..., description="订单ID")
    current_price: float = Field(..., gt=0, description="当前市场价格")


class UpdatePricesRequest(BaseModel):
    """更新价格请求"""
    prices: Dict[str, float] = Field(..., description="价格字典 {symbol: price}")


class ClosePositionRequest(BaseModel):
    """平仓请求"""
    symbol: str = Field(..., description="交易对")
    current_price: float = Field(..., gt=0, description="当前价格")


# ==================== API Endpoints ====================

@router.post("/api/contract-trading/init")
async def initialize_simulator(
    initial_balance: float = Query(10000, gt=0, description="初始资金")
):
    """
    初始化交易模拟器

    Args:
        initial_balance: 初始资金（USDT）

    Returns:
        初始化结果
    """
    global simulator

    simulator = ContractTradingSimulator(initial_balance=initial_balance)

    return {
        "success": True,
        "message": "交易模拟器初始化成功",
        "data": simulator.get_account_info()
    }


@router.get("/api/contract-trading/account")
async def get_account():
    """
    获取账户信息

    Returns:
        账户详细信息
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    return {
        "success": True,
        "data": simulator.get_account_info()
    }


@router.post("/api/contract-trading/order")
async def create_order(request: CreateOrderRequest):
    """
    创建订单

    Args:
        request: 订单请求

    Returns:
        订单信息
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    try:
        # 转换枚举
        side = OrderSide.LONG if request.side.upper() == "LONG" else OrderSide.SHORT
        order_type = OrderType.MARKET if request.order_type.upper() == "MARKET" else OrderType.LIMIT

        # 创建订单
        order = simulator.create_order(
            symbol=request.symbol,
            side=side,
            quantity=request.quantity,
            order_type=order_type,
            price=request.price,
            leverage=request.leverage,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit
        )

        if not order:
            raise HTTPException(status_code=400, detail="创建订单失败")

        return {
            "success": True,
            "message": "订单创建成功",
            "data": {
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side.value,
                "type": order.order_type.value,
                "quantity": order.quantity,
                "price": order.price,
                "leverage": order.leverage,
                "status": order.status.value,
                "created_at": order.created_at.isoformat()
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"创建订单失败: {str(e)}")


@router.post("/api/contract-trading/order/execute")
async def execute_order(request: ExecuteOrderRequest):
    """
    执行订单

    Args:
        request: 执行请求

    Returns:
        执行结果
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    try:
        print(f"[DEBUG] 执行订单请求: order_id={request.order_id}, price={request.current_price}")

        # 检查订单是否存在
        if request.order_id not in simulator.orders:
            error_msg = f"订单不存在: {request.order_id}"
            print(f"[ERROR] {error_msg}")
            raise HTTPException(status_code=400, detail=error_msg)

        order = simulator.orders[request.order_id]
        print(f"[DEBUG] 找到订单: {order.symbol} {order.side.value} {order.quantity}张 杠杆{order.leverage}x")

        success = await simulator.execute_order(
            order_id=request.order_id,
            current_price=request.current_price
        )

        if not success:
            error_msg = "执行订单失败 - 可能是保证金不足或订单状态不正确"
            print(f"[ERROR] {error_msg}")

            # 获取更详细的失败原因
            if order.status.value == "REJECTED":
                error_msg = "订单被拒绝 - 保证金不足或余额不足支付手续费"

            raise HTTPException(status_code=400, detail=error_msg)

        print(f"[SUCCESS] 订单执行成功: {request.order_id}")

        return {
            "success": True,
            "message": "订单执行成功",
            "data": {
                "order_id": request.order_id,
                "execution_price": request.current_price,
                "account": simulator.get_account_info()
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        error_msg = f"执行订单失败: {str(e)}"
        print(f"[ERROR] {error_msg}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/api/contract-trading/positions")
async def get_positions():
    """
    获取所有持仓

    Returns:
        持仓列表
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    positions = simulator.get_positions()

    return {
        "success": True,
        "data": {
            "positions": positions,
            "count": len(positions)
        }
    }


@router.post("/api/contract-trading/position/close")
async def close_position(request: ClosePositionRequest):
    """
    平仓

    Args:
        request: 平仓请求

    Returns:
        平仓结果
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    try:
        await simulator._close_position(request.symbol, request.current_price)

        return {
            "success": True,
            "message": "平仓成功",
            "data": simulator.get_account_info()
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"平仓失败: {str(e)}")


@router.post("/api/contract-trading/prices/update")
async def update_prices(request: UpdatePricesRequest):
    """
    更新价格并检查风控

    Args:
        request: 价格更新请求

    Returns:
        风控检查结果
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    try:
        # 更新账户权益
        simulator._update_account_equity(request.prices)

        # 检查爆仓
        liquidated = simulator.check_liquidation(request.prices)

        # 检查止盈止损
        triggered = simulator.check_stop_loss_take_profit(request.prices)

        return {
            "success": True,
            "data": {
                "account": simulator.get_account_info(),
                "liquidated_positions": liquidated,
                "triggered_orders": [{"symbol": t[0], "type": t[1]} for t in triggered]
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新价格失败: {str(e)}")


@router.get("/api/contract-trading/trades")
async def get_trades(limit: int = Query(100, ge=1, le=1000)):
    """
    获取交易历史

    Args:
        limit: 返回数量限制

    Returns:
        交易历史列表
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    trades = simulator.get_trades(limit=limit)

    return {
        "success": True,
        "data": {
            "trades": trades,
            "count": len(trades)
        }
    }


@router.get("/api/contract-trading/statistics")
async def get_statistics():
    """
    获取交易统计

    Returns:
        统计信息
    """
    if not simulator:
        raise HTTPException(status_code=400, detail="交易模拟器未初始化")

    stats = simulator.get_statistics()

    return {
        "success": True,
        "data": stats
    }


@router.delete("/api/contract-trading/reset")
async def reset_simulator():
    """
    重置交易模拟器

    Returns:
        重置结果
    """
    global simulator
    simulator = None

    return {
        "success": True,
        "message": "交易模拟器已重置"
    }
