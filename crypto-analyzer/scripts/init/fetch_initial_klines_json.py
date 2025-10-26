#!/usr/bin/env python3
"""
初始数据采集脚本 - 获取最近300条1小时K线数据 (JSON版本)
Fetch Initial K-line Data - Get 300 1-hour candles from Binance (JSON output)

如果数据库连接有问题，此脚本将数据保存为 JSON 文件
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import yaml
import json
import requests
from datetime import datetime


class InitialKlinesFetcher:
    """初始K线数据获取器 (JSON版本)"""

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

        # Binance API 配置
        self.binance_api_base = 'https://api.binance.com'

        # 输出目录
        self.output_dir = Path('kline_data_export')
        self.output_dir.mkdir(exist_ok=True)

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
        从 Binance 获取K线数据 (使用 REST API)

        Args:
            symbol: 交易对 (如 BTC/USDT)
            timeframe: 时间周期 (1h)
            limit: 获取数量 (300)

        Returns:
            List[Dict]: K线数据列表
        """
        try:
            print(f"📊 正在获取 {symbol} 的 {limit} 条 {timeframe} K线数据...")

            # 转换币种格式
            binance_symbol = self.symbol_to_binance_format(symbol)

            # 构建 API 请求
            url = f"{self.binance_api_base}/api/v3/klines"
            params = {
                'symbol': binance_symbol,
                'interval': timeframe,
                'limit': limit
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
                    'timestamp': datetime.fromtimestamp(timestamp_ms / 1000).isoformat(),
                    'open': open_price,
                    'high': high,
                    'low': low,
                    'close': close,
                    'volume': volume
                })

            print(f"  ✅ 成功获取 {len(klines)} 条数据")
            print(f"  时间范围: {klines[0]['timestamp']} ~ {klines[-1]['timestamp']}")
            print(f"  最新价格: ${klines[-1]['close']:,.2f}")

            return klines

        except requests.exceptions.RequestException as e:
            print(f"  ❌ 获取 {symbol} K线失败 (网络错误): {e}")
            return []
        except Exception as e:
            print(f"  ❌ 获取 {symbol} K线失败: {e}")
            return []

    def save_klines_json(self, symbol: str, klines: list):
        """
        保存K线数据为JSON文件

        Args:
            symbol: 交易对
            klines: K线数据列表

        Returns:
            str: 保存的文件路径
        """
        if not klines:
            return None

        try:
            # 文件名: BTC_USDT_1h_300.json
            filename = f"{symbol.replace('/', '_')}_1h_{len(klines)}.json"
            filepath = self.output_dir / filename

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(klines, f, indent=2, ensure_ascii=False)

            print(f"  💾 保存到文件: {filepath}")
            return str(filepath)

        except Exception as e:
            print(f"  ❌ 保存文件失败: {e}")
            return None

    def fetch_all_symbols(self):
        """获取所有币种的K线数据"""
        print("\n" + "=" * 80)
        print("🚀 开始批量获取K线数据")
        print("=" * 80)

        total_fetched = 0
        saved_files = []

        for i, symbol in enumerate(self.symbols, 1):
            print(f"\n[{i}/{len(self.symbols)}] 处理 {symbol}")

            # 获取K线数据
            klines = self.fetch_klines(symbol, timeframe='1h', limit=300)

            if klines:
                total_fetched += len(klines)

                # 保存为JSON文件
                filepath = self.save_klines_json(symbol, klines)
                if filepath:
                    saved_files.append(filepath)

            # 延迟，避免请求过快
            import time
            if i < len(self.symbols):
                time.sleep(0.5)

        print("\n" + "=" * 80)
        print("✅ 数据采集完成")
        print("=" * 80)
        print(f"总共获取: {total_fetched} 条")
        print(f"保存文件数: {len(saved_files)} 个")
        print("=" * 80 + "\n")

        return saved_files

    def generate_import_sql(self, saved_files: list):
        """
        生成导入SQL脚本

        Args:
            saved_files: 已保存的JSON文件列表
        """
        if not saved_files:
            return

        sql_file = self.output_dir / 'import_klines.sql'

        print("\n" + "=" * 80)
        print("📝 生成SQL导入脚本...")
        print("=" * 80 + "\n")

        with open(sql_file, 'w', encoding='utf-8') as f:
            f.write("-- K线数据导入脚本\n")
            f.write("-- 自动生成\n\n")

            f.write("USE `binance-data`;\n\n")

            f.write("-- 创建临时表\n")
            f.write("DROP TABLE IF EXISTS kline_data_temp;\n\n")

            for json_file in saved_files:
                # 读取JSON数据
                with open(json_file, 'r', encoding='utf-8') as jf:
                    klines = json.load(jf)

                if klines:
                    f.write(f"-- 导入 {klines[0]['symbol']} 数据\n")
                    f.write("INSERT INTO kline_data\n")
                    f.write("  (symbol, exchange, timeframe, open_time, timestamp, open, high, low, close, volume)\n")
                    f.write("VALUES\n")

                    for idx, kline in enumerate(klines):
                        timestamp_str = kline['timestamp'].replace('T', ' ')

                        f.write(f"  ('{kline['symbol']}', '{kline['exchange']}', '{kline['timeframe']}', "
                               f"{kline['open_time']}, '{timestamp_str}', "
                               f"{kline['open']}, {kline['high']}, {kline['low']}, "
                               f"{kline['close']}, {kline['volume']})")

                        if idx < len(klines) - 1:
                            f.write(",\n")
                        else:
                            f.write("\n")

                    f.write("ON DUPLICATE KEY UPDATE\n")
                    f.write("  open = VALUES(open),\n")
                    f.write("  high = VALUES(high),\n")
                    f.write("  low = VALUES(low),\n")
                    f.write("  close = VALUES(close),\n")
                    f.write("  volume = VALUES(volume);\n\n")

        print(f"✅ SQL脚本已生成: {sql_file}\n")
        print("📋 导入方法:")
        print(f"  mysql -u root -p binance-data < {sql_file}")
        print("\n" + "=" * 80)

    def run(self):
        """运行主流程"""
        try:
            # 1. 获取K线数据并保存为JSON
            saved_files = self.fetch_all_symbols()

            # 2. 生成SQL导入脚本
            self.generate_import_sql(saved_files)

            print("\n✅ 完成！")
            print(f"\n📁 数据文件位置: {self.output_dir.absolute()}")
            print(f"   - JSON 文件: {len(saved_files)} 个")
            print(f"   - SQL 脚本: import_klines.sql\n")

        except Exception as e:
            print(f"❌ 执行失败: {e}")
            import traceback
            traceback.print_exc()


def main():
    """主函数"""
    print("\n" + "=" * 80)
    print("初始K线数据采集脚本 (JSON版本)")
    print("功能: 从 Binance 获取最近 300 条 1 小时 K 线数据")
    print("输出: JSON 文件 + SQL 导入脚本")
    print("=" * 80 + "\n")

    fetcher = InitialKlinesFetcher(config_path='config.yaml')
    fetcher.run()


if __name__ == '__main__':
    main()
