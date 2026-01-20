#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试权重优化功能
验证优化器能否正常工作
"""

import sys
import pymysql
from dotenv import load_dotenv
import os
import json
from datetime import datetime, timedelta

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
print("🧪 测试权重优化功能")
print("=" * 100)

# 测试1: 检查是否有足够的数据
print("\n📋 测试1: 检查数据情况")
print("-" * 100)

try:
    conn = pymysql.connect(**db_config, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 检查最近7天有多少包含 signal_components 的订单
    cursor.execute("""
        SELECT COUNT(*) as count
        FROM futures_positions
        WHERE status = 'closed'
        AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        AND signal_components IS NOT NULL
        AND signal_components != ''
    """)
    result = cursor.fetchone()
    orders_with_components = result['count']

    print(f"最近7天包含signal_components的订单数: {orders_with_components}")

    if orders_with_components == 0:
        print("\n⚠️  还没有包含signal_components的订单")
        print("💡 这是正常的，因为系统刚部署")
        print("📝 我们可以创建一些测试数据来验证优化器")

        # 询问是否创建测试数据
        print("\n是否创建测试数据来验证优化器? (yes/no): ", end='')
        response = input().strip().lower()

        if response == 'yes':
            print("\n📝 创建测试数据...")

            # 获取最近的一些已平仓订单
            cursor.execute("""
                SELECT id, position_side, entry_signal_type, realized_pnl
                FROM futures_positions
                WHERE status = 'closed'
                ORDER BY close_time DESC
                LIMIT 20
            """)
            orders = cursor.fetchall()

            if not orders:
                print("❌ 数据库中没有已平仓订单，无法创建测试数据")
                sys.exit(1)

            # 为这些订单添加模拟的 signal_components
            test_components = [
                {'position_low': 20, 'trend_1h_bull': 20, 'momentum_down_3pct': 15},  # 典型LONG信号
                {'position_high': 20, 'trend_1h_bear': 20, 'momentum_up_3pct': 15},   # 典型SHORT信号
                {'position_mid': 5, 'volatility_high': 10, 'consecutive_bull': 15},   # 混合信号
                {'trend_1h_bull': 20, 'trend_1d_bull': 10, 'consecutive_bull': 15},   # 强趋势
            ]

            updated_count = 0
            for i, order in enumerate(orders):
                # 根据方向选择合适的组件
                if order['position_side'] == 'LONG':
                    components = test_components[i % 2]  # 使用LONG或混合信号
                else:
                    components = test_components[1 if i % 2 == 0 else 2]  # 使用SHORT或混合信号

                # 计算得分
                score = sum(components.values())

                cursor.execute("""
                    UPDATE futures_positions
                    SET signal_components = %s,
                        entry_score = %s
                    WHERE id = %s
                """, (json.dumps(components), score, order['id']))
                updated_count += 1

            conn.commit()
            print(f"✅ 已为 {updated_count} 个订单添加测试数据")

            # 重新统计
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE status = 'closed'
                AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                AND signal_components IS NOT NULL
                AND signal_components != ''
            """)
            result = cursor.fetchone()
            orders_with_components = result['count']
            print(f"现在有 {orders_with_components} 个订单包含signal_components")
        else:
            print("\n⏭️  跳过创建测试数据")
    else:
        print(f"✅ 有足够的数据进行测试 ({orders_with_components} 个订单)")

    cursor.close()

except Exception as e:
    print(f"❌ 检查数据失败: {e}")
    sys.exit(1)

# 测试2: 测试 ScoringWeightOptimizer
print("\n📋 测试2: 测试 ScoringWeightOptimizer 类")
print("-" * 100)

try:
    from app.services.scoring_weight_optimizer import ScoringWeightOptimizer

    optimizer = ScoringWeightOptimizer(db_config)
    print("✅ ScoringWeightOptimizer 导入成功")

    # 测试分析组件表现
    print("\n🔍 分析组件表现...")
    performance = optimizer.analyze_component_performance(days=7)

    if performance:
        print(f"✅ 成功分析 {len(performance)} 个组件")

        # 显示前3个组件的详细信息
        print("\n示例组件表现:")
        for comp_name, sides in list(performance.items())[:3]:
            print(f"\n  📊 {comp_name}:")
            for side, perf in sides.items():
                print(f"    {side}: 订单={perf['total_orders']}, 胜率={perf['win_rate']*100:.1f}%, "
                      f"平均盈亏=${perf['avg_pnl']:.2f}, 表现评分={perf['performance_score']:.1f}")
    else:
        print("⚠️  没有足够的数据进行分析")

