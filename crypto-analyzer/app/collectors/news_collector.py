"""
加密货币新闻采集器
支持多个数据源：CryptoPanic、RSS feeds、Reddit等
"""

import asyncio
import aiohttp
import feedparser
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from loguru import logger


class NewsCollector:
    """新闻采集器基类"""

    def __init__(self, config: dict):
        self.config = config

    async def collect(self, symbols: List[str]) -> List[Dict]:
        """采集新闻"""
        raise NotImplementedError


class CryptoPanicCollector(NewsCollector):
    """CryptoPanic新闻采集器"""

    BASE_URL = "https://cryptopanic.com/api/v1"

    def __init__(self, config: dict):
        super().__init__(config)
        # config 已经是 news 配置，直接读取 cryptopanic
        self.api_key = config.get('cryptopanic', {}).get('api_key', '')

    async def collect(self, symbols: List[str] = None) -> List[Dict]:
        """
        采集CryptoPanic新闻

        Args:
            symbols: 币种列表，如 ['BTC', 'ETH']

        Returns:
            新闻列表
        """
        if not self.api_key:
            logger.warning("CryptoPanic API key未配置")
            return []

        news_list = []

        try:
            async with aiohttp.ClientSession() as session:
                # 如果指定了币种，逐个查询
                if symbols:
                    for symbol in symbols:
                        url = f"{self.BASE_URL}/posts/"
                        params = {
                            'auth_token': self.api_key,
                            'currencies': symbol,
                            'kind': 'news',  # 只要新闻，不要媒体帖子
                            'filter': 'hot'  # hot: 热门, rising: 上升, bullish: 利好, bearish: 利空
                        }

                        async with session.get(url, params=params) as response:
                            if response.status == 200:
                                data = await response.json()
                                news_list.extend(self._parse_cryptopanic(data, symbol))
                            else:
                                logger.error(f"CryptoPanic API错误: {response.status}")
                else:
                    # 获取所有热门新闻
                    url = f"{self.BASE_URL}/posts/"
                    params = {
                        'auth_token': self.api_key,
                        'kind': 'news',
                        'filter': 'hot'
                    }

                    async with session.get(url, params=params) as response:
                        if response.status == 200:
                            data = await response.json()
                            news_list.extend(self._parse_cryptopanic(data))
                        else:
                            logger.error(f"CryptoPanic API错误: {response.status}")

        except Exception as e:
            logger.error(f"CryptoPanic采集失败: {e}")

        logger.info(f"CryptoPanic采集到 {len(news_list)} 条新闻")
        return news_list

    def _parse_cryptopanic(self, data: dict, symbol: str = None) -> List[Dict]:
        """解析CryptoPanic数据"""
        news_list = []

        for item in data.get('results', []):
            # 提取币种
            currencies = [c['code'] for c in item.get('currencies', [])]

            # 如果 API 没有返回 currencies，从标题中检测
            if not currencies:
                currencies = self._detect_symbols_from_title(item.get('title', ''))

            # 如果指定了 symbol，检查是否匹配
            if symbol and currencies and symbol not in currencies:
                continue

            # 计算情绪
            votes = item.get('votes', {})
            positive = votes.get('positive', 0)
            negative = votes.get('negative', 0)

            if positive > negative * 2:
                sentiment = 'positive'
            elif negative > positive * 2:
                sentiment = 'negative'
            else:
                sentiment = 'neutral'

            news = {
                'id': f"cp_{item['id']}",
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'source': item.get('domain', 'cryptopanic'),
                'published_at': item.get('published_at'),
                'symbols': currencies,
                'sentiment': sentiment,
                'votes': {
                    'positive': positive,
                    'negative': negative,
                    'important': votes.get('important', 0)
                },
                'data_source': 'cryptopanic'
            }
            news_list.append(news)

        return news_list

    def _detect_symbols_from_title(self, title: str) -> List[str]:
        """从标题中检测币种关键词"""
        title_lower = title.lower()
        detected = []

        # 币种关键词映射
        keywords = {
            'BTC': ['bitcoin', 'btc'],
            'ETH': ['ethereum', 'eth', 'ether'],
            'BNB': ['binance', 'bnb'],
            'DOGE': ['dogecoin', 'doge'],
            'SOL': ['solana', 'sol'],
            'XRP': ['ripple', 'xrp'],
            'LTC': ['litecoin', 'ltc'],
            'ADA': ['cardano', 'ada'],
            'MATIC': ['polygon', 'matic'],
            'LINK': ['chainlink', 'link']
        }

        for symbol, words in keywords.items():
            for word in words:
                if word in title_lower:
                    detected.append(symbol)
                    break

        return detected


