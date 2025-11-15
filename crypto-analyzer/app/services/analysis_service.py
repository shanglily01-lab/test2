"""
数据分析服务
提供技术指标分析和投资建议
"""

from sqlalchemy.orm import Session
from sqlalchemy import desc, func
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from loguru import logger
import pandas as pd
import math

from app.database.models import PriceData, KlineData, NewsData, TradeData, FundingRateData


def safe_float(value, default=0.0):
    """安全转换浮点数,处理NaN和Inf"""
    try:
        if value is None:
            return default
        if isinstance(value, (int, float)):
            if math.isnan(value) or math.isinf(value):
                return default
            return float(value)
        return float(value)
    except (ValueError, TypeError):
        return default


class AnalysisService:
    """数据分析服务"""

    def __init__(self, session: Session):
        self.session = session

    def get_latest_prices(self, limit: int = 10) -> List[Dict]:
        """获取最新价格数据"""
        try:
            # 每个币种获取最新的一条记录
            subquery = (
                self.session.query(
                    PriceData.symbol,
                    func.max(PriceData.timestamp).label('max_timestamp')
                )
                .group_by(PriceData.symbol)
                .subquery()
            )

            prices = (
                self.session.query(PriceData)
                .join(
                    subquery,
                    (PriceData.symbol == subquery.c.symbol) &
                    (PriceData.timestamp == subquery.c.max_timestamp)
                )
                .limit(limit)
                .all()
            )

            return [{
                'symbol': p.symbol,
                'price': float(p.price),
                'change_24h': float(p.change_24h) if p.change_24h else 0,
                'high': float(p.high_price) if p.high_price else 0,
                'low': float(p.low_price) if p.low_price else 0,
                'volume': float(p.volume) if p.volume else 0,
                'timestamp': p.timestamp.strftime('%Y-%m-%d %H:%M:%S') if p.timestamp else ''
            } for p in prices]

        except Exception as e:
            logger.error(f"获取价格数据失败: {e}")
            return []

    def get_kline_data(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        """获取K线数据"""
        try:
            klines = (
                self.session.query(KlineData)
                .filter(KlineData.symbol == symbol)
                .filter(KlineData.timeframe == timeframe)
                .order_by(desc(KlineData.timestamp))
                .limit(limit)
                .all()
            )

            if not klines:
                return pd.DataFrame()

            data = [{
                'timestamp': k.timestamp,
                'open': float(k.open_price),
                'high': float(k.high_price),
                'low': float(k.low_price),
                'close': float(k.close_price),
                'volume': float(k.volume)
            } for k in reversed(klines)]

            return pd.DataFrame(data)

        except Exception as e:
            logger.error(f"获取K线数据失败: {e}")
            return pd.DataFrame()

    def get_funding_rate(self, symbol: str) -> Optional[Dict]:
        """获取最新资金费率"""
        try:
            # 获取最新的资金费率记录
            funding_rate = (
                self.session.query(FundingRateData)
                .filter(FundingRateData.symbol == symbol)
                .order_by(desc(FundingRateData.timestamp))
                .first()
            )

            if not funding_rate:
                return None

            # 计算下次结算时间 (安全处理None值)
            from datetime import datetime
            next_funding_time_str = 'N/A'
            hours_until = 0

            if funding_rate.next_funding_time:
                try:
                    next_funding_dt = datetime.fromtimestamp(funding_rate.next_funding_time / 1000)
                    # 使用UTC时间计算
                    hours_until = (next_funding_dt - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() / 3600
                    next_funding_time_str = next_funding_dt.strftime('%Y-%m-%d %H:%M:%S UTC')
                except:
                    pass

            return {
                'funding_rate': funding_rate.funding_rate,
                'funding_rate_pct': round(funding_rate.funding_rate * 100, 4),
                'mark_price': float(funding_rate.mark_price) if funding_rate.mark_price else 0,
                'index_price': float(funding_rate.index_price) if funding_rate.index_price else 0,
                'next_funding_time': next_funding_time_str,
                'hours_until': round(hours_until, 2)
            }

        except Exception as e:
            logger.error(f"获取资金费率失败: {e}")
            return None

    def get_smart_money_signal(self, symbol: str) -> Optional[Dict]:
        """
        获取聪明钱信号

        Args:
            symbol: 代币符号(如 BTC/USDT)

        Returns:
            聪明钱信号数据或None
        """
        try:
            from app.database.models import SmartMoneySignal

            # 提取代币符号 (BTC/USDT -> BTC)
            token_symbol = symbol.split('/')[0] if '/' in symbol else symbol

            # 获取最新的活跃信号
            signal = (
                self.session.query(SmartMoneySignal)
                .filter(SmartMoneySignal.token_symbol == token_symbol)
                .filter(SmartMoneySignal.is_active == True)
                .order_by(desc(SmartMoneySignal.timestamp))
                .first()
            )

            if not signal:
                return None

            return {
                'token_symbol': signal.token_symbol,
                'signal_type': signal.signal_type,
                'signal_strength': signal.signal_strength,
                'confidence_score': float(signal.confidence_score),
                'smart_money_count': signal.smart_money_count,
                'net_flow_usd': float(signal.net_flow_usd) if signal.net_flow_usd else 0,
                'transaction_count': signal.transaction_count,
                'timestamp': signal.timestamp.strftime('%Y-%m-%d %H:%M:%S')
            }

        except Exception as e:
            logger.error(f"获取聪明钱信号失败: {e}")
            return None

    def get_news_sentiment(self, symbol: str = None, hours: int = 24) -> Dict:
        """获取新闻情绪分析（使用UTC时间）"""
        try:
            # 使用UTC时间计算24小时范围
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            # 转换为naive datetime以便与数据库中的时间比较（数据库存储的是UTC时间的naive datetime）
            cutoff_time = cutoff_time.replace(tzinfo=None)

            query = self.session.query(NewsData).filter(
                NewsData.published_datetime >= cutoff_time
            )

            if symbol:
                # 提取币种代码 BTC/USDT -> BTC
                symbol_code = symbol.split('/')[0] if '/' in symbol else symbol
                query = query.filter(NewsData.symbols.like(f'%{symbol_code}%'))

            news_list = query.order_by(desc(NewsData.published_datetime)).all()

            # 统计情绪
            total = len(news_list)
            positive = sum(1 for n in news_list if n.sentiment == 'positive')
            negative = sum(1 for n in news_list if n.sentiment == 'negative')
            neutral = sum(1 for n in news_list if n.sentiment == 'neutral')

            # 计算情绪指数 (-100 到 +100)
            sentiment_score = 0
            if total > 0:
                sentiment_score = ((positive - negative) / total) * 100

            return {
                'total': total,
                'positive': positive,
                'negative': negative,
                'neutral': neutral,
                'sentiment_score': round(sentiment_score, 2),
                'latest_news': [{
                    'title': n.title,
                    'source': n.source,
                    'sentiment': n.sentiment,
                    'published_at': n.published_datetime.strftime('%Y-%m-%d %H:%M') if n.published_datetime else '',
                    'url': n.url
                } for n in news_list[:10]]
            }

        except Exception as e:
            logger.error(f"获取新闻情绪失败: {e}")
            return {
                'total': 0,
                'positive': 0,
                'negative': 0,
                'neutral': 0,
                'sentiment_score': 0,
                'latest_news': []
            }

    def calculate_technical_indicators(self, df: pd.DataFrame) -> Dict:
        """计算技术指标"""
        if df.empty or len(df) < 20:
            return {}

        try:
            indicators = {}

            # RSI (相对强弱指标)
            indicators['rsi'] = self._calculate_rsi(df['close'], period=14)

            # MACD
            macd_data = self._calculate_macd(df['close'])
            indicators['macd'] = macd_data

            # 布林带
            bb_data = self._calculate_bollinger_bands(df['close'])
            indicators['bollinger_bands'] = bb_data

            # EMA
            ema_12_val = df['close'].ewm(span=12).mean().iloc[-1]
            ema_26_val = df['close'].ewm(span=26).mean().iloc[-1]
            indicators['ema_12'] = safe_float(ema_12_val, 0)
            indicators['ema_26'] = safe_float(ema_26_val, 0)

            # 成交量趋势
            try:
                current_vol = safe_float(df['volume'].iloc[-1], 0)
                avg_vol = safe_float(df['volume'].iloc[-5:].mean(), 0)
                indicators['volume_trend'] = 'increasing' if current_vol > avg_vol else 'decreasing'
            except:
                indicators['volume_trend'] = 'neutral'

            return indicators

        except Exception as e:
            logger.error(f"计算技术指标失败: {e}")
            return {}

    def _calculate_rsi(self, prices: pd.Series, period: int = 14) -> float:
        """计算RSI"""
        try:
            delta = prices.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

            rs = gain / loss
            rsi = 100 - (100 / (1 + rs))

            rsi_value = safe_float(rsi.iloc[-1], 50.0)
            return round(rsi_value, 2)
        except:
            return 50.0

    def _calculate_macd(self, prices: pd.Series) -> Dict:
        """计算MACD"""
        try:
            ema_12 = prices.ewm(span=12).mean()
            ema_26 = prices.ewm(span=26).mean()
            macd_line = ema_12 - ema_26
            signal_line = macd_line.ewm(span=9).mean()
            histogram = macd_line - signal_line

            return {
                'macd': round(safe_float(macd_line.iloc[-1], 0), 2),
                'signal': round(safe_float(signal_line.iloc[-1], 0), 2),
                'histogram': round(safe_float(histogram.iloc[-1], 0), 2)
            }
        except:
            return {'macd': 0, 'signal': 0, 'histogram': 0}

    def _calculate_bollinger_bands(self, prices: pd.Series, period: int = 20) -> Dict:
        """计算布林带"""
        try:
            sma = prices.rolling(window=period).mean()
            std = prices.rolling(window=period).std()

            upper = sma + (std * 2)
            lower = sma - (std * 2)
            current_price = prices.iloc[-1]

            return {
                'upper': round(safe_float(upper.iloc[-1], 0), 2),
                'middle': round(safe_float(sma.iloc[-1], 0), 2),
                'lower': round(safe_float(lower.iloc[-1], 0), 2),
                'current': round(safe_float(current_price, 0), 2)
            }
        except:
            return {'upper': 0, 'middle': 0, 'lower': 0, 'current': 0}

    def generate_investment_advice(self, symbol: str) -> Dict:
        """生成投资建议"""
        try:
            # 1. 获取技术指标
            df = self.get_kline_data(symbol, timeframe='1h', limit=100)
            if df.empty:
                return {
                    'symbol': symbol,
                    'signal': 'HOLD',
                    'confidence': 0,
                    'advice': '数据不足,无法分析',
                    'reasons': [],
                    'entry_price': 0,
                    'stop_loss': 0,
                    'take_profit': 0,
                    'scores': {
                        'technical': 50,
                        'news': 50,
                        'funding': 50,
                        'hyperliquid': 50,
                        'ethereum': 50,
                        'total': 50
                    }
                }

            indicators = self.calculate_technical_indicators(df)

            # 获取当前价格
            current_price = float(df['close'].iloc[-1])

            # 2. 获取新闻情绪
            sentiment = self.get_news_sentiment(symbol, hours=24)

            # 3. 获取资金费率
            funding_rate_data = self.get_funding_rate(symbol)

            # 4. 获取聪明钱信号
            smart_money_signal = self.get_smart_money_signal(symbol)

            # 5. 综合分析 - 计算各维度评分
            signals = []
            score = 0

            # 初始化各维度评分 (0-100)
            technical_score = 50  # 技术指标评分
            news_score = 50       # 新闻情绪评分
            funding_score = 50    # 资金费率评分
            hyperliquid_score = 50  # Hyperliquid聪明钱评分
            ethereum_score = 50   # 链上数据评分

            tech_points = 0  # 技术指标原始分数

            # RSI分析
            rsi = indicators.get('rsi', 50)
            if rsi < 30:
                signals.append('RSI超卖,可能反弹')
                score += 2
                tech_points += 2
            elif rsi > 70:
                signals.append('RSI超买,注意回调')
                score -= 2
                tech_points -= 2
            elif 40 <= rsi <= 60:
                signals.append('RSI中性区域')

            # MACD分析
            macd = indicators.get('macd', {})
            if macd.get('histogram', 0) > 0:
                signals.append('MACD金叉,看涨信号')
                score += 1
                tech_points += 1
            elif macd.get('histogram', 0) < 0:
                signals.append('MACD死叉,看跌信号')
                score -= 1
                tech_points -= 1

            # 布林带分析
            bb = indicators.get('bollinger_bands', {})
            if current_price < bb.get('lower', 0):
                signals.append('价格触及布林下轨,超卖')
                score += 1
                tech_points += 1
            elif current_price > bb.get('upper', 0):
                signals.append('价格触及布林上轨,超买')
                score -= 1
                tech_points -= 1

            # EMA趋势
            ema_12 = indicators.get('ema_12', 0)
            ema_26 = indicators.get('ema_26', 0)
            if ema_12 > ema_26:
                signals.append('均线多头排列,上升趋势')
                score += 1
                tech_points += 1
            else:
                signals.append('均线空头排列,下降趋势')
                score -= 1
                tech_points -= 1

            # 成交量
            if indicators.get('volume_trend') == 'increasing':
                signals.append('成交量放大')
                score += 1
                tech_points += 1

            # 技术指标评分：基准50，每点±8分，范围[10, 90]
            technical_score = max(10, min(90, 50 + tech_points * 8))

            # 新闻情绪
            sentiment_score = sentiment.get('sentiment_score', 0)
            news_points = 0
            if sentiment_score > 30:
                signals.append(f'新闻情绪积极({sentiment_score:.0f}/100)')
                score += 2
                news_points = 2
            elif sentiment_score < -30:
                signals.append(f'新闻情绪消极({sentiment_score:.0f}/100)')
                score -= 2
                news_points = -2
            else:
                signals.append(f'新闻情绪中性({sentiment_score:.0f}/100)')

            # 新闻情绪评分：直接映射sentiment_score(-100到100) -> (0到100)
            news_score = max(0, min(100, 50 + sentiment_score / 2))

            # 资金费率分析
            funding_points = 0
            if funding_rate_data:
                funding_rate = funding_rate_data.get('funding_rate', 0)
                funding_rate_pct = funding_rate_data.get('funding_rate_pct', 0)

                if funding_rate > 0.0005:  # > 0.05%
                    signals.append(f'资金费率极高({funding_rate_pct:+.4f}%),多头过热')
                    score -= 2
                    funding_points = -2
                elif funding_rate > 0.0001:  # > 0.01%
                    signals.append(f'资金费率偏高({funding_rate_pct:+.4f}%),谨慎做多')
                    score -= 1
                    funding_points = -1
                elif funding_rate < -0.0005:  # < -0.05%
                    signals.append(f'资金费率极低({funding_rate_pct:+.4f}%),空头过度')
                    score += 2
                    funding_points = 2
                elif funding_rate < -0.0001:  # < -0.01%
                    signals.append(f'资金费率偏低({funding_rate_pct:+.4f}%),可能反弹')
                    score += 1
                    funding_points = 1
                else:
                    signals.append(f'资金费率中性({funding_rate_pct:+.4f}%)')

            # 资金费率评分：基准50，每点±12分，范围[20, 80]
            funding_score = max(20, min(80, 50 + funding_points * 12))

            # 聪明钱信号分析
            hyperliquid_points = 0
            if smart_money_signal:
                signal_type = smart_money_signal.get('signal_type')
                signal_strength = smart_money_signal.get('signal_strength')
                sm_count = smart_money_signal.get('smart_money_count', 0)
                net_flow = smart_money_signal.get('net_flow_usd', 0)

                if signal_type == 'ACCUMULATION':
                    # 积累 - 多个地址买入
                    signals.append(f'🧠 聪明钱积累: {sm_count}个地址买入${abs(net_flow):,.0f}')
                    if signal_strength == 'STRONG':
                        score += 3
                        hyperliquid_points = 3
                    else:
                        score += 2
                        hyperliquid_points = 2
                elif signal_type == 'BUY':
                    signals.append(f'🧠 聪明钱买入: ${abs(net_flow):,.0f}')
                    score += 2
                    hyperliquid_points = 2
                elif signal_type == 'DISTRIBUTION':
                    # 分发 - 多个地址卖出
                    signals.append(f'🧠 聪明钱分发: {sm_count}个地址卖出${abs(net_flow):,.0f}')
                    if signal_strength == 'STRONG':
                        score -= 3
                        hyperliquid_points = -3
                    else:
                        score -= 2
                        hyperliquid_points = -2
                elif signal_type == 'SELL':
                    signals.append(f'🧠 聪明钱卖出: ${abs(net_flow):,.0f}')
                    score -= 2
                    hyperliquid_points = -2

            # Hyperliquid评分：基准50，每点±10分，范围[20, 80]
            hyperliquid_score = max(20, min(80, 50 + hyperliquid_points * 10))

            # 链上数据评分：目前使用Hyperliquid的部分数据，保持中性偏向
            ethereum_score = max(30, min(70, 50 + hyperliquid_points * 5))

            # 生成建议
            if score >= 4:
                signal = 'STRONG_BUY'
                advice = '强烈建议买入,多个指标显示上涨趋势'
                entry_price = current_price
                stop_loss = current_price * 0.95  # 5%止损
                take_profit = current_price * 1.10  # 10%止盈
            elif score >= 2:
                signal = 'BUY'
                advice = '建议买入,技术面偏多'
                entry_price = current_price
                stop_loss = current_price * 0.97
                take_profit = current_price * 1.06
            elif score <= -4:
                signal = 'STRONG_SELL'
                advice = '强烈建议卖出,多个指标显示下跌风险'
                entry_price = 0
                stop_loss = 0
                take_profit = 0
            elif score <= -2:
                signal = 'SELL'
                advice = '建议卖出,技术面偏空'
                entry_price = 0
                stop_loss = 0
                take_profit = 0
            else:
                signal = 'HOLD'
                advice = '建议观望,等待更明确信号'
                entry_price = current_price
                stop_loss = current_price * 0.95
                take_profit = current_price * 1.05

            confidence = min(abs(score) * 10, 100)

            # 计算加权综合评分（技术60% + 新闻30% + 其他10%）
            weighted_total_score = (
                technical_score * 0.40 +  # 技术指标 40%
                news_score * 0.20 +       # 新闻情绪 20%
                funding_score * 0.15 +    # 资金费率 15%
                hyperliquid_score * 0.20 + # Hyperliquid 20%
                ethereum_score * 0.05     # 链上数据 5%
            )

            return {
                'symbol': symbol,
                'signal': signal,
                'confidence': confidence,
                'advice': advice,
                'reasons': signals,
                'current_price': round(current_price, 2),
                'entry_price': round(entry_price, 2) if entry_price else 0,
                'stop_loss': round(stop_loss, 2) if stop_loss else 0,
                'take_profit': round(take_profit, 2) if take_profit else 0,
                'indicators': indicators,
                'sentiment': sentiment,
                'funding_rate': funding_rate_data,
                'smart_money': smart_money_signal,
                'scores': {
                    'technical': round(technical_score),
                    'news': round(news_score),
                    'funding': round(funding_score),
                    'hyperliquid': round(hyperliquid_score),
                    'ethereum': round(ethereum_score),
                    'total': round(weighted_total_score)
                }
            }

        except Exception as e:
            logger.error(f"生成投资建议失败: {e}")
            return {
                'symbol': symbol,
                'signal': 'HOLD',
                'confidence': 0,
                'advice': f'分析失败: {str(e)}',
                'reasons': [],
                'entry_price': 0,
                'stop_loss': 0,
                'take_profit': 0,
                'scores': {
                    'technical': 50,
                    'news': 50,
                    'funding': 50,
                    'hyperliquid': 50,
                    'ethereum': 50,
                    'total': 50
                }
            }

    def get_dashboard_data(self) -> Dict:
        """获取仪表盘数据"""
        try:
            # 获取最新价格
            latest_prices = self.get_latest_prices(limit=10)

            # 为每个币种生成建议
            recommendations = []
            for price_data in latest_prices[:5]:  # 只分析前5个
                symbol = price_data['symbol']
                advice = self.generate_investment_advice(symbol)
                recommendations.append(advice)

            # 获取最新新闻
            recent_news = (
                self.session.query(NewsData)
                .order_by(desc(NewsData.published_datetime))
                .limit(20)
                .all()
            )

            news_list = [{
                'title': n.title,
                'source': n.source,
                'sentiment': n.sentiment,
                'symbols': n.symbols,
                'published_at': n.published_datetime.strftime('%Y-%m-%d %H:%M') if n.published_datetime else '',
                'url': n.url
            } for n in recent_news]

            return {
                'prices': latest_prices,
                'recommendations': recommendations,
                'news': news_list,
                'last_updated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            }

        except Exception as e:
            logger.error(f"获取仪表盘数据失败: {e}")
            return {
                'prices': [],
                'recommendations': [],
                'news': [],
                'last_updated': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
            }
