"""
数据库schema验证脚本
验证所有表的字段是否与代码中使用的字段匹配
"""
import pymysql
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}

def print_header(title):
    """打印标题"""
    print("\n" + "=" * 100)
    print(title)
    print("=" * 100)

def test_table_exists(cursor, table_name):
    """测试表是否存在"""
    cursor.execute(f"SHOW TABLES LIKE '{table_name}';")
    result = cursor.fetchone()
    if result:
        print(f"✅ 表 {table_name} 存在")
        return True
    else:
        print(f"❌ 表 {table_name} 不存在")
        return False

def get_table_columns(cursor, table_name):
    """获取表的所有列"""
    cursor.execute(f"DESCRIBE {table_name};")
    columns = cursor.fetchall()
    return {col['Field']: col['Type'] for col in columns}

def test_futures_positions_table(cursor):
    """测试 futures_positions 表"""
    print_header("测试 futures_positions 表")

    if not test_table_exists(cursor, 'futures_positions'):
        return False

    columns = get_table_columns(cursor, 'futures_positions')

    # 检查代码中使用的字段
    required_fields = {
        'id': '主键',
        'symbol': '交易对',
        'position_side': '方向(LONG/SHORT)',
        'quantity': '数量',
        'entry_price': '开仓价',
        'mark_price': '标记价格/平仓价',
        'realized_pnl': '已实现盈亏',
        'unrealized_pnl': '未实现盈亏',
        'entry_signal_type': '入场信号类型',
        'open_time': '开仓时间',
        'close_time': '平仓时间',
        'status': '状态(open/closed)'
    }

    print("\n检查必需字段:")
    all_ok = True
    for field, desc in required_fields.items():
        if field in columns:
            print(f"  ✅ {field:<20} ({desc}): {columns[field]}")
        else:
            print(f"  ❌ {field:<20} ({desc}): 缺失!")
            all_ok = False

    # 检查是否有 entry_time 字段（这是常见的错误）
    if 'entry_time' in columns:
        print(f"\n  ⚠️  警告: 发现 entry_time 字段，代码中应使用 open_time")

    return all_ok

def test_adaptive_params_table(cursor):
    """测试 adaptive_params 表"""
    print_header("测试 adaptive_params 表")

    if not test_table_exists(cursor, 'adaptive_params'):
        return False

    columns = get_table_columns(cursor, 'adaptive_params')

    required_fields = {
        'id': '主键',
        'param_key': '参数键名',
        'param_value': '参数值',
        'param_type': '参数类型(long/short)',
        'description': '描述',
        'updated_at': '更新时间',
        'updated_by': '更新者'
    }

    print("\n检查必需字段:")
    all_ok = True
    for field, desc in required_fields.items():
        if field in columns:
            print(f"  ✅ {field:<20} ({desc}): {columns[field]}")
        else:
            print(f"  ❌ {field:<20} ({desc}): 缺失!")
            all_ok = False

    # 检查数据
    cursor.execute("SELECT COUNT(*) as count FROM adaptive_params;")
    count = cursor.fetchone()['count']
    print(f"\n当前记录数: {count}")

    if count == 8:
        print("  ✅ 参数数量正确 (应为8个: 4个LONG + 4个SHORT)")
    else:
        print(f"  ⚠️  参数数量异常 (当前{count}个, 预期8个)")

    # 显示所有参数
    cursor.execute("""
        SELECT param_key, param_value, param_type, description
        FROM adaptive_params
        ORDER BY param_type, param_key;
    """)
    params = cursor.fetchall()

    if params:
        print("\n当前参数列表:")
        for param in params:
            print(f"  {param['param_type']:<6} | {param['param_key']:<30} = {param['param_value']:<10} ({param['description']})")

    return all_ok

def test_trading_blacklist_table(cursor):
    """测试 trading_blacklist 表"""
    print_header("测试 trading_blacklist 表")

    if not test_table_exists(cursor, 'trading_blacklist'):
        return False

    columns = get_table_columns(cursor, 'trading_blacklist')

    required_fields = {
        'id': '主键',
        'symbol': '交易对',
        'reason': '加入原因',
        'total_loss': '总亏损',
        'win_rate': '胜率',
        'order_count': '订单数',
        'created_at': '创建时间',
        'updated_at': '更新时间',
        'is_active': '是否激活'
    }

    print("\n检查必需字段:")
    all_ok = True
    for field, desc in required_fields.items():
        if field in columns:
            print(f"  ✅ {field:<20} ({desc}): {columns[field]}")
        else:
            print(f"  ❌ {field:<20} ({desc}): 缺失!")
            all_ok = False

    # 检查数据
    cursor.execute("SELECT COUNT(*) as count FROM trading_blacklist WHERE is_active = TRUE;")
    count = cursor.fetchone()['count']
    print(f"\n当前激活的黑名单数量: {count}")

    # 显示黑名单
    cursor.execute("""
        SELECT symbol, reason, total_loss, is_active
        FROM trading_blacklist
        ORDER BY total_loss ASC;
    """)
    blacklist = cursor.fetchall()

    if blacklist:
        print("\n黑名单列表:")
        for item in blacklist:
            status = "✅" if item['is_active'] else "❌"
            print(f"  {status} {item['symbol']:<15} | 亏损: ${item['total_loss']:<10.2f} | {item['reason']}")
    else:
        print("  ⚠️  黑名单为空")

    return all_ok

