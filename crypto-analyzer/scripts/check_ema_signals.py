#!/usr/bin/env python3
"""
EMA信号监控状态检查工具

使用方法:
    python scripts/check_ema_signals.py
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import yaml
import pymysql
from datetime import datetime, timedelta

def main():
    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config['database']['mysql']
    ema_config = config.get('ema_signal', {})

    print("=" * 80)
    print("EMA 信号监控状态检查")
    print("=" * 80)
    print()

    # 1. 检查配置
    print("1️⃣  配置检查")
    print("-" * 80)
    print(f"EMA监控启用状态: {ema_config.get('enabled', False)}")
    print(f"短期EMA周期: {ema_config.get('short_period', 9)}")
    print(f"长期EMA周期: {ema_config.get('long_period', 21)}")
    print(f"时间周期: {ema_config.get('timeframe', '15m')}")
    print(f"成交量阈值: {ema_config.get('volume_threshold', 1.5)}")
    print()

    # 2. 连接数据库
    try:
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database']
        )
        cursor = conn.cursor()
        print("✅ 数据库连接成功")
        print()
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return

    # 3. 检查ema_signals表
    print("2️⃣  数据库表检查")
    print("-" * 80)

    cursor.execute("SHOW TABLES LIKE 'ema_signals'")
    if not cursor.fetchone():
        print("❌ ema_signals 表不存在！")
        print()
        print("解决方案:")
        print("  1. 运行数据库迁移脚本创建表")
        print("  2. 或者启动scheduler.py，它会自动创建表")
        conn.close()
        return

    print("✅ ema_signals 表存在")
    print()

    # 4. 统计信号数据
    print("3️⃣  信号数据统计")
    print("-" * 80)

    cursor.execute("SELECT COUNT(*) FROM ema_signals")
    total_signals = cursor.fetchone()[0]
    print(f"信号总数: {total_signals}")

    if total_signals == 0:
        print("⚠️  没有任何信号记录")
        print()
        print("可能原因:")
        print("  1. scheduler.py 未运行")
        print("  2. EMA监控任务未执行")
        print("  3. 没有符合条件的交叉信号")
        print()
        print("建议:")
        print("  运行测试脚本: python scripts/test_ema_scan.py")
        conn.close()
        return

    # 最近24小时的信号
    cursor.execute("""
        SELECT COUNT(*) FROM ema_signals
        WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
    """)
    signals_24h = cursor.fetchone()[0]
    print(f"最近24小时信号: {signals_24h}")

    # 按信号类型统计
    cursor.execute("""
        SELECT signal_type, COUNT(*)
        FROM ema_signals
        GROUP BY signal_type
    """)
    print()
    print("信号类型分布:")
    for signal_type, count in cursor.fetchall():
        print(f"  {signal_type:15} {count:5} 条")

    # 按交易对统计
    cursor.execute("""
        SELECT symbol, COUNT(*) as cnt
        FROM ema_signals
        GROUP BY symbol
        ORDER BY cnt DESC
        LIMIT 10
    """)
    print()
    print("各交易对信号数 (Top 10):")
    for symbol, count in cursor.fetchall():
        print(f"  {symbol:15} {count:5} 条")

    print()

    # 5. 最近的信号
    print("4️⃣  最近的信号 (最多显示10条)")
    print("-" * 80)

    cursor.execute("""
        SELECT symbol, signal_type, timestamp, short_ema, long_ema, volume_ratio
        FROM ema_signals
        ORDER BY timestamp DESC
        LIMIT 10
    """)

    signals = cursor.fetchall()
    if signals:
        print(f"{'时间':<20} {'交易对':<15} {'信号':<10} {'短期EMA':<12} {'长期EMA':<12} {'成交量比'}")
        print("-" * 90)
        for row in signals:
            timestamp, symbol, signal_type = row[2], row[0], row[1]
            short_ema, long_ema, volume_ratio = row[3], row[4], row[5]

            # 格式化时间
            time_str = timestamp.strftime('%Y-%m-%d %H:%M:%S')

            print(f"{time_str:<20} {symbol:<15} {signal_type:<10} {short_ema:<12.2f} {long_ema:<12.2f} {volume_ratio or 0:.2f}x")
    else:
        print("  没有信号")

    print()

    # 6. 检查K线数据
    print("5️⃣  K线数据检查")
    print("-" * 80)

    timeframe = ema_config.get('timeframe', '15m')
    cursor.execute(f"""
        SELECT COUNT(DISTINCT symbol) as symbol_count,
               COUNT(*) as kline_count,
               MAX(timestamp) as latest_time
        FROM klines
        WHERE timeframe = '{timeframe}'
    """)

    result = cursor.fetchone()
    if result:
        symbol_count, kline_count, latest_time = result
        print(f"时间周期: {timeframe}")
        print(f"交易对数量: {symbol_count}")
        print(f"K线记录总数: {kline_count}")
        if latest_time:
            print(f"最新K线时间: {latest_time}")

            # 检查数据是否实时
            now = datetime.now()
            time_diff = now - latest_time
            if time_diff.total_seconds() > 3600:  # 超过1小时
                print(f"⚠️  数据不是实时的！最后更新: {time_diff}")
                print("   建议检查 scheduler.py 是否在运行")
            else:
                print(f"✅ 数据是实时的 (最后更新: {time_diff.seconds // 60} 分钟前)")
    else:
        print(f"❌ 没有 {timeframe} K线数据")
        print("   建议检查数据采集是否正常")

    print()
    print("=" * 80)

    # 7. 建议
    print()
    print("💡 建议:")
    if total_signals == 0:
        print("  1. 确认 scheduler.py 正在运行")
        print("  2. 运行测试脚本手动扫描一次: python scripts/test_ema_scan.py")
        print("  3. 查看 scheduler 日志检查错误信息")
    else:
        print("  ✅ EMA信号监控正常工作")
        if signals_24h == 0:
            print("  ℹ️  最近24小时没有新信号（市场可能没有EMA交叉）")

    conn.close()


if __name__ == '__main__':
    main()
