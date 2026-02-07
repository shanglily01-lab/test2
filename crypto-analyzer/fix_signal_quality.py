#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号质量修复脚本
基于analyze_signal_quality.py的分析结果，执行优化建议
"""

import pymysql
import os
import sys
from datetime import datetime
from dotenv import load_dotenv

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

def fix_signal_quality():
    """执行信号质量修复"""

    conn = get_db_connection()
    cursor = conn.cursor()

    print("="*100)
    print("🔧 信号质量修复脚本")
    print(f"⏰ 执行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*100)
    print()

    # 1. 禁用低质量信号组合 (胜率<30%)
    print("📝 【步骤1】禁用低质量信号组合 (7日胜率<30%)...")
    print("-"*100)

    bad_signals = [
        {
            'signal': 'TREND_breakout_long + momentum_up_3pct + volatility_high + volume_power_bull',
            'side': 'LONG',
            'reason': '7日21笔交易胜率0.0%,亏损$-943.60'
        },
        {
            'signal': 'consecutive_bull + momentum_up_3pct + position_mid + volume_power_1h_bull',
            'side': 'LONG',
            'reason': '7日5笔交易胜率0.0%,亏损$-187.73'
        },
        {
            'signal': 'TREND_position_mid + volatility_high + volume_power_bull',
            'side': 'LONG',
            'reason': '7日5笔交易胜率0.0%,亏损$-54.70'
        },
        {
            'signal': 'momentum_up_3pct + volatility_high',
            'side': 'LONG',
            'reason': '7日4笔交易胜率0.0%,亏损$-51.33'
        },
        {
            'signal': 'TREND_consecutive_bull + position_mid + volatility_high',
            'side': 'LONG',
            'reason': '7日4笔交易胜率0.0%,亏损$-50.68'
        },
        {
            'signal': 'TREND_momentum_up_3pct + position_mid + volatility_high',
            'side': 'LONG',
            'reason': '7日4笔交易胜率0.0%,亏损$-42.11'
        },
        {
            'signal': 'TREND_consecutive_bear + momentum_down_3pct + position_mid',
            'side': 'SHORT',
            'reason': '7日3笔交易胜率0.0%,亏损$-25.54'
        },
        {
            'signal': 'consecutive_bull + position_mid + volatility_high + volume_power_1h_bull',
            'side': 'LONG',
            'reason': '7日3笔交易胜率0.0%,亏损$-13.62'
        },
        {
            'signal': 'TREND_momentum_down_3pct + position_mid + trend_1h_bear',
            'side': 'SHORT',
            'reason': '7日33笔交易胜率9.09%,亏损$-781.14'
        },
        {
            'signal': 'consecutive_bull + position_mid',
            'side': 'LONG',
            'reason': '7日11笔交易胜率9.09%,亏损$-292.85'
        },
        {
            'signal': 'TREND_momentum_down_3pct + position_mid + volatility_high',
            'side': 'SHORT',
            'reason': '7日23笔交易胜率13.04%,亏损$-280.46'
        },
        {
            'signal': 'TREND_momentum_down_3pct + position_mid + volatility_high + volume_power_bear',
            'side': 'SHORT',
            'reason': '7日13笔交易胜率15.38%,亏损$-316.81'
        },
        {
            'signal': 'RANGE_range_trading',
            'side': 'BOTH',
            'reason': '7日16笔交易胜率18.75%,亏损$-64.64'
        },
        {
            'signal': 'momentum_up_3pct + position_mid',
            'side': 'LONG',
            'reason': '7日21笔交易胜率23.81%,亏损$-455.55'
        },
        {
            'signal': 'TREND_breakout_long + momentum_up_3pct + volatility_high',
            'side': 'LONG',
            'reason': '7日8笔交易胜率25.00%,亏损$-157.80'
        }
    ]

    added_count = 0
    skipped_count = 0

    for signal_info in bad_signals:
        try:
            # 检查是否已存在
            cursor.execute("""
                SELECT id FROM signal_blacklist
                WHERE signal_type = %s AND position_side = %s
            """, (signal_info['signal'], signal_info['side']))

            if cursor.fetchone():
                print(f"⏭️  已存在: {signal_info['signal'][:60]}...")
                skipped_count += 1
                continue

            # 添加到黑名单
            cursor.execute("""
                INSERT INTO signal_blacklist
                (signal_type, position_side, reason, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, 1, NOW(), NOW())
            """, (signal_info['signal'], signal_info['side'], signal_info['reason']))

            print(f"✅ 已禁用: {signal_info['signal'][:60]}... ({signal_info['reason']})")
            added_count += 1

        except Exception as e:
            print(f"❌ 错误: {signal_info['signal'][:60]}... - {e}")

    conn.commit()
    print()
    print(f"✅ 完成: 新增{added_count}个黑名单信号, 跳过{skipped_count}个已存在")
    print()
    print()

    # 2. 加入交易对黑名单/降级
    print("📝 【步骤2】处理高频亏损交易对...")
    print("-"*100)

    bad_symbols = [
        {
            'symbol': 'ZAMA/USDT',
            'rating': 3,  # 3=黑名单
            'margin': 0,
            'reason': '7日13笔胜率30.8%,亏损$-378.80'
        },
        {
            'symbol': 'DASH/USDT',
            'rating': 3,
            'margin': 0,
            'reason': '7日7笔胜率14.3%,亏损$-279.68'
        },
        {
            'symbol': 'DOGE/USDT',
            'rating': 3,
            'margin': 0,
            'reason': '7日5笔胜率0.0%,亏损$-202.51'
        },
        {
            'symbol': 'CHZ/USDT',
            'rating': 3,
            'margin': 0,
            'reason': '7日5笔胜率0.0%,亏损$-187.73'
        },
        {
            'symbol': 'DOGE/USD',
            'rating': 3,
            'margin': 0,
            'reason': '7日27笔胜率29.6%,亏损$-186.89'
        },
        {
            'symbol': 'XLM/USD',
            'rating': 3,
            'margin': 0,
            'reason': '7日21笔胜率23.8%,亏损$-148.58'
        },
        {
            'symbol': 'SOL/USD',
            'rating': 2,  # 2=低评级(降低仓位50%)
            'margin': 0.5,
            'reason': '7日17笔胜率29.4%,亏损$-135.70'
        },
        {
            'symbol': 'NEAR/USDT',
            'rating': 2,
            'margin': 0.5,
            'reason': '7日8笔胜率37.5%,亏损$-160.24'
        },
        {
            'symbol': 'LTC/USDT',
            'rating': 2,
            'margin': 0.5,
            'reason': '7日13笔胜率38.5%,亏损$-155.27'
        }
    ]

    added_count = 0
    updated_count = 0

    for symbol_info in bad_symbols:
        try:
            # 检查是否已存在
            cursor.execute("""
                SELECT id, rating_level FROM trading_symbol_rating
                WHERE symbol = %s
            """, (symbol_info['symbol'],))

            existing = cursor.fetchone()

            if existing:
                # 更新评级
                cursor.execute("""
                    UPDATE trading_symbol_rating
                    SET rating_level = %s,
                        margin_multiplier = %s,
                        level_change_reason = %s,
                        updated_at = NOW()
                    WHERE symbol = %s
                """, (symbol_info['rating'], symbol_info['margin'], symbol_info['reason'], symbol_info['symbol']))

                action = "黑名单" if symbol_info['rating'] == 3 else "降级"
                print(f"🔄 已更新: {symbol_info['symbol']} -> {action} ({symbol_info['reason']})")
                updated_count += 1
            else:
                # 新增评级
                cursor.execute("""
                    INSERT INTO trading_symbol_rating
                    (symbol, rating_level, margin_multiplier, score_bonus,
                     level_change_reason, stats_start_date, stats_end_date, created_at, updated_at)
                    VALUES (%s, %s, %s, 0, %s, CURDATE(), CURDATE(), NOW(), NOW())
                """, (symbol_info['symbol'], symbol_info['rating'], symbol_info['margin'], symbol_info['reason']))

                action = "黑名单" if symbol_info['rating'] == 3 else "降级"
                print(f"✅ 已添加: {symbol_info['symbol']} -> {action} ({symbol_info['reason']})")
                added_count += 1

        except Exception as e:
            print(f"❌ 错误: {symbol_info['symbol']} - {e}")

    conn.commit()
    print()
    print(f"✅ 完成: 新增{added_count}个评级, 更新{updated_count}个评级")
    print()
    print()

    # 3. 显示修复后的统计
    print("📊 【修复后的黑名单统计】")
    print("-"*100)

    cursor.execute("""
        SELECT COUNT(*) as total_blacklist_signals
        FROM signal_blacklist
        WHERE is_active = 1
    """)
    signal_count = cursor.fetchone()['total_blacklist_signals']

    cursor.execute("""
        SELECT COUNT(*) as total_blacklist_symbols
        FROM trading_symbol_rating
        WHERE rating_level = 3
    """)
    symbol_count = cursor.fetchone()['total_blacklist_symbols']

    cursor.execute("""
        SELECT COUNT(*) as total_low_rating_symbols
        FROM trading_symbol_rating
        WHERE rating_level = 2
    """)
    low_rating_count = cursor.fetchone()['total_low_rating_symbols']

    print(f"🚫 信号黑名单: {signal_count} 个")
    print(f"🚫 交易对黑名单: {symbol_count} 个")
    print(f"⚠️  低评级交易对: {low_rating_count} 个")
    print()

    # 4. 显示建议
    print("="*100)
    print("💡 【后续建议】")
    print("="*100)
    print()
    print("1️⃣ 提高开仓阈值:")
    print("   - 当前阈值: 35分")
    print("   - 建议阈值: 50-60分 (只接受中等以上质量的信号)")
    print("   - 修改文件: smart_decision_brain_enhanced.py")
    print()
    print("2️⃣ 加强Big4过滤:")
    print("   - 当前强度阈值: 可能较低")
    print("   - 建议强度阈值: >=70才开仓")
    print("   - 修改文件: smart_decision_brain_enhanced.py")
    print()
    print("3️⃣ 监控效果:")
    print("   - 观察未来3-7天的胜率变化")
    print("   - 如果胜率提升到45%+, 说明修复有效")
    print("   - 定期运行 analyze_signal_quality.py 检查新的问题信号")
    print()
    print("4️⃣ 考虑暂停交易:")
    print("   - 如果修复后胜率仍低于45%, 建议暂停交易")
    print("   - 等待市场环境改善或调整策略")
    print()
    print("="*100)

    cursor.close()
    conn.close()

if __name__ == '__main__':
    try:
        # 确认执行
        print()
        print("⚠️  警告: 此脚本将修改数据库中的信号黑名单和交易对评级")
        print()
        response = input("确认执行? (yes/no): ")

        if response.lower() in ['yes', 'y']:
            print()
            fix_signal_quality()
            print()
            print("🎉 修复完成! 建议重启交易服务以应用更改。")
        else:
            print("❌ 已取消")

    except KeyboardInterrupt:
        print("\n❌ 用户取消")
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()
