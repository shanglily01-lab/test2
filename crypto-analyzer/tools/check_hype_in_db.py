"""
检查数据库中的 HYPE 价格数据
"""

import pymysql
import yaml
from datetime import datetime, timedelta

print("=" * 80)
print("  检查数据库中的 HYPE/USDT 价格数据")
print("=" * 80)
print()

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config['database']['mysql']

# 连接数据库
conn = pymysql.connect(
    host=db_config['host'],
    port=db_config['port'],
    user=db_config['user'],
    password=db_config['password'],
    database=db_config['database'],
    cursorclass=pymysql.cursors.DictCursor
)

try:
    with conn.cursor() as cursor:
        # 1. 检查 price_data 表中的 HYPE 数据
        print("1. 检查实时价格表 (price_data)...")
        print("-" * 80)

        cursor.execute("""
            SELECT symbol, exchange, price, change_24h, volume, timestamp
            FROM price_data
            WHERE symbol = 'HYPE/USDT'
            ORDER BY timestamp DESC
            LIMIT 10
        """)

        tickers = cursor.fetchall()

        if tickers:
            print(f"✅ 找到 {len(tickers)} 条 HYPE/USDT 实时价格记录")
            print()
            print("最近10条记录:")
            for i, t in enumerate(tickers, 1):
                print(f"  {i}. [{t['exchange']}] ${t['price']:,.4f} "
                      f"(24h: {t['change_24h']:+.2f}%) "
                      f"- {t['timestamp']}")

            # 检查最新记录的时间
            latest = tickers[0]
            time_diff = datetime.now() - latest['timestamp']
            print()
            print(f"最新记录时间: {latest['timestamp']}")
            print(f"距离现在: {time_diff}")

            if time_diff.total_seconds() > 300:  # 超过5分钟
                print(f"⚠️  警告: 最新数据超过 5 分钟，可能调度器未运行")
        else:
            print("❌ price_data 表中没有 HYPE/USDT 数据")
            print()
            print("可能的原因:")
            print("1. 调度器从未运行过")
            print("2. 采集时出错但没有保存数据")

        print()
        print("2. 检查 K线数据表 (kline_data)...")
        print("-" * 80)

        cursor.execute("""
            SELECT symbol, exchange, timeframe, close_price as price, timestamp
            FROM kline_data
            WHERE symbol = 'HYPE/USDT'
            ORDER BY timestamp DESC
            LIMIT 5
        """)

        klines = cursor.fetchall()

        if klines:
            print(f"✅ 找到 {len(klines)} 条 HYPE/USDT K线记录")
            print()
            for i, k in enumerate(klines, 1):
                print(f"  {i}. [{k['exchange']}] {k['timeframe']} "
                      f"收盘价: ${k['price']:,.4f} - {k['timestamp']}")
        else:
            print("❌ kline_data 表中没有 HYPE/USDT 数据")
            print()
            print("说明: K线数据目前只从 Binance 采集")
            print("      Binance 没有 HYPE/USDT，所以不会有 K线数据")
            print("      这是正常的！")

        print()
        print("3. 统计所有交易对的数据...")
        print("-" * 80)

        cursor.execute("""
            SELECT symbol, exchange, COUNT(*) as count, MAX(timestamp) as latest
            FROM price_data
            GROUP BY symbol, exchange
            ORDER BY latest DESC
            LIMIT 20
        """)

        stats = cursor.fetchall()

        print(f"数据库中有 {len(stats)} 个交易对 x 交易所组合:")
        print()

        hype_found = False
        for s in stats:
            symbol_str = f"{s['symbol']} [{s['exchange']}]"
            if 'HYPE' in s['symbol']:
                print(f"  ✅ {symbol_str:30} {s['count']:6} 条记录, 最新: {s['latest']}")
                hype_found = True
            else:
                print(f"     {symbol_str:30} {s['count']:6} 条记录, 最新: {s['latest']}")

        if not hype_found:
            print()
            print("⚠️  HYPE/USDT 不在前20个交易对中，或没有数据")

finally:
    conn.close()

print()
print("=" * 80)
print("检查完成")
print("=" * 80)
print()

print("💡 结论:")
print()
print("如果 price_data 表中:")
print("  ✅ 有 HYPE 数据 → 说明采集正常，前端显示问题可能在缓存或查询逻辑")
print("  ❌ 没有 HYPE 数据 → 说明调度器没有保存数据，需要启动/重启调度器")
print()
print("建议操作:")
print("  1. 启动/重启调度器: python app/scheduler.py")
print("  2. 等待 1-2 分钟后再次检查")
print("  3. 刷新前端页面")
