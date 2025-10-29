"""
EMA信号状态检查脚本
检查EMA信号数据、配置和历史记录
"""

import sys
from pathlib import Path
import yaml
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus
from loguru import logger

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def load_config():
    """加载配置文件"""
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def get_db_connection(config):
    """获取数据库连接"""
    from urllib.parse import quote_plus

    # 按照 DatabaseService 的方式读取配置
    db_config = config.get('database', {})
    mysql_config = db_config.get('mysql', {})

    host = mysql_config.get('host', 'localhost')
    port = mysql_config.get('port', 3306)
    user = mysql_config.get('user', 'root')
    password = mysql_config.get('password', '')
    database = mysql_config.get('database', 'binance-data')

    # URL编码密码以处理特殊字符
    password_encoded = quote_plus(password)

    # 创建连接字符串
    db_uri = f"mysql+pymysql://{user}:{password_encoded}@{host}:{port}/{database}?charset=utf8mb4"

    return create_engine(
        db_uri,
        pool_pre_ping=True,
        echo=False
    )


def check_ema_config(config):
    """检查EMA配置"""
    logger.info("\n" + "="*80)
    logger.info("1️⃣  检查EMA配置")
    logger.info("="*80)

    ema_config = config.get('ema_signal', {})

    enabled = ema_config.get('enabled', True)
    short_period = ema_config.get('short_period', 9)
    long_period = ema_config.get('long_period', 21)
    timeframe = ema_config.get('timeframe', '15m')
    volume_threshold = ema_config.get('volume_threshold', 1.5)

    logger.info(f"  启用状态: {'✅ 已启用' if enabled else '❌ 已禁用'}")
    logger.info(f"  短期EMA: {short_period}")
    logger.info(f"  长期EMA: {long_period}")
    logger.info(f"  时间周期: {timeframe}")
    logger.info(f"  成交量阈值: {volume_threshold}x")

    if not enabled:
        logger.warning("\n  ⚠️  EMA信号监控未启用！")
        logger.info("  💡 在 config.yaml 中设置: ema_signal.enabled = true")
        return False

    return True


def check_kline_data(engine, config):
    """检查K线数据"""
    logger.info("\n" + "="*80)
    logger.info("2️⃣  检查K线数据")
    logger.info("="*80)

    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
    ema_config = config.get('ema_signal', {})
    timeframe = ema_config.get('timeframe', '15m')

    with engine.connect() as conn:
        try:
            # 检查K线表是否存在
            result = conn.execute(text("SHOW TABLES LIKE 'kline_data'"))
            if not result.fetchone():
                logger.error("  ❌ kline_data 表不存在！")
                return False

            logger.info(f"  ✅ kline_data 表存在")
            logger.info(f"\n  检查 {timeframe} K线数据:")

            for symbol in symbols[:10]:  # 只检查前10个
                try:
                    # 检查最近的K线数据
                    query = text("""
                        SELECT
                            COUNT(*) as count,
                            MAX(timestamp) as last_time,
                            MIN(timestamp) as first_time
                        FROM kline_data
                        WHERE symbol = :symbol
                        AND timeframe = :timeframe
                        AND timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                    """)

                    result = conn.execute(query, {
                        'symbol': symbol,
                        'timeframe': timeframe
                    })
                    row = result.fetchone()

                    if row and row[0] > 0:
                        count = row[0]
                        last_time = row[1]
                        first_time = row[2]

                        # 计算数据覆盖范围
                        if last_time and first_time:
                            days = (last_time - first_time).days
                            hours_old = (datetime.now() - last_time).total_seconds() / 3600

                            status = "✅" if hours_old < 1 else "⚠️"
                            logger.info(f"  {status} {symbol:15s} | 记录数: {count:4d} | "
                                      f"最新: {hours_old:.1f}小时前 | 覆盖: {days}天")
                        else:
                            logger.info(f"  ✅ {symbol:15s} | 记录数: {count:4d}")
                    else:
                        logger.warning(f"  ❌ {symbol:15s} | 无数据")

                except Exception as e:
                    logger.error(f"  ❌ {symbol:15s} | 查询失败: {e}")

        except Exception as e:
            logger.error(f"  ❌ 检查K线数据失败: {e}")
            return False

    return True


