#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试币安 Algo Order API 设置止损止盈

使用方法：
    python3 test_algo_order_api.py

测试内容：
    1. 测试设置止损单 (STOP_MARKET via /fapi/v1/algoOrder)
    2. 测试设置止盈单 (TAKE_PROFIT_MARKET via /fapi/v1/algoOrder)
    3. 查询 Algo 订单
    4. 取消 Algo 订单（可选）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal
from app.trading.binance_futures_engine import BinanceFuturesEngine

# 数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def test_algo_order():
    """测试 Algo Order API"""

    # 初始化引擎
    print("初始化交易引擎...")
    engine = BinanceFuturesEngine(db_config)

    # 使用 DOT/USDT SHORT 持仓测试 (id=119)
    symbol = 'DOT/USDT'
    position_side = 'SHORT'
    quantity = Decimal('487.9')
    entry_price = Decimal('2.049')

    # 做空止损应高于入场价，止盈应低于入场价
    stop_loss_price = Decimal('2.10')   # 高于入场价 2.049 约 2.5%
    take_profit_price = Decimal('1.98') # 低于入场价 2.049 约 3.4%

    print(f"=" * 60)
    print(f"测试 Algo Order API")
    print(f"=" * 60)
    print(f"交易对: {symbol}")
    print(f"方向: {position_side}")
    print(f"数量: {quantity}")
    print(f"入场价: {entry_price}")
    print(f"止损价: {stop_loss_price} (做空止损应高于入场价)")
    print(f"止盈价: {take_profit_price} (做空止盈应低于入场价)")
    print(f"-" * 60)

    # 测试设置止损单
    print("\n[1] 测试设置止损单 (STOP_MARKET via /fapi/v1/algoOrder)...")
    sl_result = engine._place_stop_loss(
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        stop_price=stop_loss_price
    )

    sl_order_id = None
    if sl_result.get('success'):
        sl_order_id = sl_result.get('order_id')
        print(f"✅ 止损单设置成功!")
        print(f"   订单ID: {sl_order_id}")
        print(f"   止损价: {sl_result.get('stop_price')}")
    else:
        print(f"❌ 止损单设置失败: {sl_result.get('error')}")

    # 测试设置止盈单
    print("\n[2] 测试设置止盈单 (TAKE_PROFIT_MARKET via /fapi/v1/algoOrder)...")
    tp_result = engine._place_take_profit(
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        take_profit_price=take_profit_price
    )

    tp_order_id = None
    if tp_result.get('success'):
        tp_order_id = tp_result.get('order_id')
        print(f"✅ 止盈单设置成功!")
        print(f"   订单ID: {tp_order_id}")
        print(f"   止盈价: {tp_result.get('take_profit_price')}")
    else:
        print(f"❌ 止盈单设置失败: {tp_result.get('error')}")

    # 查询 Algo 订单
    print("\n[3] 查询 Algo 订单...")
    binance_symbol = symbol.replace('/', '')
    algo_orders = engine._request('GET', '/fapi/v1/algoOrder/openOrders', {'symbol': binance_symbol})

    if isinstance(algo_orders, dict) and algo_orders.get('orders'):
        print(f"当前 Algo 订单:")
        for order in algo_orders['orders']:
            print(f"   - algoId: {order.get('algoId')}, "
                  f"type: {order.get('orderType')}, "
                  f"side: {order.get('side')}, "
                  f"triggerPrice: {order.get('triggerPrice')}, "
                  f"quantity: {order.get('quantity')}")
    elif isinstance(algo_orders, list) and len(algo_orders) > 0:
        print(f"当前 Algo 订单:")
        for order in algo_orders:
            print(f"   - algoId: {order.get('algoId')}, "
                  f"type: {order.get('orderType')}, "
                  f"side: {order.get('side')}, "
                  f"triggerPrice: {order.get('triggerPrice')}, "
                  f"quantity: {order.get('quantity')}")
    else:
        print(f"查询结果: {algo_orders}")

    # 询问是否取消测试订单
    print("\n[4] 是否取消刚创建的测试订单? (y/n)")
    choice = input().strip().lower()

    if choice == 'y':
        if sl_order_id:
            print(f"取消止损单 {sl_order_id}...")
            cancel_result = engine._request('DELETE', '/fapi/v1/algoOrder', {
                'symbol': binance_symbol,
                'algoId': sl_order_id
            })
            print(f"   结果: {cancel_result}")

        if tp_order_id:
            print(f"取消止盈单 {tp_order_id}...")
            cancel_result = engine._request('DELETE', '/fapi/v1/algoOrder', {
                'symbol': binance_symbol,
                'algoId': tp_order_id
            })
            print(f"   结果: {cancel_result}")

    print(f"\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

if __name__ == '__main__':
    test_algo_order()
