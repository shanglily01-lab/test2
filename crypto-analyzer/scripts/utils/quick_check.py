#!/usr/bin/env python3
"""
快速检查 - 能否自动开仓
Quick Check - Can Auto-Trade
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
import pymysql

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['database']['mysql']

try:
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor(pymysql.cursors.DictCursor)

    print("=" * 70)
    print("⚡ 快速检查 - 自动交易条件")
    print("=" * 70)

    # 检查1: investment_recommendations表
    print("\n1️⃣ 检查投资建议表...")
    try:
        cursor.execute("""
            SELECT symbol, recommendation, confidence,
                   TIMESTAMPDIFF(MINUTE, updated_at, NOW()) as minutes_ago
            FROM investment_recommendations
            WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT')
        """)
        recommendations = cursor.fetchall()

        if not recommendations:
            print("   ❌ 表存在，但没有数据")
            print("   💡 运行: mysql -u root -p binance-data < app\\database\\investment_recommendations_schema.sql")
        else:
            print(f"   ✅ 找到 {len(recommendations)} 条建议\n")

            can_open = 0
            for rec in recommendations:
                symbol = rec['symbol']
                recommendation = rec['recommendation']
                confidence = float(rec['confidence'])
                minutes_ago = rec['minutes_ago'] or 0

                status_icon = "✅" if confidence >= 75 else "❌"
                time_status = "✅" if minutes_ago <= 60 else "⏰"

                print(f"   {status_icon} {symbol}: {recommendation} ({confidence:.0f}%) - {minutes_ago}分钟前 {time_status}")

                if confidence >= 75 and minutes_ago <= 60 and recommendation != '持有':
                    can_open += 1

            print(f"\n   💡 {can_open} 个币种满足开仓条件 (置信度>=75%, 建议不是持有, 时间<1小时)")

    except pymysql.err.ProgrammingError:
        print("   ❌ investment_recommendations 表不存在")
        print("   💡 运行: mysql -u root -p binance-data < app\\database\\investment_recommendations_schema.sql")

    # 检查2: futures_positions表
    print("\n2️⃣ 检查合约持仓表...")
    try:
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM futures_positions
            WHERE status = 'open'
        """)
        position_count = cursor.fetchone()['count']

        if position_count > 0:
            cursor.execute("""
                SELECT symbol, position_side, margin, unrealized_pnl
                FROM futures_positions
                WHERE status = 'open'
            """)
            positions = cursor.fetchall()

            print(f"   ✅ 当前有 {position_count} 个持仓\n")
            for pos in positions:
                pnl = float(pos['unrealized_pnl'] or 0)
                pnl_icon = "📈" if pnl >= 0 else "📉"
                print(f"      {pnl_icon} {pos['symbol']}: {pos['position_side']} (盈亏: ${pnl:.2f})")
        else:
            print("   ✅ 没有持仓 (可以开新仓)")

    except pymysql.err.ProgrammingError:
        print("   ❌ futures_positions 表不存在")
        print("   💡 运行: mysql -u root -p binance-data < app\\database\\futures_trading_schema.sql")

    # 检查3: 账户余额
    print("\n3️⃣ 检查账户余额...")
    try:
        cursor.execute("""
            SELECT current_balance, frozen_balance,
                   (current_balance - frozen_balance) as available
            FROM paper_trading_accounts
            WHERE id = 2
        """)
        account = cursor.fetchone()

        if account:
            available = float(account['available'])
            print(f"   ✅ 可用余额: ${available:.2f}")

            if available < 50:
                print("   ⚠️  余额不足 (需要至少 $50)")
        else:
            print("   ❌ 找不到账户ID=2")

    except pymysql.err.ProgrammingError:
        print("   ❌ paper_trading_accounts 表不存在")

    # 总结
    print("\n" + "=" * 70)
    print("📋 结论")
    print("=" * 70)

    # 重新检查是否有满足条件的
    try:
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM investment_recommendations
            WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT')
            AND confidence >= 75
            AND recommendation != '持有'
            AND updated_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
        """)
        eligible_count = cursor.fetchone()['count']

        if eligible_count > 0:
            print(f"\n✅ 有 {eligible_count} 个币种满足自动开仓条件")
            print("\n下一步:")
            print("   1. 确保调度器在运行: python app\\scheduler.py")
            print("   2. 等待最多30分钟（自动交易每30分钟执行一次）")
            print("   3. 或立即测试: python app\\trading\\auto_futures_trader.py")
        else:
            print("\n❌ 当前没有币种满足自动开仓条件")
            print("\n原因:")
            print("   • 置信度 < 75%")
            print("   • 建议是'持有'")
            print("   • 或建议太旧（超过1小时）")
            print("\n建议:")
            print("   1. 保持调度器运行，等待市场信号")
            print("   2. 查看详细评分: python check_confidence_breakdown.py")
            print("   3. 或临时降低门槛测试（修改 auto_futures_trader.py 第53行）")

    except:
        print("\n⚠️  无法判断，可能缺少必要的表")

    print("=" * 70)

    cursor.close()
    connection.close()

except pymysql.err.OperationalError as e:
    print(f"\n❌ 数据库连接失败: {e}")
    print("\n请检查:")
    print("   1. MySQL服务是否运行")
    print("   2. config.yaml 中的数据库配置是否正确")
    print("   3. 数据库 'binance-data' 是否存在")

except Exception as e:
    print(f"\n❌ 错误: {e}")
    import traceback
    traceback.print_exc()
