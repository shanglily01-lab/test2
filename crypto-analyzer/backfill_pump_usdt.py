#!/usr/bin/env python3
"""
PUMP/USDT 历史数据回填脚本
采集最近8天的 1m, 5m, 15m, 1h K线和价格数据

使用方法：
    python backfill_pump_usdt.py
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
    "logs/backfill_pump_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
)
logger.add(lambda msg: print(msg), level="INFO")


class PumpBackfill:
    """PUMP/USDT 历史数据回填器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """初始化回填器"""

        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 只监控 PUMP/USDT
        self.symbols = ['PUMP/USDT']

        # 初始化数据库
        from app.database.db_service import DatabaseService
        db_config = self.config.get('database', {})
        self.db_service = DatabaseService(db_config)

        # 初始化价格采集器
        from app.collectors.price_collector import MultiExchangeCollector
        self.price_collector = MultiExchangeCollector(self.config)

        logger.info(f"初始化完成，目标币种: PUMP/USDT")

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
        logger.info(f"开始回填 PUMP/USDT K线数据")
        logger.info(f"日期范围: {start_date} ~ {end_date}")
        logger.info(f"时间周期: {', '.join(timeframes)}")
        logger.info(f"交易所: Binance, Gate.io")
        logger.info("="*60)

        # 转换日期
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)  # 包含结束日期

        # 转换为毫秒时间戳
        since = int(start_dt.timestamp() * 1000)
        until = int(end_dt.timestamp() * 1000)

        # 对每个时间周期进行回填
        for timeframe in timeframes:
            logger.info(f"\n{'='*60}")
            logger.info(f"时间周期: {timeframe}")
            logger.info(f"{'='*60}")

            symbol = 'PUMP/USDT'
            logger.info(f"正在获取 {symbol} 数据...")

            total_saved = 0
            errors = []

            # 尝试 Binance
            try:
                logger.info(f"  正在从 binance 获取数据...")
                klines = await self.price_collector.binance_collector.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=since,
                    limit=1000
                )

                if klines and len(klines) > 0:
                    # 保存数据
                    saved = self.db_service.save_kline_data(
                        symbol=symbol,
                        timeframe=timeframe,
                        klines=klines,
                        exchange='binance'
                    )
                    total_saved += saved
                    logger.info(f"  ✓ binance 保存 {saved} 根K线")
                else:
                    logger.warning(f"  ⚠ binance 无数据")

            except Exception as e:
                error_msg = f"binance 获取失败: {str(e)}"
                logger.error(f"  ✗ {error_msg}")
                errors.append(error_msg)

            # 延迟避免频率限制
            await asyncio.sleep(1)

            # 尝试 Gate.io（作为备选）
            try:
                logger.info(f"  正在从 gate 获取数据...")
                klines = await self.price_collector.gate_collector.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=timeframe,
                    since=since,
                    limit=1000
                )

                if klines and len(klines) > 0:
                    # 保存数据
                    saved = self.db_service.save_kline_data(
                        symbol=symbol,
                        timeframe=timeframe,
                        klines=klines,
                        exchange='gate'
                    )
                    total_saved += saved
                    logger.info(f"  ✓ gate 保存 {saved} 根K线")
                else:
                    logger.warning(f"  ⚠ gate 无数据")

            except Exception as e:
                error_msg = f"gate 获取失败: {str(e)}"
                logger.error(f"  ✗ {error_msg}")
                errors.append(error_msg)

            # 延迟
            await asyncio.sleep(1)

            # 总结
            if total_saved > 0:
                logger.info(f"  ✓ {symbol} 总计保存 {total_saved} 根K线")
            else:
                logger.error(f"  ⊗ {symbol} 未保存任何数据")
                if errors:
                    for err in errors:
                        logger.error(f"    - {err}")

        logger.info(f"\n{'='*60}")
        logger.info(f"K线数据回填完成")
        logger.info(f"{'='*60}")

    async def fetch_current_prices(self):
        """获取当前价格数据"""
        logger.info(f"\n{'='*60}")
        logger.info(f"开始获取当前价格数据")
        logger.info(f"{'='*60}")

        symbol = 'PUMP/USDT'
        logger.info(f"正在获取 {symbol} 价格...")

        total_saved = 0
        total_errors = 0

        try:
            # 从 Binance 获取价格
            try:
                ticker = await self.price_collector.binance_collector.fetch_ticker(symbol)
                if ticker:
                    # 保存价格
                    self.db_service.save_price_data(
                        symbol=symbol,
                        exchange='binance',
                        price_data={
                            'price': ticker.get('last'),
                            'change_24h': ticker.get('percentage', 0),
                            'volume_24h': ticker.get('baseVolume', 0),
                            'quote_volume_24h': ticker.get('quoteVolume', 0),
                            'high_24h': ticker.get('high', 0),
                            'low_24h': ticker.get('low', 0)
                        }
                    )
                    total_saved += 1
                    logger.info(f"  ✓ [binance] 价格: ${ticker.get('last', 0):.6f} (24h: {ticker.get('percentage', 0):+.2f}%)")
            except Exception as e:
                logger.error(f"  ✗ [binance] 获取失败: {e}")
                total_errors += 1

            # 从 Gate.io 获取价格
            await asyncio.sleep(0.5)
            try:
                ticker = await self.price_collector.gate_collector.fetch_ticker(symbol)
                if ticker:
                    # 保存价格
                    self.db_service.save_price_data(
                        symbol=symbol,
                        exchange='gate',
                        price_data={
                            'price': ticker.get('last'),
                            'change_24h': ticker.get('percentage', 0),
                            'volume_24h': ticker.get('baseVolume', 0),
                            'quote_volume_24h': ticker.get('quoteVolume', 0),
                            'high_24h': ticker.get('high', 0),
                            'low_24h': ticker.get('low', 0)
                        }
                    )
                    total_saved += 1
                    logger.info(f"  ✓ [gate] 价格: ${ticker.get('last', 0):.6f} (24h: {ticker.get('percentage', 0):+.2f}%)")
            except Exception as e:
                logger.error(f"  ✗ [gate] 获取失败: {e}")
                total_errors += 1

        except Exception as e:
            logger.error(f"  ✗ {symbol} 获取价格失败: {e}")
            total_errors += 1

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

        # 转换日期
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)

        start_ts = int(start_dt.timestamp() * 1000)
        end_ts = int(end_dt.timestamp() * 1000)

        symbol = 'PUMP/USDT'

        # 查询数据
        from sqlalchemy import text
        session = self.db_service.get_session()

        try:
            for timeframe in timeframes:
                logger.info(f"\n时间周期: {timeframe}")
                logger.info("-" * 60)

                query = text("""
                    SELECT exchange, COUNT(*) as count,
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

    # 计算最近8天的日期范围
    end_date = datetime.now()
    start_date = end_date - timedelta(days=8)

    START_DATE = start_date.strftime('%Y-%m-%d')
    END_DATE = end_date.strftime('%Y-%m-%d')
    TIMEFRAMES = ['1m', '5m', '15m', '1h']  # 时间周期

    logger.info(f"""
╔════════════════════════════════════════════════════════════╗
║          PUMP/USDT 历史数据回填脚本                          ║
╠════════════════════════════════════════════════════════════╣
║  币种:     PUMP/USDT                                       ║
║  日期范围: {START_DATE} ~ {END_DATE}                  ║
║  时间周期: {', '.join(TIMEFRAMES)}                         ║
║  交易所:   Binance, Gate.io                                ║
╚════════════════════════════════════════════════════════════╝
    """)

    try:
        # 初始化回填器
        backfill = PumpBackfill()

        # 1. 回填 K线数据
        await backfill.backfill_klines(
            start_date=START_DATE,
            end_date=END_DATE,
            timeframes=TIMEFRAMES
        )

        # 2. 获取当前价格
        await backfill.fetch_current_prices()

        # 3. 验证数据
        await backfill.verify_data(
            start_date=START_DATE,
            end_date=END_DATE,
            timeframes=TIMEFRAMES
        )

        logger.info(f"\n{'='*60}")
        logger.info("✅ 所有任务完成！")
        logger.info(f"{'='*60}")

    except Exception as e:
        logger.error(f"\n❌ 执行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == '__main__':
    asyncio.run(main())
