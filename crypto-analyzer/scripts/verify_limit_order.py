#!/usr/bin/env python3
"""
验证限价单逻辑
检查策略配置的 longPrice/shortPrice 是否正确传递和计算
"""
import sys
import os

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# 尝试加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(project_root, '.env'))
except ImportError:
    pass  # 没有 dotenv 模块，使用环境变量或默认值

import json
import pymysql
from decimal import Decimal

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD', 'Tonny@1000'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'cursorclass': pymysql.cursors.DictCursor
}


def calculate_limit_price(current_price: float, price_type: str, direction: str) -> float:
    """复制 strategy_executor_v2.py 中的限价计算逻辑"""
    if price_type == 'market':
        return None

    price_adjustments = {
        'market_minus_0_2': -0.2,
        'market_minus_0_4': -0.4,
        'market_minus_0_6': -0.6,
        'market_minus_0_8': -0.8,
        'market_minus_1': -1.0,
        'market_plus_0_2': 0.2,
        'market_plus_0_4': 0.4,
        'market_plus_0_6': 0.6,
        'market_plus_0_8': 0.8,
        'market_plus_1': 1.0,
    }

    adjustment_pct = price_adjustments.get(price_type)
    if adjustment_pct is None:
        print(f"   ❌ 未知的价格类型: {price_type}")
        return None

    limit_price = current_price * (1 + adjustment_pct / 100)
    return limit_price


