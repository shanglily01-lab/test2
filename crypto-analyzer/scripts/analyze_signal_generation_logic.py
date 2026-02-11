#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析单一信号产生的根本原因

根本问题：
1. 阈值设置过低
2. 权重配置不合理
3. 缺少多信号组合验证
"""

import os
import sys
import pymysql
from dotenv import load_dotenv

# 加载环境变量
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, '.env'))

def get_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        charset='utf8mb4'
    )

def analyze_threshold_and_weights():
    """分析阈值和权重配置"""
    conn = get_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    print("\n" + "="*80)
    print("【单一信号产生原因分析】")
    print("="*80)

    # 1. 查询当前阈值配置
    cursor.execute("""
        SELECT param_name, param_value, description
        FROM optimization_config
        WHERE param_name = 'threshold'
    """)
    threshold_config = cursor.fetchone()

    if threshold_config:
        threshold = float(threshold_config['param_value'])
        print(f"\n1. 当前开仓阈值: {threshold}分")
    else:
        threshold = 50  # 默认值
        print(f"\n1. 当前开仓阈值: {threshold}分 (默认值)")

    # 2. 查询scoring_weights配置
    cursor.execute("""
        SELECT param_name, param_value
        FROM optimization_config
        WHERE param_name = 'scoring_weights'
    """)
    weights_config = cursor.fetchone()

    if weights_config:
        import json
        try:
            weights = json.loads(weights_config['param_value'])
            print(f"\n2. 信号权重配置:")
            print("-"*80)

            # 按类别分组显示
            categories = {
                '位置信号': ['position_low', 'position_high', 'position_mid'],
                '动量信号': ['momentum_up_3pct', 'momentum_down_3pct'],
                '趋势信号': ['trend_1h_bull', 'trend_1h_bear', 'consecutive_bull', 'consecutive_bear'],
                '量能信号': ['volume_power_bull', 'volume_power_bear', 'volume_power_1h_bull', 'volume_power_1h_bear'],
                '突破信号': ['breakout_long', 'breakdown_short'],
                '其他信号': ['volatility_high']
            }

            single_signal_can_open = []

            for category, signals in categories.items():
                print(f"\n   {category}:")
                for signal in signals:
                    if signal in weights:
                        long_score = weights[signal].get('long', 0)
                        short_score = weights[signal].get('short', 0)
                        max_score = max(long_score, short_score)

                        can_open_alone = "YES [可单独开仓!]" if max_score >= threshold else "NO"
                        if max_score >= threshold:
                            single_signal_can_open.append((signal, max_score))

                        print(f"      {signal:<25} LONG:{long_score:>3}, SHORT:{short_score:>3} | 最高:{max_score:>3}分 | {can_open_alone}")
        except:
            weights = {}
            print("   无法解析权重配置")
    else:
        weights = {}
        print("\n2. 未找到权重配置")

    # 3. 分析单一信号达标情况
    if single_signal_can_open:
        print("\n" + "="*80)
        print("【根本原因】单一信号权重 >= 开仓阈值，可以独立触发开仓：")
        print("="*80)
        for signal, score in single_signal_can_open:
            print(f"   {signal:<25} {score}分 >= 阈值{threshold}分")

        print("\n这些信号可以在没有其他信号配合的情况下单独开仓！")
    else:
        print("\n" + "="*80)
        print("【配置正常】没有单一信号可以达到开仓阈值")
        print("="*80)

    # 4. 查询实际交易中的单一信号案例
    print("\n" + "="*80)
    print("【实际交易验证】最近30天单一信号开仓记录：")
    print("="*80)

    cursor.execute("""
        SELECT
            entry_signal_type,
            position_side,
            COUNT(*) as trades,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE status = 'CLOSED'
        AND entry_signal_type NOT LIKE '% + %'
        AND entry_signal_type NOT LIKE '%SMART_BRAIN_SCORE%'
        AND close_time >= UNIX_TIMESTAMP(DATE_SUB(NOW(), INTERVAL 30 DAY)) * 1000
        GROUP BY entry_signal_type, position_side
        ORDER BY trades DESC
    """)

    single_signals = cursor.fetchall()

    if single_signals:
        print(f"\n找到 {len(single_signals)} 种单一信号组合：\n")
        total_trades = sum(s['trades'] for s in single_signals)
        total_pnl = sum(s['total_pnl'] for s in single_signals)

        for s in single_signals:
            print(f"   {s['entry_signal_type']:<30} | {s['position_side']:<5} | "
                  f"{s['trades']:>3}单 | 胜率{s['win_rate']:>5.1f}% | "
                  f"盈亏:{s['total_pnl']:>8.2f}U | 均值:{s['avg_pnl']:>6.2f}U")

        print(f"\n   总计: {total_trades}单 | 总盈亏: {total_pnl:.2f}U")
    else:
        print("\n   [良好] 没有发现单一信号开仓记录")

    # 5. 提出解决方案
    print("\n" + "="*80)
    print("【解决方案】如何防止单一信号开仓：")
    print("="*80)

    print("""
方案1: 提高开仓阈值 (推荐)
   - 将threshold从当前值提高到60-80分
   - 确保必须至少2-3个信号组合才能达标
   - 优点: 简单直接，立即生效
   - 缺点: 可能减少开仓机会

方案2: 降低单一信号权重 (推荐)
   - 将所有信号权重降到threshold以下
   - 位置信号: 20分 → 15分
   - 量能信号: 25分 → 20分
   - 优点: 强制多信号组合
   - 缺点: 需要重新平衡权重

方案3: 代码层面强制验证 (最彻底)
   - 在smart_trader_service.py的analyze()方法中添加:
     if len(signal_components) < 2:
         logger.warning(f"{{symbol}} 信号不足: 只有{len(signal_components)}个信号")
         return None
   - 优点: 强制要求多信号组合，无论配置如何
   - 缺点: 需要修改代码

方案4: 建立信号组合白名单 (最灵活)
   - 定义可靠的信号组合模式:
     * 趋势 + 位置 + 量能
     * 动量 + 位置 + 波动率
     * 突破 + 量能 + 位置
   - 只允许这些组合开仓
   - 优点: 精确控制交易质量
   - 缺点: 维护成本高

推荐实施: 方案1 + 方案3
   - 立即提高阈值到70分
   - 添加代码层面的len(signal_components) >= 2验证
   - 这样可以双重保障，防止单一信号开仓
""")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    try:
        analyze_threshold_and_weights()
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
