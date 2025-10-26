#!/usr/bin/env python3
"""
快速验证 Dashboard 能看到什么数据
直接查询数据库，模拟 Dashboard 的查询逻辑
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import pymysql
import yaml
from datetime import datetime, timedelta

def main():
    print("=" * 80)
    print("验证 Dashboard Hyperliquid 数据")
    print("=" * 80)
    print()

    # 加载配置
    print("📋 加载配置...")
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        db_config = config['database']['mysql']

    # 连接数据库
    print(f"🔌 连接数据库 {db_config['database']}...")
    conn = pymysql.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        cursorclass=pymysql.cursors.DictCursor
    )
    cursor = conn.cursor()

    try:
        # 1. 检查监控钱包数量
        print("\n1️⃣  检查监控钱包...")
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM hyperliquid_monitored_wallets
            WHERE is_monitoring = 1
        """)
        wallet_count = cursor.fetchone()['count']
        print(f"   活跃监控钱包: {wallet_count} 个")

        if wallet_count == 0:
            print("\n❌ 没有活跃的监控钱包！")
            print("\n解决方法:")
            print("   运行: python add_monitored_wallets.py")
            return

        # 2. 检查最近 24 小时交易
        print("\n2️⃣  检查最近 24 小时交易...")
        cutoff = datetime.now() - timedelta(hours=24)

        cursor.execute("""
            SELECT COUNT(*) as count, MAX(trade_time) as latest
            FROM hyperliquid_wallet_trades
            WHERE trade_time >= %s
        """, (cutoff,))
        result = cursor.fetchone()

        trades_24h = result['count']
        latest_trade = result['latest']

        print(f"   最近 24 小时交易数: {trades_24h} 笔")
        if latest_trade:
            print(f"   最新交易时间: {latest_trade}")

            # 计算距离现在多久
            time_diff = datetime.now() - latest_trade
            hours_ago = time_diff.total_seconds() / 3600
            print(f"   距离现在: {hours_ago:.1f} 小时前")

        # 3. 判断结果
        print("\n" + "=" * 80)
        if trades_24h == 0:
            print("❌ Dashboard 不显示数据的原因: 最近 24 小时没有交易！")
            print("=" * 80)
            print()

            # 检查历史数据
            print("📊 检查历史数据...")
            cursor.execute("""
                SELECT COUNT(*) as total, MIN(trade_time) as earliest, MAX(trade_time) as latest
                FROM hyperliquid_wallet_trades
            """)
            history = cursor.fetchone()

            print(f"\n   历史交易总数: {history['total']} 笔")
            print(f"   最早交易: {history['earliest']}")
            print(f"   最新交易: {history['latest']}")

            if history['latest']:
                time_since_last = datetime.now() - history['latest']
                days_ago = time_since_last.days
                hours_ago = time_since_last.seconds / 3600

                print(f"\n   ⚠️  最后一笔交易是 {days_ago} 天 {hours_ago:.1f} 小时前")

            print("\n💡 解决方法:")
            print("   1. 采集器可能已停止运行")
            print("      运行: python hyperliquid_monitor.py")
            print()
            print("   2. 或者监控的钱包最近确实没有交易（这是正常的）")
            print("      等待钱包有新交易，Dashboard 就会自动显示")
            print()

            # 显示最近几天的交易统计
            print("📈 最近交易统计:")
            for days in [1, 3, 7, 30]:
                cutoff_date = datetime.now() - timedelta(days=days)
                cursor.execute("""
                    SELECT COUNT(*) as count
                    FROM hyperliquid_wallet_trades
                    WHERE trade_time >= %s
                """, (cutoff_date,))
                count = cursor.fetchone()['count']
                print(f"   最近 {days:2d} 天: {count:4d} 笔交易")

        else:
            print("✅ Dashboard 应该能正常显示数据！")
            print("=" * 80)
            print()
            print(f"📊 数据概览:")
            print(f"   监控钱包: {wallet_count} 个")
            print(f"   24h 交易: {trades_24h} 笔")
            print(f"   最新交易: {latest_trade}")
            print()

            # 显示前 5 笔大额交易
            print("💰 最近 5 笔大额交易:")
            cursor.execute("""
                SELECT
                    mw.label,
                    wt.coin,
                    wt.side,
                    wt.notional_usd,
                    wt.trade_time
                FROM hyperliquid_wallet_trades wt
                LEFT JOIN hyperliquid_monitored_wallets mw ON wt.address = mw.address
                WHERE wt.trade_time >= %s
                ORDER BY wt.notional_usd DESC
                LIMIT 5
            """, (cutoff,))

            for idx, trade in enumerate(cursor.fetchall(), 1):
                direction = "📈" if trade['side'] == 'LONG' else "📉"
                label = trade['label'] or 'Unknown'
                print(f"   {idx}. {direction} {trade['coin']} ${trade['notional_usd']:,.0f}")
                print(f"      {label} @ {trade['trade_time']}")

            print()
            print("如果 Dashboard 仍然不显示，可能的原因:")
            print("   1. 浏览器缓存 - 清除缓存或按 Ctrl+Shift+R 强制刷新")
            print("   2. Dashboard 未重启 - 重启 Web 服务器")
            print("   3. 代码错误 - 检查 Dashboard 日志")

    finally:
        conn.close()

if __name__ == '__main__':
    main()
