"""
快速合约数据采集器
专门为超级大脑优化，采集5m K线数据
使用并发请求，减少延迟

注意：实时价格由 WebSocket 服务(binance_ws_price.py)提供，不在此采集
"""

import asyncio
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql
from decimal import Decimal


class FastFuturesCollector:
    """快速合约数据采集器 - 专注于K线和价格"""

    def __init__(self, db_config: dict):
        """
        初始化快速采集器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.base_url = "https://fapi.binance.com"

        # 超时设置（秒）
        self.timeout = aiohttp.ClientTimeout(total=5, connect=2)

        # 并发限制（同时采集的交易对数量）
        self.max_concurrent = 10

        logger.info("初始化快速合约数据采集器")

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(**self.db_config, autocommit=False)

    async def fetch_kline(self, session: aiohttp.ClientSession, symbol: str) -> Optional[Dict]:
        """
        异步获取单个交易对的最新K线

        Args:
            session: aiohttp会话
            symbol: 交易对符号（如 BTCUSDT）

        Returns:
            K线数据字典，失败返回None
        """
        url = f"{self.base_url}/fapi/v1/klines"
        params = {
            'symbol': symbol,
            'interval': '5m',
            'limit': 1  # 只获取最新的一条
        }

        try:
            async with session.get(url, params=params, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        kline = data[0]
                        return {
                            'symbol': f"{symbol[:-4]}/USDT",  # BTCUSDT -> BTC/USDT
                            'timeframe': '5m',
                            'open_time': kline[0],
                            'close_time': kline[6],
                            'timestamp': datetime.fromtimestamp(kline[0] / 1000),
                            'open_price': Decimal(kline[1]),
                            'high_price': Decimal(kline[2]),
                            'low_price': Decimal(kline[3]),
                            'close_price': Decimal(kline[4]),
                            'volume': Decimal(kline[5]),
                            'quote_volume': Decimal(kline[7]),
                            'number_of_trades': int(kline[8]),
                            'taker_buy_base_volume': Decimal(kline[9]),
                            'taker_buy_quote_volume': Decimal(kline[10])
                        }
                else:
                    logger.warning(f"获取 {symbol} K线失败: HTTP {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"获取 {symbol} K线超时")
            return None
        except Exception as e:
            logger.error(f"获取 {symbol} K线异常: {e}")
            return None

    async def fetch_price(self, session: aiohttp.ClientSession, symbol: str) -> Optional[Dict]:
        """
        异步获取单个交易对的最新价格

        Args:
            session: aiohttp会话
            symbol: 交易对符号（如 BTCUSDT）

        Returns:
            价格数据字典，失败返回None
        """
        url = f"{self.base_url}/fapi/v1/ticker/price"
        params = {'symbol': symbol}

        try:
            async with session.get(url, params=params, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    return {
                        'symbol': f"{symbol[:-4]}/USDT",
                        'price': Decimal(data['price']),
                        'timestamp': datetime.now()
                    }
                else:
                    logger.warning(f"获取 {symbol} 价格失败: HTTP {response.status}")
                    return None
        except asyncio.TimeoutError:
            logger.warning(f"获取 {symbol} 价格超时")
            return None
        except Exception as e:
            logger.error(f"获取 {symbol} 价格异常: {e}")
            return None

    async def collect_batch(self, symbols: List[str], collect_type: str = 'kline') -> List[Dict]:
        """
        批量采集数据（并发）

        Args:
            symbols: 交易对列表（如 ['BTCUSDT', 'ETHUSDT']）
            collect_type: 采集类型 'kline' 或 'price'

        Returns:
            成功采集的数据列表
        """
        results = []

        # 创建aiohttp会话
        async with aiohttp.ClientSession() as session:
            # 创建任务列表
            if collect_type == 'kline':
                tasks = [self.fetch_kline(session, symbol) for symbol in symbols]
            else:
                tasks = [self.fetch_price(session, symbol) for symbol in symbols]

            # 使用信号量限制并发数
            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def bounded_task(task):
                async with semaphore:
                    return await task

            # 执行所有任务
            bounded_tasks = [bounded_task(task) for task in tasks]
            results_raw = await asyncio.gather(*bounded_tasks, return_exceptions=True)

            # 过滤成功的结果
            for result in results_raw:
                if result is not None and not isinstance(result, Exception):
                    results.append(result)

        return results

    def save_klines(self, klines: List[Dict]) -> int:
        """
        保存K线数据到数据库（批量插入）

        Args:
            klines: K线数据列表

        Returns:
            成功插入的记录数
        """
        if not klines:
            return 0

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 批量插入SQL
            sql = """
                INSERT INTO kline_data (
                    symbol, exchange, timeframe, open_time, close_time, timestamp,
                    open_price, high_price, low_price, close_price,
                    volume, quote_volume, number_of_trades,
                    taker_buy_base_volume, taker_buy_quote_volume,
                    created_at
                ) VALUES (
                    %s, 'binance_futures', %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    NOW()
                )
                ON DUPLICATE KEY UPDATE
                    open_price = VALUES(open_price),
                    high_price = VALUES(high_price),
                    low_price = VALUES(low_price),
                    close_price = VALUES(close_price),
                    volume = VALUES(volume),
                    quote_volume = VALUES(quote_volume),
                    number_of_trades = VALUES(number_of_trades),
                    taker_buy_base_volume = VALUES(taker_buy_base_volume),
                    taker_buy_quote_volume = VALUES(taker_buy_quote_volume)
            """

            # 准备批量数据
            values = []
            for k in klines:
                values.append((
                    k['symbol'], k['timeframe'], k['open_time'], k['close_time'], k['timestamp'],
                    float(k['open_price']), float(k['high_price']), float(k['low_price']), float(k['close_price']),
                    float(k['volume']), float(k['quote_volume']), k['number_of_trades'],
                    float(k['taker_buy_base_volume']), float(k['taker_buy_quote_volume'])
                ))

            # 批量插入
            cursor.executemany(sql, values)
            conn.commit()

            inserted = cursor.rowcount
            logger.info(f"✓ 保存 {len(klines)} 条K线数据，影响 {inserted} 行")
            return inserted

        except Exception as e:
            conn.rollback()
            logger.error(f"保存K线数据失败: {e}")
            return 0
        finally:
            cursor.close()
            conn.close()

    def save_prices(self, prices: List[Dict]) -> int:
        """
        保存价格数据到数据库

        Args:
            prices: 价格数据列表

        Returns:
            成功插入的记录数
        """
        if not prices:
            return 0

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            # 批量插入SQL
            sql = """
                INSERT INTO futures_prices (
                    symbol, exchange, price, timestamp, created_at
                ) VALUES (
                    %s, 'binance_futures', %s, %s, NOW()
                )
            """

            values = [(p['symbol'], float(p['price']), p['timestamp']) for p in prices]

            cursor.executemany(sql, values)
            conn.commit()

            inserted = cursor.rowcount
            logger.info(f"✓ 保存 {len(prices)} 条价格数据")
            return inserted

        except Exception as e:
            conn.rollback()
            logger.error(f"保存价格数据失败: {e}")
            return 0
        finally:
            cursor.close()
            conn.close()

    def get_trading_symbols(self) -> List[str]:
        """
        从config.yaml获取需要监控的交易对列表

        Returns:
            交易对列表（币安格式，如 ['BTCUSDT', 'ETHUSDT']）
        """
        try:
            import yaml
            from pathlib import Path

            # 查找config.yaml文件
            config_path = Path(__file__).parent.parent.parent / 'config.yaml'

            if not config_path.exists():
                logger.error(f"配置文件不存在: {config_path}")
                return []

            # 读取配置文件
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                symbols_list = config.get('symbols', [])

            if not symbols_list:
                logger.warning("配置文件中没有找到交易对列表")
                return []

            # 转换为币安格式: BTC/USDT -> BTCUSDT
            symbols = [s.replace('/', '') for s in symbols_list]

            logger.info(f"从配置文件获取 {len(symbols)} 个交易对")
            return symbols

        except Exception as e:
            logger.error(f"获取交易对列表失败: {e}")
            # 降级：返回默认交易对列表
            default_symbols = [
                'BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT',
                'ADAUSDT', 'DOGEUSDT', 'MATICUSDT', 'DOTUSDT', 'LTCUSDT'
            ]
            logger.info(f"使用默认交易对列表: {len(default_symbols)} 个")
            return default_symbols

    async def run_collection_cycle(self):
        """
        执行一次完整的采集周期
        注意：只采集K线数据，实时价格由 WebSocket 服务提供
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("开始快速数据采集周期")

        # 获取交易对列表
        symbols = self.get_trading_symbols()
        if not symbols:
            logger.warning("没有可采集的交易对")
            return

        logger.info(f"目标: {len(symbols)} 个交易对")

        # 采集K线数据（5m周期）
        logger.info("采集5m K线数据...")
        klines = await self.collect_batch(symbols, 'kline')
        logger.info(f"成功获取 {len(klines)}/{len(symbols)} 条K线")

        # 保存K线
        if klines:
            self.save_klines(klines)

        # 注意：不再采集价格数据，实时价格由 binance_ws_price.py 的 WebSocket 服务提供
        # 合约交易需要毫秒级的实时价格推送，而不是每5分钟的轮询

        # 统计
        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"✓ 采集周期完成，耗时 {elapsed:.2f} 秒")
        logger.info(f"  K线: {len(klines)}/{len(symbols)}")
        logger.info("=" * 60)


async def main():
    """测试入口"""
    from app.utils.config_loader import load_config

    config = load_config()
    db_config = config['database']['mysql']

    collector = FastFuturesCollector(db_config)

    # 运行一次采集
    await collector.run_collection_cycle()


if __name__ == '__main__':
    asyncio.run(main())
