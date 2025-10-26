"""
增强版Dashboard API
整合所有数据源提供综合仪表盘数据
"""

import asyncio
import logging
from typing import Dict, List
from datetime import datetime, timedelta

from app.database.db_service import DatabaseService
from app.database.hyperliquid_db import HyperliquidDB
from app.analyzers.enhanced_investment_analyzer import EnhancedInvestmentAnalyzer
from app.analyzers.technical_indicators import TechnicalIndicators
from app.services.hyperliquid_token_mapper import get_token_mapper
import pandas as pd

logger = logging.getLogger(__name__)


class EnhancedDashboard:
    """增强版仪表盘数据服务"""

    def __init__(self, config: dict):
        """
        初始化

        Args:
            config: 系统配置
        """
        self.config = config
        self.db_service = DatabaseService(config.get('database', {}))
        self.analyzer = EnhancedInvestmentAnalyzer(config)
        self.technical_analyzer = TechnicalIndicators(config.get('indicators', {}))
        self.token_mapper = get_token_mapper()  # Hyperliquid代币映射器

    async def get_dashboard_data(self, symbols: List[str] = None) -> Dict:
        """
        获取完整的仪表盘数据

        Args:
            symbols: 币种列表,如 ['BTC/USDT', 'ETH/USDT']

        Returns:
            仪表盘数据字典
        """
        if symbols is None:
            symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 并行获取各维度数据
        tasks = [
            self._get_prices(symbols),
            self._get_recommendations(symbols),
            self._get_news(limit=20),
            self._get_hyperliquid_smart_money(),
            self._get_system_stats(),
            self._get_futures_data(symbols)  # 新增：获取合约数据
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        prices, recommendations, news, hyperliquid, stats, futures = results

        # 处理异常
        if isinstance(prices, Exception):
            logger.error(f"获取价格失败: {prices}")
            prices = []
        if isinstance(recommendations, Exception):
            logger.error(f"获取建议失败: {recommendations}")
            recommendations = []
        if isinstance(news, Exception):
            logger.error(f"获取新闻失败: {news}")
            news = []
        if isinstance(hyperliquid, Exception):
            logger.error(f"获取Hyperliquid数据失败: {hyperliquid}")
            hyperliquid = {}
        if isinstance(stats, Exception):
            logger.error(f"获取统计失败: {stats}")
            stats = {}
        if isinstance(futures, Exception):
            logger.error(f"获取合约数据失败: {futures}")
            futures = []

        # 统计信号
        signal_stats = self._calculate_signal_stats(recommendations)

        return {
            'success': True,
            'data': {
                'prices': prices,
                'recommendations': recommendations,
                'news': news,
                'hyperliquid': hyperliquid,
                'futures': futures,  # 新增：合约数据
                'stats': {
                    **stats,
                    **signal_stats
                },
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
        }

    async def _get_prices(self, symbols: List[str]) -> List[Dict]:
        """
        获取实时价格

        Returns:
            价格列表
        """
        prices = []

        for symbol in symbols:
            try:
                # 从数据库获取最新价格
                latest = self.db_service.get_latest_kline(symbol, '1m')

                if latest:
                    # 计算24h涨跌
                    change_24h = self._calculate_24h_change(symbol)

                    prices.append({
                        'symbol': symbol.replace('/USDT', ''),
                        'full_symbol': symbol,
                        'price': float(latest.close),
                        'change_24h': change_24h,
                        'volume_24h': self._get_24h_volume(symbol),
                        'high_24h': self._get_24h_high(symbol),
                        'low_24h': self._get_24h_low(symbol),
                        'timestamp': latest.timestamp.strftime('%Y-%m-%d %H:%M:%S')
                    })
            except Exception as e:
                logger.warning(f"获取{symbol}价格失败: {e}")
                continue

        # 按价格变化排序
        prices.sort(key=lambda x: x['change_24h'], reverse=True)

        return prices

    async def _get_recommendations(self, symbols: List[str]) -> List[Dict]:
        """
        获取投资建议

        Returns:
            建议列表
        """
        recommendations = []

        for symbol in symbols:
            try:
                # 获取各维度数据
                technical_data = await self._get_technical_data(symbol)
                news_data = await self._get_news_sentiment(symbol)
                funding_data = await self._get_funding_rate(symbol)
                hyperliquid_data = await self._get_hyperliquid_for_symbol(symbol)
                ethereum_data = await self._get_ethereum_data(symbol)

                # 优先从technical_data获取价格，如果不存在则从数据库获取最新价格
                current_price = technical_data.get('price', 0) if technical_data else 0
                if current_price == 0:
                    # 尝试从kline_data表获取最新价格
                    latest_kline = self.db_service.get_latest_kline(symbol, '1m')
                    if latest_kline:
                        current_price = float(latest_kline.close)
                        logger.info(f"{symbol} 从数据库获取最新价格: {current_price}")

                # 生成综合分析
                analysis = self.analyzer.analyze(
                    symbol=symbol,
                    technical_data=technical_data,
                    news_data=news_data,
                    funding_data=funding_data,
                    hyperliquid_data=hyperliquid_data,
                    ethereum_data=ethereum_data,
                    current_price=current_price
                )

                # 转换为前端格式
                recommendations.append({
                    'symbol': symbol.replace('/USDT', ''),
                    'full_symbol': symbol,
                    'signal': analysis['signal'],
                    'confidence': analysis['confidence'],
                    'current_price': analysis['price']['current'],
                    'entry_price': analysis['price']['entry'],
                    'stop_loss': analysis['price']['stop_loss'],
                    'take_profit': analysis['price']['take_profit'],
                    'reasons': analysis['reasons'],
                    'risk_level': analysis['risk']['level'],
                    'risk_factors': analysis['risk']['factors'],
                    'scores': analysis['score'],
                    'data_sources': analysis['data_sources'],
                    'funding_rate': funding_data if funding_data else None
                })

            except Exception as e:
                logger.warning(f"生成{symbol}建议失败: {e}")
                continue

        # 按置信度排序
        recommendations.sort(key=lambda x: x['confidence'], reverse=True)

        return recommendations

    async def _get_news(self, limit: int = 20) -> List[Dict]:
        """
        获取最新新闻

        Returns:
            新闻列表
        """
        try:
            news_list = self.db_service.get_recent_news(hours=24, limit=limit)

            result = []
            for news in news_list:
                # 处理发布时间
                if hasattr(news, 'published_datetime') and news.published_datetime:
                    published_at = news.published_datetime.strftime('%Y-%m-%d %H:%M')
                elif hasattr(news, 'published_at') and news.published_at:
                    published_at = news.published_at if isinstance(news.published_at, str) else str(news.published_at)
                else:
                    published_at = 'N/A'

                # 处理采集时间  
                if hasattr(news, 'collected_at') and news.collected_at:
                    if isinstance(news.collected_at, datetime):
                        collected_at = news.collected_at.strftime('%Y-%m-%d %H:%M')
                    else:
                        collected_at = str(news.collected_at)
                elif hasattr(news, 'created_at') and news.created_at:
                    if isinstance(news.created_at, datetime):
                        collected_at = news.created_at.strftime('%Y-%m-%d %H:%M')
                    else:
                        collected_at = str(news.created_at)
                else:
                    collected_at = 'N/A'

                result.append({
                    'title': news.title or 'No Title',
                    'source': news.source or 'Unknown',
                    'url': news.url or '',
                    'symbols': news.symbols or '',
                    'sentiment': news.sentiment or 'neutral',
                    'sentiment_score': float(news.sentiment_score) if news.sentiment_score else 0.5,
                    'published_at': published_at,
                    'collected_at': collected_at
                })

            return result

        except Exception as e:
            logger.error(f"获取新闻失败: {e}")
            import traceback
            traceback.print_exc()
            return []

    async def _get_hyperliquid_smart_money(self) -> Dict:
        """
        获取Hyperliquid聪明钱概览

        Returns:
            聪明钱数据
        """
        try:
            logger.info("开始获取Hyperliquid聪明钱数据...")

            with HyperliquidDB() as db:
                # 获取活跃监控钱包总数
                monitored = db.get_monitored_wallets(active_only=True)
                total_monitored = len(monitored)
                logger.info(f"获取到 {total_monitored} 个监控钱包")

                if not monitored:
                    logger.warning("没有活跃的监控钱包")
                    return {
                        'monitored_wallets': 0,
                        'total_volume_24h': 0,
                        'recent_trades': [],
                        'top_coins': []
                    }

                # 直接查询最近24小时有交易的钱包（改进方案）
                # 查询所有监控钱包的交易，按交易金额倒序排列
                logger.info(f"查询最近24小时的交易记录（从 {total_monitored} 个监控钱包）...")

                cutoff_time = datetime.now() - timedelta(hours=24)

                # 查询最近24小时的所有交易，按交易量倒序
                # 这会从所有5000+个钱包中找出交易金额最大的交易
                db.cursor.execute("""
                    SELECT
                        t.*,
                        w.label as wallet_label
                    FROM hyperliquid_wallet_trades t
                    LEFT JOIN hyperliquid_monitored_wallets w ON t.address = w.address
                    WHERE t.trade_time >= %s
                        AND w.is_monitoring = 1
                    ORDER BY t.notional_usd DESC
                    LIMIT 500
                """, (cutoff_time,))

                all_trades = db.cursor.fetchall()
                logger.info(f"✅ 查询到 {len(all_trades)} 笔大额交易（来自所有监控钱包）")

                # 统计涉及的钱包数量
                unique_wallets = set(trade.get('address') for trade in all_trades if trade.get('address'))
                logger.info(f"   涉及 {len(unique_wallets)} 个活跃钱包")

                # 获取最近24小时交易
                recent_trades = []
                total_volume = 0
                net_flow_by_coin = {}

                for trade in all_trades:
                    try:
                        # 格式化代币符号（将@N转换为真实符号）
                        coin_display = self.token_mapper.format_symbol(trade['coin'])

                        # 获取钱包标签
                        wallet_label = trade.get('wallet_label', 'Unknown')
                        if not wallet_label or wallet_label == 'None':
                            wallet_label = trade.get('address', 'Unknown')[:10] + '...'

                        recent_trades.append({
                            'wallet_label': wallet_label,
                            'coin': coin_display,  # 使用格式化后的符号
                            'coin_raw': trade['coin'],  # 保留原始索引
                            'side': trade['side'],
                            'notional_usd': float(trade['notional_usd']),
                            'price': float(trade['price']),
                            'closed_pnl': float(trade['closed_pnl']),
                            'trade_time': trade['trade_time'].strftime('%Y-%m-%d %H:%M')
                        })

                        total_volume += float(trade['notional_usd'])

                        # 统计各币种净流入（使用格式化后的符号）
                        coin = coin_display
                        if coin not in net_flow_by_coin:
                            net_flow_by_coin[coin] = 0

                        if trade['side'] == 'LONG':
                            net_flow_by_coin[coin] += float(trade['notional_usd'])
                        else:
                            net_flow_by_coin[coin] -= float(trade['notional_usd'])

                    except Exception as trade_error:
                        logger.warning(f"处理交易记录失败: {trade_error}")
                        continue

                logger.info(f"成功获取 {len(recent_trades)} 笔交易记录，总交易量: ${total_volume:,.2f}")

                # 排序
                recent_trades.sort(
                    key=lambda x: x['notional_usd'],
                    reverse=True
                )

                # Top 币种
                top_coins = sorted(
                    net_flow_by_coin.items(),
                    key=lambda x: abs(x[1]),
                    reverse=True
                )[:5]

                result = {
                    'monitored_wallets': total_monitored,
                    'total_volume_24h': total_volume,
                    'recent_trades': recent_trades[:20],  # 最近20笔大额交易
                    'top_coins': [
                        {
                            'coin': coin,
                            'net_flow': flow,
                            'direction': 'bullish' if flow > 0 else 'bearish'
                        }
                        for coin, flow in top_coins
                    ]
                }

                logger.info(f"Hyperliquid数据获取成功: {len(result['recent_trades'])} 笔交易, {len(result['top_coins'])} 个活跃币种")
                return result

        except Exception as e:
            logger.error(f"获取Hyperliquid数据失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                'monitored_wallets': 0,
                'total_volume_24h': 0,
                'recent_trades': [],
                'top_coins': []
            }

    async def _get_system_stats(self) -> Dict:
        """
        获取系统统计

        Returns:
            统计数据
        """
        try:
            stats = {
                'total_symbols': len(self.config.get('symbols', [])),
                'news_24h': len(self.db_service.get_recent_news(hours=24, limit=1000)),
                'database_size': self._get_database_size()
            }

            return stats

        except Exception as e:
            logger.error(f"获取系统统计失败: {e}")
            return {}

    async def _get_technical_data(self, symbol: str) -> Dict:
        """获取技术指标数据"""
        try:
            # 获取足够的K线数据用于计算技术指标(至少需要100条)
            klines = self.db_service.get_latest_klines(symbol, '1h', limit=100)
            if not klines or len(klines) < 50:
                return None

            # 转换为DataFrame
            df = pd.DataFrame([{
                'timestamp': k.timestamp,
                'open': float(k.open),
                'high': float(k.high),
                'low': float(k.low),
                'close': float(k.close),
                'volume': float(k.volume)
            } for k in reversed(klines)])  # 反转，使时间从旧到新

            # 计算技术指标
            indicators = self.technical_analyzer.analyze(df)

            if not indicators:
                # 如果计算失败，返回基础数据
                latest = klines[0]
                return {
                    'price': float(latest.close),
                    'rsi': {'value': 50},
                    'macd': {'bullish_cross': False, 'bearish_cross': False, 'histogram': 0},
                    'ema': {'trend': 'neutral'},
                    'volume': {'above_average': False}
                }

            return indicators

        except Exception as e:
            logger.warning(f"获取{symbol}技术数据失败: {e}")
            return None

    async def _get_news_sentiment(self, symbol: str) -> Dict:
        """获取新闻情绪"""
        try:
            symbol_code = symbol.split('/')[0]
            news_list = self.db_service.get_recent_news(hours=24, limit=100)

            # 筛选相关新闻
            relevant_news = [
                n for n in news_list
                if n.symbols and symbol_code in n.symbols
            ]

            if not relevant_news:
                return None

            # 计算情绪指数
            positive = sum(1 for n in relevant_news if n.sentiment == 'positive')
            negative = sum(1 for n in relevant_news if n.sentiment == 'negative')
            total = len(relevant_news)

            if total == 0:
                return None

            sentiment_index = ((positive - negative) / total) * 100

            return {
                'sentiment_index': sentiment_index,
                'total_news': total,
                'positive': positive,
                'negative': negative,
                'major_events_count': sum(1 for n in relevant_news if n.sentiment_score and abs(n.sentiment_score) > 0.7)
            }

        except Exception as e:
            logger.warning(f"获取{symbol}新闻情绪失败: {e}")
            return None

    async def _get_funding_rate(self, symbol: str) -> Dict:
        """获取资金费率"""
        try:
            funding = self.db_service.get_latest_funding_rate(symbol)
            if not funding:
                return None

            return {
                'funding_rate': float(funding.funding_rate),
                'funding_rate_pct': float(funding.funding_rate) * 100,
                'exchange': funding.exchange,
                'timestamp': funding.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            }

        except Exception as e:
            logger.warning(f"获取{symbol}资金费率失败: {e}")
            return None

    async def _get_hyperliquid_for_symbol(self, symbol: str) -> Dict:
        """获取特定币种的Hyperliquid数据"""
        try:
            coin = symbol.split('/')[0]

            # 尝试获取Hyperliquid上的索引（如果币种用@N表示）
            coin_index = self.token_mapper.get_index(coin)

            with HyperliquidDB() as db:
                monitored = db.get_monitored_wallets(active_only=True)

                long_trades = 0
                short_trades = 0
                net_flow = 0
                total_pnl = 0
                active_wallets = 0

                for wallet in monitored:
                    trades = db.get_wallet_recent_trades(wallet['address'], hours=24)

                    # 匹配币种：支持直接符号匹配或@N索引匹配
                    coin_trades = [
                        t for t in trades
                        if t['coin'] == coin or t['coin'] == coin_index or
                           self.token_mapper.get_symbol(t['coin']) == coin
                    ]

                    if coin_trades:
                        active_wallets += 1

                        for trade in coin_trades:
                            if trade['side'] == 'LONG':
                                long_trades += 1
                                net_flow += float(trade['notional_usd'])
                            else:
                                short_trades += 1
                                net_flow -= float(trade['notional_usd'])

                            total_pnl += float(trade['closed_pnl'])

                if long_trades + short_trades == 0:
                    return None

                return {
                    'net_flow': net_flow,
                    'long_trades': long_trades,
                    'short_trades': short_trades,
                    'avg_pnl': total_pnl / (long_trades + short_trades),
                    'active_wallets': active_wallets
                }

        except Exception as e:
            logger.warning(f"获取{symbol} Hyperliquid数据失败: {e}")
            return None

    async def _get_ethereum_data(self, symbol: str) -> Dict:
        """获取以太坊链上数据"""
        try:
            coin = symbol.split('/')[0]
            # TODO: 从数据库获取链上交易数据
            # 暂时返回None
            return None

        except Exception as e:
            logger.warning(f"获取{symbol}链上数据失败: {e}")
            return None

    def _calculate_24h_change(self, symbol: str) -> float:
        """计算24小时涨跌幅（从数据库计算）"""
        try:
            # 从数据库计算24h涨跌
            now_kline = self.db_service.get_latest_kline(symbol, '1m')
            past_kline = self.db_service.get_kline_at_time(
                symbol,
                '5m',
                datetime.now() - timedelta(hours=24)
            )

            if now_kline and past_kline:
                change = ((float(now_kline.close) - float(past_kline.close)) /
                         float(past_kline.close)) * 100
                logger.debug(f"{symbol} 从数据库计算24h涨跌: {change}%")
                return change

            logger.warning(f"{symbol} 数据库中没有足够的历史数据计算24h涨跌")
            return 0

        except Exception as e:
            logger.warning(f"计算{symbol} 24h涨跌失败: {e}")
            return 0

    def _get_24h_volume(self, symbol: str) -> float:
        """获取24小时交易量"""
        try:
            klines = self.db_service.get_klines(
                symbol,
                '1h',
                start_time=datetime.now() - timedelta(hours=24),
                limit=24
            )
            return sum(float(k.volume) for k in klines)
        except:
            return 0

    def _get_24h_high(self, symbol: str) -> float:
        """获取24小时最高价"""
        try:
            klines = self.db_service.get_klines(
                symbol,
                '1h',
                start_time=datetime.now() - timedelta(hours=24),
                limit=24
            )
            return max(float(k.high) for k in klines) if klines else 0
        except:
            return 0

    def _get_24h_low(self, symbol: str) -> float:
        """获取24小时最低价"""
        try:
            klines = self.db_service.get_klines(
                symbol,
                '1h',
                start_time=datetime.now() - timedelta(hours=24),
                limit=24
            )
            return min(float(k.low) for k in klines) if klines else 0
        except:
            return 0

    def _get_avg_volume(self, symbol: str) -> float:
        """获取平均交易量"""
        try:
            klines = self.db_service.get_klines(symbol, '1h', limit=24)
            if not klines:
                return 0
            return sum(float(k.volume) for k in klines) / len(klines)
        except:
            return 0

    def _get_database_size(self) -> str:
        """获取数据库大小"""
        try:
            # TODO: 实现数据库大小查询
            return "N/A"
        except:
            return "N/A"

    def _calculate_signal_stats(self, recommendations: List[Dict]) -> Dict:
        """统计信号分布"""
        bullish_count = sum(
            1 for r in recommendations
            if r['signal'] in ['BUY', 'STRONG_BUY']
        )

        bearish_count = sum(
            1 for r in recommendations
            if r['signal'] in ['SELL', 'STRONG_SELL']
        )

        hold_count = sum(
            1 for r in recommendations
            if r['signal'] == 'HOLD'
        )

        return {
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'hold_count': hold_count
        }

    async def _get_futures_data(self, symbols: List[str]) -> List[Dict]:
        """
        获取合约数据（持仓量、多空比、资金费率）

        Args:
            symbols: 币种列表

        Returns:
            合约数据列表
        """
        futures_data = []

        for symbol in symbols:
            try:
                # 获取合约数据
                data = self.db_service.get_latest_futures_data(symbol)

                if data and data.get('open_interest') is not None:
                    # 获取最新资金费率
                    funding_rate = self.db_service.get_latest_funding_rate(symbol)

                    futures_data.append({
                        'symbol': symbol.replace('/USDT', ''),
                        'full_symbol': symbol,
                        'open_interest': float(data.get('open_interest', 0)),
                        'long_short_ratio': data.get('long_short_ratio', {}).get('ratio', 0) if data.get('long_short_ratio') else 0,
                        'long_account': data.get('long_short_ratio', {}).get('long_account', 0) if data.get('long_short_ratio') else 0,
                        'short_account': data.get('long_short_ratio', {}).get('short_account', 0) if data.get('long_short_ratio') else 0,
                        'funding_rate': float(funding_rate.funding_rate) if funding_rate else 0,
                        'funding_rate_pct': float(funding_rate.funding_rate) * 100 if funding_rate else 0,
                        'timestamp': data.get('timestamp').strftime('%Y-%m-%d %H:%M:%S') if data.get('timestamp') else None
                    })
            except Exception as e:
                logger.warning(f"获取{symbol}合约数据失败: {e}")
                continue

        return futures_data
