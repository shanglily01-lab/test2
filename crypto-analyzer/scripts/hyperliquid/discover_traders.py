#!/usr/bin/env python3
"""
Hyperliquid 聪明交易者发现工具 (独立版本)
自动从排行榜发现表现优秀的交易者,并可选择性添加到配置文件
"""

import requests
import yaml
import sys
from datetime import datetime


def fetch_leaderboard():
    """
    获取 Hyperliquid 排行榜

    Returns:
        排行榜数据列表
    """
    api_url = 'https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'

    try:
        response = requests.get(api_url, timeout=30)

        if response.status_code == 200:
            data = response.json()
            leaderboard = data.get('leaderboardRows', [])
            print(f"✅ 成功获取排行榜: {len(leaderboard)} 个交易者")
            return leaderboard
        else:
            print(f"❌ 获取排行榜失败: HTTP {response.status_code}")
            return []

    except Exception as e:
        print(f"❌ 异常: {e}")
        return []


def discover_smart_traders(leaderboard, period="week", min_pnl=50000):
    """
    从排行榜筛选聪明交易者

    Args:
        leaderboard: 排行榜数据
        period: 时间周期
        min_pnl: 最小 PnL 阈值

    Returns:
        聪明交易者列表
    """
    smart_traders = []

    for entry in leaderboard:
        account_value = float(entry.get('accountValue', 0))
        user = entry.get('ethAddress', '')
        display_name = entry.get('displayName')
        window_performances = entry.get('windowPerformances', [])

        # 找到指定周期的数据
        period_data = None
        for window in window_performances:
            if len(window) == 2 and window[0] == period:
                period_data = window[1]
                break

        if not period_data:
            continue

        pnl = float(period_data.get('pnl', 0))
        roi_decimal = float(period_data.get('roi', 0))
        vlm = float(period_data.get('vlm', 0))

        # 筛选条件
        if pnl >= min_pnl and account_value > 0:
            trader = {
                'address': user,
                'display_name': display_name or '',
                'pnl': pnl,
                'account_value': account_value,
                'roi': roi_decimal * 100,  # 转换为百分比
                'volume': vlm,
                'period': period
            }

            smart_traders.append(trader)

    # 按 PnL 排序
    smart_traders.sort(key=lambda x: x['pnl'], reverse=True)

    return smart_traders


def display_traders(traders, limit=30):
    """
    显示交易者列表

    Args:
        traders: 交易者列表
        limit: 显示数量限制
    """
    if not traders:
        print("\n❌ 未找到符合条件的交易者")
        return

    print(f"\n✅ 发现 {len(traders)} 个符合条件的交易者")
    print(f"\n前 {min(limit, len(traders))} 名:")
    print("-" * 120)
    print(f"{'排名':<6} {'昵称':<20} {'地址':<44} {'PnL (USD)':>18} {'ROI':>10} {'交易量 (USD)':>20}")
    print("-" * 120)

    for i, trader in enumerate(traders[:limit], 1):
        addr = trader['address']
        name = trader['display_name'][:18] if trader['display_name'] else 'N/A'
        pnl = trader['pnl']
        roi = trader['roi']
        volume = trader['volume']

        print(f"{i:<6} {name:<20} {addr:<44} ${pnl:>16,.2f} {roi:>9.2f}% ${volume:>18,.2f}")

    print("-" * 120)

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


def add_traders_to_config(config_path, traders, indices):
    """
    将选定的交易者添加到配置文件

    Args:
        config_path: 配置文件路径
        traders: 交易者列表
        indices: 要添加的交易者索引列表
    """
    try:
        # 读取当前配置
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 获取当前配置的地址
        if 'hyperliquid' not in config:
            config['hyperliquid'] = {}
        if 'addresses' not in config['hyperliquid']:
            config['hyperliquid']['addresses'] = []

        current_addresses = config['hyperliquid']['addresses']
        current_addr_set = {addr.get('address', '').lower() for addr in current_addresses}

        # 添加新地址
        added_count = 0
        skipped_count = 0

        for idx in indices:
            if idx < 0 or idx >= len(traders):
                print(f"⚠️  无效的索引: {idx + 1}")
                continue

            trader = traders[idx]
            address = trader['address']

            # 检查是否已存在
            if address.lower() in current_addr_set:
                print(f"⚠️  地址已存在,跳过: {address}")
                skipped_count += 1
                continue

            # 生成标签
            if trader['display_name']:
                label = trader['display_name']
            else:
                label = f"SmartTrader_{len(current_addresses) + added_count + 1}"

            # 添加新地址
            new_entry = {
                'address': address,
                'label': label,
                'type': 'trader'
            }

            current_addresses.append(new_entry)
            current_addr_set.add(address.lower())
            added_count += 1

            print(f"✅ 已添加 #{idx+1}: {address}")
            print(f"   标签: {label}")
            print(f"   PnL: ${trader['pnl']:,.2f}, ROI: {trader['roi']:.2f}%")

        # 更新配置
        if added_count > 0:
            config['hyperliquid']['addresses'] = current_addresses

            # 写回文件
            with open(config_path, 'w', encoding='utf-8') as f:
                yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

            print(f"\n✅ 成功添加 {added_count} 个地址到配置文件: {config_path}")
            if skipped_count > 0:
                print(f"⚠️  跳过 {skipped_count} 个已存在的地址")
        else:
            print(f"\n⚠️  没有新地址被添加")

    except Exception as e:
        print(f"❌ 添加到配置文件失败: {e}")


