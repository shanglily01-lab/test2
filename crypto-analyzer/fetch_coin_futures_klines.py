#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拉取币本位合约K线数据（最近3天）

用途：
- 解决币本位合约K线数据不足问题
- 拉取1d、1h、15m三个周期的K线数据
- 保存到kline_data表

使用方法：
python3 fetch_coin_futures_klines.py
"""

import asyncio
import pymysql
from datetime import datetime, timedelta
from loguru import logger
import sys
from pathlib import Path
import ccxt.async_support as ccxt

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from app.utils.config_loader import load_config


class CoinFuturesKlineFetcher:
    """币本位合约K线数据拉取器"""

    def __init__(self):
        """初始化"""
        # 配置日志
        logger.remove()
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:<8}</level> | <level>{message}</level>",
            level="INFO"
        )

        # 加载配置
        config = load_config()
        self.db_config = config['database']['mysql']

        # 从config.yaml加载交易对列表
        self.symbols = config.get('symbols', [])

        # 初始化交易所
        self.exchange = None

    async def init_exchange(self):
        """初始化交易所连接"""
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # 币本位合约
                'defaultSubType': 'inverse',  # 反向合约（币本位）
            }
        })
        logger.info("✅ 已连接到Binance币本位合约API (Inverse Perpetual)")

    async def fetch_and_save_klines(self, symbol: str, timeframe: str, limit: int):
        """
        拉取并保存K线数据

        Args:
            symbol: 交易对 (如 BTC/USD)
            timeframe: 时间周期 (1d/1h/15m)
            limit: 拉取数量
        """
        try:
            # 拉取币本位合约K线数据
            params = {'subType': 'inverse'}  # 显式指定币本位合约
            ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit, params=params)

            if not ohlcv:
                logger.warning(f"⚠️ {symbol} {timeframe} 无数据")
                return 0

            # 连接数据库
            conn = pymysql.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                charset='utf8mb4'
            )
            cursor = conn.cursor()

            # 准备插入数据
            insert_count = 0
            for candle in ohlcv:
                open_time, open_price, high, low, close, volume = candle

                # 转换时间戳为datetime
                dt = datetime.fromtimestamp(open_time / 1000)

                # 插入数据（使用ON DUPLICATE KEY UPDATE避免重复）
                cursor.execute("""
                    INSERT INTO kline_data
                    (symbol, exchange, timeframe, open_time, timestamp, open_price, high_price, low_price, close_price, volume, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON DUPLICATE KEY UPDATE
                        open_price = VALUES(open_price),
                        high_price = VALUES(high_price),
                        low_price = VALUES(low_price),
                        close_price = VALUES(close_price),
                        volume = VALUES(volume)
                """, (
                    symbol, 'binance', timeframe, open_time, dt,
                    open_price, high, low, close, volume
                ))
                insert_count += 1

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ {symbol:<12} {timeframe:<4} 保存 {insert_count} 条K线数据")
            return insert_count

        except Exception as e:
            logger.error(f"❌ {symbol} {timeframe} 拉取失败: {e}")
            return 0

    async def fetch_all(self):
        """拉取所有交易对的K线数据"""
        await self.init_exchange()

        total_count = 0
        start_time = datetime.now()

        logger.info("=" * 80)
        logger.info(f"🚀 开始拉取币本位K线数据 | 交易对: {len(self.symbols)} 个")
        logger.info("=" * 80)

        # 需要拉取的数据量
        timeframes = {
            '1d': 50,   # 50天
            '1h': 100,  # 100小时（约4天）
            '15m': 96   # 96个15分钟（24小时）
        }

        for symbol in self.symbols:
            logger.info(f"\n📊 拉取 {symbol}")

            for timeframe, limit in timeframes.items():
                count = await self.fetch_and_save_klines(symbol, timeframe, limit)
                total_count += count

                # 避免API限流
                await asyncio.sleep(0.1)

        elapsed = (datetime.now() - start_time).total_seconds()

        logger.info("\n" + "=" * 80)
        logger.info(f"✅ 拉取完成！")
        logger.info(f"   交易对: {len(self.symbols)} 个")
        logger.info(f"   K线数据: {total_count} 条")
        logger.info(f"   耗时: {elapsed:.1f} 秒")
        logger.info("=" * 80)

    async def close(self):
        """关闭交易所连接"""
        if self.exchange:
            await self.exchange.close()


async def main():
    """主函数"""
    fetcher = CoinFuturesKlineFetcher()

    try:
        await fetcher.fetch_all()
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"执行失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        await fetcher.close()


if __name__ == '__main__':
    asyncio.run(main())
