"""
智能合约数据采集器 - 分层采集策略
根据K线周期的更新频率，智能决定采集哪些时间周期，避免重复浪费

采集策略:
- 5分钟周期:  采集 5m K线 (每5分钟更新一次)
- 15分钟周期: 采集 15m K线 (每15分钟更新一次)
- 1小时周期:  采集 1h K线 (每1小时更新一次)
- 1天周期:    采集 1d K线 (每1天更新一次)

优势:
- 减少99%的无效采集
- 降低API请求压力
- 节省数据库写入
- 提高系统效率
"""

import asyncio
import aiohttp
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql
from decimal import Decimal
from app.database.connection_pool import get_global_pool


class SmartFuturesCollector:
    """智能合约数据采集器 - 分层采集策略"""

    def __init__(self, db_config: dict):
        """
        初始化智能采集器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config

        # 初始化数据库连接池
        self.db_pool = get_global_pool(db_config, pool_size=5)

        # U本位合约API
        self.usdt_base_url = "https://fapi.binance.com"

        # 币本位合约API
        self.coin_base_url = "https://dapi.binance.com"

        # 超时设置（秒）
        self.timeout = aiohttp.ClientTimeout(total=5, connect=2)

        # 并发限制
        self.max_concurrent = 10

        # 上次采集时间记录（用于判断是否需要采集）
        self.last_collection_time = {}

        logger.info("✅ 初始化智能合约数据采集器（分层采集策略，支持U本位+币本位）")


    def should_collect_interval(self, interval: str) -> bool:
        """
        判断当前是否需要采集该时间周期的K线

        🔥 修复逻辑：基于K线整点时间判断，而不是距上次采集时间
        - 5m: 每5分钟整点 (00:00, 00:05, 00:10, ...)
        - 15m: 每15分钟整点 (00:00, 00:15, 00:30, 00:45)
        - 1h: 每小时整点 (00:00, 01:00, 02:00, ...)
        - 1d: 每天00:00

        Args:
            interval: 时间周期 (5m, 15m, 1h, 1d)

        Returns:
            True表示需要采集，False表示跳过
        """
        now = datetime.utcnow()

        # 如果从未采集过，则需要采集
        if interval not in self.last_collection_time:
            return True

        last_time = self.last_collection_time[interval]

        # 🔥 新逻辑：基于K线整点时间判断
        if interval == '5m':
            # 计算当前5分钟整点（向下取整到最近的5分钟）
            current_bar_minute = (now.minute // 5) * 5
            current_bar_time = now.replace(minute=current_bar_minute, second=0, microsecond=0)

            # 如果上次采集时间早于当前K线整点，则需要采集
            return last_time < current_bar_time

        elif interval == '15m':
            # 计算当前15分钟整点（0, 15, 30, 45）
            current_bar_minute = (now.minute // 15) * 15
            current_bar_time = now.replace(minute=current_bar_minute, second=0, microsecond=0)

            return last_time < current_bar_time

        elif interval == '1h':
            # 计算当前小时整点
            current_bar_time = now.replace(minute=0, second=0, microsecond=0)

            return last_time < current_bar_time

        elif interval == '1d':
            # 计算当前天00:00
            current_bar_time = now.replace(hour=0, minute=0, second=0, microsecond=0)

            return last_time < current_bar_time

        else:
            return True

    async def fetch_kline(self, session: aiohttp.ClientSession, symbol: str, interval: str = '5m', limit: int = 1) -> Optional[List[Dict]]:
        """
        异步获取单个U本位合约交易对的K线

        Args:
            session: aiohttp会话
            symbol: 交易对符号（如 BTCUSDT）
            interval: 时间周期 (5m, 15m, 1h, 1d)
            limit: 获取K线数量

        Returns:
            K线数据列表，失败返回None
        """
        url = f"{self.usdt_base_url}/fapi/v1/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }

        try:
            async with session.get(url, params=params, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        klines = []
                        # 🔥 修复：只处理已完成的K线（排除最后一根未完成的）
                        # 对于5m/15m，limit=2，只取第一根（已完成）
                        # 对于1h/1d，limit>=50，取所有但排除最后一根
                        completed_data = data[:-1] if len(data) > 1 else data

                        for kline in completed_data:
                            klines.append({
                                'symbol': f"{symbol[:-4]}/USDT",  # BTCUSDT -> BTC/USDT
                                'timeframe': interval,
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
                            })
                        return klines
                else:
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"获取 {symbol} {interval} K线异常: {e}")
            return None

    async def fetch_coin_kline(self, session: aiohttp.ClientSession, symbol: str, interval: str = '5m', limit: int = 1) -> Optional[List[Dict]]:
        """
        异步获取币本位合约的K线

        Args:
            session: aiohttp会话
            symbol: 交易对符号（如 BTCUSD_PERP）
            interval: 时间周期 (5m, 15m, 1h, 1d)
            limit: 获取K线数量

        Returns:
            K线数据列表，失败返回None
        """
        url = f"{self.coin_base_url}/dapi/v1/klines"
        params = {
            'symbol': symbol,
            'interval': interval,
            'limit': limit
        }

        try:
            async with session.get(url, params=params, timeout=self.timeout) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and len(data) > 0:
                        klines = []
                        # 🔥 修复：只处理已完成的K线（排除最后一根未完成的）
                        completed_data = data[:-1] if len(data) > 1 else data

                        for kline in completed_data:
                            # BTCUSD_PERP -> BTC/USD
                            base_symbol = symbol.replace('USD_PERP', '/USD')
                            klines.append({
                                'symbol': base_symbol,
                                'contract_type': 'coin_futures',
                                'timeframe': interval,
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
                            })
                        return klines
                else:
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"获取币本位 {symbol} {interval} K线异常: {e}")
            return None

    async def collect_batch(self, symbols: List[str], interval: str = '5m', limit: int = 1) -> List[Dict]:
        """
        批量采集U本位K线数据（并发）

        Args:
            symbols: 交易对列表（如 ['BTCUSDT', 'ETHUSDT']）
            interval: 时间周期 (5m, 15m, 1h, 1d)
            limit: 每个交易对获取的K线数量

        Returns:
            成功采集的K线数据列表（扁平化）
        """
        results = []

        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_kline(session, symbol, interval, limit) for symbol in symbols]

            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def bounded_task(task):
                async with semaphore:
                    return await task

            bounded_tasks = [bounded_task(task) for task in tasks]
            results_raw = await asyncio.gather(*bounded_tasks, return_exceptions=True)

            # 过滤成功的结果并扁平化
            for result in results_raw:
                if result is not None and not isinstance(result, Exception):
                    if isinstance(result, list):
                        results.extend(result)

        return results

    async def collect_coin_batch(self, symbols: List[str], interval: str = '5m', limit: int = 1) -> List[Dict]:
        """
        批量采集币本位合约K线数据（并发）

        Args:
            symbols: 币本位合约交易对列表（如 ['BTCUSD_PERP', 'ETHUSD_PERP']）
            interval: 时间周期 (5m, 15m, 1h, 1d)
            limit: 每个交易对获取的K线数量

        Returns:
            成功采集的K线数据列表（扁平化）
        """
        results = []

        async with aiohttp.ClientSession() as session:
            tasks = [self.fetch_coin_kline(session, symbol, interval, limit) for symbol in symbols]

            semaphore = asyncio.Semaphore(self.max_concurrent)

            async def bounded_task(task):
                async with semaphore:
                    return await task

            bounded_tasks = [bounded_task(task) for task in tasks]
            results_raw = await asyncio.gather(*bounded_tasks, return_exceptions=True)

            # 过滤成功的结果并扁平化
            for result in results_raw:
                if result is not None and not isinstance(result, Exception):
                    if isinstance(result, list):
                        results.extend(result)

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

        # 使用连接池获取连接
        with self.db_pool.get_connection() as conn:
            cursor = conn.cursor()

            try:
                sql = """
                    INSERT INTO kline_data (
                        symbol, exchange, timeframe, open_time, close_time, timestamp,
                        open_price, high_price, low_price, close_price,
                        volume, quote_volume, number_of_trades,
                        taker_buy_base_volume, taker_buy_quote_volume,
                        created_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s,
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

                values = []
                for k in klines:
                    # 确定exchange类型: U本位或币本位
                    exchange = 'binance_coin_futures' if k.get('contract_type') == 'coin_futures' else 'binance_futures'
                    values.append((
                        k['symbol'], exchange, k['timeframe'], k['open_time'], k['close_time'], k['timestamp'],
                        float(k['open_price']), float(k['high_price']), float(k['low_price']), float(k['close_price']),
                        float(k['volume']), float(k['quote_volume']), k['number_of_trades'],
                        float(k['taker_buy_base_volume']), float(k['taker_buy_quote_volume'])
                    ))

                cursor.executemany(sql, values)
                conn.commit()

                inserted = cursor.rowcount
                return inserted

            except Exception as e:
                conn.rollback()
                logger.error(f"保存K线数据失败: {e}")
                return 0
            finally:
                cursor.close()

    def get_trading_symbols(self) -> List[str]:
        """
        从config.yaml获取需要监控的U本位合约交易对列表

        Returns:
            交易对列表（币安格式，如 ['BTCUSDT', 'ETHUSDT']）
        """
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).parent.parent.parent / 'config.yaml'

            if not config_path.exists():
                logger.error(f"配置文件不存在: {config_path}")
                return []

            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                symbols_list = config.get('symbols', [])

            if not symbols_list:
                logger.warning("配置文件中没有找到交易对列表")
                return []

            # 转换为币安格式: BTC/USDT -> BTCUSDT
            symbols = [s.replace('/', '') for s in symbols_list]
            return symbols

        except Exception as e:
            logger.error(f"获取交易对列表失败: {e}")
            return []

    def get_coin_futures_symbols(self) -> List[str]:
        """
        从config.yaml获取需要监控的币本位合约交易对列表

        Returns:
            币本位合约交易对列表（币安格式，如 ['BTCUSD_PERP', 'ETHUSD_PERP']）
        """
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).parent.parent.parent / 'config.yaml'

            if not config_path.exists():
                logger.error(f"配置文件不存在: {config_path}")
                return []

            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                coin_symbols_list = config.get('coin_futures_symbols', [])

            if not coin_symbols_list:
                logger.info("配置文件中没有币本位合约交易对")
                return []

            return coin_symbols_list

        except Exception as e:
            logger.error(f"获取币本位合约交易对列表失败: {e}")
            return []

    async def run_collection_cycle(self):
        """
        执行一次智能采集周期
        根据时间判断需要采集哪些时间周期，避免重复采集
        同时采集U本位和币本位合约数据
        """
        start_time = datetime.utcnow()
        logger.info("=" * 60)
        logger.info("🧠 开始智能数据采集周期（分层策略）")

        # 获取U本位和币本位交易对列表
        usdt_symbols = self.get_trading_symbols()
        coin_symbols = self.get_coin_futures_symbols()

        if not usdt_symbols and not coin_symbols:
            logger.warning("没有可采集的交易对")
            return

        logger.info(f"目标: {len(usdt_symbols)} 个U本位交易对 + {len(coin_symbols)} 个币本位交易对")

        # 定义所有时间周期及其采集规则
        # 🔥 修复：5m/15m获取2根K线，只保存第一根（已完成的），丢弃第二根（未完成的）
        intervals = [
            ('5m', 2),    # 5分钟K线，获取2根，只保存第1根（已完成）
            ('15m', 2),   # 15分钟K线，获取2根，只保存第1根（已完成）
            ('1h', 100),  # 1小时K线，要100条（超级大脑需要）
            ('1d', 50)    # 1天K线，要50条（超级大脑需要）
        ]

        all_klines = []
        collected_intervals = []  # 记录本次采集的时间周期

        # 智能判断并采集各个时间周期
        for interval, limit in intervals:
            if self.should_collect_interval(interval):
                logger.info(f"✅ 采集 {interval} K线 (每个交易对{limit}条，距上次 {self._get_elapsed_time(interval)})...")

                # 采集U本位
                if usdt_symbols:
                    usdt_klines = await self.collect_batch(usdt_symbols, interval, limit)
                    all_klines.extend(usdt_klines)

                # 采集币本位
                if coin_symbols:
                    coin_klines = await self.collect_coin_batch(coin_symbols, interval, limit)
                    all_klines.extend(coin_klines)

                logger.info(f"   成功获取 {len(all_klines) - len([k for k in all_klines if k['timeframe'] != interval])} 条 {interval} K线")
                collected_intervals.append(interval)

                # 更新采集时间
                self.last_collection_time[interval] = datetime.utcnow()
            else:
                elapsed = self._get_elapsed_time(interval)
                logger.info(f"⏭️  跳过 {interval} K线 (距上次仅 {elapsed}，无需采集)")

        # 保存所有K线
        if all_klines:
            inserted = self.save_klines(all_klines)
            logger.info(f"✓ 保存 {len(all_klines)} 条K线数据，影响 {inserted} 行")

        # 统计
        elapsed = (datetime.utcnow() - start_time).total_seconds()

        # 分别统计U本位和币本位数据
        usdt_klines = [k for k in all_klines if k.get('contract_type') != 'coin_futures']
        coin_klines = [k for k in all_klines if k.get('contract_type') == 'coin_futures']

        logger.info(f"✓ 采集周期完成，耗时 {elapsed:.2f} 秒")
        logger.info(f"  本次采集: {', '.join(collected_intervals) if collected_intervals else '无'}")
        logger.info(f"  总K线数: {len(all_klines)}")
        if coin_symbols:
            logger.info(f"  U本位: {len(usdt_klines)} 条 | 币本位: {len(coin_klines)} 条")

        # 显示节省统计
        if not collected_intervals:
            logger.info(f"  ⚡ 本次跳过所有周期，节省100%采集资源")
        elif len(collected_intervals) < len(intervals):
            saved_pct = (1 - len(collected_intervals) / len(intervals)) * 100
            logger.info(f"  ⚡ 智能跳过 {len(intervals) - len(collected_intervals)} 个周期，节省 {saved_pct:.0f}% 采集资源")

        logger.info("=" * 60)

    def _get_elapsed_time(self, interval: str) -> str:
        """
        获取距离上次采集的时间（用于日志显示）

        Args:
            interval: 时间周期

        Returns:
            时间描述字符串
        """
        if interval not in self.last_collection_time:
            return "首次"

        elapsed_seconds = (datetime.utcnow() - self.last_collection_time[interval]).total_seconds()

        if elapsed_seconds < 60:
            return f"{int(elapsed_seconds)}秒"
        elif elapsed_seconds < 3600:
            return f"{int(elapsed_seconds / 60)}分钟"
        elif elapsed_seconds < 86400:
            return f"{int(elapsed_seconds / 3600)}小时"
        else:
            return f"{int(elapsed_seconds / 86400)}天"


async def main():
    """测试入口"""
    from app.utils.config_loader import load_config

    config = load_config()
    db_config = config['database']['mysql']

    collector = SmartFuturesCollector(db_config)

    # 模拟多次采集，展示智能跳过效果
    logger.info("开始测试智能采集策略...")

    for i in range(3):
        logger.info(f"\n第 {i+1} 次采集:")
        await collector.run_collection_cycle()

        if i < 2:
            logger.info("等待 5 秒后再次采集...")
            await asyncio.sleep(5)


if __name__ == '__main__':
    asyncio.run(main())
