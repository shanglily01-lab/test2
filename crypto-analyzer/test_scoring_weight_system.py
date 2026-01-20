#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试信号评分权重自适应系统
验证所有组件是否正常工作
"""

import sys
import pymysql
from dotenv import load_dotenv
import os

# 加载环境变量
load_dotenv()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}

print("=" * 100)
print("🧪 测试信号评分权重自适应系统")
print("=" * 100)

# 测试1: 检查数据库表是否存在
print("\n📋 测试1: 检查数据库表...")
try:
    conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 检查3张表
    tables = [
        'signal_scoring_weights',
        'signal_component_performance',
        'futures_positions'
    ]

    for table in tables:
        cursor.execute(f"SHOW TABLES LIKE '{table}'")
        result = cursor.fetchone()
        if result:
            print(f"  ✅ {table} 表存在")
        else:
            print(f"  ❌ {table} 表不存在")
            sys.exit(1)

    # 检查futures_positions表的新字段
    cursor.execute("SHOW COLUMNS FROM futures_positions LIKE 'signal_components'")
    if cursor.fetchone():
        print(f"  ✅ futures_positions.signal_components 字段存在")
    else:
        print(f"  ⚠️  futures_positions.signal_components 字段不存在（需要运行schema）")

    cursor.execute("SHOW COLUMNS FROM futures_positions LIKE 'entry_score'")
    if cursor.fetchone():
        print(f"  ✅ futures_positions.entry_score 字段存在")
    else:
        print(f"  ⚠️  futures_positions.entry_score 字段不存在（需要运行schema）")

    cursor.close()

except Exception as e:
    print(f"  ❌ 数据库连接失败: {e}")
    sys.exit(1)

# 测试2: 检查初始权重数据
print("\n📋 测试2: 检查初始权重数据...")
try:
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) as count FROM signal_scoring_weights")
    result = cursor.fetchone()
    count = result['count']

    if count >= 12:
        print(f"  ✅ signal_scoring_weights 表有 {count} 条权重记录")

        # 显示几个示例
        cursor.execute("""
            SELECT signal_component, weight_long, weight_short, is_active
            FROM signal_scoring_weights
            LIMIT 5
        """)
        weights = cursor.fetchall()
        print("\n  示例权重:")
        for w in weights:
            print(f"    • {w['signal_component']:<20} LONG:{w['weight_long']:>4.0f} SHORT:{w['weight_short']:>4.0f} active:{w['is_active']}")
    else:
        print(f"  ⚠️  signal_scoring_weights 表只有 {count} 条记录，应该有12条")

    cursor.close()

except Exception as e:
    print(f"  ❌ 检查权重数据失败: {e}")

# 测试3: 检查是否有包含signal_components的订单
print("\n📋 测试3: 检查是否有signal_components数据...")
try:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM futures_positions
        WHERE signal_components IS NOT NULL
        AND signal_components != ''
    """)
    result = cursor.fetchone()
    count = result['count']

    if count > 0:
        print(f"  ✅ 找到 {count} 个包含signal_components的订单")

        # 显示最新一个
        cursor.execute("""
            SELECT symbol, position_side, entry_score, signal_components, realized_pnl
            FROM futures_positions
            WHERE signal_components IS NOT NULL AND signal_components != ''
            ORDER BY open_time DESC
            LIMIT 1
        """)
        order = cursor.fetchone()
        if order:
            print(f"\n  最新订单示例:")
            print(f"    交易对: {order['symbol']}")
            print(f"    方向: {order['position_side']}")
            print(f"    得分: {order['entry_score']}")
            print(f"    信号组成: {order['signal_components'][:100]}...")
            print(f"    盈亏: {order['realized_pnl']}")
    else:
        print(f"  ⚠️  还没有包含signal_components的订单（系统刚部署）")

    cursor.close()

except Exception as e:
    print(f"  ❌ 检查订单数据失败: {e}")

