"""
币安合约数据采集器
支持采集永续合约的实时价格/K线/资金费率/持仓量等.

历史: 本文件早期持有 requests.Session + python-binance SDK Client 直接打 fapi,
9 个 fetch 方法各自 requests.get, 多个 symbol 并发拉取时会让 IP 走向 -1003.
现已统一收敛到 BinanceDataHub - hub 内置 rate_guard 熔断 + 令牌桶限速 +
统一缓存. 本类只剩 path/params 拼装与响应字段转换.
"""

import asyncio
from typing import List, Dict, Optional
from datetime import datetime
from loguru import logger
import pandas as pd

from app.services.binance_data_hub import get_global_data_hub


class BinanceFuturesCollector:
    """币安合约数据采集器 - 全部走 BinanceDataHub."""

    def __init__(self, config: dict = None):
        """
        初始化合约数据采集器

        Args:
            config: 配置字典. api_key/api_secret 现在不再需要 (公开接口走 hub).
        """
        self.config = config or {}
        self.exchange_id = 'binance_futures'
        logger.info("初始化币安合约采集器 (通过 BinanceDataHub)")

    def _hub(self):
        """获取 hub 单例, 未初始化时返回 None 让上层走 fallback."""
        h = get_global_data_hub()
        if h is None:
            logger.warning("BinanceDataHub 未初始化, 采集器无法工作")
        return h

    async def fetch_futures_ticker(self, symbol: str) -> Optional[Dict]:
        """获取合约 24h ticker (通过 hub)."""
        hub = self._hub()
        if hub is None:
            return None
        binance_symbol = symbol.replace('/', '')
        ticker = await hub.fapi_request_get('/fapi/v1/ticker/24hr', {'symbol': binance_symbol})
        if not ticker or not isinstance(ticker, dict) or 'lastPrice' not in ticker:
            return None
        try:
            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'timestamp': datetime.fromtimestamp(ticker['closeTime'] / 1000),
                'price': float(ticker['lastPrice']),
                'open': float(ticker['openPrice']),
                'high': float(ticker['highPrice']),
                'low': float(ticker['lowPrice']),
                'close': float(ticker['lastPrice']),
                'volume': float(ticker['volume']),
                'quote_volume': float(ticker['quoteVolume']),
                'price_change': float(ticker['priceChange']),
                'price_change_percent': float(ticker['priceChangePercent']),
                'weighted_avg_price': float(ticker['weightedAvgPrice']),
                'last_qty': float(ticker['lastQty']),
                'count': int(ticker['count']),
            }
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"解析 {symbol} 合约 ticker 失败: {e}")
            return None

    async def fetch_futures_klines(
        self,
        symbol: str,
        timeframe: str = '1m',
        limit: int = 100
    ) -> Optional[pd.DataFrame]:
        """获取合约 K 线 (通过 hub, 受熔断 + 令牌桶)."""
        hub = self._hub()
        if hub is None:
            return None
        binance_symbol = symbol.replace('/', '')
        try:
            klines = await hub.fapi_request_get(
                '/fapi/v1/klines',
                {'symbol': binance_symbol, 'interval': timeframe, 'limit': min(limit, 1500)},
            )
            if not klines:
                return None

            df = pd.DataFrame(klines, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_volume', 'trades', 'taker_buy_volume',
                'taker_buy_quote_volume', 'ignore'
            ])
            df = df[['open_time', 'open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades']].copy()
            df['timestamp'] = pd.to_datetime(df['open_time'], unit='ms')
            df['open'] = df['open'].astype(float)
            df['high'] = df['high'].astype(float)
            df['low'] = df['low'].astype(float)
            df['close'] = df['close'].astype(float)
            df['volume'] = df['volume'].astype(float)
            df['quote_volume'] = df['quote_volume'].astype(float)
            df['trades'] = df['trades'].astype(int)
            df['symbol'] = symbol
            df['exchange'] = self.exchange_id
            df['timeframe'] = timeframe
            return df
        except Exception as e:
            error_msg = str(e)
            if 'Invalid symbol' in error_msg or '-1121' in error_msg:
                logger.error(f"获取 {symbol} 合约K线失败: 交易对不存在 ({binance_symbol})")
            else:
                logger.error(f"获取 {symbol} 合约K线失败: {e}")
            return None

    async def fetch_funding_rate(self, symbol: str) -> Optional[Dict]:
        """获取永续合约资金费率 (通过 hub)."""
        hub = self._hub()
        if hub is None:
            return None
        binance_symbol = symbol.replace('/', '')
        data = await hub.fapi_request_get('/fapi/v1/premiumIndex', {'symbol': binance_symbol})
        if not data or not isinstance(data, dict):
            return None
        try:
            t = int(data.get('time', 0))
            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'funding_rate': float(data.get('lastFundingRate', 0)),
                'funding_time': t,
                'timestamp': datetime.fromtimestamp(t / 1000) if t else datetime.utcnow(),
                'mark_price': float(data.get('markPrice', 0)),
                'index_price': float(data.get('indexPrice', 0)),
                'next_funding_time': int(data.get('nextFundingTime', 0)),
                'interest_rate': float(data.get('interestRate', 0)),
            }
        except (ValueError, TypeError) as e:
            logger.error(f"解析 {symbol} 资金费率失败: {e}")
            return None

    async def fetch_open_interest(self, symbol: str) -> Optional[Dict]:
        """获取持仓量 (通过 hub)."""
        hub = self._hub()
        if hub is None:
            return None
        binance_symbol = symbol.replace('/', '')
        data = await hub.fapi_request_get('/fapi/v1/openInterest', {'symbol': binance_symbol})
        if not data or not isinstance(data, dict):
            return None
        try:
            t = int(data.get('time', 0))
            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'open_interest': float(data.get('openInterest', 0)),
                'timestamp': datetime.fromtimestamp(t / 1000) if t else datetime.utcnow(),
            }
        except (ValueError, TypeError) as e:
            logger.error(f"解析 {symbol} 持仓量失败: {e}")
            return None

    async def fetch_long_short_ratio(self, symbol: str, period: str = '5m') -> Optional[Dict]:
        """获取多空比率 (全局账户数, 通过 hub)."""
        hub = self._hub()
        if hub is None:
            return None
        binance_symbol = symbol.replace('/', '')
        data = await hub.fapi_request_get(
            '/futures/data/globalLongShortAccountRatio',
            {'symbol': binance_symbol, 'period': period, 'limit': 1},
        )
        if not data or not isinstance(data, list) or not data:
            return None
        latest = data[0]
        try:
            ts = int(latest.get('timestamp', 0))
            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'long_account': float(latest.get('longAccount', 0)),
                'short_account': float(latest.get('shortAccount', 0)),
                'long_short_ratio': float(latest.get('longShortRatio', 0)),
                'timestamp': datetime.fromtimestamp(ts / 1000) if ts else datetime.utcnow(),
            }
        except (ValueError, TypeError) as e:
            logger.error(f"解析 {symbol} 多空比失败: {e}")
            return None

    async def fetch_long_short_position_ratio(self, symbol: str, period: str = '5m') -> Optional[Dict]:
        """获取多空持仓量比率 (Top 20% 大户, 通过 hub)."""
        hub = self._hub()
        if hub is None:
            return None
        binance_symbol = symbol.replace('/', '')
        data = await hub.fapi_request_get(
            '/futures/data/topLongShortPositionRatio',
            {'symbol': binance_symbol, 'period': period, 'limit': 1},
        )
        if not data or not isinstance(data, list) or not data:
            return None
        latest = data[0]
        try:
            ts = int(latest.get('timestamp', 0))
            return {
                'exchange': self.exchange_id,
                'symbol': symbol,
                'long_position': float(latest.get('longAccount', 0)),
                'short_position': float(latest.get('shortAccount', 0)),
                'long_short_position_ratio': float(latest.get('longShortRatio', 0)),
                'timestamp': datetime.fromtimestamp(ts / 1000) if ts else datetime.utcnow(),
            }
        except (ValueError, TypeError) as e:
            logger.error(f"解析 {symbol} 持仓量比失败: {e}")
            return None

    async def fetch_all_data(self, symbol: str, timeframe: str = '1m') -> Dict:
        """
        获取所有合约数据（一次性采集）

        Args:
            symbol: 交易对
            timeframe: K线时间周期

        Returns:
            包含所有数据的字典
        """
        try:
            # 并发获取所有数据（包括账户数比和持仓量比）
            ticker_task = self.fetch_futures_ticker(symbol)
            klines_task = self.fetch_futures_klines(symbol, timeframe, limit=1)
            funding_task = self.fetch_funding_rate(symbol)
            oi_task = self.fetch_open_interest(symbol)
            ls_account_task = self.fetch_long_short_ratio(symbol, period='5m')
            ls_position_task = self.fetch_long_short_position_ratio(symbol, period='5m')

            ticker, klines, funding, oi, ls_account, ls_position = await asyncio.gather(
                ticker_task,
                klines_task,
                funding_task,
                oi_task,
                ls_account_task,
                ls_position_task,
                return_exceptions=True
            )

            result = {
                'symbol': symbol,
                'timestamp': datetime.utcnow(),
                'ticker': ticker if not isinstance(ticker, Exception) else None,
                'kline': klines.iloc[-1].to_dict() if klines is not None and not isinstance(klines, Exception) and len(klines) > 0 else None,
                'funding_rate': funding if not isinstance(funding, Exception) else None,
                'open_interest': oi if not isinstance(oi, Exception) else None,
                'long_short_account_ratio': ls_account if not isinstance(ls_account, Exception) else None,  # 账户数比
                'long_short_position_ratio': ls_position if not isinstance(ls_position, Exception) else None,  # 持仓量比
            }

            return result

        except Exception as e:
            logger.error(f"获取 {symbol} 所有合约数据失败: {e}")
            return {}

    async def get_all_futures_symbols(self) -> List[str]:
        """获取所有 USDT 永续合约交易对 (通过 hub)."""
        hub = self._hub()
        if hub is None:
            return []
        data = await hub.fapi_request_get('/fapi/v1/exchangeInfo', None, timeout=15)
        if not data or not isinstance(data, dict):
            return []
        symbols: List[str] = []
        for info in data.get('symbols', []) or []:
            if (info.get('contractType') == 'PERPETUAL'
                    and info.get('quoteAsset') == 'USDT'
                    and info.get('status') == 'TRADING'):
                base = info.get('baseAsset')
                if base:
                    symbols.append(f"{base}/USDT")
        logger.info(f"获取到 {len(symbols)} 个USDT永续合约")
        return symbols


