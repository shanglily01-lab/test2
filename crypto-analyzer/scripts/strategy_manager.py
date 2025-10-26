#!/usr/bin/env python3
"""
投资策略管理工具
用于创建、修改、查看、切换投资策略
"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径（兼容Windows和Linux）
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent
sys.path.insert(0, str(project_root))

import argparse
import logging
from typing import Optional

from app.strategies.strategy_config import (
    get_strategy_manager,
    InvestmentStrategy,
    DimensionWeights,
    RiskProfile,
    TradingRules
)

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


def list_strategies():
    """列出所有策略"""
    print("\n" + "=" * 70)
    print("所有可用策略")
    print("=" * 70)

    manager = get_strategy_manager()
    strategies = manager.list_strategies()
    active = manager.get_active_strategy()

    if not strategies:
        print("  暂无策略")
        return

    for name in strategies:
        strategy = manager.load_strategy(name)
        if strategy:
            is_active = "✅" if active and active.name == name else "  "
            print(f"\n{is_active} {strategy.name}")
            print(f"  描述: {strategy.description or '无描述'}")
            print(f"  风险等级: {strategy.risk_profile.level}")
            print(f"  标签: {', '.join(strategy.tags) if strategy.tags else '无'}")


def show_strategy(name: str):
    """显示策略详情"""
    manager = get_strategy_manager()
    strategy = manager.load_strategy(name)

    if not strategy:
        print(f"❌ 策略不存在: {name}")
        return

    print(strategy.get_summary())


def create_strategy(name: str, template: str = None):
    """创建新策略"""
    manager = get_strategy_manager()

    # 检查策略是否已存在
    if name in manager.list_strategies():
        print(f"❌ 策略已存在: {name}")
        return

    if template:
        # 从模板复制
        success = manager.copy_strategy(template, name, f"基于 {template} 的自定义策略")
        if success:
            print(f"✅ 成功从 {template} 创建策略: {name}")
        else:
            print(f"❌ 创建策略失败")
    else:
        # 创建全新策略
        strategy = InvestmentStrategy(name=name)
        strategy.description = input("策略描述: ").strip()

        # 交互式配置
        print("\n配置维度权重 (总和需为100):")
        strategy.dimension_weights.technical = float(input("  技术指标权重 [40]: ") or "40")
        strategy.dimension_weights.hyperliquid = float(input("  Hyperliquid权重 [20]: ") or "20")
        strategy.dimension_weights.news = float(input("  新闻情绪权重 [15]: ") or "15")
        strategy.dimension_weights.funding_rate = float(input("  资金费率权重 [15]: ") or "15")
        strategy.dimension_weights.ethereum = float(input("  以太坊链上权重 [10]: ") or "10")

        # 标准化权重
        strategy.dimension_weights.normalize()

        print("\n配置风险控制:")
        strategy.risk_profile.level = input("  风险等级 (conservative/balanced/aggressive) [balanced]: ") or "balanced"
        strategy.risk_profile.max_position_size = float(input("  最大仓位比例% [20]: ") or "20")
        strategy.risk_profile.stop_loss = float(input("  止损比例% [5]: ") or "5")
        strategy.risk_profile.take_profit = float(input("  止盈比例% [15]: ") or "15")

        if manager.save_strategy(strategy):
            print(f"\n✅ 策略创建成功: {name}")
        else:
            print(f"\n❌ 策略创建失败")


def edit_strategy(name: str):
    """编辑策略"""
    manager = get_strategy_manager()
    strategy = manager.load_strategy(name)

    if not strategy:
        print(f"❌ 策略不存在: {name}")
        return

    # 不允许编辑默认策略
    if name in ['conservative', 'balanced', 'aggressive']:
        print(f"⚠️  默认策略不可编辑，请先复制: ./scripts/strategy_manager.py copy {name} my_{name}")
        return

    print(f"\n编辑策略: {name}")
    print("=" * 70)
    print("提示: 直接回车保留当前值\n")

    # 编辑描述
    current_desc = strategy.description
    new_desc = input(f"描述 [{current_desc}]: ").strip()
    if new_desc:
        strategy.description = new_desc

    # 编辑维度权重
    print("\n维度权重:")
    weights = strategy.dimension_weights
    new_technical = input(f"  技术指标 [{weights.technical}]: ").strip()
    if new_technical:
        weights.technical = float(new_technical)

    new_hyperliquid = input(f"  Hyperliquid [{weights.hyperliquid}]: ").strip()
    if new_hyperliquid:
        weights.hyperliquid = float(new_hyperliquid)

    new_news = input(f"  新闻情绪 [{weights.news}]: ").strip()
    if new_news:
        weights.news = float(new_news)

    new_funding = input(f"  资金费率 [{weights.funding_rate}]: ").strip()
    if new_funding:
        weights.funding_rate = float(new_funding)

    new_ethereum = input(f"  以太坊链上 [{weights.ethereum}]: ").strip()
    if new_ethereum:
        weights.ethereum = float(new_ethereum)

    # 标准化权重
    weights.normalize()

    # 编辑风险控制
    print("\n风险控制:")
    risk = strategy.risk_profile

    new_position = input(f"  最大仓位% [{risk.max_position_size}]: ").strip()
    if new_position:
        risk.max_position_size = float(new_position)

    new_stop_loss = input(f"  止损% [{risk.stop_loss}]: ").strip()
    if new_stop_loss:
        risk.stop_loss = float(new_stop_loss)

    new_take_profit = input(f"  止盈% [{risk.take_profit}]: ").strip()
    if new_take_profit:
        risk.take_profit = float(new_take_profit)

    # 保存
    if manager.save_strategy(strategy):
        print(f"\n✅ 策略已更新: {name}")
    else:
        print(f"\n❌ 策略更新失败")


def delete_strategy(name: str):
    """删除策略"""
    manager = get_strategy_manager()

    # 确认删除
    confirm = input(f"确认删除策略 '{name}'? (yes/no): ")
    if confirm.lower() != 'yes':
        print("已取消")
        return

    if manager.delete_strategy(name):
        print(f"✅ 策略已删除: {name}")
    else:
        print(f"❌ 删除失败")


def activate_strategy(name: str):
    """激活策略"""
    manager = get_strategy_manager()

    if manager.set_active_strategy(name):
        print(f"✅ 已激活策略: {name}")
        print("\n提示: Dashboard将使用此策略进行分析")
    else:
        print(f"❌ 激活失败")


def copy_strategy(source: str, dest: str):
    """复制策略"""
    manager = get_strategy_manager()

    desc = input("新策略描述: ").strip()

    if manager.copy_strategy(source, dest, desc):
        print(f"✅ 已复制策略: {source} -> {dest}")
    else:
        print(f"❌ 复制失败")


def compare_strategies(names: list):
    """对比策略"""
    manager = get_strategy_manager()

    print("\n" + "=" * 100)
    print("策略对比")
    print("=" * 100)

    # 表头
    print(f"\n{'维度':<20} ", end="")
    for name in names:
        print(f"{name:<15} ", end="")
    print()
    print("-" * 100)

    # 加载所有策略
    strategies = {}
    for name in names:
        strategy = manager.load_strategy(name)
        if strategy:
            strategies[name] = strategy
        else:
            print(f"⚠️  策略不存在: {name}")

    if not strategies:
        return

    # 对比维度权重
    print("\n维度权重:")
    dimensions = [
        ('technical', '技术指标'),
        ('hyperliquid', 'Hyperliquid'),
        ('news', '新闻情绪'),
        ('funding_rate', '资金费率'),
        ('ethereum', '以太坊链上')
    ]

    for dim_key, dim_name in dimensions:
        print(f"  {dim_name:<18} ", end="")
        for name in names:
            if name in strategies:
                value = getattr(strategies[name].dimension_weights, dim_key)
                print(f"{value:>13.1f}% ", end="")
        print()

    # 对比风险控制
    print("\n风险控制:")
    risks = [
        ('max_position_size', '最大仓位%'),
        ('stop_loss', '止损%'),
        ('take_profit', '止盈%'),
        ('max_leverage', '最大杠杆'),
        ('min_signal_strength', '最小信号强度')
    ]

    for risk_key, risk_name in risks:
        print(f"  {risk_name:<18} ", end="")
        for name in names:
            if name in strategies:
                value = getattr(strategies[name].risk_profile, risk_key)
                suffix = "x" if risk_key == 'max_leverage' else ""
                print(f"{value:>13.1f}{suffix} ", end="")
        print()

    print()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="投资策略管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 列出所有策略
  python scripts/strategy_manager.py list

  # 查看策略详情
  python scripts/strategy_manager.py show balanced

  # 创建新策略(从模板)
  python scripts/strategy_manager.py create my_strategy --template balanced

  # 编辑策略
  python scripts/strategy_manager.py edit my_strategy

  # 激活策略
  python scripts/strategy_manager.py activate my_strategy

  # 复制策略
  python scripts/strategy_manager.py copy balanced my_balanced

  # 对比策略
  python scripts/strategy_manager.py compare conservative balanced aggressive

  # 删除策略
  python scripts/strategy_manager.py delete my_strategy
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='命令')

    # list命令
    subparsers.add_parser('list', help='列出所有策略')

    # show命令
    parser_show = subparsers.add_parser('show', help='查看策略详情')
    parser_show.add_argument('name', help='策略名称')

    # create命令
    parser_create = subparsers.add_parser('create', help='创建新策略')
    parser_create.add_argument('name', help='策略名称')
    parser_create.add_argument('--template', help='模板策略名称')

    # edit命令
    parser_edit = subparsers.add_parser('edit', help='编辑策略')
    parser_edit.add_argument('name', help='策略名称')

    # delete命令
    parser_delete = subparsers.add_parser('delete', help='删除策略')
    parser_delete.add_argument('name', help='策略名称')

    # activate命令
    parser_activate = subparsers.add_parser('activate', help='激活策略')
    parser_activate.add_argument('name', help='策略名称')

    # copy命令
    parser_copy = subparsers.add_parser('copy', help='复制策略')
    parser_copy.add_argument('source', help='源策略名称')
    parser_copy.add_argument('dest', help='目标策略名称')

    # compare命令
    parser_compare = subparsers.add_parser('compare', help='对比策略')
    parser_compare.add_argument('names', nargs='+', help='策略名称列表')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 执行命令
    if args.command == 'list':
        list_strategies()
    elif args.command == 'show':
        show_strategy(args.name)
    elif args.command == 'create':
        create_strategy(args.name, args.template)
    elif args.command == 'edit':
        edit_strategy(args.name)
    elif args.command == 'delete':
        delete_strategy(args.name)
    elif args.command == 'activate':
        activate_strategy(args.name)
    elif args.command == 'copy':
        copy_strategy(args.source, args.dest)
    elif args.command == 'compare':
        compare_strategies(args.names)


if __name__ == "__main__":
    main()
