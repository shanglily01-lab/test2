"""
增强版Dashboard API - 优化版本
使用缓存表，大幅提升性能
"""

import asyncio
import logging
from typing import Dict, List
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError as SQLTimeoutError
import pymysql

from app.database.db_service import DatabaseService

logger = logging.getLogger(__name__)


class EnhancedDashboardCached:
    """增强版仪表盘数据服务（使用缓存）"""

    def __init__(self, config: dict, price_collector=None):
        """
        初始化

        Args:
            config: 系统配置
            price_collector: 价格采集器（可选，用于实时价格获取）
        """
        self.config = config
        self.db_service = DatabaseService(config.get('database', {}))
        self.price_collector = price_collector

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
        start_time = datetime.utcnow()

        # 并行读取缓存表
        tasks = [
            self._get_prices_from_cache(symbols),
            self._get_recommendations_from_cache(symbols),
            self._get_news_from_db(limit=20),
            self._get_hyperliquid_from_cache(),
            self._get_system_stats(),
            self._get_futures_from_cache(symbols),  # 合约数据
        ]

        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Dashboard数据获取异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 如果gather失败，返回空数据
            results = [[], [], [], {}, {}, []]

        prices, recommendations, news, hyperliquid, stats, futures = results

        # 处理异常
        if isinstance(prices, Exception):
            logger.error(f"获取价格失败: {prices}")
            import traceback
            logger.error(traceback.format_exc())
            prices = []
        if isinstance(recommendations, Exception):
            logger.error(f"获取建议失败: {recommendations}")
            import traceback
            logger.error(traceback.format_exc())
            recommendations = []
        if isinstance(news, Exception):
            logger.error(f"获取新闻失败: {news}")
            import traceback
            logger.error(traceback.format_exc())
            news = []
        if isinstance(hyperliquid, Exception):
            logger.error(f"获取Hyperliquid数据失败: {hyperliquid}")
            import traceback
            logger.error(traceback.format_exc())
            hyperliquid = {}
        if isinstance(stats, Exception):
            logger.error(f"获取统计失败: {stats}")
            import traceback
            logger.error(traceback.format_exc())
            stats = {}
        if isinstance(futures, Exception):
            logger.error(f"获取合约数据失败: {futures}")
            import traceback
            logger.error(traceback.format_exc())
            futures = []

        # 将 prices 合并到 futures（避免在 _get_futures_from_cache 内部重复查价格）
        if isinstance(prices, list) and isinstance(futures, list) and prices:
            prices_map = {p.get('full_symbol', ''): p for p in prices}
            for item in futures:
                sym = item.get('full_symbol', '')
                if sym in prices_map:
                    pi = prices_map[sym]
                    item['price']           = pi.get('price', 0)
                    item['current_price']   = pi.get('price', 0)
                    item['change_24h']      = pi.get('change_24h', 0)
                    item['price_change_24h']= pi.get('change_24h', 0)
                    item['volume_24h']      = pi.get('volume_24h', 0)

        # news_24h 直接从已获取的新闻列表计算，无需额外 DB 查询
        if isinstance(stats, dict):
            stats['news_24h'] = len(news) if isinstance(news, list) else 0

        # 统计信号
        signal_stats = self._calculate_signal_stats(recommendations)

        elapsed = (datetime.utcnow() - start_time).total_seconds()
        # logger.info(f"✅ Dashboard数据获取完成，耗时: {elapsed:.3f}秒（从缓存）")  # 减少日志输出

        # 确保所有数据都是可序列化的
        try:
            # 确保stats是字典
            if not isinstance(stats, dict):
                stats = {}
            if not isinstance(signal_stats, dict):
                signal_stats = {}
            
            return {
                'success': True,
                'data': {
                    'prices': prices or [],
                    'recommendations': recommendations or [],
                    'news': news or [],
                    'hyperliquid': hyperliquid or {},
                    'futures': futures or [],  # 合约数据
                    'stats': {
                        **stats,
                        **signal_stats
                    },
                    'last_updated': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                    'from_cache': True  # 标记数据来源于缓存
                }
            }
        except Exception as e:
            logger.error(f"构建Dashboard响应失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # 返回最小有效响应
            return {
                'success': False,
                'data': {
                    'prices': [],
                    'recommendations': [],
                    'news': [],
                    'hyperliquid': {},
                    'futures': [],
                    'stats': {},
                    'last_updated': datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'),
                },
                'error': str(e)
            }

    async def _get_prices_from_cache(self, symbols: List[str]) -> List[Dict]:
        """
        从价格统计缓存表读取价格数据（超快）
        如果缓存数据超过30秒，则从实时价格源获取最新价格

        Returns:
            价格列表
        """
        from datetime import datetime, timedelta
        
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

            # 检查哪些价格需要实时更新（超过30秒）
            now = datetime.utcnow()
            symbols_need_realtime = []
            
            for row in results:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                updated_at = row_dict.get('updated_at')
                
                if updated_at:
                    # 如果updated_at是datetime对象，直接比较
                    if isinstance(updated_at, datetime):
                        age_seconds = (now - updated_at).total_seconds()
                    else:
                        # 如果是字符串，转换为datetime
                        if isinstance(updated_at, str):
                            updated_at = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
                        age_seconds = (now - updated_at).total_seconds()
                    
                    # 如果缓存超过90秒，标记为需要实时更新（price_stats_24h每60s更新一次）
                    if age_seconds > 90:
                        symbols_need_realtime.append(row_dict['symbol'])

            # 从实时价格源获取需要更新的价格
            realtime_prices = {}
            if symbols_need_realtime:
                # 优先使用 price_collector，如果没有则从数据库获取最新K线价格
                if hasattr(self, 'price_collector') and self.price_collector:
                    try:
                        async def _fetch_one(sym):
                            try:
                                info = await self.price_collector.fetch_best_price(sym)
                                return sym, float(info.get('price', 0)) if info else None
                            except Exception as e:
                                logger.warning(f"获取 {sym} 实时价格失败: {e}")
                                return sym, None
                        fetch_results = await asyncio.gather(*[_fetch_one(s) for s in symbols_need_realtime])
                        for sym, price in fetch_results:
                            if price:
                                realtime_prices[sym] = price
                    except Exception as e:
                        logger.warning(f"批量获取实时价格失败: {e}")
                else:
                    # 从数据库获取最新1分钟K线价格作为实时价格
                    try:
                        for symbol in symbols_need_realtime:
                            latest_kline = self.db_service.get_latest_kline(symbol, '1m')
                            if latest_kline:
                                realtime_prices[symbol] = float(latest_kline.close_price)
                                logger.debug(f"🔄 从数据库实时更新 {symbol} 价格: {realtime_prices[symbol]}")
                    except Exception as e:
                        logger.warning(f"从数据库获取实时价格失败: {e}")

            for row in results:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                symbol = row_dict['symbol']
                
                # 如果从实时源获取到了新价格，使用实时价格
                current_price = float(row_dict['current_price'])
                if symbol in realtime_prices:
                    current_price = realtime_prices[symbol]

                price_data = {
                    'symbol': symbol.replace('/USDT', ''),
                    'full_symbol': symbol,
                    'price': current_price,
                    'change_24h': float(row_dict['change_24h']) if row_dict['change_24h'] else 0,
                    'volume_24h': float(row_dict['volume_24h']) if row_dict['volume_24h'] else 0,
                    'quote_volume_24h': float(row_dict['quote_volume_24h']) if row_dict['quote_volume_24h'] else 0,
                    'high_24h': float(row_dict['high_24h']) if row_dict['high_24h'] else 0,
                    'low_24h': float(row_dict['low_24h']) if row_dict['low_24h'] else 0,
                    'trend': row_dict['trend'],
                    'timestamp': row_dict['updated_at'].strftime('%Y-%m-%d %H:%M:%S') if row_dict['updated_at'] else ''
                }

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
        确保所有配置的交易对都返回，即使没有缓存数据也返回默认值

        Returns:
            建议列表
        """
        recommendations = []
        session = None
        cached_symbols = set()  # 记录已从缓存获取的交易对

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
                cached_symbols.add(symbol)

                # 格式化信号生成时间
                signal_time = ''
                if row_dict.get('updated_at'):
                    if isinstance(row_dict['updated_at'], datetime):
                        signal_time = row_dict['updated_at'].strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        signal_time = str(row_dict['updated_at'])
                
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
                        'etf': float(row_dict['etf_score']) if row_dict.get('etf_score') else 50,
                    },
                    'data_sources': {
                        'technical': bool(row_dict['has_technical']),
                        'news': bool(row_dict['has_news']),
                        'funding': bool(row_dict['has_funding']),
                        'hyperliquid': bool(row_dict['has_hyperliquid']),
                        'ethereum': bool(row_dict['has_ethereum']),
                        'etf': bool(row_dict.get('has_etf')),
                    },
                    'data_completeness': float(row_dict['data_completeness']) if row_dict['data_completeness'] else 0,
                    'funding_rate': funding_rates.get(symbol),
                    'signal_time': signal_time  # 信号生成时间
                })

            # 为没有缓存数据的交易对创建默认建议
            for symbol in symbols:
                if symbol not in cached_symbols:
                    recommendations.append({
                        'symbol': symbol.replace('/USDT', ''),
                        'full_symbol': symbol,
                        'signal': '持有',
                        'confidence': 0,
                        'current_price': 0,
                        'entry_price': 0,
                        'stop_loss': 0,
                        'take_profit': 0,
                        'reasons': ['数据不足，无法生成投资建议'],
                        'risk_level': 'UNKNOWN',
                        'risk_factors': ['缺少价格数据'],
                        'scores': {
                            'total': 50,
                            'technical': 50,
                            'news': 50,
                            'funding': 50,
                            'hyperliquid': 50,
                            'ethereum': 50,
                            'etf': 50,
                        },
                        'data_sources': {
                            'technical': False,
                            'news': False,
                            'funding': False,
                            'hyperliquid': False,
                            'ethereum': False,
                            'etf': False,
                        },
                        'data_completeness': 0,
                        'funding_rate': None
                    })

            logger.debug(f"✅ 从缓存读取 {len([r for r in recommendations if r['current_price'] > 0])} 个有效投资建议，{len([r for r in recommendations if r['current_price'] == 0])} 个数据不足的交易对")

        except Exception as e:
            logger.error(f"从缓存读取投资建议失败: {e}")
            import traceback
            traceback.print_exc()
            # 如果查询失败，至少返回所有交易对的默认值
            for symbol in symbols:
                recommendations.append({
                    'symbol': symbol.replace('/USDT', ''),
                    'full_symbol': symbol,
                    'signal': '持有',
                    'confidence': 0,
                    'current_price': 0,
                    'entry_price': 0,
                    'stop_loss': 0,
                    'take_profit': 0,
                    'reasons': ['数据获取失败'],
                    'risk_level': 'UNKNOWN',
                    'risk_factors': [],
                    'scores': {'total': 50, 'technical': 50, 'news': 50, 'funding': 50, 'hyperliquid': 50, 'ethereum': 50, 'etf': 50},
                    'data_sources': {'technical': False, 'news': False, 'funding': False, 'hyperliquid': False, 'ethereum': False, 'etf': False},
                    'data_completeness': 0,
                    'funding_rate': None
                })
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
        从预计算缓存表读取聪明钱数据（存储过程版，极速）
        dashboard_hl_summary + dashboard_hl_recent_trades 由存储过程每5分钟刷新
        """
        _empty = {'monitored_wallets': 0, 'active_wallets': 0, 'total_volume_24h': 0,
                  'recent_trades': [], 'top_coins': []}
        session = None
        try:
            session = self.db_service.get_session()

            # 读汇总（单行）
            summary_row = session.execute(text(
                "SELECT monitored_count, active_wallets_24h, total_volume_24h FROM dashboard_hl_summary WHERE id = 1"
            )).fetchone()

            monitored_count      = int(summary_row[0]) if summary_row else 0
            active_wallets_count = int(summary_row[1]) if summary_row else 0
            total_volume_24h     = float(summary_row[2]) if summary_row else 0.0

            # 读 Top 币种（已有聚合表，快）
            top_coins_rows = session.execute(text("""
                SELECT symbol AS coin, net_flow, total_volume
                FROM hyperliquid_symbol_aggregation
                WHERE period = '24h'
                ORDER BY ABS(net_flow) DESC
                LIMIT 20
            """)).fetchall()

            # 读近50条交易快照
            trades_rows = session.execute(text("""
                SELECT address, wallet_label, coin, side, price, size,
                       notional_usd, closed_pnl, leverage, trade_time
                FROM dashboard_hl_recent_trades
                ORDER BY trade_time DESC
            """)).fetchall()

            from app.services.hyperliquid_token_mapper import get_token_mapper
            token_mapper = get_token_mapper()

            recent_trades = []
            for t in trades_rows:
                td = dict(t._mapping) if hasattr(t, '_mapping') else dict(t)
                wallet_label = td.get('wallet_label') or ''
                if not wallet_label or wallet_label == 'None':
                    wallet_label = (td.get('address') or 'Unknown')[:10] + '...'
                recent_trades.append({
                    'wallet_label': wallet_label,
                    'coin':         token_mapper.format_symbol(td['coin']),
                    'coin_raw':     td['coin'],
                    'side':         td['side'],
                    'size':         float(td.get('size', 0)),
                    'leverage':     float(td.get('leverage', 1)),
                    'notional_usd': float(td.get('notional_usd', 0)),
                    'price':        float(td.get('price', 0)),
                    'closed_pnl':   float(td.get('closed_pnl', 0)),
                    'trade_time':   td['trade_time'].strftime('%Y-%m-%d %H:%M') if td.get('trade_time') else '',
                })

            top_coins = []
            for r in top_coins_rows:
                rd = dict(r._mapping) if hasattr(r, '_mapping') else dict(r)
                net_flow = float(rd.get('net_flow', 0))
                top_coins.append({
                    'coin':      rd['coin'],
                    'net_flow':  net_flow,
                    'direction': 'bullish' if net_flow > 0 else 'bearish',
                })

            logger.debug(f"✅ Hyperliquid缓存读取完成（{len(recent_trades)} 条交易）")
            return {
                'monitored_wallets': monitored_count,
                'active_wallets':    active_wallets_count,
                'total_volume_24h':  total_volume_24h,
                'recent_trades':     recent_trades,
                'top_coins':         top_coins,
            }

        except Exception as e:
            logger.error(f"从缓存读取Hyperliquid数据失败: {e}")
            return _empty
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass

    async def _get_system_stats(self) -> Dict:
        """
        获取系统统计（无 DB 查询，news_24h 由 get_dashboard_data 从已获取的新闻列表填充）
        """
        return {
            'total_symbols': len(self.config.get('symbols', [])),
            'news_24h': 0,  # 由 get_dashboard_data 在 gather 完成后用 len(news) 填充
        }

    def _calculate_signal_stats(self, recommendations: List[Dict]) -> Dict:
        """统计信号分布"""
        strong_buy_count = sum(
            1 for r in recommendations
            if r['signal'] == 'STRONG_BUY'
        )
        
        buy_count = sum(
            1 for r in recommendations
            if r['signal'] == 'BUY'
        )
        
        strong_sell_count = sum(
            1 for r in recommendations
            if r['signal'] == 'STRONG_SELL'
        )
        
        sell_count = sum(
            1 for r in recommendations
            if r['signal'] == 'SELL'
        )

        bullish_count = strong_buy_count + buy_count
        bearish_count = strong_sell_count + sell_count

        hold_count = sum(
            1 for r in recommendations
            if r['signal'] == 'HOLD'
        )

        return {
            'strong_buy_count': strong_buy_count,
            'buy_count': buy_count,
            'strong_sell_count': strong_sell_count,
            'sell_count': sell_count,
            'bullish_count': bullish_count,
            'bearish_count': bearish_count,
            'hold_count': hold_count,
            'total_count': len(recommendations)
        }

    async def _get_futures_from_cache(self, symbols: List[str]) -> List[Dict]:
        """
        从资金费率缓存表读取合约数据，并用 2 条批量 SQL 补充持仓量和多空比
        （原来是 N×2 条循环查询，现在是固定 3 条查询）
        价格数据由 get_dashboard_data 在 gather 完成后统一合并，不在此重复查询。
        """
        futures_data = []
        session = None

        try:
            session = self.db_service.get_session()

            # ── Step 1: funding_rate_stats 缓存表（极快）──────────────────────
            placeholders = ','.join([f':symbol{i}' for i in range(len(symbols))])
            params = {f'symbol{i}': sym for i, sym in enumerate(symbols)}
            results = session.execute(text(f"""
                SELECT symbol, current_rate, current_rate_pct, trend, market_sentiment
                FROM funding_rate_stats
                WHERE symbol IN ({placeholders})
            """), params).fetchall()

            for row in results:
                row_dict = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                symbol = row_dict['symbol']
                futures_data.append({
                    'symbol': symbol.replace('/USDT', ''),
                    'full_symbol': symbol,
                    'open_interest': 0,
                    'long_short_ratio': 0,
                    'funding_rate': float(row_dict['current_rate']) if row_dict.get('current_rate') else 0,
                    'funding_rate_pct': float(row_dict['current_rate_pct']) if row_dict.get('current_rate_pct') else 0,
                    'trend': row_dict.get('trend', 'neutral'),
                    'market_sentiment': row_dict.get('market_sentiment', 'normal')
                })

            if not futures_data:
                return futures_data

            # ── Step 2: 批量查持仓量 + 多空比（2 条 SQL 替代 N×2 循环）──────────
            # 同时包含 BTC/USDT 和 BTCUSDT 两种格式，兼容不同数据源写入方式
            all_syms = []
            sym_map = {}   # 'BTCUSDT' -> 'BTC/USDT'
            for sym in symbols:
                all_syms.append(sym)
                sym_map[sym] = sym
                no_slash = sym.replace('/', '')
                all_syms.append(no_slash)
                sym_map[no_slash] = sym

            ph2 = ','.join([f':s{i}' for i in range(len(all_syms))])
            p2  = {f's{i}': s for i, s in enumerate(all_syms)}

            # 持仓量：每个 symbol 取最新一行
            oi_rows = session.execute(text(f"""
                SELECT f.symbol, f.open_interest
                FROM futures_open_interest f
                JOIN (
                    SELECT symbol, MAX(timestamp) AS max_ts
                    FROM futures_open_interest
                    WHERE symbol IN ({ph2}) AND exchange = 'binance_futures'
                    GROUP BY symbol
                ) t ON f.symbol = t.symbol AND f.timestamp = t.max_ts
                WHERE f.exchange = 'binance_futures'
            """), p2).fetchall()

            oi_map = {}
            for row in oi_rows:
                rd = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                orig = sym_map.get(rd['symbol'])
                if orig:
                    oi_map[orig] = float(rd['open_interest']) if rd.get('open_interest') else 0

            # 多空比：每个 symbol 取最新一行
            lsr_rows = session.execute(text(f"""
                SELECT f.symbol, f.long_account, f.short_account, f.long_short_ratio,
                       f.long_position, f.short_position, f.long_short_position_ratio
                FROM futures_long_short_ratio f
                JOIN (
                    SELECT symbol, MAX(timestamp) AS max_ts
                    FROM futures_long_short_ratio
                    WHERE symbol IN ({ph2})
                    GROUP BY symbol
                ) t ON f.symbol = t.symbol AND f.timestamp = t.max_ts
            """), p2).fetchall()

            lsr_map = {}
            for row in lsr_rows:
                rd = dict(row._mapping) if hasattr(row, '_mapping') else dict(row)
                orig = sym_map.get(rd['symbol'])
                if orig:
                    lsr_map[orig] = rd

            # ── Step 3: 将批量结果合并到 futures_data ─────────────────────────
            for item in futures_data:
                sym = item['full_symbol']
                item['open_interest'] = oi_map.get(sym, 0)
                if sym in lsr_map:
                    rd = lsr_map[sym]
                    item['long_short_account_ratio'] = float(rd.get('long_short_ratio') or 0)
                    item['long_account']  = float(rd.get('long_account')  or 0)
                    item['short_account'] = float(rd.get('short_account') or 0)
                    if rd.get('long_short_position_ratio'):
                        item['long_short_position_ratio'] = float(rd['long_short_position_ratio'])
                        item['long_position']  = float(rd.get('long_position')  or 0)
                        item['short_position'] = float(rd.get('short_position') or 0)
                    else:
                        item['long_short_position_ratio'] = 0
                else:
                    item['long_short_account_ratio']  = 0
                    item['long_short_position_ratio'] = 0

            logger.debug(f"✅ 合约数据批量读取完成: {len(futures_data)} 个币种")

        except Exception as e:
            logger.error(f"从缓存读取合约数据失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if session:
                session.close()

        return futures_data



# 导入timedelta
from datetime import timedelta
