"""
价格数据采集器
全部通过 BinanceDataHub 取数据 (统一币安数据网关).

历史: 早期持有 python-binance SDK Client 直接打 fapi/api 端点,
多个并发调用会让 IP 走向 -1003. 现已收敛到 hub 统一限速 + 熔断.
"""

import asyncio
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from loguru import logger
import pandas as pd


class PriceCollector:
    """价格数据采集器基类 - 全部走 BinanceDataHub."""

    def __init__(self, exchange_id: str = 'binance', config: dict = None):
        """
        初始化采集器.

        Args:
            exchange_id: 交易所ID (目前仅支持binance)
            config: 配置字典. api_key/api_secret 已不需要 (公开接口走 hub).
        """
        self.exchange_id = exchange_id
        self.config = config or {}
        logger.info(f"初始化 {exchange_id} 采集器 (通过 BinanceDataHub)")

    async def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        """
        获取实时价格

        Args:
            symbol: 交易对，如 'BTC/USDT' 会转换为 'BTCUSDT'

        Returns:
            价格数据字典
        """
        try:
            # 转换交易对格式: BTC/USDT -> BTCUSDT
            binance_symbol = symbol.replace('/', '')

            # 通过 DataHub 取 24h ticker (合约端点, 受熔断 + 令牌桶)
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is None:
                return None
            ticker = await hub.fapi_request_get('/fapi/v1/ticker/24hr', {'symbol': binance_symbol})
            if not ticker or not isinstance(ticker, dict) or 'lastPrice' not in ticker:
                return None
            last_price = float(ticker['lastPrice'])
            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'timestamp': datetime.fromtimestamp(ticker['closeTime'] / 1000),
                'price': last_price,
                'open': float(ticker['openPrice']),
                'high': float(ticker['highPrice']),
                'low': float(ticker['lowPrice']),
                'close': last_price,
                'volume': float(ticker['volume']),
                'quote_volume': float(ticker['quoteVolume']),
                'bid': float(ticker.get('bidPrice', last_price)),
                'ask': float(ticker.get('askPrice', last_price)),
                'change_24h': float(ticker['priceChangePercent']),
            }

        except Exception as e:
            logger.error(f"{self.exchange_id} 获取 {symbol} 实时价格失败: {e}")
            return None

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1h',
        limit: int = 100,
        since: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取K线数据 (OHLCV) - 使用合约API

        Args:
            symbol: 交易对
            timeframe: 时间周期 (1m, 5m, 15m, 1h, 4h, 1d等)
            limit: 获取数量
            since: 起始时间戳(毫秒)

        Returns:
            DataFrame包含 [timestamp, open, high, low, close, volume]
        """
        try:
            # 走 BinanceDataHub - 优先 DB kline 命中, 滞后才走 REST (受熔断 + 令牌桶)
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is None:
                logger.warning(f"DataHub 未初始化, {symbol} K线取不到")
                return None
            rows = await hub.get_klines(symbol, interval=timeframe, limit=min(limit, 1500))
            if not rows:
                return None

            # hub 已经把 K 线转成结构化字典 (open/high/low/close/volume/open_time/close_time),
            # 这里再转回 DataFrame, 保持原方法返回结构兼容下游 (analyzers/data_management_api).
            klines = []
            for r in rows:
                klines.append([
                    int(r['open_time'].timestamp() * 1000),
                    r['open'], r['high'], r['low'], r['close'], r['volume'],
                    int(r['close_time'].timestamp() * 1000),
                    0, 0, 0, 0, 0,
                ])

            df = pd.DataFrame(klines, columns=[
                'timestamp', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_base',
                'taker_buy_quote', 'ignore'
            ])

            # 选择需要的列并转换类型（包含 quote_volume）
            df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']].copy()
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df['quote_volume'] = df['quote_volume'].astype(float)  # 添加 quote_volume 转换

            # 添加元数据
            df['symbol'] = symbol
            df['exchange'] = self.exchange_id
            df['timeframe'] = timeframe

            return df

        except Exception as e:
            error_msg = str(e)
            # 如果是无效交易对错误，提供更友好的提示
            if 'Invalid symbol' in error_msg or '-1121' in error_msg:
                logger.error(f"{self.exchange_id} 获取 {symbol} K线数据失败: 交易对格式错误或不存在 (尝试的格式: {binance_symbol})")
                logger.debug(f"提示: 币安API需要格式如 'BTCUSDT'，请确认交易对 {symbol} 在币安是否存在")
            else:
                logger.error(f"{self.exchange_id} 获取 {symbol} K线数据失败: {e}")
            return None

    async def fetch_order_book(self, symbol: str, limit: int = 20) -> Optional[Dict]:
        """
        获取订单簿（买卖盘）

        Args:
            symbol: 交易对
            limit: 深度

        Returns:
            订单簿数据
        """
        try:
            # 转换交易对格式
            binance_symbol = symbol.replace('/', '')

            # 通过 DataHub 取订单簿 (现货端点, 受熔断 + 令牌桶)
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is None:
                return None
            orderbook = await hub.spot_request_get(
                '/api/v3/depth', {'symbol': binance_symbol, 'limit': limit}
            )
            if not orderbook or not isinstance(orderbook, dict):
                return None

            # 转换格式
            bids = [[float(price), float(qty)] for price, qty in orderbook.get('bids', [])[:limit]]
            asks = [[float(price), float(qty)] for price, qty in orderbook.get('asks', [])[:limit]]

            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'timestamp': datetime.utcnow(),  # 币安订单簿没有时间戳，使用当前时间
                'bids': bids,
                'asks': asks,
                'bid_volume': sum(bid[1] for bid in bids),
                'ask_volume': sum(ask[1] for ask in asks),
            }

        except Exception as e:
            logger.error(f"{self.exchange_id} 获取 {symbol} 订单簿失败: {e}")
            return None

    async def fetch_trades(self, symbol: str, limit: int = 50) -> Optional[List[Dict]]:
        """
        获取最近成交记录

        Args:
            symbol: 交易对
            limit: 数量

        Returns:
            成交记录列表
        """
        try:
            # 转换交易对格式
            binance_symbol = symbol.replace('/', '')

            # 通过 DataHub 取成交记录 (现货端点, 受熔断 + 令牌桶)
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is None:
                return None
            trades = await hub.spot_request_get(
                '/api/v3/trades', {'symbol': binance_symbol, 'limit': limit}
            )
            if not trades or not isinstance(trades, list):
                return None

            return [{
                'exchange': self.exchange_id,
                'symbol': symbol,
                'timestamp': datetime.fromtimestamp(t['time'] / 1000),
                'price': float(t['price']),
                'amount': float(t['qty']),
                'side': 'sell' if t['isBuyerMaker'] else 'buy',
                'cost': float(t['price']) * float(t['qty'])
            } for t in trades]

        except Exception as e:
            logger.error(f"{self.exchange_id} 获取 {symbol} 成交记录失败: {e}")
            return None

    async def fetch_funding_rate(self, symbol: str) -> Optional[Dict]:
        """
        获取永续合约资金费率

        Args:
            symbol: 交易对,如 'BTC/USDT'

        Returns:
            资金费率数据字典
        """
        try:
            # 转换交易对格式: BTC/USDT -> BTCUSDT
            binance_symbol = symbol.replace('/', '')

            # 走 BinanceDataHub 缓存 (hub 60s 后台拉一次全市场 premiumIndex)
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is None:
                logger.warning(f"DataHub 未初始化, {symbol} 资金费率取不到")
                return None

            premium_map = hub.get_premium_index_map(market="futures")
            mark = premium_map.get(binance_symbol)
            funding = hub.get_funding_rate_sync(symbol)
            if mark is None and funding is None:
                logger.debug(f"DataHub 缓存暂无 {symbol} 资金费率")
                return None

            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'funding_rate': float(funding) if funding is not None else 0.0,
                'funding_time': 0,
                'timestamp': datetime.utcnow(),
                'mark_price': float(mark) if mark is not None else 0.0,
                'index_price': 0.0,
                'next_funding_time': 0,
            }

        except Exception as e:
            logger.error(f"{self.exchange_id} 获取 {symbol} 资金费率失败: {e}")
            return None


class MultiExchangeCollector:
    """多交易所数据采集器"""

    def __init__(self, config: dict):
        """
        初始化多交易所采集器

        Args:
            config: 配置字典，包含各交易所配置
        """
        self.collectors = {}
        exchanges_config = config.get('exchanges', {})

        # 初始化启用的交易所
        for exchange_id, exchange_config in exchanges_config.items():
            if exchange_config.get('enabled', False):
                try:
                    # Gate.io 使用专用采集器
                    if exchange_id == 'gate':
                        from app.collectors.gate_collector import GateCollector
                        self.collectors[exchange_id] = GateCollector(exchange_config)
                    else:
                        # Binance 等使用默认采集器
                        self.collectors[exchange_id] = PriceCollector(
                            exchange_id,
                            exchange_config
                        )
                    logger.info(f"已启用交易所: {exchange_id}")
                except Exception as e:
                    logger.error(f"初始化交易所 {exchange_id} 失败: {e}")

    async def fetch_price(self, symbol: str) -> List[Dict]:
        """
        从所有交易所获取价格（智能路由：HYPE/USDT 只从 Gate.io 获取）

        Args:
            symbol: 交易对

        Returns:
            各交易所价格列表
        """
        # 智能路由：某些交易对只从特定交易所获取
        symbol_upper = symbol.upper()
        
        # HYPE/USDT 只从 Gate.io 获取
        if symbol_upper == 'HYPE/USDT':
            if 'gate' in self.collectors:
                try:
                    result = await self.collectors['gate'].fetch_ticker(symbol)
                    return [result] if result else []
                except Exception:
                    return []
            return []
        
        # 其他交易对只从 Binance 获取（跳过 Gate.io，避免速率限制）
        # Gate.io 只用于 HYPE/USDT，其他交易对不需要从 Gate.io 获取
        tasks = []
        for exchange_id, collector in self.collectors.items():
            # 跳过 Gate.io（只用于 HYPE/USDT）
            if exchange_id == 'gate':
                continue
            tasks.append(collector.fetch_ticker(symbol))

        if not tasks:
            return []

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 过滤异常和None
        prices = [
            r for r in results
            if r is not None and not isinstance(r, Exception)
        ]

        return prices

    async def fetch_best_price(self, symbol: str) -> Optional[Dict]:
        """
        获取最优价格（多个交易所中间价）

        Args:
            symbol: 交易对

        Returns:
            聚合价格数据
        """
        prices = await self.fetch_price(symbol)

        if not prices:
            return None

        # 计算平均价格
        avg_price = sum(p['price'] for p in prices) / len(prices)
        max_price = max(p['price'] for p in prices)
        min_price = min(p['price'] for p in prices)
        total_volume = sum(p.get('volume', 0) for p in prices)

        return {
            'symbol': symbol,
            'timestamp': datetime.utcnow(),
            'price': avg_price,
            'max_price': max_price,
            'min_price': min_price,
            'spread': max_price - min_price,
            'spread_pct': (max_price - min_price) / avg_price * 100,
            'total_volume': total_volume,
            'exchanges': len(prices),
            'details': prices
        }

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1h',
        exchange: str = None
    ) -> Optional[pd.DataFrame]:
        """
        获取K线数据（智能路由：HYPE/USDT 只从 Gate.io 获取）

        Args:
            symbol: 交易对
            timeframe: 时间周期
            exchange: 指定交易所，不指定则智能选择

        Returns:
            K线DataFrame
        """
        # 智能路由：某些交易对只从特定交易所获取
        symbol_upper = symbol.upper()
        
        # HYPE/USDT 只从 Gate.io 获取
        if symbol_upper == 'HYPE/USDT':
            if 'gate' in self.collectors:
                return await self.collectors['gate'].fetch_ohlcv(symbol, timeframe)
            return None
        
        # 如果指定了交易所
        if exchange and exchange in self.collectors:
            return await self.collectors[exchange].fetch_ohlcv(
                symbol,
                timeframe
            )

        # 使用第一个可用的交易所（优先 Binance）
        if 'binance' in self.collectors:
            return await self.collectors['binance'].fetch_ohlcv(symbol, timeframe)
        elif self.collectors:
            first_exchange = list(self.collectors.values())[0]
            return await first_exchange.fetch_ohlcv(symbol, timeframe)

        return None

    async def fetch_historical_data(
        self,
        symbol: str,
        timeframe: str = '1h',
        days: int = 30,
        exchange: str = None
    ) -> Optional[pd.DataFrame]:
        """
        获取历史数据（智能路由：HYPE/USDT 只从 Gate.io 获取）

        Args:
            symbol: 交易对
            timeframe: 时间周期
            days: 历史天数
            exchange: 指定交易所

        Returns:
            历史K线DataFrame
        """
        # 智能路由：某些交易对只从特定交易所获取
        symbol_upper = symbol.upper()
        
        # HYPE/USDT 只从 Gate.io 获取
        if symbol_upper == 'HYPE/USDT':
            if 'gate' in self.collectors:
                # Gate.io 使用秒时间戳
                since = int((datetime.utcnow() - timedelta(days=days)).timestamp())
                all_data = []
                limit = 1000
                
                while True:
                    df = await self.collectors['gate'].fetch_ohlcv(
                        symbol,
                        timeframe,
                        limit=limit,
                        since=since
                    )
                    
                    if df is None or len(df) == 0:
                        break
                    
                    all_data.append(df)
                    last_timestamp = df['timestamp'].iloc[-1]
                    since = int(last_timestamp.timestamp()) + 1
                    
                    if len(df) < limit:
                        break
                    
                    await asyncio.sleep(0.5)
                
                if all_data:
                    result = pd.concat(all_data, ignore_index=True)
                    result = result.drop_duplicates(subset=['timestamp'])
                    result = result.sort_values('timestamp').reset_index(drop=True)
                    return result
            return None
        
        # 计算起始时间（毫秒时间戳，用于 Binance）
        since = int((datetime.utcnow() - timedelta(days=days)).timestamp() * 1000)

        # 选择交易所
        if exchange and exchange in self.collectors:
            collector = self.collectors[exchange]
        elif 'binance' in self.collectors:
            collector = self.collectors['binance']
        elif self.collectors:
            collector = list(self.collectors.values())[0]
        else:
            return None

        # 分批获取数据（币安限制单次获取数量）
        all_data = []
        limit = 1000  # 每次获取1000条

        try:
            while True:
                df = await collector.fetch_ohlcv(
                    symbol,
                    timeframe,
                    limit=limit,
                    since=since
                )

                if df is None or len(df) == 0:
                    break

                all_data.append(df)

                # 更新since为最后一条数据的时间
                last_timestamp = df['timestamp'].iloc[-1]
                since = int(last_timestamp.timestamp() * 1000) + 1

                # 如果获取到的数据少于limit，说明已经到最新了
                if len(df) < limit:
                    break

                # 避免请求过快
                await asyncio.sleep(0.5)

            if all_data:
                result = pd.concat(all_data, ignore_index=True)
                result = result.drop_duplicates(subset=['timestamp'])
                result = result.sort_values('timestamp').reset_index(drop=True)
                logger.info(f"获取 {symbol} {timeframe} 历史数据: {len(result)} 条")
                return result

        except Exception as e:
            logger.error(f"获取历史数据失败: {e}")

        return None

    async def monitor_price(
        self,
        symbols: List[str],
        interval: int = 60,
        callback=None
    ):
        """
        持续监控价格

        Args:
            symbols: 要监控的交易对列表
            interval: 监控间隔（秒）
            callback: 回调函数，接收价格数据
        """
        logger.info(f"开始监控价格，间隔 {interval} 秒")

        while True:
            try:
                for symbol in symbols:
                    price_data = await self.fetch_best_price(symbol)

                    if price_data and callback:
                        await callback(price_data)

                await asyncio.sleep(interval)

            except KeyboardInterrupt:
                logger.info("停止价格监控")
                break
            except Exception as e:
                logger.error(f"价格监控错误: {e}")
                await asyncio.sleep(interval)

    def get_supported_symbols(self, exchange: str = None) -> List[str]:
        """
        获取支持的交易对列表

        Args:
            exchange: 交易所ID

        Returns:
            交易对列表
        """
        if exchange and exchange in self.collectors:
            collector = self.collectors[exchange]
        elif self.collectors:
            collector = list(self.collectors.values())[0]
        else:
            return []

        try:
            # 通过 DataHub 同步取交易所信息 (现货端点, 一次性元数据, 受熔断保护)
            from app.services.binance_data_hub import get_global_data_hub
            hub = get_global_data_hub()
            if hub is None:
                return []
            exchange_info = hub.spot_request_get_sync('/api/v3/exchangeInfo', None, timeout=15)
            if not exchange_info or not isinstance(exchange_info, dict):
                return []
            symbols = []
            for symbol_info in exchange_info.get('symbols', []) or []:
                if symbol_info.get('status') == 'TRADING':
                    base = symbol_info.get('baseAsset')
                    quote = symbol_info.get('quoteAsset')
                    if base and quote:
                        symbols.append(f"{base}/{quote}")

            return symbols
        except Exception as e:
            logger.error(f"获取交易对列表失败: {e}")
            return []


# 使用示例
async def main():
    """测试价格采集器"""

    config = {
        'exchanges': {
            'binance': {
                'enabled': True,
                'api_key': '',  # 可以为空，使用公开接口
                'api_secret': '',
                'options': {
                    'defaultType': 'spot'
                }
            }
        }
    }

    collector = MultiExchangeCollector(config)

    # 测试1: 获取实时价格
    print("\n=== 测试1: 获取BTC实时价格 ===")
    prices = await collector.fetch_price('BTC/USDT')
    for price in prices:
        print(f"\n交易所: {price['exchange']}")
        print(f"价格: ${price['price']:,.2f}")
        print(f"24h变化: {price['change_24h']:.2f}%")
        print(f"成交量: {price['volume']:,.2f} BTC")

    # 测试2: 获取最优价格
    print("\n\n=== 测试2: 获取聚合价格 ===")
    best_price = await collector.fetch_best_price('BTC/USDT')
    if best_price:
        print(f"平均价格: ${best_price['price']:,.2f}")
        print(f"总成交量: {best_price['total_volume']:,.2f} BTC")

    # 测试3: 获取K线数据
    print("\n\n=== 测试3: 获取1小时K线数据 ===")
    ohlcv = await collector.fetch_ohlcv('BTC/USDT', timeframe='1h')
    if ohlcv is not None:
        print(f"获取到 {len(ohlcv)} 条K线数据")
        print("\n最近5条:")
        print(ohlcv.tail(5)[['timestamp', 'open', 'high', 'low', 'close', 'volume']])

    # 测试4: 获取历史数据
    print("\n\n=== 测试4: 获取最近7天历史数据 ===")
    historical = await collector.fetch_historical_data(
        'BTC/USDT',
        timeframe='1d',
        days=7
    )
    if historical is not None:
        print(f"获取到 {len(historical)} 天的数据")
        print(historical[['timestamp', 'close', 'volume']])

    # 测试5: 获取订单簿
    print("\n\n=== 测试5: 获取订单簿 ===")
    orderbook = await collector.collectors['binance'].fetch_order_book('BTC/USDT', limit=5)
    if orderbook:
        print(f"买一: ${orderbook['bids'][0][0]:,.2f} ({orderbook['bids'][0][1]:.4f} BTC)")
        print(f"卖一: ${orderbook['asks'][0][0]:,.2f} ({orderbook['asks'][0][1]:.4f} BTC)")

    print("\n\n测试完成！")


if __name__ == '__main__':
    asyncio.run(main())
