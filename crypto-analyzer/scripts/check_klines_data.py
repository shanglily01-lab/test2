"""
检查数据库中的 K线数据情况
Check K-line data in database
"""

import mysql.connector
import yaml
from pathlib import Path
from datetime import datetime, timedelta

def load_config():
    """加载配置文件"""
    config_path = Path(__file__).parent.parent / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def check_klines_data():
    """检查 K线数据"""
    print("=" * 80)
    print("📊 K线数据检查工具")
    print("=" * 80)

    # 加载配置
    config = load_config()
    mysql_config = config.get('database', {}).get('mysql', {})
    symbols = config.get('symbols', [])

    # 连接数据库
    try:
        conn = mysql.connector.connect(
            host=mysql_config.get('host', 'localhost'),
            port=mysql_config.get('port', 3306),
            user=mysql_config.get('user', 'root'),
            password=mysql_config.get('password', ''),
            database=mysql_config.get('database', 'binance-data')
        )
        cursor = conn.cursor(dictionary=True)

        print(f"\n✅ 数据库连接成功")
        print(f"   主机: {mysql_config.get('host', 'localhost')}")
        print(f"   数据库: {mysql_config.get('database', 'binance-data')}\n")

        # 列出所有表
        print("=" * 80)
        print("📋 数据库中的所有表")
        print("=" * 80 + "\n")

        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()

        if tables:
            print(f"找到 {len(tables)} 个表:\n")
            for table in tables:
                table_name = list(table.values())[0]

                # 获取表的记录数
                cursor.execute(f"SELECT COUNT(*) as count FROM `{table_name}`")
                count = cursor.fetchone()['count']

                print(f"   📊 {table_name:<40} {count:>10,} 条记录")
        else:
            print("⚠️  数据库中没有任何表\n")

    except mysql.connector.Error as e:
        print(f"\n❌ 数据库连接失败: {e}")
        print(f"\n💡 提示: 如果数据库在 Windows 本地，请确保:")
        print(f"   1. MySQL 服务正在运行")
        print(f"   2. config.yaml 中的 host 配置正确")
        print(f"   3. 用户名和密码正确\n")
        return

    # 检查 kline_data 通用表（新版数据库结构）
    cursor.execute("SHOW TABLES LIKE 'kline_data'")
    if cursor.fetchone():
        print("\n" + "=" * 80)
        print("📈 K线数据统计 (kline_data 表)")
        print("=" * 80 + "\n")

        # 按时间周期统计
        cursor.execute("""
            SELECT
                timeframe,
                COUNT(*) as total,
                COUNT(DISTINCT symbol) as symbol_count,
                MIN(timestamp) as earliest,
                MAX(timestamp) as latest
            FROM kline_data
            GROUP BY timeframe
            ORDER BY
                CASE timeframe
                    WHEN '1m' THEN 1
                    WHEN '5m' THEN 2
                    WHEN '15m' THEN 3
                    WHEN '1h' THEN 4
                    WHEN '4h' THEN 5
                    WHEN '1d' THEN 6
                    ELSE 7
                END
        """)

        timeframe_stats = cursor.fetchall()

        for stat in timeframe_stats:
            tf = stat['timeframe']
            total = stat['total']
            symbol_count = stat['symbol_count']
            earliest = stat['earliest']
            latest = stat['latest']

            print(f"⏰ {tf:10s} | 总记录: {total:,}".ljust(50) + f"| 币种数: {symbol_count}")
            if earliest and latest:
                print(f"   {'':10s} | 最早: {earliest}")
                print(f"   {'':10s} | 最新: {latest}")
            print()

    else:
        # 检查分表结构（旧版）
        timeframes = ['1m', '5m', '15m', '1h', '4h', '1d']

        print("\n" + "=" * 80)
        print("📈 各时间周期 K线数据统计")
        print("=" * 80 + "\n")

        summary = {}

        for timeframe in timeframes:
            table_name = f"klines_{timeframe}"

            # 检查表是否存在
            cursor.execute(f"SHOW TABLES LIKE '{table_name}'")
            if not cursor.fetchone():
                print(f"⚠️  表 {table_name} 不存在")
                continue

            # 获取总记录数
            cursor.execute(f"SELECT COUNT(*) as total FROM {table_name}")
            total = cursor.fetchone()['total']

            # 获取币种数量
            cursor.execute(f"SELECT COUNT(DISTINCT symbol) as symbol_count FROM {table_name}")
            symbol_count = cursor.fetchone()['symbol_count']

            # 获取时间范围
            cursor.execute(f"""
                SELECT
                    MIN(timestamp) as earliest,
                    MAX(timestamp) as latest
                FROM {table_name}
            """)
            time_range = cursor.fetchone()

            summary[timeframe] = {
                'total': total,
                'symbol_count': symbol_count,
                'earliest': time_range['earliest'],
                'latest': time_range['latest']
            }

            print(f"⏰ {table_name:15s} | 总记录: {total:,}".ljust(50) + f"| 币种数: {symbol_count}")
            if time_range['earliest'] and time_range['latest']:
                print(f"   {'':15s} | 最早: {time_range['earliest']}")
                print(f"   {'':15s} | 最新: {time_range['latest']}")
            print()

    # 检查各币种的数据情况
    print("\n" + "=" * 80)
    print("💰 各币种 K线数据详情 (1小时周期)")
    print("=" * 80 + "\n")

    # 检查是否使用 kline_data 通用表
    cursor.execute("SHOW TABLES LIKE 'kline_data'")
    use_kline_data = cursor.fetchone() is not None

    if use_kline_data:
        print(f"{'币种':<15} {'记录数':<12} {'最新时间':<20} {'数据天数':<10} {'状态'}")
        print("-" * 80)

        insufficient_symbols = []

        for symbol in symbols:
            # 去掉 /USDT 后缀
            symbol_clean = symbol.replace('/USDT', '').replace('/', '')

            # 获取该币种的数据统计（从 kline_data 表查询 1小时数据）
            cursor.execute("""
                SELECT
                    COUNT(*) as count,
                    MAX(timestamp) as latest,
                    MIN(timestamp) as earliest
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1h'
            """, (symbol_clean,))

            result = cursor.fetchone()

            if result and result['count'] > 0:
                count = result['count']
                latest = result['latest']
                earliest = result['earliest']

                # 计算数据天数
                if earliest and latest:
                    days = (latest - earliest).days
                else:
                    days = 0

                # 判断是否足够 (至少需要 31 条记录用于 EMA 计算)
                status = "✅ 充足" if count >= 31 else "⚠️  不足"

                if count < 31:
                    insufficient_symbols.append(symbol)

                print(f"{symbol:<15} {count:<12,} {str(latest):<20} {days:<10} {status}")
            else:
                insufficient_symbols.append(symbol)
                print(f"{symbol:<15} {'0':<12} {'-':<20} {'-':<10} ❌ 无数据")

        # 总结
        print("\n" + "=" * 80)
        print("📊 数据质量总结")
        print("=" * 80 + "\n")

        total_symbols = len(symbols)
        ok_symbols = total_symbols - len(insufficient_symbols)

        print(f"✅ 数据充足的币种: {ok_symbols}/{total_symbols}")
        print(f"⚠️  数据不足的币种: {len(insufficient_symbols)}/{total_symbols}")

        if insufficient_symbols:
            print(f"\n数据不足的币种列表:")
            for sym in insufficient_symbols:
                print(f"   - {sym}")
            print(f"\n💡 提示: 这些币种需要至少 31 条 K线数据才能进行 EMA 技术分析")
        else:
            print(f"\n🎉 所有币种的 K线数据都充足！")

        # 检查数据是否最新
        print("\n" + "-" * 80)
        print("🕐 数据新鲜度检查 (1小时周期)")
        print("-" * 80 + "\n")

        cursor.execute("""
            SELECT
                symbol,
                MAX(timestamp) as latest
            FROM kline_data
            WHERE timeframe = '1h'
            GROUP BY symbol
            ORDER BY latest DESC
            LIMIT 5
        """)

        results = cursor.fetchall()
        now = datetime.now()

        for result in results:
            symbol = result['symbol']
            latest = result['latest']

            if latest:
                age = now - latest
                hours_ago = age.total_seconds() / 3600

                if hours_ago < 2:
                    status = "✅ 最新"
                elif hours_ago < 24:
                    status = f"⚠️  {hours_ago:.1f}小时前"
                else:
                    status = f"❌ {age.days}天前"

                print(f"   {symbol:<15} 最新数据: {latest}  ({status})")

    else:
        # 使用旧版分表结构
        table_name = "klines_1h"
        cursor.execute(f"SHOW TABLES LIKE '{table_name}'")

        if cursor.fetchone():
            print(f"{'币种':<15} {'记录数':<12} {'最新时间':<20} {'数据天数':<10} {'状态'}")
            print("-" * 80)

            insufficient_symbols = []

            for symbol in symbols:
                # 去掉 /USDT 后缀
                symbol_clean = symbol.replace('/USDT', '').replace('/', '')

                # 获取该币种的数据统计
                cursor.execute(f"""
                    SELECT
                        COUNT(*) as count,
                        MAX(timestamp) as latest,
                        MIN(timestamp) as earliest
                    FROM {table_name}
                    WHERE symbol = %s
                """, (symbol_clean,))

                result = cursor.fetchone()

                if result and result['count'] > 0:
                    count = result['count']
                    latest = result['latest']
                    earliest = result['earliest']

                    # 计算数据天数
                    if earliest and latest:
                        days = (latest - earliest).days
                    else:
                        days = 0

                    # 判断是否足够 (至少需要 31 条记录用于 EMA 计算)
                    status = "✅ 充足" if count >= 31 else "⚠️  不足"

                    if count < 31:
                        insufficient_symbols.append(symbol)

                    print(f"{symbol:<15} {count:<12,} {str(latest):<20} {days:<10} {status}")
                else:
                    insufficient_symbols.append(symbol)
                    print(f"{symbol:<15} {'0':<12} {'-':<20} {'-':<10} ❌ 无数据")

            # 总结
            print("\n" + "=" * 80)
            print("📊 数据质量总结")
            print("=" * 80 + "\n")

            total_symbols = len(symbols)
            ok_symbols = total_symbols - len(insufficient_symbols)

            print(f"✅ 数据充足的币种: {ok_symbols}/{total_symbols}")
            print(f"⚠️  数据不足的币种: {len(insufficient_symbols)}/{total_symbols}")

            if insufficient_symbols:
                print(f"\n数据不足的币种列表:")
                for sym in insufficient_symbols:
                    print(f"   - {sym}")
                print(f"\n💡 提示: 这些币种需要至少 31 条 K线数据才能进行 EMA 技术分析")
            else:
                print(f"\n🎉 所有币种的 K线数据都充足！")

            # 检查数据是否最新
            print("\n" + "-" * 80)
            print("🕐 数据新鲜度检查")
            print("-" * 80 + "\n")

            cursor.execute(f"""
                SELECT
                    symbol,
                    MAX(timestamp) as latest
                FROM {table_name}
                GROUP BY symbol
                ORDER BY latest DESC
                LIMIT 5
            """)

            results = cursor.fetchall()
            now = datetime.now()

            for result in results:
                symbol = result['symbol']
                latest = result['latest']

                if latest:
                    age = now - latest
                    hours_ago = age.total_seconds() / 3600

                    if hours_ago < 2:
                        status = "✅ 最新"
                    elif hours_ago < 24:
                        status = f"⚠️  {hours_ago:.1f}小时前"
                    else:
                        status = f"❌ {age.days}天前"

                    print(f"   {symbol:<15} 最新数据: {latest}  ({status})")

        else:
            print(f"❌ 表 kline_data 和 klines_1h 都不存在\n")
            print(f"💡 提示: 请先运行数据采集器填充 K线数据")

    cursor.close()
    conn.close()

    print("\n" + "=" * 80)
    print("检查完成!")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    try:
        check_klines_data()
    except Exception as e:
        print(f"\n❌ 检查过程出错: {e}")
        import traceback
        traceback.print_exc()
