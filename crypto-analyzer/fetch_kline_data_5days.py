#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取指定交易对最近5天的K线数据 (5m/15m/1h/1d)
"""
import os
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import pymysql
import ccxt
from loguru import logger

# 目标交易对
TARGET_SYMBOLS = [
    'CHZ/USDT', 'BCH/USDT', 'DASH/USDT', 'LINK/USDT', 'ETC/USDT',
    'XLM/USDT', 'ADA/USDT', 'XTZ/USDT', 'ALGO/USDT', 'ZRX/USDT',
    'KAVA/USDT', 'DOT/USDT', 'ZIL/USDT', 'COMP/USDT', 'TRB/USDT', 'UNI/USDT'
]

# 时间周期
TIMEFRAMES = ['5m', '15m', '1h', '1d']

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4',
}


class KlineDataFetcher:
    """K线数据获取器"""

    def __init__(self, db_config: dict):
        """初始化"""
        self.db_config = db_config
        self.connection = None

        # 初始化Binance API
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # 合约
            }
        })

        logger.info("✅ K线数据获取器初始化完成")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(**self.db_config)
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(**self.db_config)
        return self.connection

    def ensure_table(self):
        """确保kline_data表存在（实际表已存在，跳过）"""
        logger.info("✅ 使用现有kline_data表")

    def fetch_klines(self, symbol: str, timeframe: str, days: int = 5) -> list:
        """
        从Binance获取K线数据

        Args:
            symbol: 交易对 (如 BTC/USDT)
            timeframe: 时间周期 (5m/15m/1h/1d)
            days: 获取最近N天的数据

        Returns:
            K线数据列表
        """
        try:
            # 计算起始时间
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)

            # 转换为毫秒时间戳
            since = int(start_time.timestamp() * 1000)

            logger.info(f"📥 获取 {symbol} {timeframe} K线数据 (最近{days}天)")

            # 获取K线数据
            ohlcv = self.exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=since,
                limit=1000
            )

            logger.info(f"   获取到 {len(ohlcv)} 根K线")
            return ohlcv

        except Exception as e:
            logger.error(f"❌ 获取 {symbol} {timeframe} 失败: {e}")
            return []

    def save_klines(self, symbol: str, timeframe: str, klines: list) -> int:
        """
        保存K线数据到数据库

        Args:
            symbol: 交易对
            timeframe: 时间周期
            klines: K线数据 [[timestamp, open, high, low, close, volume], ...]

        Returns:
            插入的数量
        """
        if not klines:
            return 0

        conn = self._get_connection()
        cursor = conn.cursor()

        inserted = 0
        updated = 0

        for kline in klines:
            open_time_ms, open_price, high, low, close, volume = kline

            # Convert milliseconds to datetime
            timestamp_dt = datetime.fromtimestamp(open_time_ms / 1000)

            try:
                cursor.execute("""
                    INSERT INTO kline_data
                    (symbol, exchange, timeframe, open_time, timestamp, open_price, high_price, low_price, close_price, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        open_price = VALUES(open_price),
                        high_price = VALUES(high_price),
                        low_price = VALUES(low_price),
                        close_price = VALUES(close_price),
                        volume = VALUES(volume)
                """, (symbol, 'binance_futures', timeframe, open_time_ms, timestamp_dt, open_price, high, low, close, volume))

                if cursor.rowcount == 1:
                    inserted += 1
                elif cursor.rowcount == 2:
                    updated += 1

            except Exception as e:
                logger.error(f"插入数据失败: {e}")
                continue

        conn.commit()
        cursor.close()

        logger.info(f"   💾 保存完成: 新增{inserted}条, 更新{updated}条")
        return inserted

    def fetch_and_save(self, symbol: str, timeframe: str, days: int = 5):
        """获取并保存K线数据"""
        klines = self.fetch_klines(symbol, timeframe, days)
        if klines:
            self.save_klines(symbol, timeframe, klines)

    def close(self):
        """关闭连接"""
        if self.connection and self.connection.open:
            self.connection.close()


def main():
    """主函数"""
    print("=" * 100)
    print("获取K线数据 - 最近5天")
    print("=" * 100)
    print(f"交易对数量: {len(TARGET_SYMBOLS)}")
    print(f"时间周期: {', '.join(TIMEFRAMES)}")
    print(f"数据范围: 最近5天")
    print("=" * 100)

    # 初始化
    fetcher = KlineDataFetcher(DB_CONFIG)
    fetcher.ensure_table()

    total_tasks = len(TARGET_SYMBOLS) * len(TIMEFRAMES)
    current_task = 0

    print(f"\n开始获取数据... (共{total_tasks}个任务)\n")

    # 统计
    stats = {
        'success': 0,
        'failed': 0,
        'total_klines': 0
    }

    # 遍历所有交易对和时间周期
    for symbol in TARGET_SYMBOLS:
        print(f"\n{'=' * 100}")
        print(f"交易对: {symbol}")
        print('=' * 100)

        for timeframe in TIMEFRAMES:
            current_task += 1
            print(f"\n[{current_task}/{total_tasks}] {symbol} {timeframe}")

            try:
                klines = fetcher.fetch_klines(symbol, timeframe, days=5)
                if klines:
                    count = fetcher.save_klines(symbol, timeframe, klines)
                    stats['success'] += 1
                    stats['total_klines'] += len(klines)
                else:
                    stats['failed'] += 1

                # 避免请求过快
                import time
                time.sleep(0.5)

            except Exception as e:
                logger.error(f"❌ 处理失败: {e}")
                stats['failed'] += 1

    # 关闭连接
    fetcher.close()

    # 打印统计
    print("\n" + "=" * 100)
    print("【统计结果】")
    print("=" * 100)
    print(f"成功任务: {stats['success']}/{total_tasks}")
    print(f"失败任务: {stats['failed']}/{total_tasks}")
    print(f"获取K线总数: {stats['total_klines']}")
    print(f"成功率: {stats['success']/total_tasks*100:.1f}%")
    print("=" * 100)

    # 验证数据
    print("\n【数据验证】")
    print("=" * 100)

    conn = pymysql.connect(**DB_CONFIG)
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    for symbol in TARGET_SYMBOLS:
        counts = {}
        for tf in TIMEFRAMES:
            cursor.execute("""
                SELECT COUNT(*) as cnt
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                AND timestamp >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 5 DAY)) * 1000
            """, (symbol, tf))
            result = cursor.fetchone()
            counts[tf] = result['cnt']

        print(f"{symbol:15s} | 5m:{counts['5m']:4d} | 15m:{counts['15m']:4d} | 1h:{counts['1h']:3d} | 1d:{counts['1d']:2d}")

    cursor.close()
    conn.close()

    print("=" * 100)
    print("✅ 所有任务完成！")
    print("=" * 100)


if __name__ == '__main__':
    main()
