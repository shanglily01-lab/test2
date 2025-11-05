"""
历史数据回填脚本
用于拉取指定日期范围的历史 K线数据和价格数据

使用方法：
    python backfill_historical_data.py

配置：
    - 日期范围：2025年10月29日 ~ 2025年11月4日
    - 时间周期：1m, 5m, 15m, 1h
    - 交易所：Binance, Gate.io
"""

import asyncio
import yaml
from datetime import datetime, timedelta
from loguru import logger
import pandas as pd
from typing import List

# 配置日志
logger.remove()
logger.add(
    "logs/backfill_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)
logger.add(lambda msg: print(msg), level="INFO")


class HistoricalDataBackfill:
    """历史数据回填器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """初始化回填器"""

        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取监控币种
        self.symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 初始化数据库
        from app.database.db_service import DatabaseService
        db_config = self.config.get('database', {})
        self.db_service = DatabaseService(db_config)

        # 初始化价格采集器
        from app.collectors.price_collector import MultiExchangeCollector
        self.price_collector = MultiExchangeCollector(self.config)

        logger.info(f"初始化完成，监控币种: {len(self.symbols)} 个")

    async def backfill_klines(
        self,
        start_date: str,
        end_date: str,
        timeframes: List[str] = ['1m', '5m', '15m', '1h']
    ):
        """
        回填 K线数据

        Args:
            start_date: 开始日期，格式 'YYYY-MM-DD'
            end_date: 结束日期，格式 'YYYY-MM-DD'
            timeframes: 时间周期列表
        """
        logger.info("="*60)
        logger.info(f"开始回填 K线数据")
        logger.info(f"日期范围: {start_date} ~ {end_date}")
        logger.info(f"时间周期: {', '.join(timeframes)}")
        logger.info(f"交易所: Binance, Gate.io")
        logger.info("="*60)

        # 转换日期
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)  # 包含结束日期

        # 获取启用的交易所
        enabled_exchanges = list(self.price_collector.collectors.keys())
        logger.info(f"启用的交易所: {', '.join(enabled_exchanges)}")

        total_saved = 0
        total_errors = 0

        # 遍历每个时间周期
        for timeframe in timeframes:
            logger.info(f"\n{'='*60}")
            logger.info(f"时间周期: {timeframe}")
            logger.info(f"{'='*60}")

            # 计算需要获取的数据量
            # 根据时间周期计算间隔分钟数
            interval_minutes = {
                '1m': 1,
                '5m': 5,
                '15m': 15,
                '1h': 60,
                '1d': 1440
            }.get(timeframe, 60)

            # 计算总时长（分钟）
            total_minutes = int((end_dt - start_dt).total_seconds() / 60)
            # 计算需要的K线数量
            limit = min(total_minutes // interval_minutes + 1, 1000)  # API限制通常是1000

            logger.info(f"预计需要获取约 {limit} 根K线")

            # 遍历每个币对
            for i, symbol in enumerate(self.symbols, 1):
                logger.info(f"\n[{i}/{len(self.symbols)}] {symbol}")

                symbol_saved = 0
                symbol_errors = 0

                # 尝试从不同交易所获取数据
                for exchange in ['binance', 'gate']:
                    if exchange not in enabled_exchanges:
                        continue

                    try:
                        logger.info(f"  正在从 {exchange} 获取数据...")

                        # 获取 K线数据 - 直接使用底层 collector
                        if exchange not in self.price_collector.collectors:
                            logger.warning(f"  ⊗ {exchange} 未启用")
                            continue

                        collector = self.price_collector.collectors[exchange]
                        df = await collector.fetch_ohlcv(
                            symbol=symbol,
                            timeframe=timeframe,
                            since=int(start_dt.timestamp() * 1000),
                            limit=limit
                        )

                        if df is None or len(df) == 0:
                            logger.warning(f"  ⊗ {exchange} 无数据")
                            symbol_errors += 1
                            continue

                        # 过滤日期范围
                        df = df[
                            (df['timestamp'] >= start_dt) &
                            (df['timestamp'] < end_dt)
                        ]

                        if len(df) == 0:
                            logger.warning(f"  ⊗ {exchange} 过滤后无数据")
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
                                self.db_service.save_kline_data(kline_data)
                                saved_count += 1
                            except Exception as e:
                                logger.debug(f"  保存K线失败: {e}")

                        symbol_saved += saved_count
                        logger.info(f"  ✓ {exchange} 保存 {saved_count} 根K线")

                        # 找到一个交易所有数据就跳出
                        break

                    except Exception as e:
                        logger.error(f"  ✗ {exchange} 获取失败: {e}")
                        symbol_errors += 1
                        continue

                    # 延迟避免请求过快
                    await asyncio.sleep(0.5)

                total_saved += symbol_saved
                total_errors += symbol_errors

                if symbol_saved > 0:
                    logger.info(f"  ✓ {symbol} 总计保存 {symbol_saved} 根K线")
                else:
                    logger.warning(f"  ⊗ {symbol} 未保存任何数据")

                # 延迟避免请求过快
                await asyncio.sleep(1)

            logger.info(f"\n{timeframe} 周期完成，保存 {total_saved} 根K线")

        logger.info(f"\n{'='*60}")
        logger.info(f"K线数据回填完成")
        logger.info(f"总计保存: {total_saved} 根K线")
        logger.info(f"错误数量: {total_errors}")
        logger.info(f"{'='*60}")

    async def backfill_prices(
        self,
        start_date: str,
        end_date: str
    ):
        """
        回填价格数据（ticker数据）

        注意：大多数交易所不提供历史ticker数据，只能获取当前价格
        此方法主要用于记录当前价格快照

        Args:
            start_date: 开始日期
            end_date: 结束日期
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"开始获取当前价格数据")
        logger.info(f"{'='*60}")

        total_saved = 0
        total_errors = 0

        for i, symbol in enumerate(self.symbols, 1):
            logger.info(f"\n[{i}/{len(self.symbols)}] {symbol}")

            try:
                # 获取当前价格（从所有交易所）
                prices = await self.price_collector.fetch_price(symbol)

                if prices:
                    for price_data in prices:
                        self.db_service.save_price_data(price_data)
                        exchange = price_data.get('exchange', 'unknown')
                        logger.info(f"  ✓ [{exchange}] 价格: ${price_data['price']:,.2f} "
                                  f"(24h: {price_data['change_24h']:+.2f}%)")
                        total_saved += 1
                else:
                    logger.warning(f"  ⊗ {symbol} 未获取到价格数据")
                    total_errors += 1

            except Exception as e:
                logger.error(f"  ✗ {symbol} 获取价格失败: {e}")
                total_errors += 1

            # 延迟
            await asyncio.sleep(1)

        logger.info(f"\n{'='*60}")
        logger.info(f"价格数据获取完成")
        logger.info(f"总计保存: {total_saved} 条")
        logger.info(f"错误数量: {total_errors}")
        logger.info(f"{'='*60}")

    async def verify_data(
        self,
        start_date: str,
        end_date: str,
        timeframes: List[str] = ['1m', '5m', '15m', '1h']
    ):
        """
        验证回填的数据

        Args:
            start_date: 开始日期
            end_date: 结束日期
            timeframes: 时间周期列表
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"验证回填数据")
        logger.info(f"{'='*60}")

        from sqlalchemy import text

        session = self.db_service.get_session()

        try:
            # 转换日期为时间戳
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            start_ts = int(start_dt.timestamp() * 1000)
            end_ts = int(end_dt.timestamp() * 1000)

            for timeframe in timeframes:
                logger.info(f"\n时间周期: {timeframe}")
                logger.info(f"-" * 60)

                # 查询每个币对的数据量
                for symbol in self.symbols[:5]:  # 只显示前5个
                    query = text("""
                        SELECT
                            exchange,
                            COUNT(*) as count,
                            MIN(open_time) as first_time,
                            MAX(open_time) as last_time
                        FROM kline_data
                        WHERE symbol = :symbol
                        AND timeframe = :timeframe
                        AND open_time >= :start_ts
                        AND open_time < :end_ts
                        GROUP BY exchange
                    """)

                    results = session.execute(query, {
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'start_ts': start_ts,
                        'end_ts': end_ts
                    }).fetchall()

                    if results:
                        for row in results:
                            first_dt = datetime.fromtimestamp(row.first_time / 1000)
                            last_dt = datetime.fromtimestamp(row.last_time / 1000)
                            logger.info(f"  {symbol} [{row.exchange}]: {row.count} 根K线 "
                                      f"({first_dt.strftime('%m-%d %H:%M')} ~ "
                                      f"{last_dt.strftime('%m-%d %H:%M')})")
                    else:
                        logger.warning(f"  {symbol}: 无数据 ⚠️")

        finally:
            session.close()

        logger.info(f"\n{'='*60}")
        logger.info(f"验证完成")
        logger.info(f"{'='*60}")


async def main():
    """主函数"""

    # 配置参数
    START_DATE = '2025-10-29'  # 开始日期
    END_DATE = '2025-11-04'    # 结束日期
    TIMEFRAMES = ['1m', '5m', '15m', '1h']  # 时间周期

    logger.info(f"""
