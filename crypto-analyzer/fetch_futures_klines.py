"""
拉取所有交易对的合约K线数据
支持时间周期: 1m, 5m, 15m, 1h, 1d
"""
import ccxt
import pymysql
import yaml
from datetime import datetime, timedelta
import time
import sys
from typing import List, Dict

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# 获取交易对列表
symbols = config.get('symbols', [])
timeframes = ['1m', '5m', '15m', '1h', '1d']

print(f'=' * 100)
print(f'合约K线数据拉取工具')
print(f'=' * 100)
print(f'交易对数量: {len(symbols)}')
print(f'时间周期: {", ".join(timeframes)}')
print(f'=' * 100)

# 初始化币安合约交易所
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # 使用合约市场
    }
})

# 数据库连接
db = pymysql.connect(
    host='13.212.252.171',
    port=3306,
    user='admin',
    password='Tonny@1000',
    database='binance-data',
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

cursor = db.cursor()

# 确保kline_data表存在
cursor.execute("""
    CREATE TABLE IF NOT EXISTS kline_data (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        symbol VARCHAR(20) NOT NULL,
        timeframe VARCHAR(10) NOT NULL,
        open_time DATETIME NOT NULL,
        open DECIMAL(20, 8) NOT NULL,
        high DECIMAL(20, 8) NOT NULL,
        low DECIMAL(20, 8) NOT NULL,
        close DECIMAL(20, 8) NOT NULL,
        volume DECIMAL(30, 8) NOT NULL,
        close_time DATETIME NOT NULL,
        quote_volume DECIMAL(30, 8),
        trades INT,
        taker_buy_base DECIMAL(30, 8),
        taker_buy_quote DECIMAL(30, 8),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_kline (symbol, timeframe, open_time),
        INDEX idx_symbol_timeframe (symbol, timeframe),
        INDEX idx_open_time (open_time)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
""")
db.commit()

def fetch_klines(symbol: str, timeframe: str, days: int = 7) -> List[List]:
    """
    拉取K线数据

    Args:
        symbol: 交易对 (如 BTC/USDT)
        timeframe: 时间周期 (1m, 5m, 15m, 1h, 1d)
        days: 拉取天数

    Returns:
        K线数据列表
    """
    try:
        # 计算起始时间
        since = exchange.parse8601((datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S'))

        # 拉取K线数据
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)

        return ohlcv
    except Exception as e:
        print(f"  ❌ 拉取失败: {e}")
        return []

def save_klines(symbol: str, timeframe: str, ohlcv: List[List]) -> int:
    """
    保存K线数据到数据库

    Args:
        symbol: 交易对
        timeframe: 时间周期
        ohlcv: K线数据 [[timestamp, open, high, low, close, volume], ...]

    Returns:
        插入/更新的记录数
    """
    if not ohlcv:
        return 0

    inserted = 0
    updated = 0

    for candle in ohlcv:
        timestamp = candle[0]
        open_price = candle[1]
        high = candle[2]
        low = candle[3]
        close = candle[4]
        volume = candle[5]

        # 转换时间戳为datetime
        open_time = datetime.fromtimestamp(timestamp / 1000)

        # 计算close_time (根据timeframe)
        timeframe_minutes = {
            '1m': 1,
            '5m': 5,
            '15m': 15,
            '1h': 60,
            '1d': 1440
        }
        minutes = timeframe_minutes.get(timeframe, 1)
        close_time = open_time + timedelta(minutes=minutes)

        try:
            # 使用 INSERT ... ON DUPLICATE KEY UPDATE
            cursor.execute("""
                INSERT INTO kline_data
                (symbol, timeframe, open_time, open, high, low, close, volume, close_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    open = VALUES(open),
                    high = VALUES(high),
                    low = VALUES(low),
                    close = VALUES(close),
                    volume = VALUES(volume),
                    close_time = VALUES(close_time)
            """, (symbol, timeframe, open_time, open_price, high, low, close, volume, close_time))

            if cursor.rowcount == 1:
                inserted += 1
            elif cursor.rowcount == 2:
                updated += 1
        except Exception as e:
            print(f"  ⚠️ 保存K线失败 [{open_time}]: {e}")
            continue

    db.commit()
    return inserted + updated

def main():
    """主函数"""
    total_symbols = len(symbols)
    total_timeframes = len(timeframes)

    print(f'\n开始拉取数据...\n')

    success_count = 0
    fail_count = 0
    total_klines = 0

    for idx, symbol in enumerate(symbols, 1):
        print(f'\n[{idx}/{total_symbols}] {symbol}')
        print('-' * 80)

        symbol_success = True

        for timeframe in timeframes:
            try:
                # 拉取K线数据
                print(f'  拉取 {timeframe} K线...', end=' ')
                ohlcv = fetch_klines(symbol, timeframe, days=7)

                if ohlcv:
                    # 保存到数据库
                    saved = save_klines(symbol, timeframe, ohlcv)
                    total_klines += saved
                    print(f'✓ {len(ohlcv)}根K线 (保存{saved}条)')
                else:
                    print(f'❌ 无数据')
                    symbol_success = False

                # 避免请求过快
                time.sleep(0.1)

            except Exception as e:
                print(f'❌ 错误: {e}')
                symbol_success = False

        if symbol_success:
            success_count += 1
        else:
            fail_count += 1

    # 显示统计
    print('\n' + '=' * 100)
    print('拉取完成')
    print('=' * 100)
    print(f'成功: {success_count}/{total_symbols} 个交易对')
    print(f'失败: {fail_count}/{total_symbols} 个交易对')
    print(f'总K线数: {total_klines} 条')

    # 显示数据库统计
    print('\n数据库统计:')
    for timeframe in timeframes:
        cursor.execute("""
            SELECT COUNT(*) as count, MIN(open_time) as min_time, MAX(open_time) as max_time
            FROM kline_data
            WHERE timeframe = %s
        """, (timeframe,))

        result = cursor.fetchone()
        if result and result['count'] > 0:
            print(f"  {timeframe}: {result['count']}条 (时间范围: {result['min_time']} ~ {result['max_time']})")
        else:
            print(f"  {timeframe}: 0条")

    cursor.close()
    db.close()

    print('\n' + '=' * 100)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\n用户中断')
        cursor.close()
        db.close()
        sys.exit(0)
    except Exception as e:
        print(f'\n\n严重错误: {e}')
        import traceback
        traceback.print_exc()
        cursor.close()
        db.close()
        sys.exit(1)
