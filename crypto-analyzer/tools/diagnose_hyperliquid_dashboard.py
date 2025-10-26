#!/usr/bin/env python3
"""
Hyperliquid 仪表盘数据加载诊断工具
用于诊断为什么 Hyperliquid 聪明钱活动数据在仪表盘上加载不出来
"""

import sys
from pathlib import Path
import yaml
from datetime import datetime, timedelta

# 添加项目根目录
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.database.hyperliquid_db import HyperliquidDB
from app.database.db_service import DatabaseService


def print_section(title):
    """打印分隔线"""
    print("\n" + "="*80)
    print(f"  {title}")
    print("="*80 + "\n")


def check_database_connection():
    """检查数据库连接"""
    print_section("1. 数据库连接检查")

    try:
        # 加载配置
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        db_config = config.get('database', {})
        print(f"数据库类型: {db_config.get('type')}")
        print(f"数据库主机: {db_config.get('host')}")
        print(f"数据库名称: {db_config.get('database')}")
        print(f"数据库用户: {db_config.get('username')}")

        # 测试连接
        with HyperliquidDB() as db:
            print("\n✅ Hyperliquid 数据库连接成功!")

        return True

    except Exception as e:
        print(f"\n❌ 数据库连接失败: {e}")
        return False


def check_monitored_wallets():
    """检查监控钱包"""
    print_section("2. 监控钱包检查")

    try:
        with HyperliquidDB() as db:
            # 获取所有监控钱包
            all_wallets = db.get_monitored_wallets(active_only=False)
            active_wallets = db.get_monitored_wallets(active_only=True)

            print(f"总监控钱包数: {len(all_wallets)}")
            print(f"活跃钱包数: {len(active_wallets)}")

            if not active_wallets:
                print("\n⚠️  警告: 没有活跃的监控钱包!")
                print("   解决方法: 运行 python hyperliquid_monitor.py scan --add 10")
                return False

            print("\n前5个活跃钱包:")
            for i, wallet in enumerate(active_wallets[:5], 1):
                print(f"  {i}. {wallet.get('label', 'Unknown')}")
                print(f"     地址: {wallet['address'][:10]}...")
                print(f"     最后检查: {wallet.get('last_check_at', 'Never')}")

            return True

    except Exception as e:
        print(f"\n❌ 检查监控钱包失败: {e}")
        return False


