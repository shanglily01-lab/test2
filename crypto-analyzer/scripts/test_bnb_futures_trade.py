#!/usr/bin/env python3
"""
测试 BNB/USDT 合约交易脚本

功能：
1. 测试API连接和权限
2. 查询账户余额
3. 执行小额测试交易（开仓+平仓）

用法：
    python scripts/test_bnb_futures_trade.py [--trade]

参数：
    --trade     实际执行交易测试（不加此参数只测试连接）
"""

import sys
import os
import argparse
from decimal import Decimal

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from app.trading.binance_futures_engine import BinanceFuturesEngine


def load_config():
    """加载配置"""
    with open('config.yaml', 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def test_connection(engine):
    """测试API连接"""
    print("\n" + "=" * 60)
    print("【1】测试API连接")
    print("=" * 60)

    result = engine.test_connection()

    if result.get('success'):
        print("✓ API连接成功！")
        print(f"  服务器时间: {result.get('server_time')}")
        print(f"  账户总余额: {result.get('balance'):.4f} USDT")
        print(f"  可用余额: {result.get('available'):.4f} USDT")
        return True, result.get('available', 0)
    else:
        print(f"✗ API连接失败: {result.get('error')}")
        return False, 0


def test_get_price(engine, symbol='BNB/USDT'):
    """测试获取价格"""
    print("\n" + "=" * 60)
    print(f"【2】获取 {symbol} 价格")
    print("=" * 60)

    price = engine.get_current_price(symbol)

    if price > 0:
        print(f"✓ {symbol} 当前价格: {price}")
        return price
    else:
        print(f"✗ 无法获取 {symbol} 价格")
        return None


def test_get_positions(engine):
    """测试获取持仓"""
    print("\n" + "=" * 60)
    print("【3】查询当前持仓")
    print("=" * 60)

    positions = engine.get_open_positions()

    if positions:
        print(f"  当前持仓数: {len(positions)}")
        for pos in positions:
            symbol = pos.get('symbol')
            side = pos.get('position_side')
            qty = pos.get('quantity')
            entry = pos.get('entry_price')
            mark = pos.get('mark_price')
            pnl = pos.get('unrealized_pnl')
            leverage = pos.get('leverage')
            print(f"  - {symbol} {side} {leverage}x")
            print(f"    数量: {qty}, 入场价: {entry}, 标记价: {mark}")
            print(f"    未实现盈亏: {pnl:.4f} USDT")
    else:
        print("  当前无持仓")

    return positions


def test_get_orders(engine, symbol='BNB/USDT'):
    """测试获取挂单"""
    print("\n" + "=" * 60)
    print(f"【4】查询 {symbol} 挂单")
    print("=" * 60)

    orders = engine.get_open_orders(symbol)

    if orders:
        print(f"  当前挂单数: {len(orders)}")
        for order in orders:
            order_id = order.get('orderId')
            order_type = order.get('type')
            side = order.get('side')
            price = order.get('price')
            qty = order.get('origQty')
            print(f"  - ID:{order_id} {order_type} {side} {qty} @ {price}")
    else:
        print("  当前无挂单")

    return orders


def execute_test_trade(engine, symbol='BNB/USDT', test_usdt=6.0):
    """
    执行测试交易

    Args:
        engine: 交易引擎
        symbol: 交易对
        test_usdt: 测试金额(USDT)，默认6 USDT（最小5 USDT）
    """
    print("\n" + "=" * 60)
    print(f"【5】执行 {symbol} 测试交易")
    print("=" * 60)
    print(f"  测试金额: {test_usdt} USDT")
    print("  杠杆: 1x")
    print("  方向: LONG (做多)")
    print("-" * 60)

    # 获取当前价格
    price = engine.get_current_price(symbol)
    if price == 0:
        print("✗ 无法获取价格，交易取消")
        return False

    print(f"  当前价格: {price}")

    # 计算数量
    quantity = Decimal(str(test_usdt)) / price
    print(f"  计算数量: {quantity}")

    # 精度处理
    binance_symbol = engine._convert_symbol(symbol)
    info = engine._symbol_info_cache.get(binance_symbol, {})
    step_size = info.get('step_size', Decimal('0.001'))
    min_qty = info.get('min_qty', Decimal('0.001'))
    min_notional = info.get('min_notional', Decimal('5'))

    print(f"  交易对信息: step_size={step_size}, min_qty={min_qty}, min_notional={min_notional}")

    quantity = engine._round_quantity(quantity, symbol)
    print(f"  调整后数量: {quantity}")

    notional = quantity * price
    print(f"  名义价值: {notional:.4f} USDT")

    if quantity < min_qty:
        print(f"✗ 数量 {quantity} 小于最小值 {min_qty}")
        return False

    if notional < min_notional:
        print(f"✗ 名义价值 {notional} 小于最小值 {min_notional}")
        return False

    # 确认执行
    print("-" * 60)
    print("准备执行开仓...")
    confirm = input("确认执行? (yes/no): ").strip().lower()

    if confirm != 'yes':
        print("用户取消交易")
        return False

    # 执行开仓
    print("\n>>> 执行开仓...")
    open_result = engine.open_position(
        account_id=1,  # 使用账户ID 1
        symbol=symbol,
        position_side='LONG',
        quantity=quantity,
        leverage=1,
        stop_loss_pct=None,  # 测试不设止损
        take_profit_pct=None,  # 测试不设止盈
        source='test_script'
    )

    if not open_result.get('success'):
        print(f"✗ 开仓失败: {open_result.get('error')}")
        return False

    print("✓ 开仓成功！")
    print(f"  持仓ID: {open_result.get('position_id')}")
    print(f"  订单ID: {open_result.get('order_id')}")
    print(f"  入场价: {open_result.get('entry_price')}")
    print(f"  数量: {open_result.get('quantity')}")

    position_id = open_result.get('position_id')

    # 等待用户确认平仓
    print("\n" + "-" * 60)
    print("持仓已建立，请检查币安账户确认")
    close_confirm = input("是否立即平仓? (yes/no): ").strip().lower()

    if close_confirm != 'yes':
        print("保留持仓，稍后可手动平仓")
        return True

    # 执行平仓
    print("\n>>> 执行平仓...")
    close_result = engine.close_position(
        position_id=position_id,
        reason='test_close'
    )

    if not close_result.get('success'):
        print(f"✗ 平仓失败: {close_result.get('error')}")
        return False

    print("✓ 平仓成功！")
    print(f"  平仓价: {close_result.get('close_price')}")
    print(f"  平仓数量: {close_result.get('close_quantity')}")
    print(f"  盈亏: {close_result.get('realized_pnl'):.4f} USDT")
    print(f"  ROI: {close_result.get('roi'):.2f}%")

    return True


def main():
    parser = argparse.ArgumentParser(description='测试 BNB/USDT 合约交易')
    parser.add_argument('--trade', action='store_true', help='实际执行交易测试')
    parser.add_argument('--amount', type=float, default=6.0, help='测试金额(USDT)，默认6')
    args = parser.parse_args()

    print("=" * 60)
    print("BNB/USDT 合约交易测试")
    print("=" * 60)

    # 加载配置
    print("\n加载配置...")
    try:
        config = load_config()
        db_config = config.get('database', {}).get('mysql', {})
        binance_config = config.get('exchanges', {}).get('binance', {})

        api_key = binance_config.get('api_key', '').strip()
        api_secret = binance_config.get('api_secret', '').strip()

        if not api_key or not api_secret:
            print("✗ 错误: config.yaml 中未配置币安API")
            return 1

        print(f"  API Key: {api_key[:8]}...{api_key[-4:]}")
        print("  API Secret: ********")

    except Exception as e:
        print(f"✗ 加载配置失败: {e}")
        return 1

    # 初始化引擎
    print("\n初始化交易引擎...")
    try:
        engine = BinanceFuturesEngine(db_config)
        print("✓ 初始化成功")
    except Exception as e:
        print(f"✗ 初始化失败: {e}")
        return 1

    # 测试连接
    success, available = test_connection(engine)
    if not success:
        return 1

    # 获取价格
    price = test_get_price(engine, 'BNB/USDT')
    if not price:
        return 1

    # 查询持仓
    test_get_positions(engine)

    # 查询挂单
    test_get_orders(engine, 'BNB/USDT')

    # 如果指定了 --trade 参数，执行交易测试
    if args.trade:
        if available < args.amount:
            print(f"\n✗ 可用余额不足: {available} < {args.amount} USDT")
            return 1

        print("\n" + "!" * 60)
        print("警告: 即将执行真实交易！")
        print("!" * 60)

        execute_test_trade(engine, 'BNB/USDT', args.amount)
    else:
        print("\n" + "-" * 60)
        print("连接测试完成！")
        print("\n如需执行交易测试，请运行:")
        print(f"  python scripts/test_bnb_futures_trade.py --trade --amount {args.amount}")
        print("-" * 60)

    print("\n测试完成！")
    return 0


if __name__ == '__main__':
    sys.exit(main())