def interactive_mode(config_path='config.yaml'):
    """
    交互模式

    Args:
        config_path: 配置文件路径
    """
    while True:
        print("\n" + "=" * 120)
        print("Hyperliquid 聪明交易者发现工具 - 交互模式")
        print("=" * 120)
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
        elif choice == '6':
            # 显示当前配置的地址
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                current_addresses = config.get('hyperliquid', {}).get('addresses', [])
                print(f"\n当前配置了 {len(current_addresses)} 个地址:")
                print("-" * 120)
                for i, addr_config in enumerate(current_addresses, 1):
                    addr = addr_config.get('address', 'N/A')
                    label = addr_config.get('label', 'N/A')
                    addr_type = addr_config.get('type', 'N/A')
                    print(f"{i}. {addr} - {label} ({addr_type})")
                print("-" * 120)
            except Exception as e:
                print(f"❌ 读取配置失败: {e}")
            continue

        # 确定筛选参数
        if choice == '1':
            period, min_pnl, limit = "week", 50000, 30
        elif choice == '2':
            period, min_pnl, limit = "week", 100000, 30
        elif choice == '3':
            period, min_pnl, limit = "week", 500000, 20
        elif choice == '4':
            period, min_pnl, limit = "month", 100000, 30
        elif choice == '5':
            try:
                period = input("输入周期 (day/week/month/allTime) [week]: ").strip() or "week"
                min_pnl_str = input("输入最小 PnL (USD) [50000]: ").strip() or "50000"
                min_pnl = float(min_pnl_str)
                limit_str = input("显示数量 [30]: ").strip() or "30"
                limit = int(limit_str)
            except ValueError as e:
                print(f"❌ 输入错误: {e}")
                continue
        else:
            print("❌ 无效选择")
            continue

        # 获取并显示数据
        print(f"\n正在获取 {period.upper()} 排行榜数据...")
        leaderboard = fetch_leaderboard()

        if not leaderboard:
            continue

        print(f"正在筛选交易者 (PnL >= ${min_pnl:,})...")
        traders = discover_smart_traders(leaderboard, period=period, min_pnl=min_pnl)
        display_traders(traders, limit=limit)

        if not traders:
            continue

        # 询问是否添加到配置
        print("\n是否要添加交易者到配置文件?")
        add_choice = input("输入排名序号 (用逗号分隔,例如: 1,3,5) 或按回车跳过: ").strip()

        if add_choice:
            try:
                # 解析输入
                indices = [int(x.strip()) - 1 for x in add_choice.split(',')]
                add_traders_to_config(config_path, traders, indices)
            except ValueError:
                print("❌ 输入格式错误")


def main():
    """主函数"""

    print("=" * 120)
    print("Hyperliquid 聪明交易者发现工具")
    print("=" * 120)
    print()

    config_path = 'config.yaml'

    # 检查命令行参数
    if len(sys.argv) > 1:
        # 命令行模式
        period = sys.argv[1] if len(sys.argv) > 1 else "week"
        min_pnl = float(sys.argv[2]) if len(sys.argv) > 2 else 50000
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 30

        print(f"命令行模式: period={period}, min_pnl=${min_pnl:,}, limit={limit}\n")

        leaderboard = fetch_leaderboard()
        if leaderboard:
            traders = discover_smart_traders(leaderboard, period=period, min_pnl=min_pnl)
            display_traders(traders, limit=limit)
    else:
        # 交互模式
        interactive_mode(config_path)


if __name__ == "__main__":
    main()
