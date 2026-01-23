#!/usr/bin/env python3
"""
Hyperliquid 聪明钱包监控工具

功能:
1. 扫描排行榜发现聪明钱包
2. 添加钱包到监控列表
3. 查看监控钱包的实时交易和持仓
4. 生成交易信号
"""

import sys
import argparse
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from tabulate import tabulate
import yaml

# 添加项目根目录
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from app.collectors.hyperliquid_collector import HyperliquidCollector
from app.database.hyperliquid_db import HyperliquidDB


def load_config():
    """加载配置"""
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"❌ 加载配置文件失败: {e}")
        print("   请确认 config.yaml 文件存在")
        sys.exit(1)


# ==================== 命令: scan ====================

async def cmd_scan(args):
    """扫描排行榜发现聪明钱包"""
    print("\n" + "="*100)
    print(f"🔍 扫描 Hyperliquid 排行榜 - 发现聪明钱包")
    print("="*100 + "\n")

    config = load_config()
    hyperliquid_config = config.get('hyperliquid', {})
    collector = HyperliquidCollector(hyperliquid_config)

    # 获取聪明交易者
    print(f"正在获取 {args.period} 排行榜 (最低 PnL: ${args.min_pnl:,})...\n")

    smart_traders = await collector.discover_smart_traders(
        period=args.period,
        min_pnl=args.min_pnl
    )

    if not smart_traders:
        print("❌ 未发现符合条件的聪明钱包")
        print(f"   尝试降低 min_pnl 参数 (当前: ${args.min_pnl:,})")
        return

    print(f"✅ 发现 {len(smart_traders)} 个聪明钱包 (PnL >= ${args.min_pnl:,})\n")

    # 显示排行榜
    table_data = []
    for i, trader in enumerate(smart_traders[:args.limit], 1):
        addr = trader['address']
        display_name = trader.get('displayName', addr[:10])
        table_data.append([
            i,
            display_name[:20],
            f"{addr[:8]}...{addr[-6:]}",
            f"${trader['pnl']:,.2f}",
            f"{trader['roi']:.2f}%",
            f"${trader.get('volume', 0):,.0f}",
            f"${trader.get('accountValue', 0):,.0f}"
        ])

    headers = ['排名', '名称', '地址', f'{args.period.upper()} PnL', 'ROI', '交易量', '账户价值']
    print(tabulate(table_data, headers=headers, tablefmt='grid'))
    print("-"*100 + "\n")

    # 询问是否添加到监控
    if args.add and args.add > 0:
        print(f"正在添加前 {args.add} 个钱包到监控列表...\n")

        with HyperliquidDB() as db:
            added = 0
            skipped = 0
            for trader in smart_traders[:args.add]:
                try:
                    display_name = trader.get('displayName', trader['address'][:10])
                    result = db.add_monitored_wallet(
                        address=trader['address'],
                        label=f"Auto_{display_name}",
                        monitor_type='auto',
                        pnl=trader['pnl'],
                        roi=trader['roi'],
                        account_value=trader.get('accountValue', 0)
                    )
                    if result:
                        added += 1
                        print(f"  ✅ 已添加: {display_name} (PnL: ${trader['pnl']:,.2f})")
                    else:
                        skipped += 1
                        print(f"  ⊗ 已存在: {display_name}")
                except Exception as e:
                    print(f"  ❌ 添加失败: {trader['address'][:10]}... - {e}")

            print(f"\n汇总: 新增 {added} 个, 跳过 {skipped} 个\n")


# ==================== 命令: list ====================

