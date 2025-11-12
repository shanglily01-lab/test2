#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
补采数据脚本 - 从今天13:00到现在
用于补采因调度器阻塞而缺失的数据
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


async def collect_backfill_data(start_hour: int = 13):
    """
    补采数据 - 从指定小时到现在
    
    Args:
        start_hour: 开始的小时数（默认13，即13:00）
    """
    # 加载配置
    config_path = project_root / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 获取所有交易对
    symbols = config.get('symbols', [])
    if not symbols:
        print("❌ 配置文件中没有找到交易对列表")
        sys.exit(1)
    
    # 计算时间范围
    now = datetime.now()
    today_start = now.replace(hour=start_hour, minute=0, second=0, microsecond=0)
    
    # 如果今天13:00还没到，使用昨天13:00
    if today_start > now:
        start_time = (now - timedelta(days=1)).replace(hour=start_hour, minute=0, second=0, microsecond=0)
    else:
        start_time = today_start
    
    end_time = now
    
    # 确保开始时间不早于现在24小时前（避免采集过多数据）
    max_start = now - timedelta(hours=24)
    if start_time < max_start:
        start_time = max_start
        print(f"⚠️  开始时间已调整为24小时前: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    print(f"\n{'='*80}")
    print(f"📊 开始补采数据")
    print(f"交易对数量: {len(symbols)}")
    print(f"时间范围: {start_time.strftime('%Y-%m-%d %H:%M:%S')} 至 {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"时长: {(end_time - start_time).total_seconds() / 3600:.1f} 小时")
    print(f"{'='*80}\n")
    
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
                    since = int(start_time.timestamp())
                    df = await gate_collector.fetch_ohlcv(
                        symbol=symbol,
                        timeframe='1m',
                        limit=1000,
                        since=since * 1000
                    )
                else:
                    # 其他交易对从Binance采集
                    since = int(start_time.timestamp())
                    df = await collector.fetch_ohlcv(
                        symbol=symbol,
                        timeframe='1m',
                        exchange='binance',
                        limit=1000,
                        since=since * 1000
                    )
                
                if df is not None and len(df) > 0:
                    # 过滤时间范围
                    df = df[df['timestamp'] >= start_time]
                    df = df[df['timestamp'] <= end_time]
                    
                    if len(df) > 0:
                        saved_count = 0
                        for _, row in df.iterrows():
                            timestamp = row['timestamp']
                            if isinstance(timestamp, pd.Timestamp):
                                timestamp_dt = timestamp.to_pydatetime()
                            else:
                                timestamp_dt = pd.to_datetime(timestamp).to_pydatetime()
                            
                            cursor.execute("""
                                INSERT INTO price_data
                                (symbol, exchange, timestamp, price, open_price, high_price, low_price, close_price, volume, quote_volume)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON DUPLICATE KEY UPDATE
                                    price = VALUES(price),
                                    open_price = VALUES(open_price),
                                    high_price = VALUES(high_price),
                                    low_price = VALUES(low_price),
                                    close_price = VALUES(close_price),
                                    volume = VALUES(volume),
                                    quote_volume = VALUES(quote_volume)
                            """, (
                                symbol, 'gate' if use_gate else 'binance', timestamp_dt,
                                float(row['close']), float(row['open']), float(row['high']),
                                float(row['low']), float(row['close']), float(row['volume']),
                                float(row.get('quote_volume', 0))
                            ))
                            if cursor.rowcount > 0:
                                saved_count += 1
                        
                        conn.commit()
                        total_saved += saved_count
                        print(f"    ✓ 价格数据: 保存 {saved_count} 条")
                    else:
                        print(f"    ⊗ 价格数据: 时间范围内无数据")
                else:
                    print(f"    ⊗ 价格数据: 获取失败或为空")
            except Exception as e:
                error_msg = f"{symbol} 价格数据采集失败: {e}"
                print(f"    ❌ {error_msg}")
                errors.append(error_msg)
            
            # 2. 采集K线数据（所有时间周期）
            for timeframe in timeframes:
                try:
                    print(f"  📊 采集K线数据 ({timeframe})...")
                    
                    # 根据时间周期计算需要采集的数据量
                    timeframe_minutes = {
                        '1m': 1, '5m': 5, '15m': 15, '30m': 30,
                        '1h': 60, '4h': 240, '1d': 1440
                    }.get(timeframe, 60)
                    
                    # 计算需要采集的K线数量
                    total_minutes = int((end_time - start_time).total_seconds() / 60)
                    needed_klines = (total_minutes // timeframe_minutes) + 1
                    
                    # Binance API限制，每次最多1000条
                    all_klines = []
                    current_start = start_time
                    
                    while current_start < end_time:
                        try:
                            if use_gate and gate_collector:
                                # HYPE/USDT 从Gate.io采集
                                since = int(current_start.timestamp())
                                df = await gate_collector.fetch_ohlcv(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    limit=1000,
                                    since=since * 1000
                                )
                            else:
                                # 其他交易对从Binance采集
                                since = int(current_start.timestamp())
                                df = await collector.fetch_ohlcv(
                                    symbol=symbol,
                                    timeframe=timeframe,
                                    exchange='binance',
                                    limit=1000,
                                    since=since * 1000
                                )
                            
                            if df is not None and len(df) > 0:
                                # 过滤时间范围
                                df = df[df['timestamp'] >= start_time]
                                df = df[df['timestamp'] <= end_time]
                                
                                if len(df) > 0:
                                    all_klines.append(df)
                                    
                                    # 更新起始时间（使用最后一条K线的时间）
                                    last_time = df['timestamp'].iloc[-1]
                                    if isinstance(last_time, pd.Timestamp):
                                        last_time_dt = last_time.to_pydatetime()
                                    else:
                                        last_time_dt = pd.to_datetime(last_time).to_pydatetime()
                                    
                                    # 移动到下一个时间点
                                    current_start = last_time_dt + timedelta(minutes=timeframe_minutes)
                                    
                                    # 如果获取的数据少于1000条，说明已经到末尾
                                    if len(df) < 1000:
                                        break
                                else:
                                    break
                            else:
                                break
                            
                            # 避免请求过快
                            await asyncio.sleep(0.2)
                            
                        except Exception as e:
                            print(f"    ⚠️  获取K线数据时出错: {e}")
                            break
                    
                    # 合并所有K线数据
                    if all_klines:
                        df_all = pd.concat(all_klines, ignore_index=True)
                        df_all = df_all.drop_duplicates(subset=['timestamp'], keep='last')
                        df_all = df_all.sort_values('timestamp')
                        
                        # 保存到数据库
                        saved_count = 0
                        for _, row in df_all.iterrows():
                            try:
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
                                    symbol, 'gate' if use_gate else 'binance', timeframe,
                                    open_time_ms, close_time_ms, timestamp_dt,
                                    float(row['open']), float(row['high']),
                                    float(row['low']), float(row['close']),
                                    float(row['volume']), float(row.get('quote_volume', 0)),
                                    created_at
                                ))
                                if cursor.rowcount > 0:
                                    saved_count += 1
                            except Exception as e:
                                print(f"    ⚠️  保存K线数据时出错: {e}")
                                continue
                        
                        conn.commit()
                        total_saved += saved_count
                        print(f"    ✓ K线数据 ({timeframe}): 保存 {saved_count} 条")
                    else:
                        print(f"    ⊗ K线数据 ({timeframe}): 无数据")
                    
                except Exception as e:
                    error_msg = f"{symbol} K线数据({timeframe})采集失败: {e}"
                    print(f"    ❌ {error_msg}")
                    errors.append(error_msg)
            
            # 3. 采集合约数据（仅Binance，不包括HYPE/USDT）
            if binance_futures_collector and not use_gate:
                try:
                    print(f"  📊 采集合约数据...")
                    # 合约数据采集逻辑（如果需要）
                    # 这里可以添加合约数据采集
                    pass
                except Exception as e:
                    error_msg = f"{symbol} 合约数据采集失败: {e}"
                    print(f"    ⚠️  {error_msg}")
            
            # 延迟避免请求过快
            await asyncio.sleep(0.5)
            
        except Exception as e:
            error_msg = f"{symbol} 采集失败: {e}"
            print(f"  ❌ {error_msg}")
            errors.append(error_msg)
            import traceback
            logger.error(traceback.format_exc())
    
    # 关闭数据库连接
    cursor.close()
    conn.close()
    
    # 输出统计信息
    print(f"\n{'='*80}")
    print(f"✅ 补采完成")
    print(f"总保存记录数: {total_saved:,}")
    if errors:
        print(f"错误数量: {len(errors)}")
        print(f"\n错误列表（前10个）:")
        for error in errors[:10]:
            print(f"  - {error}")
    print(f"{'='*80}\n")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='补采数据脚本 - 从指定小时到现在')
    parser.add_argument('--hour', type=int, default=13, help='开始的小时数（默认13，即13:00）')
    args = parser.parse_args()
    
    # 运行补采
    asyncio.run(collect_backfill_data(start_hour=args.hour))


if __name__ == '__main__':
    main()

