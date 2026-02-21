"""
最佳买点和卖点分析器
使用多种技术分析方法识别历史数据中的最佳买入和卖出时机
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class BuySellAnalyzer:
    """最佳买点和卖点分析器"""
    
    def __init__(self):
        """初始化分析器"""
        pass
    
    def analyze_optimal_points(
        self,
        df: pd.DataFrame,
        lookback_period: int = 20,
        min_profit_pct: float = 5.0
    ) -> Dict:
        """
        分析历史数据，找出最佳买点和卖点
        
        Args:
            df: 历史K线数据，必须包含 ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            lookback_period: 回看周期（用于计算局部极值）
            min_profit_pct: 最小盈利百分比（过滤掉盈利太小的交易）
        
        Returns:
            包含买点和卖点的字典
        """
        if df is None or df.empty or len(df) < lookback_period * 2:
            return {
                'buy_points': [],
                'sell_points': [],
                'optimal_pairs': [],
                'total_opportunities': 0
            }
        
        # 确保数据按时间排序
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # 计算技术指标
        df = self._calculate_indicators(df)
        
        # 方法1: 基于局部极值识别
        local_minima = self._find_local_minima(df, lookback_period)
        local_maxima = self._find_local_maxima(df, lookback_period)
        
        # 方法2: 基于技术指标识别
        technical_buy_signals = self._find_technical_buy_signals(df)
        technical_sell_signals = self._find_technical_sell_signals(df)
        
        # 方法3: 基于支撑阻力位识别
        support_resistance_points = self._find_support_resistance(df, lookback_period)
        
        # 合并所有买点和卖点
        buy_points = self._merge_buy_points(local_minima, technical_buy_signals, support_resistance_points['support'])
        sell_points = self._merge_sell_points(local_maxima, technical_sell_signals, support_resistance_points['resistance'])
        
        # 过滤和评分
        buy_points = self._score_buy_points(df, buy_points)
        sell_points = self._score_sell_points(df, sell_points)
        
        # 匹配买点和卖点，形成交易对
        optimal_pairs = self._match_buy_sell_pairs(df, buy_points, sell_points, min_profit_pct)
        
        # 为每个买点添加对应的最佳卖点信息
        buy_points_with_sell = []
        for buy_point in buy_points[:20]:
            # 找到该买点对应的最佳卖点
            best_pair = None
            best_profit = 0
            for pair in optimal_pairs:
                if pair['buy']['index'] == buy_point['index']:
                    if pair['profit_pct'] > best_profit:
                        best_profit = pair['profit_pct']
                        best_pair = pair
            
            buy_point_with_sell = buy_point.copy()
            if best_pair:
                buy_point_with_sell['optimal_sell'] = best_pair['sell']
                buy_point_with_sell['expected_profit_pct'] = best_pair['profit_pct']
                buy_point_with_sell['duration_days'] = best_pair.get('duration_days', 0)
            else:
                buy_point_with_sell['optimal_sell'] = None
                buy_point_with_sell['expected_profit_pct'] = 0
                buy_point_with_sell['duration_days'] = 0
            
            buy_points_with_sell.append(buy_point_with_sell)
        
        # 为每个卖点添加对应的最佳买点信息
        sell_points_with_buy = []
        for sell_point in sell_points[:20]:
            # 找到该卖点对应的最佳买点
            best_pair = None
            best_profit = 0
            for pair in optimal_pairs:
                if pair['sell']['index'] == sell_point['index']:
                    if pair['profit_pct'] > best_profit:
                        best_profit = pair['profit_pct']
                        best_pair = pair
            
            sell_point_with_buy = sell_point.copy()
            if best_pair:
                sell_point_with_buy['optimal_buy'] = best_pair['buy']
                sell_point_with_buy['expected_profit_pct'] = best_pair['profit_pct']
                sell_point_with_buy['duration_days'] = best_pair.get('duration_days', 0)
            else:
                sell_point_with_buy['optimal_buy'] = None
                sell_point_with_buy['expected_profit_pct'] = 0
                sell_point_with_buy['duration_days'] = 0
            
            sell_points_with_buy.append(sell_point_with_buy)
        
        return {
            'buy_points': buy_points_with_sell,  # 买点包含对应的卖点信息
            'sell_points': sell_points_with_buy,  # 卖点包含对应的买点信息
            'optimal_pairs': optimal_pairs,
            'total_opportunities': len(optimal_pairs)
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
        df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
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
    
    def _find_local_minima(self, df: pd.DataFrame, period: int) -> List[Dict]:
        """找出局部最小值（潜在的买点）"""
        minima = []
        for i in range(period, len(df) - period):
            current_price = df.iloc[i]['close']
            # 检查是否是局部最小值
            window = df.iloc[i-period:i+period+1]
            if current_price == window['close'].min():
                minima.append({
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp'],
                    'price': float(current_price),
                    'type': 'local_minima',
                    'score': 0
                })
        return minima
    
    def _find_local_maxima(self, df: pd.DataFrame, period: int) -> List[Dict]:
        """找出局部最大值（潜在的卖点）"""
        maxima = []
        for i in range(period, len(df) - period):
            current_price = df.iloc[i]['close']
            # 检查是否是局部最大值
            window = df.iloc[i-period:i+period+1]
            if current_price == window['close'].max():
                maxima.append({
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp'],
                    'price': float(current_price),
                    'type': 'local_maxima',
                    'score': 0
                })
        return maxima
    
    def _find_technical_buy_signals(self, df: pd.DataFrame) -> List[Dict]:
        """基于技术指标找出买入信号"""
        buy_signals = []
        
        for i in range(50, len(df)):
            row = df.iloc[i]
            
            # 检查多个买入条件
            conditions_met = 0
            
            # RSI超卖
            if pd.notna(row.get('rsi')) and row['rsi'] < 30:
                conditions_met += 1
            
            # MACD金叉
            if i > 0 and pd.notna(row.get('macd_hist')) and pd.notna(df.iloc[i-1].get('macd_hist')):
                if df.iloc[i-1]['macd_hist'] < 0 and row['macd_hist'] > 0:
                    conditions_met += 1
            
            # 价格接近布林带下轨
            if pd.notna(row.get('bb_lower')):
                bb_position = (row['close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower']) if row['bb_upper'] != row['bb_lower'] else 0.5
                if bb_position < 0.2:  # 价格在下轨附近
                    conditions_met += 1
            
            # EMA金叉
            if i > 0 and pd.notna(row.get('ema_20')) and pd.notna(row.get('ema_50')):
                if df.iloc[i-1]['ema_20'] <= df.iloc[i-1]['ema_50'] and row['ema_20'] > row['ema_50']:
                    conditions_met += 1
            
            # 成交量放大
            if pd.notna(row.get('volume_ma')) and row['volume'] > row['volume_ma'] * 1.5:
                conditions_met += 1
            
            if conditions_met >= 2:  # 至少满足2个条件
                buy_signals.append({
                    'index': i,
                    'timestamp': row['timestamp'],
                    'price': float(row['close']),
                    'type': 'technical_signal',
                    'score': conditions_met * 10,
                    'reasons': self._get_buy_reasons(row, conditions_met)
                })
        
        return buy_signals
    
    def _find_technical_sell_signals(self, df: pd.DataFrame) -> List[Dict]:
        """基于技术指标找出卖出信号"""
        sell_signals = []
        
        for i in range(50, len(df)):
            row = df.iloc[i]
            
            # 检查多个卖出条件
            conditions_met = 0
            
            # RSI超买
            if pd.notna(row.get('rsi')) and row['rsi'] > 70:
                conditions_met += 1
            
            # MACD死叉
            if i > 0 and pd.notna(row.get('macd_hist')) and pd.notna(df.iloc[i-1].get('macd_hist')):
                if df.iloc[i-1]['macd_hist'] > 0 and row['macd_hist'] < 0:
                    conditions_met += 1
            
            # 价格接近布林带上轨
            if pd.notna(row.get('bb_upper')):
                bb_position = (row['close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower']) if row['bb_upper'] != row['bb_lower'] else 0.5
                if bb_position > 0.8:  # 价格在上轨附近
                    conditions_met += 1
            
            # EMA死叉
            if i > 0 and pd.notna(row.get('ema_20')) and pd.notna(row.get('ema_50')):
                if df.iloc[i-1]['ema_20'] >= df.iloc[i-1]['ema_50'] and row['ema_20'] < row['ema_50']:
                    conditions_met += 1
            
            # 成交量放大
            if pd.notna(row.get('volume_ma')) and row['volume'] > row['volume_ma'] * 1.5:
                conditions_met += 1
            
            if conditions_met >= 2:  # 至少满足2个条件
                sell_signals.append({
                    'index': i,
                    'timestamp': row['timestamp'],
                    'price': float(row['close']),
                    'type': 'technical_signal',
                    'score': conditions_met * 10,
                    'reasons': self._get_sell_reasons(row, conditions_met)
                })
        
        return sell_signals
    
    def _find_support_resistance(self, df: pd.DataFrame, period: int) -> Dict:
        """找出支撑位和阻力位"""
        support_points = []
        resistance_points = []
        
        # 使用滚动窗口找出支撑和阻力
        for i in range(period, len(df) - period):
            window = df.iloc[i-period:i+period+1]
            current_price = df.iloc[i]['close']
            
            # 支撑位：价格在窗口底部附近
            window_low = window['low'].min()
            if abs(current_price - window_low) / current_price < 0.02:  # 2%以内
                support_points.append({
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp'],
                    'price': float(window_low),
                    'type': 'support',
                    'score': 0
                })
            
            # 阻力位：价格在窗口顶部附近
            window_high = window['high'].max()
            if abs(current_price - window_high) / current_price < 0.02:  # 2%以内
                resistance_points.append({
                    'index': i,
                    'timestamp': df.iloc[i]['timestamp'],
                    'price': float(window_high),
                    'type': 'resistance',
                    'score': 0
                })
        
        return {
            'support': support_points,
            'resistance': resistance_points
        }
    
    def _merge_buy_points(self, *point_lists) -> List[Dict]:
        """合并所有买点，去重"""
        all_points = []
        seen_indices = set()
        
        for point_list in point_lists:
            for point in point_list:
                idx = point['index']
                if idx not in seen_indices:
                    seen_indices.add(idx)
                    all_points.append(point)
        
        return sorted(all_points, key=lambda x: x['index'])
    
    def _merge_sell_points(self, *point_lists) -> List[Dict]:
        """合并所有卖点，去重"""
        return self._merge_buy_points(*point_lists)  # 使用相同逻辑
    
    def _score_buy_points(self, df: pd.DataFrame, buy_points: List[Dict]) -> List[Dict]:
        """为买点评分"""
        for point in buy_points:
            score = point.get('score', 0)
            idx = point['index']
            
            if idx < len(df):
                row = df.iloc[idx]
                
                # RSI越低越好
                if pd.notna(row.get('rsi')):
                    if row['rsi'] < 25:
                        score += 20
                    elif row['rsi'] < 30:
                        score += 10
                
                # 价格相对位置（越低越好）
                if idx > 0:
                    recent_high = df.iloc[max(0, idx-20):idx]['high'].max()
                    price_position = (row['close'] - recent_high) / recent_high
                    if price_position < -0.1:  # 下跌超过10%
                        score += 15
                    elif price_position < -0.05:  # 下跌超过5%
                        score += 8
                
                # 成交量确认
                if pd.notna(row.get('volume_ma')) and row['volume'] > row['volume_ma'] * 1.2:
                    score += 10
            
            point['score'] = score
        
        # 按分数排序
        return sorted(buy_points, key=lambda x: x['score'], reverse=True)
    
    def _score_sell_points(self, df: pd.DataFrame, sell_points: List[Dict]) -> List[Dict]:
        """为卖点评分"""
        for point in sell_points:
            score = point.get('score', 0)
            idx = point['index']
            
            if idx < len(df):
                row = df.iloc[idx]
                
                # RSI越高越好
                if pd.notna(row.get('rsi')):
                    if row['rsi'] > 75:
                        score += 20
                    elif row['rsi'] > 70:
                        score += 10
                
                # 价格相对位置（越高越好）
                if idx > 0:
                    recent_low = df.iloc[max(0, idx-20):idx]['low'].min()
                    price_position = (row['close'] - recent_low) / recent_low
                    if price_position > 0.1:  # 上涨超过10%
                        score += 15
                    elif price_position > 0.05:  # 上涨超过5%
                        score += 8
                
                # 成交量确认
                if pd.notna(row.get('volume_ma')) and row['volume'] > row['volume_ma'] * 1.2:
                    score += 10
            
            point['score'] = score
        
        # 按分数排序
        return sorted(sell_points, key=lambda x: x['score'], reverse=True)
    
    def _match_buy_sell_pairs(
        self,
        df: pd.DataFrame,
        buy_points: List[Dict],
        sell_points: List[Dict],
        min_profit_pct: float
    ) -> List[Dict]:
        """匹配买点和卖点，形成交易对"""
        pairs = []
        
        for buy_point in buy_points:
            buy_idx = buy_point['index']
            buy_price = buy_point['price']
            
            # 找到买点之后的最佳卖点
            best_sell = None
            best_profit = 0
            
            for sell_point in sell_points:
                sell_idx = sell_point['index']
                
                # 卖点必须在买点之后
                if sell_idx > buy_idx:
                    profit_pct = ((sell_point['price'] - buy_price) / buy_price) * 100
                    
                    if profit_pct >= min_profit_pct and profit_pct > best_profit:
                        best_profit = profit_pct
                        best_sell = sell_point
            
            if best_sell:
                pairs.append({
                    'buy': buy_point,
                    'sell': best_sell,
                    'profit_pct': round(best_profit, 2),
                    'duration_days': (best_sell['timestamp'] - buy_point['timestamp']).days if hasattr(best_sell['timestamp'], 'days') else 0
                })
        
        # 按盈利百分比排序
        return sorted(pairs, key=lambda x: x['profit_pct'], reverse=True)
    
    def _get_buy_reasons(self, row: pd.Series, conditions_met: int) -> List[str]:
        """获取买入原因"""
        reasons = []
        if pd.notna(row.get('rsi')) and row['rsi'] < 30:
            reasons.append(f"RSI超卖({row['rsi']:.1f})")
        if pd.notna(row.get('macd_hist')) and row['macd_hist'] > 0:
            reasons.append("MACD金叉")
        if pd.notna(row.get('bb_lower')):
            bb_position = (row['close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower']) if row['bb_upper'] != row['bb_lower'] else 0.5
            if bb_position < 0.2:
                reasons.append("价格接近布林带下轨")
        return reasons
    
    def _get_sell_reasons(self, row: pd.Series, conditions_met: int) -> List[str]:
        """获取卖出原因"""
        reasons = []
        if pd.notna(row.get('rsi')) and row['rsi'] > 70:
            reasons.append(f"RSI超买({row['rsi']:.1f})")
        if pd.notna(row.get('macd_hist')) and row['macd_hist'] < 0:
            reasons.append("MACD死叉")
        if pd.notna(row.get('bb_upper')):
            bb_position = (row['close'] - row['bb_lower']) / (row['bb_upper'] - row['bb_lower']) if row['bb_upper'] != row['bb_lower'] else 0.5
            if bb_position > 0.8:
                reasons.append("价格接近布林带上轨")
        return reasons