class RSSCollector(NewsCollector):
    """RSS新闻采集器"""

    RSS_FEEDS = {
        'coindesk': 'https://www.coindesk.com/arc/outboundfeeds/rss/',
        'cointelegraph': 'https://cointelegraph.com/rss',
        'bitcoinmagazine': 'https://bitcoinmagazine.com/.rss/full/',
        'cryptoslate': 'https://cryptoslate.com/feed/',
        'decrypt': 'https://decrypt.co/feed',
        'theblock': 'https://www.theblock.co/rss.xml'
    }

    async def collect(self, symbols: List[str] = None) -> List[Dict]:
        """采集RSS新闻"""
        news_list = []

        for source_name, feed_url in self.RSS_FEEDS.items():
            try:
                # feedparser不支持异步，在executor中运行
                loop = asyncio.get_event_loop()
                feed = await loop.run_in_executor(None, feedparser.parse, feed_url)

                for entry in feed.entries[:10]:  # 每个源取前10条
                    # 提取标题和描述
                    title = entry.get('title', '')
                    description = entry.get('summary', '')

                    # 简单的币种识别
                    detected_symbols = self._detect_symbols(title + ' ' + description, symbols)

                    if not symbols or detected_symbols:
                        news = {
                            'id': f"rss_{source_name}_{hash(entry.link)}",
                            'title': title,
                            'url': entry.get('link', ''),
                            'source': source_name,
                            'published_at': entry.get('published', ''),
                            'symbols': detected_symbols,
                            'sentiment': 'neutral',  # RSS不提供情绪，后续NLP分析
                            'description': description[:200],
                            'data_source': 'rss'
                        }
                        news_list.append(news)

            except Exception as e:
                logger.error(f"RSS采集失败 {source_name}: {e}")

        logger.info(f"RSS采集到 {len(news_list)} 条新闻")
        return news_list

    def _detect_symbols(self, text: str, target_symbols: List[str] = None) -> List[str]:
        """从文本中检测币种"""
        text_lower = text.lower()
        detected = []

        # 币种关键词映射
        symbol_keywords = {
            'BTC': ['bitcoin', 'btc'],
            'ETH': ['ethereum', 'eth', 'ether'],
            'BNB': ['binance coin', 'bnb'],
            'DOGE': ['dogecoin', 'doge'],
            'SOL': ['solana', 'sol'],
            'XRP': ['ripple', 'xrp'],
            'LTC': ['litecoin', 'ltc'],
            'EOS': ['eos'],
            'ADA': ['cardano', 'ada'],
            'MATIC': ['polygon', 'matic'],
            'DOT': ['polkadot', 'dot'],
            'AVAX': ['avalanche', 'avax'],
            'LINK': ['chainlink', 'link'],
            'UNI': ['uniswap', 'uni'],
            'ATOM': ['cosmos', 'atom']
        }

        for symbol, keywords in symbol_keywords.items():
            if target_symbols and symbol not in target_symbols:
                continue

            for keyword in keywords:
                if keyword in text_lower:
                    detected.append(symbol)
                    break

        return detected


