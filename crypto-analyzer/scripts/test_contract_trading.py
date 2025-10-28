"""
模拟合约交易系统测试脚本
Contract Trading Simulator Test Script
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from loguru import logger
from app.trading.contract_trading_simulator import (
    ContractTradingSimulator,
    OrderSide,
    OrderType
)


async def test_basic_trading():
    """测试基本交易功能"""

    logger.info("=" * 80)
    logger.info("🧪 测试 1: 基本交易功能")
    logger.info("=" * 80 + "\n")

    # 初始化模拟器
    simulator = ContractTradingSimulator(
        initial_balance=10000,
        max_leverage=125
    )

    # 显示初始账户信息
    account = simulator.get_account_info()
    logger.info(f"📊 初始账户状态:")
    logger.info(f"   余额: ${account['balance']:,.2f}")
    logger.info(f"   权益: ${account['equity']:,.2f}")
    logger.info(f"   可用保证金: ${account['margin_available']:,.2f}\n")

    # 测试1: 开多单
    logger.info("🎯 测试开多单...")
    order1 = simulator.create_order(
        symbol="BTC/USDT",
        side=OrderSide.LONG,
        quantity=1,
        order_type=OrderType.MARKET,
        leverage=10,
        stop_loss=95000,
        take_profit=105000
    )

    if order1:
        success = await simulator.execute_order(order1.order_id, 100000)
        logger.info(f"   执行结果: {'✅ 成功' if success else '❌ 失败'}\n")

    # 显示持仓
    positions = simulator.get_positions()
    if positions:
        logger.info(f"📈 当前持仓:")
        for pos in positions:
            logger.info(f"   {pos['symbol']} | {pos['side']} | {pos['quantity']} 张")
            logger.info(f"   开仓价: ${pos['entry_price']:,.2f}")
            logger.info(f"   杠杆: {pos['leverage']}x")
            logger.info(f"   强平价: ${pos['liquidation_price']:,.2f}")
            logger.info(f"   保证金: ${pos['margin']:,.2f}\n")

    # 测试2: 更新价格
    logger.info("🔄 模拟价格上涨到 $102,000...")
    simulator._update_account_equity({"BTC/USDT": 102000})

    account = simulator.get_account_info()
    logger.info(f"   权益: ${account['equity']:,.2f}")
    logger.info(f"   保证金率: {account['margin_ratio']:.2f}x\n")

    positions = simulator.get_positions()
    if positions:
        pos = positions[0]
        logger.info(f"   未实现盈亏: ${pos['unrealized_pnl']:+,.2f}")
        logger.info(f"   盈亏比例: {pos['pnl_percentage']:+.2f}%\n")

    # 测试3: 平仓
    logger.info("💰 平仓操作...")
    await simulator._close_position("BTC/USDT", 102000)

    account = simulator.get_account_info()
    logger.info(f"   最终余额: ${account['balance']:,.2f}")
    logger.info(f"   总盈亏: ${account['total_pnl']:+,.2f}\n")


async def test_liquidation():
    """测试爆仓机制"""

    logger.info("=" * 80)
    logger.info("🧪 测试 2: 爆仓机制")
    logger.info("=" * 80 + "\n")

    simulator = ContractTradingSimulator(initial_balance=1000)

    # 开高杠杆多单
    logger.info("🎯 开 50x 杠杆多单...")
    order = simulator.create_order(
        symbol="ETH/USDT",
        side=OrderSide.LONG,
        quantity=10,
        leverage=50
    )

    if order:
        await simulator.execute_order(order.order_id, 3000)

        positions = simulator.get_positions()
        if positions:
            pos = positions[0]
            logger.info(f"   开仓价: ${pos['entry_price']:,.2f}")
            logger.info(f"   强平价: ${pos['liquidation_price']:,.2f}\n")

            # 模拟价格下跌
            logger.info("📉 模拟价格下跌...")
            liquidation_price = pos['liquidation_price']

            # 价格逐步下跌
            for price in [2950, 2900, 2850, liquidation_price - 10]:
                logger.info(f"   当前价: ${price:,.2f}")

                simulator._update_account_equity({"ETH/USDT": price})
                liquidated = simulator.check_liquidation({"ETH/USDT": price})

                if liquidated:
                    logger.warning(f"   💥 触发强制平仓!")
                    break

                await asyncio.sleep(0.5)

            account = simulator.get_account_info()
            logger.info(f"\n   最终余额: ${account['balance']:,.2f}")
            logger.info(f"   总盈亏: ${account['total_pnl']:+,.2f}\n")


async def test_stop_loss_take_profit():
    """测试止盈止损"""

    logger.info("=" * 80)
    logger.info("🧪 测试 3: 止盈止损")
    logger.info("=" * 80 + "\n")

    simulator = ContractTradingSimulator(initial_balance=10000)

    # 开单带止盈止损
    logger.info("🎯 开多单（止损 $48,000 | 止盈 $52,000）...")
    order = simulator.create_order(
        symbol="BTC/USDT",
        side=OrderSide.LONG,
        quantity=0.1,
        leverage=10,
        stop_loss=48000,
        take_profit=52000
    )

    if order:
        await simulator.execute_order(order.order_id, 50000)

        # 测试止盈
        logger.info("\n📈 价格上涨触发止盈...")
        logger.info(f"   价格: $52,500")

        triggered = simulator.check_stop_loss_take_profit({"BTC/USDT": 52500})

        if triggered:
            logger.info(f"   ✅ 触发: {triggered[0][1]}")

        await asyncio.sleep(1)

        account = simulator.get_account_info()
        logger.info(f"   最终余额: ${account['balance']:,.2f}")
        logger.info(f"   总盈亏: ${account['total_pnl']:+,.2f}\n")


async def test_multiple_positions():
    """测试多个持仓"""

    logger.info("=" * 80)
    logger.info("🧪 测试 4: 多个持仓管理")
    logger.info("=" * 80 + "\n")

    simulator = ContractTradingSimulator(initial_balance=50000)

    # 开多个持仓
    trades = [
        ("BTC/USDT", OrderSide.LONG, 0.5, 100000, 20),
        ("ETH/USDT", OrderSide.SHORT, 5, 3000, 15),
        ("SOL/USDT", OrderSide.LONG, 100, 150, 10),
    ]

    logger.info("🎯 开多个持仓...")
    for symbol, side, quantity, price, leverage in trades:
        order = simulator.create_order(
            symbol=symbol,
            side=side,
            quantity=quantity,
            leverage=leverage
        )

        if order:
            await simulator.execute_order(order.order_id, price)
            await asyncio.sleep(0.3)

    logger.info("")

    # 显示所有持仓
    positions = simulator.get_positions()
    logger.info(f"📊 持仓概览 ({len(positions)} 个):")
    for pos in positions:
        logger.info(f"   {pos['symbol']:<12} | {pos['side']:<5} | {pos['quantity']:>6} 张 | {pos['leverage']:>2}x | ${pos['margin']:>8,.2f}")

    # 更新价格
    logger.info("\n🔄 价格变动...")
    new_prices = {
        "BTC/USDT": 102000,
        "ETH/USDT": 2950,
        "SOL/USDT": 155,
    }

    simulator._update_account_equity(new_prices)

    account = simulator.get_account_info()
    logger.info(f"   权益: ${account['equity']:,.2f}")
    logger.info(f"   保证金率: {account['margin_ratio']:.2f}x\n")

    # 显示每个持仓的盈亏
    positions = simulator.get_positions()
    logger.info("💰 持仓盈亏:")
    for pos in positions:
        logger.info(f"   {pos['symbol']:<12} | {pos['unrealized_pnl']:>+10,.2f} | {pos['pnl_percentage']:>+6.2f}%")

    logger.info("")


async def test_statistics():
    """测试统计功能"""

    logger.info("=" * 80)
    logger.info("🧪 测试 5: 交易统计")
    logger.info("=" * 80 + "\n")

    simulator = ContractTradingSimulator(initial_balance=10000)

    # 执行多笔交易
    logger.info("🎯 执行模拟交易...")

    trades = [
        (OrderSide.LONG, 100000, 101000),   # 赢
        (OrderSide.SHORT, 3000, 3050),       # 输
        (OrderSide.LONG, 150, 155),          # 赢
        (OrderSide.SHORT, 2000, 1950),       # 赢
        (OrderSide.LONG, 50000, 49000),      # 输
    ]

    for i, (side, entry_price, exit_price) in enumerate(trades, 1):
        symbol = f"TEST{i}/USDT"

        # 开仓
        order1 = simulator.create_order(
            symbol=symbol,
            side=side,
            quantity=1,
            leverage=10
        )

        if order1:
            await simulator.execute_order(order1.order_id, entry_price)

        await asyncio.sleep(0.1)

        # 平仓
        close_side = OrderSide.SHORT if side == OrderSide.LONG else OrderSide.LONG
        order2 = simulator.create_order(
            symbol=symbol,
            side=close_side,
            quantity=1,
            leverage=10
        )

        if order2:
            await simulator.execute_order(order2.order_id, exit_price)

        await asyncio.sleep(0.1)

    logger.info("")

    # 显示统计
    stats = simulator.get_statistics()

    logger.info("📊 交易统计:")
    logger.info(f"   总交易数: {stats['total_trades']}")
    logger.info(f"   盈利次数: {stats['winning_trades']}")
    logger.info(f"   亏损次数: {stats['losing_trades']}")
    logger.info(f"   胜率: {stats['win_rate']:.2f}%")
    logger.info(f"   总盈利: ${stats['total_profit']:+,.2f}")
    logger.info(f"   总亏损: ${stats['total_loss']:+,.2f}")
    logger.info(f"   净盈亏: ${stats['net_pnl']:+,.2f}")
    logger.info(f"   总手续费: ${stats['total_fee']:,.2f}")
    logger.info(f"   ROI: {stats['roi']:+.2f}%\n")


async def main():
    """主测试函数"""

    logger.info("\n" + "🎮 " * 20)
    logger.info("模拟合约交易系统 - 完整测试")
    logger.info("🎮 " * 20 + "\n")

    try:
        # 运行所有测试
        await test_basic_trading()
        await asyncio.sleep(1)

        await test_liquidation()
        await asyncio.sleep(1)

        await test_stop_loss_take_profit()
        await asyncio.sleep(1)

        await test_multiple_positions()
        await asyncio.sleep(1)

        await test_statistics()

        logger.info("=" * 80)
        logger.info("✅ 所有测试完成")
        logger.info("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
