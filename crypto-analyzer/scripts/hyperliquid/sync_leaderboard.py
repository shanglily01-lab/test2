#!/usr/bin/env python3
"""
同步 Hyperliquid 排行榜数据到数据库
定期运行此脚本以保存聪明钱的历史战绩
"""

import requests
from datetime import datetime, date, timedelta
from pathlib import Path
import sys

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.database.hyperliquid_db import HyperliquidDB


def get_week_range(target_date: date = None) -> tuple:
    """
    获取指定日期所在周的开始和结束日期（周一到周日）

    Args:
        target_date: 目标日期，None表示今天

    Returns:
        (week_start, week_end)
    """
    if target_date is None:
        target_date = date.today()

    # 获取本周一
    weekday = target_date.weekday()  # 0=Monday, 6=Sunday
    week_start = target_date - timedelta(days=weekday)

    # 获取本周日
    week_end = week_start + timedelta(days=6)

    return week_start, week_end


def fetch_leaderboard():
    """
    获取 Hyperliquid 排行榜

    Returns:
        排行榜数据列表
    """
    api_url = 'https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'

    try:
        print(f"正在获取排行榜数据...")
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


def sync_to_database(leaderboard, min_pnl=0):
    """
    同步排行榜数据到数据库

    Args:
        leaderboard: 排行榜数据
        min_pnl: 最小PnL阈值，低于此值的不保存（节省空间）

    Returns:
        保存的记录数
    """
    if not leaderboard:
        print("❌ 没有数据可同步")
        return 0

    print(f"\n开始同步数据到数据库...")
    print(f"最小PnL阈值: ${min_pnl:,}")

    # 获取当前周范围
    week_start, week_end = get_week_range()
    snapshot_date = date.today()

    print(f"周范围: {week_start} ~ {week_end}")
    print(f"快照日期: {snapshot_date}")

    saved_count = 0
    skipped_count = 0

    with HyperliquidDB() as db:
        for rank, entry in enumerate(leaderboard, 1):
            address = entry.get('ethAddress', '')
            display_name = entry.get('displayName')
            account_value = float(entry.get('accountValue', 0))
            window_performances = entry.get('windowPerformances', [])

            # 解析各周期数据
            performance_data = {
                'account_value': account_value
            }

            week_pnl = 0  # 用于过滤

            for window in window_performances:
                if len(window) != 2:
                    continue

                period_name = window[0]
                data = window[1]

                pnl = float(data.get('pnl', 0))
                roi = float(data.get('roi', 0)) * 100  # 转为百分比
                vlm = float(data.get('vlm', 0))

                performance_data[f'{period_name}_pnl'] = pnl
                performance_data[f'{period_name}_roi'] = roi
                performance_data[f'{period_name}_volume'] = vlm

                if period_name == 'week':
                    week_pnl = pnl

            # 过滤：只保存周PnL >= min_pnl 的交易者
            if week_pnl < min_pnl:
                skipped_count += 1
                continue

            try:
                # 保存周度表现
                db.save_weekly_performance(
                    address=address,
                    display_name=display_name,
                    week_start=week_start,
                    week_end=week_end,
                    pnl=performance_data.get('week_pnl', 0),
                    roi=performance_data.get('week_roi', 0),
                    volume=performance_data.get('week_volume', 0),
                    account_value=account_value,
                    pnl_rank=rank
                )

                # 保存表现快照
                db.save_performance_snapshot(
                    address=address,
                    display_name=display_name,
                    snapshot_date=snapshot_date,
                    performance_data=performance_data
                )

                saved_count += 1

                if saved_count % 100 == 0:
                    print(f"  已保存 {saved_count} 条记录...")

            except Exception as e:
                print(f"❌ 保存地址 {address} 失败: {e}")

    print(f"\n同步完成!")
    print(f"  ✅ 成功保存: {saved_count} 条")
    print(f"  ⏭️  跳过: {skipped_count} 条 (PnL < ${min_pnl:,})")

    return saved_count


def main():
    """主函数"""

    print("=" * 80)
    print("Hyperliquid 排行榜数据同步")
    print("=" * 80)
    print()

    # 解析命令行参数
    if len(sys.argv) > 1:
        min_pnl = float(sys.argv[1])
    else:
        min_pnl = 5000  # 默认只保存周PnL >= $5K的交易者

    # 获取排行榜数据
    leaderboard = fetch_leaderboard()

    if not leaderboard:
        print("获取数据失败,退出")
        return

    # 同步到数据库
    saved_count = sync_to_database(leaderboard, min_pnl=min_pnl)

    if saved_count > 0:
        print(f"\n✅ 同步成功! 共保存 {saved_count} 个交易者的数据")
    else:
        print(f"\n⚠️  没有保存任何数据")


if __name__ == "__main__":
    main()
