"""
模拟交易 API 接口
提供账户管理、下单、持仓查询等功能
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List
from decimal import Decimal
from datetime import datetime
import yaml
from functools import lru_cache

from app.trading.paper_trading_engine import PaperTradingEngine
from app.services.price_cache_service import get_global_price_cache

router = APIRouter(prefix="/api/paper-trading", tags=["模拟交易"])

# ==================== 依赖注入：延迟初始化（修复阻塞问题）====================

@lru_cache()
def get_config():
    """缓存配置文件读取"""
    with open('config.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def get_db_config():
    """获取数据库配置"""
    config = get_config()
    return config.get('database', {}).get('mysql', {})

def get_engine():
    """获取 PaperTradingEngine 实例（依赖注入，使用价格缓存优化）"""
    db_config = get_db_config()
    price_cache = get_global_price_cache()  # 获取全局价格缓存
    return PaperTradingEngine(db_config, price_cache_service=price_cache)


# ==================== 请求模型 ====================

class PlaceOrderRequest(BaseModel):
    """下单请求"""
    account_id: Optional[int] = None  # None表示使用默认账户
    symbol: str  # 交易对，如 BTC/USDT
    side: str  # BUY 或 SELL
    quantity: float  # 数量
    order_type: str = "MARKET"  # MARKET 或 LIMIT
    price: Optional[float] = None  # 限价单价格
    order_source: str = "manual"  # manual, signal, auto


class CreateAccountRequest(BaseModel):
    """创建账户请求"""
    account_name: str
    initial_balance: float = 10000.0


# ==================== API 接口 ====================

@router.get("/account")
async def get_account(account_id: Optional[int] = None, engine: PaperTradingEngine = Depends(get_engine)):

    """
    获取账户信息

    Args:
        account_id: 账户ID，不传则获取默认账户

    Returns:
        账户详细信息
    """
    try:
        summary = engine.get_account_summary(account_id or 1)
        if not summary:
            raise HTTPException(status_code=404, detail="账户不存在")

        account = summary['account']

        # 转换 Decimal 为 float
        return {
            "account": {
                "id": account['id'],
                "account_name": account['account_name'],
                "current_balance": float(account['current_balance']),
                "total_equity": float(account['total_equity']),
                "initial_balance": float(account['initial_balance']),
                "realized_pnl": float(account['realized_pnl']),
                "unrealized_pnl": float(account['unrealized_pnl']),
                "total_profit_loss": float(account['total_profit_loss']),
                "total_profit_loss_pct": float(account['total_profit_loss_pct']),
                "total_trades": account['total_trades'],
                "winning_trades": account['winning_trades'],
                "losing_trades": account['losing_trades'],
                "win_rate": float(account['win_rate']),
                "status": account['status']
            },
            "positions_count": len(summary['positions']),
            "recent_trades_count": len(summary['recent_trades'])
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/account/summary")
async def get_account_summary(account_id: Optional[int] = None, engine: PaperTradingEngine = Depends(get_engine)):
    """
    获取账户完整摘要（包括持仓、订单、交易历史）

    Returns:
        完整账户摘要
    """
    try:
        summary = engine.get_account_summary(account_id or 1)
        if not summary:
            raise HTTPException(status_code=404, detail="账户不存在")

        # 转换数据
        account = summary['account']
        positions = []
        for pos in summary['positions']:
            positions.append({
                "symbol": pos['symbol'],
                "quantity": float(pos['quantity']),
                "available_quantity": float(pos['available_quantity']),
                "avg_entry_price": float(pos['avg_entry_price']),
                "current_price": float(pos['current_price']) if pos['current_price'] else 0,
                "market_value": float(pos['market_value']) if pos['market_value'] else 0,
                "unrealized_pnl": float(pos['unrealized_pnl']) if pos['unrealized_pnl'] else 0,
                "unrealized_pnl_pct": float(pos['unrealized_pnl_pct']) if pos['unrealized_pnl_pct'] else 0,
                "first_buy_time": pos['first_buy_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['first_buy_time'] else None
            })

        recent_trades = []
        for trade in summary['recent_trades']:
            recent_trades.append({
                "trade_id": trade['trade_id'],
                "symbol": trade['symbol'],
                "side": trade['side'],
                "price": float(trade['price']),
                "quantity": float(trade['quantity']),
                "total_amount": float(trade['total_amount']),
                "fee": float(trade['fee']),
                "realized_pnl": float(trade['realized_pnl']) if trade['realized_pnl'] else None,
                "pnl_pct": float(trade['pnl_pct']) if trade['pnl_pct'] else None,
                "trade_time": trade['trade_time'].strftime('%Y-%m-%d %H:%M:%S')
            })

        return {
            "account": {
                "id": account['id'],
                "account_name": account['account_name'],
                "current_balance": float(account['current_balance']),
                "total_equity": float(account['total_equity']),
                "initial_balance": float(account['initial_balance']),
                "realized_pnl": float(account['realized_pnl']),
                "unrealized_pnl": float(account['unrealized_pnl']),
                "total_profit_loss": float(account['total_profit_loss']),
                "total_profit_loss_pct": float(account['total_profit_loss_pct']),
                "total_trades": account['total_trades'],
                "winning_trades": account['winning_trades'],
                "losing_trades": account['losing_trades'],
                "win_rate": float(account['win_rate'])
            },
            "positions": positions,
            "recent_trades": recent_trades
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/account")
async def create_account(request: CreateAccountRequest, engine: PaperTradingEngine = Depends(get_engine)):
    """
    创建新账户

    Returns:
        新账户ID
    """
    try:
        account_id = engine.create_account(
            request.account_name,
            Decimal(str(request.initial_balance))
        )
        return {
            "success": True,
            "account_id": account_id,
            "message": f"账户 {request.account_name} 创建成功"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/order")
async def place_order(request: PlaceOrderRequest, engine: PaperTradingEngine = Depends(get_engine)):
    """
    下单（买入/卖出）

    Returns:
        订单执行结果
    """
    try:
        account_id = request.account_id or 1

        success, message, order_id = engine.place_order(
            account_id=account_id,
            symbol=request.symbol,
            side=request.side.upper(),
            quantity=Decimal(str(request.quantity)),
            order_type=request.order_type.upper(),
            price=Decimal(str(request.price)) if request.price else None,
            order_source=request.order_source
        )

        if success:
            return {
                "success": True,
                "order_id": order_id,
                "message": message
            }
        else:
            raise HTTPException(status_code=400, detail=message)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/positions")
async def get_positions(account_id: Optional[int] = None, engine: PaperTradingEngine = Depends(get_engine)):
    """
    获取持仓列表

    Returns:
        持仓列表
    """
    try:
        summary = engine.get_account_summary(account_id or 1)
        if not summary:
            raise HTTPException(status_code=404, detail="账户不存在")

        positions = []
        for pos in summary['positions']:
            positions.append({
                "symbol": pos['symbol'],
                "quantity": float(pos['quantity']),
                "available_quantity": float(pos['available_quantity']),
                "avg_entry_price": float(pos['avg_entry_price']),
                "current_price": float(pos['current_price']) if pos['current_price'] else 0,
                "market_value": float(pos['market_value']) if pos['market_value'] else 0,
                "total_cost": float(pos['total_cost']),
                "unrealized_pnl": float(pos['unrealized_pnl']) if pos['unrealized_pnl'] else 0,
                "unrealized_pnl_pct": float(pos['unrealized_pnl_pct']) if pos['unrealized_pnl_pct'] else 0,
                "first_buy_time": pos['first_buy_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['first_buy_time'] else None,
                "last_update_time": pos['last_update_time'].strftime('%Y-%m-%d %H:%M:%S') if pos['last_update_time'] else None
            })

        return {
            "positions": positions,
            "total_count": len(positions)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trades")
async def get_trades(account_id: Optional[int] = None, limit: int = 50, engine: PaperTradingEngine = Depends(get_engine)):
    """
    获取交易历史

    Args:
        account_id: 账户ID
        limit: 返回数量限制

    Returns:
        交易历史列表
    """
    try:
        summary = engine.get_account_summary(account_id or 1)
        if not summary:
            raise HTTPException(status_code=404, detail="账户不存在")

        trades = []
        for trade in summary['recent_trades'][:limit]:
            trades.append({
                "trade_id": trade['trade_id'],
                "order_id": trade['order_id'],
                "symbol": trade['symbol'],
                "side": trade['side'],
                "price": float(trade['price']),
                "quantity": float(trade['quantity']),
                "total_amount": float(trade['total_amount']),
                "fee": float(trade['fee']),
                "realized_pnl": float(trade['realized_pnl']) if trade['realized_pnl'] else None,
                "pnl_pct": float(trade['pnl_pct']) if trade['pnl_pct'] else None,
                "trade_time": trade['trade_time'].strftime('%Y-%m-%d %H:%M:%S')
            })

        return {
            "trades": trades,
            "total_count": len(trades)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/price")
async def get_current_price(symbol: str, engine: PaperTradingEngine = Depends(get_engine)):
    """
    获取当前市场价格

    Args:
        symbol: 交易对（查询参数，如 ?symbol=BTC/USDT）
        engine: 自动注入的 Engine 实例

    Returns:
        当前价格
    """
    try:
        price = engine.get_current_price(symbol)

        if price == 0:
            raise HTTPException(
                status_code=404,
                detail=f"{symbol} 暂无价格数据，请确保数据采集器正在运行"
            )

        return {
            "symbol": symbol,
            "price": float(price),
            "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update-positions")
async def update_positions(account_id: Optional[int] = None, engine: PaperTradingEngine = Depends(get_engine)):
    """
    手动更新持仓市值和盈亏

    Returns:
        更新结果
    """
    try:
        engine.update_positions_value(account_id or 1)
        return {
            "success": True,
            "message": "持仓市值已更新"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/symbols")
async def get_symbols():
    """
    获取可交易的币种列表（从配置文件读取）

    Returns:
        交易对列表
    """
    try:
        config = get_config()
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
        return {
            "symbols": symbols,
            "total": len(symbols)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/ema-signals")
async def get_ema_signals(limit: int = 10):
    """
    获取最新的 EMA 买入信号

    Args:
        limit: 返回信号数量限制

    Returns:
        最新的 EMA 信号列表
    """
    try:
        from app.database.db_service import DatabaseService
        from sqlalchemy import text
        from datetime import datetime, timedelta

        db_config = get_db_config()
        db_service = DatabaseService({'database': {'mysql': db_config}})
        session = db_service.get_session()

        try:
            # 读取信号文件（如果存在）
            import os
            signal_file = 'signals/ema_alerts.txt'
            signals = []

            if os.path.exists(signal_file):
                # 读取最近的信号
                with open(signal_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    # 倒序读取最近的信号
                    for line in reversed(lines[-limit*10:]):  # 读取更多行以确保有足够的唯一信号
                        if '买入信号' in line and '时间:' in line:
                            try:
                                # 解析信号信息
                                parts = line.split()
                                if len(parts) >= 3:
                                    symbol = parts[1]
                                    strength = '未知'

                                    if 'STRONG' in line or '强' in line:
                                        strength = 'strong'
                                    elif 'MEDIUM' in line or '中' in line:
                                        strength = 'medium'
                                    elif 'WEAK' in line or '弱' in line:
                                        strength = 'weak'

                                    # 检查是否已存在相同交易对的信号
                                    if not any(s['symbol'] == symbol for s in signals):
                                        signals.append({
                                            'symbol': symbol,
                                            'signal_strength': strength,
                                            'signal_type': 'BUY',
                                            'timeframe': '15m',
                                            'message': line.strip(),
                                            'timestamp': datetime.now().isoformat()
                                        })

                                        if len(signals) >= limit:
                                            break
                            except Exception as e:
                                continue

            # 如果文件中没有信号，返回空列表
            return {
                "success": True,
                "signals": signals,
                "count": len(signals),
                "message": "从信号文件读取成功" if signals else "暂无信号"
            }

        finally:
            session.close()

    except Exception as e:
        return {
            "success": False,
            "signals": [],
            "count": 0,
            "message": f"读取信号失败: {str(e)}"
        }
