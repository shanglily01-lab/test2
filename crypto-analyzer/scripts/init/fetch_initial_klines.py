#!/usr/bin/env python3
"""
初始数据采集脚本 - 获取最近300条1小时K线数据
Fetch Initial K-line Data - Get 300 1-hour candles from Binance
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
import pymysql
import requests
from datetime import datetime


class InitialKlinesFetcher:
    """初始K线数据获取器"""

    def __init__(self, config_path='config.yaml'):
        """
        初始化

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取币种列表
        self.symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT'])
        print(f"配置币种: {', '.join(self.symbols)}")

        # 数据库配置
        self.db_config = self.config['database']['mysql']
        self.connection = None
        self.cursor = None

        # Binance API 配置
        self.binance_api_base = 'https://api.binance.com'

    def connect_db(self):
        """连接数据库"""
        try:
            # 尝试连接数据库
            self.connection = pymysql.connect(**self.db_config)
            self.cursor = self.connection.cursor()
            print("✅ 数据库连接成功")
        except pymysql.err.OperationalError as e:
            print(f"❌ 数据库连接失败: {e}")
            print(f"\n💡 可能的原因:")
            print(f"  1. MySQL 服务未启动")
            print(f"  2. 数据库密码不正确")
            print(f"  3. 数据库 'binance-data' 不存在")
            print(f"  4. MySQL 未授权远程连接（Docker 容器）")
            print(f"\n🔧 解决方案:")
            print(f"  # 检查 MySQL 状态")
            print(f"  systemctl status mysql")
            print(f"  ")
            print(f"  # 创建数据库")
            print(f"  mysql -u root -p -e \"CREATE DATABASE IF NOT EXISTS \\\`binance-data\\\`;\"")
            print(f"  ")
            print(f"  # 授权远程连接（如果使用 Docker）")
            print(f"  mysql -u root -p -e \"GRANT ALL ON \\\`binance-data\\\`.* TO 'root'@'%' IDENTIFIED BY 'Tonny@1000';\"")
            print(f"  mysql -u root -p -e \"FLUSH PRIVILEGES;\"")
            raise
        except Exception as e:
            print(f"❌ 数据库连接失败: {e}")
            raise

    def close_db(self):
        """关闭数据库连接"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
        print("数据库连接已关闭")

    def create_table_if_not_exists(self):
        """创建 kline_data 表（如果不存在）"""
        try:
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS `kline_data` (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `symbol` VARCHAR(20) NOT NULL COMMENT '交易对，如 BTC/USDT',
                `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance' COMMENT '交易所',
                `timeframe` VARCHAR(10) NOT NULL COMMENT '时间周期 (1m, 5m, 1h, 1d)',
                `open_time` BIGINT NOT NULL COMMENT '开盘时间戳(毫秒)',
                `timestamp` TIMESTAMP NOT NULL COMMENT '时间戳',
                `open` DECIMAL(18, 8) NOT NULL COMMENT '开盘价',
                `high` DECIMAL(18, 8) NOT NULL COMMENT '最高价',
                `low` DECIMAL(18, 8) NOT NULL COMMENT '最低价',
                `close` DECIMAL(18, 8) NOT NULL COMMENT '收盘价',
                `volume` DECIMAL(20, 8) NOT NULL COMMENT '成交量',
                `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                -- 索引
                KEY `idx_symbol_timeframe_timestamp` (`symbol`, `timeframe`, `timestamp`),
                KEY `idx_timestamp` (`timestamp`),

                -- 唯一约束（防止重复）
                UNIQUE KEY `uk_symbol_exchange_timeframe_timestamp` (`symbol`, `exchange`, `timeframe`, `timestamp`)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='K线数据表';
            """

            self.cursor.execute(create_table_sql)
            self.connection.commit()
            print("✅ kline_data 表已确认/创建")

        except Exception as e:
            print(f"❌ 创建表失败: {e}")
            raise

    def symbol_to_binance_format(self, symbol: str) -> str:
        """
        转换币种格式
        BTC/USDT -> BTCUSDT

        Args:
            symbol: 交易对 (如 BTC/USDT)

        Returns:
            str: Binance 格式 (如 BTCUSDT)
        """
        return symbol.replace('/', '')

    def fetch_klines(self, symbol: str, timeframe: str = '1h', limit: int = 300):
        """
        从 Binance 合约API 获取K线数据 (使用 REST API)

        Args:
            symbol: 交易对 (如 BTC/USDT)
            timeframe: 时间周期 (1h)
            limit: 获取数量 (300)

        Returns:
            List[Dict]: K线数据列表
        """
        try:
            print(f"📊 正在获取 {symbol} 的 {limit} 条 {timeframe} 合约K线数据...")

            # 转换币种格式
            binance_symbol = self.symbol_to_binance_format(symbol)

            # 构建 API 请求 - 使用合约API
            url = "https://fapi.binance.com/fapi/v1/klines"
            params = {
                'symbol': binance_symbol,
                'interval': timeframe,
                'limit': min(limit, 1500)  # 合约API限制最大1500
            }

            # 发送请求
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()

            data = response.json()

            if not data:
                print(f"⚠️  {symbol} 没有返回数据")
                return []

            # 转换为字典格式
            klines = []
            for candle in data:
                timestamp_ms = candle[0]
                open_price = float(candle[1])
                high = float(candle[2])
                low = float(candle[3])
                close = float(candle[4])
                volume = float(candle[5])

                klines.append({
                    'symbol': symbol,
                    'exchange': 'binance',
                    'timeframe': timeframe,
                    'open_time': timestamp_ms,
                    'timestamp': datetime.fromtimestamp(timestamp_ms / 1000),
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'close': close,
                    'volume': volume
                })

            print(f"  ✅ 成功获取 {len(klines)} 条合约K线数据")
            print(f"  时间范围: {klines[0]['timestamp']} ~ {klines[-1]['timestamp']}")
            print(f"  最新价格: ${klines[-1]['close']:,.2f}")

            return klines

        except requests.exceptions.RequestException as e:
            print(f"  ❌ 获取 {symbol} 合约K线失败 (网络错误): {e}")
            return []
        except Exception as e:
            print(f"  ❌ 获取 {symbol} 合约K线失败: {e}")
            return []

    def save_klines(self, klines: list):
        """
        保存K线数据到数据库

        Args:
            klines: K线数据列表

        Returns:
            int: 保存的数据条数
        """
        if not klines:
            return 0

        try:
            insert_sql = """
            INSERT INTO kline_data
                (symbol, exchange, timeframe, open_time, timestamp, open_price, high_price, low_price, close_price, volume)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open_price = VALUES(open_price),
                high_price = VALUES(high_price),
                low_price = VALUES(low_price),
                close_price = VALUES(close_price),
                volume = VALUES(volume)
            """

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

            self.cursor.executemany(insert_sql, values)
            self.connection.commit()

            inserted_count = self.cursor.rowcount
            print(f"  💾 保存 {inserted_count} 条数据到数据库")

            return inserted_count

        except Exception as e:
            print(f"  ❌ 保存数据失败: {e}")
            self.connection.rollback()
            return 0

    def fetch_all_symbols(self):
        """获取所有币种的K线数据"""
        print("\n" + "=" * 80)
        print("🚀 开始批量获取K线数据")
        print("=" * 80)

        total_fetched = 0
        total_saved = 0

        for i, symbol in enumerate(self.symbols, 1):
            print(f"\n[{i}/{len(self.symbols)}] 处理 {symbol}")

            # 获取K线数据
            klines = self.fetch_klines(symbol, timeframe='1h', limit=300)

            if klines:
                total_fetched += len(klines)

                # 保存到数据库
                saved = self.save_klines(klines)
                total_saved += saved

            # 延迟，避免请求过快
            import time
            if i < len(self.symbols):
                time.sleep(0.5)

        print("\n" + "=" * 80)
        print("✅ 数据采集完成")
        print("=" * 80)
        print(f"总共获取: {total_fetched} 条")
        print(f"成功保存: {total_saved} 条")
        print("=" * 80 + "\n")

    def verify_data(self):
        """验证数据是否正确保存"""
        print("\n" + "=" * 80)
        print("🔍 验证数据...")
        print("=" * 80 + "\n")

        try:
            for symbol in self.symbols:
                self.cursor.execute("""
                    SELECT COUNT(*) as count,
                           MIN(timestamp) as earliest,
                           MAX(timestamp) as latest
                    FROM kline_data
                    WHERE symbol = %s AND timeframe = '1h'
                """, (symbol,))

                result = self.cursor.fetchone()
                count, earliest, latest = result

                if count > 0:
                    print(f"✅ {symbol:12s} | {count:3d} 条 | {earliest} ~ {latest}")
                else:
                    print(f"⚠️  {symbol:12s} | 没有数据")

            print("\n" + "=" * 80)

        except Exception as e:
            print(f"❌ 验证失败: {e}")

    def run(self):
        """运行主流程"""
        try:
            # 1. 连接数据库
            self.connect_db()

            # 2. 创建表
            self.create_table_if_not_exists()

            # 3. 获取并保存K线数据
            self.fetch_all_symbols()

            # 4. 验证数据
            self.verify_data()

        except Exception as e:
            print(f"❌ 执行失败: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # 5. 关闭数据库连接
            self.close_db()


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("初始K线数据采集脚本")
    print("功能: 从 Binance 获取最近 300 条 1 小时 K 线数据")
    print("=" * 80 + "\n")

    fetcher = InitialKlinesFetcher(config_path='config.yaml')
    fetcher.run()

    print("\n✅ 脚本执行完成！\n")
    print("下一步:")
    print("  1. 查看数据: python3 quick_check.py")
    print("  2. 查看详细分析: python3 check_confidence_breakdown.py")
    print("  3. 启动调度器: python3 app/scheduler.py")
    print("  4. 启动Web服务: python3 app/main.py\n")


if __name__ == '__main__':
    main()
