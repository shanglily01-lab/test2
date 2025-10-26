"""
统一数据采集调度器
整合所有数据源的采集任务，按照不同频率定时执行

采集频率：
- Binance 现货数据: 1m, 5m, 1h, 1d
- Binance 合约数据: 每1分钟 (价格、K线、资金费率、持仓量、多空比)
- Gate.io K线数据: 1m, 5m, 1h, 1d
- Ethereum 链上数据: 5m, 1h, 1d
- Hyperliquid 排行榜: 每天一次
- 资金费率 (Binance + Gate.io): 每5分钟
- 新闻数据: 每15分钟

缓存更新频率（性能优化）：
- 价格统计缓存: 每1分钟
- 分析缓存 (技术指标、新闻情绪、资金费率、投资建议): 每5分钟
- Hyperliquid聚合缓存: 每10分钟
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import schedule
import time
import yaml
from datetime import datetime, date, timedelta
from loguru import logger
from typing import List, Dict

from app.collectors.price_collector import MultiExchangeCollector
from app.collectors.binance_futures_collector import BinanceFuturesCollector
from app.collectors.news_collector import NewsAggregator
from app.collectors.enhanced_news_collector import EnhancedNewsAggregator
from app.collectors.smart_money_collector import SmartMoneyCollector
from app.collectors.hyperliquid_collector import HyperliquidCollector
from app.database.db_service import DatabaseService
from app.trading.futures_monitor_service import FuturesMonitorService
from app.trading.auto_futures_trader import AutoFuturesTrader
from app.services.cache_update_service import CacheUpdateService


class UnifiedDataScheduler:
    """统一数据采集调度器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """
        初始化调度器

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取监控币种列表
        self.symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 初始化采集器
        logger.info("初始化数据采集器...")
        self._init_collectors()

        # 初始化数据库服务
        logger.info("初始化数据库服务...")
        db_config = self.config.get('database', {})
        self.db_service = DatabaseService(db_config)

        # 初始化缓存更新服务
        logger.info("初始化缓存更新服务...")
        self.cache_service = CacheUpdateService(self.config)

        # 任务统计
        self.task_stats = {
            'binance_spot_1m': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_spot_5m': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_spot_1h': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_spot_1d': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_futures_1m': {'count': 0, 'last_run': None, 'last_error': None},
            'ethereum_5m': {'count': 0, 'last_run': None, 'last_error': None},
            'ethereum_1h': {'count': 0, 'last_run': None, 'last_error': None},
            'ethereum_1d': {'count': 0, 'last_run': None, 'last_error': None},
            'hyperliquid_daily': {'count': 0, 'last_run': None, 'last_error': None},
            'hyperliquid_monitor': {'count': 0, 'last_run': None, 'last_error': None},
            'funding_rate': {'count': 0, 'last_run': None, 'last_error': None},
            'news': {'count': 0, 'last_run': None, 'last_error': None},
            'futures_monitor': {'count': 0, 'last_run': None, 'last_error': None},
            'auto_trading': {'count': 0, 'last_run': None, 'last_error': None},
            'cache_price': {'count': 0, 'last_run': None, 'last_error': None},
            'cache_analysis': {'count': 0, 'last_run': None, 'last_error': None},
            'cache_hyperliquid': {'count': 0, 'last_run': None, 'last_error': None}
        }

        logger.info(f"调度器初始化完成 - 监控币种: {len(self.symbols)} 个")

    def _init_collectors(self):
        """初始化所有采集器"""
        # 1. 现货价格采集器 (Binance + Gate.io)
        self.price_collector = MultiExchangeCollector(self.config)
        logger.info("  ✓ 现货价格采集器 (Binance + Gate.io)")

        # 1.5 合约数据采集器 (Binance Futures)
        futures_config = self.config.get('binance_futures', {})
        if futures_config.get('enabled', True):  # 默认启用
            binance_config = self.config.get('exchanges', {}).get('binance', {})
            self.futures_collector = BinanceFuturesCollector(binance_config)
            logger.info("  ✓ 合约数据采集器 (Binance Futures)")
        else:
            self.futures_collector = None
            logger.info("  ⊗ 合约数据采集器 (未启用)")

        # 2. 新闻采集器 (基础 + 增强)
        self.news_aggregator = NewsAggregator(self.config)
        self.enhanced_news_aggregator = EnhancedNewsAggregator(self.config)
        logger.info("  ✓ 新闻采集器 (RSS, CryptoPanic, SEC, Twitter, CoinGecko)")

        # 3. 聪明钱采集器 (Ethereum/BSC)
        smart_money_config = self.config.get('smart_money', {})
        if smart_money_config.get('enabled', False):
            self.smart_money_collector = SmartMoneyCollector(self.config)
            logger.info("  ✓ 聪明钱采集器 (Ethereum/BSC)")
        else:
            self.smart_money_collector = None
            logger.info("  ⊗ 聪明钱采集器 (未启用)")

        # 4. Hyperliquid 采集器
        hyperliquid_config = self.config.get('hyperliquid', {})
        if hyperliquid_config.get('enabled', False):
            self.hyperliquid_collector = HyperliquidCollector(hyperliquid_config)
            logger.info("  ✓ Hyperliquid 采集器")
        else:
            self.hyperliquid_collector = None
            logger.info("  ⊗ Hyperliquid 采集器 (未启用)")

        # 5. 合约监控服务
        try:
            self.futures_monitor = FuturesMonitorService(config_path=self.config)
            logger.info("  ✓ 合约监控服务")
        except Exception as e:
            self.futures_monitor = None
            logger.warning(f"  ⊗ 合约监控服务初始化失败: {e}")

        # 6. 自动合约交易服务
        try:
            self.auto_trader = AutoFuturesTrader()
            logger.info("  ✓ 自动合约交易服务 (BTC, ETH, SOL, BNB)")
        except Exception as e:
            self.auto_trader = None
            logger.warning(f"  ⊗ 自动合约交易服务初始化失败: {e}")

    # ==================== 多交易所数据采集任务 ====================

    async def collect_binance_data(self, timeframe: str = '5m'):
        """
        采集所有启用的交易所数据 (Binance + Gate.io等)

        Args:
            timeframe: 时间周期 (1m, 5m, 1h, 1d)
        """
        task_name = f'binance_spot_{timeframe}'
        try:
            # 获取启用的交易所列表
            enabled_exchanges = list(self.price_collector.collectors.keys())
            exchanges_str = ' + '.join(enabled_exchanges) if enabled_exchanges else 'Binance'

            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集多交易所数据 ({exchanges_str}) ({timeframe})...")

            for symbol in self.symbols:
                try:
                    # 1. 采集实时价格数据 (1m和5m任务都采集) - 自动从所有交易所采集
                    if timeframe in ['1m', '5m']:
                        await self._collect_ticker(symbol)

                    # 2. 采集K线数据 - 目前只从Binance采集
                    await self._collect_klines(symbol, timeframe)

                    # 小延迟，避免请求过快
                    await asyncio.sleep(0.3)

                except Exception as e:
                    logger.error(f"  采集 {symbol} 数据失败: {e}")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            logger.info(f"  ✓ 多交易所数据采集完成 ({exchanges_str}) ({timeframe})")

        except Exception as e:
            logger.error(f"多交易所数据采集任务失败 ({timeframe}): {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    async def _collect_ticker(self, symbol: str):
        """采集实时价格数据 - 自动从所有启用的交易所采集"""
        try:
            # fetch_price() 会自动从所有启用的交易所(Binance + Gate.io等)获取价格
            prices = await self.price_collector.fetch_price(symbol)

            if prices:
                for price_data in prices:
                    self.db_service.save_price_data(price_data)
                    exchange = price_data.get('exchange', 'unknown')
                    logger.debug(f"    ✓ [{exchange}] {symbol} 价格: ${price_data['price']:,.2f} "
                               f"(24h: {price_data['change_24h']:+.2f}%)")

        except Exception as e:
            logger.error(f"    采集 {symbol} 实时价格失败: {e}")

    async def _collect_klines(self, symbol: str, timeframe: str):
        """采集K线数据 - 自动从可用的交易所采集"""
        try:
            # 获取启用的交易所列表
            enabled_exchanges = list(self.price_collector.collectors.keys())

            # 优先级：binance > gate > 其他
            priority_exchanges = ['binance', 'gate'] + [e for e in enabled_exchanges if e not in ['binance', 'gate']]

            df = None
            used_exchange = None

            # 尝试从优先级列表中的交易所获取K线
            for exchange in priority_exchanges:
                if exchange not in enabled_exchanges:
                    continue

                try:
                    df = await self.price_collector.fetch_ohlcv(
                        symbol,
                        timeframe=timeframe,
                        exchange=exchange
                    )

                    if df is not None and len(df) > 0:
                        used_exchange = exchange
                        logger.debug(f"    ✓ 从 {exchange} 获取 {symbol} K线数据")
                        break
                except Exception as e:
                    logger.debug(f"    ⊗ {exchange} 不支持 {symbol}: {e}")
                    continue

            if df is not None and len(df) > 0:
                # 只保存最新的一条K线
                latest_kline = df.iloc[-1]

                kline_data = {
                    'symbol': symbol,
                    'exchange': used_exchange,
                    'timeframe': timeframe,
                    'open_time': int(latest_kline['timestamp'].timestamp() * 1000),
                    'timestamp': latest_kline['timestamp'],
                    'open': latest_kline['open'],
                    'high': latest_kline['high'],
                    'low': latest_kline['low'],
                    'close': latest_kline['close'],
                    'volume': latest_kline['volume']
                }

                self.db_service.save_kline_data(kline_data)
                logger.debug(f"    ✓ [{used_exchange}] {symbol} K线({timeframe}): "
                           f"C:{latest_kline['close']:.2f}")
            else:
                logger.debug(f"    ⊗ {symbol} K线({timeframe}): 所有交易所均不可用")

        except Exception as e:
            logger.error(f"    采集 {symbol} K线({timeframe})失败: {e}")

    # ==================== 币安合约数据采集任务 ====================

    async def collect_binance_futures_data(self):
        """采集币安合约数据 (每1分钟) - 包括价格、K线、资金费率、持仓量、多空比"""
        if not self.futures_collector:
            return

        task_name = 'binance_futures_1m'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集币安合约数据...")

            collected_count = 0
            error_count = 0

            for symbol in self.symbols:
                try:
                    # 获取所有合约数据
                    data = await self.futures_collector.fetch_all_data(symbol, timeframe='1m')

                    if not data:
                        logger.warning(f"  ⊗ {symbol}: 未获取到数据")
                        error_count += 1
                        continue

                    # 1. 保存ticker数据
                    if data.get('ticker'):
                        ticker = data['ticker']
                        price_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'timestamp': ticker['timestamp'],
                            'price': ticker['price'],
                            'open': ticker['open'],
                            'high': ticker['high'],
                            'low': ticker['low'],
                            'close': ticker['close'],
                            'volume': ticker['volume'],
                            'quote_volume': ticker['quote_volume'],
                            'bid': 0,
                            'ask': 0,
                            'change_24h': ticker['price_change_percent']
                        }
                        self.db_service.save_price_data(price_data)

                    # 2. 保存K线数据
                    if data.get('kline'):
                        kline = data['kline']
                        kline_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'timeframe': '1m',
                            'open_time': int(kline['open_time']),
                            'timestamp': kline['timestamp'],
                            'open': kline['open'],
                            'high': kline['high'],
                            'low': kline['low'],
                            'close': kline['close'],
                            'volume': kline['volume']
                        }
                        self.db_service.save_kline_data(kline_data)

                    # 3. 保存资金费率
                    if data.get('funding_rate'):
                        funding = data['funding_rate']
                        funding_data = {
                            'exchange': 'binance_futures',
                            'symbol': symbol,
                            'funding_rate': funding['funding_rate'],
                            'funding_time': funding['funding_time'],
                            'timestamp': funding['timestamp'],
                            'mark_price': funding['mark_price'],
                            'index_price': funding['index_price'],
                            'next_funding_time': funding['next_funding_time']
                        }
                        self.db_service.save_funding_rate_data(funding_data)

                    # 4. 保存持仓量
                    if data.get('open_interest'):
                        oi = data['open_interest']
                        oi_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'open_interest': oi['open_interest'],
                            'open_interest_value': oi.get('open_interest_value'),
                            'timestamp': oi['timestamp']
                        }
                        self.db_service.save_open_interest_data(oi_data)

                    # 5. 保存多空比
                    if data.get('long_short_ratio'):
                        ls = data['long_short_ratio']
                        ls_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'period': '5m',
                            'long_account': ls['long_account'],
                            'short_account': ls['short_account'],
                            'long_short_ratio': ls['long_short_ratio'],
                            'timestamp': ls['timestamp']
                        }
                        self.db_service.save_long_short_ratio_data(ls_data)

                    # 日志输出
                    price = data['ticker']['price'] if data.get('ticker') else 0
                    funding_rate = data['funding_rate']['funding_rate'] * 100 if data.get('funding_rate') else 0
                    oi = data['open_interest']['open_interest'] if data.get('open_interest') else 0
                    ls_ratio = data['long_short_ratio']['long_short_ratio'] if data.get('long_short_ratio') else 0

                    logger.info(
                        f"  ✓ {symbol}: "
                        f"价格=${price:,.2f}, "
                        f"费率={funding_rate:+.4f}%, "
                        f"持仓={oi:,.0f}, "
                        f"多空比={ls_ratio:.2f}"
                    )

                    collected_count += 1

                    # 延迟避免API限流和网络错误
                    await asyncio.sleep(0.5)

                except Exception as e:
                    logger.error(f"  ✗ {symbol}: {e}")
                    error_count += 1

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

            elapsed_time = (datetime.now() - self.task_stats[task_name]['last_run']).total_seconds() if self.task_stats[task_name]['last_run'] else 0
            logger.info(
                f"  ✓ 合约数据采集完成: 成功 {collected_count}/{len(self.symbols)}, "
                f"失败 {error_count}"
            )

        except Exception as e:
            logger.error(f"合约数据采集任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== 资金费率采集任务 ====================

    async def collect_funding_rates(self):
        """采集资金费率数据 (每5分钟) - 从所有交易所"""
        task_name = 'funding_rate'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集资金费率...")

            total_count = 0

            for symbol in self.symbols:
                try:
                    # 从所有启用的交易所采集资金费率
                    for exchange_id, collector in self.price_collector.collectors.items():
                        try:
                            # 检查采集器是否有 fetch_funding_rate 方法
                            if hasattr(collector, 'fetch_funding_rate'):
                                funding_data = await collector.fetch_funding_rate(symbol)

                                if funding_data:
                                    self.db_service.save_funding_rate_data(funding_data)
                                    funding_rate_pct = funding_data['funding_rate'] * 100
                                    logger.info(f"    ✓ [{exchange_id}] {symbol} 资金费率: {funding_rate_pct:+.4f}%")
                                    total_count += 1

                                await asyncio.sleep(0.2)

                        except Exception as e:
                            logger.error(f"    采集 [{exchange_id}] {symbol} 资金费率失败: {e}")

                    await asyncio.sleep(0.3)

                except Exception as e:
                    logger.error(f"    采集 {symbol} 资金费率失败: {e}")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            logger.info(f"  ✓ 资金费率采集完成 (共 {total_count} 条)")

        except Exception as e:
            logger.error(f"资金费率采集任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== 新闻数据采集任务 ====================

    async def collect_news(self):
        """采集新闻数据 (每15分钟) - 多渠道采集"""
        task_name = 'news'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集新闻数据...")

            # 提取币种代码 (BTC/USDT -> BTC)
            symbols_codes = [symbol.split('/')[0] for symbol in self.symbols]

            # 并发采集: 基础渠道 + 增强渠道
            basic_news_task = self.news_aggregator.collect_all(symbols_codes)
            enhanced_news_task = self.enhanced_news_aggregator.collect_all(symbols_codes)

            basic_news, enhanced_news = await asyncio.gather(
                basic_news_task,
                enhanced_news_task,
                return_exceptions=True
            )

            # 合并新闻
            all_news = []
            if not isinstance(basic_news, Exception):
                all_news.extend(basic_news)
                logger.info(f"    基础渠道: {len(basic_news)} 条")
            else:
                logger.error(f"    基础渠道采集失败: {basic_news}")

            if not isinstance(enhanced_news, Exception):
                all_news.extend(enhanced_news)
                logger.info(f"    增强渠道: {len(enhanced_news)} 条 (SEC, Twitter, CoinGecko)")
            else:
                logger.error(f"    增强渠道采集失败: {enhanced_news}")

            if all_news:
                # 去重 (基于 URL)
                seen_urls = set()
                unique_news = []
                for news in all_news:
                    url = news.get('url', '')
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        unique_news.append(news)

                # 批量保存新闻
                count = self.db_service.save_news_batch(unique_news)
                logger.info(f"  ✓ 新闻数据: 总采集 {len(all_news)} 条, 去重后 {len(unique_news)} 条, 保存 {count} 条新数据")

                # 显示重要新闻
                critical_news = [n for n in unique_news if n.get('importance') == 'critical']
                if critical_news:
                    logger.info(f"  ⚠️  重要新闻 ({len(critical_news)} 条):")
                    for news in critical_news[:3]:
                        logger.info(f"    - [{news.get('source')}] {news.get('title', '')[:60]}")
            else:
                logger.info(f"  ✓ 新闻数据: 未采集到新新闻")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

        except Exception as e:
            logger.error(f"新闻采集任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== Ethereum 链上数据采集任务 ====================

    async def collect_ethereum_data(self, timeframe: str = '5m'):
        """
        采集 Ethereum 链上聪明钱数据

        Args:
            timeframe: 时间周期 (5m, 1h, 1d)
        """
        if not self.smart_money_collector:
            return

        task_name = f'ethereum_{timeframe}'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集 Ethereum 链上数据 ({timeframe})...")

            # 根据时间周期确定回溯时间
            lookback_hours_map = {
                '5m': 1,    # 5分钟任务: 回溯1小时
                '1h': 6,    # 1小时任务: 回溯6小时
                '1d': 24,   # 1天任务: 回溯24小时
                '1mon': 720 # 1月任务: 回溯30天
            }
            lookback_hours = lookback_hours_map.get(timeframe, 24)

            # 监控所有配置的地址
            results = await self.smart_money_collector.monitor_all_addresses(hours=lookback_hours)

            total_transactions = sum(len(txs) for txs in results.values())
            logger.info(f"  ✓ Ethereum 数据: 监控 {len(results)} 个地址, 发现 {total_transactions} 笔交易")

            # 保存交易到数据库
            for address, transactions in results.items():
                for tx in transactions:
                    try:
                        self.db_service.save_smart_money_transaction(tx)
                    except Exception as e:
                        logger.debug(f"    保存交易失败: {e}")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

        except Exception as e:
            logger.error(f"Ethereum 数据采集任务失败 ({timeframe}): {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== 自动合约交易任务 ====================

    async def run_auto_trading(self):
        """自动合约交易 - 根据投资建议开仓 (每30分钟)"""
        if not self.auto_trader:
            return

        task_name = 'auto_trading'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 🤖 开始自动合约交易...")

            # 执行自动交易
            results = self.auto_trader.run_auto_trading_cycle()

            # 统计
            total = results['processed']
            opened = results['opened']
            skipped = results['skipped']
            failed = results['failed']

            logger.info(f"  ✓ 自动交易: 处理 {total}, 开仓 {opened}, 跳过 {skipped}, 失败 {failed}")

            # 重要事件通知
            if opened > 0:
                logger.info(f"  🚀 {opened} 个新持仓已开启")
                for detail in results['details']:
                    if detail['status'] == 'opened':
                        logger.info(f"     • {detail['symbol']}: {detail['recommendation']} "
                                  f"(置信度 {detail['confidence']:.1f}%, ID: {detail['position_id']})")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

        except Exception as e:
            logger.error(f"自动交易任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== 合约监控任务 ====================

    async def monitor_futures_positions(self):
        """监控合约持仓 - 止盈止损触发 (每1分钟)"""
        if not self.futures_monitor:
            return

        task_name = 'futures_monitor'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始监控合约持仓...")

            # 执行监控
            results = self.futures_monitor.monitor_positions()

            if results:
                total = results['total_positions']
                monitoring = results['monitoring']
                stop_loss = results['stop_loss']
                take_profit = results['take_profit']
                liquidated = results['liquidated']

                logger.info(f"  ✓ 合约监控: 总持仓 {total}, 监控中 {monitoring}, "
                          f"止损 {stop_loss}, 止盈 {take_profit}, 强平 {liquidated}")

                # 重要事件通知
                if liquidated > 0:
                    logger.warning(f"  ⚠️  {liquidated} 个持仓被强制平仓！")
                if stop_loss > 0:
                    logger.info(f"  🛑 {stop_loss} 个持仓触发止损")
                if take_profit > 0:
                    logger.info(f"  ✅ {take_profit} 个持仓触发止盈")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

        except Exception as e:
            logger.error(f"合约监控任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== Hyperliquid 数据采集任务 ====================

    async def collect_hyperliquid_leaderboard(self):
        """采集 Hyperliquid 排行榜数据 (每天一次)"""
        if not self.hyperliquid_collector:
            return

        task_name = 'hyperliquid_daily'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集 Hyperliquid 排行榜...")

            # 1. 获取排行榜
            leaderboard = await self.hyperliquid_collector.fetch_leaderboard()

            if not leaderboard:
                logger.warning("  ⊗ 未获取到 Hyperliquid 排行榜数据")
                return

            logger.info(f"  ✓ 获取到 {len(leaderboard)} 个交易者")

            # 2. 过滤高 PnL 交易者
            auto_discover_config = self.config.get('hyperliquid', {}).get('auto_discover', {})
            min_pnl = auto_discover_config.get('min_pnl', 10000)
            period = auto_discover_config.get('period', 'week')

            smart_traders = await self.hyperliquid_collector.discover_smart_traders(
                period=period,
                min_pnl=min_pnl
            )

            logger.info(f"  ✓ 发现 {len(smart_traders)} 个聪明交易者 (周 PnL >= ${min_pnl:,})")

            # 3. 保存到数据库
            from app.database.hyperliquid_db import HyperliquidDB

            # 计算周期时间范围
            today = date.today()
            week_start = today - timedelta(days=today.weekday())
            week_end = week_start + timedelta(days=6)

            saved_count = 0
            added_to_monitor = 0
            with HyperliquidDB() as db:
                for trader in smart_traders:
                    try:
                        # 1. 保存周表现数据
                        db.save_weekly_performance(
                            address=trader['address'],
                            display_name=trader.get('displayName', trader['address'][:10]),
                            week_start=week_start,
                            week_end=week_end,
                            pnl=trader['pnl'],
                            roi=trader['roi'],
                            volume=trader.get('volume', 0),
                            account_value=trader.get('accountValue', 0)
                        )
                        saved_count += 1

                        # 2. 添加到监控钱包列表（自动发现）
                        monitor_id = db.add_monitored_wallet(
                            address=trader['address'],
                            label=trader.get('displayName', trader['address'][:10]),
                            monitor_type='auto',  # 标记为自动发现
                            pnl=trader['pnl'],
                            roi=trader['roi'],
                            account_value=trader.get('accountValue', 0)
                        )
                        if monitor_id:
                            added_to_monitor += 1

                    except Exception as e:
                        logger.debug(f"    保存交易者数据失败: {e}")

            logger.info(f"  ✓ 保存 {saved_count} 个交易者数据，添加 {added_to_monitor} 个到监控列表")

            # 4. 显示 Top 5 交易者
            logger.info("  Top 5 交易者:")
            for i, trader in enumerate(smart_traders[:5], 1):
                logger.info(f"    {i}. {trader.get('displayName', trader['address'][:10])} - "
                          f"PnL: ${trader['pnl']:,.2f}, ROI: {trader['roi']:.2f}%")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

        except Exception as e:
            logger.error(f"Hyperliquid 数据采集任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    async def monitor_hyperliquid_wallets(self):
        """监控 Hyperliquid 聪明钱包的资金动态 (每30分钟)"""
        if not self.hyperliquid_collector:
            return

        task_name = 'hyperliquid_monitor'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始监控 Hyperliquid 聪明钱包...")

            from app.database.hyperliquid_db import HyperliquidDB

            with HyperliquidDB() as db:
                # 获取活跃监控钱包列表
                all_wallets = db.get_monitored_wallets(active_only=True)

                if not all_wallets:
                    logger.info("  ⊗ 暂无监控钱包")
                    return

                # ⚠️ 性能优化：每次只监控50个钱包（按最后检查时间排序，优先监控最久未检查的）
                # 这样可以避免任务超时，并确保所有钱包都能被轮流监控
                MAX_WALLETS_PER_RUN = 50

                # 按 last_check_at 排序（None 排在最前面）
                all_wallets.sort(key=lambda w: w.get('last_check_at') or datetime.min)
                monitored_wallets = all_wallets[:MAX_WALLETS_PER_RUN]

                logger.info(f"  总钱包数: {len(all_wallets)}, 本次监控: {len(monitored_wallets)} 个 (最久未检查)")

                wallet_updates = []
                total_trades = 0
                total_positions = 0

                for wallet in monitored_wallets:
                    address = wallet['address']
                    label = wallet.get('label') or wallet.get('display_name') or address[:10]

                    try:
                        # 监控钱包活动 (最近1小时)
                        result = await self.hyperliquid_collector.monitor_address(
                            address=address,
                            hours=1
                        )

                        # 保存交易记录
                        recent_trades = result.get('recent_trades', [])
                        for trade in recent_trades:
                            trade_data = {
                                'coin': trade['coin'],
                                'side': trade['action'],  # LONG/SHORT
                                'action': 'TRADE',
                                'price': trade['price'],
                                'size': trade['size'],
                                'notional_usd': trade['notional_usd'],
                                'closed_pnl': trade['closed_pnl'],
                                'trade_time': trade['timestamp'],
                                'raw_data': trade.get('raw_data', {})
                            }
                            db.save_wallet_trade(address, trade_data)
                            total_trades += 1

                        # 保存持仓快照
                        positions = result.get('positions', [])
                        snapshot_time = datetime.now()
                        for pos in positions:
                            position_data = {
                                'coin': pos['coin'],
                                'side': pos['side'],
                                'size': pos['size'],
                                'entry_price': pos['entry_price'],
                                'mark_price': pos.get('mark_price', pos['entry_price']),
                                'notional_usd': pos['notional_usd'],
                                'unrealized_pnl': pos['unrealized_pnl'],
                                'leverage': 1,
                                'raw_data': {}
                            }
                            db.save_wallet_position(address, position_data, snapshot_time)
                            total_positions += 1

                        # 更新检查时间
                        last_trade_time = recent_trades[0]['timestamp'] if recent_trades else None
                        db.update_wallet_check_time(wallet['trader_id'], last_trade_time)

                        # 记录有活动的钱包
                        if recent_trades or positions:
                            stats = result.get('statistics', {})
                            wallet_updates.append({
                                'label': label,
                                'trades': len(recent_trades),
                                'positions': len(positions),
                                'net_flow': stats.get('net_flow_usd', 0),
                                'total_pnl': stats.get('total_pnl', 0)
                            })

                        # 延迟避免API限流
                        await asyncio.sleep(2)

                    except Exception as e:
                        logger.error(f"  监控钱包 {label} 失败: {e}")
                        db.update_wallet_check_time(wallet['trader_id'])

                # 汇总报告
                logger.info(f"  ✓ 监控完成: 检查 {len(monitored_wallets)} 个钱包, "
                          f"新交易 {total_trades} 笔, 持仓 {total_positions} 个")

                # 显示有活动的钱包
                if wallet_updates:
                    logger.info(f"  活跃钱包 ({len(wallet_updates)} 个):")
                    for w in wallet_updates[:5]:
                        pnl_str = f"PnL: ${w['total_pnl']:,.0f}" if w['total_pnl'] != 0 else ""
                        flow_str = f"净流: ${w['net_flow']:,.0f}" if w['net_flow'] != 0 else ""
                        logger.info(f"    • {w['label']}: {w['trades']}笔交易, {w['positions']}个持仓 {pnl_str} {flow_str}")

            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()

        except Exception as e:
            logger.error(f"Hyperliquid 钱包监控任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== 调度器控制 ====================

    async def run_task_async(self, coro):
        """异步运行任务（schedule 兼容）"""
        await coro

    def schedule_tasks(self):
        """设置所有定时任务"""
        logger.info("设置定时任务...")

        # 获取启用的交易所列表
        enabled_exchanges = list(self.price_collector.collectors.keys())
        exchanges_str = ' + '.join(enabled_exchanges) if enabled_exchanges else 'Binance'

        # 1. 现货数据 (Binance + Gate.io等)
        schedule.every(1).minutes.do(
            lambda: asyncio.run(self.collect_binance_data('1m'))
        )
        logger.info(f"  ✓ 现货({exchanges_str}) 1分钟数据 - 每 1 分钟")

        schedule.every(5).minutes.do(
            lambda: asyncio.run(self.collect_binance_data('5m'))
        )
        logger.info(f"  ✓ 现货({exchanges_str}) 5分钟数据 - 每 5 分钟")

        schedule.every(1).hours.do(
            lambda: asyncio.run(self.collect_binance_data('1h'))
        )
        logger.info(f"  ✓ 现货({exchanges_str}) 1小时数据 - 每 1 小时")

        schedule.every().day.at("00:05").do(
            lambda: asyncio.run(self.collect_binance_data('1d'))
        )
        logger.info(f"  ✓ 现货({exchanges_str}) 1天数据 - 每天 00:05")

        # 1.5 币安合约数据
        if self.futures_collector:
            schedule.every(1).minutes.do(
                lambda: asyncio.run(self.collect_binance_futures_data())
            )
            logger.info("  ✓ 币安合约数据 (价格+K线+资金费率+持仓量+多空比) - 每 1 分钟")

        # 2. 资金费率
        schedule.every(5).minutes.do(
            lambda: asyncio.run(self.collect_funding_rates())
        )
        logger.info("  ✓ 资金费率 - 每 5 分钟")

        # 3. 新闻数据
        schedule.every(15).minutes.do(
            lambda: asyncio.run(self.collect_news())
        )
        logger.info("  ✓ 新闻数据 - 每 15 分钟")

        # 3.5 自动合约交易
        if self.auto_trader:
            schedule.every(30).minutes.do(
                lambda: asyncio.run(self.run_auto_trading())
            )
            logger.info("  ✓ 自动合约交易 (BTC, ETH, SOL, BNB) - 每 30 分钟")

        # 3.6 合约持仓监控
        if self.futures_monitor:
            schedule.every(1).minutes.do(
                lambda: asyncio.run(self.monitor_futures_positions())
            )
            logger.info("  ✓ 合约持仓监控 (止盈止损) - 每 1 分钟")

        # 4. Ethereum 链上数据
        if self.smart_money_collector:
            schedule.every(5).minutes.do(
                lambda: asyncio.run(self.collect_ethereum_data('5m'))
            )
            logger.info("  ✓ Ethereum 5分钟数据 - 每 5 分钟")

            schedule.every(1).hours.do(
                lambda: asyncio.run(self.collect_ethereum_data('1h'))
            )
            logger.info("  ✓ Ethereum 1小时数据 - 每 1 小时")

            schedule.every().day.at("00:10").do(
                lambda: asyncio.run(self.collect_ethereum_data('1d'))
            )
            logger.info("  ✓ Ethereum 1天数据 - 每天 00:10")

        # 5. Hyperliquid 排行榜
        if self.hyperliquid_collector:
            schedule.every().day.at("02:00").do(
                lambda: asyncio.run(self.collect_hyperliquid_leaderboard())
            )
            logger.info("  ✓ Hyperliquid 排行榜 - 每天 02:00")

            # 6. Hyperliquid 钱包监控
            schedule.every(30).minutes.do(
                lambda: asyncio.run(self.monitor_hyperliquid_wallets())
            )
            logger.info("  ✓ Hyperliquid 钱包监控 - 每 30 分钟")

        # 7. 缓存更新任务
        logger.info("\n  🚀 性能优化: 缓存自动更新")

        # 价格缓存 - 每1分钟
        schedule.every(1).minutes.do(
            lambda: asyncio.run(self.update_price_cache())
        )
        logger.info("  ✓ 价格统计缓存 - 每 1 分钟")

        # 分析缓存 - 每5分钟
        schedule.every(5).minutes.do(
            lambda: asyncio.run(self.update_analysis_cache())
        )
        logger.info("  ✓ 分析缓存 (技术指标+新闻+资金费率+投资建议) - 每 5 分钟")

        # Hyperliquid缓存 - 每10分钟
        if self.hyperliquid_collector:
            schedule.every(10).minutes.do(
                lambda: asyncio.run(self.update_hyperliquid_cache())
            )
            logger.info("  ✓ Hyperliquid聚合缓存 - 每 10 分钟")

        logger.info("所有定时任务设置完成")

    async def run_initial_collection(self):
        """首次启动时执行一次所有采集任务"""
        logger.info("\n" + "=" * 80)
        logger.info("首次数据采集开始...")
        logger.info("=" * 80 + "\n")

        # 1. Binance 现货数据 (先采集1分钟数据，获取最新价格)
        await self.collect_binance_data('1m')
        await asyncio.sleep(2)

        await self.collect_binance_data('5m')
        await asyncio.sleep(2)

        # 1.5 Binance 合约数据
        if self.futures_collector:
            await self.collect_binance_futures_data()
            await asyncio.sleep(2)

        # 2. 资金费率
        await self.collect_funding_rates()
        await asyncio.sleep(2)

        # 3. 新闻数据
        await self.collect_news()
        await asyncio.sleep(2)

        # 4. Ethereum 数据
        if self.smart_money_collector:
            await self.collect_ethereum_data('1h')
            await asyncio.sleep(2)

        # 5. Hyperliquid 数据
        if self.hyperliquid_collector:
            await self.collect_hyperliquid_leaderboard()

        # 6. 首次缓存更新
        logger.info("\n🚀 性能优化：首次缓存更新...")
        await self.update_price_cache()
        await asyncio.sleep(2)

        await self.update_analysis_cache()
        await asyncio.sleep(2)

        if self.hyperliquid_collector:
            await self.update_hyperliquid_cache()

        logger.info("\n" + "=" * 80)
        logger.info("首次数据采集完成")
        logger.info("=" * 80 + "\n")

    def print_status(self):
        """打印调度器状态"""
        logger.info("\n" + "=" * 80)
        logger.info("调度器运行状态")
        logger.info("=" * 80)

        for task_name, stats in self.task_stats.items():
            status = "✓" if stats['last_run'] else "⊗"
            last_run = stats['last_run'].strftime('%H:%M:%S') if stats['last_run'] else "未运行"
            error = f" (错误: {stats['last_error'][:30]})" if stats['last_error'] else ""

            logger.info(f"{status} {task_name:20s} | 运行次数: {stats['count']:3d} | "
                       f"最后运行: {last_run}{error}")

        logger.info("=" * 80 + "\n")

    def start(self):
        """启动调度器"""
        logger.info("\n" + "=" * 80)
        logger.info("统一数据采集调度器启动")
        logger.info("=" * 80)
        logger.info(f"监控币种: {', '.join(self.symbols)}")
        logger.info(f"数据库类型: {self.config.get('database', {}).get('type', 'mysql')}")
        logger.info("=" * 80 + "\n")

        # 设置定时任务
        self.schedule_tasks()

        # 首次采集
        asyncio.run(self.run_initial_collection())

        # 定期打印状态 (每小时)
        schedule.every(1).hours.do(self.print_status)

        logger.info("\n调度器已启动，按 Ctrl+C 停止\n")

        # 保持运行
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("\n\n收到停止信号，正在关闭...")
            self.stop()

    # ==================== 缓存更新任务 ====================

    async def update_price_cache(self):
        """更新价格统计缓存 (每1分钟)"""
        task_name = 'cache_price'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始更新价格缓存...")

            await self.cache_service.update_price_stats_cache(self.symbols)

            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            logger.info(f"  ✓ 价格缓存更新完成 - {len(self.symbols)} 个币种")

        except Exception as e:
            logger.error(f"更新价格缓存失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    async def update_analysis_cache(self):
        """更新分析类缓存 (每5分钟) - 技术指标、新闻情绪、资金费率、投资建议"""
        task_name = 'cache_analysis'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始更新分析缓存...")

            # 并发更新4个分析缓存
            await asyncio.gather(
                self.cache_service.update_technical_indicators_cache(self.symbols),
                self.cache_service.update_news_sentiment_aggregation(self.symbols),
                self.cache_service.update_funding_rate_stats(self.symbols),
                self.cache_service.update_recommendations_cache(self.symbols),
                return_exceptions=True
            )

            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            logger.info(f"  ✓ 分析缓存更新完成 (技术指标、新闻情绪、资金费率、投资建议)")

        except Exception as e:
            logger.error(f"更新分析缓存失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    async def update_hyperliquid_cache(self):
        """更新Hyperliquid聚合缓存 (每10分钟)"""
        task_name = 'cache_hyperliquid'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始更新Hyperliquid缓存...")

            await self.cache_service.update_hyperliquid_aggregation(self.symbols)

            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            logger.info(f"  ✓ Hyperliquid缓存更新完成 - {len(self.symbols)} 个币种")

        except Exception as e:
            logger.error(f"更新Hyperliquid缓存失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    # ==================== 调度器控制 ====================

    def stop(self):
        """停止调度器"""
        logger.info("关闭数据库连接...")
        self.db_service.close()
        logger.info("调度器已停止")


def main():
    """主函数"""
    # 配置日志
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    logger.add(
        "logs/scheduler_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
    )

    # 创建并启动调度器
    scheduler = UnifiedDataScheduler(config_path='config.yaml')
    scheduler.start()


if __name__ == '__main__':
    main()
