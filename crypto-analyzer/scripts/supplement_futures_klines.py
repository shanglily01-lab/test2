#!/usr/bin/env python3
"""
数据补充采集脚本 - 采集最近7天的合约K线数据
采集所有交易对的 5m/15m/1h 时间周期数据
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import yaml
import pymysql
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pandas as pd
import requests
from loguru import logger

# 确保控制台输出使用UTF-8编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


class FuturesKlinesSupplementer:
    """合约K线数据补充采集器"""

    def __init__(self, config_path='config.yaml'):
        """
        初始化

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        config_file = Path(config_path)
        if not config_file.exists():
            config_file = project_root / 'config.yaml'
        
        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取交易对列表
        self.symbols = self.config.get('symbols', [])
        print(f"📊 配置交易对: {len(self.symbols)} 个")
        print(f"   交易对列表: {', '.join(self.symbols)}")

        # 时间周期
        self.timeframes = ['5m', '15m', '1h']
        print(f"📅 时间周期: {', '.join(self.timeframes)}")

        # 数据库配置
        db_config = self.config.get('database', {}).get('mysql', {})
        self.db_config = {
            'host': db_config.get('host', 'localhost'),
            'port': db_config.get('port', 3306),
            'user': db_config.get('user', 'root'),
            'password': db_config.get('password', ''),
            'database': db_config.get('database', 'binance-data'),
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor
        }

        # Binance Futures API
        self.base_url = "https://fapi.binance.com"

        # 统计信息
        self.stats = {
            'total_requests': 0,
            'success_requests': 0,
            'failed_requests': 0,
            'total_klines': 0,
            'saved_klines': 0,
            'skipped_klines': 0
        }

    def connect_db(self):
        """连接数据库"""
        try:
            connection = pymysql.connect(**self.db_config)
            print("✅ 数据库连接成功")
            return connection
        except Exception as e:
            print(f"❌ 数据库连接失败: {e}")
            raise

    def calculate_klines_needed(self, timeframe: str, days: int) -> int:
        """
        计算指定天数需要的K线数量

        Args:
            timeframe: 时间周期 (5m, 15m, 1h等)
            days: 天数

        Returns:
            K线数量
        """
        timeframe_minutes = {
            '1m': 1,
            '5m': 5,
            '15m': 15,
            '30m': 30,
            '1h': 60,
            '4h': 240,
            '1d': 1440
        }
        minutes = timeframe_minutes.get(timeframe, 60)
        return int(days * 24 * 60 / minutes)

    async def fetch_klines_batch(
        self,
        symbol: str,
        timeframe: str,
        start_time: datetime,
        end_time: datetime,
        limit: int = 1500
    ) -> Optional[pd.DataFrame]:
        """
        批量获取K线数据

        Args:
            symbol: 交易对
            timeframe: 时间周期
            start_time: 开始时间
            end_time: 结束时间
            limit: 每次请求的最大数量（币安限制1500）

        Returns:
            DataFrame包含K线数据
        """
        try:
            binance_symbol = symbol.replace('/', '')
            url = f"{self.base_url}/fapi/v1/klines"
            
            all_klines = []
            current_start = start_time

            batch_num = 0
            while current_start < end_time:
                batch_num += 1
                params = {
                    'symbol': binance_symbol,
                    'interval': timeframe,
                    'startTime': int(current_start.timestamp() * 1000),
                    'endTime': int(end_time.timestamp() * 1000),
                    'limit': limit
                }

                self.stats['total_requests'] += 1
                print(f"    📡 请求批次 {batch_num}: {current_start.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')}", end='\r')
                sys.stdout.flush()
                
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(requests.get, url, params=params, timeout=30),
                        timeout=35  # 总超时35秒
                    )
                except asyncio.TimeoutError:
                    logger.error(f"获取 {symbol} {timeframe} K线超时（批次 {batch_num}）")
                    self.stats['failed_requests'] += 1
                    break
                except Exception as e:
                    logger.error(f"获取 {symbol} {timeframe} K线请求异常: {e}")
                    self.stats['failed_requests'] += 1
                    break

                if response.status_code != 200:
                    error_msg = response.text
                    logger.error(f"获取 {symbol} {timeframe} K线失败: HTTP {response.status_code} - {error_msg}")
                    self.stats['failed_requests'] += 1
                    return None

                klines = response.json()
                
                if not klines:
                    break

                # 转换为DataFrame
                df = pd.DataFrame(klines, columns=[
                    'open_time', 'open', 'high', 'low', 'close', 'volume',
                    'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
                    'taker_buy_quote_volume', 'ignore'
                ])

                # 选择需要的列并转换类型
                df = df[['open_time', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades', 'close_time']].copy()
                df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
                df['open'] = df['open'].astype(float)
                df['high'] = df['high'].astype(float)
                df['low'] = df['low'].astype(float)
                df['close'] = df['close'].astype(float)
                df['volume'] = df['volume'].astype(float)
                df['quote_volume'] = df['quote_volume'].astype(float)
                df['trades'] = df['trades'].astype(int)
                df['close_time'] = df['close_time'].astype(int)

                all_klines.append(df)
                print(f"    ✅ 批次 {batch_num} 获取到 {len(klines)} 条K线（累计: {sum(len(k) for k in all_klines)} 条）")
                sys.stdout.flush()

                # 如果返回的数据少于limit，说明已经获取完所有数据
                if len(klines) < limit:
                    break

                # 更新开始时间为最后一条K线的结束时间 + 1毫秒
                last_close_time = df['close_time'].iloc[-1]
                current_start = pd.to_datetime(last_close_time, unit='ms') + pd.Timedelta(milliseconds=1)

                # 避免无限循环
                if len(all_klines) > 200:  # 最多200批（14天5m数据约需要200批）
                    logger.warning(f"获取 {symbol} {timeframe} K线数据过多，停止获取")
                    break

                # 添加延迟，避免API限流
                await asyncio.sleep(0.2)

            if not all_klines:
                return None

            # 合并所有批次的数据
            result_df = pd.concat(all_klines, ignore_index=True)
            # 去重（按open_time）
            result_df = result_df.drop_duplicates(subset=['open_time'], keep='last')
            # 排序
            result_df = result_df.sort_values('open_time').reset_index(drop=True)

            # 添加元数据
            result_df['symbol'] = symbol
            result_df['exchange'] = 'binance_futures'
            result_df['timeframe'] = timeframe

            self.stats['success_requests'] += 1
            return result_df

        except Exception as e:
            logger.error(f"获取 {symbol} {timeframe} K线失败: {e}")
            self.stats['failed_requests'] += 1
            return None

    def save_klines_to_db(self, connection, df: pd.DataFrame, symbol: str, timeframe: str) -> int:
        """
        保存K线数据到数据库（分批处理，避免一次性插入太多数据）

        Args:
            connection: 数据库连接
            df: K线数据DataFrame
            symbol: 交易对
            timeframe: 时间周期

        Returns:
            保存的数据条数
        """
        if df is None or len(df) == 0:
            return 0

        cursor = connection.cursor()
        saved_count = 0
        batch_size = 500  # 每批500条，避免一次性插入太多

        try:
            insert_sql = """
            INSERT INTO kline_data
                (symbol, exchange, timeframe, open_time, close_time, timestamp, 
                 open_price, high_price, low_price, close_price, volume, quote_volume, number_of_trades)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open_price = VALUES(open_price),
                high_price = VALUES(high_price),
                low_price = VALUES(low_price),
                close_price = VALUES(close_price),
                volume = VALUES(volume),
                quote_volume = VALUES(quote_volume),
                number_of_trades = VALUES(number_of_trades),
                close_time = VALUES(close_time)
            """

            total_rows = len(df)
            print(f"  💾 开始保存 {total_rows} 条数据到数据库（分批处理，每批 {batch_size} 条）...")
            sys.stdout.flush()

            # 分批处理
            for i in range(0, total_rows, batch_size):
                batch_df = df.iloc[i:i + batch_size]
                values = []
                
                for _, row in batch_df.iterrows():
                    values.append((
                        row['symbol'],
                        row['exchange'],
                        row['timeframe'],
                        int(row['open_time']),
                        int(row['close_time']),
                        row['timestamp'],
                        float(row['open']),
                        float(row['high']),
                        float(row['low']),
                        float(row['close']),
                        float(row['volume']),
                        float(row['quote_volume']),
                        int(row['trades'])
                    ))

                # 批量插入当前批次
                cursor.executemany(insert_sql, values)
                connection.commit()
                batch_saved = cursor.rowcount
                saved_count += batch_saved

                # 显示进度
                progress = min(i + batch_size, total_rows)
                print(f"  📊 进度: {progress}/{total_rows} ({progress*100//total_rows}%) - 已保存 {saved_count} 条", end='\r')
                sys.stdout.flush()

            print()  # 换行
            self.stats['saved_klines'] += saved_count
            self.stats['total_klines'] += len(df)

        except Exception as e:
            connection.rollback()
            logger.error(f"保存 {symbol} {timeframe} K线数据到数据库失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            cursor.close()

        return saved_count

    async def supplement_symbol_timeframe(
        self,
        connection,
        symbol: str,
        timeframe: str,
        days: int = 7
    ):
        """
        补充单个交易对单个时间周期的数据

        Args:
            connection: 数据库连接
            symbol: 交易对
            timeframe: 时间周期
            days: 天数
        """
        try:
            # 计算时间范围
            end_time = datetime.now()
            start_time = end_time - timedelta(days=days)

            print(f"\n📥 采集 {symbol} {timeframe} (最近{days}天: {start_time.strftime('%Y-%m-%d %H:%M:%S')} ~ {end_time.strftime('%Y-%m-%d %H:%M:%S')})")
            sys.stdout.flush()

            # 获取K线数据
            df = await self.fetch_klines_batch(symbol, timeframe, start_time, end_time)

            if df is None or len(df) == 0:
                print(f"  ⚠️  未获取到数据")
                return

            print(f"  ✅ 获取到 {len(df)} 条K线数据")

            # 保存到数据库
            saved_count = self.save_klines_to_db(connection, df, symbol, timeframe)
            
            if saved_count > 0:
                print(f"  💾 保存 {saved_count} 条数据到数据库")
            else:
                print(f"  ⚠️  保存失败或数据已存在")

        except Exception as e:
            logger.error(f"补充 {symbol} {timeframe} 数据失败: {e}")
            import traceback
            traceback.print_exc()

    async def run(self, days: int = 7):
        """
        运行补充采集

        Args:
            days: 采集最近多少天的数据，默认7天
        """
        print("=" * 80)
        print("🚀 开始补充采集合约K线数据")
        print("=" * 80)
        print(f"📅 时间范围: 最近 {days} 天")
        print(f"📊 交易对数量: {len(self.symbols)}")
        print(f"⏱️  时间周期: {len(self.timeframes)} 个")
        print(f"📈 预计任务数: {len(self.symbols) * len(self.timeframes)}")
        print("=" * 80)

        connection = self.connect_db()

        try:
            total_tasks = len(self.symbols) * len(self.timeframes)
            current_task = 0

            for symbol in self.symbols:
                for timeframe in self.timeframes:
                    current_task += 1
                    print(f"\n[{current_task}/{total_tasks}] 处理 {symbol} {timeframe}")

                    await self.supplement_symbol_timeframe(connection, symbol, timeframe, days)

                    # 添加延迟，避免API限流
                    await asyncio.sleep(0.5)

            # 打印统计信息
            print("\n" + "=" * 80)
            print("📊 采集统计")
            print("=" * 80)
            print(f"总请求数: {self.stats['total_requests']}")
            print(f"成功请求: {self.stats['success_requests']}")
            print(f"失败请求: {self.stats['failed_requests']}")
            print(f"获取K线总数: {self.stats['total_klines']}")
            print(f"保存K线数: {self.stats['saved_klines']}")
            print(f"跳过K线数: {self.stats['skipped_klines']}")
            print("=" * 80)
            print("✅ 补充采集完成！")

        except Exception as e:
            logger.error(f"补充采集过程出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            connection.close()
            print("🔌 数据库连接已关闭")


async def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='补充采集最近N天的合约K线数据')
    parser.add_argument(
        '--days',
        type=int,
        default=7,
        help='采集最近多少天的数据（默认: 7）'
    )
    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='配置文件路径（默认: config.yaml）'
    )

    args = parser.parse_args()

    supplementer = FuturesKlinesSupplementer(config_path=args.config)
    await supplementer.run(days=args.days)


if __name__ == '__main__':
    asyncio.run(main())