def check_ema_signals_history(engine, config):
    """检查EMA信号历史记录"""
    logger.info("\n" + "="*80)
    logger.info("3️⃣  检查EMA信号历史")
    logger.info("="*80)

    with engine.connect() as conn:
        try:
            # 检查表是否存在
            result = conn.execute(text("SHOW TABLES LIKE 'ema_signals'"))
            if not result.fetchone():
                logger.warning("  ⚠️  ema_signals 表不存在（正常，首次运行会自动创建）")
                return True

            # 统计信号数量
            query = text("""
                SELECT
                    COUNT(*) as total_signals,
                    COUNT(DISTINCT symbol) as unique_symbols,
                    MAX(signal_time) as last_signal,
                    MIN(signal_time) as first_signal
                FROM ema_signals
                WHERE signal_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
            """)

            result = conn.execute(query)
            row = result.fetchone()

            if row and row[0] > 0:
                total = row[0]
                unique = row[1]
                last = row[2]
                first = row[3]

                logger.info(f"  ✅ 7天内信号数: {total}")
                logger.info(f"  ✅ 涉及币种数: {unique}")
                logger.info(f"  ✅ 最新信号时间: {last}")
                logger.info(f"  ✅ 最早信号时间: {first}")

                # 查询最近5个信号
                recent_query = text("""
                    SELECT
                        symbol,
                        signal_time,
                        signal_strength,
                        current_price,
                        ema_short,
                        ema_long,
                        volume_ratio
                    FROM ema_signals
                    ORDER BY signal_time DESC
                    LIMIT 5
                """)

                result = conn.execute(recent_query)
                rows = result.fetchall()

                if rows:
                    logger.info(f"\n  📊 最近5个EMA信号:")
                    logger.info("  " + "-"*100)
                    logger.info(f"  {'时间':<20} {'币种':<15} {'强度':<8} {'价格':<12} {'EMA短':<10} {'EMA长':<10} {'成交量比':<8}")
                    logger.info("  " + "-"*100)

                    for row in rows:
                        signal_time = row[1].strftime('%Y-%m-%d %H:%M:%S') if row[1] else 'N/A'
                        symbol = row[0]
                        strength = row[2]
                        price = f"${row[3]:,.2f}" if row[3] else 'N/A'
                        ema_short = f"{row[4]:.2f}" if row[4] else 'N/A'
                        ema_long = f"{row[5]:.2f}" if row[5] else 'N/A'
                        volume_ratio = f"{row[6]:.2f}x" if row[6] else 'N/A'

                        logger.info(f"  {signal_time:<20} {symbol:<15} {strength:<8} {price:<12} {ema_short:<10} {ema_long:<10} {volume_ratio:<8}")

                    logger.info("  " + "-"*100)
            else:
                logger.warning("  ⚠️  近7天没有EMA信号记录")
                logger.info("\n  可能的原因:")
                logger.info("  1. K线数据不足（需要至少30根K线）")
                logger.info("  2. 没有币种满足EMA交叉条件")
                logger.info("  3. scheduler.py 未运行EMA监控任务")
                logger.info("  4. 成交量阈值设置过高")

        except Exception as e:
            logger.error(f"  ❌ 检查EMA信号历史失败: {e}")
            import traceback
            traceback.print_exc()