except Exception as e:
    print(f"❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 测试3: 模拟权重调整 (dry run)
print("\n📋 测试3: 模拟权重调整 (dry_run=True)")
print("-" * 100)

try:
    print("🔧 执行模拟调整...")
    results = optimizer.adjust_weights(dry_run=True)

    if results.get('adjusted'):
        print(f"\n✅ 模拟调整成功，建议调整 {len(results['adjusted'])} 个权重")
        optimizer.print_adjustment_report(results)
    else:
        print("\nℹ️  当前无需调整权重")
        if results.get('error'):
            print(f"⚠️  错误: {results['error']}")

except Exception as e:
    print(f"❌ 模拟调整失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

# 测试4: 测试 AdaptiveOptimizer 集成
print("\n📋 测试4: 测试 AdaptiveOptimizer 集成")
print("-" * 100)

try:
    from app.services.adaptive_optimizer import AdaptiveOptimizer

    adaptive_optimizer = AdaptiveOptimizer(db_config)
    print("✅ AdaptiveOptimizer 初始化成功")

    # 检查是否包含 weight_optimizer
    if hasattr(adaptive_optimizer, 'weight_optimizer'):
        print("✅ weight_optimizer 已集成到 AdaptiveOptimizer")
    else:
        print("❌ weight_optimizer 未集成到 AdaptiveOptimizer")

except Exception as e:
    print(f"❌ 测试失败: {e}")
    import traceback
    traceback.print_exc()

# 测试5: 询问是否实际执行一次权重调整
print("\n📋 测试5: 实际执行权重调整")
print("-" * 100)

if results.get('adjusted'):
    print("\n是否实际执行这次权重调整? (yes/no): ", end='')
    response = input().strip().lower()

    if response == 'yes':
        print("\n🔧 执行实际调整...")
        try:
            actual_results = optimizer.adjust_weights(dry_run=False)

            if actual_results.get('adjusted'):
                print(f"\n✅ 权重调整成功！已调整 {len(actual_results['adjusted'])} 个权重")
                optimizer.print_adjustment_report(actual_results)

                # 显示调整后的权重
                print("\n查看调整后的权重:")
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT signal_component, weight_long, weight_short,
                           performance_score, adjustment_count, last_adjusted
                    FROM signal_scoring_weights
                    WHERE adjustment_count > 0
                    ORDER BY last_adjusted DESC
                    LIMIT 10
                """)
                adjusted_weights = cursor.fetchall()

                if adjusted_weights:
                    print(f"\n最近调整的 {len(adjusted_weights)} 个权重:")
                    for w in adjusted_weights:
                        print(f"  • {w['signal_component']:<20} "
                              f"LONG:{w['weight_long']:>4.0f} SHORT:{w['weight_short']:>4.0f} "
                              f"评分:{w['performance_score']:>6.1f} "
                              f"调整次数:{w['adjustment_count']} "
                              f"最后调整:{w['last_adjusted']}")

                cursor.close()
            else:
                print("ℹ️  无需调整权重")

        except Exception as e:
            print(f"❌ 实际调整失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("⏭️  跳过实际调整")
else:
    print("ℹ️  当前没有需要调整的权重，跳过实际执行")

conn.close()

print("\n" + "=" * 100)
print("🎉 测试完成！")
print("=" * 100)

print("\n📝 总结:")
print("  ✅ 如果所有测试通过，优化器已准备就绪")
print("  ✅ 系统会在每日凌晨2点自动运行权重优化")
print("  ✅ 也可以手动运行: python app/services/scoring_weight_optimizer.py")

print("\n📊 查看优化历史:")
print("  SELECT * FROM signal_component_performance ORDER BY last_analyzed DESC;")
print("  SELECT * FROM signal_scoring_weights WHERE adjustment_count > 0;")
