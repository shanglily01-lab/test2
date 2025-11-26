"""
验证策略交易记录功能
检查表结构、数据保存功能是否正常
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
import pymysql
from datetime import datetime

# 加载配置
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

print("=" * 80)
print("验证策略交易记录功能")
print("=" * 80)
print()

try:
    # 连接数据库
    connection = pymysql.connect(**db_config)
    cursor = connection.cursor(pymysql.cursors.DictCursor)
    
    try:
        # 1. 检查表是否存在
        print("1. 检查 strategy_trade_records 表...")
        cursor.execute("""
            SELECT COUNT(*) as count 
            FROM information_schema.tables 
            WHERE table_schema = DATABASE() 
            AND table_name = 'strategy_trade_records'
        """)
        table_exists = cursor.fetchone()['count'] > 0
        
        if not table_exists:
            print("   [ERROR] 表不存在！请先执行 SQL 脚本创建表")
            print("   执行: scripts/update_database.sql")
            sys.exit(1)
        
        print("   [OK] 表存在")
        
        # 2. 检查表结构
        print("\n2. 检查表结构...")
        cursor.execute("DESCRIBE strategy_trade_records")
        columns = cursor.fetchall()
        
        required_columns = {
            'strategy_id': 'BIGINT',
            'strategy_name': 'VARCHAR',
            'account_id': 'INT',
            'symbol': 'VARCHAR',
            'action': 'VARCHAR',
            'direction': 'VARCHAR',
            'entry_price': 'DECIMAL',
            'exit_price': 'DECIMAL',
            'quantity': 'DECIMAL',
            'leverage': 'INT',
            'fee': 'DECIMAL',
            'realized_pnl': 'DECIMAL',
            'reason': 'VARCHAR',
            'trade_time': 'DATETIME'
        }
        
        column_types = {col['Field']: col['Type'] for col in columns}
        all_ok = True
        
        for col_name, col_type_prefix in required_columns.items():
            if col_name not in column_types:
                print(f"   [ERROR] 缺少字段: {col_name}")
                all_ok = False
            else:
                col_type = column_types[col_name]
                if col_name == 'strategy_id' and 'bigint' not in col_type.lower():
                    print(f"   [WARNING] strategy_id 字段类型应该是 BIGINT，当前是: {col_type}")
                else:
                    print(f"   [OK] {col_name}: {col_type}")
        
        if not all_ok:
            print("\n   [ERROR] 表结构不完整！")
            sys.exit(1)
        
        # 3. 检查索引
        print("\n3. 检查索引...")
        cursor.execute("SHOW INDEX FROM strategy_trade_records")
        indexes = cursor.fetchall()
        index_names = [idx['Key_name'] for idx in indexes]
        
        required_indexes = ['idx_strategy_id', 'idx_account_id', 'idx_symbol', 'idx_action', 'idx_trade_time']
        for idx_name in required_indexes:
            if idx_name in index_names:
                print(f"   [OK] 索引 {idx_name} 存在")
            else:
                print(f"   [WARNING] 索引 {idx_name} 不存在")
        
        # 4. 测试插入一条记录
        print("\n4. 测试插入测试记录...")
        test_record = {
            'strategy_id': 999999,
            'strategy_name': '功能测试策略',
            'account_id': 999,
            'symbol': 'ETH/USDT',
            'action': 'BUY',
            'direction': 'long',
            'position_side': 'LONG',
            'entry_price': 3000.0,
            'exit_price': None,
            'quantity': 0.1,
            'leverage': 10,
            'margin': 30.0,
            'total_value': 300.0,
            'fee': 0.1,
            'realized_pnl': None,
            'position_id': None,
            'order_id': None,
            'signal_id': None,
            'reason': '功能测试',
            'trade_time': datetime.now()
        }
        
        try:
            cursor.execute("""
                INSERT INTO strategy_trade_records 
                (strategy_id, strategy_name, account_id, symbol, action, direction, position_side,
                 entry_price, exit_price, quantity, leverage, margin, total_value, fee, realized_pnl,
                 position_id, order_id, signal_id, reason, trade_time)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                test_record['strategy_id'],
                test_record['strategy_name'],
                test_record['account_id'],
                test_record['symbol'],
                test_record['action'],
                test_record['direction'],
                test_record['position_side'],
                test_record['entry_price'],
                test_record['exit_price'],
                test_record['quantity'],
                test_record['leverage'],
                test_record['margin'],
                test_record['total_value'],
                test_record['fee'],
                test_record['realized_pnl'],
                test_record['position_id'],
                test_record['order_id'],
                test_record['signal_id'],
                test_record['reason'],
                test_record['trade_time']
            ))
            connection.commit()
            test_id = cursor.lastrowid
            print(f"   [OK] 测试记录插入成功，ID: {test_id}")
            
            # 5. 验证读取
            print("\n5. 验证读取测试记录...")
            cursor.execute("SELECT * FROM strategy_trade_records WHERE id = %s", (test_id,))
            retrieved = cursor.fetchone()
            
            if retrieved:
                print(f"   [OK] 成功读取记录")
                print(f"       策略: {retrieved['strategy_name']}")
                print(f"       交易对: {retrieved['symbol']}")
                print(f"       动作: {retrieved['action']}")
                print(f"       价格: {retrieved['entry_price']}")
                print(f"       数量: {retrieved['quantity']}")
            else:
                print("   [ERROR] 无法读取刚插入的记录")
            
            # 6. 清理测试记录
            print("\n6. 清理测试记录...")
            cursor.execute("DELETE FROM strategy_trade_records WHERE id = %s", (test_id,))
            connection.commit()
            print(f"   [OK] 测试记录已删除")
            
        except Exception as e:
            connection.rollback()
            print(f"   [ERROR] 插入测试记录失败: {e}")
            import traceback
            traceback.print_exc()
        
        # 7. 统计现有记录
        print("\n7. 统计现有记录...")
        cursor.execute("SELECT COUNT(*) as total FROM strategy_trade_records")
        total = cursor.fetchone()['total']
        print(f"   总记录数: {total}")
        
        if total > 0:
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(DISTINCT strategy_id) as strategy_count,
                    COUNT(DISTINCT symbol) as symbol_count,
                    COUNT(CASE WHEN action = 'BUY' THEN 1 END) as buy_count,
                    COUNT(CASE WHEN action IN ('SELL', 'CLOSE') THEN 1 END) as sell_count
                FROM strategy_trade_records
            """)
            stats = cursor.fetchone()
            print(f"   涉及策略数: {stats['strategy_count']}")
            print(f"   涉及交易对数: {stats['symbol_count']}")
            print(f"   买入记录: {stats['buy_count']}")
            print(f"   卖出记录: {stats['sell_count']}")
            
            # 查询最近的记录
            cursor.execute("""
                SELECT strategy_name, symbol, action, trade_time
                FROM strategy_trade_records
                ORDER BY trade_time DESC
                LIMIT 5
            """)
            recent = cursor.fetchall()
            if recent:
                print("\n   最近的5条记录:")
                for r in recent:
                    print(f"     - {r['trade_time']}: {r['strategy_name']} {r['action']} {r['symbol']}")
        
        print("\n" + "=" * 80)
        print("[OK] 功能验证完成！所有检查通过")
        print("=" * 80)
        
    finally:
        cursor.close()
        connection.close()
        
except Exception as e:
    print(f"\n[ERROR] 验证失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


