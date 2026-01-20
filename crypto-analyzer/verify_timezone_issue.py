"""
验证时区问题的临时脚本
"""
import pymysql
from datetime import datetime, timezone
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

conn = pymysql.connect(
    host='13.212.252.171',
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4'
)
cursor = conn.cursor()

print("=" * 100)
print("时区问题诊断")
print("=" * 100)

# 1. 检查MySQL时区
print("\n1. MySQL 服务器时区设置")
print("-" * 100)
cursor.execute("SELECT NOW(), UTC_TIMESTAMP(), @@global.time_zone, @@session.time_zone")
mysql_now, mysql_utc, global_tz, session_tz = cursor.fetchone()
print(f"MySQL NOW():            {mysql_now}")
print(f"MySQL UTC_TIMESTAMP():  {mysql_utc}")
print(f"Global 时区:            {global_tz}")
print(f"Session 时区:           {session_tz}")

if mysql_now != mysql_utc:
    diff_hours = (mysql_now - mysql_utc).total_seconds() / 3600
    print(f"⚠️  MySQL时区差异:      {diff_hours:+.1f} 小时")
else:
    print(f"✅ MySQL运行在UTC+0时区")

# 2. 检查Python时间
print("\n2. Python 运行环境时区")
print("-" * 100)
python_local = datetime.now()
python_utc = datetime.now(timezone.utc).replace(tzinfo=None)
print(f"Python 本地时间:        {python_local}")
print(f"Python UTC时间:         {python_utc}")

time_diff = (python_local - python_utc).total_seconds() / 3600
if abs(time_diff) > 0.1:
    print(f"⚠️  Python时区差异:     {time_diff:+.1f} 小时")
else:
    print(f"✅ Python运行在UTC+0时区")

# 3. 检查数据库最新数据 - 使用NOW()
print("\n3. 数据库数据新鲜度检查 - 使用NOW()")
print("-" * 100)
cursor.execute("""
    SELECT
        exchange,
        MAX(timestamp) as latest,
        TIMESTAMPDIFF(SECOND, MAX(timestamp), NOW()) as delay_now,
        TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) as delay_utc
    FROM price_data
    WHERE timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 12 HOUR)
    GROUP BY exchange
    ORDER BY delay_utc ASC
""")

results = cursor.fetchall()
for exchange, latest, delay_now, delay_utc in results:
    print(f"\n{exchange}:")
    print(f"  最新时间戳:           {latest}")
    print(f"  延迟(NOW):            {delay_now}秒 = {delay_now/60:.1f}分钟")
    print(f"  延迟(UTC_TIMESTAMP):  {delay_utc}秒 = {delay_utc/60:.1f}分钟")

    diff = abs(delay_now - delay_utc)
    if diff > 3600:  # 超过1小时差异
        print(f"  ⚠️  时区差异:         {diff/3600:.1f}小时 - 这就是DATA_STALE警告的原因!")

        if delay_utc < 60:
            print(f"  ✅ 真实延迟:          <1分钟，数据是新鲜的")
        else:
            print(f"  ❌ 真实延迟:          {delay_utc/60:.1f}分钟，数据确实过时")
    else:
        if delay_utc < 60:
            print(f"  ✅ 数据新鲜")
        else:
            print(f"  ⚠️  数据延迟:         {delay_utc/60:.1f}分钟")

# 4. 检查K线数据
print("\n4. K线数据新鲜度检查")
print("-" * 100)
cursor.execute("""
    SELECT
        exchange,
        COUNT(DISTINCT symbol) as symbol_count,
        MAX(timestamp) as latest,
        TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP()) as delay_utc
    FROM kline_data
    WHERE timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)
    GROUP BY exchange
""")

kline_results = cursor.fetchall()
if kline_results:
    for exchange, symbol_count, latest, delay_utc in kline_results:
        print(f"\n{exchange}:")
        print(f"  交易对数:             {symbol_count}")
        print(f"  最新K线时间:          {latest}")
        print(f"  延迟(UTC):            {delay_utc}秒 = {delay_utc/60:.1f}分钟")

        if delay_utc < 120:
            print(f"  ✅ K线数据新鲜")
        else:
            print(f"  ⚠️  K线数据延迟")
