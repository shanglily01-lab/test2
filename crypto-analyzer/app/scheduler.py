"""
统一数据采集调度器
整合所有数据源的采集任务，按照不同频率定时执行

采集频率：
- Binance 现货数据: 5m, 15m, 1h, 1d (移除了1m高频采集)
- Binance 合约数据: 由 fast_collector_service.py 单独采集 (5m K线 + 价格)
- Ethereum 链上数据: 5m, 1h, 1d
- Hyperliquid 排行榜: 每天一次
- 资金费率 (Binance): 每5分钟
- 新闻数据: 每15分钟

缓存更新频率（性能优化）：
- 价格统计缓存: 已移除高频更新
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
import threading
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
# 合约监控服务已移至 main.py，不再在此导入
from app.trading.auto_futures_trader import AutoFuturesTrader
from app.trading.futures_trading_engine import FuturesTradingEngine
from app.services.cache_update_service import CacheUpdateService


class UnifiedDataScheduler:
    """统一数据采集调度器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """
        初始化调度器

        Args:
            config_path: 配置文件路径
        """
        # 加载配置（支持环境变量）
        from app.utils.config_loader import load_config
        self.config = load_config(Path(config_path))

        # 获取监控币种列表
        self.symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 初始化数据库服务
        logger.info("初始化数据库服务...")
        db_config = self.config.get('database', {})
        self.db_service = DatabaseService(db_config)

        # 初始化采集器
        logger.info("初始化数据采集器...")
        self._init_collectors()

        # 初始化缓存更新服务
        logger.info("初始化缓存更新服务...")
        self.cache_service = CacheUpdateService(self.config)

        # 初始化 Binance 公告监控
        try:
            from app.services.binance_news_monitor import BinanceNewsMonitor
            from app.services.trade_notifier import init_trade_notifier
            _notifier = init_trade_notifier(self.config)
            _mysql_cfg = self.config.get("database", {}).get("mysql", {})
            self.binance_news_monitor = BinanceNewsMonitor(_mysql_cfg, notifier=_notifier)
            logger.info("初始化 Binance 公告监控器 OK")
        except Exception as e:
            self.binance_news_monitor = None
            logger.warning("Binance 公告监控器初始化失败: %s", e)

        # 任务统计
        self.task_stats = {
            'binance_spot_1m': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_spot_5m': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_spot_15m': {'count': 0, 'last_run': None, 'last_error': None},
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
            'cache_price': {'count': 0, 'last_run': None, 'last_error': None},
            'cache_analysis': {'count': 0, 'last_run': None, 'last_error': None},
            'cache_hyperliquid': {'count': 0, 'last_run': None, 'last_error': None},
            'etf_daily': {'count': 0, 'last_run': None, 'last_error': None},
            'bitcointreasuries_daily': {'count': 0, 'last_run': None, 'last_error': None},
            'futures_equity_update': {'count': 0, 'last_run': None, 'last_error': None},
            'binance_news': {'count': 0, 'last_run': None, 'last_error': None},
            'correct_live_trades': {'count': 0, 'last_run': None, 'last_error': None},
            'deepseek_explore': {'count': 0, 'last_run': None, 'last_error': None},
            'deepseek_predict': {'count': 0, 'last_run': None, 'last_error': None},
            'gemini_position_advisor': {'count': 0, 'last_run': None, 'last_error': None},
            'deepseek_position_advisor': {'count': 0, 'last_run': None, 'last_error': None},
            'paper_closed_live_sync': {'count': 0, 'last_run': None, 'last_error': None}
        }
        self._gemini_position_advisor_running = False
        self._deepseek_position_advisor_running = False
        self._paper_closed_live_sync_running = False

        logger.info(f"调度器初始化完成 - 监控币种: {len(self.symbols)} 个")

    def _init_collectors(self):
        """初始化所有采集器"""
        # 1. 现货价格采集器 (Binance)
        self.price_collector = MultiExchangeCollector(self.config)
        logger.info("  ✓ 现货价格采集器 (Binance)")

        # 1.5 合约数据采集器 (Binance Futures)
        futures_config = self.config.get('binance_futures', {})
        if futures_config.get('enabled', True):  # 默认启用
            try:
                binance_config = self.config.get('exchanges', {}).get('binance', {})
                self.futures_collector = BinanceFuturesCollector(binance_config)
                logger.info("  ✓ 合约数据采集器 (Binance Futures)")
            except Exception as e:
                logger.warning(f"  ⚠️  合约数据采集器初始化失败: {e}")
                logger.debug(f"  错误详情: {type(e).__name__}: {str(e)}")
                self.futures_collector = None
                logger.info("  ⊗ 合约数据采集器 (初始化失败，将跳过合约数据采集)")
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

        # 5. 合约监控服务（已移至 main.py，由 FastAPI 生命周期管理，此处不再初始化）
        self.futures_monitor = None

        # 5.5. 合约交易引擎（用于更新总权益）
        db_config = self.config.get('database', {}).get('mysql', {})
        try:
            from app.services.trade_notifier import init_trade_notifier
            trade_notifier = init_trade_notifier(self.config)
            self.futures_engine = FuturesTradingEngine(db_config, trade_notifier=trade_notifier)
            logger.info("  ✓ 合约交易引擎 (用于更新总权益)")
        except Exception as e:
            logger.warning(f"  ⊗ 合约交易引擎初始化失败: {e}")
            self.futures_engine = None

        # 注意: 自动合约交易和评级更新已移至 smart_trader_service.py
        # 6. 自动合约交易服务 - 已移至 smart_trader_service.py
        # 7. 交易对评级管理器 - 已移至 smart_trader_service.py (每天凌晨2点自动运行)



    # ==================== 多交易所数据采集任务 ====================

    async def collect_binance_data(self, timeframe: str = '5m'):
        """
        采集所有启用的交易所数据 (Binance)

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
                    # 1. 实时价格数据已改用 WebSocket 推送，不再轮询采集
                    # if timeframe in ['1m', '5m']:
                    #     await self._collect_ticker(symbol)

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
            # fetch_price() 会自动从所有启用的交易所获取价格
            prices = await self.price_collector.fetch_price(symbol)

            if prices:
                for price_data in prices:
                    self.db_service.save_price_data(price_data)
                    exchange = price_data.get('exchange', 'unknown')
                    logger.info(f"    ✓ [{exchange}] {symbol} 价格: ${price_data['price']:,.2f} "
                               f"(24h: {price_data['change_24h']:+.2f}%)")
            else:
                logger.warning(f"    ⊗ {symbol}: 未获取到价格数据")

        except Exception as e:
            logger.error(f"    采集 {symbol} 实时价格失败: {e}")

    async def _collect_klines(self, symbol: str, timeframe: str):
        """采集K线数据 - 自动从可用的交易所采集"""
        try:
            # 获取启用的交易所列表
            enabled_exchanges = list(self.price_collector.collectors.keys())

            # 优先级：binance > 其他
            priority_exchanges = ['binance'] + [e for e in enabled_exchanges if e != 'binance']

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
                    'volume': latest_kline['volume'],
                    'quote_volume': latest_kline.get('quote_volume')  # 添加成交额字段
                }

                self.db_service.save_kline_data(kline_data)
                logger.debug(f"    ✓ [{used_exchange}] {symbol} K线({timeframe}): "
                           f"C:{latest_kline['close']:.2f}")
            else:
                logger.debug(f"    ⊗ {symbol} K线({timeframe}): 所有交易所均不可用")

        except Exception as e:
            logger.error(f"    采集 {symbol} K线({timeframe})失败: {e}")

    # ==================== 币安合约数据采集任务 ====================
    # 注意: 以下合约数据采集方法已被 fast_collector_service.py 替代
    # 保留代码仅供参考，不再使用

    # async def collect_binance_futures_data(self):
    #     """采集币安合约数据 (每1分钟) - 包括价格、K线、资金费率、持仓量、多空比"""
    #     if not self.futures_collector:
    #         return
    #
    #     task_name = 'binance_futures_1m'
    #     start_time = datetime.now()
    #
    #     try:
    #         logger.info(f"[{start_time.strftime('%H:%M:%S')}] 开始采集币安合约数据...")
    #
    #         collected_count = 0
    #         error_count = 0
    #
    #         for symbol in self.symbols:
    #             try:
    #                 # 获取所有合约数据
    #                 data = await self.futures_collector.fetch_all_data(symbol, timeframe='1m')
    #
    #                 if not data:
    #                     logger.warning(f"  ⊗ {symbol}: 未获取到数据")
    #                     error_count += 1
    #                     continue
    #
    #                 # 1. 保存ticker数据
    #                 if data.get('ticker'):
    #                     ticker = data['ticker']
    #                     price_data = {
    #                         'symbol': symbol,
    #                         'exchange': 'binance_futures',
    #                         'timestamp': ticker['timestamp'],
    #                         'price': ticker['price'],
    #                         'open': ticker['open'],
    #                         'high': ticker['high'],
    #                         'low': ticker['low'],
    #                         'close': ticker['close'],
    #                         'volume': ticker['volume'],
    #                         'quote_volume': ticker['quote_volume'],
    #                         'bid': 0,
    #                         'ask': 0,
    #                         'change_24h': ticker['price_change_percent']
    #                     }
    #                     self.db_service.save_price_data(price_data)
    #
    #                 # 2. 保存K线数据
    #                 if data.get('kline'):
    #                     kline = data['kline']
    #                     kline_data = {
    #                         'symbol': symbol,
    #                         'exchange': 'binance_futures',
    #                         'timeframe': '1m',
    #                         'open_time': int(kline['open_time']),
    #                         'timestamp': kline['timestamp'],
    #                         'open': kline['open'],
    #                         'high': kline['high'],
    #                         'low': kline['low'],
    #                         'close': kline['close'],
    #                         'volume': kline['volume']
    #                     }
    #                     self.db_service.save_kline_data(kline_data)
    #
    #                 # 3. 保存资金费率
    #                 if data.get('funding_rate'):
    #                     funding = data['funding_rate']
    #                     funding_data = {
    #                         'exchange': 'binance_futures',
    #                         'symbol': symbol,
    #                         'funding_rate': funding['funding_rate'],
    #                         'funding_time': funding['funding_time'],
    #                         'timestamp': funding['timestamp'],
    #                         'mark_price': funding['mark_price'],
    #                         'index_price': funding['index_price'],
    #                         'next_funding_time': funding['next_funding_time']
    #                     }
    #                     self.db_service.save_funding_rate_data(funding_data)
    #
    #                 # 4. 保存持仓量
    #                 if data.get('open_interest'):
    #                     oi = data['open_interest']
    #                     oi_data = {
    #                         'symbol': symbol,
    #                         'exchange': 'binance_futures',
    #                         'open_interest': oi['open_interest'],
    #                         'open_interest_value': oi.get('open_interest_value'),
    #                         'timestamp': oi['timestamp']
    #                     }
    #                     self.db_service.save_open_interest_data(oi_data)
    #
    #                 # 5. 保存多空比（账户数比 + 持仓量比）
    #                 ls_account = data.get('long_short_account_ratio')
    #                 ls_position = data.get('long_short_position_ratio')
    #
    #                 if ls_account or ls_position:
    #                     ls_data = {
    #                         'symbol': symbol,
    #                         'exchange': 'binance_futures',
    #                         'period': '5m',
    #                         'timestamp': datetime.now()
    #                     }
    #
    #                     # 账户数比数据
    #                     if ls_account:
    #                         ls_data.update({
    #                             'long_account': ls_account['long_account'],
    #                             'short_account': ls_account['short_account'],
    #                             'long_short_ratio': ls_account['long_short_ratio'],
    #                             'timestamp': ls_account['timestamp']
    #                         })
    #
    #                     # 持仓量比数据
    #                     if ls_position:
    #                         ls_data.update({
    #                             'long_position': ls_position['long_position'],
    #                             'short_position': ls_position['short_position'],
    #                             'long_short_position_ratio': ls_position['long_short_position_ratio']
    #                         })
    #
    #                     self.db_service.save_long_short_ratio_data(ls_data)
    #
    #                 # 日志输出
    #                 price = data['ticker']['price'] if data.get('ticker') else 0
    #                 funding_rate = data['funding_rate']['funding_rate'] * 100 if data.get('funding_rate') else 0
    #                 oi = data['open_interest']['open_interest'] if data.get('open_interest') else 0
    #                 ls_ratio = data['long_short_ratio']['long_short_ratio'] if data.get('long_short_ratio') else 0
    #
    #                 logger.info(
    #                     f"  ✓ {symbol}: "
    #                     f"价格=${price:,.2f}, "
    #                     f"费率={funding_rate:+.4f}%, "
    #                     f"持仓={oi:,.0f}, "
    #                     f"多空比={ls_ratio:.2f}"
    #                 )
    #
    #                 collected_count += 1
    #
    #                 # 延迟避免API限流 (优化: 从0.5秒减少到0.1秒以提升采集速度)
    #                 await asyncio.sleep(0.1)
    #
    #             except Exception as e:
    #                 logger.error(f"  ✗ {symbol}: {e}")
    #                 error_count += 1
    #
    #         # 更新统计
    #         self.task_stats[task_name]['count'] += 1
    #         self.task_stats[task_name]['last_run'] = datetime.now()
    #
    #         # 计算执行时间
    #         elapsed_time = (datetime.now() - start_time).total_seconds()
    #         logger.info(
    #             f"  ✓ 合约数据采集完成: 成功 {collected_count}/{len(self.symbols)}, "
    #             f"失败 {error_count}, 耗时 {elapsed_time:.1f}秒"
    #         )
    #
    #         # 如果耗时超过预期,发出警告
    #         if elapsed_time > 8:
    #             logger.warning(f"  ⚠️  合约数据采集耗时过长: {elapsed_time:.1f}秒 (预期 <8秒)")
    #
    #     except Exception as e:
    #         logger.error(f"合约数据采集任务失败: {e}")
    #         self.task_stats[task_name]['last_error'] = str(e)
    #
    # async def collect_binance_futures_klines(self, timeframe: str):
    #     """采集币安合约K线数据 - 指定时间周期
    #
    #     Args:
    #         timeframe: 时间周期 (5m, 15m, 1h, 1d)
    #     """
    #     if not self.futures_collector:
    #         return
    #
    #     task_name = f'binance_futures_kline_{timeframe}'
    #     try:
    #         logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始采集币安合约 {timeframe} K线数据...")
    #
    #         collected_count = 0
    #         error_count = 0
    #
    #         for symbol in self.symbols:
    #             try:
    #                 # 获取合约K线数据
    #                 df = await self.futures_collector.fetch_futures_klines(symbol, timeframe=timeframe, limit=2)
    #
    #                 if df is None or len(df) == 0:
    #                     logger.warning(f"  ⊗ {symbol} {timeframe}: 未获取到K线数据")
    #                     error_count += 1
    #                     continue
    #
    #                 # 保存最新的K线数据
    #                 for _, row in df.iterrows():
    #                     kline_data = {
    #                         'symbol': symbol,
    #                         'exchange': 'binance_futures',
    #                         'timeframe': timeframe,
    #                         'open_time': int(row['open_time']),
    #                         'timestamp': row['timestamp'],
    #                         'open': float(row['open']),
    #                         'high': float(row['high']),
    #                         'low': float(row['low']),
    #                         'close': float(row['close']),
    #                         'volume': float(row['volume']),
    #                         'quote_volume': float(row.get('quote_volume', 0))
    #                     }
    #                     self.db_service.save_kline_data(kline_data)
    #
    #                 logger.debug(f"  ✓ {symbol} {timeframe}: 保存 {len(df)} 条K线")
    #                 collected_count += 1
    #
    #                 # 延迟避免API限流
    #                 await asyncio.sleep(0.3)
    #
    #             except Exception as e:
    #                 logger.error(f"  ✗ {symbol} {timeframe}: {e}")
    #                 error_count += 1
    #
    #         # 更新统计
    #         if task_name not in self.task_stats:
    #             self.task_stats[task_name] = {'count': 0, 'last_run': None, 'last_error': None}
    #         self.task_stats[task_name]['count'] += 1
    #         self.task_stats[task_name]['last_run'] = datetime.now()
    #
    #         logger.info(
    #             f"  ✓ 合约 {timeframe} K线采集完成: 成功 {collected_count}/{len(self.symbols)}, "
    #             f"失败 {error_count}"
    #         )

        except Exception as e:
            logger.error(f"合约 {timeframe} K线采集任务失败: {e}")
            if task_name not in self.task_stats:
                self.task_stats[task_name] = {'count': 0, 'last_run': None, 'last_error': None}
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

    # ==================== 交易对评级更新任务 ====================
    # 注意: 评级更新已移至 smart_trader_service.py (每天凌晨2点自动运行)

    # ==================== 自动合约交易任务 ====================
    # 注意: 自动合约交易已移至 smart_trader_service.py

    # ==================== 合约监控任务 ====================
    # 合约止盈止损监控已移至 main.py，由 FastAPI 生命周期管理
    # 与现货限价单执行器保持一致，都在 main.py 中启动

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

    async def monitor_hyperliquid_wallets(self, priority: str = 'all'):
        """
        监控 Hyperliquid 聪明钱包的资金动态

        Args:
            priority: 监控优先级 (high, medium, low, all, config)
        """
        if not self.hyperliquid_collector:
            return

        task_name = 'hyperliquid_monitor'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始监控 Hyperliquid 聪明钱包 (优先级: {priority})...")

            from app.database.hyperliquid_db import HyperliquidDB

            with HyperliquidDB() as db:
                # 使用新的分级监控逻辑
                results = await self.hyperliquid_collector.monitor_all_addresses(
                    hours=168,  # 回溯7天（7*24=168小时）
                    priority=priority,
                    hyperliquid_db=db
                )

                if not results:
                    logger.info("  ⊗ 暂无监控钱包或未发现交易")
                    return

                monitored_wallets = list(results.keys())
                logger.info(f"  本次监控: {len(monitored_wallets)} 个地址")

                total_trades = 0
                total_positions = 0
                wallet_updates = []

                for address, result in results.items():
                    try:
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
                                'leverage': pos.get('leverage', 1),  # 从采集器获取杠杆倍数
                                'raw_data': {}
                            }
                            db.save_wallet_position(address, position_data, snapshot_time)
                            total_positions += 1

                        # 更新检查时间（需要先获取trader_id）
                        trader_id = db.get_or_create_trader(address)
                        last_trade_time = recent_trades[0]['timestamp'] if recent_trades else None
                        db.update_wallet_check_time(trader_id, last_trade_time)

                        # 记录有活动的钱包
                        if recent_trades or positions:
                            stats = result.get('statistics', {})
                            wallet_updates.append({
                                'address': address[:10] + '...',
                                'trades': len(recent_trades),
                                'positions': len(positions),
                                'net_flow': stats.get('net_flow_usd', 0),
                                'total_pnl': stats.get('total_pnl', 0)
                            })

                        # 延迟避免API限流
                        await asyncio.sleep(2)

                    except Exception as e:
                        logger.error(f"  监控钱包 {address[:10]}... 失败: {e}")
                        try:
                            trader_id = db.get_or_create_trader(address)
                            db.update_wallet_check_time(trader_id)
                        except:
                            pass

                # 汇总报告
                logger.info(f"  ✓ 监控完成: 检查 {len(monitored_wallets)} 个钱包, "
                          f"新交易 {total_trades} 笔, 持仓 {total_positions} 个")

                # 显示有活动的钱包
                if wallet_updates:
                    logger.info(f"  活跃钱包 ({len(wallet_updates)} 个):")
                    for w in wallet_updates[:5]:
                        pnl_str = f"PnL: ${w['total_pnl']:,.0f}" if w['total_pnl'] != 0 else ""
                        flow_str = f"净流: ${w['net_flow']:,.0f}" if w['net_flow'] != 0 else ""
                        logger.info(f"    • {w.get('address', w.get('label', 'Unknown'))}: {w['trades']}笔交易, {w['positions']}个持仓 {pnl_str} {flow_str}")

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

    # ── 后台线程调度辅助方法 ────────────────────────────────────────────────
    # 所有非即时任务走 daemon 线程, 确保 schedule.run_pending() 快速返回,
    # 不阻塞1分钟/5分钟任务的按时触发.

    def _run_async_in_thread(self, coro_factory):
        """在后台 daemon 线程执行异步任务.

        Args:
            coro_factory: 可调用, 调用后返回一个协程对象.
                          例: self.collect_funding_rates 是 bound method → coroutine.
        """
        def _wrapper():
            try:
                asyncio.run(coro_factory())
            except Exception as e:
                logger.error(f"[后台线程] 异步任务异常: {e.__class__.__name__}: {e}")
        threading.Thread(target=_wrapper, daemon=True).start()

    def _run_sync_in_thread(self, fn):
        """在后台 daemon 线程执行同步任务."""
        def _wrapper():
            try:
                fn()
            except Exception as e:
                logger.error(f"[后台线程] 同步任务异常: {e.__class__.__name__}: {e}")
        threading.Thread(target=_wrapper, daemon=True).start()

    def schedule_tasks(self):
        """设置所有定时任务"""
        logger.info("设置定时任务...")

        # 获取启用的交易所列表
        enabled_exchanges = list(self.price_collector.collectors.keys())
        exchanges_str = ' + '.join(enabled_exchanges) if enabled_exchanges else 'Binance'

        # 1. 现货K线数据采集 - 已禁用，改用 fast_collector_service.py 采集合约K线
        # 注意: 交易系统使用合约数据，不需要现货K线数据
        # schedule.every(5).minutes.do(
        #     lambda: asyncio.run(self.collect_binance_data('5m'))
        # )
        # logger.info(f"  ✓ 现货({exchanges_str}) 5分钟数据 - 每 5 分钟")
        #
        # schedule.every(15).minutes.do(
        #     lambda: asyncio.run(self.collect_binance_data('15m'))
        # )
        # logger.info(f"  ✓ 现货({exchanges_str}) 15分钟数据 - 每 15 分钟")
        #
        # schedule.every(1).hours.do(
        #     lambda: asyncio.run(self.collect_binance_data('1h'))
        # )
        # logger.info(f"  ✓ 现货({exchanges_str}) 1小时数据 - 每 1 小时")
        #
        # schedule.every().day.at("00:05").do(
        #     lambda: asyncio.run(self.collect_binance_data('1d'))
        # )
        # logger.info(f"  ✓ 现货({exchanges_str}) 1天数据 - 每天 00:05")

        logger.info("  ⚠️  现货K线采集已禁用 - 交易系统使用合约数据，由 fast_collector_service.py 采集")

        # 1.5 币安合约数据 - 已移至 fast_collector_service.py
        # if self.futures_collector:
        #     schedule.every(10).seconds.do(
        #         lambda: asyncio.run(self.collect_binance_futures_data())
        #     )
        #     logger.info("  ✓ 币安合约数据 (价格+1m K线+资金费率+持仓量+多空比) - 每 10 秒")
        #
        #     # 合约 5m K线
        #     schedule.every(5).minutes.do(
        #         lambda: asyncio.run(self.collect_binance_futures_klines('5m'))
        #     )
        #     logger.info("  ✓ 币安合约 5分钟K线 - 每 5 分钟")
        #
        #     # 合约 15m K线
        #     schedule.every(15).minutes.do(
        #         lambda: asyncio.run(self.collect_binance_futures_klines('15m'))
        #     )
        #     logger.info("  ✓ 币安合约 15分钟K线 - 每 15 分钟")
        #
        #     # 合约 1h K线
        #     schedule.every(1).hours.do(
        #         lambda: asyncio.run(self.collect_binance_futures_klines('1h'))
        #     )
        #     logger.info("  ✓ 币安合约 1小时K线 - 每 1 小时")
        #
        #     # 合约 1d K线
        #     schedule.every().day.at("00:10").do(
        #         lambda: asyncio.run(self.collect_binance_futures_klines('1d'))
        #     )
        #     logger.info("  ✓ 币安合约 1天K线 - 每天 00:10")

        logger.info("  ⚠️  合约K线和价格数据由 fast_collector_service.py 单独采集")

        # 2. 资金费率
        schedule.every(5).minutes.do(
            lambda: self._run_async_in_thread(self.collect_funding_rates)
        )
        logger.info("  ✓ 资金费率 - 每 5 分钟 (后台线程)")

        # 3. 新闻数据
        schedule.every(15).minutes.do(
            lambda: self._run_async_in_thread(self.collect_news)
        )
        logger.info("  ✓ 新闻数据 - 每 15 分钟 (后台线程)")

        # 3.5 Binance 官方公告监控（新上线/下架/维护/Launchpool）
        schedule.every(30).minutes.do(self.monitor_binance_news)
        logger.info("  ✓ Binance 公告监控 - 每 30 分钟")

        # 4. 区块链Gas统计 (每天采集昨天的数据，使用线程避免阻塞主调度器)
        try:
            from app.collectors.blockchain_gas_collector import BlockchainGasCollector
            
            def run_gas_collection_in_thread():
                """在独立线程中运行Gas采集，避免阻塞主调度器"""
                def collect_gas():
                    try:
                        logger.info("开始执行Gas采集任务（后台线程）...")
                        gas_collector = BlockchainGasCollector()
                        # 使用asyncio.run在独立线程中运行异步任务
                        # 注意：每个线程需要自己的事件循环
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(gas_collector.collect_all_chains())
                            logger.info("Gas采集任务完成（后台线程）")
                        finally:
                            loop.close()
                    except Exception as e:
                        logger.error(f"Gas采集任务执行失败: {e}", exc_info=True)
                
                # 在独立线程中运行，daemon=True确保主程序退出时线程也会退出
                # 这样即使Gas采集任务执行时间很长，也不会阻塞主调度器的其他任务
                thread = threading.Thread(target=collect_gas, daemon=True, name="GasCollector")
                thread.start()
                logger.debug("Gas采集任务已在后台线程启动")
            
            schedule.every().day.at("01:00").do(run_gas_collection_in_thread)
            # schedule库默认使用本地时间（系统时区），不是UTC时间
            import time
            local_tz = time.tzname[0] if time.daylight == 0 else time.tzname[1]
            logger.info(f"  ✓ 区块链Gas统计 - 每天 01:00 本地时间 ({local_tz}) (后台线程执行，不阻塞主调度器)")
        except Exception as e:
            logger.warning(f"  ⚠️  区块链Gas统计任务注册失败: {e}")

        # Farside BTC / ETH ETF 日度资金流（/btc/、/eth/）
        try:
            fe = self.config.get("farside_etf", {})
            if fe.get("enabled", True):

                def run_farside_etf_in_thread():
                    def job():
                        try:
                            from app.services.farside_etf_sync import (
                                sync_farside_btc_flows,
                                sync_farside_eth_flows,
                            )

                            mysql_config = self.config.get("database", {}).get("mysql", {})
                            btc_url = fe.get("btc_url", "https://farside.co.uk/btc/")
                            eth_url = fe.get("eth_url", "https://farside.co.uk/eth/")

                            logger.info("开始 Farside BTC ETF 同步（后台线程）...")
                            r_btc = sync_farside_btc_flows(mysql_config, page_url=btc_url)
                            logger.info(
                                "Farside BTC ETF 同步完成: imported={}, tickers={}, errors={}",
                                r_btc.get("imported_rows"),
                                len(r_btc.get("tickers") or []),
                                r_btc.get("error_count", 0),
                            )

                            logger.info("开始 Farside ETH ETF 同步（后台线程）...")
                            r_eth = sync_farside_eth_flows(mysql_config, page_url=eth_url)
                            logger.info(
                                "Farside ETH ETF 同步完成: imported={}, tickers={}, errors={}",
                                r_eth.get("imported_rows"),
                                len(r_eth.get("tickers") or []),
                                r_eth.get("error_count", 0),
                            )

                            self.task_stats["etf_daily"]["count"] += 1
                            self.task_stats["etf_daily"]["last_run"] = datetime.now()
                            self.task_stats["etf_daily"]["last_error"] = None
                        except Exception as ex:
                            logger.error("Farside ETF 同步失败: {}", ex, exc_info=True)
                            self.task_stats["etf_daily"]["last_error"] = str(ex)

                    threading.Thread(target=job, daemon=True, name="FarsideEtfSync").start()

                daily_at = fe.get("daily_at", "06:45")
                schedule.every().day.at(daily_at).do(run_farside_etf_in_thread)
                logger.info(f"  ✓ Farside BTC/ETH ETF 同步 - 每天 {daily_at} 本地时间 (后台线程)")
        except Exception as e:
            logger.warning(f"  ⚠️  Farside ETF 任务注册失败: {e}")

        # BitcoinTreasuries.NET 上市公司 BTC 金库持仓（首页表格）
        try:
            bt = self.config.get("bitcointreasuries", {})
            if bt.get("enabled", True):

                def run_bt_in_thread():
                    def job():
                        try:
                            logger.info("开始 bitcointreasuries.net 企业金库同步（后台线程）...")
                            from app.services.bitcointreasuries_sync import (
                                sync_bitcointreasuries_holdings,
                            )

                            mysql_config = self.config.get("database", {}).get("mysql", {})
                            url = bt.get("url", "https://bitcointreasuries.net/")
                            r = sync_bitcointreasuries_holdings(
                                mysql_config, page_url=url
                            )
                            logger.info(
                                "企业金库同步完成: companies={}, imported={}, updated={}, skipped={}",
                                r.get("company_count"),
                                r.get("imported"),
                                r.get("updated"),
                                r.get("skipped"),
                            )
                            self.task_stats["bitcointreasuries_daily"]["count"] += 1
                            self.task_stats["bitcointreasuries_daily"][
                                "last_run"
                            ] = datetime.now()
                            self.task_stats["bitcointreasuries_daily"][
                                "last_error"
                            ] = None
                        except Exception as ex:
                            logger.error("bitcointreasuries.net 同步失败: {}", ex, exc_info=True)
                            self.task_stats["bitcointreasuries_daily"][
                                "last_error"
                            ] = str(ex)

                    threading.Thread(
                        target=job, daemon=True, name="BitcoinTreasuriesSync"
                    ).start()

                daily_bt = bt.get("daily_at", "07:30")
                schedule.every().day.at(daily_bt).do(run_bt_in_thread)
                logger.info(
                    f"  ✓ BitcoinTreasuries 企业金库 - 每天 {daily_bt} 本地时间 (后台线程)"
                )
        except Exception as e:
            logger.warning(f"  ⚠️  BitcoinTreasuries 任务注册失败: {e}")

        # 5. Hyperliquid 排行榜 (由下方 #5 区块统一注册, 此项已合并)
        # 不在此重复注册 —— 见本文件 #5 区块的 Hyperliquid 排行榜任务
        #

        # 3.5 自动合约交易 - 已移至 smart_trader_service.py
        logger.info("  ℹ️  自动合约交易已移至 smart_trader_service.py")
        logger.info("     请单独运行: python smart_trader_service.py")

        # 3.6 合约持仓监控（已移至 main.py，由 FastAPI 生命周期管理）
        # 合约止盈止损监控现在在 main.py 中启动，与现货限价单执行器保持一致

        # 4. Ethereum 链上数据
        if self.smart_money_collector:
            schedule.every(5).minutes.do(
                lambda: self._run_async_in_thread(
                    lambda: self.collect_ethereum_data('5m')
                )
            )
            logger.info("  ✓ Ethereum 5分钟数据 - 每 5 分钟 (后台线程)")

            schedule.every(1).hours.do(
                lambda: self._run_async_in_thread(
                    lambda: self.collect_ethereum_data('1h')
                )
            )
            logger.info("  ✓ Ethereum 1小时数据 - 每 1 小时 (后台线程)")

            schedule.every().day.at("00:10").do(
                lambda: self._run_async_in_thread(
                    lambda: self.collect_ethereum_data('1d')
                )
            )
            logger.info("  ✓ Ethereum 1天数据 - 每天 00:10 (后台线程)")

        # 5. Hyperliquid 排行榜 (重复注册已修复: 仅此一处)
        if self.hyperliquid_collector:
            schedule.every().day.at("02:00").do(
                lambda: self._run_async_in_thread(self.collect_hyperliquid_leaderboard)
            )
            logger.info("  ✓ Hyperliquid 排行榜 - 每天 02:00 (后台线程)")

        # 6. Hyperliquid 钱包监控 - 已移至独立的 hyperliquid_scheduler.py
        # 注意: Hyperliquid 监控任务现在由独立的调度器运行，避免阻塞主调度器
        if self.hyperliquid_collector:
            logger.info("  ℹ️  Hyperliquid 钱包监控已移至独立调度器 (app/hyperliquid_scheduler.py)")
            logger.info("     请单独运行: python app/hyperliquid_scheduler.py")

        # 6.5 日终维护：TOP50 榜单 + 统一评级（每天 02:05）
        # update_top_performers.py 内部已包含评级逻辑，一步调用完成
        def _run_eod_maintenance():
            try:
                from update_top_performers import update_top_performing_symbols
                update_top_performing_symbols(account_id=2, top_n=50)
            except Exception as e:
                logger.error(f"[日终维护] 失败: {e}")
                import traceback
                logger.error(traceback.format_exc())

        schedule.every().day.at("02:05").do(
            lambda: self._run_sync_in_thread(_run_eod_maintenance)
        )
        logger.info("  ✓ 日终维护 TOP50 + 统一评级 - 每天 02:05 (后台线程)")

        # 7. 缓存更新任务
        logger.info("\n  🚀 性能优化: 缓存自动更新")

        # 价格缓存 - 每1分钟更新
        schedule.every(1).minutes.do(
            lambda: self._run_async_in_thread(self.update_price_cache)
        )
        logger.info("  ✓ 价格统计缓存 (price_stats_24h) - 每 1 分钟 (后台线程)")

        # 分析缓存 - 每5分钟
        schedule.every(5).minutes.do(
            lambda: self._run_async_in_thread(self.update_analysis_cache)
        )
        logger.info("  ✓ 分析缓存 (技术指标+新闻+资金费率+投资建议) - 每 5 分钟 (后台线程)")

        # Hyperliquid缓存 - 每10分钟
        if self.hyperliquid_collector:
            schedule.every(10).minutes.do(
                lambda: self._run_async_in_thread(self.update_hyperliquid_cache)
            )
            logger.info("  ✓ Hyperliquid聚合缓存 - 每 10 分钟 (后台线程)")

        # 模拟合约总权益更新 - 移除高频更新
        # if self.futures_engine:
        #     schedule.every(30).seconds.do(
        #         self.update_futures_accounts_equity
        #     )
        #     logger.info("  ✓ 模拟合约总权益更新 - 每 30 秒")

        # ============================================================
        # 8. data_cache 层 — 预计算缓存刷新
        # ============================================================
        logger.info("\n  🚀 data_cache 层: 预计算缓存自动刷新")

        def _run_cache_task(job_fn):
            """在线程中执行缓存任务."""
            def wrapper():
                try:
                    job_fn()
                except Exception as e:
                    logger.error(f"[data_cache] 任务失败: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name=f"Cache_{job_fn.__name__}").start()

        from app.services.data_cache_service import (
            refresh_market_snapshot,
            refresh_market_movers,
            refresh_candidate_pool,
            refresh_explore_prepared_only,
            refresh_position_stats,
            sync_settings_cache,
        )

        # 市场快照 - 每1分钟
        schedule.every(1).minutes.do(lambda: _run_cache_task(refresh_market_snapshot))
        logger.info("  ✓ market_snapshot - 每 1 分钟 (后台线程)")

        # 市场异动 - 每5分钟
        schedule.every(5).minutes.do(lambda: _run_cache_task(refresh_market_movers))
        logger.info("  ✓ market_movers_snapshot - 每 5 分钟 (后台线程)")

        # 候选交易对池 (含 K 线叙事) — 每 6 分钟
        schedule.every(6).minutes.do(lambda: _run_cache_task(refresh_candidate_pool))
        logger.info("  ✓ candidate_pool_snapshot - 每 6 分钟 (后台线程)")

        # 探索/战术共用 universe — 每 15 分钟，仅组装 (~35s)，不重复全量候选池
        schedule.every(15).minutes.do(
            lambda: _run_cache_task(refresh_explore_prepared_only)
        )
        logger.info(
            "  ✓ explore_prepared_snapshot - 每 15 分钟 (全 Gemini/DeepSeek 策略只读)"
        )

        # 持仓统计 - 每30分钟
        schedule.every(30).minutes.do(lambda: _run_cache_task(refresh_position_stats))
        logger.info("  ✓ position_stats_snapshot - 每 30 分钟 (后台线程)")

        # 系统设置缓存 - 每1分钟同步 (保持与 system_settings 表同步)
        schedule.every(1).minutes.do(lambda: _run_cache_task(sync_settings_cache))
        logger.info("  ✓ settings_cache - 每 1 分钟 (后台线程)")

        # ============================================================
        # 9. Gemini 系列 — AI 交易任务
        # ============================================================
        logger.info("\n  🤖 Gemini 系列: AI 交易任务")

        # Gemini 探索 - 每 4h 调一轮 Gemini 检测方向异动, 模拟开仓
        # kill switch = system_settings.gemini_explore_enabled
        def _run_gemini_explore():
            def wrapper():
                try:
                    from app.services.gemini_explore_worker import run_explore_round
                    run_explore_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[Gemini探索] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="GeminiExplore").start()

        schedule.every(4).hours.do(_run_gemini_explore)
        # 兜底: every(N).hours 在 restart 后从 0 计时, 易错过周期; 10min 轮询由 worker 4h 防重
        schedule.every(10).minutes.do(_run_gemini_explore)
        logger.info("  ✓ gemini_explore - 每 4 小时 + 10 分钟到期轮询 (后台线程)")

        # DeepSeek 探索 - 每 4h 调一轮 DeepSeek 检测短时方向异动, 模拟开仓
        # kill switch = system_settings.deepseek_explore_enabled
        def _run_deepseek_explore():
            def wrapper():
                try:
                    from app.services.deepseek_explore_worker import run_explore_round
                    run_explore_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[DeepSeek探索] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="DeepSeekExplore").start()

        schedule.every(4).hours.do(_run_deepseek_explore)
        schedule.every(10).minutes.do(_run_deepseek_explore)
        logger.info("  ✓ deepseek_explore - 每 4 小时 + 10 分钟到期轮询 (后台线程)")

        # GPT 探索 - 每 4h 调一轮 GPT 检测短时方向异动, 模拟开仓
        # kill switch = system_settings.gpt_explore_enabled
        def _run_gpt_explore():
            def wrapper():
                try:
                    from app.services.gpt_explore_worker import run_explore_round
                    run_explore_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[GPT探索] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="GPTExplore").start()

        schedule.every(4).hours.do(_run_gpt_explore)
        schedule.every(10).minutes.do(_run_gpt_explore)
        logger.info("  ✓ gpt_explore - 每 4 小时 + 10 分钟到期轮询 (后台线程)")

        # DeepSeek 预测 - 每 4h 调一次 DeepSeek 预测 TOP50 方向
        def _run_deepseek_predict():
            def wrapper():
                try:
                    from app.services.deepseek_predictor import run_predict_round
                    run_predict_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[DeepSeek预测] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="DeepSeekPredict").start()

        schedule.every(4).hours.do(_run_deepseek_predict)
        schedule.every(5).minutes.do(_run_deepseek_predict)
        logger.info("  ✓ deepseek_predict - 每 4h 周期 + 5 分钟到期轮询 (DB next_due 防重)")

        # Gemini 预测 - 每 4h 调一次 Gemini 预测 TOP50 方向
        def _run_gemini_predict():
            def wrapper():
                try:
                    from app.services.gemini_predictor import run_predict_round
                    run_predict_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[Gemini预测] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="GeminiPredict").start()

        schedule.every(4).hours.do(_run_gemini_predict)
        schedule.every(5).minutes.do(_run_gemini_predict)
        logger.info("  ✓ gemini_predict - 每 4h 周期 + 5 分钟到期轮询 (DB next_due 防重)")

        # GPT 预测 - 每 4h 调一次 GPT 预测 TOP50 方向
        def _run_gpt_predict():
            def wrapper():
                try:
                    from app.services.gpt_predictor import run_predict_round
                    run_predict_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[GPT预测] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="GPTPredict").start()

        schedule.every(4).hours.do(_run_gpt_predict)
        schedule.every(5).minutes.do(_run_gpt_predict)
        logger.info("  ✓ gpt_predict - 每 4h 周期 + 5 分钟到期轮询 (DB next_due 防重)")

        # Gemini 持仓顾问 - 监管 gemini_explore/gemini_predict 模拟仓，满 30min 后每 15min 复查
        def _run_gemini_position_advisor():
            if self._gemini_position_advisor_running:
                logger.info("[Gemini持仓顾问] 上一轮仍在运行，跳过本轮")
                return

            def wrapper():
                task_name = 'gemini_position_advisor'
                self._gemini_position_advisor_running = True
                try:
                    from app.services.gemini_position_advisor import get_open_advisor
                    stats = get_open_advisor().tick()
                    self.task_stats[task_name]['count'] += 1
                    self.task_stats[task_name]['last_run'] = datetime.now()
                    self.task_stats[task_name]['last_error'] = None
                    logger.info(f"[Gemini持仓顾问] 调度完成: {stats}")
                except Exception as e:
                    logger.error(f"[Gemini持仓顾问] 调度异常: {e}", exc_info=True)
                    self.task_stats[task_name]['last_error'] = str(e)
                finally:
                    self._gemini_position_advisor_running = False

            threading.Thread(
                target=wrapper,
                daemon=True,
                name="GeminiPositionAdvisor",
            ).start()

        schedule.every(15).minutes.do(_run_gemini_position_advisor)
        _run_gemini_position_advisor()
        logger.info("  ✓ gemini_position_advisor - 每 15 分钟 (后台线程)")

        # DeepSeek 持仓顾问 - 监管非 Gemini 主探索/预测模拟仓，满 30min 后每 15min 复查
        def _run_deepseek_position_advisor():
            if self._deepseek_position_advisor_running:
                logger.info("[DeepSeek持仓顾问] 上一轮仍在运行，跳过本轮")
                return

            def wrapper():
                task_name = 'deepseek_position_advisor'
                self._deepseek_position_advisor_running = True
                try:
                    from app.services.deepseek_position_advisor import get_deepseek_advisor
                    stats = get_deepseek_advisor().tick()
                    self.task_stats[task_name]['count'] += 1
                    self.task_stats[task_name]['last_run'] = datetime.now()
                    self.task_stats[task_name]['last_error'] = None
                    logger.info(f"[DeepSeek持仓顾问] 调度完成: {stats}")
                except Exception as e:
                    logger.error(f"[DeepSeek持仓顾问] 调度异常: {e}", exc_info=True)
                    self.task_stats[task_name]['last_error'] = str(e)
                finally:
                    self._deepseek_position_advisor_running = False

            threading.Thread(
                target=wrapper,
                daemon=True,
                name="DeepSeekPositionAdvisor",
            ).start()

        schedule.every(15).minutes.do(_run_deepseek_position_advisor)
        _run_deepseek_position_advisor()
        logger.info("  ✓ deepseek_position_advisor - 每 15 分钟 (后台线程)")

        # Big4 综合行情 LLM 分析 — 每 4h (Gemini + DeepSeek)
        def _run_gemini_big4_analysis():
            def wrapper():
                try:
                    from app.services.big4_comprehensive_analyzer import run_big4_analysis_round
                    run_big4_analysis_round("gemini", triggered_by="scheduler")
                except Exception as e:
                    logger.error(f"[Gemini Big4分析] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="GeminiBig4Analysis").start()

        def _run_deepseek_big4_analysis():
            def wrapper():
                try:
                    from app.services.big4_comprehensive_analyzer import run_big4_analysis_round
                    run_big4_analysis_round("deepseek", triggered_by="scheduler")
                except Exception as e:
                    logger.error(f"[DeepSeek Big4分析] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="DeepSeekBig4Analysis").start()

        schedule.every(4).hours.do(_run_gemini_big4_analysis)
        schedule.every(10).minutes.do(_run_gemini_big4_analysis)
        schedule.every(4).hours.do(_run_deepseek_big4_analysis)
        schedule.every(10).minutes.do(_run_deepseek_big4_analysis)
        logger.info("  ✓ big4_analysis - Gemini/DeepSeek 每 4h + 10min 轮询 (worker 内 4h 防重)")

        # Gemini 市场情绪 + 川普分析 - 每 8h 调一次
        def _run_gemini_sentiment():
            def wrapper():
                try:
                    from app.services.gemini_sentiment_analyzer import run_sentiment_round
                    run_sentiment_round(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[Gemini情绪分析] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="GeminiSentiment").start()

        schedule.every(8).hours.do(_run_gemini_sentiment)
        logger.info("  ✓ gemini_sentiment - 每 8 小时 (后台线程, kill switch 默认 ON)")

        logger.info("\n所有定时任务设置完成")

        # ============================================================
        # 10. 实盘交易记录同步 — 每 15 分钟从币安拉取完整持仓数据
        #     先 sync_positions_from_binance 全量同步, 再 correct_live_trade_records 修正
        # ============================================================
        def _run_correct_live_trades():
            def wrapper():
                try:
                    self.correct_live_trade_records_all_accounts()
                except Exception as e:
                    logger.error(f"[实盘记录同步] 调度异常: {e}", exc_info=True)
            threading.Thread(target=wrapper, daemon=True, name="CorrectLiveTrades").start()

        schedule.every(15).minutes.do(_run_correct_live_trades)
        logger.info("  ✓ correct_live_trades - 每 15 分钟 (后台线程)")

        # Paper 已平但关联 live 仍 OPEN 的兜底补平。
        # 只处理 paper_position_id 明确绑定的 live 单，避免误平手工同币种仓位。
        def _run_paper_closed_live_sync():
            if self._paper_closed_live_sync_running:
                logger.info("[PaperClosedLiveSync] 上一轮仍在运行，跳过本轮")
                return

            def wrapper():
                task_name = 'paper_closed_live_sync'
                self._paper_closed_live_sync_running = True
                try:
                    from app.services.paper_closed_live_sync import run_paper_closed_live_sync
                    _mysql_cfg = self.config.get("database", {}).get("mysql", {})
                    stats = run_paper_closed_live_sync(_mysql_cfg, limit=50)
                    self.task_stats[task_name]['count'] += 1
                    self.task_stats[task_name]['last_run'] = datetime.now()
                    self.task_stats[task_name]['last_error'] = None
                    if stats.get("checked") or stats.get("errors"):
                        logger.info(f"[PaperClosedLiveSync] 调度完成: {stats}")
                except Exception as e:
                    logger.error(f"[PaperClosedLiveSync] 调度异常: {e}", exc_info=True)
                    self.task_stats[task_name]['last_error'] = str(e)
                finally:
                    self._paper_closed_live_sync_running = False

            threading.Thread(
                target=wrapper,
                daemon=True,
                name="PaperClosedLiveSync",
            ).start()

        schedule.every(1).minutes.do(_run_paper_closed_live_sync)
        _run_paper_closed_live_sync()
        logger.info("  ✓ paper_closed_live_sync - 每 1 分钟 (后台线程)")

    async def run_initial_collection(self):
        """首次启动时执行一次缓存更新.

        2026-05-20 重构: 删掉重复的 K 线/funding/新闻初次采集 (已经被
        fast_collector_service / ws_kline_collector_service 独立进程跑了),
        改成只跑缓存更新 (price_stats / 分析缓存 / hyperliquid 缓存).

        原版每个 await 串行跑, 任何一步慢 (collect_funding_rates 295 symbol
        × 0.2s sleep + API = 2-5 分钟) 会把 update_price_cache 拖死,
        进而阻塞 start() 进入 schedule 主循环.

        现在: initial 仅做缓存, 1-3 秒即可跑完, 立即进入 schedule 主循环,
        所有 every(N).minutes 任务都能按时触发.
        """
        logger.info("\n" + "=" * 80)
        logger.info("首次缓存更新开始 (K线/funding 采集由独立 collector 进程负责, 此处不再重复)")
        logger.info("=" * 80 + "\n")

        # 缓存更新 (各自有内部 timeout 保护)
        try:
            await asyncio.wait_for(self.update_price_cache(), timeout=30)
        except asyncio.TimeoutError:
            logger.warning("  ⊗ update_price_cache 超时 (>30s), 将在定时任务中重试")
        except Exception as e:
            logger.warning(f"  ⊗ update_price_cache 失败: {e}")

        try:
            await asyncio.wait_for(self.update_analysis_cache(), timeout=60)
        except asyncio.TimeoutError:
            logger.warning("  ⊗ update_analysis_cache 超时 (>60s)")
        except Exception as e:
            logger.warning(f"  ⊗ update_analysis_cache 失败: {e}")

        if self.hyperliquid_collector:
            try:
                await asyncio.wait_for(self.update_hyperliquid_cache(), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("  ⊗ Hyperliquid 缓存更新超时")
            except Exception as e:
                logger.warning(f"  ⊗ Hyperliquid 缓存更新失败: {e}")

        # data_cache 初始刷新 (后台线程, 不阻塞)
        try:
            from app.services.data_cache_service import (
                refresh_market_snapshot,
                refresh_market_movers,
                refresh_candidate_pool,
                refresh_explore_shared_data,
                refresh_position_stats,
                sync_settings_cache,
            )
            import threading as _t

            def _init_cache():
                logger.info("[data_cache] 首次刷新 market_movers_snapshot (后台)...")
                refresh_market_movers()
                logger.info("[data_cache] 首次刷新 market_snapshot (后台)...")
                refresh_market_snapshot()
                logger.info("[data_cache] 首次刷新 settings_cache (后台)...")
                sync_settings_cache()
                logger.info("[data_cache] 首次刷新 candidate_pool + explore_prepared (后台)...")
                refresh_explore_shared_data(force_candidate_pool=True)
                logger.info("[data_cache] 首次刷新 position_stats_snapshot (后台)...")
                refresh_position_stats()
                logger.info("[data_cache] 首次刷新完成")

            _t.Thread(target=_init_cache, daemon=True, name="InitDataCache").start()
        except Exception as e:
            logger.warning(f"[data_cache] 首次刷新失败 (将在定时任务中重试): {e}")

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
        # 显示时区信息：schedule库默认使用本地时间（系统时区），不是UTC时间
        import time
        local_tz = time.tzname[0] if time.daylight == 0 else time.tzname[1]
        logger.info(f"时区: 本地时间 ({local_tz}) - 所有定时任务使用系统本地时区")
        logger.info("=" * 80 + "\n")

        # 设置定时任务
        self.schedule_tasks()

        # 首次采集 — 后台线程执行, 不阻塞 schedule 主循环
        self._run_async_in_thread(self.run_initial_collection)

        # AI 首次启动 — 各任务独立线程触发 (探索错峰, 避免同时打爆 kline_data)
        def _launch_ai_init_task(name, module_path, func_name, delay_s: int = 15):
            """在独立后台线程执行一次 AI 初始化任务."""
            def _run():
                import time
                time.sleep(delay_s)
                try:
                    import importlib
                    mod = importlib.import_module(module_path)
                    func = getattr(mod, func_name)
                    func(triggered_by='scheduler_init')
                except Exception as e:
                    logger.error(f"[{name}] 初始化运行失败: {e}", exc_info=True)
            threading.Thread(target=_run, daemon=True, name=f"AIInit_{name}").start()

        _launch_ai_init_task("Gemini探索",   "app.services.gemini_explore_worker",   "run_explore_round", 15)
        # 预测不走 scheduler_init；由 5min 轮询 + next_due 保证每 4h 至少一轮 (triggered_by=scheduler)
        def _launch_predict_catchup(name: str, module_path: str, func_name: str, delay_s: int):
            def _run():
                import time
                time.sleep(delay_s)
                try:
                    import importlib
                    mod = importlib.import_module(module_path)
                    getattr(mod, func_name)(triggered_by='scheduler')
                except Exception as e:
                    logger.error(f"[{name}] 启动补跑检查失败: {e}", exc_info=True)
            threading.Thread(target=_run, daemon=True, name=f"PredictCatchup_{name}").start()

        _launch_ai_init_task("GeminiBig4",   "app.services.big4_comprehensive_analyzer", "run_big4_analysis_round_gemini", 28)
        _launch_ai_init_task("DeepSeekBig4", "app.services.big4_comprehensive_analyzer", "run_big4_analysis_round_deepseek", 95)
        _launch_ai_init_task("Gemini情绪",   "app.services.gemini_sentiment_analyzer","run_sentiment_round", 25)
        _launch_ai_init_task("DeepSeek探索","app.services.deepseek_explore_worker",  "run_explore_round", 90)
        _launch_ai_init_task("GPT探索",      "app.services.gpt_explore_worker",       "run_explore_round", 120)
        _launch_predict_catchup("Gemini预测", "app.services.gemini_predictor", "run_predict_round", 45)
        _launch_predict_catchup("DeepSeek预测", "app.services.deepseek_predictor", "run_predict_round", 50)
        _launch_predict_catchup("GPT预测", "app.services.gpt_predictor", "run_predict_round", 55)

        # 定期打印状态 (每小时)
        schedule.every(1).hours.do(self.print_status)

        logger.info("\n调度器已启动，按 Ctrl+C 停止\n")

        # 保持运行 — run_pending() 仅触发任务提交, 不阻塞 (任务已在后台线程跑)
        try:
            while True:
                try:
                    schedule.run_pending()
                except Exception as e:
                    # 捕获定时任务中的异常，防止整个调度器崩溃
                    logger.error(f"定时任务执行异常: {e}", exc_info=True)
                # 智能休眠: 当下个任务距现在 > 2 分钟时最长睡 2 分钟,
                # 否则睡到下次检查前, 兼顾实时性和 CPU 开销
                _idle = schedule.idle_seconds()
                if _idle is None or _idle <= 0:
                    _sleep = 30  # 无定时任务或已到期, 每30秒检查一次
                elif _idle > 120:
                    _sleep = 120  # 上限 2 分钟
                else:
                    _sleep = max(int(_idle) + 1, 5)  # 稍早唤醒, 最小 5 秒
                time.sleep(_sleep)

        except KeyboardInterrupt:
            logger.info("\n\n收到停止信号，正在关闭...")
            self.stop()

    # ==================== 缓存更新任务 ====================

    async def update_price_cache(self):
        """更新价格统计缓存 (每15秒)"""
        task_name = 'cache_price'
        try:
            await self.cache_service.update_price_stats_cache(self.symbols)

            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            # 移除成功时的日志，仅在失败时打印

        except Exception as e:
            logger.error(f"更新价格缓存失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    def update_futures_accounts_equity(self):
        """更新所有模拟合约账户的总权益 (每30秒)"""
        if not self.futures_engine:
            return
        
        task_name = 'futures_equity_update'
        try:
            updated_count = self.futures_engine.update_all_accounts_equity()
            if updated_count > 0:
                logger.debug(f"已更新 {updated_count} 个账户的总权益")
            
            # 更新统计
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            self.task_stats[task_name]['last_error'] = None
            
        except Exception as e:
            logger.error(f"更新模拟合约总权益失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    def correct_live_trade_records_all_accounts(self):
        """遍历所有活跃API Key，全量从币安同步持仓数据。
        1. sync_positions_from_binance — 同步结构 (新开/已平/PENDING 状态)
        2. correct_live_trade_records — 修正字段 (mark_price/liquidation_price/unrealized_pnl 等)
        """
        task_name = 'correct_live_trades'
        try:
            from app.services.api_key_service import APIKeyService
            from app.trading.binance_futures_engine import BinanceFuturesEngine
            _mysql_cfg = self.config.get("database", {}).get("mysql", {})
            key_service = APIKeyService(_mysql_cfg)
            api_keys = key_service.get_all_active_api_keys(exchange='binance')

            if not api_keys:
                logger.debug("[实盘记录同步] 无活跃API Key，跳过")
                return

            for ak in api_keys:
                try:
                    engine = BinanceFuturesEngine(
                        _mysql_cfg,
                        api_key=ak['api_key'],
                        api_secret=ak['api_secret'],
                    )
                    # Step 1: 全量同步持仓结构 (Binance-app 开的单、已平的、PENDING 的)
                    sync_result = engine.sync_positions_from_binance(account_id=ak['id'])
                    if sync_result.get('success'):
                        details = f"new={sync_result.get('new',0)} closed={sync_result.get('closed',0)} canceled={sync_result.get('canceled',0)}"
                        if sync_result.get('total', 0) > 0:
                            logger.info(
                                f"[实盘记录同步] 账号[{ak['account_name']}] "
                                f"sync={sync_result.get('total', 0)} ({details})"
                            )

                    # Step 2: 修正字段 (mark_price, liquidation_price, unrealized_pnl 等)
                    result = engine.correct_live_trade_records(account_id=ak['id'])
                    if result.get('success'):
                        total = result.get('total_corrected', 0)
                        if total > 0:
                            logger.info(
                                f"[实盘记录同步] 账号[{ak['account_name']}] "
                                f"修正 {total} 条 (OPEN={result.get('open_corrected', 0)}, "
                                f"CLOSED={result.get('closed_corrected', 0)})"
                            )
                except Exception as e:
                    logger.warning(f"[实盘记录同步] 账号[{ak['account_name']}] 失败: {e}")
                    continue

            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            self.task_stats[task_name]['last_error'] = None
        except Exception as e:
            logger.error(f"[实盘记录同步] 任务失败: {e}")
            self.task_stats[task_name]['last_error'] = str(e)

    def monitor_binance_news(self):
        """拉取并处理 Binance 公告（新上线/下架/维护/Launchpool），每30分钟"""
        if not self.binance_news_monitor:
            return
        task_name = 'binance_news'
        try:
            stats = self.binance_news_monitor.run()
            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            self.task_stats[task_name]['last_error'] = None
            if any(v > 0 for v in stats.values() if isinstance(v, int)):
                logger.info(
                    "Binance 公告: 新上线=%d, 下架=%d, 维护=%d, Launchpool=%d",
                    stats.get('new_listing', 0), stats.get('delisting', 0),
                    stats.get('maintenance', 0), stats.get('launchpool', 0)
                )
        except Exception as e:
            logger.error("Binance 公告监控任务失败: %s", e)
            self.task_stats[task_name]['last_error'] = str(e)

    async def update_analysis_cache(self):
        """更新分析类缓存 (每5分钟) - 技术指标、新闻情绪、资金费率、投资建议"""
        task_name = 'cache_analysis'
        try:
            logger.info(f"[{datetime.now().strftime('%H:%M:%S')}] 开始更新分析缓存...")

            # 并发更新5个缓存（含价格统计）
            await asyncio.gather(
                self.cache_service.update_price_stats_cache(self.symbols),
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

            # 添加60秒超时保护，防止无限挂起
            await asyncio.wait_for(
                self.cache_service.update_hyperliquid_aggregation(self.symbols),
                timeout=60
            )

            self.task_stats[task_name]['count'] += 1
            self.task_stats[task_name]['last_run'] = datetime.now()
            logger.info(f"  ✓ Hyperliquid缓存更新完成 - {len(self.symbols)} 个币种")

        except asyncio.TimeoutError:
            logger.warning(f"更新Hyperliquid缓存超时（60秒）")
            self.task_stats[task_name]['last_error'] = "超时"
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
    # PID 文件锁 — 防止重复启动 (2026-05-17)
    # scheduler 重复跑会导致 Farside / BitcoinTreasuries 一天调 2 次,触发 Cloudflare 限流
    try:
        from app.utils.pid_lock import acquire_pid_lock
        acquire_pid_lock('scheduler')
    except ImportError:
        pass  # 老环境无 pid_lock 模块,不强制
    main()

