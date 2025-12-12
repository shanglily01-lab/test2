#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试模拟盘平仓同步实盘

使用方法：
    python3 test_paper_live_sync.py

测试流程：
    1. 初始化模拟引擎和实盘引擎
    2. 绑定实盘引擎到模拟引擎
    3. 在模拟盘创建一个测试持仓
    4. 调用模拟盘平仓，检查是否同步到实盘
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from decimal import Decimal
import pymysql

# 数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}


def test_sync():
    """测试模拟盘平仓同步实盘"""

    print("=" * 60)
    print("测试模拟盘平仓同步实盘")
    print("=" * 60)

    # 1. 初始化模拟引擎
    print("\n[1] 初始化模拟交易引擎...")
    from app.trading.futures_trading_engine import FuturesTradingEngine
    futures_engine = FuturesTradingEngine(db_config)
    print(f"   ✅ 模拟引擎初始化成功")
    print(f"   live_engine 初始状态: {futures_engine.live_engine}")

    # 2. 初始化实盘引擎并绑定
    print("\n[2] 初始化实盘交易引擎...")
    from app.trading.binance_futures_engine import BinanceFuturesEngine
    live_engine = BinanceFuturesEngine(db_config)
    print(f"   ✅ 实盘引擎初始化成功")

    # 绑定实盘引擎到模拟引擎
    futures_engine.live_engine = live_engine
    print(f"   ✅ 已绑定实盘引擎到模拟引擎")
    print(f"   live_engine 绑定后状态: {futures_engine.live_engine is not None}")

    # 3. 查找一个有 syncLive=true 的策略的模拟盘持仓
    print("\n[3] 查找测试持仓...")
    conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 查找一个开放的模拟盘持仓，且关联的策略启用了 syncLive
    cursor.execute("""
        SELECT p.id, p.symbol, p.position_side, p.quantity, p.entry_price, p.strategy_id,
               s.name as strategy_name, s.config
        FROM futures_positions p
        LEFT JOIN trading_strategies s ON p.strategy_id = s.id
        WHERE p.status = 'open'
        ORDER BY p.id DESC
        LIMIT 10
    """)
    positions = cursor.fetchall()

    if not positions:
        print("   ⚠️ 没有找到开放的模拟盘持仓")
        cursor.close()
        conn.close()
        return

    print(f"   找到 {len(positions)} 个开放持仓:")
    for pos in positions:
        config = pos.get('config', '{}')
        sync_live = False
        if config:
            import json
            try:
                if isinstance(config, str):
                    config = json.loads(config)
                sync_live = config.get('syncLive', False)
            except:
                pass
        print(f"   - ID={pos['id']}, {pos['symbol']} {pos['position_side']}, qty={pos['quantity']}, strategy={pos.get('strategy_name', 'N/A')}, syncLive={sync_live}")

    # 选择第一个持仓进行测试
    test_position = positions[0]
    position_id = test_position['id']
    symbol = test_position['symbol']
    position_side = test_position['position_side']

    print(f"\n   选择持仓 ID={position_id} ({symbol} {position_side}) 进行测试")

    # 4. 检查实盘是否有对应持仓
    print("\n[4] 检查实盘持仓...")
    live_positions = live_engine.get_open_positions()
    matching_live = [p for p in live_positions if p['symbol'] == symbol and p['position_side'] == position_side]

    if matching_live:
        print(f"   ✅ 实盘有 {len(matching_live)} 个 {symbol} {position_side} 持仓:")
        for lp in matching_live:
            print(f"      - qty={lp['quantity']}, entry_price={lp['entry_price']}")
    else:
        print(f"   ⚠️ 实盘没有 {symbol} {position_side} 持仓，同步平仓将失败")

    # 5. 询问是否执行平仓测试
    print("\n" + "=" * 60)
    print("⚠️ 注意：下一步将执行真实平仓操作！")
    print(f"   - 模拟盘持仓 ID={position_id} 将被平仓")
    if matching_live:
        print(f"   - 实盘 {symbol} {position_side} 持仓也将被同步平仓")
    print("=" * 60)

    choice = input("\n是否执行平仓测试? (yes/no): ").strip().lower()

    if choice != 'yes':
        print("已取消测试")
        cursor.close()
        conn.close()
        return

    # 6. 执行平仓
    print("\n[5] 执行模拟盘平仓...")
    close_result = futures_engine.close_position(
        position_id=position_id,
        reason='test_sync'
    )

    print(f"\n平仓结果:")
    print(f"   success: {close_result.get('success')}")
    if close_result.get('success'):
        print(f"   symbol: {close_result.get('symbol')}")
        print(f"   close_quantity: {close_result.get('close_quantity')}")
        print(f"   close_price: {close_result.get('close_price')}")
        print(f"   realized_pnl: {close_result.get('realized_pnl')}")
    else:
        print(f"   error: {close_result.get('error')}")
        print(f"   message: {close_result.get('message')}")

    # 7. 检查实盘持仓是否被平仓
    print("\n[6] 检查实盘持仓变化...")
    live_positions_after = live_engine.get_open_positions()
    matching_live_after = [p for p in live_positions_after if p['symbol'] == symbol and p['position_side'] == position_side]

    if len(matching_live_after) < len(matching_live):
        print(f"   ✅ 实盘持仓已减少: {len(matching_live)} -> {len(matching_live_after)}")
    elif len(matching_live_after) == len(matching_live):
        print(f"   ⚠️ 实盘持仓数量未变化: {len(matching_live_after)}")
        # 检查数量是否减少
        before_qty = sum(float(p['quantity']) for p in matching_live)
        after_qty = sum(float(p['quantity']) for p in matching_live_after)
        if after_qty < before_qty:
            print(f"   ✅ 但总数量已减少: {before_qty} -> {after_qty}")
        else:
            print(f"   ❌ 同步平仓可能失败！")

    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == '__main__':
    test_sync()
