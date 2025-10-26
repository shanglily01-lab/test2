#!/usr/bin/env python3
"""
价格数据新鲜度诊断工具
检查数据库中各个表的价格数据是否实时更新
"""

import sys
sys.path.insert(0, '.')

import pymysql
import yaml
from datetime import datetime, timedelta
from tabulate import tabulate

def main():
    print("=" * 100)
    print("价格数据新鲜度诊断工具")
    print("=" * 100)
    print()

    # 加载配置
    print("📋 加载配置...")
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
        db_config = config['database']['mysql']
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

    # 连接数据库
    print(f"🔌 连接数据库 {db_config['database']}...")
    conn = pymysql.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        cursorclass=pymysql.cursors.DictCursor
    )

    try:
        cursor = conn.cursor()
        now = datetime.now()

        # ============================================================
        # 1. 检查 price_data 表 (实时价格)
        # ============================================================
        print("\n" + "=" * 100)
        print("📊 1. 实时价格数据 (price_data 表)")
        print("=" * 100)

        price_data_results = []
        for symbol in symbols:
            cursor.execute("""
                SELECT symbol, exchange, price, timestamp,
                       TIMESTAMPDIFF(MINUTE, timestamp, NOW()) as minutes_ago
                FROM price_data
                WHERE symbol = %s
                ORDER BY timestamp DESC
                LIMIT 1
            """, (symbol,))
            result = cursor.fetchone()

            if result:
                minutes_ago = result['minutes_ago']
                if minutes_ago < 5:
                    status = "✅ 最新"
                elif minutes_ago < 30:
                    status = "⚠️  稍旧"
                else:
                    status = "❌ 过期"

                price_data_results.append([
                    status,
                    result['symbol'],
                    result['exchange'],
                    f"${result['price']:,.2f}",
                    result['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                    f"{minutes_ago} 分钟前"
                ])
            else:
                price_data_results.append([
                    "❌ 无数据",
                    symbol,
                    "-",
                    "-",
                    "-",
                    "-"
                ])

        print(tabulate(price_data_results,
                      headers=['状态', '交易对', '交易所', '价格', '时间戳', '数据年龄'],
                      tablefmt='grid'))

        # ============================================================
        # 2. 检查 kline_data 表 (K线数据)
        # ============================================================
        print("\n" + "=" * 100)
        print("📈 2. K线数据 (kline_data 表)")
        print("=" * 100)

        timeframes = ['1m', '5m', '1h', '1d']
        kline_results = []

        for timeframe in timeframes:
            for symbol in symbols[:3]:  # 只检查前3个币种
                cursor.execute("""
                    SELECT symbol, exchange, timeframe, close_price,
                           FROM_UNIXTIME(open_time/1000) as open_time,
                           TIMESTAMPDIFF(MINUTE, FROM_UNIXTIME(open_time/1000), NOW()) as minutes_ago
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY open_time DESC
                    LIMIT 1
                """, (symbol, timeframe))
                result = cursor.fetchone()

                if result:
                    minutes_ago = result['minutes_ago']

                    # 根据时间周期判断数据是否新鲜
                    if timeframe == '1m':
                        threshold = 5
                    elif timeframe == '5m':
                        threshold = 10
                    elif timeframe == '1h':
                        threshold = 90
                    else:  # 1d
                        threshold = 1500  # 25小时

                    if minutes_ago < threshold:
                        status = "✅ 最新"
                    elif minutes_ago < threshold * 2:
                        status = "⚠️  稍旧"
                    else:
                        status = "❌ 过期"

                    kline_results.append([
                        status,
                        f"{result['symbol']} ({timeframe})",
                        result['exchange'],
                        f"${result['close_price']:,.2f}",
                        result['open_time'].strftime('%Y-%m-%d %H:%M:%S'),
                        f"{minutes_ago} 分钟前"
                    ])
                else:
                    kline_results.append([
                        "❌ 无数据",
                        f"{symbol} ({timeframe})",
                        "-",
                        "-",
                        "-",
                        "-"
                    ])

        print(tabulate(kline_results,
                      headers=['状态', '交易对(周期)', '交易所', '收盘价', 'K线时间', '数据年龄'],
                      tablefmt='grid'))

        # ============================================================
        # 3. 总体统计
        # ============================================================
        print("\n" + "=" * 100)
        print("📊 3. 数据库总体统计")
        print("=" * 100)

        # price_data 表统计
        cursor.execute("""
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT symbol) as total_symbols,
                MIN(timestamp) as earliest,
                MAX(timestamp) as latest,
                TIMESTAMPDIFF(MINUTE, MAX(timestamp), NOW()) as minutes_since_last_update
            FROM price_data
        """)
        price_stats = cursor.fetchone()

        # kline_data 表统计
        cursor.execute("""
            SELECT
                COUNT(*) as total_records,
                COUNT(DISTINCT symbol) as total_symbols,
                COUNT(DISTINCT timeframe) as total_timeframes,
                MIN(FROM_UNIXTIME(open_time/1000)) as earliest,
                MAX(FROM_UNIXTIME(open_time/1000)) as latest,
                TIMESTAMPDIFF(MINUTE, MAX(FROM_UNIXTIME(open_time/1000)), NOW()) as minutes_since_last_update
            FROM kline_data
        """)
        kline_stats = cursor.fetchone()

        stats_table = [
            ["price_data", price_stats['total_records'], price_stats['total_symbols'],
             price_stats['earliest'].strftime('%Y-%m-%d %H:%M:%S') if price_stats['earliest'] else '-',
             price_stats['latest'].strftime('%Y-%m-%d %H:%M:%S') if price_stats['latest'] else '-',
             f"{price_stats['minutes_since_last_update']} 分钟前" if price_stats['minutes_since_last_update'] is not None else '-'],
            ["kline_data", kline_stats['total_records'], kline_stats['total_symbols'],
             kline_stats['earliest'].strftime('%Y-%m-%d %H:%M:%S') if kline_stats['earliest'] else '-',
             kline_stats['latest'].strftime('%Y-%m-%d %H:%M:%S') if kline_stats['latest'] else '-',
             f"{kline_stats['minutes_since_last_update']} 分钟前" if kline_stats['minutes_since_last_update'] is not None else '-']
        ]

        print(tabulate(stats_table,
                      headers=['表名', '总记录数', '币种数', '最早数据', '最新数据', '最后更新'],
                      tablefmt='grid'))

        # ============================================================
        # 4. 诊断结果和建议
        # ============================================================
        print("\n" + "=" * 100)
        print("🔍 4. 诊断结果")
        print("=" * 100)

        issues = []
        recommendations = []

        # 检查 price_data 新鲜度
        if price_stats['minutes_since_last_update'] is None:
            issues.append("❌ price_data 表为空，没有任何价格数据")
            recommendations.append("启动数据采集器: python app/scheduler.py")
        elif price_stats['minutes_since_last_update'] > 10:
            issues.append(f"⚠️  price_data 表数据已过期 ({price_stats['minutes_since_last_update']} 分钟)")
            recommendations.append("检查数据采集器是否运行: tasklist | findstr python (Windows) 或 ps aux | grep scheduler (Linux)")
            recommendations.append("重启数据采集器: python app/scheduler.py")

        # 检查 kline_data 新鲜度
        if kline_stats['minutes_since_last_update'] is None:
            issues.append("❌ kline_data 表为空，没有任何K线数据")
            recommendations.append("启动数据采集器: python app/scheduler.py")
        elif kline_stats['minutes_since_last_update'] > 10:
            issues.append(f"⚠️  kline_data 表数据已过期 ({kline_stats['minutes_since_last_update']} 分钟)")

        # 检查币种覆盖
        expected_symbols = len(symbols)
        actual_symbols = price_stats['total_symbols']
        if actual_symbols < expected_symbols:
            issues.append(f"⚠️  配置了 {expected_symbols} 个币种，但只有 {actual_symbols} 个有价格数据")
            recommendations.append("检查 config.yaml 中的 symbols 配置")
            recommendations.append("某些币种可能只在特定交易所有交易对 (如 HYPE 只在 Gate.io)")

        # 检查 Gate.io 数据 (针对 HYPE)
        if 'HYPE/USDT' in symbols:
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM price_data
                WHERE symbol = 'HYPE/USDT' AND exchange = 'gate'
            """)
            hype_count = cursor.fetchone()['count']

            if hype_count == 0:
                issues.append("❌ HYPE/USDT 没有价格数据 (仅 Gate.io 支持)")
                recommendations.append("运行 Gate.io 采集器: python collect_gate_prices.py")

        # 输出结果
        if not issues:
            print("\n✅ 所有检查通过! 价格数据正常更新中")
        else:
            print("\n发现以下问题:\n")
            for i, issue in enumerate(issues, 1):
                print(f"  {i}. {issue}")

            print("\n💡 建议解决方案:\n")
            for i, rec in enumerate(recommendations, 1):
                print(f"  {i}. {rec}")

        # ============================================================
        # 5. 采集器运行检查
        # ============================================================
        print("\n" + "=" * 100)
        print("🚀 5. 采集器运行状态检查")
        print("=" * 100)

        import os
        import subprocess

        print("\n检查采集器进程...")
        if os.name == 'nt':  # Windows
            try:
                result = subprocess.run(['tasklist'], capture_output=True, text=True)
                python_processes = [line for line in result.stdout.split('\n') if 'python' in line.lower()]
                if python_processes:
                    print("✅ 发现 Python 进程:")
                    for proc in python_processes[:5]:
                        print(f"   {proc.strip()}")
                else:
                    print("❌ 没有发现 Python 进程")
                    print("   请启动数据采集器: python app/scheduler.py")
            except Exception as e:
                print(f"⚠️  无法检查进程: {e}")
        else:  # Linux/Mac
            try:
                result = subprocess.run(['ps', 'aux'], capture_output=True, text=True)
                scheduler_processes = [line for line in result.stdout.split('\n') if 'scheduler' in line or 'collector' in line]
                if scheduler_processes:
                    print("✅ 发现采集器进程:")
                    for proc in scheduler_processes[:5]:
                        print(f"   {proc.strip()}")
                else:
                    print("❌ 没有发现采集器进程")
                    print("   请启动数据采集器: python3 app/scheduler.py")
            except Exception as e:
                print(f"⚠️  无法检查进程: {e}")

        # ============================================================
        # 总结
        # ============================================================
        print("\n" + "=" * 100)
        print("📝 诊断完成")
        print("=" * 100)

        if not issues:
            print("\n✅ 系统运行正常，价格数据实时更新中")
        else:
            print(f"\n⚠️  发现 {len(issues)} 个问题，请按照建议进行修复")

        print()

    except Exception as e:
        print(f"\n❌ 诊断过程出错: {e}")
        import traceback
        traceback.print_exc()

    finally:
        conn.close()

if __name__ == '__main__':
    main()
