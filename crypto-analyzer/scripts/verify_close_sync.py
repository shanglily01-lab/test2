#!/usr/bin/env python3
"""
测试模拟盘平仓同步到实盘的功能
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.utils.config_loader import load_config
from app.trading.futures_trading_engine import FuturesTradingEngine
from app.trading.binance_futures_engine import BinanceFuturesEngine
from app.services.trade_notifier import init_trade_notifier

def test_close_sync():
    """测试平仓同步"""
    print("=" * 60)
    print("测试模拟盘平仓同步到实盘")
    print("=" * 60)

    # 加载配置
    config = load_config()
    db_config = {
        'host': config['database']['host'],
        'port': config['database']['port'],
        'user': config['database']['user'],
        'password': config['database']['password'],
        'database': config['database']['database']
    }

    # 初始化通知服务
    trade_notifier = init_trade_notifier(config)

    # 初始化模拟盘引擎
    print("\n1. 初始化模拟盘引擎...")
    futures_engine = FuturesTradingEngine(db_config, trade_notifier=trade_notifier)
    print(f"   futures_engine 创建成功")

    # 初始化实盘引擎
    print("\n2. 初始化实盘引擎...")
    try:
        live_engine = BinanceFuturesEngine(db_config, config)
        print(f"   live_engine 创建成功")
    except Exception as e:
        print(f"   ❌ live_engine 创建失败: {e}")
        return

    # 绑定 live_engine 到 futures_engine
    print("\n3. 绑定 live_engine 到 futures_engine...")
    futures_engine.live_engine = live_engine
    print(f"   绑定状态: {futures_engine.live_engine is not None}")

    # 查询模拟盘持仓
    print("\n4. 查询模拟盘持仓 (account_id=1)...")
    import pymysql
    conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, symbol, position_side, quantity, entry_price, strategy_id
        FROM futures_positions
        WHERE account_id = 1 AND status = 'open'
        ORDER BY created_at DESC
        LIMIT 5
    """)
    positions = cursor.fetchall()
    cursor.close()
    conn.close()

    if not positions:
        print("   没有模拟盘持仓，无法测试平仓同步")
        return

    print(f"   找到 {len(positions)} 个模拟盘持仓:")
    for p in positions:
        print(f"   - ID={p['id']}, {p['symbol']} {p['position_side']}, 数量={p['quantity']}, strategy_id={p['strategy_id']}")

    # 选择第一个持仓进行测试
    test_position = positions[0]
    position_id = test_position['id']
    symbol = test_position['symbol']

    print(f"\n5. 测试平仓: position_id={position_id}, symbol={symbol}")
    print("   确认要平仓吗？(y/n): ", end="")
    confirm = input().strip().lower()

    if confirm != 'y':
        print("   取消测试")
        return

    # 执行平仓
    print("\n6. 执行平仓...")
    result = futures_engine.close_position(
        position_id=position_id,
        reason='test_close_sync'
    )

    print(f"\n7. 平仓结果:")
    print(f"   成功: {result.get('success')}")
    if result.get('success'):
        print(f"   平仓价格: {result.get('close_price')}")
        print(f"   盈亏: {result.get('realized_pnl')}")
    else:
        print(f"   错误: {result.get('error')}")

    print("\n" + "=" * 60)
    print("测试完成，请检查实盘是否同步平仓")
    print("=" * 60)


if __name__ == '__main__':
    test_close_sync()
