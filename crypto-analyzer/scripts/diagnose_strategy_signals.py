"""
诊断策略信号：检查最近的K线数据和EMA值，看看是否有交叉信号
"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml
import pandas as pd
from datetime import datetime, timedelta
from app.analyzers.technical_indicators import TechnicalIndicators

# 加载配置文件
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

# 策略配置
strategies_file = project_root / 'config' / 'strategies' / 'futures_strategies.json'
import json
with open(strategies_file, 'r', encoding='utf-8') as f:
    strategies = json.load(f)

enabled_strategies = [s for s in strategies if s.get('enabled', False)]

connection = pymysql.connect(
    host=db_config.get('host', 'localhost'),
    port=db_config.get('port', 3306),
    user=db_config.get('user', 'root'),
    password=db_config.get('password', ''),
    database=db_config.get('database', 'binance-data'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

try:
    cursor = connection.cursor()
    tech_indicators = TechnicalIndicators()
    
    print("=" * 80)
    print("策略信号诊断")
    print("=" * 80)
    print(f"启用的策略数量: {len(enabled_strategies)}\n")
    
    for strategy in enabled_strategies:
        strategy_name = strategy.get('name')
        symbols = strategy.get('symbols', [])
        buy_signal = strategy.get('buySignals')
        buy_timeframe = '15m' if buy_signal == 'ema_15m' else '5m' if buy_signal == 'ema_5m' else '1h'
        
        print(f"\n策略: {strategy_name}")
        print(f"买入信号: {buy_signal} ({buy_timeframe})")
        print(f"交易对: {symbols}")
        print("-" * 80)
        
        for symbol in symbols:
            # 获取K线数据
            cursor.execute("""
                SELECT * 
                FROM kline_data
                WHERE symbol = %s AND timeframe = %s
                ORDER BY timestamp DESC
                LIMIT 50
            """, (symbol, buy_timeframe))
            klines = cursor.fetchall()
            
            if not klines or len(klines) < 26:
                print(f"  {symbol}: K线数据不足（需要至少26根，实际{len(klines) if klines else 0}根）")
                continue
            
            # 转换为DataFrame
            df = pd.DataFrame(list(reversed(klines)))
            if 'close_price' in df.columns:
                df['close'] = pd.to_numeric(df['close_price'], errors='coerce')
            else:
                df['close'] = pd.to_numeric(df['close'], errors='coerce')
            
            # 计算EMA
            ema_short_series = tech_indicators.calculate_ema(df, period=9)
            ema_long_series = tech_indicators.calculate_ema(df, period=26)
            
            # 获取最新的2根K线
            latest_ema_short = ema_short_series.iloc[-1] if not pd.isna(ema_short_series.iloc[-1]) else None
            latest_ema_long = ema_long_series.iloc[-1] if not pd.isna(ema_long_series.iloc[-1]) else None
            prev_ema_short = ema_short_series.iloc[-2] if len(ema_short_series) >= 2 and not pd.isna(ema_short_series.iloc[-2]) else None
            prev_ema_long = ema_long_series.iloc[-2] if len(ema_long_series) >= 2 and not pd.isna(ema_long_series.iloc[-2]) else None
            
            if latest_ema_short is None or latest_ema_long is None or prev_ema_short is None or prev_ema_long is None:
                print(f"  {symbol}: EMA数据不完整")
                continue
            
            # 检查交叉
            is_golden_cross = (prev_ema_short <= prev_ema_long and latest_ema_short > latest_ema_long) or \
                             (prev_ema_short < prev_ema_long and latest_ema_short >= latest_ema_long)
            is_death_cross = (prev_ema_short >= prev_ema_long and latest_ema_short < latest_ema_long) or \
                            (prev_ema_short > prev_ema_long and latest_ema_short <= latest_ema_long)
            
            print(f"  {symbol}:")
            print(f"    前EMA9: {prev_ema_short:.4f}, 前EMA26: {prev_ema_long:.4f}")
            print(f"    当前EMA9: {latest_ema_short:.4f}, 当前EMA26: {latest_ema_long:.4f}")
            print(f"    金叉(做多): {is_golden_cross}")
            print(f"    死叉(做空): {is_death_cross}")
            
            # 检查MA10/EMA10过滤
            if len(klines) >= 10:
                ma10_series = tech_indicators.calculate_ma(df, period=10)
                ema10_series = tech_indicators.calculate_ema(df, period=10)
                latest_ma10 = ma10_series.iloc[-1] if not pd.isna(ma10_series.iloc[-1]) else None
                latest_ema10 = ema10_series.iloc[-1] if not pd.isna(ema10_series.iloc[-1]) else None
                
                if latest_ma10 and latest_ema10:
                    ma10_ema10_ok_long = latest_ema10 > latest_ma10  # 做多需要EMA10 > MA10
                    ma10_ema10_ok_short = latest_ema10 < latest_ma10  # 做空需要EMA10 < MA10
                    print(f"    MA10: {latest_ma10:.4f}, EMA10: {latest_ema10:.4f}")
                    print(f"    MA10/EMA10过滤(做多): {ma10_ema10_ok_long}")
                    print(f"    MA10/EMA10过滤(做空): {ma10_ema10_ok_short}")
                    
                    # 检查策略配置的MA10/EMA10过滤
                    ma10_ema10_trend_filter = strategy.get('ma10Ema10TrendFilter', False)
                    if ma10_ema10_trend_filter:
                        if is_golden_cross and not ma10_ema10_ok_long:
                            print(f"    [过滤] 做多信号被MA10/EMA10过滤掉")
                        if is_death_cross and not ma10_ema10_ok_short:
                            print(f"    [过滤] 做空信号被MA10/EMA10过滤掉")
            
            # 检查配置的方向
            buy_directions = strategy.get('buyDirection', [])
            if is_golden_cross and 'long' not in buy_directions:
                print(f"    [过滤] 金叉信号但未配置做多方向")
            if is_death_cross and 'short' not in buy_directions:
                print(f"    [过滤] 死叉信号但未配置做空方向")
            
            print()
    
finally:
    cursor.close()
    connection.close()

