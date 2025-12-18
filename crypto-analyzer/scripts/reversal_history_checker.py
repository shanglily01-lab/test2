#!/usr/bin/env python3
"""
测试反转开仓逻辑 - 使用真实数据库数据
连接服务端数据库，验证反转逻辑是否正常
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pymysql

# 服务端数据库配置
DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}


def get_db_connection():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)


def test_ltc_positions():
    """测试 LTC/USDT 的持仓数据"""
    print("\n" + "=" * 60)
    print("测试 1: 检查 LTC/USDT 持仓历史")
    print("=" * 60)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 查询最近的 LTC 持仓
        cursor.execute("""
            SELECT id, symbol, position_side, entry_price, status, notes,
                   entry_reason, entry_signal_type, created_at, close_time
            FROM futures_positions
            WHERE symbol = 'LTC/USDT'
            ORDER BY id DESC
            LIMIT 10
        """)
        positions = cursor.fetchall()

        print(f"\n找到 {len(positions)} 条 LTC/USDT 持仓记录:")
        for p in positions:
            print(f"\n  ID: {p['id']}")
            print(f"  方向: {p['position_side']}")
            print(f"  入场价: {p['entry_price']}")
            print(f"  状态: {p['status']}")
            print(f"  平仓原因(notes): {p['notes']}")
            print(f"  开仓原因: {p['entry_reason']}")
            print(f"  信号类型: {p['entry_signal_type']}")
            print(f"  开仓时间: {p['created_at']}")
            print(f"  平仓时间: {p['close_time']}")

        # 检查是否有金叉反转平仓但没有后续反转开仓的情况
        print("\n" + "-" * 40)
        print("检查反转平仓后是否有对应的反转开仓:")

        for i, p in enumerate(positions):
            notes = p['notes'] or ''
            if '金叉反转平仓' in notes:
                print(f"\n⚠️ 发现金叉反转平仓: ID={p['id']}, 平仓时间={p['close_time']}")
                # 检查下一条记录是否是反转开多
                if i > 0:  # 因为是倒序，i-1 是后面的记录
                    next_p = positions[i-1]
                    if next_p['position_side'] == 'LONG':
                        time_diff = (next_p['created_at'] - p['close_time']).total_seconds() if p['close_time'] else None
                        print(f"  ✅ 后续有开多: ID={next_p['id']}, 开仓时间={next_p['created_at']}")
                        if time_diff:
                            print(f"  ⏱️ 时间差: {time_diff:.0f} 秒 ({time_diff/60:.1f} 分钟)")
                            if time_diff > 60:
                                print(f"  ❌ 时间差过大！应该立即反转开仓")
                    else:
                        print(f"  ❌ 后续不是开多，而是: {next_p['position_side']}")
                else:
                    print(f"  ❓ 没有后续记录")

            elif '死叉反转平仓' in notes:
                print(f"\n⚠️ 发现死叉反转平仓: ID={p['id']}, 平仓时间={p['close_time']}")
                if i > 0:
                    next_p = positions[i-1]
                    if next_p['position_side'] == 'SHORT':
                        time_diff = (next_p['created_at'] - p['close_time']).total_seconds() if p['close_time'] else None
                        print(f"  ✅ 后续有开空: ID={next_p['id']}, 开仓时间={next_p['created_at']}")
                        if time_diff:
                            print(f"  ⏱️ 时间差: {time_diff:.0f} 秒 ({time_diff/60:.1f} 分钟)")
                            if time_diff > 60:
                                print(f"  ❌ 时间差过大！应该立即反转开仓")
                    else:
                        print(f"  ❌ 后续不是开空，而是: {next_p['position_side']}")

    finally:
        cursor.close()
        conn.close()


def test_all_reversal_positions():
    """测试所有交易对的反转平仓记录"""
    print("\n" + "=" * 60)
    print("测试 2: 检查所有交易对的反转平仓记录")
    print("=" * 60)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 查询所有金叉/死叉反转平仓的记录
        cursor.execute("""
            SELECT id, symbol, position_side, entry_price, status, notes,
                   created_at, close_time
            FROM futures_positions
            WHERE notes LIKE '%反转平仓%'
            ORDER BY close_time DESC
            LIMIT 20
        """)
        positions = cursor.fetchall()

        print(f"\n找到 {len(positions)} 条反转平仓记录:")

        reversal_stats = {
            'golden_cross': 0,  # 金叉反转
            'death_cross': 0,   # 死叉反转
            'trend': 0,         # 趋势反转
            'missing_entry': 0  # 缺失反转开仓
        }

        for p in positions:
            notes = p['notes'] or ''
            symbol = p['symbol']
            close_time = p['close_time']

            if '金叉反转平仓' in notes:
                reversal_stats['golden_cross'] += 1
                reversal_type = 'long'
            elif '死叉反转平仓' in notes:
                reversal_stats['death_cross'] += 1
                reversal_type = 'short'
            else:
                reversal_stats['trend'] += 1
                reversal_type = None

            print(f"\n  {symbol} | ID={p['id']} | {p['position_side']} | 平仓时间={close_time}")
            print(f"  原因: {notes}")

            # 检查是否有后续的反转开仓
            if reversal_type and close_time:
                cursor.execute("""
                    SELECT id, position_side, entry_reason, entry_signal_type, created_at
                    FROM futures_positions
                    WHERE symbol = %s AND created_at > %s
                    ORDER BY created_at ASC
                    LIMIT 1
                """, (symbol, close_time))
                next_pos = cursor.fetchone()

                if next_pos:
                    expected_side = 'LONG' if reversal_type == 'long' else 'SHORT'
                    time_diff = (next_pos['created_at'] - close_time).total_seconds()

                    if next_pos['position_side'] == expected_side:
                        if time_diff < 60:
                            print(f"  ✅ 反转开仓正常: ID={next_pos['id']}, {time_diff:.0f}秒后")
                        else:
                            print(f"  ⚠️ 反转开仓延迟: ID={next_pos['id']}, {time_diff/60:.1f}分钟后")
                            reversal_stats['missing_entry'] += 1
                    else:
                        print(f"  ❌ 反转方向错误: 期望{expected_side}, 实际{next_pos['position_side']}")
                        reversal_stats['missing_entry'] += 1
                else:
                    print(f"  ❌ 没有后续开仓!")
                    reversal_stats['missing_entry'] += 1

        print("\n" + "-" * 40)
        print("统计:")
        print(f"  金叉反转平仓: {reversal_stats['golden_cross']} 次")
        print(f"  死叉反转平仓: {reversal_stats['death_cross']} 次")
        print(f"  趋势反转平仓: {reversal_stats['trend']} 次")
        print(f"  缺失/延迟反转开仓: {reversal_stats['missing_entry']} 次")

        if reversal_stats['missing_entry'] > 0:
            print(f"\n❌ 发现问题: 有 {reversal_stats['missing_entry']} 次反转开仓未能及时执行!")
        else:
            print(f"\n✅ 所有反转开仓都正常执行")

    finally:
        cursor.close()
        conn.close()


def test_check_table_structure():
    """检查表结构是否有 close_reason 字段"""
    print("\n" + "=" * 60)
    print("测试 3: 检查数据库表结构")
    print("=" * 60)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("DESCRIBE futures_positions")
        columns = cursor.fetchall()

        column_names = [c['Field'] for c in columns]

        print(f"\nfutures_positions 表有 {len(columns)} 个字段:")
        for c in columns:
            print(f"  - {c['Field']}: {c['Type']}")

        print("\n关键字段检查:")
        if 'close_reason' in column_names:
            print("  ✅ 有 close_reason 字段")
        else:
            print("  ❌ 没有 close_reason 字段 (平仓原因存在 notes 字段)")

        if 'notes' in column_names:
            print("  ✅ 有 notes 字段 (用于存储平仓原因)")
        else:
            print("  ❌ 没有 notes 字段")

        if 'entry_reason' in column_names:
            print("  ✅ 有 entry_reason 字段 (用于存储开仓原因)")
        else:
            print("  ❌ 没有 entry_reason 字段")

    finally:
        cursor.close()
        conn.close()


def main():
    print("=" * 60)
    print("反转开仓逻辑 - 真实数据库测试")
    print("=" * 60)

    try:
        test_check_table_structure()
        test_ltc_positions()
        test_all_reversal_positions()

        print("\n" + "=" * 60)
        print("测试完成")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n❌ 测试异常: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
