"""
分析V2和V3策略的分别表现
"""
import pymysql
import json
from datetime import datetime
from collections import defaultdict

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

# 1. 获取所有策略及其版本
cursor.execute('SELECT id, name, config FROM trading_strategies')
strategies = cursor.fetchall()

strategy_versions = {}
for s in strategies:
    config = json.loads(s['config'])
    version = config.get('strategyType', 'v2')
    strategy_versions[s['id']] = {
        'name': s['name'],
        'version': version
    }

print('=' * 100)
print('V2 vs V3 Strategy Performance Analysis')
print('=' * 100)

# 2. 分析持仓
cursor.execute("""
    SELECT
        symbol,
        position_side,
        strategy_id,
        entry_price,
        mark_price,
        realized_pnl,
        unrealized_pnl_pct,
        status,
        open_time,
        close_time,
        notes
    FROM futures_positions
    WHERE close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
    OR status = 'open'
    ORDER BY open_time DESC
""")

positions = cursor.fetchall()

# 按版本统计
v2_stats = {'count': 0, 'win': 0, 'loss': 0, 'total_pnl': 0, 'open': 0}
v3_stats = {'count': 0, 'win': 0, 'loss': 0, 'total_pnl': 0, 'open': 0}

v2_positions = []
v3_positions = []

for p in positions:
    strategy_id = p['strategy_id']
    if strategy_id not in strategy_versions:
        continue

    version = strategy_versions[strategy_id]['version']
    strategy_name = strategy_versions[strategy_id]['name']

    if p['status'] == 'open':
        if version == 'v3':
            v3_stats['open'] += 1
            v3_positions.append((p, strategy_name))
        else:
            v2_stats['open'] += 1
            v2_positions.append((p, strategy_name))
    else:
        pnl = float(p['realized_pnl'] or 0)

        if version == 'v3':
            v3_stats['count'] += 1
            v3_stats['total_pnl'] += pnl
            if pnl > 0:
                v3_stats['win'] += 1
            else:
                v3_stats['loss'] += 1
            v3_positions.append((p, strategy_name))
        else:
            v2_stats['count'] += 1
            v2_stats['total_pnl'] += pnl
            if pnl > 0:
                v2_stats['win'] += 1
            else:
                v2_stats['loss'] += 1
            v2_positions.append((p, strategy_name))

# 3. 显示统计
print('\n=== Closed Positions Summary ===')
print(f'{"Version":<10} {"Total":<8} {"Win":<6} {"Loss":<6} {"Win Rate":<10} {"Total PnL":<12}')
print('-' * 60)

if v2_stats['count'] > 0:
    v2_win_rate = v2_stats['win'] / v2_stats['count'] * 100
    print(f'{"V2":<10} {v2_stats["count"]:<8} {v2_stats["win"]:<6} {v2_stats["loss"]:<6} {v2_win_rate:>8.1f}% ${v2_stats["total_pnl"]:>10.2f}')

if v3_stats['count'] > 0:
    v3_win_rate = v3_stats['win'] / v3_stats['count'] * 100
    print(f'{"V3":<10} {v3_stats["count"]:<8} {v3_stats["win"]:<6} {v3_stats["loss"]:<6} {v3_win_rate:>8.1f}% ${v3_stats["total_pnl"]:>10.2f}')
else:
    print('V3        No closed positions yet')

# 4. 显示当前持仓
print('\n=== Current Open Positions ===')
print(f'{"Version":<8} {"Symbol":<12} {"Side":<6} {"Strategy":<20} {"Entry":<10} {"PnL%":<8}')
print('-' * 80)

for p, strategy_name in v2_positions[:10]:
    if p['status'] == 'open':
        pnl_pct = float(p['unrealized_pnl_pct'] or 0)
        print(f'{"V2":<8} {p["symbol"]:<12} {p["position_side"]:<6} {strategy_name:<20} ${float(p["entry_price"]):.4f} {pnl_pct:>6.2f}%')

for p, strategy_name in v3_positions:
    if p['status'] == 'open':
        pnl_pct = float(p['unrealized_pnl_pct'] or 0)
        print(f'{"V3":<8} {p["symbol"]:<12} {p["position_side"]:<6} {strategy_name:<20} ${float(p["entry_price"]):.4f} {pnl_pct:>6.2f}%')

# 5. 显示限价单
cursor.execute("""
    SELECT
        symbol,
        side,
        price,
        strategy_id,
        status,
        created_at
    FROM futures_orders
    WHERE status = 'PENDING'
    ORDER BY created_at DESC
    LIMIT 20
""")

orders = cursor.fetchall()

print('\n=== Pending Limit Orders ===')
print(f'{"Version":<8} {"Symbol":<12} {"Side":<15} {"Strategy":<20} {"Price":<12} {"Created":<20}')
print('-' * 100)

for o in orders:
    strategy_id = o['strategy_id']
    if strategy_id in strategy_versions:
        version = strategy_versions[strategy_id]['version'].upper()
        strategy_name = strategy_versions[strategy_id]['name']
        print(f'{version:<8} {o["symbol"]:<12} {o["side"]:<15} {strategy_name:<20} ${float(o["price"] or 0):.4f} {o["created_at"]}')

# 6. V3最近交易详情（如果有）
if v3_stats['count'] > 0 or v3_stats['open'] > 0:
    print('\n=== V3 Recent Trades (Last 10) ===')
    print(f'{"Symbol":<12} {"Side":<6} {"Status":<8} {"Entry":<10} {"PnL%":<8} {"PnL$":<10} {"Time":<20}')
    print('-' * 90)

    for p, strategy_name in v3_positions[:10]:
        if p['status'] == 'closed':
            pnl_pct = float(p['unrealized_pnl_pct'] or 0)
            pnl = float(p['realized_pnl'] or 0)
            time_str = p['close_time'].strftime('%Y-%m-%d %H:%M') if p['close_time'] else 'N/A'
        else:
            pnl_pct = float(p['unrealized_pnl_pct'] or 0)
            pnl = 0
            time_str = 'Open'

        print(f'{p["symbol"]:<12} {p["position_side"]:<6} {p["status"]:<8} ${float(p["entry_price"]):.4f} {pnl_pct:>6.2f}% ${pnl:>8.2f} {time_str:<20}')

cursor.close()
db.close()

print('\n' + '=' * 100)
print('Analysis Complete')
print('=' * 100)
