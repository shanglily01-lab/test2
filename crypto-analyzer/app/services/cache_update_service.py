"""
缓存更新服务
用于定期更新各个缓存表，提升API性能
"""

import asyncio
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from loguru import logger
import pandas as pd
from sqlalchemy import text

from app.database.db_service import DatabaseService
from app.database.hyperliquid_db import HyperliquidDB
from app.analyzers.technical_indicators import TechnicalIndicators
from app.analyzers.enhanced_investment_analyzer import EnhancedInvestmentAnalyzer
from app.services.hyperliquid_token_mapper import get_token_mapper


class CacheUpdateService:
    """缓存更新服务"""

    def __init__(self, config: dict):
        """
        初始化

        Args:
            config: 系统配置
        """
        self.config = config
        self.db_service = DatabaseService(config.get('database', {}))
        self.technical_analyzer = TechnicalIndicators(config.get('indicators', {}))
        self.investment_analyzer = EnhancedInvestmentAnalyzer(config)
        self.token_mapper = get_token_mapper()

    async def update_all_caches(self, symbols: List[str] = None):
        """
        更新所有缓存表

        Args:
            symbols: 币种列表，如果为None则使用配置中的币种
        """
        if symbols is None:
            symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        logger.info(f"🔄 开始更新缓存 - {len(symbols)} 个币种")
        start_time = datetime.now()

        try:
            # 并行更新各个缓存表
            tasks = [
                self.update_price_stats_cache(symbols),
                self.update_technical_indicators_cache(symbols),
                self.update_hyperliquid_aggregation(symbols),
                self.update_news_sentiment_aggregation(symbols),
                self.update_funding_rate_stats(symbols),
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 最后更新投资建议缓存（依赖前面的缓存）
            await self.update_recommendations_cache(symbols)

            # 统计结果
            success_count = sum(1 for r in results if not isinstance(r, Exception))
            failed_count = len(results) - success_count

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"✅ 缓存更新完成 - 成功: {success_count}, 失败: {failed_count}, "
                f"耗时: {elapsed:.2f}秒"
            )

        except Exception as e:
            logger.error(f"❌ 缓存更新失败: {e}")
            import traceback
            traceback.print_exc()

    async def update_price_stats_cache(self, symbols: List[str]):
        """更新24小时价格统计缓存"""
        logger.info("📊 更新价格统计缓存...")

        for symbol in symbols:
            try:
                # 获取当前价格
                latest_kline = self.db_service.get_latest_kline(symbol, '1m')
                if not latest_kline:
                    continue

                current_price = float(latest_kline.close)

                # 获取24小时前的价格
                past_time = datetime.now() - timedelta(hours=24)
                past_kline = self.db_service.get_kline_at_time(symbol, '5m', past_time)
                price_24h_ago = float(past_kline.close) if past_kline else current_price

                # 获取24小时K线数据
                klines_24h = self.db_service.get_klines(
                    symbol, '1h',
                    start_time=datetime.now() - timedelta(hours=24),
                    limit=24
                )

                if not klines_24h:
                    continue

                # 计算统计数据
                high_24h = max(float(k.high) for k in klines_24h)
                low_24h = min(float(k.low) for k in klines_24h)
                volume_24h = sum(float(k.volume) for k in klines_24h)
                quote_volume_24h = sum(float(k.quote_volume) for k in klines_24h if k.quote_volume)

                change_24h = ((current_price - price_24h_ago) / price_24h_ago) * 100 if price_24h_ago > 0 else 0
                change_24h_abs = current_price - price_24h_ago
                price_range_24h = high_24h - low_24h
                price_range_pct = (price_range_24h / current_price) * 100 if current_price > 0 else 0

                # 判断趋势
                if change_24h > 5:
                    trend = 'strong_up'
                elif change_24h > 1:
                    trend = 'up'
                elif change_24h < -5:
                    trend = 'strong_down'
                elif change_24h < -1:
                    trend = 'down'
                else:
                    trend = 'sideways'

                # 写入数据库
                self._upsert_price_stats(
                    symbol=symbol,
                    current_price=current_price,
                    price_24h_ago=price_24h_ago,
                    change_24h=change_24h,
                    change_24h_abs=change_24h_abs,
                    high_24h=high_24h,
                    low_24h=low_24h,
                    volume_24h=volume_24h,
                    quote_volume_24h=quote_volume_24h,
                    price_range_24h=price_range_24h,
                    price_range_pct=price_range_pct,
                    trend=trend
                )

            except Exception as e:
                logger.warning(f"更新{symbol}价格统计失败: {e}")
                continue

        logger.info(f"✅ 价格统计缓存更新完成 - {len(symbols)} 个币种")

    async def update_technical_indicators_cache(self, symbols: List[str]):
        """更新技术指标缓存"""
        logger.info("📈 更新技术指标缓存...")

        for symbol in symbols:
            try:
                # 获取足够的K线数据用于计算技术指标
                klines = self.db_service.get_latest_klines(symbol, '1h', limit=200)
                if not klines or len(klines) < 50:
                    logger.warning(f"{symbol} K线数据不足，跳过")
                    continue

                # 转换为DataFrame
                df = pd.DataFrame([{
                    'timestamp': k.timestamp,
                    'open': float(k.open),
                    'high': float(k.high),
                    'low': float(k.low),
                    'close': float(k.close),
                    'volume': float(k.volume)
                } for k in reversed(klines)])

                # 计算技术指标
                indicators = self.technical_analyzer.analyze(df)
                if not indicators:
                    continue

                # 提取指标数据
                rsi = indicators.get('rsi', {})
                macd = indicators.get('macd', {})
                bollinger = indicators.get('bollinger', {})
                ema = indicators.get('ema', {})
                kdj = indicators.get('kdj', {})
                volume = indicators.get('volume', {})

                # 计算技术评分 (0-100)
                technical_score = self._calculate_technical_score(indicators)

                # 生成技术信号
                if technical_score >= 75:
                    technical_signal = 'STRONG_BUY'
                elif technical_score >= 60:
                    technical_signal = 'BUY'
                elif technical_score >= 40:
                    technical_signal = 'HOLD'
                elif technical_score >= 25:
                    technical_signal = 'SELL'
                else:
                    technical_signal = 'STRONG_SELL'

                # 获取24小时成交量
                volume_24h = volume.get('volume_24h', 0)
                volume_avg = volume.get('average_volume', 0)

                # 写入数据库
                self._upsert_technical_indicators(
                    symbol=symbol,
                    timeframe='1h',
                    rsi_value=rsi.get('value'),
                    rsi_signal=rsi.get('signal'),
                    macd_value=macd.get('value'),
                    macd_signal_line=macd.get('signal'),
                    macd_histogram=macd.get('histogram'),
                    macd_trend='bullish_cross' if macd.get('bullish_cross') else ('bearish_cross' if macd.get('bearish_cross') else 'neutral'),
                    bb_upper=bollinger.get('upper'),
                    bb_middle=bollinger.get('middle'),
                    bb_lower=bollinger.get('lower'),
                    bb_position=bollinger.get('position', 'middle'),
                    bb_width=bollinger.get('width'),
                    ema_short=ema.get('short'),
                    ema_long=ema.get('long'),
                    ema_trend=ema.get('trend', 'neutral'),
                    kdj_k=kdj.get('k'),
                    kdj_d=kdj.get('d'),
                    kdj_j=kdj.get('j'),
                    kdj_signal=kdj.get('signal'),
                    volume_24h=volume_24h,
                    volume_avg=volume_avg,
                    volume_ratio=(volume_24h / volume_avg) if volume_avg > 0 else 1,
                    volume_signal='high' if volume.get('above_average') else 'normal',
                    technical_score=technical_score,
                    technical_signal=technical_signal,
                    data_points=len(df)
                )

            except Exception as e:
                logger.warning(f"更新{symbol}技术指标失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        logger.info(f"✅ 技术指标缓存更新完成 - {len(symbols)} 个币种")

    async def update_hyperliquid_aggregation(self, symbols: List[str]):
        """更新Hyperliquid聚合数据"""
        logger.info("🧠 更新Hyperliquid聚合缓存...")

        try:
            with HyperliquidDB() as db:
                monitored = db.get_monitored_wallets(active_only=True)

                if not monitored:
                    logger.warning("没有活跃的监控钱包")
                    return

                # 对每个币种进行聚合
                for symbol in symbols:
                    try:
                        coin = symbol.split('/')[0]
                        coin_index = self.token_mapper.get_index(coin)

                        # 统计数据
                        long_trades = 0
                        short_trades = 0
                        net_flow = 0
                        inflow = 0
                        outflow = 0
                        total_volume = 0
                        total_pnl = 0
                        active_wallets = set()
                        trade_sizes = []

                        # 遍历所有监控钱包
                        for wallet in monitored:
                            trades = db.get_wallet_recent_trades(wallet['address'], hours=24)

                            # 筛选该币种的交易
                            coin_trades = [
                                t for t in trades
                                if t['coin'] == coin or t['coin'] == coin_index or
                                   self.token_mapper.get_symbol(t['coin']) == coin
                            ]

                            if not coin_trades:
                                continue

                            active_wallets.add(wallet['address'])

                            for trade in coin_trades:
                                notional = float(trade['notional_usd'])
                                pnl = float(trade['closed_pnl'])

                                total_volume += notional
                                total_pnl += pnl
                                trade_sizes.append(notional)

                                if trade['side'] == 'LONG':
                                    long_trades += 1
                                    net_flow += notional
                                    inflow += notional
                                else:
                                    short_trades += 1
                                    net_flow -= notional
                                    outflow += notional

                        # 如果没有交易，跳过
                        if long_trades + short_trades == 0:
                            continue

                        # 计算统计指标
                        total_trades = long_trades + short_trades
                        long_short_ratio = (long_trades / short_trades) if short_trades > 0 else 999
                        avg_trade_size = total_volume / total_trades if total_trades > 0 else 0
                        max_trade_size = max(trade_sizes) if trade_sizes else 0
                        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

                        # 计算Hyperliquid评分
                        hyperliquid_score = self._calculate_hyperliquid_score(
                            net_flow, long_short_ratio, len(active_wallets), avg_pnl
                        )

                        # 生成信号
                        if net_flow > 1000000:
                            hyperliquid_signal = 'STRONG_BULLISH'
                            sentiment = 'bullish'
                        elif net_flow > 500000:
                            hyperliquid_signal = 'BULLISH'
                            sentiment = 'bullish'
                        elif net_flow < -1000000:
                            hyperliquid_signal = 'STRONG_BEARISH'
                            sentiment = 'bearish'
                        elif net_flow < -500000:
                            hyperliquid_signal = 'BEARISH'
                            sentiment = 'bearish'
                        else:
                            hyperliquid_signal = 'NEUTRAL'
                            sentiment = 'neutral'

                        # 写入数据库
                        self._upsert_hyperliquid_aggregation(
                            symbol=coin,
                            period='24h',
                            net_flow=net_flow,
                            inflow=inflow,
                            outflow=outflow,
                            long_trades=long_trades,
                            short_trades=short_trades,
                            total_trades=total_trades,
                            long_short_ratio=long_short_ratio,
                            total_volume=total_volume,
                            avg_trade_size=avg_trade_size,
                            max_trade_size=max_trade_size,
                            active_wallets=len(active_wallets),
                            unique_wallets=len(active_wallets),
                            total_pnl=total_pnl,
                            avg_pnl=avg_pnl,
                            hyperliquid_score=hyperliquid_score,
                            hyperliquid_signal=hyperliquid_signal,
                            sentiment=sentiment
                        )

                    except Exception as e:
                        logger.warning(f"聚合{symbol} Hyperliquid数据失败: {e}")
                        continue

        except Exception as e:
            logger.error(f"更新Hyperliquid聚合失败: {e}")

        logger.info(f"✅ Hyperliquid聚合缓存更新完成 - {len(symbols)} 个币种")

    async def update_news_sentiment_aggregation(self, symbols: List[str]):
        """更新新闻情绪聚合"""
        logger.info("📰 更新新闻情绪聚合缓存...")

        for symbol in symbols:
            try:
                coin = symbol.split('/')[0]

                # 获取24小时内的新闻
                news_list = self.db_service.get_recent_news(hours=24, limit=1000)

                # 筛选相关新闻
                relevant_news = [
                    n for n in news_list
                    if n.symbols and coin in n.symbols
                ]

                if not relevant_news:
                    continue

                # 统计
                total_news = len(relevant_news)
                positive_news = sum(1 for n in relevant_news if n.sentiment == 'positive')
                negative_news = sum(1 for n in relevant_news if n.sentiment == 'negative')
                neutral_news = sum(1 for n in relevant_news if n.sentiment == 'neutral')

                # 计算情绪指数 (-100 到 +100)
                sentiment_index = ((positive_news - negative_news) / total_news) * 100 if total_news > 0 else 0

                # 平均情绪分数
                sentiment_scores = [float(n.sentiment_score) for n in relevant_news if n.sentiment_score]
                avg_sentiment_score = sum(sentiment_scores) / len(sentiment_scores) if sentiment_scores else 0.5

                # 重大事件
                major_events = [
                    n for n in relevant_news
                    if n.sentiment_score and abs(float(n.sentiment_score) - 0.5) > 0.3
                ]

                # 计算新闻评分 (0-100)
                news_score = self._calculate_news_score(sentiment_index, total_news, len(major_events))

                # 写入数据库
                self._upsert_news_sentiment(
                    symbol=coin,
                    period='24h',
                    total_news=total_news,
                    positive_news=positive_news,
                    negative_news=negative_news,
                    neutral_news=neutral_news,
                    sentiment_index=sentiment_index,
                    avg_sentiment_score=avg_sentiment_score,
                    major_events_count=len(major_events),
                    news_score=news_score
                )

            except Exception as e:
                logger.warning(f"更新{symbol}新闻情绪失败: {e}")
                continue

        logger.info(f"✅ 新闻情绪聚合缓存更新完成 - {len(symbols)} 个币种")

    async def update_funding_rate_stats(self, symbols: List[str]):
        """更新资金费率统计"""
        logger.info("💰 更新资金费率统计缓存...")

        for symbol in symbols:
            try:
                # 获取当前资金费率
                current_funding = self.db_service.get_latest_funding_rate(symbol)
                if not current_funding:
                    continue

                current_rate = float(current_funding.funding_rate)
                current_rate_pct = current_rate * 100

                # 计算资金费率评分
                funding_score = self._calculate_funding_score(current_rate)

                # 判断市场情绪
                if current_rate > 0.0005:  # 0.05%
                    market_sentiment = 'overheated'
                    trend = 'strongly_bullish'
                elif current_rate > 0.0001:
                    market_sentiment = 'normal'
                    trend = 'bullish'
                elif current_rate < -0.0005:
                    market_sentiment = 'oversold'
                    trend = 'strongly_bearish'
                elif current_rate < -0.0001:
                    market_sentiment = 'normal'
                    trend = 'bearish'
                else:
                    market_sentiment = 'normal'
                    trend = 'neutral'

                # 写入数据库
                self._upsert_funding_rate_stats(
                    symbol=symbol,
                    current_rate=current_rate,
                    current_rate_pct=current_rate_pct,
                    trend=trend,
                    market_sentiment=market_sentiment,
                    funding_score=funding_score,
                    exchange=current_funding.exchange
                )

            except Exception as e:
                logger.warning(f"更新{symbol}资金费率统计失败: {e}")
                continue

        logger.info(f"✅ 资金费率统计缓存更新完成 - {len(symbols)} 个币种")

    async def update_recommendations_cache(self, symbols: List[str]):
        """更新投资建议缓存（综合所有缓存表的数据）"""
        logger.info("🎯 更新投资建议缓存...")

        for symbol in symbols:
            try:
                # 从缓存表读取各维度数据
                technical_data = self._get_cached_technical_data(symbol)
                news_data = self._get_cached_news_data(symbol)
                funding_data = self._get_cached_funding_data(symbol)
                hyperliquid_data = self._get_cached_hyperliquid_data(symbol)
                price_stats = self._get_cached_price_stats(symbol)

                # 获取当前价格
                current_price = price_stats.get('current_price', 0) if price_stats else 0

                if current_price == 0:
                    continue

                # 使用投资分析器生成综合分析
                analysis = self.investment_analyzer.analyze(
                    symbol=symbol,
                    technical_data=technical_data,
                    news_data=news_data,
                    funding_data=funding_data,
                    hyperliquid_data=hyperliquid_data,
                    ethereum_data=None,
                    current_price=current_price
                )

                # 写入投资建议缓存
                self._upsert_recommendation(symbol, analysis)

            except Exception as e:
                logger.warning(f"更新{symbol}投资建议失败: {e}")
                import traceback
                traceback.print_exc()
                continue

        logger.info(f"✅ 投资建议缓存更新完成 - {len(symbols)} 个币种")

    # ========== 辅助方法：计算评分 ==========

    def _calculate_technical_score(self, indicators: dict) -> float:
        """计算技术指标综合评分 (0-100)"""
        score = 50.0  # 基础分

        # RSI评分
        rsi = indicators.get('rsi', {})
        rsi_value = rsi.get('value', 50)
        if rsi_value < 30:
            score += 15  # 超卖，看涨
        elif rsi_value > 70:
            score -= 15  # 超买，看跌
        elif 40 <= rsi_value <= 60:
            score += 5  # 中性区域

        # MACD评分
        macd = indicators.get('macd', {})
        if macd.get('bullish_cross'):
            score += 15
        elif macd.get('bearish_cross'):
            score -= 15

        # EMA趋势评分
        ema = indicators.get('ema', {})
        if ema.get('trend') == 'bullish':
            score += 10
        elif ema.get('trend') == 'bearish':
            score -= 10

        # 成交量评分
        volume = indicators.get('volume', {})
        if volume.get('above_average'):
            score += 10

        return max(0, min(100, score))

    def _calculate_hyperliquid_score(self, net_flow: float, long_short_ratio: float,
                                      active_wallets: int, avg_pnl: float) -> float:
        """计算Hyperliquid评分 (0-100)"""
        score = 50.0

        # 净流入评分 (最重要)
        if net_flow > 1000000:
            score += 30
        elif net_flow > 500000:
            score += 20
        elif net_flow > 100000:
            score += 10
        elif net_flow < -1000000:
            score -= 30
        elif net_flow < -500000:
            score -= 20
        elif net_flow < -100000:
            score -= 10

        # 多空比评分
        if long_short_ratio > 2:
            score += 10
        elif long_short_ratio < 0.5:
            score -= 10

        # 活跃钱包数评分
        if active_wallets > 10:
            score += 10
        elif active_wallets > 5:
            score += 5

        return max(0, min(100, score))

    def _calculate_news_score(self, sentiment_index: float, total_news: int,
                               major_events: int) -> float:
        """计算新闻评分 (0-100)"""
        score = 50.0

        # 情绪指数评分
        score += sentiment_index * 0.3  # sentiment_index范围-100到+100

        # 新闻数量评分
        if total_news > 20:
            score += 10
        elif total_news > 10:
            score += 5

        # 重大事件评分
        if major_events > 5:
            score += 10

        return max(0, min(100, score))

    def _calculate_funding_score(self, funding_rate: float) -> float:
        """计算资金费率评分 (0-100)"""
        # 负费率（空头过度）= 看涨
        # 正费率（多头过度）= 看跌

        if funding_rate < -0.001:  # -0.1%
            return 85  # 强烈看涨
        elif funding_rate < -0.0005:  # -0.05%
            return 70  # 看涨
        elif funding_rate > 0.001:  # 0.1%
            return 15  # 强烈看跌
        elif funding_rate > 0.0005:  # 0.05%
            return 30  # 看跌
        else:
            return 50  # 中性

    # ========== 辅助方法：从缓存表读取数据 ==========

    def _get_cached_technical_data(self, symbol: str) -> Optional[dict]:
        """从缓存表读取技术指标数据"""
        session = None
        try:
            session = self.db_service.get_session()
            sql = text("SELECT * FROM technical_indicators_cache WHERE symbol = :symbol")
            result = session.execute(sql, {"symbol": symbol}).fetchone()

            if not result:
                return None

            # Convert result to dict
            result_dict = dict(result._mapping) if hasattr(result, '_mapping') else dict(result)

            return {
                'price': self._get_cached_price_stats(symbol).get('current_price', 0) if self._get_cached_price_stats(symbol) else 0,
                'rsi': {
                    'value': float(result_dict['rsi_value']) if result_dict.get('rsi_value') else 50,
                    'signal': result_dict.get('rsi_signal')
                },
                'macd': {
                    'value': float(result_dict['macd_value']) if result_dict.get('macd_value') else 0,
                    'signal': float(result_dict['macd_signal_line']) if result_dict.get('macd_signal_line') else 0,
                    'histogram': float(result_dict['macd_histogram']) if result_dict.get('macd_histogram') else 0,
                    'bullish_cross': result_dict.get('macd_trend') == 'bullish_cross',
                    'bearish_cross': result_dict.get('macd_trend') == 'bearish_cross'
                },
                'ema': {
                    'trend': result_dict.get('ema_trend')
                },
                'volume': {
                    'above_average': result_dict.get('volume_signal') == 'high'
                }
            }
        except Exception as e:
            logger.warning(f"读取{symbol}技术指标缓存失败: {e}")
            return None
        finally:
            if session:
                session.close()

    def _get_cached_news_data(self, symbol: str) -> Optional[dict]:
        """从缓存表读取新闻情绪数据"""
        try:
            coin = symbol.split('/')[0]
            session = self.db_service.get_session()
            
            cursor.execute(
                "SELECT * FROM news_sentiment_aggregation WHERE symbol = %s AND period = '24h'",
                (coin,)
            )
            result = result_proxy.fetchone() if result_proxy else None
            
            if not result:
                return None

            return {
                'sentiment_index': float(result['sentiment_index']),
                'total_news': result['total_news'],
                'positive': result['positive_news'],
                'negative': result['negative_news'],
                'major_events_count': result['major_events_count']
            }
        except Exception as e:
            logger.warning(f"读取{symbol}新闻缓存失败: {e}")
            return None

    def _get_cached_funding_data(self, symbol: str) -> Optional[dict]:
        """从缓存表读取资金费率数据"""
        try:
            session = self.db_service.get_session()
            
            cursor.execute(
                "SELECT * FROM funding_rate_stats WHERE symbol = %s",
                (symbol,)
            )
            result = result_proxy.fetchone() if result_proxy else None
            
            if not result:
                return None

            return {
                'funding_rate': float(result['current_rate']),
                'funding_rate_pct': float(result['current_rate_pct']),
                'trend': result['trend'],
                'market_sentiment': result['market_sentiment']
            }
        except Exception as e:
            logger.warning(f"读取{symbol}资金费率缓存失败: {e}")
            return None

    def _get_cached_hyperliquid_data(self, symbol: str) -> Optional[dict]:
        """从缓存表读取Hyperliquid数据"""
        try:
            coin = symbol.split('/')[0]
            session = self.db_service.get_session()
            
            cursor.execute(
                "SELECT * FROM hyperliquid_symbol_aggregation WHERE symbol = %s AND period = '24h'",
                (coin,)
            )
            result = result_proxy.fetchone() if result_proxy else None
            
            if not result:
                return None

            return {
                'net_flow': float(result['net_flow']),
                'long_trades': result['long_trades'],
                'short_trades': result['short_trades'],
                'active_wallets': result['active_wallets'],
                'avg_pnl': float(result['avg_pnl']) if result['avg_pnl'] else 0
            }
        except Exception as e:
            logger.warning(f"读取{symbol} Hyperliquid缓存失败: {e}")
            return None

    def _get_cached_price_stats(self, symbol: str) -> Optional[dict]:
        """从缓存表读取价格统计数据"""
        try:
            session = self.db_service.get_session()
            
            cursor.execute(
                "SELECT * FROM price_stats_24h WHERE symbol = %s",
                (symbol,)
            )
            result = result_proxy.fetchone() if result_proxy else None
            
            if not result:
                return None

            return {
                'current_price': float(result['current_price']),
                'change_24h': float(result['change_24h']) if result['change_24h'] else 0,
                'volume_24h': float(result['volume_24h']) if result['volume_24h'] else 0
            }
        except Exception as e:
            logger.warning(f"读取{symbol}价格统计缓存失败: {e}")
            return None

    # ========== 辅助方法：写入数据库 ==========

    def _upsert_price_stats(self, **kwargs):
        """插入或更新价格统计"""
        session = None
        try:
            session = self.db_service.get_session()

            sql = text("""
                INSERT INTO price_stats_24h (
                    symbol, current_price, price_24h_ago, change_24h, change_24h_abs,
                    high_24h, low_24h, volume_24h, quote_volume_24h,
                    price_range_24h, price_range_pct, trend, updated_at
                ) VALUES (
                    :symbol, :current_price, :price_24h_ago, :change_24h, :change_24h_abs,
                    :high_24h, :low_24h, :volume_24h, :quote_volume_24h,
                    :price_range_24h, :price_range_pct, :trend, NOW()
                )
                ON DUPLICATE KEY UPDATE
                    current_price = VALUES(current_price),
                    price_24h_ago = VALUES(price_24h_ago),
                    change_24h = VALUES(change_24h),
                    change_24h_abs = VALUES(change_24h_abs),
                    high_24h = VALUES(high_24h),
                    low_24h = VALUES(low_24h),
                    volume_24h = VALUES(volume_24h),
                    quote_volume_24h = VALUES(quote_volume_24h),
                    price_range_24h = VALUES(price_range_24h),
                    price_range_pct = VALUES(price_range_pct),
                    trend = VALUES(trend),
                    updated_at = NOW()
            """)

            session.execute(sql, kwargs)
            session.commit()

        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"写入价格统计失败: {e}")
        finally:
            if session:
                session.close()

    def _upsert_technical_indicators(self, **kwargs):
        """插入或更新技术指标"""
        session = None
        try:
            session = self.db_service.get_session()

            sql = text("""
                INSERT INTO technical_indicators_cache (
                    symbol, timeframe, rsi_value, rsi_signal,
                    macd_value, macd_signal_line, macd_histogram, macd_trend,
                    bb_upper, bb_middle, bb_lower, bb_position, bb_width,
                    ema_short, ema_long, ema_trend,
                    kdj_k, kdj_d, kdj_j, kdj_signal,
                    volume_24h, volume_avg, volume_ratio, volume_signal,
                    technical_score, technical_signal, data_points, updated_at
                ) VALUES (
                    :symbol, :timeframe, :rsi_value, :rsi_signal,
                    :macd_value, :macd_signal_line, :macd_histogram, :macd_trend,
                    :bb_upper, :bb_middle, :bb_lower, :bb_position, :bb_width,
                    :ema_short, :ema_long, :ema_trend,
                    :kdj_k, :kdj_d, :kdj_j, :kdj_signal,
                    :volume_24h, :volume_avg, :volume_ratio, :volume_signal,
                    :technical_score, :technical_signal, :data_points, NOW()
                )
                ON DUPLICATE KEY UPDATE
                    rsi_value = VALUES(rsi_value),
                    rsi_signal = VALUES(rsi_signal),
                    macd_value = VALUES(macd_value),
                    macd_signal_line = VALUES(macd_signal_line),
                    macd_histogram = VALUES(macd_histogram),
                    macd_trend = VALUES(macd_trend),
                    bb_upper = VALUES(bb_upper),
                    bb_middle = VALUES(bb_middle),
                    bb_lower = VALUES(bb_lower),
                    bb_position = VALUES(bb_position),
                    bb_width = VALUES(bb_width),
                    ema_short = VALUES(ema_short),
                    ema_long = VALUES(ema_long),
                    ema_trend = VALUES(ema_trend),
                    kdj_k = VALUES(kdj_k),
                    kdj_d = VALUES(kdj_d),
                    kdj_j = VALUES(kdj_j),
                    kdj_signal = VALUES(kdj_signal),
                    volume_24h = VALUES(volume_24h),
                    volume_avg = VALUES(volume_avg),
                    volume_ratio = VALUES(volume_ratio),
                    volume_signal = VALUES(volume_signal),
                    technical_score = VALUES(technical_score),
                    technical_signal = VALUES(technical_signal),
                    data_points = VALUES(data_points),
                    updated_at = NOW()
            """)

            session.execute(sql, kwargs)
            session.commit()

        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"写入技术指标失败: {e}")
        finally:
            if session:
                session.close()

    def _upsert_hyperliquid_aggregation(self, **kwargs):
        """插入或更新Hyperliquid聚合数据"""
        session = None
        try:
            session = self.db_service.get_session()

            sql = text("""
                INSERT INTO hyperliquid_symbol_aggregation (
                    symbol, period, net_flow, inflow, outflow,
                    long_trades, short_trades, total_trades, long_short_ratio,
                    total_volume, avg_trade_size, max_trade_size,
                    active_wallets, unique_wallets, total_pnl, avg_pnl,
                    hyperliquid_score, hyperliquid_signal, sentiment, updated_at
                ) VALUES (
                    :symbol, :period, :net_flow, :inflow, :outflow,
                    :long_trades, :short_trades, :total_trades, :long_short_ratio,
                    :total_volume, :avg_trade_size, :max_trade_size,
                    :active_wallets, :unique_wallets, :total_pnl, :avg_pnl,
                    :hyperliquid_score, :hyperliquid_signal, :sentiment, NOW()
                )
                ON DUPLICATE KEY UPDATE
                    net_flow = VALUES(net_flow),
                    inflow = VALUES(inflow),
                    outflow = VALUES(outflow),
                    long_trades = VALUES(long_trades),
                    short_trades = VALUES(short_trades),
                    total_trades = VALUES(total_trades),
                    long_short_ratio = VALUES(long_short_ratio),
                    total_volume = VALUES(total_volume),
                    avg_trade_size = VALUES(avg_trade_size),
                    max_trade_size = VALUES(max_trade_size),
                    active_wallets = VALUES(active_wallets),
                    unique_wallets = VALUES(unique_wallets),
                    total_pnl = VALUES(total_pnl),
                    avg_pnl = VALUES(avg_pnl),
                    hyperliquid_score = VALUES(hyperliquid_score),
                    hyperliquid_signal = VALUES(hyperliquid_signal),
                    sentiment = VALUES(sentiment),
                    updated_at = NOW()
            """)

            session.execute(sql, kwargs)
            session.commit()

        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"写入Hyperliquid聚合数据失败: {e}")
        finally:
            if session:
                session.close()

    def _upsert_news_sentiment(self, **kwargs):
        """插入或更新新闻情绪"""
        session = None
        try:
            session = self.db_service.get_session()

            sql = text("""
                INSERT INTO news_sentiment_aggregation (
                    symbol, period, total_news, positive_news, negative_news, neutral_news,
                    sentiment_index, avg_sentiment_score, major_events_count, news_score, updated_at
                ) VALUES (
                    :symbol, :period, :total_news, :positive_news, :negative_news, :neutral_news,
                    :sentiment_index, :avg_sentiment_score, :major_events_count, :news_score, NOW()
                )
                ON DUPLICATE KEY UPDATE
                    total_news = VALUES(total_news),
                    positive_news = VALUES(positive_news),
                    negative_news = VALUES(negative_news),
                    neutral_news = VALUES(neutral_news),
                    sentiment_index = VALUES(sentiment_index),
                    avg_sentiment_score = VALUES(avg_sentiment_score),
                    major_events_count = VALUES(major_events_count),
                    news_score = VALUES(news_score),
                    updated_at = NOW()
            """)

            session.execute(sql, kwargs)
            session.commit()

        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"写入新闻情绪失败: {e}")
        finally:
            if session:
                session.close()

    def _upsert_funding_rate_stats(self, **kwargs):
        """插入或更新资金费率统计"""
        session = None
        try:
            session = self.db_service.get_session()

            sql = text("""
                INSERT INTO funding_rate_stats (
                    symbol, current_rate, current_rate_pct, trend,
                    market_sentiment, funding_score, exchange, updated_at
                ) VALUES (
                    :symbol, :current_rate, :current_rate_pct, :trend,
                    :market_sentiment, :funding_score, :exchange, NOW()
                )
                ON DUPLICATE KEY UPDATE
                    current_rate = VALUES(current_rate),
                    current_rate_pct = VALUES(current_rate_pct),
                    trend = VALUES(trend),
                    market_sentiment = VALUES(market_sentiment),
                    funding_score = VALUES(funding_score),
                    exchange = VALUES(exchange),
                    updated_at = NOW()
            """)

            session.execute(sql, kwargs)
            session.commit()

        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"写入资金费率统计失败: {e}")
        finally:
            if session:
                session.close()

    def _upsert_recommendation(self, symbol: str, analysis: dict):
        """插入或更新投资建议"""
        import json
        session = None
        try:
            session = self.db_service.get_session()

            scores = analysis['score']
            data_sources = analysis['data_sources']

            sql = text("""
                INSERT INTO investment_recommendations_cache (
                    symbol, total_score, technical_score, news_score, funding_score,
                    hyperliquid_score, ethereum_score, signal, confidence,
                    current_price, entry_price, stop_loss, take_profit,
                    risk_level, risk_factors, reasons,
                    has_technical, has_news, has_funding, has_hyperliquid, has_ethereum,
                    data_completeness, updated_at
                ) VALUES (
                    :symbol, :total_score, :technical_score, :news_score, :funding_score,
                    :hyperliquid_score, :ethereum_score, :signal, :confidence,
                    :current_price, :entry_price, :stop_loss, :take_profit,
                    :risk_level, :risk_factors, :reasons,
                    :has_technical, :has_news, :has_funding, :has_hyperliquid, :has_ethereum,
                    :data_completeness, NOW()
                )
                ON DUPLICATE KEY UPDATE
                    total_score = VALUES(total_score),
                    technical_score = VALUES(technical_score),
                    news_score = VALUES(news_score),
                    funding_score = VALUES(funding_score),
                    hyperliquid_score = VALUES(hyperliquid_score),
                    ethereum_score = VALUES(ethereum_score),
                    signal = VALUES(signal),
                    confidence = VALUES(confidence),
                    current_price = VALUES(current_price),
                    entry_price = VALUES(entry_price),
                    stop_loss = VALUES(stop_loss),
                    take_profit = VALUES(take_profit),
                    risk_level = VALUES(risk_level),
                    risk_factors = VALUES(risk_factors),
                    reasons = VALUES(reasons),
                    has_technical = VALUES(has_technical),
                    has_news = VALUES(has_news),
                    has_funding = VALUES(has_funding),
                    has_hyperliquid = VALUES(has_hyperliquid),
                    has_ethereum = VALUES(has_ethereum),
                    data_completeness = VALUES(data_completeness),
                    updated_at = NOW()
            """)

            params = {
                'symbol': symbol,
                'total_score': scores['total'],
                'technical_score': scores['technical'],
                'news_score': scores['news'],
                'funding_score': scores['funding'],
                'hyperliquid_score': scores['hyperliquid'],
                'ethereum_score': scores['ethereum'],
                'signal': analysis['signal'],
                'confidence': analysis['confidence'],
                'current_price': analysis['price']['current'],
                'entry_price': analysis['price']['entry'],
                'stop_loss': analysis['price']['stop_loss'],
                'take_profit': analysis['price']['take_profit'],
                'risk_level': analysis['risk']['level'],
                'risk_factors': json.dumps(analysis['risk']['factors'], ensure_ascii=False),
                'reasons': json.dumps(analysis['reasons'], ensure_ascii=False),
                'has_technical': data_sources.get('technical', False),
                'has_news': data_sources.get('news', False),
                'has_funding': data_sources.get('funding', False),
                'has_hyperliquid': data_sources.get('hyperliquid', False),
                'has_ethereum': data_sources.get('ethereum', False),
                'data_completeness': sum(1 for v in data_sources.values() if v) / len(data_sources) * 100
            }

            session.execute(sql, params)
            session.commit()

        except Exception as e:
            if session:
                session.rollback()
            logger.error(f"写入投资建议失败: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if session:
                session.close()