else:
    print("⚠️  最近1小时没有K线数据")

# 5. 检查最近10分钟的数据写入频率
print("\n5. 最近10分钟的数据写入频率")
print("-" * 100)
cursor.execute("""
    SELECT
        COUNT(*) as total_records,
        COUNT(DISTINCT symbol) as symbol_count,
        MIN(timestamp) as earliest,
        MAX(timestamp) as latest,
        TIMESTAMPDIFF(SECOND, MIN(timestamp), MAX(timestamp)) as time_span_sec
    FROM price_data
    WHERE exchange = 'binance_futures'
    AND timestamp >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 10 MINUTE)
""")

freq_result = cursor.fetchone()
if freq_result and freq_result[0] > 0:
    total, symbols, earliest, latest, span = freq_result
    print(f"总记录数:               {total}")
    print(f"交易对数:               {symbols}")
    print(f"时间跨度:               {span}秒 = {span/60:.1f}分钟")
    print(f"最早时间:               {earliest}")
    print(f"最新时间:               {latest}")

    if symbols > 0 and span > 0:
        # 计算实际采集频率
        records_per_symbol = total / symbols
        interval = span / records_per_symbol if records_per_symbol > 0 else 0
        print(f"平均采集间隔:           {interval:.1f}秒 (预期: 10秒)")

        if interval > 15:
            print(f"⚠️  采集间隔过长 ({interval:.1f}秒 > 15秒)")
        elif interval > 12:
            print(f"⚠️  采集间隔略长 ({interval:.1f}秒)")
        else:
            print(f"✅ 采集频率正常")

    # 检查最新数据延迟
    cursor.execute("""
        SELECT TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP())
        FROM price_data
        WHERE exchange = 'binance_futures'
    """)
    latest_delay = cursor.fetchone()[0]
    print(f"最新数据延迟:           {latest_delay}秒")

    if latest_delay < 15:
        print(f"✅ Scheduler正在正常写入数据")
    elif latest_delay < 60:
        print(f"⚠️  数据略有延迟")
    else:
        print(f"❌ Scheduler可能未运行或写入失败 (延迟{latest_delay}秒)")
else:
    print("❌ 最近10分钟没有任何数据!")
    print("可能原因:")
    print("  1. Scheduler服务未运行")
    print("  2. 数据库连接失败")
    print("  3. API采集失败")

# 6. 给出诊断结论
print("\n" + "=" * 100)
print("诊断结论")
print("=" * 100)

cursor.execute("""
    SELECT TIMESTAMPDIFF(SECOND, MAX(timestamp), UTC_TIMESTAMP())
    FROM price_data
    WHERE exchange = 'binance_futures'
""")
actual_delay = cursor.fetchone()[0] if cursor.rowcount > 0 else None

if actual_delay is not None:
    if actual_delay < 60:
        print("\n✅ 系统运行正常!")
        print(f"   - 数据延迟: {actual_delay}秒 (<1分钟)")
        print("   - Scheduler正在正常采集和写入数据")
        print("   - 之前的DATA_STALE警告是由于时区问题导致的误报")
        print("\n建议:")
        print("   1. 修复所有使用NOW()的查询,改为UTC_TIMESTAMP()")
        print("   2. 确保Python代码使用datetime.utcnow()而不是datetime.now()")
    elif actual_delay < 600:
        print(f"\n⚠️  数据有延迟 ({actual_delay}秒 = {actual_delay/60:.1f}分钟)")
        print("   建议检查:")
        print("   1. Scheduler日志是否有错误")
        print("   2. 网络连接是否正常")
        print("   3. API是否被限流")
    else:
        print(f"\n❌ 数据严重延迟 ({actual_delay}秒 = {actual_delay/60:.1f}分钟)")
        print("   可能原因:")
        print("   1. Scheduler服务已停止")
        print("   2. 数据库写入失败")
        print("   3. 所有API调用失败")
        print("\n建议立即:")
        print("   1. 检查scheduler进程: ps aux | grep scheduler.py")
        print("   2. 查看scheduler日志: tail -100 logs/scheduler.log")
        print("   3. 重启scheduler服务")
else:
    print("\n❌ 数据库中没有任何数据!")

cursor.close()
conn.close()

print("\n" + "=" * 100)
