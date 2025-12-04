"""
实盘合约交易API接口
提供币安实盘合约交易的HTTP接口
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List
from decimal import Decimal
from loguru import logger
import yaml
import pymysql

router = APIRouter(prefix="/api/live-trading", tags=["实盘交易"])

# 全局变量
_live_engine = None
_db_config = None


def get_db_config():
    """获取数据库配置"""
    global _db_config
    if _db_config is None:
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            _db_config = config.get('database', {}).get('mysql', {})
        except Exception as e:
            logger.error(f"加载数据库配置失败: {e}")
            _db_config = {}
    return _db_config


def get_live_engine():
    """获取实盘交易引擎实例"""
    global _live_engine
    if _live_engine is None:
        try:
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            db_config = get_db_config()
            _live_engine = BinanceFuturesEngine(db_config)
            logger.info("实盘交易引擎初始化成功")
        except Exception as e:
            logger.error(f"初始化实盘交易引擎失败: {e}")
            raise HTTPException(status_code=500, detail=f"初始化实盘交易引擎失败: {e}")
    return _live_engine


# ==================== 请求模型 ====================

class OpenPositionRequest(BaseModel):
    """开仓请求"""
    account_id: int = Field(default=1, description="账户ID")
    symbol: str = Field(..., description="交易对，如 BTC/USDT")
    position_side: str = Field(..., description="持仓方向: LONG 或 SHORT")
    quantity: float = Field(..., gt=0, description="开仓数量")
    leverage: int = Field(default=5, ge=1, le=125, description="杠杆倍数")
    limit_price: Optional[float] = Field(default=None, description="限价（None为市价）")
    stop_loss_pct: Optional[float] = Field(default=None, description="止损百分比")
    take_profit_pct: Optional[float] = Field(default=None, description="止盈百分比")
    stop_loss_price: Optional[float] = Field(default=None, description="止损价格")
    take_profit_price: Optional[float] = Field(default=None, description="止盈价格")
    source: str = Field(default="manual", description="来源")
    strategy_id: Optional[int] = Field(default=None, description="策略ID")


class ClosePositionRequest(BaseModel):
    """平仓请求"""
    position_id: int = Field(..., description="持仓ID")
    close_quantity: Optional[float] = Field(default=None, description="平仓数量（None为全部）")
    reason: str = Field(default="manual", description="平仓原因")


class SetLeverageRequest(BaseModel):
    """设置杠杆请求"""
    symbol: str = Field(..., description="交易对")
    leverage: int = Field(..., ge=1, le=125, description="杠杆倍数")


class CancelOrderRequest(BaseModel):
    """取消订单请求"""
    symbol: str = Field(..., description="交易对")
    order_id: str = Field(..., description="订单ID")


# ==================== API端点 ====================

@router.get("/test-connection")
async def test_connection():
    """
    测试币安API连接

    返回连接状态和账户余额
    """
    try:
        engine = get_live_engine()
        result = engine.test_connection()

        if result.get('success'):
            return {
                "success": True,
                "message": "币安API连接正常",
                "data": {
                    "balance": result.get('balance', 0),
                    "available": result.get('available', 0),
                    "server_time": result.get('server_time')
                }
            }
        else:
            return {
                "success": False,
                "message": result.get('error', '连接失败'),
                "data": None
            }
    except Exception as e:
        logger.error(f"测试连接失败: {e}")
        return {
            "success": False,
            "message": str(e),
            "data": None
        }


@router.get("/account/balance")
async def get_account_balance():
    """
    获取账户余额

    返回USDT余额信息
    """
    try:
        engine = get_live_engine()
        result = engine.get_account_balance()

        if result.get('success'):
            return {
                "success": True,
                "data": {
                    "asset": result.get('asset', 'USDT'),
                    "balance": float(result.get('balance', 0)),
                    "available": float(result.get('available', 0)),
                    "unrealized_pnl": float(result.get('unrealized_pnl', 0))
                }
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取余额失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account/info")
async def get_account_info():
    """
    获取账户详细信息

    返回完整的账户信息
    """
    try:
        engine = get_live_engine()
        result = engine.get_account_info()

        if result.get('success'):
            return {
                "success": True,
                "data": {
                    "total_margin_balance": float(result.get('total_margin_balance', 0)),
                    "available_balance": float(result.get('available_balance', 0)),
                    "total_unrealized_profit": float(result.get('total_unrealized_profit', 0)),
                    "total_wallet_balance": float(result.get('total_wallet_balance', 0))
                }
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取账户信息失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/price/{symbol:path}")
async def get_price(symbol: str):
    """
    获取当前价格

    Args:
        symbol: 交易对，如 BTCUSDT 或 BTC/USDT
    """
    try:
        # 统一格式
        if '/' not in symbol:
            if 'USDT' in symbol.upper():
                base = symbol.upper().replace('USDT', '')
                symbol = f"{base}/USDT"

        engine = get_live_engine()
        price = engine.get_current_price(symbol)

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "price": float(price)
            }
        }
    except Exception as e:
        logger.error(f"获取价格失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/price")
async def get_price_by_query(symbol: str = Query(..., description="交易对，如 BTC/USDT")):
    """
    获取当前价格（查询参数版本）

    Args:
        symbol: 交易对，如 BTCUSDT 或 BTC/USDT
    """
    try:
        # 统一格式
        if '/' not in symbol:
            if 'USDT' in symbol.upper():
                base = symbol.upper().replace('USDT', '')
                symbol = f"{base}/USDT"

        engine = get_live_engine()
        price = engine.get_current_price(symbol)

        return {
            "success": True,
            "data": {
                "symbol": symbol,
                "price": float(price)
            }
        }
    except Exception as e:
        logger.error(f"获取价格失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leverage")
async def set_leverage(request: SetLeverageRequest):
    """
    设置杠杆倍数

    设置指定交易对的杠杆
    """
    try:
        engine = get_live_engine()
        result = engine.set_leverage(request.symbol, request.leverage)

        if result.get('success'):
            return {
                "success": True,
                "message": f"杠杆已设置为 {request.leverage}x",
                "data": result
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"设置杠杆失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions():
    """
    获取当前持仓

    返回所有活跃持仓
    """
    try:
        engine = get_live_engine()
        positions = engine.get_open_positions()

        # 转换Decimal为float
        for pos in positions:
            for key, value in pos.items():
                if isinstance(value, Decimal):
                    pos[key] = float(value)

        return {
            "success": True,
            "data": positions,
            "count": len(positions)
        }
    except Exception as e:
        logger.error(f"获取持仓失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/open")
async def open_position(request: OpenPositionRequest):
    """
    开仓

    执行实盘开仓操作

    注意：这是实盘交易，会使用真实资金！
    """
    try:
        engine = get_live_engine()

        # 验证方向
        position_side = request.position_side.upper()
        if position_side not in ['LONG', 'SHORT']:
            raise HTTPException(status_code=400, detail="position_side 必须是 LONG 或 SHORT")

        logger.info(f"[实盘API] 收到开仓请求: {request.symbol} {position_side} "
                   f"{request.quantity} @ {request.limit_price or '市价'}")

        result = engine.open_position(
            account_id=request.account_id,
            symbol=request.symbol,
            position_side=position_side,
            quantity=Decimal(str(request.quantity)),
            leverage=request.leverage,
            limit_price=Decimal(str(request.limit_price)) if request.limit_price else None,
            stop_loss_pct=Decimal(str(request.stop_loss_pct)) if request.stop_loss_pct else None,
            take_profit_pct=Decimal(str(request.take_profit_pct)) if request.take_profit_pct else None,
            stop_loss_price=Decimal(str(request.stop_loss_price)) if request.stop_loss_price else None,
            take_profit_price=Decimal(str(request.take_profit_price)) if request.take_profit_price else None,
            source=request.source,
            strategy_id=request.strategy_id
        )

        if result.get('success'):
            return {
                "success": True,
                "message": result.get('message', '开仓成功'),
                "data": result
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"开仓失败: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/close")
async def close_position(request: ClosePositionRequest):
    """
    平仓

    执行实盘平仓操作

    注意：这是实盘交易！
    """
    try:
        engine = get_live_engine()

        logger.info(f"[实盘API] 收到平仓请求: position_id={request.position_id}, "
                   f"quantity={request.close_quantity}, reason={request.reason}")

        result = engine.close_position(
            position_id=request.position_id,
            close_quantity=Decimal(str(request.close_quantity)) if request.close_quantity else None,
            reason=request.reason
        )

        if result.get('success'):
            return {
                "success": True,
                "message": result.get('message', '平仓成功'),
                "data": result
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"平仓失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/orders")
async def get_open_orders(symbol: Optional[str] = None):
    """
    获取挂单

    返回所有未成交订单
    """
    try:
        engine = get_live_engine()
        orders = engine.get_open_orders(symbol)

        return {
            "success": True,
            "data": orders,
            "count": len(orders)
        }
    except Exception as e:
        logger.error(f"获取挂单失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/order")
async def cancel_order(request: CancelOrderRequest):
    """
    取消订单
    """
    try:
        engine = get_live_engine()
        result = engine.cancel_order(request.symbol, request.order_id)

        if result.get('success'):
            return {
                "success": True,
                "message": result.get('message', '订单已取消'),
                "data": result
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消订单失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/orders/{symbol}")
async def cancel_all_orders(symbol: str):
    """
    取消指定交易对的所有订单
    """
    try:
        engine = get_live_engine()
        result = engine.cancel_all_orders(symbol)

        if result.get('success'):
            return {
                "success": True,
                "message": result.get('message'),
                "data": result
            }
        else:
            raise HTTPException(status_code=400, detail=result.get('error'))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"取消订单失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 账户管理 ====================

@router.get("/accounts")
async def get_live_accounts():
    """
    获取实盘账户列表
    """
    try:
        db_config = get_db_config()
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM live_trading_accounts ORDER BY is_default DESC, id")
                accounts = cursor.fetchall()

                return {
                    "success": True,
                    "data": accounts,
                    "count": len(accounts)
                }
        finally:
            connection.close()

    except Exception as e:
        logger.error(f"获取账户列表失败: {e}")
        # 如果表不存在，返回空列表
        if "doesn't exist" in str(e):
            return {
                "success": True,
                "data": [],
                "count": 0,
                "message": "请先执行数据库迁移脚本创建表"
            }
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/accounts/sync")
async def sync_account_balance(account_id: int = 1):
    """
    同步账户余额

    从币安获取最新余额并更新本地记录
    """
    try:
        engine = get_live_engine()
        balance_result = engine.get_account_balance()

        if not balance_result.get('success'):
            raise HTTPException(status_code=400, detail=balance_result.get('error'))

        # 更新本地数据库
        db_config = get_db_config()
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE live_trading_accounts
                    SET total_balance = %s,
                        available_balance = %s,
                        unrealized_pnl = %s,
                        last_sync_time = NOW()
                    WHERE id = %s""",
                    (float(balance_result.get('balance', 0)),
                     float(balance_result.get('available', 0)),
                     float(balance_result.get('unrealized_pnl', 0)),
                     account_id)
                )

                return {
                    "success": True,
                    "message": "账户余额已同步",
                    "data": {
                        "balance": float(balance_result.get('balance', 0)),
                        "available": float(balance_result.get('available', 0)),
                        "unrealized_pnl": float(balance_result.get('unrealized_pnl', 0))
                    }
                }
        finally:
            connection.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"同步账户余额失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ==================== 风控设置 ====================

