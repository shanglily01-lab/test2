#!/usr/bin/env python3
"""
历史K线数据回补脚本
用于补采集因 scheduler 中断而缺失的历史数据

使用方法:
python scripts/backfill_kline_data.py --start "2025-10-28 00:00:00" --end "2025-10-28 13:00:00"
"""

import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import yaml
from datetime import datetime, timedelta
from loguru import logger
from typing import List, Dict
import pandas as pd

from app.collectors.price_collector import PriceCollector
from app.collectors.gate_collector import GateCollector
from app.database.db_service import DatabaseService


class KlineBackfiller:
    """K线数据回补器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """
        初始化回补器

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取币种列表
        self.symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 初始化数据库服务
        db_config = self.config.get('database', {})
        self.db_service = DatabaseService(db_config)

        # 初始化采集器
        self.collectors = {}

        # Binance 采集器
        if self.config.get('exchanges', {}).get('binance', {}).get('enabled', True):
            binance_config = self.config['exchanges']['binance']
            self.collectors['binance'] = PriceCollector('binance', binance_config)
            logger.info("✓ 初始化 Binance 采集器")

        # Gate.io 采集器
        if self.config.get('exchanges', {}).get('gate', {}).get('enabled', False):
            gate_config = self.config['exchanges']['gate']
            self.collectors['gate'] = GateCollector(gate_config)
            logger.info("✓ 初始化 Gate.io 采集器")

    async def backfill_klines(
        self,
        start_time: datetime,
        end_time: datetime,
        timeframes: List[str] = None
    ):
        """
        回补K线数据

        Args:
            start_time: 开始时间
            end_time: 结束时间
            timeframes: 时间周期列表，如 ['1m', '5m', '1h']
        """
        if timeframes is None:
            timeframes = ['1m', '5m', '1h']

        logger.info(f"\n{'='*80}")
        logger.info(f"开始回补K线数据")
        logger.info(f"时间范围: {start_time} ~ {end_time}")
        logger.info(f"币种数量: {len(self.symbols)}")
        logger.info(f"时间周期: {', '.join(timeframes)}")
        logger.info(f"交易所: {', '.join(self.collectors.keys())}")
        logger.info(f"{'='*80}\n")

        total_saved = 0
        total_errors = 0

        for timeframe in timeframes:
            logger.info(f"\n📊 回补 {timeframe} K线数据...")

            for symbol in self.symbols:
                try:
                    # 计算需要采集的K线数量
                    limit = self._calculate_limit(start_time, end_time, timeframe)

                    if limit == 0:
                        logger.warning(f"  ⚠️  {symbol} ({timeframe}): 时间范围太小，跳过")
                        continue

                    # 优先使用 Binance，如果失败则尝试 Gate.io
                    df = None
                    used_exchange = None

                    for exchange_name in ['binance', 'gate']:
                        if exchange_name not in self.collectors:
                            continue

                        try:
                            collector = self.collectors[exchange_name]

                            # 获取K线数据
                            since = int(start_time.timestamp() * 1000)  # 毫秒时间戳
                            df = await collector.fetch_ohlcv(
                                symbol=symbol,
                                timeframe=timeframe,
                                limit=limit,
                                since=since
                            )

                            if df is not None and len(df) > 0:
                                used_exchange = exchange_name
                                break

                        except Exception as e:
                            logger.debug(f"    {exchange_name} 获取失败: {e}")
                            continue

                    if df is None or len(df) == 0:
                        logger.warning(f"  ⊗ {symbol} ({timeframe}): 所有交易所均无数据")
                        total_errors += 1
                        continue

                    # 过滤时间范围
                    df = df[
                        (df['timestamp'] >= start_time) &
                        (df['timestamp'] <= end_time)
                    ]

                    if len(df) == 0:
                        logger.debug(f"  ⊗ {symbol} ({timeframe}): 时间范围内无数据")
                        continue

                    # 保存每一条K线
                    saved_count = 0
                    for _, row in df.iterrows():
                        kline_data = {
                            'symbol': symbol,
                            'exchange': used_exchange,
                            'timeframe': timeframe,
                            'open_time': int(row['timestamp'].timestamp() * 1000),
                            'timestamp': row['timestamp'],
                            'open': float(row['open']),
                            'high': float(row['high']),
                            'low': float(row['low']),
                            'close': float(row['close']),
                            'volume': float(row['volume']),
                            'quote_volume': float(row.get('quote_volume', 0))
                        }

                        try:
                            self.db_service.save_kline_data(kline_data)
                            saved_count += 1
                        except Exception as e:
                            logger.error(f"    保存K线失败: {e}")
                            total_errors += 1

                    total_saved += saved_count
                    logger.info(
                        f"  ✓ [{used_exchange}] {symbol} ({timeframe}): "
                        f"保存 {saved_count} 条K线"
                    )

                    # 延迟避免API限流
                    await asyncio.sleep(0.2)

                except Exception as e:
                    logger.error(f"  ✗ {symbol} ({timeframe}): {e}")
                    total_errors += 1

        logger.info(f"\n{'='*80}")
        logger.info(f"✅ K线数据回补完成")
        logger.info(f"总保存: {total_saved} 条, 错误: {total_errors} 次")
        logger.info(f"{'='*80}\n")

    async def backfill_prices(
        self,
        start_time: datetime,
        end_time: datetime
    ):
        """
        回补价格数据（基于1分钟K线）

        Args:
            start_time: 开始时间
            end_time: 结束时间
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"开始回补价格数据（基于1分钟K线）")
        logger.info(f"时间范围: {start_time} ~ {end_time}")
        logger.info(f"{'='*80}\n")

        total_saved = 0
        total_errors = 0

        for symbol in self.symbols:
            try:
                # 计算需要采集的数量
                minutes = int((end_time - start_time).total_seconds() / 60)
                limit = min(minutes, 1000)  # Binance 最多1000条

                if limit == 0:
                    continue

                # 优先使用 Binance
                df = None
                used_exchange = None

                for exchange_name in ['binance', 'gate']:
                    if exchange_name not in self.collectors:
                        continue

                    try:
                        collector = self.collectors[exchange_name]
                        since = int(start_time.timestamp() * 1000)

                        df = await collector.fetch_ohlcv(
                            symbol=symbol,
                            timeframe='1m',
                            limit=limit,
                            since=since
                        )

                        if df is not None and len(df) > 0:
                            used_exchange = exchange_name
                            break

                    except Exception as e:
                        logger.debug(f"    {exchange_name} 获取失败: {e}")
                        continue

                if df is None or len(df) == 0:
                    logger.warning(f"  ⊗ {symbol}: 所有交易所均无数据")
                    total_errors += 1
                    continue

                # 过滤时间范围
                df = df[
                    (df['timestamp'] >= start_time) &
                    (df['timestamp'] <= end_time)
                ]

                if len(df) == 0:
                    logger.debug(f"  ⊗ {symbol}: 时间范围内无数据")
                    continue

                # 保存价格数据
                saved_count = 0
                for _, row in df.iterrows():
                    price_data = {
                        'symbol': symbol,
                        'exchange': used_exchange,
                        'timestamp': row['timestamp'],
                        'price': float(row['close']),
                        'open': float(row['open']),
                        'high': float(row['high']),
                        'low': float(row['low']),
                        'close': float(row['close']),
                        'volume': float(row['volume']),
                        'quote_volume': float(row.get('quote_volume', 0)),
                        'bid': 0.0,  # 历史数据不包含买卖价
                        'ask': 0.0,
                        'change_24h': 0.0
                    }

                    try:
                        self.db_service.save_price_data(price_data)
                        saved_count += 1
                    except Exception as e:
                        logger.error(f"    保存价格失败: {e}")
                        total_errors += 1

                total_saved += saved_count
                logger.info(
                    f"  ✓ [{used_exchange}] {symbol}: "
                    f"保存 {saved_count} 条价格记录"
                )

                await asyncio.sleep(0.2)

            except Exception as e:
                logger.error(f"  ✗ {symbol}: {e}")
                total_errors += 1

        logger.info(f"\n{'='*80}")
        logger.info(f"✅ 价格数据回补完成")
        logger.info(f"总保存: {total_saved} 条, 错误: {total_errors} 次")
        logger.info(f"{'='*80}\n")

    def _calculate_limit(self, start: datetime, end: datetime, timeframe: str) -> int:
        """
        计算需要获取的K线数量

        Args:
            start: 开始时间
            end: 结束时间
            timeframe: 时间周期

        Returns:
            K线数量
        """
        # 时间间隔（分钟）
        interval_minutes = {
            '1m': 1,
            '5m': 5,
            '15m': 15,
            '30m': 30,
            '1h': 60,
            '4h': 240,
            '1d': 1440,
        }

        minutes = interval_minutes.get(timeframe, 60)
        total_minutes = int((end - start).total_seconds() / 60)
        limit = int(total_minutes / minutes) + 1

        # Binance 限制最多1000条
        return min(limit, 1000)


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='K线数据回补脚本')
    parser.add_argument(
        '--start',
        type=str,
        required=True,
        help='开始时间 (格式: "2025-10-28 00:00:00")'
    )
    parser.add_argument(
        '--end',
        type=str,
        required=True,
        help='结束时间 (格式: "2025-10-28 13:00:00")'
    )
    parser.add_argument(
        '--timeframes',
        type=str,
        default='1m,5m,1h',
        help='时间周期，逗号分隔 (默认: "1m,5m,1h")'
    )
    parser.add_argument(
        '--include-prices',
        action='store_true',
        help='同时回补价格数据表'
    )

    args = parser.parse_args()

    # 解析时间
    try:
        start_time = datetime.strptime(args.start, '%Y-%m-%d %H:%M:%S')
        end_time = datetime.strptime(args.end, '%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        logger.error(f"时间格式错误: {e}")
        logger.error('正确格式: "2025-10-28 00:00:00"')
        return

    if start_time >= end_time:
        logger.error("开始时间必须早于结束时间")
        return

    # 解析时间周期
    timeframes = [tf.strip() for tf in args.timeframes.split(',')]

    # 创建回补器
    backfiller = KlineBackfiller()

    # 回补K线数据
    await backfiller.backfill_klines(start_time, end_time, timeframes)

    # 回补价格数据（可选）
    if args.include_prices:
        await backfiller.backfill_prices(start_time, end_time)

    logger.info("🎉 所有数据回补完成！")


if __name__ == '__main__':
    asyncio.run(main())