def check_trade_data():
    """检查交易数据"""
    print_section("3. 交易数据检查")

    try:
        with HyperliquidDB() as db:
            cursor = db.conn.cursor()

            # 总交易数
            cursor.execute("SELECT COUNT(*) as total FROM hyperliquid_wallet_trades")
            result = cursor.fetchone()
            total_trades = result[0] if isinstance(result, tuple) else result.get('total', 0)
            print(f"总交易记录数: {total_trades}")

            if total_trades == 0:
                print("\n⚠️  警告: 没有交易数据!")
                print("   可能原因:")
                print("   1. 调度器未运行或刚启动")
                print("   2. 监控钱包没有交易活动")
                print("   3. API 采集失败")
                return False

            # 最近的交易
            cursor.execute("""
                SELECT MAX(trade_time) as last_trade,
                       MIN(trade_time) as first_trade
                FROM hyperliquid_wallet_trades
            """)
            result = cursor.fetchone()
            if isinstance(result, tuple):
                last_trade = result[0]
                first_trade = result[1]
            else:
                last_trade = result.get('last_trade') if result else None
                first_trade = result.get('first_trade') if result else None

            print(f"最早交易时间: {first_trade}")
            print(f"最新交易时间: {last_trade}")

            if last_trade:
                time_diff = datetime.now() - last_trade
                print(f"距离现在: {time_diff}")

                if time_diff > timedelta(hours=1):
                    print("\n⚠️  警告: 最新交易时间超过1小时前!")
                    print("   可能原因:")
                    print("   1. 调度器未运行")
                    print("   2. 监控钱包近期无交易")
                    return False

            # 最近24小时的交易
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM hyperliquid_wallet_trades
                WHERE trade_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """)
            result = cursor.fetchone()
            recent_trades = result[0] if isinstance(result, tuple) else result.get('count', 0)
            print(f"\n最近24小时交易数: {recent_trades}")

            # 按币种统计
            cursor.execute("""
                SELECT coin, COUNT(*) as count
                FROM hyperliquid_wallet_trades
                WHERE trade_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                GROUP BY coin
                ORDER BY count DESC
                LIMIT 5
            """)

            print("\n最近24小时热门币种:")
            for row in cursor.fetchall():
                if isinstance(row, tuple):
                    coin, count = row[0], row[1]
                else:
                    coin = row.get('coin', 'Unknown')
                    count = row.get('count', 0)
                print(f"  {coin}: {count} 笔交易")

            return True

    except Exception as e:
        print(f"\n❌ 检查交易数据失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_position_data():
    """检查持仓数据"""
    print_section("4. 持仓数据检查")

    try:
        with HyperliquidDB() as db:
            cursor = db.conn.cursor()

            # 总持仓快照数
            cursor.execute("SELECT COUNT(*) as total FROM hyperliquid_wallet_positions")
            result = cursor.fetchone()
            total_positions = result[0] if isinstance(result, tuple) else result.get('total', 0)
            print(f"总持仓快照数: {total_positions}")

            if total_positions == 0:
                print("\n⚠️  警告: 没有持仓数据!")
                return False

            # 最新持仓快照
            cursor.execute("""
                SELECT MAX(snapshot_time) as last_snapshot,
                       MIN(snapshot_time) as first_snapshot
                FROM hyperliquid_wallet_positions
            """)
            result = cursor.fetchone()
            if isinstance(result, tuple):
                last_snapshot = result[0]
                first_snapshot = result[1]
            else:
                last_snapshot = result.get('last_snapshot') if result else None
                first_snapshot = result.get('first_snapshot') if result else None

            print(f"最早快照时间: {first_snapshot}")
            print(f"最新快照时间: {last_snapshot}")

            if last_snapshot:
                time_diff = datetime.now() - last_snapshot
                print(f"距离现在: {time_diff}")

                if time_diff > timedelta(hours=1):
                    print("\n⚠️  警告: 最新持仓快照超过1小时前!")
                    return False

            # 当前持仓统计
            cursor.execute("""
                SELECT coin, COUNT(*) as count, SUM(notional_usd) as total_usd
                FROM hyperliquid_wallet_positions
                WHERE snapshot_time = (SELECT MAX(snapshot_time) FROM hyperliquid_wallet_positions)
                GROUP BY coin
                ORDER BY total_usd DESC
                LIMIT 5
            """)

            print("\n当前持仓前5币种:")
            for row in cursor.fetchall():
                if isinstance(row, tuple):
                    coin, count, total_usd = row[0], row[1], row[2]
                else:
                    coin = row.get('coin', 'Unknown')
                    count = row.get('count', 0)
                    total_usd = row.get('total_usd', 0)
                print(f"  {coin}: {count} 个持仓, 总价值 ${total_usd:,.2f}")

            return True

    except Exception as e:
        print(f"\n❌ 检查持仓数据失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_api_response():
    """检查 API 响应"""
    print_section("5. API 响应检查")

    try:
        import requests

        # 测试仪表盘 API
        url = "http://localhost:8000/api/dashboard"
        print(f"测试 API: {url}")

        response = requests.get(url, timeout=10)

        print(f"HTTP 状态码: {response.status_code}")

        if response.status_code != 200:
            print(f"\n❌ API 响应异常: {response.status_code}")
            print(f"响应内容: {response.text[:500]}")
            return False

        data = response.json()

        print("\nAPI 响应结构:")
        print(f"  success: {data.get('success')}")

        if 'data' in data:
            print(f"  data.prices: {len(data['data'].get('prices', []))} 个")
            print(f"  data.recommendations: {len(data['data'].get('recommendations', []))} 个")

            hyperliquid_data = data['data'].get('hyperliquid', {})
            print(f"\n  Hyperliquid 数据:")
            print(f"    monitored_wallets: {hyperliquid_data.get('monitored_wallets', 0)}")
            print(f"    total_volume_24h: ${hyperliquid_data.get('total_volume_24h', 0):,.2f}")
            print(f"    recent_trades: {len(hyperliquid_data.get('recent_trades', []))} 笔")
            print(f"    top_coins: {len(hyperliquid_data.get('top_coins', []))} 个")

            if hyperliquid_data.get('monitored_wallets', 0) == 0:
                print("\n⚠️  API 返回的监控钱包数为 0!")
                return False

            if len(hyperliquid_data.get('recent_trades', [])) == 0:
                print("\n⚠️  API 返回的最近交易为空!")
                print("   这就是为什么仪表盘加载不出来的原因!")
                return False

            print("\n✅ API 响应正常,包含 Hyperliquid 数据!")

            # 显示前3笔交易
            print("\n前3笔最近交易:")
            for i, trade in enumerate(hyperliquid_data.get('recent_trades', [])[:3], 1):
                print(f"  {i}. {trade.get('coin')} - {trade.get('side')} - ${trade.get('notional_usd', 0):,.2f}")

            return True
        else:
            print("\n❌ API 响应缺少 data 字段!")
            return False

    except requests.exceptions.ConnectionError:
        print("\n❌ 无法连接到 API (http://localhost:8000)")
        print("   请确认 Web 服务器正在运行: python app/main.py")
        return False
    except Exception as e:
        print(f"\n❌ 检查 API 响应失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_scheduler_status():
    """检查调度器状态"""
    print_section("6. 调度器状态检查")

    try:
        # 检查日志文件
        log_file = Path("logs/scheduler.log")

        if not log_file.exists():
            print("⚠️  警告: 调度器日志文件不存在!")
            print("   调度器可能未运行")
            return False

        # 读取最后几行
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            last_lines = lines[-20:] if len(lines) > 20 else lines

        print("最近的调度器日志:")
        for line in last_lines:
            if 'Hyperliquid' in line or 'hyperliquid' in line:
                print(f"  {line.strip()}")

        # 检查最后一次 Hyperliquid 监控
        for line in reversed(lines):
            if '开始监控 Hyperliquid 聪明钱包' in line or 'monitor_hyperliquid_wallets' in line:
                print(f"\n最后一次 Hyperliquid 监控:")
                print(f"  {line.strip()}")
                break
        else:
            print("\n⚠️  警告: 日志中找不到 Hyperliquid 监控记录!")
            return False

        return True

    except Exception as e:
        print(f"\n❌ 检查调度器状态失败: {e}")
        return False


def main():
    """主函数"""
    print("\n" + "🔍 Hyperliquid 仪表盘数据加载诊断工具")
    print("="*80)
    print("本工具将检查为什么 Hyperliquid 聪明钱活动数据在仪表盘上加载不出来")
    print("="*80 + "\n")

    results = {}

    # 执行检查
    results['数据库连接'] = check_database_connection()
    results['监控钱包'] = check_monitored_wallets()
    results['交易数据'] = check_trade_data()
    results['持仓数据'] = check_position_data()
    results['调度器状态'] = check_scheduler_status()
    results['API响应'] = check_api_response()

    # 汇总结果
    print_section("诊断结果汇总")

    all_passed = True
    for check_name, passed in results.items():
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"{status} - {check_name}")
        if not passed:
            all_passed = False

    print("\n" + "="*80)

    if all_passed:
        print("\n🎉 所有检查都通过!")
        print("   如果仪表盘仍然加载不出来,可能是浏览器缓存问题")
        print("   解决方法:")
        print("   1. 按 Ctrl + F5 强制刷新页面")
        print("   2. 清除浏览器缓存")
        print("   3. 打开浏览器开发者工具(F12)查看 Console 和 Network 标签页")
    else:
        print("\n⚠️  发现问题!")
        print("\n常见问题和解决方法:")
        print("\n1. 如果没有监控钱包:")
        print("   python hyperliquid_monitor.py scan --add 10")
        print("\n2. 如果没有交易数据:")
        print("   - 确认调度器正在运行: python app/scheduler.py")
        print("   - 等待30分钟让系统采集数据")
        print("\n3. 如果 API 无法连接:")
        print("   - 确认 Web 服务器正在运行: python app/main.py")
        print("   - 检查端口 8000 是否被占用")
        print("\n4. 如果数据过旧:")
        print("   - 重启调度器")
        print("   - 检查日志: logs/scheduler.log")

    print("\n" + "="*80)
    print("\n💡 提示: 如果需要更详细的日志,请查看:")
    print("   - logs/app.log (Web服务器日志)")
    print("   - logs/scheduler.log (调度器日志)")
    print("\n")


if __name__ == '__main__':
    main()