╔════════════════════════════════════════════════════════════╗
║          历史数据回填脚本                                    ║
╠════════════════════════════════════════════════════════════╣
║  日期范围: {START_DATE} ~ {END_DATE}                  ║
║  时间周期: {', '.join(TIMEFRAMES)}                         ║
║  交易所:   Binance, Gate.io                                ║
╚════════════════════════════════════════════════════════════╝
    """)

    try:
        # 初始化回填器
        backfill = HistoricalDataBackfill()

        # 1. 回填 K线数据
        await backfill.backfill_klines(
            start_date=START_DATE,
            end_date=END_DATE,
            timeframes=TIMEFRAMES
        )

        # 2. 获取当前价格数据
        logger.info("\n")
        await backfill.backfill_prices(
            start_date=START_DATE,
            end_date=END_DATE
        )

        # 3. 验证数据
        logger.info("\n")
        await backfill.verify_data(
            start_date=START_DATE,
            end_date=END_DATE,
            timeframes=TIMEFRAMES
        )

        logger.info(f"\n{'='*60}")
        logger.info(f"✅ 所有任务完成！")
        logger.info(f"{'='*60}\n")

    except KeyboardInterrupt:
        logger.warning("\n用户中断操作")
    except Exception as e:
        logger.error(f"\n回填过程出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    asyncio.run(main())