class RedditCollector(NewsCollector):
    """Reddit社区情绪采集器"""

    SUBREDDITS = [
        'CryptoCurrency',
        'Bitcoin',
        'ethereum',
        'CryptoMarkets'
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        # 代理配置（从 smart_money 或 reddit 配置中读取）
        self.proxy = self.config.get('smart_money', {}).get('proxy', '') or \
                     self.config.get('reddit', {}).get('proxy', '')

    async def collect(self, symbols: List[str] = None) -> List[Dict]:
        """
        采集Reddit热门帖子
        使用 Reddit API v2 (不需要 asyncpraw)
        """
        news_list = []

        try:
            # Reddit API配置
            client_id = self.config.get('reddit', {}).get('client_id')
            client_secret = self.config.get('reddit', {}).get('client_secret')
            subreddits = self.config.get('reddit', {}).get('subreddits', self.SUBREDDITS)

            if not client_id or not client_secret:
                logger.warning("Reddit API未配置，跳过")
                return []

            # 获取访问令牌
            auth_url = "https://www.reddit.com/api/v1/access_token"
            auth = aiohttp.BasicAuth(client_id, client_secret)
            headers = {'User-Agent': 'crypto-analyzer/1.0'}

            # 配置代理
            connector = None
            if self.proxy:
                connector = aiohttp.TCPConnector()

            async with aiohttp.ClientSession(connector=connector) as session:
                # 获取 access token
                data = {
                    'grant_type': 'client_credentials'
                }

                async with session.post(auth_url, auth=auth, data=data, headers=headers, proxy=self.proxy or None) as response:
                    if response.status != 200:
                        logger.error(f"Reddit认证失败: {response.status}")
                        return []

                    auth_data = await response.json()
                    access_token = auth_data.get('access_token')

                if not access_token:
                    logger.error("Reddit未能获取access_token")
                    return []

                # 使用 access token 获取帖子
                headers['Authorization'] = f'Bearer {access_token}'

                # 逐个采集subreddit
                for subreddit in subreddits:
                    try:
                        # 获取热门帖子
                        url = f"https://oauth.reddit.com/r/{subreddit}/hot"
                        params = {'limit': 10}  # 每个subreddit取10个热门帖子

                        async with session.get(url, headers=headers, params=params, proxy=self.proxy or None) as response:
                            if response.status == 200:
                                data = await response.json()
                                posts = data.get('data', {}).get('children', [])

                                for post_data in posts:
                                    post = post_data.get('data', {})

                                    # 从标题和内容检测币种
                                    title = post.get('title', '')
                                    selftext = post.get('selftext', '')
                                    detected_symbols = self._detect_symbols(
                                        title + ' ' + selftext, symbols
                                    )

                                    # 如果指定了symbols，只保留相关帖子
                                    if symbols and not detected_symbols:
                                        continue

                                    # 计算情绪（基于upvote_ratio）
                                    upvote_ratio = post.get('upvote_ratio', 0.5)
                                    if upvote_ratio > 0.7:
                                        sentiment = 'positive'
                                    elif upvote_ratio < 0.4:
                                        sentiment = 'negative'
                                    else:
                                        sentiment = 'neutral'

                                    news = {
                                        'id': f"reddit_{post.get('id', '')}",
                                        'title': title,
                                        'url': f"https://reddit.com{post.get('permalink', '')}",
                                        'source': f"reddit_{subreddit}",
                                        'published_at': datetime.fromtimestamp(
                                            post.get('created_utc', 0)
                                        ).isoformat(),
                                        'symbols': detected_symbols,
                                        'sentiment': sentiment,
                                        'data_source': 'reddit',
                                        'metadata': {
                                            'subreddit': subreddit,
                                            'upvotes': post.get('ups', 0),
                                            'upvote_ratio': upvote_ratio,
                                            'num_comments': post.get('num_comments', 0),
                                            'author': post.get('author', '')
                                        }
                                    }
                                    news_list.append(news)

                            else:
                                logger.error(f"获取r/{subreddit}失败: {response.status}")

                    except Exception as e:
                        logger.error(f"采集r/{subreddit}失败: {e}")

                    # 避免频率限制
                    await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Reddit采集失败: {e}")

        logger.info(f"Reddit采集到 {len(news_list)} 条帖子")
        return news_list

    def _detect_symbols(self, text: str, target_symbols: List[str] = None) -> List[str]:
        """从文本中检测币种"""
        text_lower = text.lower()
        detected = []

        # 币种关键词映射
        symbol_keywords = {
            'BTC': ['bitcoin', 'btc'],
            'ETH': ['ethereum', 'eth', 'ether'],
            'BNB': ['binance', 'bnb'],
            'DOGE': ['dogecoin', 'doge'],
            'SOL': ['solana', 'sol'],
            'XRP': ['ripple', 'xrp'],
            'LTC': ['litecoin', 'ltc'],
            'ADA': ['cardano', 'ada'],
            'MATIC': ['polygon', 'matic'],
            'LINK': ['chainlink', 'link']
        }

        for symbol, keywords in symbol_keywords.items():
            if target_symbols and symbol not in target_symbols:
                continue

            for keyword in keywords:
                if keyword in text_lower:
                    detected.append(symbol)
                    break

        return detected


class NewsAggregator:
    """新闻聚合器 - 统一管理所有采集器"""

    def __init__(self, config: dict):
        self.config = config
        self.collectors = []

        # 初始化启用的采集器
        if config.get('news', {}).get('cryptopanic', {}).get('enabled'):
            self.collectors.append(CryptoPanicCollector(config.get('news', {})))

        if config.get('news', {}).get('rss', {}).get('enabled', True):
            self.collectors.append(RSSCollector(config.get('news', {})))

        if config.get('news', {}).get('reddit', {}).get('enabled'):
            self.collectors.append(RedditCollector(config.get('news', {})))

        logger.info(f"初始化了 {len(self.collectors)} 个新闻采集器")

    async def collect_all(self, symbols: List[str] = None) -> List[Dict]:
        """
        从所有数据源采集新闻

        Args:
            symbols: 要监控的币种列表

        Returns:
            去重后的新闻列表
        """
        all_news = []

        # 并发采集所有数据源
        tasks = [collector.collect(symbols) for collector in self.collectors]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"采集器错误: {result}")
            else:
                all_news.extend(result)

        # 去重（基于URL）
        unique_news = self._deduplicate(all_news)

        # 按时间排序
        unique_news.sort(key=lambda x: x.get('published_at', ''), reverse=True)

        logger.info(f"总共采集到 {len(unique_news)} 条去重后的新闻")
        return unique_news

    def _deduplicate(self, news_list: List[Dict]) -> List[Dict]:
        """根据URL去重"""
        seen_urls = set()
        unique_news = []

        for news in news_list:
            url = news.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique_news.append(news)

        return unique_news

    async def get_symbol_sentiment(self, symbol: str, hours: int = 24) -> Dict:
        """
        获取指定币种的新闻情绪汇总

        Args:
            symbol: 币种代码，如 'BTC'
            hours: 统计过去多少小时的新闻

        Returns:
            情绪统计数据
        """
        # 采集新闻
        all_news = await self.collect_all([symbol])

        # 过滤时间范围
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent_news = [
            news for news in all_news
            if self._parse_datetime(news.get('published_at', '')) > cutoff_time
        ]

        # 统计情绪
        positive_count = sum(1 for n in recent_news if n.get('sentiment') == 'positive')
        negative_count = sum(1 for n in recent_news if n.get('sentiment') == 'negative')
        neutral_count = sum(1 for n in recent_news if n.get('sentiment') == 'neutral')
        total_count = len(recent_news)

        # 计算情绪指数 (-100 到 +100)
        if total_count > 0:
            sentiment_index = ((positive_count - negative_count) / total_count) * 100
        else:
            sentiment_index = 0

        return {
            'symbol': symbol,
            'period_hours': hours,
            'total_news': total_count,
            'positive': positive_count,
            'negative': negative_count,
            'neutral': neutral_count,
            'sentiment_index': round(sentiment_index, 2),
            'recent_news': recent_news[:5]  # 返回最近5条
        }

    def _parse_datetime(self, date_str: str) -> datetime:
        """解析各种格式的日期时间（返回naive datetime以便比较）"""
        if not date_str:
            return datetime.min

        try:
            # ISO格式
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            # 直接返回datetime，不做时区处理
            return dt
        except:
            try:
                # RSS格式
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str)
                # 直接返回datetime，不做时区处理
                return dt
            except:
                return datetime.min