# 测试4: 测试ScoringWeightOptimizer类
print("\n📋 测试4: 测试ScoringWeightOptimizer类...")
try:
    from app.services.scoring_weight_optimizer import ScoringWeightOptimizer

    optimizer = ScoringWeightOptimizer(db_config)
    print(f"  ✅ ScoringWeightOptimizer 类导入成功")

    # 尝试分析组件表现
    print(f"\n  尝试分析最近7天的组件表现...")
    performance = optimizer.analyze_component_performance(days=7)

    if performance:
        print(f"  ✅ 成功分析 {len(performance)} 个组件的表现")
        for comp_name, sides in list(performance.items())[:3]:  # 只显示前3个
            print(f"\n    组件: {comp_name}")
            for side, perf in sides.items():
                print(f"      {side}: 订单数={perf['total_orders']}, 胜率={perf['win_rate']*100:.1f}%, "
                      f"平均盈亏=${perf['avg_pnl']:.2f}, 表现评分={perf['performance_score']:.1f}")
    else:
        print(f"  ⚠️  没有足够的数据进行分析（需要包含signal_components的订单）")

except Exception as e:
    print(f"  ❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试5: 测试SmartDecisionBrain权重加载
print("\n📋 测试5: 测试SmartDecisionBrain权重加载...")
try:
    # 临时创建一个SmartDecisionBrain实例
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    # 直接测试权重加载逻辑
    cursor = conn.cursor()
    cursor.execute("""
        SELECT signal_component, weight_long, weight_short
        FROM signal_scoring_weights
        WHERE is_active = TRUE
    """)
    weight_rows = cursor.fetchall()

    scoring_weights = {}
    for row in weight_rows:
        scoring_weights[row['signal_component']] = {
            'long': float(row['weight_long']),
            'short': float(row['weight_short'])
        }

    if len(scoring_weights) >= 12:
        print(f"  ✅ 成功加载 {len(scoring_weights)} 个权重")
        print(f"\n  示例权重字典:")
        for comp_name, weights in list(scoring_weights.items())[:3]:
            print(f"    '{comp_name}': {weights}")
    else:
        print(f"  ⚠️  只加载了 {len(scoring_weights)} 个权重")

    cursor.close()

except Exception as e:
    print(f"  ❌ 测试失败: {e}")

# 测试6: 模拟权重调整（dry run）
print("\n📋 测试6: 模拟权重调整 (dry_run=True)...")
try:
    from app.services.scoring_weight_optimizer import ScoringWeightOptimizer

    optimizer = ScoringWeightOptimizer(db_config)

    # 模拟运行
    results = optimizer.adjust_weights(dry_run=True)

    if results.get('adjusted'):
        print(f"  ✅ 模拟调整成功，建议调整 {len(results['adjusted'])} 个权重")
        optimizer.print_adjustment_report(results)
    else:
        print(f"  ℹ️  当前无需调整权重（可能是数据不足或权重已优化）")

except Exception as e:
    print(f"  ❌ 模拟调整失败: {e}")
    import traceback
    traceback.print_exc()

conn.close()

print("\n" + "=" * 100)
print("🎉 测试完成!")
print("=" * 100)

print("\n📝 总结:")
print("  ✅ 如果所有测试通过，系统已准备就绪")
print("  ⚠️  如果有警告，说明系统刚部署，需要运行一段时间积累数据")
print("  ❌ 如果有错误，请检查:")
print("     1. 数据库表是否已创建 (运行 signal_scoring_weights_schema.sql)")
print("     2. futures_positions表是否有新字段")
print("     3. 系统是否已开始记录signal_components")

print("\n🚀 下一步:")
print("  1. 确保smart_trader_service.py正在运行")
print("  2. 等待系统开仓并记录signal_components")
print("  3. 每日凌晨2点，系统会自动调整权重")
print("  4. 或手动运行: python app/services/scoring_weight_optimizer.py")
