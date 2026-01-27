"""
修复卡住的分批建仓持仓

检测逻辑:
- status = 'building' 且已超过最后一个批次的超时时间
- 将这些持仓标记为需要处理
"""
import pymysql
import os
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

def get_db_connection():
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def analyze_stuck_positions():
    """分析卡住的建仓持仓"""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 查询所有building状态的持仓
    cursor.execute('''
        SELECT
            id,
            symbol,
            position_side,
            batch_plan,
            batch_filled,
            created_at,
            TIMESTAMPDIFF(MINUTE, created_at, NOW()) as minutes_elapsed
        FROM futures_positions
        WHERE account_id = 2
        AND status = 'building'
        ORDER BY created_at DESC
    ''')

    building_positions = cursor.fetchall()

    print('=' * 80)
    print('Checking building positions...')
    print('=' * 80)

    stuck_positions = []
    normal_positions = []

    for pos in building_positions:
        batch_plan = json.loads(pos['batch_plan']) if pos['batch_plan'] else None
        batch_filled = json.loads(pos['batch_filled']) if pos['batch_filled'] else None

        if not batch_plan or not batch_filled:
            continue

        total_batches = len(batch_plan['batches'])
        filled_count = len(batch_filled['batches'])

        # 获取最后一个批次的信息
        last_batch = batch_filled['batches'][-1]
        last_batch_num = last_batch['batch_num']
        last_batch_time = datetime.fromisoformat(last_batch['time'])
        minutes_since_last_batch = (datetime.now() - last_batch_time).total_seconds() / 60

        # 计算下一批次应该在什么时候执行
        next_batch_num = last_batch_num + 1
        if next_batch_num < total_batches:
            next_batch_timeout = batch_plan['batches'][next_batch_num]['timeout_minutes']
            # 超过超时时间+5分钟容错,认为是卡住了
            is_stuck = minutes_since_last_batch > (next_batch_timeout + 5)

            status_info = {
                'id': pos['id'],
                'symbol': pos['symbol'],
                'side': pos['position_side'],
                'progress': f'{filled_count}/{total_batches}',
                'last_batch': last_batch_num,
                'next_batch': next_batch_num,
                'next_timeout': next_batch_timeout,
                'minutes_since_last': minutes_since_last_batch,
                'is_stuck': is_stuck
            }

            if is_stuck:
                stuck_positions.append(status_info)
            else:
                normal_positions.append(status_info)

    # 打印正常等待的持仓
    if normal_positions:
        print('\n[OK] Normal waiting positions:')
        print('-' * 80)
        for info in normal_positions:
            remaining = info['next_timeout'] - info['minutes_since_last']
            print(f"ID {info['id']}: {info['symbol']} {info['side']} | "
                  f"Progress: {info['progress']} | "
                  f"Wait: {remaining:.1f} min remaining")

    # 打印卡住的持仓
    if stuck_positions:
        print('\n[WARNING] Stuck positions detected:')
        print('-' * 80)
        for info in stuck_positions:
            overtime = info['minutes_since_last'] - info['next_timeout']
            print(f"ID {info['id']}: {info['symbol']} {info['side']} | "
                  f"Progress: {info['progress']} | "
                  f"STUCK: overtime by {overtime:.1f} minutes")
    else:
        print('\n[OK] No stuck positions found')

    cursor.close()
    conn.close()

    return stuck_positions

def fix_stuck_positions(stuck_positions, action='mark_open'):
    """
    修复卡住的持仓
    action:
        - 'mark_open': 将状态改为open(已完成的批次保留)
        - 'close': 平仓(释放资金)
    """
    if not stuck_positions:
        print('\nNo positions to fix.')
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    print('\n' + '=' * 80)
    print(f'Fixing {len(stuck_positions)} stuck positions...')
    print('=' * 80)

    for info in stuck_positions:
        position_id = info['id']

        if action == 'mark_open':
            # 将状态改为open,保留已完成的批次
            cursor.execute('''
                UPDATE futures_positions
                SET status = 'open',
                    notes = CONCAT(COALESCE(notes, ''), ' [自动修复] 分批建仓未完成,已开仓批次保留')
                WHERE id = %s
            ''', (position_id,))

            print(f"[FIXED] Position {position_id} ({info['symbol']} {info['side']}) "
                  f"marked as OPEN with {info['progress']} batches completed")

        elif action == 'close':
            # 平仓
            cursor.execute('''
                UPDATE futures_positions
                SET status = 'closed',
                    close_time = NOW(),
                    notes = CONCAT(COALESCE(notes, ''), ' [自动关闭] 分批建仓卡住,已自动平仓')
                WHERE id = %s
            ''', (position_id,))

            print(f"[CLOSED] Position {position_id} ({info['symbol']} {info['side']}) closed")

    cursor.close()
    conn.close()

    print('\nAll stuck positions have been fixed.')

if __name__ == '__main__':
    print('Analyzing stuck batch entry positions...\n')

    stuck = analyze_stuck_positions()

    if stuck:
        print('\n' + '=' * 80)
        print('What would you like to do?')
        print('1. Mark as OPEN (keep filled batches, continue trading)')
        print('2. Close positions (free up margin)')
        print('3. Do nothing')
        print('=' * 80)

        choice = input('Enter choice (1/2/3): ').strip()

        if choice == '1':
            fix_stuck_positions(stuck, action='mark_open')
        elif choice == '2':
            fix_stuck_positions(stuck, action='close')
        else:
            print('\nNo action taken.')

    print('\nDone!')
