#!/usr/bin/env python3
"""
查看 Hyperliquid 排行榜（从数据库）
"""

import sys
from pathlib import Path
from datetime import date, timedelta
from tabulate import tabulate

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.database.hyperliquid_db import HyperliquidDB


def view_weekly_leaderboard(limit=20):
    """
    查看周度排行榜

    Args:
        limit: 显示数量
    """
    with HyperliquidDB() as db:
        leaderboard = db.get_weekly_leaderboard(limit=limit)

        if not leaderboard:
            print("❌ 暂无排行榜数据")
            print("   请先运行: python3 sync_hyperliquid_leaderboard.py")
            return

        # 显示排行榜
        week_start = leaderboard[0]['week_start']
        week_end = leaderboard[0]['week_end']

        print(f"\n{'='*120}")
        print(f"Hyperliquid 周度排行榜 ({week_start} ~ {week_end})")
        print(f"{'='*120}\n")

        # 准备表格数据
        table_data = []
        for i, trader in enumerate(leaderboard, 1):
            addr = trader['address']
            name = trader['display_name'] if trader['display_name'] else 'N/A'
            pnl = trader['pnl']
            roi = trader['roi']
            volume = trader['volume']
            account_value = trader['account_value']

            table_data.append([
                i,
                name[:18] if name != 'N/A' else name,
                f"{addr[:8]}...{addr[-6:]}",
                f"${pnl:,.2f}",
                f"{roi:.2f}%",
                f"${volume:,.0f}",
                f"${account_value:,.0f}"
            ])

        headers = ['排名', '昵称', '地址', 'PnL (周)', 'ROI', '交易量', '账户价值']
        print(tabulate(table_data, headers=headers, tablefmt='simple'))
        print(f"\n{'='*120}")

        # 统计信息
        total_traders = len(leaderboard)
        total_pnl = sum(t['pnl'] for t in leaderboard)
        avg_pnl = total_pnl / total_traders if total_traders > 0 else 0
        avg_roi = sum(t['roi'] for t in leaderboard) / total_traders if total_traders > 0 else 0

        print(f"\n统计信息:")
        print(f"  显示交易者数: {total_traders}")
        print(f"  总 PnL: ${total_pnl:,.2f}")
        print(f"  平均 PnL: ${avg_pnl:,.2f}")
        print(f"  平均 ROI: {avg_roi:.2f}%")
        print()


def view_trader_history(address, days=30):
    """
    查看交易者历史表现

    Args:
        address: 钱包地址
        days: 查询天数
    """
    with HyperliquidDB() as db:
        history = db.get_trader_history(address, days=days)

        if not history:
            print(f"❌ 未找到地址 {address} 的历史数据")
            return

        print(f"\n{'='*140}")
        print(f"交易者历史表现: {address}")
        print(f"{'='*140}\n")

        # 准备表格数据
        table_data = []
        for record in history:
            snapshot_date = record['snapshot_date']
            week_pnl = record['week_pnl']
            week_roi = record['week_roi']
            month_pnl = record['month_pnl']
            month_roi = record['month_roi']
            account_value = record['account_value']

            table_data.append([
                snapshot_date,
                f"${week_pnl:,.2f}",
                f"{week_roi:.2f}%",
                f"${month_pnl:,.2f}",
                f"{month_roi:.2f}%",
                f"${account_value:,.0f}"
            ])

        headers = ['日期', '周PnL', '周ROI', '月PnL', '月ROI', '账户价值']
        print(tabulate(table_data, headers=headers, tablefmt='simple'))
        print(f"\n{'='*140}\n")