def verify_limit_order_logic():
    """验证限价单逻辑"""
    print("=" * 70)
    print("验证限价单逻辑")
    print("=" * 70)

    # 1. 获取启用的策略配置
    print("\n【1. 获取策略配置】")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    cursor.execute("SELECT id, name, config FROM trading_strategies WHERE enabled = 1")
    strategies = cursor.fetchall()
    cursor.close()
    conn.close()

    if not strategies:
        print("   ❌ 没有找到启用的策略")
        return

    for strategy_row in strategies:
        print(f"\n   策略 ID: {strategy_row['id']}")
        print(f"   策略名称: {strategy_row['name']}")

        config = json.loads(strategy_row['config']) if strategy_row.get('config') else {}
        long_price_type = config.get('longPrice', 'market')
        short_price_type = config.get('shortPrice', 'market')
        cross_signal_force_market = config.get('crossSignalForceMarket', True)

        print(f"   longPrice: {long_price_type}")
        print(f"   shortPrice: {short_price_type}")
        print(f"   crossSignalForceMarket: {cross_signal_force_market}")

    # 2. 测试限价计算
    print("\n【2. 测试限价计算】")
    current_price = 100.0
    print(f"   假设当前价格: {current_price}")

    # 测试做多 market_minus_0_4
    long_price_type = 'market_minus_0_4'
    limit_price = calculate_limit_price(current_price, long_price_type, 'long')
    if limit_price:
        print(f"\n   做多 ({long_price_type}):")
        print(f"      限价 = {current_price} * (1 + (-0.4) / 100) = {limit_price:.4f}")
        print(f"      当前价 {current_price} > 限价 {limit_price:.4f} ? {current_price > limit_price}")
        if current_price > limit_price:
            print(f"      ✅ 应该创建 PENDING 限价单（等待价格下跌到限价）")
        else:
            print(f"      ❌ 会立即成交（市价单）")

    # 测试做空 market_plus_0_4
    short_price_type = 'market_plus_0_4'
    limit_price = calculate_limit_price(current_price, short_price_type, 'short')
    if limit_price:
        print(f"\n   做空 ({short_price_type}):")
        print(f"      限价 = {current_price} * (1 + 0.4 / 100) = {limit_price:.4f}")
        print(f"      当前价 {current_price} < 限价 {limit_price:.4f} ? {current_price < limit_price}")
        if current_price < limit_price:
            print(f"      ✅ 应该创建 PENDING 限价单（等待价格上涨到限价）")
        else:
            print(f"      ❌ 会立即成交（市价单）")

    # 3. 测试信号类型判断
    print("\n【3. 测试信号类型判断】")
    print("   crossSignalForceMarket=True 时，金叉/死叉信号会强制使用市价单")

    test_signals = [
        ('sustained_trend', '持续趋势'),
        ('sustained_trend_entry', '持续趋势入场'),
        ('golden_cross', '金叉'),
        ('death_cross', '死叉'),
        ('ema_crossover', 'EMA交叉'),
        ('oscillation_reversal', '震荡反转'),
        ('limit_order_timeout', '限价单超时')
    ]

    cross_signal_force_market = True
    print(f"\n   crossSignalForceMarket = {cross_signal_force_market}")
    for signal_type, signal_name in test_signals:
        is_cross_signal = signal_type in ('golden_cross', 'death_cross', 'ema_crossover')
        if is_cross_signal and cross_signal_force_market:
            result = "❌ 强制市价"
        else:
            result = "✅ 使用限价配置"
        print(f"      {signal_name} ({signal_type}): {result}")

    # 4. 检查最近订单的 order_type
    print("\n【4. 检查最近订单】")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    # 检查 LIMIT 类型订单
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM futures_orders WHERE order_type = 'LIMIT'
    """)
    limit_count = cursor.fetchone()['cnt']
    print(f"   LIMIT 类型订单总数: {limit_count}")

    # 检查 PENDING 状态订单
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM futures_orders WHERE status = 'PENDING'
    """)
    pending_count = cursor.fetchone()['cnt']
    print(f"   PENDING 状态订单总数: {pending_count}")

    # 最近10个开仓订单
    cursor.execute("""
        SELECT order_id, symbol, side, order_type, status, price, created_at
        FROM futures_orders
        WHERE side LIKE 'OPEN_%'
        ORDER BY created_at DESC
        LIMIT 10
    """)
    orders = cursor.fetchall()

    print(f"\n   最近10个开仓订单:")
    print(f"   {'时间':<20} | {'交易对':<12} | {'方向':<12} | {'类型':<8} | {'状态':<10}")
    print(f"   {'-'*20}-+-{'-'*12}-+-{'-'*12}-+-{'-'*8}-+-{'-'*10}")
    for order in orders:
        created_at = str(order['created_at'])[:19]
        print(f"   {created_at:<20} | {order['symbol']:<12} | {order['side']:<12} | {order['order_type']:<8} | {order['status']:<10}")

    cursor.close()
    conn.close()

    # 5. 检查待开仓记录
    print("\n【5. 检查待开仓记录】")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, symbol, direction, signal_type, status, created_at
        FROM pending_positions
        ORDER BY created_at DESC
        LIMIT 5
    """)
    pending = cursor.fetchall()

    if pending:
        print(f"   最近5个待开仓记录:")
        print(f"   {'ID':<8} | {'交易对':<12} | {'方向':<6} | {'信号类型':<25} | {'状态':<10}")
        print(f"   {'-'*8}-+-{'-'*12}-+-{'-'*6}-+-{'-'*25}-+-{'-'*10}")
        for p in pending:
            print(f"   {p['id']:<8} | {p['symbol']:<12} | {p['direction']:<6} | {p['signal_type']:<25} | {p['status']:<10}")
    else:
        print("   没有待开仓记录")

    cursor.close()
    conn.close()

    # 6. 检查 validated 状态的待开仓记录对应的订单
    print("\n【6. 检查 validated 记录对应的订单】")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    # 先获取 validated 的待开仓记录
    cursor.execute("""
        SELECT id, symbol, direction, signal_type, created_at
        FROM pending_positions
        WHERE status = 'validated'
        ORDER BY created_at DESC
        LIMIT 5
    """)
    validated = cursor.fetchall()

    if validated:
        print(f"   最近 validated 的待开仓记录及对应订单:")
        for v in validated:
            pending_id = v['id']
            symbol = v['symbol']
            direction = v['direction']
            signal_type = v['signal_type']
            created_at = v['created_at']

            print(f"   待开仓 #{pending_id}: {symbol} {direction} ({signal_type})")
            print(f"      待开仓创建时间: {created_at}")

            # 单独查询对应订单
            side = f"OPEN_{direction.upper()}"
            cursor.execute("""
                SELECT order_id, order_type, status, created_at
                FROM futures_orders
                WHERE symbol = %s AND side = %s
                AND created_at >= %s
                AND created_at <= DATE_ADD(%s, INTERVAL 5 MINUTE)
                ORDER BY created_at DESC
                LIMIT 1
            """, (symbol, side, created_at, created_at))
            order = cursor.fetchone()

            if order:
                print(f"      订单: {order['order_id']} | 类型: {order['order_type']} | 状态: {order['status']}")
                print(f"      订单创建时间: {order['created_at']}")
            else:
                print(f"      ⚠️ 未找到对应订单")
    else:
        print("   没有 validated 状态的待开仓记录")

    cursor.close()
    conn.close()

    # 7. 诊断问题
    print("\n【7. 问题诊断】")
    print("   根据以上信息分析：")

    if limit_count == 0:
        print("   ⚠️  没有任何 LIMIT 类型的订单")
        print("   可能原因:")
        print("      1. 信号类型是金叉/死叉，且 crossSignalForceMarket=True")
        print("      2. limit_price 没有被正确传递到 open_position")
        print("      3. 限价计算返回了 None")
        print("\n   建议检查服务日志中 [限价单调试] 开头的日志")
    else:
        print(f"   ✅ 有 {limit_count} 个 LIMIT 类型订单")

    print("\n" + "=" * 70)
    print("验证完成")
    print("=" * 70)


async def test_limit_order_open():
    """
    直接测试限价单开仓功能
    调用 futures_trading_engine.open_position 传入限价参数，验证是否创建 LIMIT 订单
    """
    print("\n" + "=" * 70)
    print("【测试限价单开仓】")
    print("=" * 70)

    # 导入必要的模块
    from decimal import Decimal
    from app.trading.futures_trading_engine import FuturesTradingEngine

    # 初始化引擎
    engine = FuturesTradingEngine()

    # 测试参数
    test_symbol = 'BTCUSDT'
    test_account_id = 1  # 测试账户ID

    # 获取当前价格
    print(f"\n1. 获取 {test_symbol} 当前价格...")
    current_price = engine.get_current_price(test_symbol)
    if not current_price:
        print(f"   ❌ 无法获取 {test_symbol} 价格")
        return

    print(f"   当前价格: {current_price}")

    # 计算限价（做多用 market_minus_0_4，即当前价 -0.4%）
    limit_price_long = Decimal(str(current_price)) * Decimal('0.996')
    limit_price_long = limit_price_long.quantize(Decimal('0.01'))

    # 计算限价（做空用 market_plus_0_4，即当前价 +0.4%）
    limit_price_short = Decimal(str(current_price)) * Decimal('1.004')
    limit_price_short = limit_price_short.quantize(Decimal('0.01'))

    print(f"\n2. 限价计算:")
    print(f"   做多限价 (market_minus_0_4): {limit_price_long} ({current_price} * 0.996)")
    print(f"   做空限价 (market_plus_0_4): {limit_price_short} ({current_price} * 1.004)")

    # 测试做多限价单
    print(f"\n3. 测试做多限价单...")
    print(f"   交易对: {test_symbol}")
    print(f"   方向: LONG")
    print(f"   限价: {limit_price_long}")
    print(f"   当前价: {current_price}")
    print(f"   预期: 当前价 > 限价 → 创建 PENDING 限价单")

    # 计算最小数量（约 10 USDT）
    quantity = Decimal('10') / limit_price_long
    quantity = quantity.quantize(Decimal('0.001'))

    print(f"   数量: {quantity}")

    result = engine.open_position(
        account_id=test_account_id,
        symbol=test_symbol,
        position_side='LONG',
        quantity=quantity,
        leverage=5,
        limit_price=limit_price_long,
        stop_loss_pct=Decimal('2'),
        take_profit_pct=Decimal('3'),
        source='test_limit_order',
        signal_id=None,
        strategy_id=None
    )

    print(f"\n   开仓结果:")
    print(f"   success: {result.get('success')}")
    print(f"   order_type: {result.get('order_type', 'N/A')}")
    print(f"   status: {result.get('status', 'N/A')}")
    print(f"   message: {result.get('message', 'N/A')}")

    if result.get('success') and result.get('order_type') == 'LIMIT':
        print(f"\n   ✅ 限价单测试成功！订单ID: {result.get('order_id')}")
    elif result.get('success') and result.get('order_type') != 'LIMIT':
        print(f"\n   ⚠️  开仓成功但不是限价单，order_type: {result.get('order_type')}")
    else:
        print(f"\n   ❌ 限价单测试失败")

    # 4. 验证数据库中的订单
    print(f"\n4. 验证数据库中的订单...")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT order_id, symbol, side, order_type, status, price, created_at
        FROM futures_orders
        WHERE order_source = 'test_limit_order'
        ORDER BY created_at DESC
        LIMIT 5
    """)
    test_orders = cursor.fetchall()

    if test_orders:
        print(f"   测试创建的订单:")
        print(f"   {'订单ID':<25} | {'类型':<8} | {'状态':<10} | {'价格':<12}")
        print(f"   {'-'*25}-+-{'-'*8}-+-{'-'*10}-+-{'-'*12}")
        for order in test_orders:
            print(f"   {order['order_id']:<25} | {order['order_type']:<8} | {order['status']:<10} | {order['price']:<12}")
    else:
        print(f"   没有找到测试订单")

    # 统计 LIMIT 类型订单
    cursor.execute("""
        SELECT COUNT(*) as cnt FROM futures_orders WHERE order_type = 'LIMIT'
    """)
    limit_count = cursor.fetchone()['cnt']
    print(f"\n   数据库中 LIMIT 类型订单总数: {limit_count}")

    cursor.close()
    conn.close()

    print("\n" + "=" * 70)
    print("限价单测试完成")
    print("=" * 70)


