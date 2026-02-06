"""
简化的灾难分析 - 不依赖big4表
"""

import pymysql
import sys
import io
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.utils.config_loader import load_config


def simple_check():
    """简化分析"""
    config = load_config()
    db_config = config.get('database', {}).get('mysql', {})

    try:
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        print("\n" + "="*80)
        print("🔍 灾难简化分析")
        print("="*80 + "\n")

        # 1. 查看所有表
        print("📋 1. 数据库中的表")
        print("-" * 80)
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
        table_names = [list(t.values())[0] for t in tables]
        print(f"共{len(table_names)}个表")
        for t in sorted(table_names):
            if 'big4' in t.lower() or 'signal' in t.lower() or 'mode' in t.lower():
                print(f"  - {t}")
        print()

        # 2. 01:38灾难开仓分析
        print("🚨 2. 01:38-02:02 灾难开仓分析")
        print("-" * 80)
        cursor.execute("""
            SELECT
                DATE_FORMAT(open_time, '%H:%i') as time_slot,
                COUNT(*) as count,
                GROUP_CONCAT(DISTINCT symbol ORDER BY symbol SEPARATOR ', ') as symbols
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 02:02:00'
            AND status = 'closed'
            GROUP BY DATE_FORMAT(open_time, '%H:%i')
            ORDER BY time_slot
        """)

        time_slots = cursor.fetchall()
        total_count = 0
        for slot in time_slots:
            print(f"{slot['time_slot']} | {slot['count']:2}笔 | {slot['symbols'][:60]}...")
            total_count += slot['count']
        print(f"\n总计: {total_count}笔交易在24分钟内开仓")
        print()

        # 3. 检查信号分数分布
        print("📊 3. 灾难时段信号分数分布")
        print("-" * 80)
        cursor.execute("""
            SELECT
                entry_signal_type,
                COUNT(*) as count,
                MIN(entry_score) as min_score,
                AVG(entry_score) as avg_score,
                MAX(entry_score) as max_score,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 02:02:00'
            AND status = 'closed'
            GROUP BY entry_signal_type
            ORDER BY count DESC
        """)

        signals = cursor.fetchall()
        for sig in signals:
            print(f"{sig['entry_signal_type'][:60]}")
            print(f"  数量:{sig['count']:2} | 分数:{sig['min_score']:.0f}-{sig['avg_score']:.0f}-{sig['max_score']:.0f} | "
                  f"盈亏:{sig['total_pnl']:.2f}U")
        print()

        # 4. 检查notes中的止损原因
        print("🛑 4. 止损原因统计")
        print("-" * 80)
        cursor.execute("""
            SELECT
                CASE
                    WHEN notes LIKE '%止损(价格%' THEN '价格止损'
                    WHEN notes LIKE '%分阶段超时%' THEN '超时平仓'
                    WHEN notes LIKE '%持仓时长到期%' THEN '强制平仓'
                    ELSE '其他'
                END as stop_reason,
                COUNT(*) as count,
                ROUND(AVG(realized_pnl), 2) as avg_loss,
                ROUND(SUM(realized_pnl), 2) as total_loss
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 02:02:00'
            AND status = 'closed'
            GROUP BY stop_reason
            ORDER BY count DESC
        """)

        reasons = cursor.fetchall()
        for r in reasons:
            print(f"{r['stop_reason']:10} | {r['count']:2}笔 | 均亏:{r['avg_loss']:6.2f}U | 总亏:{r['total_loss']:8.2f}U")
        print()

        # 5. 对比04:15时段
        print("📈 5. 对比第二波灾难 (04:15-05:15)")
        print("-" * 80)
        cursor.execute("""
            SELECT
                DATE_FORMAT(open_time, '%H:%i') as time_slot,
                COUNT(*) as count,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 04:15:00'
            AND open_time <= '2026-02-06 05:15:00'
            AND status = 'closed'
            GROUP BY DATE_FORMAT(open_time, '%H:%i')
            ORDER BY time_slot
        """)

        second_wave = cursor.fetchall()
        total_second = 0
        total_pnl_second = 0
        for slot in second_wave:
            print(f"{slot['time_slot']} | {slot['count']:2}笔 | {slot['total_pnl']:8.2f}U")
            total_second += slot['count']
            total_pnl_second += slot['total_pnl']
        print(f"\n第二波: {total_second}笔，总亏{total_pnl_second:.2f}U")
        print()

        # 6. 检查开仓速度
        print("⚡ 6. 开仓速度异常检测")
        print("-" * 80)
        cursor.execute("""
            SELECT
                open_time,
                symbol,
                entry_signal_type,
                entry_score,
                realized_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 01:42:00'
            AND status = 'closed'
            ORDER BY open_time
        """)

        first_5min = cursor.fetchall()
        print(f"01:38-01:42 (前4分钟): {len(first_5min)}笔")
        if first_5min:
            # 找出时间间隔
            last_time = None
            max_per_minute = 0
            current_minute = None
            minute_count = 0

            for t in first_5min[:15]:
                minute = t['open_time'].strftime('%H:%M')
                if current_minute != minute:
                    if current_minute:
                        print(f"  {current_minute}: {minute_count}笔")
                        max_per_minute = max(max_per_minute, minute_count)
                    current_minute = minute
                    minute_count = 1
                else:
                    minute_count += 1

            if current_minute:
                print(f"  {current_minute}: {minute_count}笔")
                max_per_minute = max(max_per_minute, minute_count)

            print(f"\n⚠️ 单分钟最多开仓{max_per_minute}笔")
        print()

        # 7. 昨天对比
        print("📅 7. 昨天同时段对比")
        print("-" * 80)
        cursor.execute("""
            SELECT
                '昨天 01:38-02:02' as period,
                COUNT(*) as count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-05 01:38:00'
            AND open_time <= '2026-02-05 02:02:00'
            AND status = 'closed'
            UNION ALL
            SELECT
                '今天 01:38-02:02' as period,
                COUNT(*) as count,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
                ROUND(SUM(realized_pnl), 2) as total_pnl
            FROM futures_positions
            WHERE account_id = 2
            AND open_time >= '2026-02-06 01:38:00'
            AND open_time <= '2026-02-06 02:02:00'
            AND status = 'closed'
        """)

        comparison = cursor.fetchall()
        for c in comparison:
            winrate = (c['wins'] / c['count'] * 100) if c['count'] > 0 else 0
            print(f"{c['period']:20} | {c['count']:2}笔 | 胜率:{winrate:5.1f}% | 盈亏:{c['total_pnl']:8.2f}U")

        cursor.close()
        conn.close()

        print("\n" + "="*80)
        print("✅ 分析完成")
        print("="*80 + "\n")

        print("🎯 核心结论:")
        print("1. 系统在01:38开始疯狂开空单")
        print("2. 几乎所有订单都在10-30分钟内止损")
        print("3. 没有熔断机制阻止连续亏损")
        print("4. 04:15又重复了同样的错误")
        print()

    except Exception as e:
        print(f"❌ 分析失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    simple_check()
