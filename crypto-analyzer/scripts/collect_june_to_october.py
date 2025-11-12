#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
采集所有交易对从6月到10月20日的数据
采集前先删除6月以前的数据
"""

import sys
import os
import io
from pathlib import Path

# Windows 控制台编码修复
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import yaml
import pymysql
import pandas as pd
from datetime import datetime, timedelta
from loguru import logger
from typing import List

# 配置日志
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO"
)


def get_db_config():
    """从配置文件读取数据库配置"""
    config_path = project_root / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    db_config = config.get('database', {}).get('mysql', {})
    return {
        'host': db_config.get('host', 'localhost'),
        'port': db_config.get('port', 3306),
        'user': db_config.get('user', 'root'),
        'password': db_config.get('password', ''),
        'database': db_config.get('database', 'binance-data'),
        'charset': 'utf8mb4',
        'cursorclass': pymysql.cursors.DictCursor
    }




async def collect_historical_data():
    """采集历史数据"""
    # 加载配置
    config_path = project_root / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 获取所有交易对
    symbols = config.get('symbols', [])
    if not symbols:
        print("❌ 配置文件中没有找到交易对列表")
        sys.exit(1)
    
    print(f"\n{'='*80}")
    print(f"📊 开始采集历史数据")
    print(f"交易对数量: {len(symbols)}")
    print(f"时间范围: 2025-06-01 00:00:00 至 2025-10-20 23:59:59")
    print(f"{'='*80}\n")
    
    # 时间范围
    start_time = datetime(2025, 6, 1, 0, 0, 0)
    end_time = datetime(2025, 10, 20, 23, 59, 59)
    
    # 导入采集器
    from app.collectors.price_collector import MultiExchangeCollector
    from app.collectors.binance_futures_collector import BinanceFuturesCollector
    from app.collectors.gate_collector import GateCollector
    
    # 初始化采集器
    collector = MultiExchangeCollector(config)
    
    # 初始化合约采集器
    binance_futures_collector = None
    gate_collector = None
    try:
        binance_config = config.get('exchanges', {}).get('binance', {})
        binance_futures_collector = BinanceFuturesCollector(binance_config)
        print("✅ Binance合约数据采集器初始化成功")
    except Exception as e:
        print(f"⚠️  Binance合约数据采集器初始化失败: {e}，将跳过Binance合约数据采集")
    
    # 初始化Gate.io采集器（用于HYPE/USDT）
    try:
        gate_config = config.get('exchanges', {}).get('gate', {})
        if gate_config.get('enabled', False):
            gate_collector = GateCollector(gate_config)
            print("✅ Gate.io采集器初始化成功（用于HYPE/USDT）")
    except Exception as e:
        print(f"⚠️  Gate.io采集器初始化失败: {e}")
    
    # 数据库连接
    db_config = get_db_config()
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    
    # 时间周期
    timeframes = ['1m', '5m', '15m', '1h', '1d']
    
    total_saved = 0
    errors = []
    
    # 遍历每个交易对
    for idx, symbol in enumerate(symbols, 1):
        try:
            symbol = symbol.strip().upper()
            if not symbol:
                continue
            
            # 确保格式正确
            symbol = symbol.replace(' ', '').replace('_', '/')
            if '/' not in symbol and symbol.endswith('USDT'):
                base = symbol[:-4]
                symbol = f"{base}/USDT"
            
            print(f"\n[{idx}/{len(symbols)}] 正在采集 {symbol}...")
            
            # 判断是否使用Gate.io采集（仅HYPE/USDT）
            use_gate = (symbol.upper() == 'HYPE/USDT')
            
            # 1. 采集价格数据（使用1m K线）
            try:
                print(f"  📈 采集价格数据...")
                if use_gate and gate_collector:
                    # HYPE/USDT 从Gate.io采集
                    days = int((end_time - start_time).total_seconds() / 86400) + 1
                    since = int(start_time.timestamp())
                    df = await gate_collector.fetch_ohlcv(
                        symbol=symbol,
                        timeframe='1m',
                        limit=1000,
                        since=since
                    )
                    # 如果数据不够，需要分批获取
                    if df is not None and len(df) > 0:
                        all_data = [df]
                        last_timestamp = df['timestamp'].iloc[-1]
                        current_since = int(last_timestamp.timestamp()) + 1
                        while current_since < int(end_time.timestamp()):
                            next_df = await gate_collector.fetch_ohlcv(
                                symbol=symbol,
                                timeframe='1m',
                                limit=1000,
                                since=current_since
                            )
                            if next_df is None or len(next_df) == 0:
                                break
                            all_data.append(next_df)
                            last_timestamp = next_df['timestamp'].iloc[-1]
                            current_since = int(last_timestamp.timestamp()) + 1
                            if len(next_df) < 1000:
                                break
                            await asyncio.sleep(0.5)
                        if len(all_data) > 1:
                            df = pd.concat(all_data, ignore_index=True)
                            df = df.drop_duplicates(subset=['timestamp'])
                            df = df.sort_values('timestamp').reset_index(drop=True)
                else:
                    # 其他交易对从Binance采集，但需要指定不使用gate
                    df = await collector.fetch_historical_data(
                        symbol=symbol,
                        timeframe='1m',
                        days=int((end_time - start_time).total_seconds() / 86400) + 1,
                        exchange='binance' if not use_gate else None
                    )
                
                if df is not None and len(df) > 0:
                    print(f"  📊 获取到 {len(df):,} 条原始数据")
                    df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                    print(f"  📊 过滤后剩余 {len(df):,} 条数据（时间范围: {start_time} 至 {end_time}）")
                    
                    if len(df) == 0:
                        print(f"  ⚠️  过滤后无数据，可能时间范围不匹配")
                        errors.append(f"{symbol}: 价格数据时间范围不匹配")
                        continue
                    
                    saved_count = 0
                    
                    for idx, row_tuple in enumerate(df.iterrows()):
                        try:
                            _, row = row_tuple
                            created_at = datetime.now()
                            cursor.execute("""
                                INSERT INTO price_data
                                (symbol, exchange, timestamp, price, open_price, high_price, low_price, close_price, volume, quote_volume, bid_price, ask_price, change_24h, created_at)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                    price = VALUES(price),
                                    open_price = VALUES(open_price),
                                    high_price = VALUES(high_price),
                                    low_price = VALUES(low_price),
                                    close_price = VALUES(close_price),
                                    volume = VALUES(volume),
                                    quote_volume = VALUES(quote_volume),
                                    bid_price = VALUES(bid_price),
                                    ask_price = VALUES(ask_price),
                                    change_24h = VALUES(change_24h),
                                    created_at = VALUES(created_at)
                            """, (
                                symbol, 'gate' if use_gate else 'binance', row['timestamp'],
                                float(row['close']), float(row['open']),
                                float(row['high']), float(row['low']),
                                float(row['close']), float(row['volume']),
                                float(row.get('quote_volume', 0)), 0, 0, 0, created_at
                            ))
                            if cursor.rowcount > 0:
                                saved_count += 1
                        except Exception as e:
                            print(f"  ❌ 保存价格数据失败: {e}")
                            import traceback
                            traceback.print_exc()
                            continue
                    
                    total_saved += saved_count
                    print(f"  ✅ 价格数据: 保存 {saved_count:,} 条")
                else:
                    print(f"  ⚠️  价格数据: 未获取到数据")
                    errors.append(f"{symbol}: 未获取到价格数据")
            except Exception as e:
                error_msg = f"{symbol} 价格数据: {str(e)}"
                errors.append(error_msg)
                print(f"  ❌ 价格数据采集失败: {e}")
            
            # 2. 采集K线数据
            for timeframe in timeframes:
                try:
                    print(f"  📊 采集 {timeframe} K线数据...")
                    if use_gate and gate_collector:
                        # HYPE/USDT 从Gate.io采集
                        days = int((end_time - start_time).total_seconds() / 86400) + 1
                        since = int(start_time.timestamp())
                        df = await gate_collector.fetch_ohlcv(
                            symbol=symbol,
                            timeframe=timeframe,
                            limit=1000,
                            since=since
                        )
                        # 如果数据不够，需要分批获取
                        if df is not None and len(df) > 0:
                            all_data = [df]
                            last_timestamp = df['timestamp'].iloc[-1]
                            current_since = int(last_timestamp.timestamp()) + 1
                            while current_since < int(end_time.timestamp()):
                                next_df = await gate_collector.fetch_ohlcv(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    limit=1000,
                                    since=current_since
                                )
                                if next_df is None or len(next_df) == 0:
                                    break
                                all_data.append(next_df)
                                last_timestamp = next_df['timestamp'].iloc[-1]
                                current_since = int(last_timestamp.timestamp()) + 1
                                if len(next_df) < 1000:
                                    break
                                await asyncio.sleep(0.5)
                            if len(all_data) > 1:
                                df = pd.concat(all_data, ignore_index=True)
                                df = df.drop_duplicates(subset=['timestamp'])
                                df = df.sort_values('timestamp').reset_index(drop=True)
                    else:
                        # 其他交易对从Binance采集
                        df = await collector.fetch_historical_data(
                            symbol=symbol,
                            timeframe=timeframe,
                            days=int((end_time - start_time).total_seconds() / 86400) + 1,
                            exchange='binance' if not use_gate else None
                        )
                    
                    if df is not None and len(df) > 0:
                        print(f"  📊 获取到 {len(df):,} 条原始数据")
                        df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                        print(f"  📊 过滤后剩余 {len(df):,} 条数据（时间范围: {start_time} 至 {end_time}）")
                        
                        if len(df) == 0:
                            print(f"  ⚠️  过滤后无数据，可能时间范围不匹配")
                            errors.append(f"{symbol} {timeframe}: K线数据时间范围不匹配")
                            continue
                        
                        timeframe_saved = 0
                        
                        for idx, row in enumerate(df.iterrows()):
                            try:
                                _, row = row
                                timestamp = row['timestamp']
                                if isinstance(timestamp, pd.Timestamp):
                                    timestamp_dt = timestamp.to_pydatetime()
                                    open_time_ms = int(timestamp.timestamp() * 1000)
                                elif isinstance(timestamp, datetime):
                                    timestamp_dt = timestamp
                                    open_time_ms = int(timestamp.timestamp() * 1000)
                                else:
                                    timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                    open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                
                                timeframe_minutes = {
                                    '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                    '1h': 60, '4h': 240, '1d': 1440
                                }.get(timeframe, 60)
                                close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                created_at = datetime.now()
                                
                                cursor.execute("""
                                    INSERT INTO kline_data
                                    (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                    ON DUPLICATE KEY UPDATE
                                        open_price = VALUES(open_price),
                                        high_price = VALUES(high_price),
                                        low_price = VALUES(low_price),
                                        close_price = VALUES(close_price),
                                        volume = VALUES(volume),
                                        quote_volume = VALUES(quote_volume),
                                        created_at = VALUES(created_at)
                                """, (
                                    symbol, 'gate' if use_gate else 'binance', timeframe, open_time_ms, close_time_ms,
                                    timestamp_dt, float(row['open']), float(row['high']),
                                    float(row['low']), float(row['close']), float(row['volume']),
                                    float(row.get('quote_volume', 0)), created_at
                                ))
                                if cursor.rowcount > 0:
                                    timeframe_saved += 1
                            except Exception as e:
                                print(f"  ❌ 保存K线数据失败: {e}")
                                import traceback
                                traceback.print_exc()
                                continue
                        
                        print(f"  ✅ {timeframe} K线: 保存 {timeframe_saved:,} 条")
                        total_saved += timeframe_saved
                    else:
                        print(f"  ⚠️  {timeframe} K线: 未获取到数据")
                        errors.append(f"{symbol} {timeframe}: 未获取到K线数据")
                    
                    # 延迟避免API限流
                    await asyncio.sleep(0.3)
                    
                except Exception as e:
                    error_msg = f"{symbol} {timeframe}: {str(e)}"
                    errors.append(error_msg)
                    print(f"  ❌ {timeframe} K线采集失败: {e}")
            
            # 3. 采集合约数据（可选）
            if use_gate and gate_collector:
                # HYPE/USDT 从Gate.io采集合约数据
                for timeframe in timeframes:
                    try:
                        print(f"  📈 采集合约 {timeframe} K线数据（Gate.io）...")
                        
                        df = await gate_collector.fetch_historical_futures_data(
                            symbol=symbol,
                            timeframe=timeframe,
                            days=int((end_time - start_time).total_seconds() / 86400) + 1
                        )
                        
                        if df is not None and len(df) > 0:
                            print(f"  📊 获取到 {len(df):,} 条原始数据")
                            df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                            print(f"  📊 过滤后剩余 {len(df):,} 条数据（时间范围: {start_time} 至 {end_time}）")
                            
                            if len(df) == 0:
                                print(f"  ⚠️  过滤后无数据，可能时间范围不匹配")
                                errors.append(f"{symbol} 合约 {timeframe}: K线数据时间范围不匹配")
                                continue
                            
                            timeframe_saved = 0
                            
                            for idx, row in enumerate(df.iterrows()):
                                try:
                                    _, row = row
                                    timestamp = row['timestamp']
                                    if isinstance(timestamp, pd.Timestamp):
                                        timestamp_dt = timestamp.to_pydatetime()
                                        open_time_ms = int(timestamp.timestamp() * 1000)
                                    elif isinstance(timestamp, datetime):
                                        timestamp_dt = timestamp
                                        open_time_ms = int(timestamp.timestamp() * 1000)
                                    else:
                                        timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                        open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                    
                                    timeframe_minutes = {
                                        '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                        '1h': 60, '4h': 240, '1d': 1440
                                    }.get(timeframe, 60)
                                    close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                    created_at = datetime.now()
                                    
                                    cursor.execute("""
                                        INSERT INTO kline_data
                                        (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                        ON DUPLICATE KEY UPDATE
                                            open_price = VALUES(open_price),
                                            high_price = VALUES(high_price),
                                            low_price = VALUES(low_price),
                                            close_price = VALUES(close_price),
                                            volume = VALUES(volume),
                                            quote_volume = VALUES(quote_volume),
                                            created_at = VALUES(created_at)
                                    """, (
                                        symbol, 'gate_futures', timeframe, open_time_ms, close_time_ms,
                                        timestamp_dt, float(row['open']), float(row['high']),
                                        float(row['low']), float(row['close']), float(row['volume']),
                                        float(row.get('quote_volume', 0)), created_at
                                    ))
                                    if cursor.rowcount > 0:
                                        timeframe_saved += 1
                                except Exception as e:
                                    print(f"  ❌ 保存合约K线数据失败: {e}")
                                    import traceback
                                    traceback.print_exc()
                                    continue
                            
                            print(f"  ✅ 合约 {timeframe} K线: 保存 {timeframe_saved:,} 条")
                            total_saved += timeframe_saved
                        else:
                            print(f"  ⚠️  合约 {timeframe} K线: 未获取到数据")
                        
                        await asyncio.sleep(0.3)
                        
                    except Exception as e:
                        error_msg = f"{symbol} 合约 {timeframe}: {str(e)}"
                        errors.append(error_msg)
                        print(f"  ❌ 合约 {timeframe} K线采集失败: {e}")
            elif binance_futures_collector:
                for timeframe in timeframes:
                    try:
                        print(f"  📈 采集合约 {timeframe} K线数据...")
                        
                        days = int((end_time - start_time).total_seconds() / 86400) + 1
                        timeframe_minutes = {
                            '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                            '1h': 60, '4h': 240, '1d': 1440
                        }.get(timeframe, 60)
                        klines_needed = int(days * 1440 / timeframe_minutes)
                        limit = min(klines_needed, 1500)
                        
                        df = await futures_collector.fetch_futures_klines(
                            symbol=symbol,
                            timeframe=timeframe,
                            limit=limit
                        )
                        
                        if df is not None and len(df) > 0:
                            print(f"  📊 获取到 {len(df):,} 条原始数据")
                            df = df[(df['timestamp'] >= start_time) & (df['timestamp'] <= end_time)]
                            print(f"  📊 过滤后剩余 {len(df):,} 条数据（时间范围: {start_time} 至 {end_time}）")
                            
                            if len(df) == 0:
                                print(f"  ⚠️  过滤后无数据，可能时间范围不匹配")
                                errors.append(f"{symbol} 合约 {timeframe}: K线数据时间范围不匹配")
                                continue
                            
                            timeframe_saved = 0
                            
                            for idx, row in enumerate(df.iterrows()):
                                try:
                                    _, row = row
                                    timestamp = row['timestamp']
                                    if isinstance(timestamp, pd.Timestamp):
                                        timestamp_dt = timestamp.to_pydatetime()
                                        open_time_ms = int(timestamp.timestamp() * 1000)
                                    elif isinstance(timestamp, datetime):
                                        timestamp_dt = timestamp
                                        open_time_ms = int(timestamp.timestamp() * 1000)
                                    else:
                                        timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                                        open_time_ms = int(pd.to_datetime(timestamp).timestamp() * 1000)
                                    
                                    timeframe_minutes = {
                                        '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                                        '1h': 60, '4h': 240, '1d': 1440
                                    }.get(timeframe, 60)
                                    close_time_ms = open_time_ms + (timeframe_minutes * 60 * 1000) - 1
                                    created_at = datetime.now()
                                    
                                    cursor.execute("""
                                        INSERT INTO kline_data
                                        (symbol, exchange, timeframe, open_time, close_time, timestamp, open_price, high_price, low_price, close_price, volume, quote_volume, created_at)
                                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                        ON DUPLICATE KEY UPDATE
                                            open_price = VALUES(open_price),
                                            high_price = VALUES(high_price),
                                            low_price = VALUES(low_price),
                                            close_price = VALUES(close_price),
                                            volume = VALUES(volume),
                                            quote_volume = VALUES(quote_volume),
                                            created_at = VALUES(created_at)
                                    """, (
                                        symbol, 'binance_futures', timeframe, open_time_ms, close_time_ms,
                                        timestamp_dt, float(row['open']), float(row['high']),
                                        float(row['low']), float(row['close']), float(row['volume']),
                                        float(row.get('quote_volume', 0)), created_at
                                    ))
                                    if cursor.rowcount > 0:
                                        timeframe_saved += 1
                                except Exception as e:
                                    print(f"  ❌ 保存合约K线数据失败: {e}")
                                    import traceback
                                    traceback.print_exc()
                                    continue
                            
                            print(f"  ✅ 合约 {timeframe} K线: 保存 {timeframe_saved:,} 条")
                            total_saved += timeframe_saved
                        else:
                            print(f"  ⚠️  合约 {timeframe} K线: 未获取到数据")
                        
                        await asyncio.sleep(0.3)
                        
                    except Exception as e:
                        error_msg = f"{symbol} 合约 {timeframe}: {str(e)}"
                        errors.append(error_msg)
                        print(f"  ❌ 合约 {timeframe} K线采集失败: {e}")
            
            # 提交当前交易对的数据
            conn.commit()
            
        except Exception as e:
            error_msg = f"{symbol}: {str(e)}"
            errors.append(error_msg)
            print(f"❌ 采集 {symbol} 数据失败: {e}")
            import traceback
            traceback.print_exc()
    
    conn.commit()
    cursor.close()
    conn.close()
    
    # 输出结果
    print(f"\n{'='*80}")
    print(f"✅ 数据采集完成！")
    print(f"总保存: {total_saved:,} 条数据")
    if errors:
        print(f"错误数量: {len(errors)}")
        print(f"\n错误列表:")
        for error in errors[:10]:  # 只显示前10个错误
            print(f"  - {error}")
        if len(errors) > 10:
            print(f"  ... 还有 {len(errors) - 10} 个错误")
    print(f"{'='*80}\n")


def main():
    """主函数"""
    print("""
    ╔══════════════════════════════════════════════════════════════╗
    ║  历史数据采集脚本                                            ║
    ║  采集所有交易对从6月1日到10月20日的数据                      ║
    ║                                                              ║
    ║  注意：删除数据请先运行 scripts/delete_data_before_date.py  ║
    ╚══════════════════════════════════════════════════════════════╝
    """)
    
    # 采集历史数据
    print("开始采集历史数据...\n")
    asyncio.run(collect_historical_data())


if __name__ == '__main__':
    main()

