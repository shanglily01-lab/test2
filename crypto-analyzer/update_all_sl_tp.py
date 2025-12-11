#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量为所有实盘持仓设置止损止盈订单

使用方法：
    python3 update_all_sl_tp.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal
import pymysql
from app.trading.binance_futures_engine import BinanceFuturesEngine

# 数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def get_open_positions():
    """获取所有开放的实盘持仓"""
    conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            p.id, p.symbol, p.position_side, p.quantity, p.entry_price,
            p.stop_loss_price, p.take_profit_price
        FROM live_futures_positions p
        WHERE p.status = 'OPEN'
        ORDER BY p.id DESC
    """)

    positions = cursor.fetchall()
    cursor.close()
    conn.close()
    return positions


def cancel_existing_algo_orders(engine, symbol):
    """取消某个交易对的所有现有 Algo 订单"""
    binance_symbol = symbol.replace('/', '')

    try:
        # 查询现有 Algo 订单
        algo_orders = engine._request('GET', '/fapi/v1/openAlgoOrders', {'symbol': binance_symbol})

        canceled_count = 0
        if isinstance(algo_orders, dict) and algo_orders.get('orders'):
            for order in algo_orders['orders']:
                algo_id = order.get('algoId')
                if algo_id:
                    result = engine._request('DELETE', '/fapi/v1/algoOrder', {
                        'symbol': binance_symbol,
                        'algoId': algo_id
                    })
                    if result.get('code') == '200':
                        canceled_count += 1
                        print(f"   已取消旧订单: {algo_id}")

        return canceled_count
    except Exception as e:
        print(f"   取消旧订单时出错: {e}")
        return 0


def update_sl_tp_for_position(engine, position):
    """为单个持仓设置止损止盈"""
    pos_id = position['id']
    symbol = position['symbol']
    position_side = position['position_side']
    quantity = Decimal(str(position['quantity']))
    entry_price = Decimal(str(position['entry_price']))
    stop_loss_price = Decimal(str(position['stop_loss_price'])) if position['stop_loss_price'] else None
    take_profit_price = Decimal(str(position['take_profit_price'])) if position['take_profit_price'] else None

    print(f"\n[{pos_id}] {symbol} {position_side}")
    print(f"   数量: {quantity}, 入场价: {entry_price}")
    print(f"   止损价: {stop_loss_price}, 止盈价: {take_profit_price}")

    if not stop_loss_price and not take_profit_price:
        print(f"   ⚠️ 没有止损止盈价格，跳过")
        return False, False

    # 先取消现有订单
    cancel_existing_algo_orders(engine, symbol)

    sl_success = False
    tp_success = False

    # 设置止损
    if stop_loss_price:
        # 验证止损价格
        sl_valid = False
        if position_side == 'LONG' and stop_loss_price < entry_price:
            sl_valid = True
        elif position_side == 'SHORT' and stop_loss_price > entry_price:
            sl_valid = True

        if sl_valid:
            sl_result = engine._place_stop_loss(symbol, position_side, quantity, stop_loss_price)
            if sl_result.get('success'):
                print(f"   ✅ 止损单设置成功: {stop_loss_price} (ID: {sl_result.get('order_id')})")
                sl_success = True
            else:
                print(f"   ❌ 止损单设置失败: {sl_result.get('error')}")
        else:
            print(f"   ⚠️ 止损价格无效，跳过 ({position_side} 入场价 {entry_price})")

    # 设置止盈
    if take_profit_price:
        # 验证止盈价格
        tp_valid = False
        if position_side == 'LONG' and take_profit_price > entry_price:
            tp_valid = True
        elif position_side == 'SHORT' and take_profit_price < entry_price:
            tp_valid = True

        if tp_valid:
            tp_result = engine._place_take_profit(symbol, position_side, quantity, take_profit_price)
            if tp_result.get('success'):
                print(f"   ✅ 止盈单设置成功: {take_profit_price} (ID: {tp_result.get('order_id')})")
                tp_success = True
            else:
                print(f"   ❌ 止盈单设置失败: {tp_result.get('error')}")
        else:
            print(f"   ⚠️ 止盈价格无效，跳过 ({position_side} 入场价 {entry_price})")

    return sl_success, tp_success


def main():
    print("=" * 60)
    print("批量更新实盘止损止盈订单")
    print("=" * 60)

    # 初始化引擎
    print("\n初始化交易引擎...")
    engine = BinanceFuturesEngine(db_config)

    # 获取所有持仓
    print("\n获取实盘持仓...")
    positions = get_open_positions()
    print(f"共 {len(positions)} 个持仓")

    # 统计
    sl_success_count = 0
    tp_success_count = 0
    sl_fail_count = 0
    tp_fail_count = 0

    # 批量设置
    for position in positions:
        sl_ok, tp_ok = update_sl_tp_for_position(engine, position)
        if sl_ok:
            sl_success_count += 1
        elif position['stop_loss_price']:
            sl_fail_count += 1
        if tp_ok:
            tp_success_count += 1
        elif position['take_profit_price']:
            tp_fail_count += 1

    # 汇总
    print("\n" + "=" * 60)
    print("执行完成")
    print("=" * 60)
    print(f"止损单: {sl_success_count} 成功, {sl_fail_count} 失败")
    print(f"止盈单: {tp_success_count} 成功, {tp_fail_count} 失败")


if __name__ == '__main__':
    main()
