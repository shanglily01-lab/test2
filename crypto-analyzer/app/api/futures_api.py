#!/usr/bin/env python3
"""
合约交易 API
Futures Trading API

提供合约交易的HTTP接口：开仓、平仓、查询持仓、基于信号自动开仓
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel, Field
import yaml
from decimal import Decimal
from typing import Dict, List, Optional
from datetime import datetime
from loguru import logger
import pymysql

from app.trading.futures_trading_engine import FuturesTradingEngine

# 创建 Router
router = APIRouter(prefix='/api/futures', tags=['futures'])

# 加载配置
config_path = Path(__file__).parent.parent.parent / 'config.yaml'
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['database']['mysql']

# 初始化交易引擎
engine = FuturesTradingEngine(db_config)


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
            connection = pymysql.connect(**db_config)
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
                close_time
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
            connection.close()

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


@router.get('/positions/{position_id}')
async def get_position(position_id: int):
    """获取单个持仓详情"""
    try:
        connection = pymysql.connect(**db_config)
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
        connection.close()

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
        connection = pymysql.connect(**db_config)
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
        connection.close()
        
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


@router.delete('/orders/{order_id}')
async def cancel_order(order_id: str, account_id: int = 2):
    """
    撤销订单
    
    - **order_id**: 订单ID
    - **account_id**: 账户ID（默认2）
    """
    try:
        connection = pymysql.connect(**db_config)
        cursor = connection.cursor(pymysql.cursors.DictCursor)
        
        # 检查订单是否存在且未成交
        cursor.execute(
            """SELECT id, status FROM futures_orders 
            WHERE order_id = %s AND account_id = %s""",
            (order_id, account_id)
        )
        order = cursor.fetchone()
        
        if not order:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=404, detail="订单不存在")
        
        if order['status'] not in ['PENDING', 'PARTIALLY_FILLED']:
            cursor.close()
            connection.close()
            raise HTTPException(status_code=400, detail=f"订单状态为 {order['status']}，无法撤销")
        
        # 更新订单状态
        cursor.execute(
            """UPDATE futures_orders 
            SET status = 'CANCELLED', updated_at = NOW()
            WHERE order_id = %s AND account_id = %s""",
            (order_id, account_id)
        )
        
        # 释放冻结的保证金
        cursor.execute(
            """SELECT margin FROM futures_orders 
            WHERE order_id = %s AND account_id = %s""",
            (order_id, account_id)
        )
        order_info = cursor.fetchone()
        
        if order_info and order_info['margin']:
            # 释放保证金到可用余额（current_balance = current_balance + margin, frozen_balance = frozen_balance - margin）
            cursor.execute(
                """UPDATE paper_trading_accounts 
                SET current_balance = current_balance + %s,
                    frozen_balance = frozen_balance - %s,
                    updated_at = NOW()
                WHERE id = %s""",
                (order_info['margin'], order_info['margin'], account_id)
            )
        
        connection.commit()
        cursor.close()
        connection.close()
        
        return {
            'success': True,
            'message': '订单已撤销'
        }
        
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

        # 平仓
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
        connection = pymysql.connect(**db_config)
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
        connection.close()

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
        connection = pymysql.connect(**db_config)
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
        connection.close()

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
        
        timeout = ClientTimeout(total=5)
        
        # 1. 优先从Binance合约API获取
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
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
        except Exception as e:
            logger.debug(f"Binance合约API获取失败: {e}")
        
        # 2. 如果Binance失败，尝试从Gate.io合约API获取
        if not price:
            try:
                gate_symbol = symbol.replace('/', '_')
                async with aiohttp.ClientSession(timeout=timeout) as session:
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
            except Exception as e:
                logger.debug(f"Gate.io合约API获取失败: {e}")
        
        # 3. 如果都失败，尝试从数据库获取最新价格（现货价格作为fallback）
        if not price:
            try:
                from app.database.db_service import DatabaseService
                db_service = DatabaseService(db_config)
                latest_kline = db_service.get_latest_kline(symbol, '1m')
                if latest_kline:
                    price = float(latest_kline.close)
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


# ==================== 健康检查 ====================

@router.get('/health')
async def health():
    """健康检查"""
    return {
        'success': True,
        'service': 'futures-api',
        'status': 'running'
    }
