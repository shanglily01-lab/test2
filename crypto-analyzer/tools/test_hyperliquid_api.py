"""
测试 Hyperliquid API 数据获取
诊断为什么 recent_trades 为空
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.database.hyperliquid_db import HyperliquidDB
from datetime import datetime, timedelta

print("=" * 80)
print("  Hyperliquid API 数据获取测试")
print("=" * 80)
print()

try:
    with HyperliquidDB() as db:
        # 1. 获取监控钱包
        print("1. 获取监控钱包...")
        monitored = db.get_monitored_wallets(active_only=True)
        print(f"   总数: {len(monitored)} 个")

        if not monitored:
            print("   ❌ 没有监控钱包！")
            sys.exit(1)

        # 显示前5个钱包
        print("\n   前5个钱包:")
        for i, wallet in enumerate(monitored[:5], 1):
            print(f"   {i}. {wallet.get('label', 'Unknown')} - {wallet['address']}")

        # 2. 测试获取交易数据
        print("\n2. 测试获取交易数据 (最近24小时)...")

        total_trades_found = 0
        wallets_with_trades = []

        # 查询前20个钱包
        max_wallets = min(len(monitored), 20)
        print(f"   查询前 {max_wallets} 个钱包...\n")

        for idx, wallet in enumerate(monitored[:max_wallets], 1):
            address = wallet['address']
            label = wallet.get('label', 'Unknown')

            # 获取该钱包的交易
            trades = db.get_wallet_recent_trades(address, hours=24, limit=50)

            if trades:
                total_trades_found += len(trades)
                wallets_with_trades.append({
                    'wallet': label,
                    'address': address,
                    'trades': trades
                })
                print(f"   ✅ 钱包 {idx}: {label}")
                print(f"      地址: {address}")
                print(f"      交易数: {len(trades)} 笔")

                # 显示前3笔交易
                for i, trade in enumerate(trades[:3], 1):
                    print(f"      交易 {i}: {trade['coin']} {trade['side']} "
                          f"${trade['notional_usd']:,.2f} @ {trade['trade_time']}")
                print()
            else:
                print(f"   ⚪ 钱包 {idx}: {label} - 无交易")

        print("\n" + "=" * 80)
        print("  测试结果汇总")
        print("=" * 80)
        print(f"查询钱包数: {max_wallets}")
        print(f"有交易的钱包: {len(wallets_with_trades)}")
        print(f"总交易数: {total_trades_found}")

        if total_trades_found == 0:
            print("\n❌ 问题: 前20个钱包在最近24小时都没有交易!")
            print("\n可能的原因:")
            print("1. 钱包列表排序问题 - 不活跃的钱包排在前面")
            print("2. 数据采集停止 - 调度器没有运行")
            print("3. 时间范围太短 - 这些钱包的交易不频繁")

            # 检查是否有任何交易
            print("\n3. 检查数据库中是否有任何交易...")
            db.cursor.execute("""
                SELECT COUNT(*) as total,
                       MAX(trade_time) as latest_trade
                FROM hyperliquid_wallet_trades
            """)
            result = db.cursor.fetchone()

            if isinstance(result, tuple):
                total_count = result[0]
                latest_trade = result[1]
            else:
                total_count = result.get('total', 0)
                latest_trade = result.get('latest_trade')

            print(f"   数据库总交易数: {total_count}")
            print(f"   最新交易时间: {latest_trade}")

            if latest_trade:
                time_diff = datetime.now() - latest_trade
                print(f"   距离现在: {time_diff}")

                if time_diff.total_seconds() > 3600:  # 超过1小时
                    print("\n   ⚠️  最新交易超过1小时前，调度器可能已停止!")
        else:
            print(f"\n✅ 成功找到 {total_trades_found} 笔交易")

            # 4. 测试 enhanced_dashboard 的逻辑
            print("\n4. 模拟 enhanced_dashboard 的查询逻辑...")

            # 这是 enhanced_dashboard.py 中的逻辑
            recent_trades_list = []
            total_volume = 0

            for wallet_data in wallets_with_trades[:10]:  # 只取前10个有交易的
                for trade in wallet_data['trades'][:20]:  # 每个钱包最多20笔
                    recent_trades_list.append({
                        'wallet_label': wallet_data['wallet'],
                        'coin': trade['coin'],
                        'side': trade['side'],
                        'notional_usd': float(trade['notional_usd']),
                        'price': float(trade['price']),
                        'closed_pnl': float(trade['closed_pnl']),
                        'trade_time': trade['trade_time'].strftime('%Y-%m-%d %H:%M')
                    })
                    total_volume += float(trade['notional_usd'])

            print(f"   会返回的交易数: {len(recent_trades_list)}")
            print(f"   总交易量: ${total_volume:,.2f}")

            if len(recent_trades_list) == 0:
                print("   ❌ 即使有交易数据，但返回列表仍为空！")
            else:
                print("   ✅ 数据处理正常")
                print("\n   示例交易:")
                for i, trade in enumerate(recent_trades_list[:3], 1):
                    print(f"   {i}. {trade['coin']} {trade['side']} "
                          f"${trade['notional_usd']:,.2f} - {trade['wallet_label']}")

except Exception as e:
    print(f"\n❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