def compare_traders(addresses: list):
    """
    对比多个交易者的表现

    Args:
        addresses: 地址列表
    """
    with HyperliquidDB() as db:
        print(f"\n{'='*120}")
        print(f"交易者对比 (最近一周)")
        print(f"{'='*120}\n")

        # 获取最近一周的数据
        leaderboard = db.get_weekly_leaderboard(limit=10000)

        # 创建地址到数据的映射
        addr_to_data = {t['address']: t for t in leaderboard}

        # 准备表格数据
        table_data = []
        for addr in addresses:
            if addr in addr_to_data:
                trader = addr_to_data[addr]
                name = trader['display_name'] if trader['display_name'] else 'N/A'
                pnl = trader['pnl']
                roi = trader['roi']
                volume = trader['volume']
                account_value = trader['account_value']
                rank = trader['pnl_rank']

                table_data.append([
                    f"{addr[:8]}...{addr[-6:]}",
                    name[:18] if name != 'N/A' else name,
                    rank,
                    f"${pnl:,.2f}",
                    f"{roi:.2f}%",
                    f"${volume:,.0f}",
                    f"${account_value:,.0f}"
                ])
            else:
                table_data.append([
                    f"{addr[:8]}...{addr[-6:]}",
                    'N/A',
                    'N/A',
                    'N/A',
                    'N/A',
                    'N/A',
                    'N/A'
                ])

        headers = ['地址', '昵称', '排名', 'PnL (周)', 'ROI', '交易量', '账户价值']
        print(tabulate(table_data, headers=headers, tablefmt='simple'))
        print(f"\n{'='*120}\n")


def interactive_mode():
    """交互模式"""

    while True:
        print("\n" + "="*80)
        print("Hyperliquid 排行榜查看工具")
        print("="*80)
        print("\n选择操作:")
        print("  1. 查看周度排行榜 (Top 20)")
        print("  2. 查看周度排行榜 (Top 50)")
        print("  3. 查看周度排行榜 (Top 100)")
        print("  4. 查看交易者历史")
        print("  5. 对比多个交易者")
        print("  0. 退出")
        print()

        choice = input("请选择 (0-5): ").strip()

        if choice == '0':
            print("\n再见!")
            break
        elif choice == '1':
            view_weekly_leaderboard(limit=20)
        elif choice == '2':
            view_weekly_leaderboard(limit=50)
        elif choice == '3':
            view_weekly_leaderboard(limit=100)
        elif choice == '4':
            address = input("\n输入钱包地址: ").strip()
            days_str = input("查询天数 [30]: ").strip() or "30"
            try:
                days = int(days_str)
                view_trader_history(address, days=days)
            except ValueError:
                print("❌ 无效的天数")
        elif choice == '5':
            addresses_str = input("\n输入钱包地址 (用逗号分隔): ").strip()
            addresses = [addr.strip() for addr in addresses_str.split(',') if addr.strip()]
            if addresses:
                compare_traders(addresses)
            else:
                print("❌ 未输入地址")
        else:
            print("❌ 无效选择")


def main():
    """主函数"""

    print("="*80)
    print("Hyperliquid 排行榜查看工具")
    print("="*80)
    print()

    # 检查命令行参数
    if len(sys.argv) > 1:
        cmd = sys.argv[1]

        if cmd == 'top':
            limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
            view_weekly_leaderboard(limit=limit)
        elif cmd == 'history':
            if len(sys.argv) < 3:
                print("用法: python3 view_hyperliquid_leaderboard.py history <address> [days]")
                return
            address = sys.argv[2]
            days = int(sys.argv[3]) if len(sys.argv) > 3 else 30
            view_trader_history(address, days=days)
        elif cmd == 'compare':
            if len(sys.argv) < 3:
                print("用法: python3 view_hyperliquid_leaderboard.py compare <addr1> <addr2> ...")
                return
            addresses = sys.argv[2:]
            compare_traders(addresses)
        else:
            print(f"未知命令: {cmd}")
            print("\n可用命令:")
            print("  top [limit]                     - 查看周度排行榜")
            print("  history <address> [days]        - 查看交易者历史")
            print("  compare <addr1> <addr2> ...     - 对比多个交易者")
    else:
        # 交互模式
        interactive_mode()


if __name__ == "__main__":
    # 检查是否安装了 tabulate
    try:
        from tabulate import tabulate
    except ImportError:
        print("❌ 缺少依赖: tabulate")
        print("   安装命令: pip install tabulate")
        sys.exit(1)

    main()