# 使用示例
async def main():
    """测试新闻采集器"""

    config = {
        'news': {
            'cryptopanic': {
                'enabled': True,
                'api_key': 'your_api_key_here'  # 从 https://cryptopanic.com/developers/api/ 获取
            },
            'rss': {
                'enabled': True
            },
            'reddit': {
                'enabled': False
            }
        }
    }

    aggregator = NewsAggregator(config)

    # 采集BTC相关新闻
    print("\n=== 采集BTC新闻 ===")
    btc_news = await aggregator.collect_all(['BTC', 'ETH'])
    for news in btc_news[:5]:
        print(f"\n标题: {news['title']}")
        print(f"来源: {news['source']}")
        print(f"情绪: {news['sentiment']}")
        print(f"币种: {news.get('symbols', [])}")
        print(f"URL: {news['url']}")

    # 获取BTC 24小时情绪指数
    print("\n\n=== BTC 24小时新闻情绪 ===")
    sentiment = await aggregator.get_symbol_sentiment('BTC', hours=24)
    print(f"总新闻数: {sentiment['total_news']}")
    print(f"利好: {sentiment['positive']}")
    print(f"利空: {sentiment['negative']}")
    print(f"中性: {sentiment['neutral']}")
    print(f"情绪指数: {sentiment['sentiment_index']}/100")


if __name__ == '__main__':
    asyncio.run(main())