def test_optimization_history_table(cursor):
    """测试 optimization_history 表"""
    print_header("测试 optimization_history 表")

    if not test_table_exists(cursor, 'optimization_history'):
        return False

    columns = get_table_columns(cursor, 'optimization_history')

    required_fields = {
        'id': '主键',
        'optimization_date': '优化日期',
        'analysis_hours': '分析时间范围',
        'blacklist_added': '新增黑名单数',
        'params_updated': '更新参数数',
        'high_severity_issues': '高严重性问题数',
        'report_summary': '报告摘要',
        'created_at': '创建时间'
    }

    print("\n检查必需字段:")
    all_ok = True
    for field, desc in required_fields.items():
        if field in columns:
            print(f"  ✅ {field:<25} ({desc}): {columns[field]}")
        else:
            print(f"  ❌ {field:<25} ({desc}): 缺失!")
            all_ok = False

    # 检查历史记录
    cursor.execute("SELECT COUNT(*) as count FROM optimization_history;")
    count = cursor.fetchone()['count']
    print(f"\n优化历史记录数: {count}")

    if count > 0:
        cursor.execute("""
            SELECT optimization_date, blacklist_added, params_updated, high_severity_issues, created_at
            FROM optimization_history
            ORDER BY created_at DESC
            LIMIT 5;
        """)
        history = cursor.fetchall()
        print("\n最近的优化记录:")
        for record in history:
            print(f"  {record['optimization_date']} | 黑名单+{record['blacklist_added']} | 参数更新{record['params_updated']} | 严重问题{record['high_severity_issues']}")

    return all_ok

def test_code_field_usage(cursor):
    """测试代码中使用的字段"""
    print_header("测试代码字段使用兼容性")

    # 测试 SmartDecisionBrain 加载配置的查询
    print("\n测试1: SmartDecisionBrain 加载黑名单")
    try:
        cursor.execute("""
            SELECT symbol FROM trading_blacklist
            WHERE is_active = TRUE
            ORDER BY created_at DESC
        """)
        blacklist = cursor.fetchall()
        print(f"  ✅ 查询成功，黑名单数量: {len(blacklist)}")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

    # 测试 SmartDecisionBrain 加载参数的查询
    print("\n测试2: SmartDecisionBrain 加载LONG参数")
    try:
        cursor.execute("""
            SELECT param_key, param_value
            FROM adaptive_params
            WHERE param_type = 'long'
        """)
        params = cursor.fetchall()
        print(f"  ✅ 查询成功，LONG参数数量: {len(params)}")
        for param in params:
            print(f"     {param['param_key']}: {param['param_value']}")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

    # 测试 analyze_trading_performance.py 的查询
    print("\n测试3: analyze_trading_performance 查询当前持仓")
    try:
        cursor.execute("""
            SELECT
                id,
                symbol,
                position_side,
                quantity,
                entry_price,
                unrealized_pnl,
                open_time,
                TIMESTAMPDIFF(MINUTE, open_time, NOW()) as hold_minutes
            FROM futures_positions
            WHERE status = 'open'
            ORDER BY open_time DESC
            LIMIT 1
        """)
        result = cursor.fetchone()
        if result:
            print(f"  ✅ 查询成功，示例持仓: {result['symbol']} {result['position_side']}")
        else:
            print(f"  ✅ 查询成功，当前无持仓")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

    # 测试 adaptive_optimizer 更新参数的查询
    print("\n测试4: AdaptiveOptimizer 更新参数 (模拟)")
    try:
        # 只测试查询，不实际更新
        cursor.execute("""
            SELECT param_key, param_value
            FROM adaptive_params
            WHERE param_key = 'long_stop_loss_pct'
        """)
        result = cursor.fetchone()
        if result:
            print(f"  ✅ 查询成功，当前止损: {result['param_value']}")
        else:
            print(f"  ❌ 未找到参数 long_stop_loss_pct")
    except Exception as e:
        print(f"  ❌ 查询失败: {e}")

def main():
    """主函数"""
    print("=" * 100)
    print("数据库Schema验证脚本")
    print("=" * 100)

    # 连接数据库
    print("\n连接数据库...")
    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        print("✅ 数据库连接成功")
    except Exception as e:
        print(f"❌ 数据库连接失败: {e}")
        return

    try:
        # 测试所有表
        results = {
            'futures_positions': test_futures_positions_table(cursor),
            'adaptive_params': test_adaptive_params_table(cursor),
            'trading_blacklist': test_trading_blacklist_table(cursor),
            'optimization_history': test_optimization_history_table(cursor)
        }

        # 测试代码字段使用
        test_code_field_usage(cursor)

        # 总结
        print_header("验证总结")

        all_passed = all(results.values())

        for table, passed in results.items():
            status = "✅ 通过" if passed else "❌ 失败"
            print(f"{status} - {table}")

        if all_passed:
            print("\n" + "=" * 100)
            print("🎉 所有验证通过！数据库schema与代码完全兼容")
            print("=" * 100)
        else:
            print("\n" + "=" * 100)
            print("⚠️  部分验证失败，请检查上述错误信息")
            print("=" * 100)

    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()