async def cmd_list(args):
    """查看监控钱包列表"""
    print("\n" + "="*100)
    print(f"📋 监控钱包列表")
    print("="*100 + "\n")

    with HyperliquidDB() as db:
        wallets = db.get_monitored_wallets(active_only=not args.all)

        if not wallets:
            print("❌ 暂无监控钱包")
            print("   运行以下命令添加钱包:")
            print("   python hyperliquid_monitor.py scan --add 10\n")
            return

        print(f"总计: {len(wallets)} 个钱包\n")

        # 显示列表
        table_data = []
        for i, wallet in enumerate(wallets[:args.limit], 1):
            addr = wallet['address']
            label = wallet.get('label') or wallet.get('display_name', 'Unknown')
            status = "✅ 活跃" if wallet.get('is_active') else "⊗ 停用"

            table_data.append([
                i,
                label[:25],
                f"{addr[:8]}...{addr[-6:]}",
                wallet.get('monitor_type', 'manual'),
                status,
                f"${wallet.get('pnl', 0):,.2f}",
                f"{wallet.get('roi', 0):.2f}%",
                wallet.get('last_check_at', 'Never')
            ])

        headers = ['#', '标签', '地址', '类型', '状态', 'PnL', 'ROI', '最后检查']
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
        print(f"\n显示 {len(table_data)} / {len(wallets)} 个钱包\n")


# ==================== 命令: add ====================

async def cmd_add(args):
    """手动添加钱包到监控"""
    print("\n" + "="*80)
    print(f"➕ 添加钱包到监控")
    print("="*80 + "\n")

    if not args.address:
        print("❌ 请提供钱包地址")
        print("   用法: python hyperliquid_monitor.py add <地址> --label \"名称\"\n")
        return

    with HyperliquidDB() as db:
        try:
            result = db.add_monitored_wallet(
                address=args.address,
                label=args.label or f"Manual_{args.address[:10]}",
                monitor_type='manual'
            )

            if result:
                print(f"✅ 成功添加钱包到监控列表")
                print(f"   地址: {args.address}")
                print(f"   标签: {args.label or '未设置'}\n")
            else:
                print(f"⚠️  钱包已存在于监控列表")
                print(f"   地址: {args.address}\n")

        except Exception as e:
            print(f"❌ 添加失败: {e}\n")


# ==================== 命令: remove ====================

async def cmd_remove(args):
    """移除监控钱包"""
    print("\n" + "="*80)
    print(f"🗑️  移除监控钱包")
    print("="*80 + "\n")

    if not args.address:
        print("❌ 请提供钱包地址")
        print("   用法: python hyperliquid_monitor.py remove <地址>\n")
        return

    with HyperliquidDB() as db:
        try:
            # 查找钱包
            cursor = db.conn.cursor()
            cursor.execute("""
                SELECT trader_id, label FROM hyperliquid_monitored_wallets
                WHERE address = %s
            """, (args.address,))

            wallet = cursor.fetchone()
            if not wallet:
                print(f"❌ 钱包不在监控列表中")
                print(f"   地址: {args.address}\n")
                return

            trader_id = wallet[0]
            label = wallet[1]

            # 确认删除
            if not args.force:
                confirm = input(f"确认删除钱包 '{label}' ({args.address[:16]}...)? [y/N]: ")
                if confirm.lower() != 'y':
                    print("❌ 已取消\n")
                    return

            # 删除 (设置为不活跃)
            cursor.execute("""
                UPDATE hyperliquid_monitored_wallets
                SET is_active = 0, updated_at = NOW()
                WHERE trader_id = %s
            """, (trader_id,))
            db.conn.commit()

            print(f"✅ 已移除钱包")
            print(f"   标签: {label}")
            print(f"   地址: {args.address}\n")

        except Exception as e:
            print(f"❌ 移除失败: {e}\n")


# ==================== 命令: watch ====================

