#!/usr/bin/env python3
"""
补采集合约K线数据脚本
Backfill K-line Data - 补采集最近N天的合约数据

功能:
- 支持多个时间周期: 5m, 15m, 1h, 1d
- 从 Binance 合约API获取历史数据
- 自动读取 config.yaml 中的交易对配置
- 智能去重，避免重复写入
- 进度显示和数据验证
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import yaml
import pymysql
import requests
import time
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dotenv import load_dotenv

# 加载 .env 文件
env_path = project_root / '.env'
if env_path.exists():
    load_dotenv(env_path)


class KlinesBackfillService:
    """合约K线数据补采集服务"""

    def __init__(self, config_path='config.yaml'):
        """
        初始化补采集服务

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        config_full_path = project_root / config_path
        with open(config_full_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取交易对列表（从配置文件读取）
        self.symbols = self.config.get('symbols', [])
        print(f"[OK] 从配置文件读取到 {len(self.symbols)} 个交易对")
        print(f"   交易对: {', '.join(self.symbols)}")

        # 数据库配置（处理环境变量和类型转换）
        import os
        db_config_raw = self.config['database']['mysql']
        self.db_config = {
            'host': self._parse_env_var(db_config_raw.get('host', 'localhost')),
            'port': int(self._parse_env_var(db_config_raw.get('port', '3306'))),
            'user': self._parse_env_var(db_config_raw.get('user', 'root')),
            'password': self._parse_env_var(db_config_raw.get('password', '')),
            'database': self._parse_env_var(db_config_raw.get('database', 'binance-data'))
        }
        self.connection = None
        self.cursor = None

        # Binance 合约API配置
        self.binance_futures_api_base = 'https://fapi.binance.com'

        # 时间周期配置（按从小到大排序，确保依赖关系）
        self.timeframes = ['5m', '15m', '1h', '1d']

        # 每个时间周期对应的Binance API间隔符号
        self.interval_map = {
            '5m': '5m',
            '15m': '15m',
            '1h': '1h',
            '1d': '1d'
        }

    def _parse_env_var(self, value: str) -> str:
        """
        解析环境变量格式的配置值

        例如: ${DB_HOST:localhost} -> 从环境变量 DB_HOST 读取，默认值为 localhost

        Args:
            value: 配置值（可能包含环境变量）

        Returns:
            str: 解析后的值
        """
        import os
        import re

        if not isinstance(value, str):
            return str(value)

        # 匹配 ${VAR_NAME:default_value} 格式
        pattern = r'\$\{([^:}]+):([^}]*)\}'
        match = re.match(pattern, value)

        if match:
            env_var_name = match.group(1)
            default_value = match.group(2)
            return os.environ.get(env_var_name, default_value)

        return value

    def connect_db(self):
        """连接数据库"""
        try:
            self.connection = pymysql.connect(
                host=self.db_config['host'],
                port=self.db_config['port'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False
            )
            self.cursor = self.connection.cursor()
            print(f"[OK] 数据库连接成功: {self.db_config['database']}@{self.db_config['host']}:{self.db_config['port']}")
        except Exception as e:
            print(f"[ERROR] 数据库连接失败: {e}")
            print(f"\n[INFO] 当前配置:")
            print(f"  Host: {self.db_config['host']}")
            print(f"  Port: {self.db_config['port']}")
            print(f"  User: {self.db_config['user']}")
            print(f"  Database: {self.db_config['database']}")
            print(f"\n[INFO] 请检查:")
            print(f"  1. MySQL 服务是否启动")
            print(f"  2. 数据库配置是否正确")
            print(f"  3. 数据库 '{self.db_config['database']}' 是否存在")
            raise

    def close_db(self):
        """关闭数据库连接"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        print("[OK] 数据库连接已关闭")

    def symbol_to_binance_format(self, symbol: str) -> str:
        """
        转换交易对格式
        BTC/USDT -> BTCUSDT

        Args:
            symbol: 交易对 (如 BTC/USDT)

        Returns:
            str: Binance 格式 (如 BTCUSDT)
        """
        return symbol.replace('/', '')

    def calculate_klines_needed(self, timeframe: str, days: int) -> int:
        """
        计算需要获取的K线数量

        Args:
            timeframe: 时间周期 (5m, 15m, 1h, 1d)
            days: 天数

        Returns:
            int: K线数量
        """
        minutes_per_day = 24 * 60

        if timeframe == '5m':
            return (minutes_per_day // 5) * days  # 288条/天
        elif timeframe == '15m':
            return (minutes_per_day // 15) * days  # 96条/天
        elif timeframe == '1h':
            return 24 * days  # 24条/天
        elif timeframe == '1d':
            return days  # 1条/天
        else:
            return 100  # 默认值

    def fetch_klines_from_binance(
        self,
        symbol: str,
        timeframe: str,
        days: int = 10
    ) -> List[Dict]:
        """
        从 Binance 合约API 批量获取K线数据

        Args:
            symbol: 交易对 (如 BTC/USDT)
            timeframe: 时间周期 (5m, 15m, 1h, 1d)
            days: 获取最近N天的数据

        Returns:
            List[Dict]: K线数据列表
        """
        try:
            binance_symbol = self.symbol_to_binance_format(symbol)
            interval = self.interval_map.get(timeframe, timeframe)

            # 计算需要获取的K线数量
            limit = self.calculate_klines_needed(timeframe, days)

            # Binance API单次最大返回1500条，需要分批获取
            max_limit_per_request = 1500
            all_klines = []

            # 计算需要请求的次数
            requests_needed = (limit + max_limit_per_request - 1) // max_limit_per_request

            print(f"   [FETCH] {symbol} {timeframe}: 需要获取约 {limit} 条数据 (分 {requests_needed} 次请求)")

            # 分批获取
            end_time = None  # 结束时间（从最新开始往前推）

            for batch in range(requests_needed):
                batch_limit = min(max_limit_per_request, limit - len(all_klines))

                if batch_limit <= 0:
                    break

                # 构建请求参数
                url = f"{self.binance_futures_api_base}/fapi/v1/klines"
                params = {
                    'symbol': binance_symbol,
                    'interval': interval,
                    'limit': batch_limit
                }

                # 如果不是第一次请求，使用 endTime 参数获取更早的数据
                if end_time:
                    params['endTime'] = end_time

                # 发送请求
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()

                data = response.json()

                if not data:
                    break

                # 转换为字典格式
                batch_klines = []
                for candle in data:
                    timestamp_ms = candle[0]
                    batch_klines.append({
                        'symbol': symbol,
                        'exchange': 'binance_futures',
                        'timeframe': timeframe,
                        'open_time': timestamp_ms,
                        'timestamp': datetime.fromtimestamp(timestamp_ms / 1000),
                        'open': float(candle[1]),
                        'high': float(candle[2]),
                        'low': float(candle[3]),
                        'close': float(candle[4]),
                        'volume': float(candle[5])
                    })

                # 添加到总列表（倒序添加，因为我们是从最新往前获取）
                all_klines = batch_klines + all_klines

                # 更新 endTime 为当前批次最早的时间戳（减1毫秒避免重复）
                if batch_klines:
                    end_time = batch_klines[0]['open_time'] - 1

                print(f"      批次 {batch + 1}/{requests_needed}: 获取 {len(batch_klines)} 条，总计 {len(all_klines)} 条")

                # 避免请求过快
                time.sleep(0.2)

            if all_klines:
                print(f"   [OK] 成功获取 {len(all_klines)} 条数据")
                print(f"      时间范围: {all_klines[0]['timestamp']} ~ {all_klines[-1]['timestamp']}")
                print(f"      最新价格: ${all_klines[-1]['close']:,.2f}")

            return all_klines

        except requests.exceptions.RequestException as e:
            print(f"   [ERROR] 网络请求失败: {e}")
            return []
        except Exception as e:
            print(f"   [ERROR] 获取数据失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    def save_klines_to_db(self, klines: List[Dict]) -> int:
        """
        保存K线数据到数据库（使用 ON DUPLICATE KEY UPDATE 去重）

        Args:
            klines: K线数据列表

        Returns:
            int: 实际插入的新数据条数
        """
        if not klines:
            return 0

        try:
            insert_sql = """
            INSERT INTO kline_data
                (symbol, exchange, timeframe, open_time, timestamp,
                 open_price, high_price, low_price, close_price, volume)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open_price = VALUES(open_price),
                high_price = VALUES(high_price),
                low_price = VALUES(low_price),
                close_price = VALUES(close_price),
                volume = VALUES(volume)
            """

            # 准备批量插入的数据
            values = []
            for kline in klines:
                values.append((
                    kline['symbol'],
                    kline['exchange'],
                    kline['timeframe'],
                    kline['open_time'],
                    kline['timestamp'],
                    kline['open'],
                    kline['high'],
                    kline['low'],
                    kline['close'],
                    kline['volume']
                ))

            # 批量插入
            self.cursor.executemany(insert_sql, values)
            self.connection.commit()

            # 获取影响的行数（新插入的行数）
            affected_rows = self.cursor.rowcount

            print(f"   [SAVE] 保存完成: {affected_rows} 条数据写入数据库")

            return affected_rows

        except Exception as e:
            print(f"   [ERROR] 保存数据失败: {e}")
            self.connection.rollback()
            import traceback
            traceback.print_exc()
            return 0

    def backfill_all_symbols(self, days: int = 10):
        """
        补采集所有交易对的K线数据

        Args:
            days: 补采集最近N天的数据
        """
        print("\n" + "=" * 80)
        print(f"[START] 开始补采集最近 {days} 天的合约K线数据")
        print(f"   时间周期: {', '.join(self.timeframes)}")
        print(f"   交易对数量: {len(self.symbols)}")
        print("=" * 80 + "\n")

        total_fetched = 0
        total_saved = 0

        start_time = time.time()

        # 遍历每个交易对
        for idx, symbol in enumerate(self.symbols, 1):
            print(f"\n[{idx}/{len(self.symbols)}] 处理交易对: {symbol}")
            print("-" * 80)

            # 遍历每个时间周期
            for timeframe in self.timeframes:
                print(f"\n  [TIMEFRAME] {timeframe}")

                # 从 Binance 获取数据
                klines = self.fetch_klines_from_binance(symbol, timeframe, days)

                if klines:
                    total_fetched += len(klines)

                    # 保存到数据库
                    saved_count = self.save_klines_to_db(klines)
                    total_saved += saved_count

                # 避免请求过快
                time.sleep(0.3)

            # 每个交易对之间稍长延迟
            if idx < len(self.symbols):
                time.sleep(0.5)

        # 统计耗时
        elapsed_time = time.time() - start_time

        print("\n" + "=" * 80)
        print("[SUCCESS] 补采集完成")
        print("=" * 80)
        print(f"总耗时: {elapsed_time:.2f} 秒")
        print(f"总共获取: {total_fetched} 条K线数据")
        print(f"成功保存: {total_saved} 条新数据")
        print(f"平均速度: {total_fetched / elapsed_time:.2f} 条/秒" if elapsed_time > 0 else "N/A")
        print("=" * 80 + "\n")

    def verify_data(self):
        """验证数据是否正确保存"""
        print("\n" + "=" * 80)
        print("[VERIFY] 验证数据完整性...")
        print("=" * 80 + "\n")

        try:
            for symbol in self.symbols:
                print(f"[SYMBOL] {symbol}:")

                for timeframe in self.timeframes:
                    self.cursor.execute("""
                        SELECT
                            COUNT(*) as count,
                            MIN(timestamp) as earliest,
                            MAX(timestamp) as latest
                        FROM kline_data
                        WHERE symbol = %s
                          AND timeframe = %s
                          AND exchange = 'binance_futures'
                    """, (symbol, timeframe))

                    result = self.cursor.fetchone()

                    if result and result['count'] > 0:
                        count = result['count']
                        earliest = result['earliest']
                        latest = result['latest']

                        # 计算数据覆盖的天数
                        if earliest and latest:
                            days_covered = (latest - earliest).days + 1
                        else:
                            days_covered = 0

                        print(f"   [OK] {timeframe:4s} | {count:5d} 条 | 覆盖 {days_covered:3d} 天 | {earliest} ~ {latest}")
                    else:
                        print(f"   [WARN] {timeframe:4s} | 没有数据")

                print()

            print("=" * 80 + "\n")

        except Exception as e:
            print(f"[ERROR] 验证失败: {e}")
            import traceback
            traceback.print_exc()

    def run(self, days: int = 10):
        """
        运行补采集流程

        Args:
            days: 补采集最近N天的数据
        """
        try:
            # 1. 连接数据库
            self.connect_db()

            # 2. 补采集数据
            self.backfill_all_symbols(days=days)

            # 3. 验证数据
            self.verify_data()

        except Exception as e:
            print(f"[ERROR] 执行失败: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # 4. 关闭数据库连接
            self.close_db()


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(
        description='补采集合约K线历史数据',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 补采集最近10天数据（默认）
  python scripts/init/backfill_klines.py

  # 补采集最近30天数据
  python scripts/init/backfill_klines.py --days 30

  # 补采集最近7天数据
  python scripts/init/backfill_klines.py --days 7
        """
    )

    parser.add_argument(
        '--days',
        type=int,
        default=10,
        help='补采集最近N天的数据（默认10天）'
    )

    parser.add_argument(
        '--config',
        type=str,
        default='config.yaml',
        help='配置文件路径（默认config.yaml）'
    )

    args = parser.parse_args()

    print("\n" + "=" * 80)
    print("[INFO] 合约K线数据补采集脚本")
    print(f"   补采集天数: 最近 {args.days} 天")
    print(f"   时间周期: 5m, 15m, 1h, 1d")
    print(f"   数据源: Binance Futures API")
    print("=" * 80 + "\n")

    # 创建服务并运行
    service = KlinesBackfillService(config_path=args.config)
    service.run(days=args.days)

    print("\n[DONE] 脚本执行完成！\n")
    print("[INFO] 提示:")
    print("  - 数据已保存到 kline_data 表")
    print("  - exchange 字段为 'binance_futures'")
    print("  - 自动去重，重复数据会被更新而不是新增")
    print("  - 可以多次运行此脚本补全缺失的数据\n")


if __name__ == '__main__':
    main()
