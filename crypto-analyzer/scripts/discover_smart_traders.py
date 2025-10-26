#!/usr/bin/env python3
"""
Hyperliquid 聪明交易者发现工具
自动从排行榜发现表现优秀的交易者,并可选择性添加到配置文件
"""

import sys
import asyncio
import yaml
from pathlib import Path
from datetime import datetime

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.collectors.hyperliquid_collector import HyperliquidCollector
from loguru import logger


async def discover_and_display_traders(config, period="week", min_pnl=50000, limit=20):
    """
    发现并显示聪明交易者

    Args:
        config: 配置字典
        period: 时间周期
        min_pnl: 最小 PnL 阈值
        limit: 显示数量限制

    Returns:
        聪明交易者列表
    """

    print("=" * 100)
    print(f"Hyperliquid 聪明交易者发现 - {period.upper()} 排行榜")
    print("=" * 100)
    print()

    # 创建采集器
    collector = HyperliquidCollector(config)

    # 发现聪明交易者
    print(f"正在从排行榜获取数据...")
    traders = await collector.discover_smart_traders(period=period, min_pnl=min_pnl)

    if not traders:
        print("❌ 未找到符合条件的交易者")
        return []

    # 按 PnL 排序
    traders.sort(key=lambda x: x['pnl'], reverse=True)

    # 显示结果
    print(f"\n✅ 发现 {len(traders)} 个符合条件的交易者 (PnL >= ${min_pnl:,})")
    print(f"\n显示前 {min(limit, len(traders))} 名:")
    print("-" * 100)
    print(f"{'排名':<6} {'地址':<44} {'PnL (USD)':>18} {'ROI':>10} {'账户价值 (USD)':>20}")
    print("-" * 100)

    for i, trader in enumerate(traders[:limit], 1):
        addr = trader['address']
        pnl = trader['pnl']
        roi = trader['roi']
        account_value = trader['account_value']

        print(f"{i:<6} {addr:<44} ${pnl:>16,.2f} {roi:>9.2f}% ${account_value:>18,.2f}")

    print("-" * 100)

    # 统计信息
    total_pnl = sum(t['pnl'] for t in traders)
    avg_pnl = total_pnl / len(traders)
    avg_roi = sum(t['roi'] for t in traders) / len(traders)

    print(f"\n统计信息:")
    print(f"  总交易者数: {len(traders)}")
    print(f"  总 PnL: ${total_pnl:,.2f}")
    print(f"  平均 PnL: ${avg_pnl:,.2f}")
    print(f"  平均 ROI: {avg_roi:.2f}%")
    print()

    return traders


def add_trader_to_config(config_path, traders, indices):
    """
    将选定的交易者添加到配置文件

    Args:
        config_path: 配置文件路径
        traders: 交易者列表
        indices: 要添加的交易者索引列表
    """

    # 读取当前配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取当前配置的地址
    current_addresses = config.get('hyperliquid', {}).get('addresses', [])
    current_addr_set = {addr.get('address', '').lower() for addr in current_addresses}

    # 添加新地址
    added_count = 0
    skipped_count = 0

    for idx in indices:
        if idx < 0 or idx >= len(traders):
            print(f"⚠️  无效的索引: {idx}")
            continue

        trader = traders[idx]
        address = trader['address']

        # 检查是否已存在
        if address.lower() in current_addr_set:
            print(f"⚠️  地址已存在,跳过: {address}")
            skipped_count += 1
            continue

        # 添加新地址
        new_entry = {
            'address': address,
            'label': f"SmartTrader_{len(current_addresses) + added_count + 1}",
            'type': 'trader'
        }

        current_addresses.append(new_entry)
        current_addr_set.add(address.lower())
        added_count += 1

        print(f"✅ 已添加: {address} (PnL: ${trader['pnl']:,.2f}, ROI: {trader['roi']:.2f}%)")

    # 更新配置
    if added_count > 0:
        if 'hyperliquid' not in config:
            config['hyperliquid'] = {}
        config['hyperliquid']['addresses'] = current_addresses

        # 写回文件
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        print(f"\n✅ 成功添加 {added_count} 个地址到配置文件")
        if skipped_count > 0:
            print(f"⚠️  跳过 {skipped_count} 个已存在的地址")
    else:
        print(f"\n⚠️  没有新地址被添加")


