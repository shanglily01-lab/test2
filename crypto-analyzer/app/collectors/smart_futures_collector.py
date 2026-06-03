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
from app.utils.binance_rate_guard import rate_guard, parse_ban_msg
from app.utils.futures_symbol import futures_symbol_clean


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

        # 超时设置（秒）
        self.timeout = aiohttp.ClientTimeout(total=5, connect=2)

        # 并发限制: 6 路并发 = ~250/6 ≈ 42 批, 每批 ~0.3-0.5s, 单周期 ≈ 12-21 秒
        # 首轮启动有雪崩防护(只采5m), 后续正常周期由 should_collect_interval 整数点判跳,
        # 极少出现 5 个周期同时触发(仅在 UTC 00:00), 6 并发安全.
        self.max_concurrent = 6
        # 间隔间等待: 并行采集后不再需要, 保留仅作兜底
        self.inter_interval_sleep = 0.5

        # 上次采集时间记录（用于判断是否需要采集）
        self.last_collection_time = {}

        logger.info("✅ 初始化智能合约数据采集器（分层采集策略，U本位）")


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
        now = datetime.now()

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

        elif interval == '4h':
            # 计算当前4小时整点（0, 4, 8, 12, 16, 20）
            current_bar_hour = (now.hour // 4) * 4
            current_bar_time = now.replace(hour=current_bar_hour, minute=0, second=0, microsecond=0)

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

        # IP级熔断: 处于封禁期则直接跳过, 不发请求
        if rate_guard.is_banned():
            return None

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
                                'timestamp': datetime.utcfromtimestamp(kline[0] / 1000),
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
                    # 418/429 可能是 IP ban, 尝试解析 banned until 并激活熔断
                    if response.status in (418, 429):
                        try:
                            body = await response.text()
                            until_ms = parse_ban_msg(body)
                            if until_ms and rate_guard.set_banned_until(until_ms, source='fast_collector_usdt'):
                                logger.error(f"fast_collector U本位收到 IP ban: {body[:200]}")
                        except Exception:
                            pass
                    return None
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            logger.error(f"获取 {symbol} {interval} K线异常: {e}")
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

        all_values = []
        for k in klines:
            exchange = 'binance_futures'
            all_values.append((
                k['symbol'], exchange, k['timeframe'], k['open_time'], k['close_time'], k['timestamp'],
                float(k['open_price']), float(k['high_price']), float(k['low_price']), float(k['close_price']),
                float(k['volume']), float(k['quote_volume']), k['number_of_trades'],
                float(k['taker_buy_base_volume']), float(k['taker_buy_quote_volume'])
            ))

        # 分批写, 防止远程 MySQL 连接 timeout 被切 (本地跑 + 大批量时尤其重要)
        BATCH_SIZE = 500
        total_inserted = 0
        for i in range(0, len(all_values), BATCH_SIZE):
            batch = all_values[i:i + BATCH_SIZE]
            try:
                with self.db_pool.get_connection() as conn:
                    cursor = conn.cursor()
                    try:
                        cursor.executemany(sql, batch)
                        conn.commit()
                        total_inserted += cursor.rowcount
                    finally:
                        cursor.close()
            except Exception as e:
                logger.error(f"保存K线数据失败 (batch {i}-{i+len(batch)}): {e}")
                # 单批失败不影响其它批
                continue

        return total_inserted

    def _get_high_risk_symbols(self) -> set:
        """
        从 trading_symbol_rating 表查 rating_level >= 2 的交易对 (严格限制 + 永久禁止)
        这些交易对系统基本不会开新仓 (level 2 仅 50U/笔, level 3 完全禁), 不必采 K 线浪费 weight.

        Returns:
            高风险 symbol 集合, symbol 格式跟 DB 里一致 (一般是 'BTC/USDT')
        """
        try:
            with self.db_pool.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT symbol FROM trading_symbol_rating WHERE rating_level >= 2"
                    )
                    rows = cursor.fetchall()
                    return {
                        futures_symbol_clean(
                            row[0] if not isinstance(row, dict) else row['symbol']
                        )
                        for row in rows
                    }
        except Exception as e:
            logger.warning(f"查询高风险 symbol 失败 (跳过过滤): {e}")
            return set()

    # 币安在交易 symbol 列表的内存缓存
    _active_symbols_cache: Dict[str, tuple] = {}  # {market_type: (symbols_set, ts)}
    _active_symbols_ttl_sec = 3600  # 缓存 1 小时

    @classmethod
    def _fetch_active_binance_symbols(cls, market_type: str = 'futures') -> Optional[set]:
        """从币安 exchangeInfo 拿当前在交易 (status='TRADING') 的 symbols 集合.

        Args:
            market_type: 'futures' (U本位)
        Returns:
            set of symbols (币安格式, 如 {'BTCUSDT', 'ETHUSDT'}); 失败返回 None
        """
        import time as _t
        market_type = 'futures'
        cached = cls._active_symbols_cache.get(market_type)
        if cached and (_t.time() - cached[1]) < cls._active_symbols_ttl_sec:
            return cached[0]

        try:
            import requests
            url = 'https://fapi.binance.com/fapi/v1/exchangeInfo'

            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"币安 exchangeInfo HTTP {resp.status_code}, 跳过过滤")
                return None
            data = resp.json()
            symbols_raw = data.get('symbols', [])
            active = {
                s['symbol']
                for s in symbols_raw
                if s.get('status') == 'TRADING'
                and s.get('contractType') == 'PERPETUAL'
            }
            cls._active_symbols_cache[market_type] = (active, _t.time())
            logger.info(f"[symbols 同步] 币安 {market_type} active 列表: {len(active)} 个 (缓存 1h)")
            return active
        except Exception as e:
            logger.warning(f"[symbols 同步] 拉币安 exchangeInfo 失败 (跳过过滤,用 config 原始列表): {e}")
            return None

    def get_trading_symbols(self) -> List[str]:
        """
        获取需要监控的 U 本位合约交易对.

        来源:
          1. config.yaml 的 symbols 列表 (硬编码基线)
          2. 实际币安在交易的 symbols (动态过滤, 防止 config 里有已下架的 ARIA/USDT 等)
          3. trading_symbol_rating 高风险剔除 (rating_level >= 2)

        Returns:
            交易对列表 (币安格式, 如 ['BTCUSDT', 'ETHUSDT'])
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

            # 仅保留 U 本位 (/USDT), 忽略误配的币本位 (/USD) 或其它格式
            usdt_only = [s for s in symbols_list if isinstance(s, str) and s.endswith('/USDT')]
            dropped_fmt = len(symbols_list) - len(usdt_only)
            if dropped_fmt:
                logger.warning(
                    f"config.yaml symbols 中非 /USDT 条目 {dropped_fmt} 个已跳过 (不采集币本位)"
                )
            symbols_list = usdt_only

            # 剔除 rating_level >= 2 的 symbol
            high_risk = self._get_high_risk_symbols()
            before = len(symbols_list)
            symbols_list = [s for s in symbols_list if futures_symbol_clean(s) not in high_risk]
            after = len(symbols_list)
            if before > after:
                logger.info(f"按 rating_level >= 2 过滤: {before} -> {after} symbols (剔除 {before - after} 个高风险)")

            # 转换为币安格式: BTC/USDT -> BTCUSDT
            binance_format = [s.replace('/', '') for s in symbols_list]

            # 与币安实际在交易的 symbols 求交集 (剔 ARIA 这种已下架的)
            active = self._fetch_active_binance_symbols('futures')
            if active is not None:
                before2 = len(binance_format)
                filtered = [s for s in binance_format if s in active]
                dropped = set(binance_format) - active
                after2 = len(filtered)
                if dropped:
                    sample = sorted(list(dropped))[:10]
                    logger.warning(
                        f"[symbols 同步] config.yaml 里 {len(dropped)} 个 symbol 已不在币安活跃列表, 剔除: "
                        f"{sample}{' ...' if len(dropped) > 10 else ''}"
                    )
                logger.info(f"[symbols 同步] 与币安 active 求交后: {before2} -> {after2} symbols")
                binance_format = filtered

            return binance_format

        except Exception as e:
            logger.error(f"获取交易对列表失败: {e}")
            return []

    async def collect_interval_data(self, symbols, interval, limit):
        """并行采集单个周期的 U 本位 K 线"""
        if not symbols:
            self.last_collection_time[interval] = datetime.now()
            return []
        klines = await self.collect_batch(symbols, interval, limit)
        self.last_collection_time[interval] = datetime.now()
        return klines or []

    async def run_collection_cycle(self):
        """
        执行一次智能采集周期
        根据时间判断需要采集哪些时间周期，避免重复采集
        各周期并行采集，大幅缩短总耗时
        """
        start_time = datetime.now()
        logger.info("=" * 60)
        logger.info("🧠 开始智能数据采集周期（并行采集策略, 仅 U 本位）")

        usdt_symbols = self.get_trading_symbols()

        if not usdt_symbols:
            logger.warning("没有可采集的交易对")
            return

        logger.info(f"目标: {len(usdt_symbols)} 个 U 本位交易对")

        # 定义所有时间周期及其采集规则
        intervals = [
            ('5m', 2),    # 5分钟K线，获取2根，只保存第1根（已完成）
            ('15m', 2),   # 15分钟K线，获取2根，只保存第1根（已完成）
            ('1h', 100),  # 1小时K线，要100条（超级大脑需要）
            ('4h', 10),   # 4小时K线，要10条（Big4动量判断）
            ('1d', 50)    # 1天K线，要50条（超级大脑需要）
        ]

        all_klines = []
        collected_intervals = []

        # 首轮启动雪崩防护: last_collection_time 为空时, 只采 5m, 其它周期标记为已采
        first_run = not self.last_collection_time
        if first_run:
            logger.info("🛡️  首轮启动: 只采 5m, 其它周期等下次整点自然触发")
            now_utc = datetime.now()
            for itv, _ in intervals:
                if itv != '5m':
                    self.last_collection_time[itv] = now_utc

        # 智能判断需要采集的周期，并行执行
        interval_tasks = []
        for interval, limit in intervals:
            if self.should_collect_interval(interval):
                logger.info(f"✅ 采集 {interval} K线 (每个交易对{limit}条，距上次 {self._get_elapsed_time(interval)})...")
                collected_intervals.append(interval)
                interval_tasks.append(
                    self.collect_interval_data(usdt_symbols, interval, limit)
                )
            else:
                elapsed = self._get_elapsed_time(interval)
                logger.info(f"⏭️  跳过 {interval} K线 (距上次仅 {elapsed})")

        # 并行执行所有需要采集的周期
        if interval_tasks:
            results = await asyncio.gather(*interval_tasks)
            for klines in results:
                if klines:
                    all_klines.extend(klines)

            # 统计各周期采集量
            for interval in collected_intervals:
                count = len([k for k in all_klines if k.get('timeframe') == interval])
                logger.info(f"  ✓ {interval}: {count} 条")

        # 保存所有K线
        if all_klines:
            inserted = self.save_klines(all_klines)
            logger.info(f"✓ 保存 {len(all_klines)} 条K线数据，影响 {inserted} 行")

        # 统计
        elapsed = (datetime.now() - start_time).total_seconds()

        logger.info(f"✓ 采集周期完成，耗时 {elapsed:.2f} 秒")
        logger.info(f"  本次采集: {', '.join(collected_intervals) if collected_intervals else '无'}")
        logger.info(f"  总K线数: {len(all_klines)}")

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

        elapsed_seconds = (datetime.now() - self.last_collection_time[interval]).total_seconds()

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
