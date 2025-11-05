"""
回填最近24小时的K线数据
用于修复成交量统计显示问题

使用方法：
    python backfill_last_24h.py
"""

import asyncio
import yaml
from datetime import datetime, timedelta
from loguru import logger

# 配置日志
logger.remove()
logger.add(
    "logs/backfill_24h_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)
logger.add(lambda msg: print(msg), level="INFO")


async def backfill_24h():
    """回填最近24小时的5分钟K线数据"""

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取监控币种
    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

    # 初始化数据库
    from app.database.db_service import DatabaseService
    db_config = config.get('database', {})
    db_service = DatabaseService(db_config)

    # 初始化价格采集器
    from app.collectors.price_collector import MultiExchangeCollector
    price_collector = MultiExchangeCollector(config)

    # 计算时间范围：最近24小时
    end_time = datetime.now()
    start_time = end_time - timedelta(hours=24)

    logger.info("="*60)
    logger.info("回填最近24小时的5分钟K线数据")
    logger.info("="*60)
    logger.info(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}")
    logger.info(f"币种数量: {len(symbols)}")
    logger.info("="*60)

    total_saved = 0
    total_errors = 0

    # 只回填5分钟K线（用于24小时统计）
    timeframe = '5m'
    limit = 288  # 24小时 * 12根/小时 = 288根

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"\n[{i}/{len(symbols)}] {symbol}")

        try:
            # 优先从 Binance 获取
            exchange = 'binance'

            if exchange not in price_collector.collectors:
                logger.warning(f"  ⊗ {exchange} 未启用")
                continue

            collector = price_collector.collectors[exchange]

            logger.info(f"  正在从 {exchange} 获取最近24小时的5分钟K线...")

            # 获取K线数据
            df = await collector.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=int(start_time.timestamp() * 1000),
                limit=limit
            )

            if df is None or len(df) == 0:
                logger.warning(f"  ⊗ {exchange} 无数据")
                total_errors += 1
                continue

            # 过滤时间范围
            df = df[
                (df['timestamp'] >= start_time) &
                (df['timestamp'] <= end_time)
            ]

            if len(df) == 0:
                logger.warning(f"  ⊗ 过滤后无数据")
                continue

            # 保存每根K线
            saved_count = 0
            for _, row in df.iterrows():
                kline_data = {
                    'symbol': symbol,
                    'exchange': exchange,
                    'timeframe': timeframe,
                    'open_time': int(row['timestamp'].timestamp() * 1000),
                    'timestamp': row['timestamp'],
                    'open': row['open'],
                    'high': row['high'],
                    'low': row['low'],
                    'close': row['close'],
                    'volume': row['volume'],
                    'quote_volume': row.get('quote_volume')
                }

                try:
                    db_service.save_kline_data(kline_data)
                    saved_count += 1
                except Exception as e:
                    logger.debug(f"  保存K线失败（可能已存在）: {e}")

            total_saved += saved_count
            logger.info(f"  ✓ 保存 {saved_count} 根K线")

        except Exception as e:
            logger.error(f"  ✗ 获取 {symbol} 数据失败: {e}")
            total_errors += 1
            continue

        # 延迟避免请求过快
        await asyncio.sleep(1)

    logger.info(f"\n{'='*60}")
    logger.info(f"回填完成")
    logger.info(f"总计保存: {total_saved} 根K线")
    logger.info(f"错误数量: {total_errors}")
    logger.info(f"{'='*60}")

    # 验证数据
    logger.info("\n验证回填结果...")
    from sqlalchemy import text
    session = db_service.get_session()

    try:
        for symbol in symbols[:5]:  # 只验证前5个
            query = text("""
                SELECT COUNT(*) as count
                FROM kline_data
                WHERE symbol = :symbol
                  AND timeframe = '5m'
                  AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """)

            result = session.execute(query, {'symbol': symbol}).fetchone()
            count = result.count if result else 0
            expected = 288
            coverage = (count / expected * 100) if expected > 0 else 0

            status = "✓" if coverage >= 80 else "⚠️"
            logger.info(f"  {status} {symbol}: {count} 根K线 (覆盖率: {coverage:.1f}%)")

    finally:
        session.close()

    logger.info(f"\n{'='*60}")
    logger.info("✅ 全部完成！现在可以刷新 Dashboard 查看正确的成交量数据")
    logger.info(f"{'='*60}\n")


async def main():
    """主函数"""
    try:
        await backfill_24h()
    except KeyboardInterrupt:
        logger.warning("\n用户中断操作")
    except Exception as e:
        logger.error(f"\n回填过程出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