def cleanup_test_orders():
    """清理测试订单"""
    print("\n【清理测试订单】")
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    # 查找测试订单
    cursor.execute("""
        SELECT order_id, symbol, side, order_type, status, margin
        FROM futures_orders
        WHERE order_source = 'test_limit_order'
    """)
    test_orders = cursor.fetchall()

    if not test_orders:
        print("   没有找到测试订单")
        cursor.close()
        conn.close()
        return

    print(f"   找到 {len(test_orders)} 个测试订单")

    for order in test_orders:
        order_id = order['order_id']
        status = order['status']
        margin = order['margin'] or 0

        # 如果是 PENDING 状态，需要解冻保证金
        if status == 'PENDING':
            print(f"   删除 PENDING 订单 {order_id}，解冻保证金 {margin}")
            # 解冻保证金（简化处理，实际需要查询 fee）
            cursor.execute("""
                UPDATE paper_trading_accounts
                SET current_balance = current_balance + %s,
                    frozen_balance = frozen_balance - %s
                WHERE id = 1
            """, (float(margin) * 1.0004, float(margin) * 1.0004))

        # 删除订单
        cursor.execute("DELETE FROM futures_orders WHERE order_id = %s", (order_id,))
        print(f"   已删除订单 {order_id}")

    conn.commit()
    cursor.close()
    conn.close()
    print("   清理完成")


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='验证限价单逻辑')
    parser.add_argument('--test', action='store_true', help='执行限价单开仓测试')
    parser.add_argument('--cleanup', action='store_true', help='清理测试订单')
    args = parser.parse_args()

    if args.cleanup:
        cleanup_test_orders()
    elif args.test:
        import asyncio
        asyncio.run(test_limit_order_open())
    else:
        verify_limit_order_logic()
