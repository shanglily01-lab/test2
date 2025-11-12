"""
Gate.io 交易所数据采集器
使用 Gate.io 公开 API 获取数据
支持实时价格、K线数据、订单簿
"""

import aiohttp
import asyncio
from typing import List, Dict, Optional
from datetime import datetime
from loguru import logger
import pandas as pd


class GateCollector:
    """Gate.io 数据采集器"""

    def __init__(self, config: dict = None):
        """
        初始化采集器

        Args:
            config: 配置字典，包含API密钥等
        """
        self.exchange_id = 'gate'
        self.config = config or {}

        # Gate.io API 基础 URL
        self.base_url = 'https://api.gateio.ws/api/v4'

        # API 密钥（可选，公开数据不需要）
        self.api_key = self.config.get('api_key', '').strip()
        self.api_secret = self.config.get('api_secret', '').strip()

        logger.info(f"初始化 Gate.io 采集器 {'(使用API密钥)' if self.api_key else '(公开接口模式)'}")

    async def fetch_ticker(self, symbol: str) -> Optional[Dict]:
        """
        获取实时价格

        Args:
            symbol: 交易对，如 'BTC/USDT' 会转换为 'BTC_USDT'

        Returns:
            价格数据字典
        """
        try:
            # 转换交易对格式: BTC/USDT -> BTC_USDT
            gate_symbol = symbol.replace('/', '_')

            # Gate.io API 端点
            url = f"{self.base_url}/spot/tickers"
            params = {'currency_pair': gate_symbol}

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data and len(data) > 0:
                            ticker = data[0]

                            # 计算24h开盘价 (Gate.io 不直接提供)
                            last_price = float(ticker['last'])
                            change_pct = float(ticker.get('change_percentage', 0))
                            # open_24h = last / (1 + change_pct/100)
                            open_24h = last_price / (1 + change_pct / 100) if change_pct != -100 else last_price

                            return {
                                'exchange': self.exchange_id,
                                'symbol': symbol,
                                'timestamp': datetime.now(),
                                'price': last_price,
                                'open': open_24h,
                                'high': float(ticker['high_24h']),
                                'low': float(ticker['low_24h']),
                                'close': last_price,
                                'volume': float(ticker['base_volume']),
                                'quote_volume': float(ticker['quote_volume']),
                                'bid': float(ticker['highest_bid']),
                                'ask': float(ticker['lowest_ask']),
                                'change_24h': change_pct,
                            }
                    else:
                        logger.error(f"Gate.io 获取 {symbol} 价格失败: HTTP {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Gate.io 获取 {symbol} 实时价格失败: {e}")
            return None

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1h',
        limit: int = 100,
        since: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取K线数据 (OHLCV)

        Args:
            symbol: 交易对
            timeframe: 时间周期 (5m, 1h, 1d等)
            limit: 获取数量
            since: 起始时间戳(秒)

        Returns:
            DataFrame包含 [timestamp, open, high, low, close, volume]
        """
        try:
            # 转换交易对格式
            gate_symbol = symbol.replace('/', '_')

            # Gate.io interval 格式
            interval_map = {
                '1m': '1m',
                '5m': '5m',
                '15m': '15m',
                '30m': '30m',
                '1h': '1h',
                '4h': '4h',
                '1d': '1d',
                '1w': '7d',
            }

            interval = interval_map.get(timeframe, '1h')

            # 构建请求参数
            url = f"{self.base_url}/spot/candlesticks"
            params = {
                'currency_pair': gate_symbol,
                'interval': interval,
                'limit': limit
            }

            if since:
                params['from'] = since

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        klines = await response.json()

                        if not klines:
                            return None

                        # 转换为DataFrame
                        # Gate.io 格式: [timestamp, quote_volume, close, high, low, open, base_volume, is_complete]
                        df = pd.DataFrame(klines, columns=[
                            'timestamp', 'quote_volume', 'close', 'high', 'low', 'open', 'volume', 'is_complete'
                        ])

                        # 重新排列列顺序 (保留 quote_volume)
                        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']].copy()

                        # 转换类型
                        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='s')
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
                    else:
                        logger.error(f"Gate.io 获取 {symbol} K线失败: HTTP {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Gate.io 获取 {symbol} K线数据失败: {e}")
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
            gate_symbol = symbol.replace('/', '_')

            url = f"{self.base_url}/spot/order_book"
            params = {
                'currency_pair': gate_symbol,
                'limit': limit
            }

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()

                        # 转换格式
                        bids = [[float(price), float(qty)] for price, qty in data['bids'][:limit]]
                        asks = [[float(price), float(qty)] for price, qty in data['asks'][:limit]]

                        return {
                            'exchange': self.exchange_id,
                            'symbol': symbol,
                            'timestamp': datetime.now(),
                            'bids': bids,
                            'asks': asks,
                            'bid_volume': sum(bid[1] for bid in bids),
                            'ask_volume': sum(ask[1] for ask in asks),
                        }
                    else:
                        logger.error(f"Gate.io 获取 {symbol} 订单簿失败: HTTP {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Gate.io 获取 {symbol} 订单簿失败: {e}")
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
            gate_symbol = symbol.replace('/', '_')

            url = f"{self.base_url}/spot/trades"
            params = {
                'currency_pair': gate_symbol,
                'limit': limit
            }

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        trades = await response.json()

                        return [{
                            'exchange': self.exchange_id,
                            'symbol': symbol,
                            'timestamp': datetime.fromtimestamp(int(t['create_time'])),
                            'price': float(t['price']),
                            'amount': float(t['amount']),
                            'side': t['side'],  # buy or sell
                            'cost': float(t['price']) * float(t['amount'])
                        } for t in trades]
                    else:
                        logger.error(f"Gate.io 获取 {symbol} 成交记录失败: HTTP {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Gate.io 获取 {symbol} 成交记录失败: {e}")
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
            # 转换交易对格式: BTC/USDT -> BTC_USDT
            gate_symbol = symbol.replace('/', '_')

            # Gate.io 期货 API 使用 settle 参数区分结算货币
            # USDT 永续合约使用 settle=usdt
            settle = 'usdt'

            # 使用 tickers 端点获取资金费率 (包含更多实时信息)
            url = f"{self.base_url.replace('/v4', '/v4/futures')}/{settle}/tickers"
            params = {'contract': gate_symbol}

            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data and len(data) > 0:
                            ticker = data[0]

                            # 获取时间戳 (使用当前时间)
                            now = datetime.now()

                            return {
                                'exchange': self.exchange_id,
                                'symbol': symbol,
                                'funding_rate': float(ticker.get('funding_rate', 0)),
                                'funding_time': int(now.timestamp() * 1000),  # 毫秒时间戳
                                'timestamp': now,
                                'mark_price': float(ticker.get('mark_price', 0)),
                                'index_price': float(ticker.get('index_price', 0)),
                                'last_price': float(ticker.get('last', 0)),
                                # Gate.io 资金费率每8小时结算一次 (28800秒)
                                'funding_interval': 28800,
                            }
                    else:
                        logger.warning(f"Gate.io 获取 {symbol} 资金费率失败: HTTP {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Gate.io 获取 {symbol} 资金费率失败: {e}")
            return None

    async def fetch_futures_klines(
        self,
        symbol: str,
        timeframe: str = '1m',
        limit: int = 100,
        since: Optional[int] = None
    ) -> Optional[pd.DataFrame]:
        """
        获取合约K线数据 (OHLCV)

        Args:
            symbol: 交易对
            timeframe: 时间周期 (1m, 5m, 15m, 1h, 1d等)
            limit: 获取数量
            since: 起始时间戳(秒)

        Returns:
            DataFrame包含 [timestamp, open, high, low, close, volume]
        """
        try:
            # 转换交易对格式
            gate_symbol = symbol.replace('/', '_')

            # Gate.io interval 格式
            interval_map = {
                '1m': '1m',
                '5m': '5m',
                '15m': '15m',
                '30m': '30m',
                '1h': '1h',
                '4h': '4h',
                '1d': '1d',
                '1w': '7d',
            }

            interval = interval_map.get(timeframe, '1h')

            # Gate.io 期货 API 使用 settle 参数区分结算货币
            # USDT 永续合约使用 settle=usdt
            settle = 'usdt'

            # 构建请求参数
            url = f"{self.base_url.replace('/v4', '/v4/futures')}/{settle}/candlesticks"
            params = {
                'contract': gate_symbol,
                'interval': interval,
                'limit': limit
            }

            if since:
                params['from'] = since

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, params=params) as response:
                    if response.status == 200:
                        klines = await response.json()

                        if not klines:
                            return None

                        # 转换为DataFrame
                        # Gate.io 期货格式: [timestamp, quote_volume, close, high, low, open, base_volume, is_complete]
                        df = pd.DataFrame(klines, columns=[
                            'timestamp', 'quote_volume', 'close', 'high', 'low', 'open', 'volume', 'is_complete'
                        ])

                        # 重新排列列顺序 (保留 quote_volume)
                        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume', 'quote_volume']].copy()

                        # 转换类型
                        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(int), unit='s')
                        df['open'] = df['open'].astype(float)
                        df['high'] = df['high'].astype(float)
                        df['low'] = df['low'].astype(float)
                        df['close'] = df['close'].astype(float)
                        df['volume'] = df['volume'].astype(float)
                        df['quote_volume'] = df['quote_volume'].astype(float)

                        # 添加元数据
                        df['symbol'] = symbol
                        df['exchange'] = self.exchange_id
                        df['timeframe'] = timeframe

                        return df
                    else:
                        logger.error(f"Gate.io 获取 {symbol} 合约K线失败: HTTP {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Gate.io 获取 {symbol} 合约K线数据失败: {e}")
            return None

    async def fetch_historical_futures_data(
        self,
        symbol: str,
        timeframe: str = '1h',
        days: int = 30
    ) -> Optional[pd.DataFrame]:
        """
        获取历史合约数据（分批获取）

        Args:
            symbol: 交易对
            timeframe: 时间周期
            days: 历史天数

        Returns:
            历史K线DataFrame
        """
        from datetime import timedelta
        
        # 计算起始时间（秒时间戳）
        since = int((datetime.now() - timedelta(days=days)).timestamp())
        
        all_data = []
        limit = 1000  # 每次获取1000条
        
        try:
            while True:
                df = await self.fetch_futures_klines(
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
                since = int(last_timestamp.timestamp()) + 1

                # 如果获取到的数据少于limit，说明已经到最新了
                if len(df) < limit:
                    break

                # 避免请求过快
                await asyncio.sleep(0.5)

            if all_data:
                result = pd.concat(all_data, ignore_index=True)
                result = result.drop_duplicates(subset=['timestamp'])
                result = result.sort_values('timestamp').reset_index(drop=True)
                logger.info(f"获取 {symbol} {timeframe} 合约历史数据: {len(result)} 条")
                return result

        except Exception as e:
            logger.error(f"获取合约历史数据失败: {e}")

        return None


# 使用示例
async def main():
    """测试 Gate.io 采集器"""

    collector = GateCollector()

    print("\n=== 测试1: 获取BTC实时价格 ===")
    ticker = await collector.fetch_ticker('BTC/USDT')
    if ticker:
        print(f"交易所: {ticker['exchange']}")
        print(f"价格: ${ticker['price']:,.2f}")
        print(f"24h变化: {ticker['change_24h']:.2f}%")
        print(f"成交量: {ticker['volume']:,.4f} BTC")

    print("\n=== 测试2: 获取1小时K线数据 ===")
    ohlcv = await collector.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=10)
    if ohlcv is not None:
        print(f"获取到 {len(ohlcv)} 条K线数据")
        print("\n最近5条:")
        print(ohlcv.tail(5)[['timestamp', 'open', 'high', 'low', 'close', 'volume']])

    print("\n=== 测试3: 获取订单簿 ===")
    orderbook = await collector.fetch_order_book('BTC/USDT', limit=5)
    if orderbook:
        print(f"买一: ${orderbook['bids'][0][0]:,.2f} ({orderbook['bids'][0][1]:.4f} BTC)")
        print(f"卖一: ${orderbook['asks'][0][0]:,.2f} ({orderbook['asks'][0][1]:.4f} BTC)")

    print("\n=== 测试4: 获取资金费率 ===")
    funding = await collector.fetch_funding_rate('BTC/USDT')
    if funding:
        print(f"交易所: {funding['exchange']}")
        print(f"交易对: {funding['symbol']}")
        print(f"资金费率: {funding['funding_rate'] * 100:.4f}%")
        print(f"标记价格: ${funding['mark_price']:,.2f}")
        print(f"指数价格: ${funding['index_price']:,.2f}")
        print(f"最新价格: ${funding['last_price']:,.2f}")
        print(f"结算间隔: {funding['funding_interval'] // 3600}小时")

    print("\n测试完成！")


if __name__ == '__main__':
    asyncio.run(main())
