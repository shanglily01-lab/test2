"""
EMA 信号诊断脚本
用于诊断为什么没有捕捉到 EMA 信号

运行方式：
    python diagnose_ema_signals.py
"""

import asyncio
import yaml
from datetime import datetime, timezone, timedelta
from loguru import logger
from sqlalchemy import text

# 配置日志
logger.remove()
logger.add(lambda msg: print(msg), level="INFO")


async def diagnose_ema_signals():
    """诊断 EMA 信号系统"""

    print("\n" + "="*60)
    print("EMA 信号系统诊断")
    print("="*60 + "\n")

    # 1. 加载配置
    print("📋 步骤 1/6: 检查配置文件")
    print("-" * 60)
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        ema_config = config.get('ema_signal', {})
        enabled = ema_config.get('enabled', False)
        short_period = ema_config.get('short_period', 9)
        long_period = ema_config.get('long_period', 21)
        timeframe = ema_config.get('timeframe', '15m')
        volume_threshold = ema_config.get('volume_threshold', 1.5)

        print(f"✓ 配置文件加载成功")
        print(f"  - EMA 监控启用: {'是' if enabled else '否 ⚠️'}")
        print(f"  - 短期 EMA: {short_period}")
        print(f"  - 长期 EMA: {long_period}")
        print(f"  - 时间周期: {timeframe}")
        print(f"  - 成交量阈值: {volume_threshold}x")

        if not enabled:
            print("\n❌ EMA 监控未启用！请在 config.yaml 中设置 ema_signal.enabled = true")
            return

    except Exception as e:
        print(f"❌ 加载配置失败: {e}")
        return

    # 2. 连接数据库
    print("\n📊 步骤 2/6: 检查数据库连接")
    print("-" * 60)
    try:
        from app.database.db_service import DatabaseService

        db_config = config.get('database', {})
        db_service = DatabaseService(db_config)
        session = db_service.get_session()

        print(f"✓ 数据库连接成功")
        print(f"  - 类型: {db_config.get('type', 'unknown')}")
        print(f"  - 数据库: {db_config.get('mysql', {}).get('database', 'unknown')}")

    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return

    # 3. 检查 K线数据
    print("\n📈 步骤 3/6: 检查 15分钟 K线数据")
    print("-" * 60)

    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])[:5]  # 只检查前5个

    kline_status = {}
    for symbol in symbols:
        try:
            query = text("""
                SELECT COUNT(*) as count,
                       MAX(open_time) as latest_time,
                       MIN(open_time) as earliest_time
                FROM kline_data
                WHERE symbol = :symbol
                AND timeframe = :timeframe
                AND exchange = 'binance'
            """)

            result = session.execute(query, {
                'symbol': symbol,
                'timeframe': timeframe
            }).fetchone()

            count = result.count if result else 0
            latest_time = result.latest_time if result else None
            earliest_time = result.earliest_time if result else None

            # 计算数据时间范围
            if latest_time and earliest_time:
                latest_dt = datetime.fromtimestamp(latest_time / 1000, tz=timezone.utc)
                earliest_dt = datetime.fromtimestamp(earliest_time / 1000, tz=timezone.utc)
                time_range = (latest_dt - earliest_dt).total_seconds() / 3600  # 小时

                # 检查数据是否足够新
                now = datetime.now(timezone.utc)
                age_minutes = (now - latest_dt).total_seconds() / 60

                is_fresh = age_minutes < 30  # 30分钟内的数据算新鲜
                is_enough = count >= (long_period + 10)

                status = "✓" if (is_fresh and is_enough) else "⚠️"

                kline_status[symbol] = {
                    'count': count,
                    'is_fresh': is_fresh,
                    'is_enough': is_enough,
                    'age_minutes': age_minutes
                }

                print(f"{status} {symbol}:")
                print(f"    数据量: {count} 条 (需要至少 {long_period + 10} 条)")
                print(f"    时间范围: {time_range:.1f} 小时")
                print(f"    最新数据: {age_minutes:.1f} 分钟前")

                if not is_fresh:
                    print(f"    ⚠️ 数据不够新鲜！最新数据已经 {age_minutes:.1f} 分钟了")
                if not is_enough:
                    print(f"    ⚠️ 数据量不足！至少需要 {long_period + 10} 条")
            else:
                print(f"❌ {symbol}: 没有 K线数据")
                kline_status[symbol] = {'count': 0, 'is_fresh': False, 'is_enough': False}

        except Exception as e:
            print(f"❌ {symbol}: 查询失败 - {e}")
            kline_status[symbol] = {'count': 0, 'is_fresh': False, 'is_enough': False}

    # 4. 检查 EMA 信号历史
    print("\n🔔 步骤 4/6: 检查历史 EMA 信号")
    print("-" * 60)

    try:
        # 检查 ema_signals 表是否存在
        check_table = text("""
            SELECT COUNT(*) as count
            FROM information_schema.tables
            WHERE table_schema = DATABASE()
            AND table_name = 'ema_signals'
        """)
        table_exists = session.execute(check_table).fetchone().count > 0

        if table_exists:
            # 查询最近的信号
            query = text("""
                SELECT symbol, signal_type, signal_strength, timestamp, price
                FROM ema_signals
                WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                ORDER BY timestamp DESC
                LIMIT 10
            """)

            signals = session.execute(query).fetchall()

            if signals:
                print(f"✓ 找到 {len(signals)} 个最近7天的信号:")
                for sig in signals[:5]:  # 只显示前5个
                    print(f"  - {sig.symbol} {sig.signal_type} ({sig.signal_strength}) - {sig.timestamp}")
            else:
                print("⚠️ 最近7天没有任何 EMA 信号记录")
                print("   可能原因:")
                print("   1. 市场没有出现金叉/死叉")
                print("   2. 数据采集有问题")
                print("   3. scheduler 没有正常运行")
        else:
            print("⚠️ ema_signals 表不存在")
            print("   提示: 第一次运行时会自动创建")

    except Exception as e:
        print(f"⚠️ 查询 EMA 信号历史失败: {e}")

    # 5. 实时测试 EMA 检测
    print("\n🔍 步骤 5/6: 实时测试 EMA 信号检测")
    print("-" * 60)

    try:
        from app.trading.ema_signal_monitor import EMASignalMonitor

        ema_monitor = EMASignalMonitor(config, db_service)

        # 测试前3个有数据的币对
        test_symbols = [s for s, status in kline_status.items()
                       if status.get('is_enough', False)][:3]

        if not test_symbols:
            print("❌ 没有足够数据的币对可以测试")
        else:
            print(f"正在测试 {len(test_symbols)} 个币对...")

            found_signals = []
            for symbol in test_symbols:
                signal = await ema_monitor.check_symbol(symbol)
                if signal:
                    found_signals.append(signal)
                    print(f"🎯 {symbol}: 发现 {signal['signal_type']} 信号 ({signal['signal_strength']})")
                else:
                    print(f"   {symbol}: 无信号")

            if found_signals:
                print(f"\n✓ 发现 {len(found_signals)} 个信号！")
            else:
                print(f"\n⚠️ 当前没有信号")
                print("   这可能是正常的，说明:")
                print("   1. 当前市场没有金叉/死叉出现")
                print("   2. 所有币对的 EMA 都未交叉")

    except Exception as e:
        print(f"❌ EMA 检测测试失败: {e}")
        import traceback
        traceback.print_exc()

    # 6. 诊断总结
    print("\n📝 步骤 6/6: 诊断总结")
    print("=" * 60)

    # 统计问题
    issues = []

    if not enabled:
        issues.append("EMA 监控未启用")

    fresh_data_count = sum(1 for s in kline_status.values() if s.get('is_fresh', False))
    enough_data_count = sum(1 for s in kline_status.values() if s.get('is_enough', False))

    if fresh_data_count == 0:
        issues.append("所有币对的 K线数据都不新鲜（可能 scheduler 没运行）")
    elif fresh_data_count < len(kline_status):
        issues.append(f"部分币对数据不新鲜 ({fresh_data_count}/{len(kline_status)})")

    if enough_data_count == 0:
        issues.append("所有币对的 K线数据都不足")
    elif enough_data_count < len(kline_status):
        issues.append(f"部分币对数据不足 ({enough_data_count}/{len(kline_status)})")

    if issues:
        print("\n❌ 发现以下问题:\n")
        for i, issue in enumerate(issues, 1):
            print(f"   {i}. {issue}")

        print("\n💡 建议解决方案:\n")

        if not enabled:
            print("   1. 编辑 config.yaml，设置 ema_signal.enabled = true")

        if fresh_data_count < len(kline_status):
            print("   2. 确保 scheduler.py 正在运行:")
            print("      python app/scheduler.py")
            print("      (应该每15分钟看到 '开始扫描 EMA 买入信号...' 日志)")

        if enough_data_count < len(kline_status):
            print("   3. 等待数据积累，或运行回填脚本:")
            print("      python scripts/backfill_kline_data.py")
    else:
        print("\n✅ 系统配置正常！")
        print("\n如果仍然没有信号，可能是因为:")
        print("   1. 当前市场确实没有金叉/死叉出现")
        print("   2. 上一个信号在1小时内已提醒过（防重复机制）")
        print("   3. 可以尝试降低成交量阈值 (当前 {volume_threshold}x)")
        print(f"      修改 config.yaml: ema_signal.volume_threshold = 1.2")

    print("\n" + "="*60)
    print("诊断完成")
    print("="*60 + "\n")

    session.close()


if __name__ == '__main__':
    asyncio.run(diagnose_ema_signals())