async def cmd_watch(args):
    """实时监控钱包"""
    print("\n" + "="*100)
    print(f"👁️  实时监控钱包")
    print("="*100 + "\n")

    if not args.address:
        print("❌ 请提供钱包地址")
        print("   用法: python hyperliquid_monitor.py watch <地址> --hours 24\n")
        return

    config = load_config()
    hyperliquid_config = config.get('hyperliquid', {})
    collector = HyperliquidCollector(hyperliquid_config)

    print(f"正在监控钱包: {args.address[:16]}...")
    print(f"时间范围: 最近 {args.hours} 小时\n")

    try:
        result = await collector.monitor_address(
            address=args.address,
            hours=args.hours
        )

        # 显示交易
        trades = result.get('recent_trades', [])
        print(f"📊 最近交易: {len(trades)} 笔\n")

        if trades:
            table_data = []
            for i, trade in enumerate(trades[:args.limit], 1):
                table_data.append([
                    i,
                    trade['coin'],
                    trade['action'],
                    f"${trade['price']:,.4f}",
                    f"{trade['size']:.4f}",
                    f"${trade['notional_usd']:,.2f}",
                    f"${trade['closed_pnl']:,.2f}",
                    trade['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
                ])

            headers = ['#', '币种', '方向', '价格', '数量', '名义价值', 'PnL', '时间']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
            print()

        # 显示持仓
        positions = result.get('positions', [])
        print(f"💼 当前持仓: {len(positions)} 个\n")

        if positions:
            table_data = []
            for i, pos in enumerate(positions, 1):
                table_data.append([
                    i,
                    pos['coin'],
                    pos['side'],
                    f"{pos['size']:.4f}",
                    f"${pos['entry_price']:,.4f}",
                    f"${pos.get('mark_price', pos['entry_price']):,.4f}",
                    f"${pos['notional_usd']:,.2f}",
                    f"${pos['unrealized_pnl']:,.2f}"
                ])

            headers = ['#', '币种', '方向', '数量', '入场价', '标记价', '名义价值', '未实现盈亏']
            print(tabulate(table_data, headers=headers, tablefmt='grid'))
            print()

        # 显示统计
        stats = result.get('statistics', {})
        print("📈 统计数据:")
        print(f"  总交易量: ${stats.get('total_volume_usd', 0):,.2f}")
        print(f"  总盈亏: ${stats.get('total_pnl', 0):,.2f}")
        print(f"  净流入/出: ${stats.get('net_flow_usd', 0):,.2f}")
        print(f"  交易次数: {stats.get('trade_count', 0)}\n")

        # 保存结果
        if args.save:
            filename = f"watch_{args.address[:10]}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"钱包监控报告\n")
                f.write(f"地址: {args.address}\n")
                f.write(f"时间: {datetime.utcnow()}\n")
                f.write(f"范围: 最近 {args.hours} 小时\n\n")
                f.write(f"交易数: {len(trades)}\n")
                f.write(f"持仓数: {len(positions)}\n")
                f.write(f"总交易量: ${stats.get('total_volume_usd', 0):,.2f}\n")
                f.write(f"总盈亏: ${stats.get('total_pnl', 0):,.2f}\n")
            print(f"✅ 结果已保存到: {filename}\n")

    except Exception as e:
        print(f"❌ 监控失败: {e}\n")
        import traceback
        traceback.print_exc()


# ==================== 命令: history ====================

async def cmd_history(args):
    """查看交易历史"""
    print("\n" + "="*100)
    print(f"📜 交易历史")
    print("="*100 + "\n")

    with HyperliquidDB() as db:
        cursor = db.conn.cursor()

        # 构建查询
        where_clause = []
        params = []

        if args.coin:
            where_clause.append("coin = %s")
            params.append(args.coin)

        if args.address:
            where_clause.append("address = %s")
            params.append(args.address)

        if args.days:
            where_clause.append("trade_time >= DATE_SUB(NOW(), INTERVAL %s DAY)")
            params.append(args.days)

        where_sql = " AND ".join(where_clause) if where_clause else "1=1"

        # 查询
        query = f"""
            SELECT
                t.coin, t.side, t.price, t.size, t.notional_usd,
                t.closed_pnl, t.trade_time, w.label
            FROM hyperliquid_wallet_trades t
            LEFT JOIN hyperliquid_monitored_wallets w ON t.address = w.address
            WHERE {where_sql}
            ORDER BY t.trade_time DESC
            LIMIT %s
        """
        params.append(args.limit)

        cursor.execute(query, params)
        trades = cursor.fetchall()

        if not trades:
            print("❌ 没有找到交易记录")
            print("   尝试调整筛选条件\n")
            return

        print(f"找到 {len(trades)} 笔交易\n")

        # 显示
        table_data = []
        for i, trade in enumerate(trades, 1):
            coin, side, price, size, notional, pnl, trade_time, label = trade
            table_data.append([
                i,
                label or 'Unknown',
                coin,
                side,
                f"${price:,.4f}",
                f"{size:.4f}",
                f"${notional:,.2f}",
                f"${pnl:,.2f}",
                trade_time.strftime('%Y-%m-%d %H:%M')
            ])

        headers = ['#', '钱包', '币种', '方向', '价格', '数量', '名义价值', 'PnL', '时间']
        print(tabulate(table_data, headers=headers, tablefmt='grid'))
        print()


# ==================== 主函数 ====================

def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='Hyperliquid 聪明钱包监控工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 扫描排行榜并添加前10名
  python hyperliquid_monitor.py scan --period week --min-pnl 50000 --add 10

  # 查看监控列表
  python hyperliquid_monitor.py list

  # 手动添加钱包
  python hyperliquid_monitor.py add 0x1234... --label "顶级交易员"

  # 实时监控钱包
  python hyperliquid_monitor.py watch 0x1234... --hours 24 --save

  # 查看交易历史
  python hyperliquid_monitor.py history --coin BTC --limit 20

  # 移除钱包
  python hyperliquid_monitor.py remove 0x1234...
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # scan 命令
    parser_scan = subparsers.add_parser('scan', help='扫描排行榜发现聪明钱包')
    parser_scan.add_argument('--period', choices=['day', 'week', 'month'], default='week',
                            help='时间周期 (默认: week)')
    parser_scan.add_argument('--min-pnl', type=float, default=10000,
                            help='最低盈利(USD) (默认: 10000)')
    parser_scan.add_argument('--limit', type=int, default=20,
                            help='显示数量 (默认: 20)')
    parser_scan.add_argument('--add', type=int, default=0,
                            help='自动添加前N个到监控 (默认: 0)')

    # list 命令
    parser_list = subparsers.add_parser('list', help='查看监控钱包列表')
    parser_list.add_argument('--all', action='store_true',
                            help='显示包括停用的钱包')
    parser_list.add_argument('--limit', type=int, default=50,
                            help='显示数量 (默认: 50)')

    # add 命令
    parser_add = subparsers.add_parser('add', help='手动添加钱包')
    parser_add.add_argument('address', help='钱包地址')
    parser_add.add_argument('--label', help='钱包标签/名称')

    # remove 命令
    parser_remove = subparsers.add_parser('remove', help='移除监控钱包')
    parser_remove.add_argument('address', help='钱包地址')
    parser_remove.add_argument('--force', action='store_true',
                               help='不询问直接删除')

    # watch 命令
    parser_watch = subparsers.add_parser('watch', help='实时监控钱包')
    parser_watch.add_argument('address', help='钱包地址')
    parser_watch.add_argument('--hours', type=int, default=24,
                             help='时间范围(小时) (默认: 24)')
    parser_watch.add_argument('--limit', type=int, default=20,
                             help='显示交易数量 (默认: 20)')
    parser_watch.add_argument('--save', action='store_true',
                             help='保存结果到文件')

    # history 命令
    parser_history = subparsers.add_parser('history', help='查看交易历史')
    parser_history.add_argument('--coin', help='筛选币种')
    parser_history.add_argument('--address', help='筛选钱包地址')
    parser_history.add_argument('--days', type=int, help='最近N天')
    parser_history.add_argument('--limit', type=int, default=50,
                               help='显示数量 (默认: 50)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # 执行命令
    try:
        if args.command == 'scan':
            asyncio.run(cmd_scan(args))
        elif args.command == 'list':
            asyncio.run(cmd_list(args))
        elif args.command == 'add':
            asyncio.run(cmd_add(args))
        elif args.command == 'remove':
            asyncio.run(cmd_remove(args))
        elif args.command == 'watch':
            asyncio.run(cmd_watch(args))
        elif args.command == 'history':
            asyncio.run(cmd_history(args))
    except KeyboardInterrupt:
        print("\n\n已取消\n")
    except Exception as e:
        print(f"\n❌ 执行失败: {e}\n")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