# 测试代码
async def main():
    """测试合约数据采集器"""

    collector = BinanceFuturesCollector()

    # 测试1: 获取合约ticker
    print("\n=== 测试1: 获取BTC合约ticker ===")
    ticker = await collector.fetch_futures_ticker('BTC/USDT')
    if ticker:
        print(f"交易所: {ticker['exchange']}")
        print(f"价格: ${ticker['price']:,.2f}")
        print(f"24h涨跌: {ticker['price_change_percent']:.2f}%")
        print(f"成交量: {ticker['volume']:,.0f} 张")
        print(f"成交额: ${ticker['quote_volume']:,.0f}")

    # 测试2: 获取K线数据
    print("\n=== 测试2: 获取1分钟K线 ===")
    klines = await collector.fetch_futures_klines('BTC/USDT', '1m', limit=5)
    if klines is not None:
        print(f"获取到 {len(klines)} 条K线")
        print(klines[['timestamp', 'open', 'high', 'low', 'close', 'volume']])

    # 测试3: 获取资金费率
    print("\n=== 测试3: 获取资金费率 ===")
    funding = await collector.fetch_funding_rate('BTC/USDT')
    if funding:
        funding_rate_pct = funding['funding_rate'] * 100
        print(f"当前资金费率: {funding_rate_pct:.4f}%")
        print(f"标记价格: ${funding['mark_price']:,.2f}")
        print(f"指数价格: ${funding['index_price']:,.2f}")
        next_time = datetime.fromtimestamp(funding['next_funding_time'] / 1000)
        print(f"下次结算时间: {next_time}")

    # 测试4: 获取持仓量
    print("\n=== 测试4: 获取持仓量 ===")
    oi = await collector.fetch_open_interest('BTC/USDT')
    if oi:
        print(f"持仓量: {oi['open_interest']:,.0f} 张")

    # 测试5: 获取多空比
    print("\n=== 测试5: 获取多空比 ===")
    ls_ratio = await collector.fetch_long_short_ratio('BTC/USDT')
    if ls_ratio:
        print(f"做多账户比例: {ls_ratio['long_account']:.2%}")
        print(f"做空账户比例: {ls_ratio['short_account']:.2%}")
        print(f"多空比: {ls_ratio['long_short_ratio']:.2f}")

    # 测试6: 获取所有数据
    print("\n=== 测试6: 一次性获取所有数据 ===")
    all_data = await collector.fetch_all_data('ETH/USDT', '1m')
    if all_data.get('ticker'):
        print(f"✓ Ticker数据: ${all_data['ticker']['price']:,.2f}")
    if all_data.get('funding_rate'):
        print(f"✓ 资金费率: {all_data['funding_rate']['funding_rate']*100:.4f}%")
    if all_data.get('open_interest'):
        print(f"✓ 持仓量: {all_data['open_interest']['open_interest']:,.0f}")
    if all_data.get('long_short_ratio'):
        print(f"✓ 多空比: {all_data['long_short_ratio']['long_short_ratio']:.2f}")

    # 测试7: 获取所有合约列表
    print("\n=== 测试7: 获取所有USDT永续合约 ===")
    symbols = await collector.get_all_futures_symbols()
    print(f"总共 {len(symbols)} 个合约")
    print(f"前10个: {symbols[:10]}")

    print("\n✓ 所有测试完成！")


if __name__ == '__main__':
    asyncio.run(main())