@router.get("/risk-config/{account_id}")
async def get_risk_config(account_id: int):
    """
    获取风控配置
    """
    try:
        db_config = get_db_config()
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """SELECT max_position_value, max_daily_loss,
                              max_total_positions, max_leverage
                    FROM live_trading_accounts WHERE id = %s""",
                    (account_id,)
                )
                config = cursor.fetchone()

                if not config:
                    raise HTTPException(status_code=404, detail="账户不存在")

                return {
                    "success": True,
                    "data": config
                }
        finally:
            connection.close()

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"获取风控配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class RiskConfigRequest(BaseModel):
    """风控配置请求"""
    max_position_value: float = Field(default=1000, description="单笔最大持仓价值")
    max_daily_loss: float = Field(default=100, description="日最大亏损")
    max_total_positions: int = Field(default=5, description="最大同时持仓数")
    max_leverage: int = Field(default=10, description="最大杠杆")


@router.put("/risk-config/{account_id}")
async def update_risk_config(account_id: int, request: RiskConfigRequest):
    """
    更新风控配置
    """
    try:
        db_config = get_db_config()
        connection = pymysql.connect(
            host=db_config.get('host', 'localhost'),
            port=db_config.get('port', 3306),
            user=db_config.get('user', 'root'),
            password=db_config.get('password', ''),
            database=db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True
        )

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """UPDATE live_trading_accounts
                    SET max_position_value = %s,
                        max_daily_loss = %s,
                        max_total_positions = %s,
                        max_leverage = %s
                    WHERE id = %s""",
                    (request.max_position_value, request.max_daily_loss,
                     request.max_total_positions, request.max_leverage, account_id)
                )

                return {
                    "success": True,
                    "message": "风控配置已更新"
                }
        finally:
            connection.close()

    except Exception as e:
        logger.error(f"更新风控配置失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
