"""
增强版合约K线数据拉取工具
功能:
- 支持指定拉取天数
- 支持断点续传
- 支持多线程并发
- 显示进度条
"""
import ccxt
import pymysql
import yaml
from datetime import datetime, timedelta
import time
import sys
import argparse
from typing import List
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

symbols = config.get('symbols', [])
timeframes = ['1m', '5m', '15m', '1h', '1d']

# 线程锁
db_lock = Lock()
print_lock = Lock()

# 统计
stats = {
    'success': 0,
    'fail': 0,
    'total_klines': 0,
    'errors': []
}

def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host='13.212.252.171',
        port=3306,
        user='admin',
        password='Tonny@1000',
        database='binance-data',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

def init_database():
    """初始化数据库表"""
    db = get_db_connection()
    cursor = db.cursor()

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

    cursor.close()
    db.close()

def fetch_symbol_timeframe(symbol: str, timeframe: str, days: int) -> dict:
    """
    拉取单个交易对的单个时间周期数据

    Args:
        symbol: 交易对
        timeframe: 时间周期
        days: 拉取天数

    Returns:
        结果字典
    """
    result = {
        'symbol': symbol,
        'timeframe': timeframe,
        'success': False,
        'klines': 0,
        'saved': 0,
        'error': None
    }

    try:
        # 初始化交易所
        exchange = ccxt.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })

        # 计算起始时间
        since = exchange.parse8601(
            (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')
        )

        # 拉取K线数据
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=1000)

        if not ohlcv:
            result['error'] = '无数据'
            return result

        result['klines'] = len(ohlcv)

        # 保存到数据库
        db = get_db_connection()
        cursor = db.cursor()

        timeframe_minutes = {
            '1m': 1, '5m': 5, '15m': 15, '1h': 60, '1d': 1440
        }
        minutes = timeframe_minutes.get(timeframe, 1)

        saved = 0
        for candle in ohlcv:
            timestamp, open_p, high, low, close, volume = candle[:6]
            open_time = datetime.fromtimestamp(timestamp / 1000)
            close_time = open_time + timedelta(minutes=minutes)

            with db_lock:
                try:
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
                    """, (symbol, timeframe, open_time, open_p, high, low, close, volume, close_time))

                    saved += cursor.rowcount
                except Exception as e:
                    result['error'] = f'保存错误: {e}'

        db.commit()
        cursor.close()
        db.close()

        result['saved'] = saved
        result['success'] = True

    except Exception as e:
        result['error'] = str(e)

    return result

def print_progress(current: int, total: int, symbol: str, timeframe: str, status: str):
    """打印进度"""
    with print_lock:
        percent = current / total * 100
        bar_length = 40
        filled = int(bar_length * current / total)
        bar = '█' * filled + '░' * (bar_length - filled)

        print(f'\r[{bar}] {percent:5.1f}% | {current}/{total} | {symbol:<13} {timeframe:<4} {status}', end='', flush=True)

def main():
    parser = argparse.ArgumentParser(description='拉取合约K线数据')
    parser.add_argument('-d', '--days', type=int, default=7, help='拉取天数 (默认7天)')
    parser.add_argument('-t', '--threads', type=int, default=5, help='并发线程数 (默认5)')
    parser.add_argument('-s', '--symbols', nargs='+', help='指定交易对 (不指定则拉取全部)')
    parser.add_argument('--timeframes', nargs='+', choices=['1m', '5m', '15m', '1h', '1d'],
                        help='指定时间周期 (不指定则拉取全部)')

    args = parser.parse_args()

    # 确定要拉取的交易对和时间周期
    target_symbols = args.symbols if args.symbols else symbols
    target_timeframes = args.timeframes if args.timeframes else timeframes

    print('=' * 100)
    print('合约K线数据拉取工具 (增强版)')
    print('=' * 100)
    print(f'交易对: {len(target_symbols)}个')
    print(f'时间周期: {", ".join(target_timeframes)}')
    print(f'拉取天数: {args.days}天')
    print(f'并发线程: {args.threads}个')
    print('=' * 100)

    # 初始化数据库
    print('\n初始化数据库...')
    init_database()
    print('✓ 数据库初始化完成')

    # 生成任务列表
    tasks = []
    for symbol in target_symbols:
        for timeframe in target_timeframes:
            tasks.append((symbol, timeframe, args.days))

    total_tasks = len(tasks)
    completed = 0

    print(f'\n开始拉取 (总任务数: {total_tasks})\n')

    # 使用线程池执行
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        # 提交所有任务
        future_to_task = {
            executor.submit(fetch_symbol_timeframe, symbol, timeframe, days): (symbol, timeframe)
            for symbol, timeframe, days in tasks
        }

        # 处理完成的任务
        for future in as_completed(future_to_task):
            symbol, timeframe = future_to_task[future]
            completed += 1

            try:
                result = future.result()

                if result['success']:
                    status = f"✓ {result['klines']}根K线 (保存{result['saved']}条)"
                    stats['success'] += 1
                    stats['total_klines'] += result['saved']
                else:
                    status = f"✗ {result['error']}"
                    stats['fail'] += 1
                    stats['errors'].append(f"{symbol} {timeframe}: {result['error']}")

                print_progress(completed, total_tasks, symbol, timeframe, status)

            except Exception as e:
                status = f"✗ 异常: {e}"
                stats['fail'] += 1
                stats['errors'].append(f"{symbol} {timeframe}: {e}")
                print_progress(completed, total_tasks, symbol, timeframe, status)

    print('\n\n' + '=' * 100)
    print('拉取完成')
    print('=' * 100)
    print(f'成功: {stats["success"]}/{total_tasks} 个任务')
    print(f'失败: {stats["fail"]}/{total_tasks} 个任务')
    print(f'总K线数: {stats["total_klines"]} 条')

    if stats['errors']:
        print(f'\n错误列表 (前10个):')
        for error in stats['errors'][:10]:
            print(f'  - {error}')

    # 显示数据库统计
    print('\n数据库统计:')
    db = get_db_connection()
    cursor = db.cursor()

    for timeframe in target_timeframes:
        cursor.execute("""
            SELECT COUNT(*) as count, MIN(open_time) as min_time, MAX(open_time) as max_time
            FROM kline_data
            WHERE timeframe = %s
        """, (timeframe,))

        result = cursor.fetchone()
        if result and result['count'] > 0:
            print(f"  {timeframe}: {result['count']:>6}条 (时间范围: {result['min_time']} ~ {result['max_time']})")
        else:
            print(f"  {timeframe}:      0条")

    cursor.close()
    db.close()

    print('\n' + '=' * 100)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\n\n用户中断')
        sys.exit(0)
    except Exception as e:
        print(f'\n\n严重错误: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
