"""
回测数据采集器
采集币安合约48小时历史K线和价格数据，存入独立的回测数据表

用法:
    python scripts/backtest/backtest_data_collector.py --hours 48 --symbols BTC/USDT,ETH/USDT
"""

import sys
import argparse
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pymysql
import yaml
import ccxt.async_support as ccxt

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 确保控制台输出使用UTF-8编码 (Windows兼容)
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


def load_config() -> Dict:
    """加载配置文件"""
    config_path = project_root / 'config.yaml'
    if not config_path.exists():
        raise FileNotFoundError(f"配置文件不存在: {config_path}")

    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


class BacktestDataCollector:
    """回测数据采集器"""

    def __init__(self, config: Dict):
        self.config = config
        self.db_config = config.get('database', {}).get('mysql', {})
        self.exchange = None
        self.connection = None

    async def init_exchange(self):
        """初始化交易所连接（无需API Key，使用公开接口获取K线）"""
        # K线数据是公开的，不需要API Key认证
        # 这样可以避免IP白名单限制问题
        self.exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',  # 使用合约市场
            }
        })
        print("✅ 交易所连接初始化完成 (合约模式, 公开接口)")

    def init_db(self):
        """初始化数据库连接"""
        self.connection = pymysql.connect(
            host=self.db_config.get('host', '13.212.252.171'),
            port=self.db_config.get('port', 3306),
            user=self.db_config.get('user', 'admin'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )
        print("✅ 数据库连接初始化完成")

    def create_tables(self):
        """创建回测数据表"""
        cursor = self.connection.cursor()

        # 回测K线数据表
        create_kline_sql = """
        CREATE TABLE IF NOT EXISTS `backtest_kline_data` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `session_id` VARCHAR(50) NOT NULL COMMENT '回测会话ID',
            `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
            `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance' COMMENT '交易所',
            `market_type` VARCHAR(20) NOT NULL DEFAULT 'futures' COMMENT '市场类型: spot/futures',
            `timeframe` VARCHAR(10) NOT NULL COMMENT '时间周期',
            `open_time` BIGINT NOT NULL COMMENT '开盘时间戳(毫秒)',
            `close_time` BIGINT NULL COMMENT '收盘时间戳(毫秒)',
            `timestamp` DATETIME NOT NULL COMMENT '时间',
            `open_price` DECIMAL(18, 8) NOT NULL COMMENT '开盘价',
            `high_price` DECIMAL(18, 8) NOT NULL COMMENT '最高价',
            `low_price` DECIMAL(18, 8) NOT NULL COMMENT '最低价',
            `close_price` DECIMAL(18, 8) NOT NULL COMMENT '收盘价',
            `volume` DECIMAL(20, 8) NULL COMMENT '成交量',
            `quote_volume` DECIMAL(24, 2) NULL COMMENT '成交额',
            `number_of_trades` INT NULL COMMENT '成交笔数',
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,

            KEY `idx_session_symbol_tf_time` (`session_id`, `symbol`, `timeframe`, `timestamp`),
            KEY `idx_session_time` (`session_id`, `timestamp`),
            UNIQUE KEY `uk_session_symbol_tf_time` (`session_id`, `symbol`, `timeframe`, `open_time`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='回测K线数据表';
        """
        cursor.execute(create_kline_sql)

        # 回测价格数据表（更高频率，用于模拟实时价格）
        create_price_sql = """
        CREATE TABLE IF NOT EXISTS `backtest_price_data` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `session_id` VARCHAR(50) NOT NULL COMMENT '回测会话ID',
            `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
            `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance' COMMENT '交易所',
            `market_type` VARCHAR(20) NOT NULL DEFAULT 'futures' COMMENT '市场类型',
            `timestamp` DATETIME NOT NULL COMMENT '时间',
            `price` DECIMAL(18, 8) NOT NULL COMMENT '当前价格',
            `open_price` DECIMAL(18, 8) NULL COMMENT '开盘价',
            `high_price` DECIMAL(18, 8) NULL COMMENT '最高价',
            `low_price` DECIMAL(18, 8) NULL COMMENT '最低价',
            `close_price` DECIMAL(18, 8) NULL COMMENT '收盘价',
            `volume` DECIMAL(20, 8) NULL COMMENT '成交量',
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,

            KEY `idx_session_symbol_time` (`session_id`, `symbol`, `timestamp`),
            KEY `idx_session_time` (`session_id`, `timestamp`),
            UNIQUE KEY `uk_session_symbol_time` (`session_id`, `symbol`, `timestamp`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='回测价格数据表';
        """
        cursor.execute(create_price_sql)

        # 回测会话表
        create_session_sql = """
        CREATE TABLE IF NOT EXISTS `backtest_sessions` (
            `id` INT AUTO_INCREMENT PRIMARY KEY,
            `session_id` VARCHAR(50) NOT NULL UNIQUE COMMENT '会话ID',
            `name` VARCHAR(100) NULL COMMENT '会话名称',
            `symbols` TEXT NOT NULL COMMENT '交易对列表(JSON)',
            `timeframes` TEXT NOT NULL COMMENT '时间周期列表(JSON)',
            `start_time` DATETIME NOT NULL COMMENT '数据开始时间',
            `end_time` DATETIME NOT NULL COMMENT '数据结束时间',
            `market_type` VARCHAR(20) NOT NULL DEFAULT 'futures' COMMENT '市场类型',
            `status` VARCHAR(20) NOT NULL DEFAULT 'collecting' COMMENT '状态: collecting/ready/running/completed',
            `kline_count` INT DEFAULT 0 COMMENT 'K线数据条数',
            `price_count` INT DEFAULT 0 COMMENT '价格数据条数',
            `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
            `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

            KEY `idx_status` (`status`),
            KEY `idx_created` (`created_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='回测会话表';
        """
        cursor.execute(create_session_sql)

        self.connection.commit()
        cursor.close()
        print("✅ 回测数据表创建完成")

    def symbol_to_futures_format(self, symbol: str) -> str:
        """转换为合约格式: BTC/USDT -> BTCUSDT"""
        return symbol.replace('/', '')

    async def fetch_futures_klines(self, symbol: str, timeframe: str,
                                    start_time: datetime, end_time: datetime) -> List[Dict]:
        """
        获取合约K线数据

        Args:
            symbol: 交易对
            timeframe: 时间周期
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            K线数据列表
        """
        all_klines = []
        futures_symbol = self.symbol_to_futures_format(symbol)

        # 计算需要获取的数据量
        since = int(start_time.timestamp() * 1000)
        until = int(end_time.timestamp() * 1000)

        print(f"  📊 获取 {symbol} {timeframe} K线: {start_time} ~ {end_time}")

        while since < until:
            try:
                # 获取K线数据
                ohlcv = await self.exchange.fetch_ohlcv(
                    futures_symbol,
                    timeframe=timeframe,
                    since=since,
                    limit=1000
                )

                if not ohlcv:
                    break

                for candle in ohlcv:
                    if candle[0] >= until:
                        break

                    all_klines.append({
                        'symbol': symbol,
                        'timeframe': timeframe,
                        'open_time': candle[0],
                        'timestamp': datetime.fromtimestamp(candle[0] / 1000),
                        'open_price': candle[1],
                        'high_price': candle[2],
                        'low_price': candle[3],
                        'close_price': candle[4],
                        'volume': candle[5]
                    })

                # 更新起始时间
                since = ohlcv[-1][0] + 1

                # 限速
                await asyncio.sleep(0.1)

            except Exception as e:
                print(f"  ❌ 获取K线失败: {e}")
                await asyncio.sleep(1)
                break

        print(f"    ✅ 获取 {len(all_klines)} 条 {timeframe} K线")
        return all_klines

    async def generate_price_data_from_klines(self, klines_1m: List[Dict],
                                               interval_seconds: int = 5) -> List[Dict]:
        """
        从1分钟K线生成高频价格数据

        通过线性插值在每根1分钟K线内生成多个价格点，模拟实时价格

        Args:
            klines_1m: 1分钟K线数据
            interval_seconds: 价格间隔(秒)，默认5秒

        Returns:
            价格数据列表
        """
        price_data = []

        for kline in klines_1m:
            open_time = kline['timestamp']
            open_price = float(kline['open_price'])
            high_price = float(kline['high_price'])
            low_price = float(kline['low_price'])
            close_price = float(kline['close_price'])

            # 在1分钟内生成价格点
            points_per_minute = 60 // interval_seconds

            for i in range(points_per_minute):
                timestamp = open_time + timedelta(seconds=i * interval_seconds)

                # 简单的价格插值：开盘->最高->最低->收盘
                progress = i / points_per_minute

                if progress < 0.25:
                    # 开盘到最高
                    price = open_price + (high_price - open_price) * (progress / 0.25)
                elif progress < 0.5:
                    # 最高到最低
                    price = high_price + (low_price - high_price) * ((progress - 0.25) / 0.25)
                elif progress < 0.75:
                    # 最低到高点
                    price = low_price + (high_price - low_price) * ((progress - 0.5) / 0.25) * 0.5
                else:
                    # 回到收盘价
                    price = low_price + (high_price - low_price) * 0.5 + \
                            (close_price - (low_price + (high_price - low_price) * 0.5)) * ((progress - 0.75) / 0.25)

                price_data.append({
                    'symbol': kline['symbol'],
                    'timestamp': timestamp,
                    'price': price,
                    'open_price': open_price,
                    'high_price': high_price,
                    'low_price': low_price,
                    'close_price': close_price,
                    'volume': kline.get('volume')
                })

        return price_data

    def save_klines(self, session_id: str, klines: List[Dict], market_type: str = 'futures'):
        """保存K线数据到数据库"""
        if not klines:
            return 0

        cursor = self.connection.cursor()

        insert_sql = """
        INSERT INTO backtest_kline_data
        (session_id, symbol, exchange, market_type, timeframe, open_time, timestamp,
         open_price, high_price, low_price, close_price, volume)
        VALUES (%s, %s, 'binance', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        open_price = VALUES(open_price),
        high_price = VALUES(high_price),
        low_price = VALUES(low_price),
        close_price = VALUES(close_price),
        volume = VALUES(volume)
        """

        count = 0
        batch_size = 1000

        for i in range(0, len(klines), batch_size):
            batch = klines[i:i+batch_size]
            values = [
                (session_id, k['symbol'], market_type, k['timeframe'], k['open_time'],
                 k['timestamp'], k['open_price'], k['high_price'], k['low_price'],
                 k['close_price'], k.get('volume'))
                for k in batch
            ]
            cursor.executemany(insert_sql, values)
            count += len(batch)

        self.connection.commit()
        cursor.close()
        return count

    def save_prices(self, session_id: str, prices: List[Dict], market_type: str = 'futures'):
        """保存价格数据到数据库"""
        if not prices:
            return 0

        cursor = self.connection.cursor()

        insert_sql = """
        INSERT INTO backtest_price_data
        (session_id, symbol, exchange, market_type, timestamp, price,
         open_price, high_price, low_price, close_price, volume)
        VALUES (%s, %s, 'binance', %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
        price = VALUES(price)
        """

        count = 0
        batch_size = 5000

        for i in range(0, len(prices), batch_size):
            batch = prices[i:i+batch_size]
            values = [
                (session_id, p['symbol'], market_type, p['timestamp'], p['price'],
                 p.get('open_price'), p.get('high_price'), p.get('low_price'),
                 p.get('close_price'), p.get('volume'))
                for p in batch
            ]
            cursor.executemany(insert_sql, values)
            count += len(batch)

        self.connection.commit()
        cursor.close()
        return count

    def create_session(self, session_id: str, symbols: List[str], timeframes: List[str],
                       start_time: datetime, end_time: datetime, market_type: str = 'futures') -> str:
        """创建回测会话"""
        import json

        cursor = self.connection.cursor()

        insert_sql = """
        INSERT INTO backtest_sessions
        (session_id, symbols, timeframes, start_time, end_time, market_type, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'collecting')
        ON DUPLICATE KEY UPDATE
        symbols = VALUES(symbols),
        timeframes = VALUES(timeframes),
        start_time = VALUES(start_time),
        end_time = VALUES(end_time),
        status = 'collecting'
        """

        cursor.execute(insert_sql, (
            session_id,
            json.dumps(symbols),
            json.dumps(timeframes),
            start_time,
            end_time,
            market_type
        ))

        self.connection.commit()
        cursor.close()

        return session_id

    def update_session_status(self, session_id: str, status: str,
                               kline_count: int = None, price_count: int = None):
        """更新会话状态"""
        cursor = self.connection.cursor()

        if kline_count is not None and price_count is not None:
            cursor.execute("""
                UPDATE backtest_sessions
                SET status = %s, kline_count = %s, price_count = %s
                WHERE session_id = %s
            """, (status, kline_count, price_count, session_id))
        else:
            cursor.execute("""
                UPDATE backtest_sessions SET status = %s WHERE session_id = %s
            """, (status, session_id))

        self.connection.commit()
        cursor.close()

    async def collect(self, symbols: List[str], hours: int = 48,
                      timeframes: List[str] = None,
                      price_interval: int = 5,
                      session_name: str = None) -> str:
        """
        采集回测数据

        Args:
            symbols: 交易对列表
            hours: 采集多少小时的数据
            timeframes: K线周期列表
            price_interval: 价格数据间隔(秒)
            session_name: 会话名称

        Returns:
            session_id
        """
        if timeframes is None:
            timeframes = ['1m', '5m', '15m', '1h', '4h']

        # 生成会话ID
        session_id = f"bt_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        print(f"\n{'='*60}")
        print(f"📈 开始采集回测数据")
        print(f"  会话ID: {session_id}")
        print(f"  时间范围: {start_time} ~ {end_time}")
        print(f"  交易对: {', '.join(symbols)}")
        print(f"  K线周期: {', '.join(timeframes)}")
        print(f"  价格间隔: {price_interval}秒")
        print(f"{'='*60}\n")

        # 创建会话
        self.create_session(session_id, symbols, timeframes, start_time, end_time)

        total_klines = 0
        total_prices = 0

        for symbol in symbols:
            print(f"\n📊 处理 {symbol}...")

            all_klines = []
            klines_1m = []

            # 采集各周期K线
            for tf in timeframes:
                klines = await self.fetch_futures_klines(symbol, tf, start_time, end_time)
                all_klines.extend(klines)

                if tf == '1m':
                    klines_1m = klines

            # 保存K线数据
            if all_klines:
                count = self.save_klines(session_id, all_klines)
                total_klines += count
                print(f"  💾 保存 {count} 条K线数据")

            # 从1分钟K线生成价格数据
            if klines_1m:
                print(f"  🔄 从1分钟K线生成价格数据...")
                price_data = await self.generate_price_data_from_klines(klines_1m, price_interval)

                if price_data:
                    count = self.save_prices(session_id, price_data)
                    total_prices += count
                    print(f"  💾 保存 {count} 条价格数据")

        # 更新会话状态
        self.update_session_status(session_id, 'ready', total_klines, total_prices)

        print(f"\n{'='*60}")
        print(f"✅ 数据采集完成!")
        print(f"  会话ID: {session_id}")
        print(f"  K线数据: {total_klines} 条")
        print(f"  价格数据: {total_prices} 条")
        print(f"{'='*60}\n")

        return session_id

    async def close(self):
        """关闭连接"""
        if self.exchange:
            await self.exchange.close()
        if self.connection:
            self.connection.close()


async def main():
    parser = argparse.ArgumentParser(description='回测数据采集器')
    parser.add_argument('--hours', type=int, default=48, help='采集多少小时的数据 (默认48)')
    parser.add_argument('--symbols', type=str, default=None, help='交易对列表，逗号分隔 (默认使用配置文件)')
    parser.add_argument('--timeframes', type=str, default='1m,5m,15m,1h,4h', help='K线周期，逗号分隔')
    parser.add_argument('--price-interval', type=int, default=5, help='价格数据间隔(秒) (默认5)')
    parser.add_argument('--name', type=str, default=None, help='会话名称')

    args = parser.parse_args()

    # 加载配置
    config = load_config()

    # 获取交易对
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(',')]
    else:
        symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

    # 获取时间周期
    timeframes = [t.strip() for t in args.timeframes.split(',')]

    # 创建采集器
    collector = BacktestDataCollector(config)

    try:
        # 初始化
        await collector.init_exchange()
        collector.init_db()
        collector.create_tables()

        # 采集数据
        session_id = await collector.collect(
            symbols=symbols,
            hours=args.hours,
            timeframes=timeframes,
            price_interval=args.price_interval,
            session_name=args.name
        )

        print(f"\n使用以下命令启动回测:")
        print(f"  python scripts/backtest/backtest_runner.py --session {session_id}")

    finally:
        await collector.close()


if __name__ == '__main__':
    asyncio.run(main())
