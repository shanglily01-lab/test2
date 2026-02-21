"""
交易诊断器
分析历史交易操作，评估买入/卖出时机的合理性
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def _convert_to_python_type(obj: Any) -> Any:
    """将numpy/pandas类型转换为Python原生类型"""
    if isinstance(obj, (np.integer, np.int64, np.int32)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32)):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, dict):
        return {k: _convert_to_python_type(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_convert_to_python_type(item) for item in obj]
    elif pd.isna(obj):
        return None
    else:
        return obj


class TradeDiagnostic:
    """交易诊断器"""
    
    def __init__(self):
        """初始化诊断器"""
        pass
    
    def diagnose_trade(
        self,
        df: pd.DataFrame,
        trade_time: datetime,
        trade_type: str = 'buy',  # 'buy' or 'sell'
        symbol: str = ''
    ) -> Dict:
        """
        诊断交易操作
        
        Args:
            df: 历史K线数据，必须包含 ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            trade_time: 交易时间
            trade_type: 交易类型 ('buy' or 'sell')
            symbol: 交易对名称
        
        Returns:
            诊断结果字典
        """
        if df is None or df.empty:
            return {
                'success': False,
                'error': '数据不足'
            }
        
        # 确保数据按时间排序
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # 确保timestamp是datetime类型
        if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # 找到交易时间点
        trade_idx = self._find_trade_index(df, trade_time)
        if trade_idx is None:
            return {
                'success': False,
                'error': f'无法找到交易时间点: {trade_time}'
            }
        
        # 计算技术指标
        df = self._calculate_indicators(df)
        
        # 获取交易时的价格和指标
        trade_row = df.iloc[trade_idx]
        trade_price = float(trade_row['close'])
        
        # 分析交易时机
        timing_analysis = self._analyze_timing(df, trade_idx, trade_type)
        
        # 分析市场环境
        market_analysis = self._analyze_market_environment(df, trade_idx)
        
        # 分析技术指标
        indicator_analysis = self._analyze_indicators(df, trade_idx, trade_type)
        
        # 分析价格位置
        price_position_analysis = self._analyze_price_position(df, trade_idx)
        
        # 分析后续表现
        performance_analysis = self._analyze_performance(df, trade_idx, trade_type)
        
        # 生成诊断结论
        diagnosis = self._generate_diagnosis(
            timing_analysis,
            market_analysis,
            indicator_analysis,
            price_position_analysis,
            performance_analysis,
            trade_type
        )
        
        # 生成建议
        recommendations = self._generate_recommendations(
            diagnosis,
            market_analysis,
            indicator_analysis,
            trade_type
        )
        
        result = {
            'success': True,
            'symbol': symbol,
            'trade_time': trade_time.isoformat() if isinstance(trade_time, datetime) else str(trade_time),
            'trade_type': trade_type,
            'trade_price': float(trade_price),
            'timing_analysis': timing_analysis,
            'market_analysis': market_analysis,
            'indicator_analysis': indicator_analysis,
            'price_position_analysis': price_position_analysis,
            'performance_analysis': performance_analysis,
            'diagnosis': diagnosis,
            'recommendations': recommendations
        }
        
        # 转换所有numpy类型为Python原生类型
        return _convert_to_python_type(result)
    
    def _find_trade_index(self, df: pd.DataFrame, trade_time: datetime) -> Optional[int]:
        """找到交易时间点"""
        # 将trade_time转换为datetime（如果还不是）
        if not isinstance(trade_time, datetime):
            trade_time = pd.to_datetime(trade_time)
        
        # 找到最接近的时间点（允许1小时误差）
        time_diffs = abs(df['timestamp'] - trade_time)
        min_idx = time_diffs.idxmin()
        min_diff = time_diffs.iloc[min_idx]
        
        # 如果时间差超过1小时，返回None
        if min_diff > timedelta(hours=1):
            return None
        
        return min_idx
    
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
        
        # 成交量移动平均
        df['volume_ma'] = df['volume'].rolling(window=20).mean()
        
        return df
    
    def _analyze_timing(self, df: pd.DataFrame, trade_idx: int, trade_type: str) -> Dict:
        """分析交易时机"""
        if trade_idx < 20:
            return {'score': 50, 'assessment': '数据不足，无法评估'}
        
        # 分析交易前后的价格变化
        before_24h = df.iloc[max(0, trade_idx-24):trade_idx]
        after_24h = df.iloc[trade_idx:min(len(df), trade_idx+24)]
        
        price_before_24h = before_24h.iloc[0]['close'] if len(before_24h) > 0 else df.iloc[trade_idx]['close']
        price_after_24h = after_24h.iloc[-1]['close'] if len(after_24h) > 0 else df.iloc[trade_idx]['close']
        trade_price = df.iloc[trade_idx]['close']
        
        change_before = ((trade_price - price_before_24h) / price_before_24h) * 100
        change_after = ((price_after_24h - trade_price) / trade_price) * 100
        
        if trade_type == 'buy':
            # 买入：如果买入后价格上涨，时机好；如果买入前已经大涨，时机可能不好
            if change_after > 5:
                timing_score = 80
                timing_assessment = '买入后价格上涨，时机较好'
            elif change_after > 0:
                timing_score = 60
                timing_assessment = '买入后价格小幅上涨'
            elif change_before > 10:
                timing_score = 30
                timing_assessment = '买入前已大幅上涨，可能追高'
            elif change_before > 5:
                timing_score = 40
                timing_assessment = '买入前已上涨，时机一般'
            else:
                timing_score = 50
                timing_assessment = '买入时机中性'
        else:
            # 卖出：如果卖出后价格下跌，时机好；如果卖出前已经大跌，时机可能不好
            if change_after < -5:
                timing_score = 80
                timing_assessment = '卖出后价格下跌，时机较好'
            elif change_after < 0:
                timing_score = 60
                timing_assessment = '卖出后价格小幅下跌'
            elif change_before < -10:
                timing_score = 30
                timing_assessment = '卖出前已大幅下跌，可能卖在低点'
            elif change_before < -5:
                timing_score = 40
                timing_assessment = '卖出前已下跌，时机一般'
            else:
                timing_score = 50
                timing_assessment = '卖出时机中性'
        
        return {
            'score': int(timing_score),
            'assessment': str(timing_assessment),
            'price_change_before_24h_pct': round(float(change_before), 2),
            'price_change_after_24h_pct': round(float(change_after), 2)
        }
    
    def _analyze_market_environment(self, df: pd.DataFrame, trade_idx: int) -> Dict:
        """分析市场环境"""
        if trade_idx < 50:
            return {'trend': 'unknown', 'volatility': 'unknown'}
        
        # 分析趋势
        recent = df.iloc[max(0, trade_idx-50):trade_idx+1]
        x = np.arange(len(recent))
        y = recent['close'].values
        slope = np.polyfit(x, y, 1)[0]
        slope_pct = (slope / recent.iloc[0]['close']) * 100
        
        if slope_pct > 0.5:
            trend = 'uptrend'
            trend_strength = 'strong'
        elif slope_pct > 0.1:
            trend = 'uptrend'
            trend_strength = 'weak'
        elif slope_pct < -0.5:
            trend = 'downtrend'
            trend_strength = 'strong'
        elif slope_pct < -0.1:
            trend = 'downtrend'
            trend_strength = 'weak'
        else:
            trend = 'sideways'
            trend_strength = 'neutral'
        
        # 分析波动率
        returns = recent['close'].pct_change().dropna()
        volatility = returns.std() * 100
        
        if volatility > 5:
            vol_level = 'high'
        elif volatility > 2:
            vol_level = 'medium'
        else:
            vol_level = 'low'
        
        return {
            'trend': str(trend),
            'trend_strength': str(trend_strength),
            'trend_slope_pct': round(float(slope_pct), 4),
            'volatility': round(float(volatility), 2),
            'volatility_level': str(vol_level)
        }
    
    def _analyze_indicators(self, df: pd.DataFrame, trade_idx: int, trade_type: str) -> Dict:
        """分析技术指标"""
        if trade_idx < 50:
            return {'signals': [], 'score': 50}
        
        row = df.iloc[trade_idx]
        signals = []
        score = 50
        
        # RSI分析
        if pd.notna(row.get('rsi')):
            rsi = row['rsi']
            if trade_type == 'buy':
                if rsi < 30:
                    signals.append('RSI超卖，买入信号强烈')
                    score += 20
                elif rsi < 40:
                    signals.append('RSI偏低，买入信号')
                    score += 10
                elif rsi > 70:
                    signals.append('RSI超买，买入风险高')
                    score -= 20
                elif rsi > 60:
                    signals.append('RSI偏高，买入需谨慎')
                    score -= 10
            else:  # sell
                if rsi > 70:
                    signals.append('RSI超买，卖出信号强烈')
                    score += 20
                elif rsi > 60:
                    signals.append('RSI偏高，卖出信号')
                    score += 10
                elif rsi < 30:
                    signals.append('RSI超卖，卖出风险高')
                    score -= 20
                elif rsi < 40:
                    signals.append('RSI偏低，卖出需谨慎')
                    score -= 10
        
        # MACD分析
        if pd.notna(row.get('macd_hist')):
            macd_hist = row['macd_hist']
            if trade_type == 'buy':
                if macd_hist > 0:
                    signals.append('MACD看涨，买入信号')
                    score += 10
                else:
                    signals.append('MACD看跌，买入需谨慎')
                    score -= 5
            else:  # sell
                if macd_hist < 0:
                    signals.append('MACD看跌，卖出信号')
                    score += 10
                else:
                    signals.append('MACD看涨，卖出需谨慎')
                    score -= 5
        
        # EMA分析
        if pd.notna(row.get('ema_9')) and pd.notna(row.get('ema_21')):
            if row['ema_9'] > row['ema_21']:
                if trade_type == 'buy':
                    signals.append('短期均线上穿长期均线，买入信号')
                    score += 10
                else:
                    signals.append('均线多头排列，卖出需谨慎')
                    score -= 5
            else:
                if trade_type == 'buy':
                    signals.append('均线空头排列，买入需谨慎')
                    score -= 5
                else:
                    signals.append('短期均线下穿长期均线，卖出信号')
                    score += 10
        
        # 布林带分析
        if pd.notna(row.get('bb_lower')) and pd.notna(row.get('bb_upper')):
            price = row['close']
            bb_position = (price - row['bb_lower']) / (row['bb_upper'] - row['bb_lower']) if row['bb_upper'] != row['bb_lower'] else 0.5
            if trade_type == 'buy':
                if bb_position < 0.2:
                    signals.append('价格接近布林带下轨，买入机会')
                    score += 15
                elif bb_position > 0.8:
                    signals.append('价格接近布林带上轨，买入风险高')
                    score -= 15
            else:  # sell
                if bb_position > 0.8:
                    signals.append('价格接近布林带上轨，卖出机会')
                    score += 15
                elif bb_position < 0.2:
                    signals.append('价格接近布林带下轨，卖出风险高')
                    score -= 15
        
        score = max(0, min(100, score))
        
        result = {
            'signals': [str(s) for s in signals],
            'score': int(score),
            'rsi': None,
            'macd_hist': None,
            'bb_position': None
        }
        
        if pd.notna(row.get('rsi')):
            result['rsi'] = round(float(row.get('rsi', 0)), 2)
        if pd.notna(row.get('macd_hist')):
            result['macd_hist'] = round(float(row.get('macd_hist', 0)), 4)
        if 'bb_position' in locals():
            result['bb_position'] = round(float(bb_position), 2)
        
        return result
    
    def _analyze_price_position(self, df: pd.DataFrame, trade_idx: int) -> Dict:
        """分析价格位置"""
        if trade_idx < 50:
            return {'position': 'unknown', 'score': 50}
        
        recent = df.iloc[max(0, trade_idx-50):trade_idx+1]
        trade_price = df.iloc[trade_idx]['close']
        
        # 计算价格在近期区间的位置
        recent_high = recent['high'].max()
        recent_low = recent['low'].min()
        price_position = (trade_price - recent_low) / (recent_high - recent_low) if recent_high != recent_low else 0.5
        
        if price_position < 0.2:
            position = '低位'
            score = 80  # 买入好位置
        elif price_position < 0.4:
            position = '中低位'
            score = 65
        elif price_position < 0.6:
            position = '中位'
            score = 50
        elif price_position < 0.8:
            position = '中高位'
            score = 35
        else:
            position = '高位'
            score = 20  # 买入风险高
        
        return {
            'position': str(position),
            'position_pct': round(float(price_position * 100), 2),
            'recent_high': round(float(recent_high), 4),
            'recent_low': round(float(recent_low), 4),
            'score': int(score)
        }
    
    def _analyze_performance(self, df: pd.DataFrame, trade_idx: int, trade_type: str) -> Dict:
        """分析后续表现"""
        if trade_idx >= len(df) - 1:
            return {'available': False, 'message': '数据不足，无法分析后续表现'}
        
        trade_price = df.iloc[trade_idx]['close']
        
        # 分析后续1小时、6小时、24小时的表现
        periods = {
            '1h': 1,
            '6h': 6,
            '24h': 24,
            '48h': 48,
            '72h': 72
        }
        
        performance = {}
        for period_name, hours in periods.items():
            future_idx = min(trade_idx + hours, len(df) - 1)
            future_price = df.iloc[future_idx]['close']
            change_pct = ((future_price - trade_price) / trade_price) * 100
            
            if trade_type == 'buy':
                performance[period_name] = {
                    'price': round(float(future_price), 4),
                    'change_pct': round(float(change_pct), 2),
                    'profit': bool(change_pct > 0)
                }
            else:  # sell
                performance[period_name] = {
                    'price': round(float(future_price), 4),
                    'change_pct': round(float(-change_pct), 2),  # 卖出后价格下跌是好事
                    'profit': bool(change_pct < 0)
                }
        
        return {
            'available': True,
            'performance': performance
        }
    
    def _generate_diagnosis(
        self,
        timing_analysis: Dict,
        market_analysis: Dict,
        indicator_analysis: Dict,
        price_position_analysis: Dict,
        performance_analysis: Dict,
        trade_type: str
    ) -> Dict:
        """生成诊断结论"""
        # 综合评分
        scores = [
            timing_analysis.get('score', 50),
            indicator_analysis.get('score', 50),
            price_position_analysis.get('score', 50)
        ]
        avg_score = sum(scores) / len(scores)
        
        # 根据评分判断
        if avg_score >= 75:
            conclusion = '优秀'
            description = '交易时机选择很好，技术指标支持，价格位置合理'
        elif avg_score >= 60:
            conclusion = '良好'
            description = '交易时机选择较好，大部分指标支持'
        elif avg_score >= 45:
            conclusion = '一般'
            description = '交易时机选择一般，部分指标不支持'
        else:
            conclusion = '较差'
            description = '交易时机选择不佳，多个指标不支持'
        
        # 主要问题
        issues = []
        if timing_analysis.get('score', 50) < 40:
            issues.append('交易时机不佳')
        if indicator_analysis.get('score', 50) < 40:
            issues.append('技术指标不支持')
        if price_position_analysis.get('score', 50) < 40:
            issues.append('价格位置不理想')
        
        # 主要优点
        advantages = []
        if timing_analysis.get('score', 50) > 70:
            advantages.append('交易时机选择很好')
        if indicator_analysis.get('score', 50) > 70:
            advantages.append('技术指标强烈支持')
        if price_position_analysis.get('score', 50) > 70:
            advantages.append('价格位置理想')
        
        return {
            'overall_score': round(float(avg_score), 1),
            'conclusion': str(conclusion),
            'description': str(description),
            'issues': [str(i) for i in issues],
            'advantages': [str(a) for a in advantages]
        }
    
    def _generate_recommendations(
        self,
        diagnosis: Dict,
        market_analysis: Dict,
        indicator_analysis: Dict,
        trade_type: str
    ) -> List[str]:
        """生成建议"""
        recommendations = []
        
        # 基于诊断结论
        if diagnosis['overall_score'] < 50:
            recommendations.append('建议在下次交易前，等待更好的入场时机')
            recommendations.append('关注技术指标，等待RSI、MACD等指标给出明确信号')
        
        # 基于市场环境
        if market_analysis.get('trend') == 'downtrend' and trade_type == 'buy':
            recommendations.append('当前处于下跌趋势，买入需谨慎，建议等待趋势反转信号')
        elif market_analysis.get('trend') == 'uptrend' and trade_type == 'sell':
            recommendations.append('当前处于上涨趋势，卖出需谨慎，可能错失后续涨幅')
        
        # 基于波动率
        if market_analysis.get('volatility_level') == 'high':
            recommendations.append('当前市场波动较大，建议控制仓位，设置止损')
        
        # 基于技术指标
        if indicator_analysis.get('score', 50) < 50:
            recommendations.append('技术指标不支持当前操作，建议等待更好的信号')
        
        if not recommendations:
            recommendations.append('交易时机选择合理，继续保持')
        
        return [str(r) for r in recommendations]

