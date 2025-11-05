"""
增强版Dashboard API - 优化版本
使用缓存表，大幅提升性能
"""

import asyncio
import logging
from typing import Dict, List
from datetime import datetime
from sqlalchemy import text

from app.database.db_service import DatabaseService

logger = logging.getLogger(__name__)


class EnhancedDashboardCached:
    """增强版仪表盘数据服务（使用缓存）"""

    def __init__(self, config: dict):
        """
        初始化

        Args:
            config: 系统配置
        """
        self.config = config
        self.db_service = DatabaseService(config.get('database', {}))

    async def get_dashboard_data(self, symbols: List[str] = None) -> Dict:
        """
        获取完整的仪表盘数据（从缓存表读取，性能极高）

        Args:
            symbols: 币种列表,如 ['BTC/USDT', 'ETH/USDT']

        Returns:
            仪表盘数据字典
        """
        if symbols is None:
            symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # logger.info(f"📊 从缓存获取Dashboard数据 - {len(symbols)} 个币种")  # 减少日志输出
        start_time = datetime.now()

        # 并行读取缓存表
        tasks = [
            self._get_prices_from_cache(symbols),
            self._get_recommendations_from_cache(symbols),
            self._get_news_from_db(limit=20),
            self._get_hyperliquid_from_cache(),
            self._get_system_stats(),
            self._get_futures_from_cache(symbols),  # 合约数据
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

        elapsed = (datetime.now() - start_time).total_seconds()
        # logger.info(f"✅ Dashboard数据获取完成，耗时: {elapsed:.3f}秒（从缓存）")  # 减少日志输出

        return {
            'success': True,
            'data': {
                'prices': prices,
                'recommendations': recommendations,
                'news': news,
                'hyperliquid': hyperliquid,
                'futures': futures,  # 合约数据
                'stats': {
                    **stats,
                    **signal_stats
                },
                'last_updated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'from_cache': True  # 标记数据来源于缓存
            }
        }

    async def _get_prices_from_cache(self, symbols: List[str]) -> List[Dict]:
        """
        从价格统计缓存表读取价格数据（超快）

        Returns:
            价格列表
        """
        prices = []
        session = None

        try:
            session = self.db_service.get_session()

            # 一次性查询所有币种的价格统计
            placeholders = ','.join([f':symbol{i}' for i in range(len(symbols))])
            sql = text(f"""
                SELECT
                    symbol,
                    current_price,
                    change_24h,
                    high_24h,
                    low_24h,
                    volume_24h,
                    quote_volume_24h,
                    trend,
                    updated_at
                FROM price_stats_24h
                WHERE symbol IN ({placeholders})
                ORDER BY change_24h DESC
            """)

            # 创建参数字典
            params = {f'symbol{i}': sym for i, sym in enumerate(symbols)}
            results = session.execute(sql, params).fetchall()

            for row in results:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)

                price_data = {
                    'symbol': row_dict['symbol'].replace('/USDT', ''),
                    'full_symbol': row_dict['symbol'],
                    'price': float(row_dict['current_price']),
                    'change_24h': float(row_dict['change_24h']) if row_dict['change_24h'] else 0,
                    'volume_24h': float(row_dict['volume_24h']) if row_dict['volume_24h'] else 0,
                    'quote_volume_24h': float(row_dict['quote_volume_24h']) if row_dict['quote_volume_24h'] else 0,
                    'high_24h': float(row_dict['high_24h']) if row_dict['high_24h'] else 0,
                    'low_24h': float(row_dict['low_24h']) if row_dict['low_24h'] else 0,
                    'trend': row_dict['trend'],
                    'timestamp': row_dict['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if row_dict['updated_at'] else ''
                }

                # 调试日志已移除 - 数据已验证正确
                # if row_dict['symbol'] == 'BTC/USDT':
                #     logger.info(f"🔍 BTC数据构建: volume_24h={price_data['volume_24h']}, quote_volume_24h={price_data['quote_volume_24h']}")

                prices.append(price_data)

            # logger.debug(f"✅ 从缓存读取 {len(prices)} 个币种价格")  # 减少日志输出

        except Exception as e:
            logger.error(f"从缓存读取价格失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if session:
                session.close()

        return prices

    async def _get_recommendations_from_cache(self, symbols: List[str]) -> List[Dict]:
        """
        从投资建议缓存表读取推荐数据（超快）

        Returns:
            建议列表
        """
        recommendations = []
        session = None

        try:
            session = self.db_service.get_session()

            # 一次性查询所有币种的投资建议
            placeholders = ','.join([f':symbol{i}' for i in range(len(symbols))])
            sql = text(f"""
                SELECT
                    symbol,
                    total_score,
                    technical_score,
                    news_score,
                    funding_score,
                    hyperliquid_score,
                    ethereum_score,
                    `signal`,
                    confidence,
                    current_price,
                    entry_price,
                    stop_loss,
                    take_profit,
                    risk_level,
                    risk_factors,
                    reasons,
                    has_technical,
                    has_news,
                    has_funding,
                    has_hyperliquid,
                    has_ethereum,
                    data_completeness,
                    updated_at
                FROM investment_recommendations_cache
                WHERE symbol IN ({placeholders})
                ORDER BY confidence DESC
            """)

            params = {f'symbol{i}': sym for i, sym in enumerate(symbols)}
            results = session.execute(sql, params).fetchall()

            # 同时获取资金费率信息
            funding_rates = await self._get_funding_rates_batch(symbols)

            for row in results:
                import json

                # Convert Row to dict
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                symbol = row_dict['symbol']

                recommendations.append({
                    'symbol': symbol.replace('/USDT', ''),
                    'full_symbol': symbol,
                    'signal': row_dict['signal'],
                    'confidence': float(row_dict['confidence']) if row_dict['confidence'] else 0,
                    'current_price': float(row_dict['current_price']) if row_dict['current_price'] else 0,
                    'entry_price': float(row_dict['entry_price']) if row_dict['entry_price'] else 0,
                    'stop_loss': float(row_dict['stop_loss']) if row_dict['stop_loss'] else 0,
                    'take_profit': float(row_dict['take_profit']) if row_dict['take_profit'] else 0,
                    'reasons': json.loads(row_dict['reasons']) if row_dict['reasons'] else [],
                    'risk_level': row_dict['risk_level'] or 'MEDIUM',
                    'risk_factors': json.loads(row_dict['risk_factors']) if row_dict['risk_factors'] else [],
                    'scores': {
                        'total': float(row_dict['total_score']) if row_dict['total_score'] else 50,
                        'technical': float(row_dict['technical_score']) if row_dict['technical_score'] else 50,
                        'news': float(row_dict['news_score']) if row_dict['news_score'] else 50,
                        'funding': float(row_dict['funding_score']) if row_dict['funding_score'] else 50,
                        'hyperliquid': float(row_dict['hyperliquid_score']) if row_dict['hyperliquid_score'] else 50,
                        'ethereum': float(row_dict['ethereum_score']) if row_dict['ethereum_score'] else 50,
                    },
                    'data_sources': {
                        'technical': bool(row_dict['has_technical']),
                        'news': bool(row_dict['has_news']),
                        'funding': bool(row_dict['has_funding']),
                        'hyperliquid': bool(row_dict['has_hyperliquid']),
                        'ethereum': bool(row_dict['has_ethereum']),
                    },
                    'data_completeness': float(row_dict['data_completeness']) if row_dict['data_completeness'] else 0,
                    'funding_rate': funding_rates.get(symbol)
                })

            logger.debug(f"✅ 从缓存读取 {len(recommendations)} 个投资建议")

        except Exception as e:
            logger.error(f"从缓存读取投资建议失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if session:
                session.close()

        return recommendations

    async def _get_funding_rates_batch(self, symbols: List[str]) -> Dict:
        """批量获取资金费率"""
        funding_rates = {}
        session = None

        try:
            session = self.db_service.get_session()

            placeholders = ','.join([f':symbol{i}' for i in range(len(symbols))])
            sql = text(f"""
                SELECT symbol, current_rate, current_rate_pct, trend, market_sentiment
                FROM funding_rate_stats
                WHERE symbol IN ({placeholders})
            """)

            params = {f'symbol{i}': sym for i, sym in enumerate(symbols)}
            results = session.execute(sql, params).fetchall()

            for row in results:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                funding_rates[row_dict['symbol']] = {
                    'funding_rate': float(row_dict['current_rate']),
                    'funding_rate_pct': float(row_dict['current_rate_pct']),
                    'trend': row_dict['trend'],
                    'market_sentiment': row_dict['market_sentiment']
                }

        except Exception as e:
            logger.warning(f"批量获取资金费率失败: {e}")
        finally:
            if session:
                session.close()

        return funding_rates

    async def _get_news_from_db(self, limit: int = 20) -> List[Dict]:
        """
        获取最新新闻（直接从数据库读取，新闻更新频率低）

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

            logger.debug(f"✅ 读取 {len(result)} 条新闻")
            return result

        except Exception as e:
            logger.error(f"获取新闻失败: {e}")
            return []

    async def _get_hyperliquid_from_cache(self) -> Dict:
        """
        从Hyperliquid聚合表读取数据（超快）

        Returns:
            聪明钱数据
        """
        session = None
        try:
            session = self.db_service.get_session()

            # 获取活跃监控钱包总数
            result = session.execute(text("""
                SELECT COUNT(*) as count
                FROM hyperliquid_monitored_wallets
                WHERE is_monitoring = 1
            """))
            row = result.fetchone()
            monitored_count = row[0] if row else 0

            # 获取Top币种（按净流入排序）
            result = session.execute(text("""
                SELECT
                    symbol as coin,
                    net_flow,
                    total_volume,
                    long_trades,
                    short_trades,
                    active_wallets,
                    hyperliquid_signal as direction
                FROM hyperliquid_symbol_aggregation
                WHERE period = '24h'
                ORDER BY ABS(net_flow) DESC
                LIMIT 20
            """))
            top_coins_data = result.fetchall()

            # 获取最近大额交易（从原表，因为需要详细信息）
            # 使用 GROUP BY 去重，避免显示重复的交易
            from datetime import timedelta
            cutoff_time = datetime.now() - timedelta(hours=24)
            result = session.execute(text("""
                SELECT
                    MAX(t.id) as id,
                    t.address,
                    t.coin,
                    t.side,
                    t.price,
                    t.notional_usd,
                    t.closed_pnl,
                    t.trade_time,
                    w.label as wallet_label
                FROM hyperliquid_wallet_trades t
                LEFT JOIN hyperliquid_monitored_wallets w ON t.address = w.address
                WHERE t.trade_time >= :cutoff_time
                    AND w.is_monitoring = 1
                GROUP BY t.address, t.coin, t.side, t.trade_time, ROUND(t.notional_usd, 2)
                ORDER BY t.notional_usd DESC
                LIMIT 50
            """), {"cutoff_time": cutoff_time})

            trades_data = result.fetchall()

            # 格式化数据
            recent_trades = []
            from app.services.hyperliquid_token_mapper import get_token_mapper
            token_mapper = get_token_mapper()

            for trade in trades_data:
                trade_dict = dict(trade._mapping) if hasattr(trade, '_mapping') else dict(trade)
                coin_display = token_mapper.format_symbol(trade_dict['coin'])
                wallet_label = trade_dict.get('wallet_label', 'Unknown')
                if not wallet_label or wallet_label == 'None':
                    wallet_label = trade_dict.get('address', 'Unknown')[:10] + '...'

                recent_trades.append({
                    'wallet_label': wallet_label,
                    'coin': coin_display,
                    'coin_raw': trade_dict['coin'],
                    'side': trade_dict['side'],
                    'notional_usd': float(trade_dict['notional_usd']),
                    'price': float(trade_dict['price']),
                    'closed_pnl': float(trade_dict['closed_pnl']),
                    'trade_time': trade_dict['trade_time'].strftime('%Y-%m-%d %H:%M')
                })

            # 计算总交易量
            top_coins_list = [dict(row._mapping) if hasattr(row, '_mapping') else dict(row) for row in top_coins_data]
            total_volume = sum(float(row['total_volume']) for row in top_coins_list)

            # 格式化Top币种
            top_coins = []
            for row in top_coins_list:
                top_coins.append({
                    'coin': row['coin'],
                    'net_flow': float(row['net_flow']),
                    'direction': 'bullish' if float(row['net_flow']) > 0 else 'bearish'
                })

            result = {
                'monitored_wallets': monitored_count,
                'total_volume_24h': total_volume,
                'recent_trades': recent_trades[:50],
                'top_coins': top_coins
            }

            logger.debug(f"✅ 从缓存读取Hyperliquid数据")
            return result

        except Exception as e:
            logger.error(f"从缓存读取Hyperliquid数据失败: {e}")
            import traceback
            traceback.print_exc()
            return {
                'monitored_wallets': 0,
                'total_volume_24h': 0,
                'recent_trades': [],
                'top_coins': []
            }
        finally:
            if session:
                session.close()

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
            }

            return stats

        except Exception as e:
            logger.error(f"获取系统统计失败: {e}")
            return {}

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

    async def _get_futures_from_cache(self, symbols: List[str]) -> List[Dict]:
        """
        从资金费率缓存表读取合约数据（持仓量、多空比、资金费率）

        Returns:
            合约数据列表
        """
        futures_data = []
        session = None

        try:
            session = self.db_service.get_session()

            # 从 funding_rate_stats 缓存表批量读取
            placeholders = ','.join([f':symbol{i}' for i in range(len(symbols))])
            sql = text(f"""
                SELECT
                    symbol,
                    current_rate,
                    current_rate_pct,
                    trend,
                    market_sentiment
                FROM funding_rate_stats
                WHERE symbol IN ({placeholders})
            """)

            params = {f'symbol{i}': sym for i, sym in enumerate(symbols)}
            results = session.execute(sql, params).fetchall()

            for row in results:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                symbol = row_dict['symbol']

                futures_data.append({
                    'symbol': symbol.replace('/USDT', ''),
                    'full_symbol': symbol,
                    'open_interest': 0,  # 缓存表中没有持仓量，设为0
                    'long_short_ratio': 0,  # 缓存表中没有多空比，设为0
                    'funding_rate': float(row_dict['current_rate']) if row_dict.get('current_rate') else 0,
                    'funding_rate_pct': float(row_dict['current_rate_pct']) if row_dict.get('current_rate_pct') else 0,
                    'trend': row_dict.get('trend', 'neutral'),
                    'market_sentiment': row_dict.get('market_sentiment', 'normal')
                })

            logger.debug(f"✅ 从缓存读取 {len(futures_data)} 个资金费率数据")

        except Exception as e:
            logger.error(f"从缓存读取资金费率数据失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if session:
                session.close()

        # 补充持仓量和多空比数据（从原始表读取）
        for item in futures_data:
            try:
                symbol = item['full_symbol']
                data = self.db_service.get_latest_futures_data(symbol)

                if data:
                    item['open_interest'] = float(data.get('open_interest', 0)) if data.get('open_interest') else 0
                    if data.get('long_short_ratio'):
                        item['long_short_ratio'] = data['long_short_ratio'].get('ratio', 0)
                        item['long_account'] = data['long_short_ratio'].get('long_account', 0)
                        item['short_account'] = data['long_short_ratio'].get('short_account', 0)
                    else:
                        item['long_short_ratio'] = 0
            except Exception as e:
                logger.warning(f"获取{symbol}持仓量和多空比失败: {e}")
                continue

        logger.debug(f"✅ 完整合约数据获取完成: {len(futures_data)} 个币种（含持仓量和多空比）")
        return futures_data



# 导入timedelta
from datetime import timedelta
