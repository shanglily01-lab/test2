#!/usr/bin/env python3
"""
Hyperliquid 数据加载问题诊断工具
快速检查为什么 Dashboard 中 Hyperliquid 数据加载不出来
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pymysql
import yaml
import asyncio
from datetime import datetime, timedelta


def check_database_tables(db_config):
    """检查数据库表"""
    print("\n" + "=" * 80)
    print("1️⃣  检查数据库表")
    print("=" * 80)

    try:
        conn = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        cursor = conn.cursor()

        # 检查 Hyperliquid 相关表
        cursor.execute("SHOW TABLES LIKE '%hyperliquid%'")
        tables = cursor.fetchall()

        if not tables:
            print("❌ 未找到 Hyperliquid 相关表！")
            print("\n解决方法:")
            print("   需要初始化 Hyperliquid 数据库表。")
            print("   查找并运行: app/database/hyperliquid_schema.sql")
            print()
            return False

        print(f"✅ 找到 {len(tables)} 个 Hyperliquid 表:")
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM {table[0]}")
            count = cursor.fetchone()[0]
            print(f"   - {table[0]}: {count} 条记录")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return False


def check_monitored_wallets(db_config):
    """检查监控钱包"""
    print("\n" + "=" * 80)
    print("2️⃣  检查监控钱包")
    print("=" * 80)

    try:
        conn = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        cursor = conn.cursor()

        # 检查监控钱包
        cursor.execute("""
            SELECT COUNT(*)
            FROM hyperliquid_monitored_wallets
            WHERE is_monitoring = 1
        """)
        active_count = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM hyperliquid_monitored_wallets")
        total_count = cursor.fetchone()[0]

        print(f"总监控钱包: {total_count} 个")
        print(f"活跃钱包: {active_count} 个")

        if active_count == 0:
            print("\n❌ 没有活跃的监控钱包！")
            print("\n解决方法:")
            print("   1. 自动发现聪明交易者:")
            print("      python manage_smart_wallets.py")
            print()
            print("   2. 或手动在 config.yaml 中配置:")
            print("      hyperliquid:")
            print("        addresses:")
            print("          - address: \"0x...\"")
            print("            label: \"Smart Trader 1\"")
            print()
            return False

        # 显示前 5 个钱包
        cursor.execute("""
            SELECT address, label, discovered_pnl, discovered_roi, is_monitoring
            FROM hyperliquid_monitored_wallets
            ORDER BY discovered_pnl DESC
            LIMIT 5
        """)

        wallets = cursor.fetchall()
        print(f"\n✅ 监控钱包 (前 5 名):")
        for addr, label, pnl, roi, is_monitoring in wallets:
            status = "✅" if is_monitoring else "⏸️ "
            print(f"   {status} {label or addr[:10]+'...'}: PnL=${pnl:,.0f}, ROI={roi:.1f}%")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ 检查监控钱包失败: {e}")
        return False


def check_recent_trades(db_config):
    """检查最近交易"""
    print("\n" + "=" * 80)
    print("3️⃣  检查最近交易")
    print("=" * 80)

    try:
        conn = pymysql.connect(
            host=db_config['host'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        cursor = conn.cursor()

        # 检查最近 24 小时交易
        cursor.execute("""
            SELECT COUNT(*), MAX(trade_time)
            FROM hyperliquid_wallet_trades
            WHERE trade_time >= NOW() - INTERVAL 24 HOUR
        """)
        count_24h, latest = cursor.fetchone()

        print(f"最近 24 小时交易: {count_24h} 笔")
        if latest:
            print(f"最新交易时间: {latest}")

        if count_24h == 0:
            print("\n⚠️  最近 24 小时没有交易记录！")
            print("\n可能原因:")
            print("   1. 采集器未运行")
            print("   2. 监控的钱包最近没有交易")
            print("   3. API 连接失败")
            print("\n解决方法:")
            print("   1. 启动采集器: python app/scheduler.py")
            print("   2. 手动采集: python hyperliquid_monitor.py")
            print("   3. 检查网络/代理配置")
            print()

            # 检查历史交易
            cursor.execute("SELECT COUNT(*), MAX(trade_time) FROM hyperliquid_wallet_trades")
            total_count, last_ever = cursor.fetchone()
            if total_count > 0:
                print(f"📊 历史交易总数: {total_count} 笔")
                print(f"   最后一次交易: {last_ever}")
                print("   说明之前采集过数据，但最近停止了。")
            return False

        # 按钱包统计
        cursor.execute("""
            SELECT address,
                   COUNT(*) as trades,
                   SUM(CASE WHEN side = 'LONG' THEN notional_usd ELSE 0 END) as long_usd,
                   SUM(CASE WHEN side = 'SHORT' THEN notional_usd ELSE 0 END) as short_usd
            FROM hyperliquid_wallet_trades
            WHERE trade_time >= NOW() - INTERVAL 24 HOUR
            GROUP BY address
            ORDER BY trades DESC
            LIMIT 5
        """)

        wallet_stats = cursor.fetchall()
        print(f"\n✅ 活跃钱包 (前 5 名):")
        for addr, trades, long_usd, short_usd in wallet_stats:
            net = long_usd - short_usd
            direction = "📈" if net > 0 else "📉"
            print(f"   {direction} {addr[:10]}...: {trades} 笔, 净流: ${net:,.0f}")

        # 按币种统计
        cursor.execute("""
            SELECT coin,
                   COUNT(*) as trades,
                   SUM(CASE WHEN side = 'LONG' THEN notional_usd ELSE -notional_usd END) as net_flow
            FROM hyperliquid_wallet_trades
            WHERE trade_time >= NOW() - INTERVAL 24 HOUR
            GROUP BY coin
            ORDER BY ABS(net_flow) DESC
            LIMIT 5
        """)

        coin_stats = cursor.fetchall()
        print(f"\n✅ 活跃币种 (前 5 名):")
        for coin, trades, net_flow in coin_stats:
            direction = "📈" if net_flow > 0 else "📉"
            print(f"   {direction} {coin}: {trades} 笔, 净流: ${net_flow:,.0f}")

        conn.close()
        return True

    except Exception as e:
        print(f"❌ 检查交易记录失败: {e}")
        return False


async def test_api_connection(config):
    """测试 API 连接"""
    print("\n" + "=" * 80)
    print("4️⃣  测试 Hyperliquid API 连接")
    print("=" * 80)

    try:
        from app.collectors.hyperliquid_collector import HyperliquidCollector

        collector = HyperliquidCollector(config)

        # 测试获取排行榜
        print("\n测试获取排行榜...")
        leaderboard = await collector.fetch_leaderboard('week')

        if leaderboard and len(leaderboard) > 0:
            print(f"✅ API 连接正常，获取到 {len(leaderboard)} 个交易者")
            print(f"\n排行榜前 3 名:")
            for idx, entry in enumerate(leaderboard[:3], 1):
                addr = entry.get('ethAddress', 'Unknown')
                account_value = entry.get('accountValue', 0)
                print(f"   {idx}. {addr[:10]}...: ${float(account_value):,.0f}")
            return True
        else:
            print("⚠️  API 返回空数据")
            return False

    except Exception as e:
        print(f"❌ API 连接失败: {e}")
        print("\n可能原因:")
        print("   1. 网络问题，需要代理")
        print("   2. API 暂时不可用")
        print("   3. 超时")
        print("\n解决方法:")
        print("   在 config.yaml 中配置代理:")
        print("     smart_money:")
        print("       proxy: \"http://127.0.0.1:7890\"")
        print()
        return False


def check_config(config_path='config.yaml'):
    """检查配置文件"""
    print("\n" + "=" * 80)
    print("5️⃣  检查配置文件")
    print("=" * 80)

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # 检查 Hyperliquid 配置
        hyperliquid_config = config.get('hyperliquid', {})

        print(f"Hyperliquid 配置:")
        print(f"   启用: {hyperliquid_config.get('enabled', False)}")

        addresses = hyperliquid_config.get('addresses', [])
        print(f"   配置的地址: {len(addresses)} 个")

        if addresses:
            for addr_config in addresses[:3]:
                print(f"      - {addr_config.get('label', 'Unknown')}: {addr_config.get('address', 'N/A')[:10]}...")

        # 检查代理
        proxy = config.get('smart_money', {}).get('proxy', None)
        if proxy:
            print(f"   代理: {proxy}")
        else:
            print(f"   代理: 未配置")

        return True

    except Exception as e:
        print(f"❌ 检查配置失败: {e}")
        return False


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("Hyperliquid 数据加载问题诊断工具")
    print("=" * 80)
    print()

    # 加载配置
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        if 'mysql' in config['database']:
            db_config = config['database']['mysql']
        else:
            db_config = config['database']

        print(f"✅ 配置加载成功")
        print(f"   数据库: {db_config['database']}")
        print(f"   主机: {db_config['host']}:{db_config['port']}")

    except Exception as e:
        print(f"❌ 加载配置失败: {e}")
        return

    # 运行诊断
    results = {
        'tables': check_database_tables(db_config),
        'wallets': check_monitored_wallets(db_config),
        'trades': check_recent_trades(db_config),
        'config': check_config()
    }

    # 测试 API 连接
    try:
        import asyncio
        results['api'] = asyncio.run(test_api_connection(config))
    except Exception as e:
        print(f"❌ API 测试失败: {e}")
        results['api'] = False

    # 总结
    print("\n" + "=" * 80)
    print("诊断总结")
    print("=" * 80)
    print()

    all_pass = all(results.values())

    if all_pass:
        print("✅ 所有检查通过！Hyperliquid 数据应该可以正常显示")
        print()
        print("如果 Dashboard 仍然不显示，请:")
        print("   1. 重启 Dashboard: python app/main.py")
        print("   2. 清除浏览器缓存")
        print("   3. 检查浏览器控制台错误")
    else:
        print("❌ 发现以下问题:")
        print()
        if not results['tables']:
            print("   ❌ 数据库表不存在 - 需要初始化")
        if not results['wallets']:
            print("   ❌ 没有监控钱包 - 需要添加")
        if not results['trades']:
            print("   ❌ 没有交易记录 - 需要运行采集器")
        if not results['api']:
            print("   ❌ API 连接失败 - 需要配置代理或检查网络")

        print()
        print("详细解决方案请查看: HYPERLIQUID_LOADING_ISSUE.md")

    print()


if __name__ == '__main__':
    main()