async def interactive_mode(config_path):
    """
    交互模式 - 让用户选择要添加的地址

    Args:
        config_path: 配置文件路径
    """

    # 加载配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    while True:
        print("\n" + "=" * 100)
        print("Hyperliquid 聪明交易者发现工具 - 交互模式")
        print("=" * 100)
        print("\n选择操作:")
        print("  1. 查看周排行榜 (PnL >= $50K)")
        print("  2. 查看周排行榜 (PnL >= $100K)")
        print("  3. 查看周排行榜 (PnL >= $500K)")
        print("  4. 查看月排行榜 (PnL >= $100K)")
        print("  5. 自定义筛选条件")
        print("  6. 查看当前配置的地址")
        print("  0. 退出")
        print()

        choice = input("请选择 (0-6): ").strip()

        if choice == '0':
            print("\n再见!")
            break
        elif choice == '1':
            traders = await discover_and_display_traders(config, period="week", min_pnl=50000, limit=30)
        elif choice == '2':
            traders = await discover_and_display_traders(config, period="week", min_pnl=100000, limit=30)
        elif choice == '3':
            traders = await discover_and_display_traders(config, period="week", min_pnl=500000, limit=20)
        elif choice == '4':
            traders = await discover_and_display_traders(config, period="month", min_pnl=100000, limit=30)
        elif choice == '5':
            try:
                period = input("输入周期 (day/week/month/allTime) [week]: ").strip() or "week"
                min_pnl_str = input("输入最小 PnL (USD) [50000]: ").strip() or "50000"
                min_pnl = float(min_pnl_str)
                limit_str = input("显示数量 [30]: ").strip() or "30"
                limit = int(limit_str)

                traders = await discover_and_display_traders(config, period=period, min_pnl=min_pnl, limit=limit)
            except ValueError as e:
                print(f"❌ 输入错误: {e}")
                continue
        elif choice == '6':
            # 显示当前配置的地址
            current_addresses = config.get('hyperliquid', {}).get('addresses', [])
            print(f"\n当前配置了 {len(current_addresses)} 个地址:")
            print("-" * 100)
            for i, addr_config in enumerate(current_addresses, 1):
                addr = addr_config.get('address', 'N/A')
                label = addr_config.get('label', 'N/A')
                addr_type = addr_config.get('type', 'N/A')
                print(f"{i}. {addr} - {label} ({addr_type})")
            print("-" * 100)
            continue
        else:
            print("❌ 无效选择")
            continue

        if not traders or choice == '6':
            continue

        # 询问是否添加到配置
        print("\n是否要添加交易者到配置文件?")
        add_choice = input("输入排名序号 (用逗号分隔,例如: 1,3,5) 或按回车跳过: ").strip()

        if add_choice:
            try:
                # 解析输入
                indices = [int(x.strip()) - 1 for x in add_choice.split(',')]
                add_trader_to_config(config_path, traders, indices)
            except ValueError:
                print("❌ 输入格式错误")


async def main():
    """主函数"""

    # 配置文件路径
    config_path = project_root / 'config.yaml'

    if not config_path.exists():
        print(f"❌ 配置文件不存在: {config_path}")
        return

    # 加载配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 检查命令行参数
    if len(sys.argv) > 1:
        # 命令行模式
        period = sys.argv[1] if len(sys.argv) > 1 else "week"
        min_pnl = float(sys.argv[2]) if len(sys.argv) > 2 else 50000
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30

        traders = await discover_and_display_traders(config, period=period, min_pnl=min_pnl, limit=limit)
    else:
        # 交互模式
        await interactive_mode(config_path)


if __name__ == "__main__":
    asyncio.run(main())
