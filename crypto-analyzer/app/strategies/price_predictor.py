"""
价格走势预测器
基于历史价格数据和技术分析预测未来走势
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class PricePredictor:
    """价格走势预测器"""
    
    def __init__(self):
        """初始化预测器"""
        pass
    
    def predict_future_trend(
        self,
        df: pd.DataFrame,
        days_ahead: int = 3
    ) -> Dict:
        """
        预测未来走势
        
        Args:
            df: 历史K线数据（至少7天），必须包含 ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            days_ahead: 预测未来天数（默认3天）
        
        Returns:
            预测结果字典
        """
        if df is None or df.empty or len(df) < 24:  # 至少需要24小时数据（1天）
            return {
                'success': False,
                'error': '数据不足，至少需要1天的历史数据'
            }
        
        # 确保数据按时间排序
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # 计算技术指标
        df = self._calculate_indicators(df)
        
        # 分析当前趋势
        trend_analysis = self._analyze_trend(df)
        
        # 预测方法1: 基于趋势延续
        trend_prediction = self._predict_by_trend(df, days_ahead)
        
        # 预测方法2: 基于技术指标
        indicator_prediction = self._predict_by_indicators(df, days_ahead)
        
        # 预测方法3: 基于价格模式
        pattern_prediction = self._predict_by_pattern(df, days_ahead)
        
        # 预测方法4: 基于移动平均
        ma_prediction = self._predict_by_moving_average(df, days_ahead)
        
        # 综合预测
        final_prediction = self._combine_predictions(
            trend_prediction,
            indicator_prediction,
            pattern_prediction,
            ma_prediction,
            df
        )
        
        # 计算置信度
        confidence = self._calculate_confidence(df, trend_analysis, final_prediction)
        
        # 生成预测点（未来3天，每天一个预测点）
        prediction_points = self._generate_prediction_points(df, final_prediction, days_ahead)
        
        # 生成建议
        recommendation = self._generate_recommendation(final_prediction, confidence, trend_analysis)
        
        return {
            'success': True,
            'current_price': float(df.iloc[-1]['close']),
            'current_trend': trend_analysis,
            'prediction': final_prediction,
            'confidence': confidence,
            'prediction_points': prediction_points,
            'recommendation': recommendation,
            'key_levels': self._identify_key_levels(df),
            'risk_factors': self._identify_risks(df, final_prediction)
        }
    
    def _calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算技术指标"""
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        # EMA
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
        
        # MACD
        ema_12 = df['close'].ewm(span=12, adjust=False).mean()
        ema_26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd'] = ema_12 - ema_26
        df['macd_signal'] = df['macd'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd'] - df['macd_signal']
        
        # 布林带
        df['bb_middle'] = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['bb_upper'] = df['bb_middle'] + (bb_std * 2)
        df['bb_lower'] = df['bb_middle'] - (bb_std * 2)
        
        # ATR (平均真实波幅)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        df['atr'] = true_range.rolling(window=14).mean()
        
        # 成交量移动平均
        df['volume_ma'] = df['volume'].rolling(window=20).mean()
        
        return df
    
    def _analyze_trend(self, df: pd.DataFrame) -> Dict:
        """分析当前趋势"""
        if len(df) < 50:
            return {'direction': 'neutral', 'strength': 0}
        
        # 使用最近50个数据点分析趋势
        recent = df.tail(50)
        
        # 计算价格变化
        price_change = ((recent.iloc[-1]['close'] - recent.iloc[0]['close']) / recent.iloc[0]['close']) * 100
        
        # 计算趋势强度（使用线性回归斜率）
        x = np.arange(len(recent))
        y = recent['close'].values
        slope = np.polyfit(x, y, 1)[0]
        slope_pct = (slope / recent.iloc[0]['close']) * 100
        
        # 判断趋势方向
        if slope_pct > 0.1:
            direction = 'upward'
            strength = min(abs(slope_pct) * 10, 100)
        elif slope_pct < -0.1:
            direction = 'downward'
            strength = min(abs(slope_pct) * 10, 100)
        else:
            direction = 'neutral'
            strength = 0
        
        # EMA趋势确认
        ema_trend = 'up' if recent.iloc[-1]['ema_9'] > recent.iloc[-1]['ema_21'] else 'down'
        
        return {
            'direction': direction,
            'strength': round(strength, 2),
            'price_change_7d': round(price_change, 2),
            'slope_pct': round(slope_pct, 4),
            'ema_trend': ema_trend,
            'trend_consistency': self._check_trend_consistency(recent)
        }
    
    def _check_trend_consistency(self, df: pd.DataFrame) -> float:
        """检查趋势一致性（0-100）"""
        if len(df) < 20:
            return 50
        
        # 计算多个时间段的趋势方向
        periods = [5, 10, 20]
        directions = []
        
        for period in periods:
            if len(df) >= period:
                segment = df.tail(period)
                change = ((segment.iloc[-1]['close'] - segment.iloc[0]['close']) / segment.iloc[0]['close']) * 100
                directions.append('up' if change > 0 else 'down')
        
        # 计算一致性
        if len(set(directions)) == 1:
            return 100  # 完全一致
        elif len(set(directions)) == 2:
            return 50   # 部分一致
        else:
            return 0     # 不一致
    
    def _predict_by_trend(self, df: pd.DataFrame, days_ahead: int) -> Dict:
        """基于趋势预测"""
        if len(df) < 50:
            return {'direction': 'neutral', 'price_change_pct': 0, 'confidence': 0}
        
        recent = df.tail(50)
        current_price = float(recent.iloc[-1]['close'])
        
        # 计算趋势斜率
        x = np.arange(len(recent))
        y = recent['close'].values
        slope = np.polyfit(x, y, 1)[0]
        
        # 预测未来价格（线性外推）
        future_index = len(recent) + (days_ahead * 24)  # 假设1小时K线
        predicted_price = slope * future_index + np.polyfit(x, y, 1)[1]
        price_change_pct = ((predicted_price - current_price) / current_price) * 100
        
        direction = 'upward' if price_change_pct > 0 else 'downward' if price_change_pct < 0 else 'neutral'
        
        return {
            'direction': direction,
            'price_change_pct': round(price_change_pct, 2),
            'predicted_price': round(predicted_price, 4),
            'confidence': min(abs(price_change_pct) * 2, 80)  # 基于变化幅度
        }
    
    def _predict_by_indicators(self, df: pd.DataFrame, days_ahead: int) -> Dict:
        """基于技术指标预测"""
        if len(df) < 50:
            return {'direction': 'neutral', 'price_change_pct': 0, 'confidence': 0}
        
        current = df.iloc[-1]
        current_price = float(current['close'])
        
        signals = []
        score = 0
        
        # RSI信号
        if pd.notna(current.get('rsi')):
            rsi = current['rsi']
            if rsi < 30:
                signals.append('RSI超卖，可能反弹')
                score += 15
            elif rsi > 70:
                signals.append('RSI超买，可能回调')
                score -= 15
            elif 40 <= rsi <= 60:
                score += 5
        
        # MACD信号
        if pd.notna(current.get('macd_hist')):
            macd_hist = current['macd_hist']
            if macd_hist > 0:
                signals.append('MACD看涨')
                score += 10
            else:
                signals.append('MACD看跌')
                score -= 10
        
        # EMA信号
        if pd.notna(current.get('ema_9')) and pd.notna(current.get('ema_21')):
            if current['ema_9'] > current['ema_21']:
                signals.append('短期均线上穿长期均线')
                score += 10
            else:
                score -= 5
        
        # 布林带位置
        if pd.notna(current.get('bb_lower')) and pd.notna(current.get('bb_upper')):
            bb_position = (current_price - current['bb_lower']) / (current['bb_upper'] - current['bb_lower']) if current['bb_upper'] != current['bb_lower'] else 0.5
            if bb_position < 0.2:
                signals.append('价格接近布林带下轨')
                score += 10
            elif bb_position > 0.8:
                signals.append('价格接近布林带上轨')
                score -= 10
        
        # 根据得分预测
        if score > 20:
            direction = 'upward'
            price_change_pct = min(score * 0.5, 15)  # 最多预测15%涨幅
        elif score < -20:
            direction = 'downward'
            price_change_pct = max(score * 0.5, -15)  # 最多预测15%跌幅
        else:
            direction = 'neutral'
            price_change_pct = score * 0.2
        
        predicted_price = current_price * (1 + price_change_pct / 100)
        
        return {
            'direction': direction,
            'price_change_pct': round(price_change_pct, 2),
            'predicted_price': round(predicted_price, 4),
            'confidence': min(abs(score) * 2, 75),
            'signals': signals
        }
    
    def _predict_by_pattern(self, df: pd.DataFrame, days_ahead: int) -> Dict:
        """基于价格模式预测"""
        if len(df) < 30:
            return {'direction': 'neutral', 'price_change_pct': 0, 'confidence': 0}
        
        recent = df.tail(30)
        current_price = float(recent.iloc[-1]['close'])
        
        # 识别价格模式
        pattern = self._identify_pattern(recent)
        
        # 根据模式预测
        if pattern['type'] == 'uptrend':
            # 上升趋势，预测继续上涨但幅度减小
            price_change_pct = pattern['strength'] * 0.3
            direction = 'upward'
            confidence = min(pattern['strength'] * 1.5, 70)
        elif pattern['type'] == 'downtrend':
            # 下降趋势，预测继续下跌但幅度减小
            price_change_pct = -pattern['strength'] * 0.3
            direction = 'downward'
            confidence = min(pattern['strength'] * 1.5, 70)
        elif pattern['type'] == 'consolidation':
            # 震荡，预测小幅波动
            price_change_pct = pattern['volatility'] * 0.5 * (1 if np.random.random() > 0.5 else -1)
            direction = 'neutral'
            confidence = 30
        else:
            price_change_pct = 0
            direction = 'neutral'
            confidence = 20
        
        predicted_price = current_price * (1 + price_change_pct / 100)
        
        return {
            'direction': direction,
            'price_change_pct': round(price_change_pct, 2),
            'predicted_price': round(predicted_price, 4),
            'confidence': round(confidence, 2),
            'pattern': pattern['type']
        }
    
    def _identify_pattern(self, df: pd.DataFrame) -> Dict:
        """识别价格模式"""
        if len(df) < 10:
            return {'type': 'unknown', 'strength': 0, 'volatility': 0}
        
        prices = df['close'].values
        
        # 计算波动率
        returns = np.diff(prices) / prices[:-1]
        volatility = np.std(returns) * 100
        
        # 计算趋势强度
        x = np.arange(len(prices))
        slope = np.polyfit(x, prices, 1)[0]
        slope_pct = (slope / prices[0]) * 100
        
        # 判断模式
        if abs(slope_pct) > 0.5:
            if slope_pct > 0:
                return {'type': 'uptrend', 'strength': min(abs(slope_pct) * 20, 100), 'volatility': volatility}
            else:
                return {'type': 'downtrend', 'strength': min(abs(slope_pct) * 20, 100), 'volatility': volatility}
        else:
            return {'type': 'consolidation', 'strength': 0, 'volatility': volatility}
    
    def _predict_by_moving_average(self, df: pd.DataFrame, days_ahead: int) -> Dict:
        """基于移动平均预测"""
        if len(df) < 50:
            return {'direction': 'neutral', 'price_change_pct': 0, 'confidence': 0}
        
        current = df.iloc[-1]
        current_price = float(current['close'])
        
        # 使用EMA预测
        if pd.notna(current.get('ema_9')) and pd.notna(current.get('ema_21')):
            ema_9 = current['ema_9']
            ema_21 = current['ema_21']
            
            # 计算EMA的斜率
            ema_9_slope = (ema_9 - df.iloc[-5]['ema_9']) / 5 if len(df) >= 5 else 0
            ema_21_slope = (ema_21 - df.iloc[-5]['ema_21']) / 5 if len(df) >= 5 else 0
            
            # 预测未来EMA值
            future_ema_9 = ema_9 + ema_9_slope * (days_ahead * 24)
            future_ema_21 = ema_21 + ema_21_slope * (days_ahead * 24)
            
            # 预测价格（基于EMA收敛）
            if ema_9 > ema_21:
                # 上升趋势，价格可能在EMA上方
                predicted_price = max(future_ema_9, future_ema_21) * 1.02
                direction = 'upward'
            else:
                # 下降趋势，价格可能在EMA下方
                predicted_price = min(future_ema_9, future_ema_21) * 0.98
                direction = 'downward'
            
            price_change_pct = ((predicted_price - current_price) / current_price) * 100
            confidence = min(abs(price_change_pct) * 3, 70)
        else:
            predicted_price = current_price
            price_change_pct = 0
            direction = 'neutral'
            confidence = 0
        
        return {
            'direction': direction,
            'price_change_pct': round(price_change_pct, 2),
            'predicted_price': round(predicted_price, 4),
            'confidence': round(confidence, 2)
        }
    
    def _combine_predictions(
        self,
        trend_pred: Dict,
        indicator_pred: Dict,
        pattern_pred: Dict,
        ma_pred: Dict,
        df: pd.DataFrame
    ) -> Dict:
        """综合多个预测结果"""
        current_price = float(df.iloc[-1]['close'])
        
        # 加权平均（趋势40%，指标30%，模式20%，均线10%）
        weights = {
            'trend': 0.4,
            'indicator': 0.3,
            'pattern': 0.2,
            'ma': 0.1
        }
        
        # 计算加权平均价格变化
        weighted_change = (
            trend_pred['price_change_pct'] * weights['trend'] +
            indicator_pred['price_change_pct'] * weights['indicator'] +
            pattern_pred['price_change_pct'] * weights['pattern'] +
            ma_pred['price_change_pct'] * weights['ma']
        )
        
        # 计算加权平均置信度
        weighted_confidence = (
            trend_pred.get('confidence', 0) * weights['trend'] +
            indicator_pred.get('confidence', 0) * weights['indicator'] +
            pattern_pred.get('confidence', 0) * weights['pattern'] +
            ma_pred.get('confidence', 0) * weights['ma']
        )
        
        # 确定方向
        if weighted_change > 2:
            direction = 'upward'
        elif weighted_change < -2:
            direction = 'downward'
        else:
            direction = 'neutral'
        
        predicted_price = current_price * (1 + weighted_change / 100)
        
        return {
            'direction': direction,
            'price_change_pct': round(weighted_change, 2),
            'predicted_price': round(predicted_price, 4),
            'confidence': round(weighted_confidence, 2),
            'components': {
                'trend': trend_pred,
                'indicator': indicator_pred,
                'pattern': pattern_pred,
                'ma': ma_pred
            }
        }
    
    def _calculate_confidence(
        self,
        df: pd.DataFrame,
        trend_analysis: Dict,
        prediction: Dict
    ) -> float:
        """计算预测置信度"""
        confidence = prediction.get('confidence', 50)
        
        # 趋势一致性加分
        if trend_analysis.get('trend_consistency', 0) > 70:
            confidence += 10
        
        # 趋势强度加分
        if trend_analysis.get('strength', 0) > 50:
            confidence += 10
        
        # 如果多个方法预测一致，加分
        components = prediction.get('components', {})
        directions = [c.get('direction', 'neutral') for c in components.values()]
        if len(set(directions)) == 1 and directions[0] != 'neutral':
            confidence += 15
        
        return min(confidence, 95)
    
    def _generate_prediction_points(
        self,
        df: pd.DataFrame,
        prediction: Dict,
        days_ahead: int
    ) -> List[Dict]:
        """生成未来预测点"""
        current_price = float(df.iloc[-1]['close'])
        current_time = df.iloc[-1]['timestamp']
        
        # 如果是datetime对象，转换为datetime
        if hasattr(current_time, 'to_pydatetime'):
            current_time = current_time.to_pydatetime()
        elif not isinstance(current_time, datetime):
            current_time = pd.to_datetime(current_time)
        
        total_change_pct = prediction['price_change_pct']
        daily_change_pct = total_change_pct / days_ahead if days_ahead > 0 else 0
        
        points = []
        for day in range(1, days_ahead + 1):
            # 计算该天的预测价格
            day_change_pct = daily_change_pct * day
            predicted_price = current_price * (1 + day_change_pct / 100)
            
            # 计算时间（每天一个点，假设在中午12点）
            predicted_time = current_time + timedelta(days=day)
            predicted_time = predicted_time.replace(hour=12, minute=0, second=0)
            
            points.append({
                'day': day,
                'timestamp': predicted_time.isoformat(),
                'predicted_price': round(predicted_price, 4),
                'price_change_pct': round(day_change_pct, 2),
                'confidence': max(prediction.get('confidence', 50) - (day * 5), 30)  # 越远置信度越低
            })
        
        return points
    
    def _generate_recommendation(
        self,
        prediction: Dict,
        confidence: float,
        trend_analysis: Dict
    ) -> Dict:
        """生成交易建议"""
        direction = prediction['direction']
        price_change_pct = prediction['price_change_pct']
        
        if direction == 'upward' and price_change_pct > 3 and confidence > 60:
            action = 'BUY'
            advice = f"预测未来3天上涨 {abs(price_change_pct):.2f}%，建议买入"
            risk_level = 'MEDIUM'
        elif direction == 'downward' and price_change_pct < -3 and confidence > 60:
            action = 'SELL'
            advice = f"预测未来3天下跌 {abs(price_change_pct):.2f}%，建议卖出或观望"
            risk_level = 'MEDIUM'
        elif direction == 'upward' and price_change_pct > 0:
            action = 'HOLD_BUY'
            advice = f"预测小幅上涨 {abs(price_change_pct):.2f}%，可考虑轻仓买入"
            risk_level = 'LOW'
        elif direction == 'downward' and price_change_pct < 0:
            action = 'HOLD_SELL'
            advice = f"预测小幅下跌 {abs(price_change_pct):.2f}%，建议谨慎观望"
            risk_level = 'LOW'
        else:
            action = 'HOLD'
            advice = "预测价格震荡，建议观望"
            risk_level = 'LOW'
        
        return {
            'action': action,
            'advice': advice,
            'risk_level': risk_level,
            'confidence': round(confidence, 2)
        }
    
    def _identify_key_levels(self, df: pd.DataFrame) -> Dict:
        """识别关键价位"""
        if len(df) < 20:
            return {'support': 0, 'resistance': 0}
        
        recent = df.tail(100) if len(df) >= 100 else df
        
        # 支撑位：最近的低点
        support = float(recent['low'].min())
        
        # 阻力位：最近的高点
        resistance = float(recent['high'].max())
        
        # 当前价格
        current_price = float(recent.iloc[-1]['close'])
        
        return {
            'support': round(support, 4),
            'resistance': round(resistance, 4),
            'current_price': round(current_price, 4),
            'support_distance_pct': round(((current_price - support) / support) * 100, 2),
            'resistance_distance_pct': round(((resistance - current_price) / current_price) * 100, 2)
        }
    
    def _identify_risks(self, df: pd.DataFrame, prediction: Dict) -> List[str]:
        """识别风险因素"""
        risks = []
        
        if len(df) < 20:
            return risks
        
        current = df.iloc[-1]
        
        # 高波动率风险
        if len(df) >= 20:
            recent_volatility = df.tail(20)['close'].pct_change().std() * 100
            if recent_volatility > 5:
                risks.append(f"近期波动率较高({recent_volatility:.2f}%)，预测不确定性增加")
        
        # RSI极端值风险
        if pd.notna(current.get('rsi')):
            rsi = current['rsi']
            if rsi > 80:
                risks.append("RSI极度超买，可能出现回调")
            elif rsi < 20:
                risks.append("RSI极度超卖，可能出现反弹")
        
        # 成交量异常
        if pd.notna(current.get('volume_ma')):
            volume_ratio = current['volume'] / current['volume_ma'] if current['volume_ma'] > 0 else 1
            if volume_ratio < 0.5:
                risks.append("成交量萎缩，趋势可能减弱")
        
        # 预测置信度低
        if prediction.get('confidence', 0) < 50:
            risks.append("预测置信度较低，建议谨慎参考")
        
        # 趋势不一致
        components = prediction.get('components', {})
        directions = [c.get('direction', 'neutral') for c in components.values()]
        if len(set(directions)) > 2:
            risks.append("多个预测方法结果不一致，市场方向不明确")
        
        return risks