def check_scheduler_ema_task():
    """检查调度器中的EMA任务配置"""
    logger.info("\n" + "="*80)
    logger.info("4️⃣  检查调度器EMA任务配置")
    logger.info("="*80)

    scheduler_file = project_root / "app" / "scheduler.py"

    if not scheduler_file.exists():
        logger.error("  ❌ scheduler.py 文件不存在！")
        return False

    try:
        with open(scheduler_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查EMA监控相关代码
        has_ema_monitor = 'ema_monitor' in content.lower()
        has_ema_task = 'monitor_ema_signals' in content
        has_schedule = 'schedule.every(15).minutes.do' in content

        logger.info(f"  EMA监控器初始化: {'✅ 存在' if has_ema_monitor else '❌ 不存在'}")
        logger.info(f"  EMA监控任务: {'✅ 存在' if has_ema_task else '❌ 不存在'}")
        logger.info(f"  15分钟定时任务: {'✅ 存在' if has_schedule else '❌ 不存在'}")

        if not (has_ema_monitor and has_ema_task):
            logger.warning("\n  ⚠️  scheduler.py 可能缺少EMA监控配置")

        return has_ema_monitor and has_ema_task

    except Exception as e:
        logger.error(f"  ❌ 检查scheduler.py失败: {e}")
        return False


def manual_ema_scan(config):
    """手动运行一次EMA扫描"""
    logger.info("\n" + "="*80)
    logger.info("5️⃣  手动运行EMA扫描测试")
    logger.info("="*80)

    try:
        import asyncio
        from app.database.db_service import DatabaseService
        from app.trading.ema_signal_monitor import EMASignalMonitor

        logger.info("  🔄 初始化EMA监控器...")
        db_config = config.get('database', {})
        db_service = DatabaseService(db_config)
        ema_monitor = EMASignalMonitor(config, db_service)

        logger.info(f"  📊 配置: EMA{ema_monitor.short_period}/EMA{ema_monitor.long_period}, {ema_monitor.timeframe}")
        logger.info("  🔍 开始扫描所有币种...")

        # 运行异步扫描
        signals = asyncio.run(ema_monitor.scan_all_symbols())

        if signals:
            logger.info(f"\n  ✅ 发现 {len(signals)} 个EMA买入信号！")
            logger.info("\n  📊 信号详情:")
            logger.info("  " + "-"*100)

            for i, signal in enumerate(signals, 1):
                symbol = signal.get('symbol', 'N/A')
                strength = signal.get('signal_strength', 'N/A')
                price = signal.get('current_price', 0)
                ema_short = signal.get('ema_short', 0)
                ema_long = signal.get('ema_long', 0)
                volume_ratio = signal.get('volume_ratio', 0)

                logger.info(f"\n  {i}. {symbol} ({strength})")
                logger.info(f"     当前价格: ${price:,.4f}")
                logger.info(f"     EMA{ema_monitor.short_period}: {ema_short:.4f}")
                logger.info(f"     EMA{ema_monitor.long_period}: {ema_long:.4f}")
                logger.info(f"     成交量比: {volume_ratio:.2f}x")
                logger.info(f"     交叉时间: {signal.get('cross_time', 'N/A')}")

            logger.info("  " + "-"*100)

            # 统计
            strong = len([s for s in signals if s.get('signal_strength') == 'strong'])
            medium = len([s for s in signals if s.get('signal_strength') == 'medium'])
            weak = len([s for s in signals if s.get('signal_strength') == 'weak'])

            logger.info(f"\n  📈 信号强度分布:")
            logger.info(f"     强: {strong}, 中: {medium}, 弱: {weak}")

        else:
            logger.warning("  ⚠️  当前没有发现EMA买入信号")
            logger.info("\n  可能的原因:")
            logger.info("  1. 当前市场没有满足EMA交叉条件的币种")
            logger.info("  2. K线数据不足（需要至少30根K线计算EMA）")
            logger.info("  3. 成交量阈值过高（当前配置需要成交量达到平均值的1.5倍）")
            logger.info("  4. 时间周期数据缺失（检查15m K线数据）")

        return len(signals) if signals else 0

    except Exception as e:
        logger.error(f"  ❌ EMA扫描失败: {e}")
        import traceback
        traceback.print_exc()
        return 0


def print_recommendations():
    """打印建议"""
    logger.info("\n" + "="*80)
    logger.info("💡 EMA信号优化建议")
    logger.info("="*80)

    logger.info("\n如果长期没有EMA信号，可以尝试：")

    logger.info("\n1️⃣  调整EMA参数（config.yaml）:")
    logger.info("   ema_signal:")
    logger.info("     short_period: 7    # 改小短期EMA，更敏感")
    logger.info("     long_period: 21")
    logger.info("     volume_threshold: 1.2  # 降低成交量阈值")

    logger.info("\n2️⃣  检查K线数据采集:")
    logger.info("   - 确保 scheduler.py 正在运行")
    logger.info("   - 检查15m K线数据是否实时更新")
    logger.info("   - 运行: python app/scheduler.py")

    logger.info("\n3️⃣  手动触发EMA扫描:")
    logger.info("   - 运行: python test_ema_scan_now.py")

    logger.info("\n4️⃣  查看EMA信号历史:")
    logger.info("   - 检查数据库 ema_signals 表")
    logger.info("   - 确认是否有历史信号记录")

    logger.info("\n" + "="*80 + "\n")


def main():
    """主函数"""
    logger.info("\n")
    logger.info("🔍 " + "="*76)
    logger.info("🔍  EMA信号状态检查工具")
    logger.info("🔍 " + "="*76)
    logger.info(f"   时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("🔍 " + "="*76)

    try:
        # 加载配置
        config = load_config()

        # 1. 检查EMA配置
        ema_enabled = check_ema_config(config)
        if not ema_enabled:
            print_recommendations()
            return

        # 2. 获取数据库连接
        engine = get_db_connection(config)

        # 3. 检查K线数据
        check_kline_data(engine, config)

        # 4. 检查EMA信号历史
        check_ema_signals_history(engine, config)

        # 5. 检查调度器配置
        check_scheduler_ema_task()

        # 6. 手动运行EMA扫描
        signal_count = manual_ema_scan(config)

        # 7. 打印建议
        print_recommendations()

        # 总结
        logger.info("="*80)
        logger.info("✅ 检查完成")
        logger.info("="*80)

        if signal_count > 0:
            logger.info(f"🎉 当前发现 {signal_count} 个EMA买入信号！")
        else:
            logger.warning("⚠️  当前没有EMA信号，请参考上述优化建议")

        logger.info("="*80 + "\n")

    except Exception as e:
        logger.error(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
