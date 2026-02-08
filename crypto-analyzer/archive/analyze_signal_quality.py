#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号质量深度分析脚本

分析为什么胜率只有35%,远低于50%的随机水平
"""

import pymysql
import os
import sys
from datetime import datetime, timedelta
from dotenv import load_dotenv
from collections import defaultdict

# 设置控制台编码为UTF-8
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()

def get_db_connection():
    """获取数据库连接"""
    return pymysql.connect(
        host=os.getenv('DB_HOST'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        cursorclass=pymysql.cursors.DictCursor
    )

def analyze_signal_quality():
    """深度分析信号质量问题"""

    conn = get_db_connection()
    cursor = conn.cursor()

    print("="*100)
    print(f"🔍 信号质量深度分析报告 - 为什么胜率低于抛硬币?")
    print(f"⏰ 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100)
    print()

    # 1. 最近7天的整体趋势
    print("📊 【最近7天交易表现趋势】")
    print("-"*100)

    cursor.execute("""
        SELECT
            DATE(close_time) as trade_date,
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE close_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND status = 'closed'
        GROUP BY DATE(close_time)
        ORDER BY trade_date DESC
    """)

    daily_stats = cursor.fetchall()

    print(f"{'日期':<12} {'交易次数':<10} {'盈利次数':<10} {'胜率':<10} {'总盈亏':<15} {'平均盈亏':<15}")
    print("-" * 100)
    for row in daily_stats:
        pnl_emoji = "✅" if row['total_pnl'] > 0 else "❌"
        print(f"{row['trade_date']}\t{row['total_trades']:<10} {row['wins']:<10} "
              f"{row['win_rate']:<10.2f}% {pnl_emoji} ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")

    print()
    print()

    # 2. 信号组合质量分析(最近7天)
    print("⚠️ 【表现最差的信号组合 Top 20】(最近7天)")
    print("-"*100)

    cursor.execute("""
        SELECT
            entry_signal_type,
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE close_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND status = 'closed'
        AND entry_signal_type IS NOT NULL
        GROUP BY entry_signal_type
        HAVING total_trades >= 3
        ORDER BY win_rate ASC, total_pnl ASC
        LIMIT 20
    """)

    bad_signals = cursor.fetchall()

    print(f"{'信号组合':<55} {'次数':<8} {'盈利':<8} {'胜率':<10} {'总盈亏':<15}")
    print("-" * 110)
    for row in bad_signals:
        signal = row['entry_signal_type'][:53] if row['entry_signal_type'] else 'N/A'
        pnl_emoji = "✅" if row['total_pnl'] > 0 else "❌"
        print(f"{signal:<55} {row['total_trades']:<8} {row['wins']:<8} "
              f"{row['win_rate']:<10.2f}% {pnl_emoji} ${row['total_pnl']:<13.2f}")

    print()
    print()

    # 3. Big4信号过滤效果分析
    print("🧠 【Big4信号过滤效果分析】(最近7天)")
    print("-"*100)

    # 检查是否有Big4相关字段
    cursor.execute("SHOW COLUMNS FROM futures_positions LIKE 'big4%'")
    big4_columns = cursor.fetchall()

    if big4_columns:
        print("检测到Big4相关字段,分析中...")
    else:
        print("⚠️ 数据库中没有Big4信号记录字段")
        print("建议: 在开仓时记录Big4信号(overall_signal, signal_strength)到持仓表")

    print()
    print()

    # 4. 交易对质量分析
    print("💀 【高频亏损交易对分析】(最近7天,至少5笔)")
    print("-"*100)

    cursor.execute("""
        SELECT
            symbol,
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE close_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND status = 'closed'
        GROUP BY symbol
        HAVING total_trades >= 5 AND win_rate < 40
        ORDER BY total_pnl ASC
        LIMIT 20
    """)

    bad_symbols = cursor.fetchall()

    if bad_symbols:
        print(f"{'交易对':<15} {'交易次数':<10} {'盈利次数':<10} {'胜率':<10} {'总亏损':<15} {'平均亏损':<15}")
        print("-" * 100)
        for row in bad_symbols:
            print(f"{row['symbol']:<15} {row['total_trades']:<10} {row['wins']:<10} "
                  f"{row['win_rate']:<10.2f}% ❌ ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")

        print()
        print("💡 建议操作:")
        for row in bad_symbols[:10]:
            if row['win_rate'] < 25:
                print(f"   - 立即禁止交易: {row['symbol']} (胜率{row['win_rate']:.1f}%, 亏损${row['total_pnl']:.2f})")
            elif row['win_rate'] < 35:
                print(f"   - 降低仓位50%: {row['symbol']} (胜率{row['win_rate']:.1f}%, 亏损${row['total_pnl']:.2f})")
    else:
        print("✅ 没有发现高频亏损的交易对")

    print()
    print()

    # 5. 开仓评分vs实际表现
    print("📊 【开仓评分vs实际胜率分析】(最近7天)")
    print("-"*100)

    cursor.execute("""
        SELECT
            CASE
                WHEN entry_score >= 80 THEN '80-100分(极优)'
                WHEN entry_score >= 70 THEN '70-79分(优秀)'
                WHEN entry_score >= 60 THEN '60-69分(良好)'
                WHEN entry_score >= 50 THEN '50-59分(一般)'
                WHEN entry_score >= 40 THEN '40-49分(及格)'
                ELSE '<40分(不及格)'
            END as score_range,
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE close_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND status = 'closed'
        AND entry_score IS NOT NULL
        GROUP BY score_range
        ORDER BY MIN(entry_score) DESC
    """)

    score_stats = cursor.fetchall()

    if score_stats:
        print(f"{'评分区间':<20} {'交易次数':<10} {'盈利次数':<10} {'胜率':<10} {'总盈亏':<15} {'平均盈亏':<15}")
        print("-" * 100)
        for row in score_stats:
            pnl_emoji = "✅" if row['total_pnl'] > 0 else "❌"
            print(f"{row['score_range']:<20} {row['total_trades']:<10} {row['wins']:<10} "
                  f"{row['win_rate']:<10.2f}% {pnl_emoji} ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")

        print()
        print("💡 分析:")
        high_score = [r for r in score_stats if '80-100' in r['score_range'] or '70-79' in r['score_range']]
        if high_score:
            for row in high_score:
                if row['win_rate'] < 50:
                    print(f"   ⚠️ 警告: {row['score_range']}的信号胜率仅{row['win_rate']:.1f}%, 评分系统可能失效!")
    else:
        print("⚠️ 没有entry_score数据")

    print()
    print()

    # 6. 止损/止盈触发分析
    print("🎯 【止损止盈触发情况分析】(最近7天)")
    print("-"*100)

    cursor.execute("""
        SELECT
            CASE
                WHEN realized_pnl >= take_profit_pct * margin * leverage * 0.01 THEN '触发止盈'
                WHEN realized_pnl <= -stop_loss_pct * margin * leverage * 0.01 THEN '触发止损'
                WHEN realized_pnl > 0 THEN '提前止盈'
                ELSE '提前止损/超时'
            END as close_type,
            COUNT(*) as count,
            ROUND(SUM(realized_pnl), 2) as total_pnl,
            ROUND(AVG(realized_pnl), 2) as avg_pnl
        FROM futures_positions
        WHERE close_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND status = 'closed'
        AND stop_loss_pct IS NOT NULL
        AND take_profit_pct IS NOT NULL
        GROUP BY close_type
        ORDER BY count DESC
    """)

    close_types = cursor.fetchall()

    if close_types:
        print(f"{'平仓类型':<20} {'次数':<10} {'总盈亏':<15} {'平均盈亏':<15}")
        print("-" * 70)
        for row in close_types:
            pnl_emoji = "✅" if row['total_pnl'] > 0 else "❌"
            print(f"{row['close_type']:<20} {row['count']:<10} {pnl_emoji} ${row['total_pnl']:<13.2f} ${row['avg_pnl']:<13.2f}")

        print()
        print("💡 分析:")
        early_loss = [r for r in close_types if '提前止损' in r['close_type']]
        if early_loss and early_loss[0]['count'] > sum(r['count'] for r in close_types) * 0.5:
            print(f"   ⚠️ 警告: {early_loss[0]['count']}笔提前止损, 占比过高! 可能是:")
            print(f"      1. 信号质量差,一开仓就亏")
            print(f"      2. 止损设置太紧")
            print(f"      3. 市场波动太大")
    else:
        print("⚠️ 数据不足或字段缺失")

    print()
    print()

    # 7. 核心问题总结
    print("="*100)
    print("🎯 【核心问题诊断】")
    print("="*100)
    print()

    # 计算7天总体数据
    cursor.execute("""
        SELECT
            COUNT(*) as total_trades,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
            ROUND(SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / COUNT(*) * 100, 2) as win_rate,
            ROUND(SUM(realized_pnl), 2) as total_pnl
        FROM futures_positions
        WHERE close_time >= DATE_SUB(CURDATE(), INTERVAL 7 DAY)
        AND status = 'closed'
    """)

    summary = cursor.fetchone()

    print(f"最近7天总交易: {summary['total_trades']}笔")
    print(f"总胜率: {summary['win_rate']}%")
    print(f"总盈亏: ${summary['total_pnl']:.2f}")
    print()

    problems = []

    # 问题1: 胜率低于50%
    if summary['win_rate'] < 50:
        problems.append({
            'severity': '严重',
            'problem': f"胜率{summary['win_rate']:.1f}%低于随机(50%)",
            'reason': '信号质量差/市场环境不适合',
            'action': '禁用表现差的信号组合'
        })

    # 问题2: 亏损交易对
    if bad_symbols and len(bad_symbols) > 5:
        problems.append({
            'severity': '严重',
            'problem': f"发现{len(bad_symbols)}个高频亏损交易对",
            'reason': '交易对选择不当/评级系统未生效',
            'action': f"立即加入黑名单: {', '.join([s['symbol'] for s in bad_symbols[:5]])}"
        })

    # 问题3: 信号组合
    if bad_signals and bad_signals[0]['win_rate'] < 30:
        problems.append({
            'severity': '严重',
            'problem': f"最差信号组合胜率仅{bad_signals[0]['win_rate']:.1f}%",
            'reason': '信号组合逻辑错误',
            'action': f"禁用信号: {bad_signals[0]['entry_signal_type'][:50]}"
        })

    # 输出问题列表
    for idx, p in enumerate(problems, 1):
        print(f"问题 {idx} [{p['severity']}]:")
        print(f"   现象: {p['problem']}")
        print(f"   原因: {p['reason']}")
        print(f"   建议: {p['action']}")
        print()

    print("="*100)
    print()

    # 8. 立即行动建议
    print("💡 【立即执行的优化建议】")
    print("-"*100)
    print()

    print("1️⃣ 禁用低质量信号组合 (胜率<30%):")
    if bad_signals:
        for idx, signal in enumerate(bad_signals[:5], 1):
            if signal['win_rate'] < 30:
                signal_str = signal['entry_signal_type'][:70]
                print(f"   {idx}. {signal_str}")
                print(f"      SQL: INSERT INTO signal_blacklist (signal_type, position_side, reason, is_active)")
                print(f"           VALUES ('{signal_str}', 'LONG', '7日胜率{signal['win_rate']:.1f}%', 1);")
    print()

    print("2️⃣ 加入黑名单/提升评级 (胜率<25%或亏损>$100):")
    if bad_symbols:
        for idx, symbol in enumerate(bad_symbols[:5], 1):
            if symbol['win_rate'] < 25 or symbol['total_pnl'] < -100:
                print(f"   {idx}. {symbol['symbol']}: 胜率{symbol['win_rate']:.1f}%, 亏损${symbol['total_pnl']:.2f}")
                print(f"      SQL: INSERT INTO trading_symbol_rating (symbol, rating_level, margin_multiplier, reason)")
                print(f"           VALUES ('{symbol['symbol']}', 3, 0, '7日胜率{symbol['win_rate']:.1f}%,亏损${symbol['total_pnl']:.2f}');")
    print()

    print("3️⃣ 提高开仓阈值:")
    print(f"   当前阈值: 35分")
    print(f"   建议阈值: 50分 (只接受中等以上质量的信号)")
    print()

    print("4️⃣ 检查Big4信号:")
    print(f"   - 查看Big4信号是否准确反映市场趋势")
    print(f"   - 考虑提高Big4过滤强度(强度>=70才开仓)")
    print()

    cursor.close()
    conn.close()

if __name__ == '__main__':
    try:
        analyze_signal_quality()
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
