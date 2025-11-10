#!/usr/bin/env python3
"""
诊断缓存不更新的原因
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
import pymysql
from datetime import datetime, timedelta
from loguru import logger

def get_db_config():
    """获取数据库配置"""
    config_path = project_root / "config.yaml"
    if config_path.exists():
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config.get('database', {}).get('mysql', {})
    return {}

def get_db_connection():
    """获取数据库连接"""
    db_config = get_db_config()
    return pymysql.connect(
        host=db_config.get('host', 'localhost'),
        port=db_config.get('port', 3306),
        user=db_config.get('user', 'root'),
        password=db_config.get('password', ''),
        database=db_config.get('database', 'binance-data'),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def diagnose_cache_update(symbol):
    """诊断缓存不更新的原因"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        print("\n" + "="*80)
        print(f"诊断 {symbol} 缓存不更新的原因")
        print("="*80)
        
        # 1. 检查配置文件中是否包含该交易对
        config_path = project_root / "config.yaml"
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        symbols = config.get('symbols', [])
        
        if symbol in symbols:
            print(f"\n[OK] {symbol} 在配置文件中")
        else:
            print(f"\n[ERROR] {symbol} 不在配置文件中！")
            print(f"  这是导致缓存不更新的主要原因")
            print(f"  调度器只会更新配置文件中列出的交易对")
            return
        
        # 2. 检查缓存表的最新更新时间
        cursor.execute("""
            SELECT updated_at
            FROM price_stats_24h
            WHERE symbol = %s
        """, (symbol,))
        cache_result = cursor.fetchone()
        
        if cache_result:
            cache_time = cache_result['updated_at']
            time_diff = datetime.now() - cache_time
            minutes_ago = int(time_diff.total_seconds() / 60)
            
            print(f"\n[INFO] 缓存表最新更新时间: {cache_time}")
            print(f"  距离现在: {minutes_ago} 分钟前")
            
            if minutes_ago > 5:
                print(f"  [WARN] 缓存超过5分钟未更新，可能的原因:")
                print(f"    1. 调度器没有运行")
                print(f"    2. 缓存更新任务遇到错误")
                print(f"    3. 数据条件不满足导致跳过")
            else:
                print(f"  [OK] 缓存最近已更新")
        else:
            print(f"\n[ERROR] 缓存表中没有 {symbol} 的数据")
            print(f"  可能的原因:")
            print(f"    1. 缓存从未更新过")
            print(f"    2. 更新时数据条件不满足被跳过")
        
        # 3. 检查数据条件
        print(f"\n检查数据条件:")
        print("-" * 80)
        
        # 3.1 检查1分钟K线
        cursor.execute("""
            SELECT COUNT(*) as count, MAX(timestamp) as latest
            FROM kline_data
            WHERE symbol = %s AND timeframe = '1m'
        """, (symbol,))
        kline_1m = cursor.fetchone()
        
        if kline_1m['count'] > 0:
            latest_time = kline_1m['latest']
            time_diff = datetime.now() - latest_time
            hours_ago = time_diff.total_seconds() / 3600
            print(f"  [OK] 1分钟K线: {kline_1m['count']} 条, 最新: {latest_time}")
            if hours_ago > 1:
                print(f"  [WARN] 最新数据是 {hours_ago:.1f} 小时前，可能过时")
        else:
            print(f"  [ERROR] 没有1分钟K线数据 - 缓存更新会跳过")
        
        # 3.2 检查24小时内的5分钟K线
        start_time = datetime.now() - timedelta(hours=24)
        cursor.execute("""
            SELECT COUNT(*) as count
            FROM kline_data
            WHERE symbol = %s 
            AND timeframe = '5m'
            AND timestamp >= %s
        """, (symbol, start_time))
        kline_5m_24h = cursor.fetchone()
        
        if kline_5m_24h['count'] >= 10:
            print(f"  [OK] 24小时内5分钟K线: {kline_5m_24h['count']} 条 (足够)")
        elif kline_5m_24h['count'] > 0:
            print(f"  [WARN] 24小时内5分钟K线: {kline_5m_24h['count']} 条 (较少，但应该可以更新)")
        else:
            print(f"  [ERROR] 24小时内没有5分钟K线数据 - 旧版本会跳过更新")
            print(f"    新版本已优化，会尝试使用可用数据或默认值")
        
        # 4. 检查是否有错误日志（通过检查缓存表更新时间）
        print(f"\n建议:")
        print("-" * 80)
        if not cache_result or (cache_result and (datetime.now() - cache_result['updated_at']).total_seconds() > 300):
            print(f"  1. 检查调度器是否正在运行")
            print(f"  2. 查看日志文件，检查是否有缓存更新相关的错误")
            print(f"  3. 手动运行缓存更新: python scripts/manual_update_cache.py --price")
            print(f"  4. 确保 {symbol} 的K线数据持续采集")
        
        print("\n" + "="*80)
        
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    diagnose_cache_update('AR/USDT')

