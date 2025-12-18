#!/usr/bin/env python3
"""
模拟反转开仓流程测试

模拟一个完整的反转流程：
1. 创建一个模拟的 SHORT 持仓
2. 模拟金叉信号触发反转平仓
3. 验证是否立即触发反转开仓 LONG

可以连接真实数据库进行测试
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from datetime import datetime
import pymysql


# 数据库配置（本地测试使用远程数据库）
DB_CONFIG = {
    'host': '127.0.0.1',  # 本地运行时改成服务器IP
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data',
    'charset': 'utf8mb4'
}


def get_db_connection():
    return pymysql.connect(**DB_CONFIG, cursorclass=pymysql.cursors.DictCursor)


def calculate_ema(prices, period):
    """计算 EMA"""
    if len(prices) < period:
        return []

    k = 2 / (period + 1)
    ema_values = [sum(prices[:period]) / period]  # SMA 作为初始值

    for price in prices[period:]:
        ema_values.append(price * k + ema_values[-1] * (1 - k))

    return ema_values


def get_real_ema_data(symbol: str, timeframe: str = '15m'):
    """从数据库获取真实的 EMA 数据"""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT timestamp, close_price
            FROM kline_data
            WHERE symbol = %s AND timeframe = %s AND exchange = 'binance_futures'
            ORDER BY timestamp DESC
            LIMIT 50
        """, (symbol, timeframe))

        klines = list(reversed(cursor.fetchall()))

        if len(klines) < 30:
            print(f"  ⚠️ K线数据不足: {len(klines)} 条")
            return None

        close_prices = [float(k['close_price']) for k in klines]

        ema9_values = calculate_ema(close_prices, 9)
        ema26_values = calculate_ema(close_prices, 26)

        if not ema9_values or not ema26_values:
            return None

        return {
            'ema9': ema9_values[-1],
            'ema26': ema26_values[-1],
            'prev_ema9': ema9_values[-2] if len(ema9_values) >= 2 else ema9_values[-1],
            'prev_ema26': ema26_values[-2] if len(ema26_values) >= 2 else ema26_values[-1],
            'current_price': close_prices[-1],
            'last_timestamp': klines[-1]['timestamp'],
            'prev_timestamp': klines[-2]['timestamp'] if len(klines) >= 2 else None,
        }

    finally:
        cursor.close()
        conn.close()


def check_cross(ema_data):
    """检查金叉/死叉"""
    ema9 = ema_data['ema9']
    ema26 = ema_data['ema26']
    prev_ema9 = ema_data['prev_ema9']
    prev_ema26 = ema_data['prev_ema26']

    # 金叉：之前 EMA9 <= EMA26，现在 EMA9 > EMA26
    is_golden_cross = prev_ema9 <= prev_ema26 and ema9 > ema26

    # 死叉：之前 EMA9 >= EMA26，现在 EMA9 < EMA26
    is_death_cross = prev_ema9 >= prev_ema26 and ema9 < ema26

    return is_golden_cross, is_death_cross


def test_ltc_ema_data():
    """测试 LTC/USDT 的 EMA 数据"""
    print("\n" + "=" * 60)
    print("测试 LTC/USDT EMA 数据 (检查金叉/死叉)")
    print("=" * 60)

    try:
        ema_data = get_real_ema_data('LTC/USDT', '15m')

        if not ema_data:
            print("  ❌ 无法获取 EMA 数据")
            return False

        print(f"\n  当前价格: {ema_data['current_price']:.4f}")
        print(f"  EMA9: {ema_data['ema9']:.4f}")
        print(f"  EMA26: {ema_data['ema26']:.4f}")
        print(f"  前一根 EMA9: {ema_data['prev_ema9']:.4f}")
        print(f"  前一根 EMA26: {ema_data['prev_ema26']:.4f}")
        print(f"  最后K线时间: {ema_data['last_timestamp']}")

        is_golden, is_death = check_cross(ema_data)

        print(f"\n  金叉判断: prev_ema9({ema_data['prev_ema9']:.4f}) <= prev_ema26({ema_data['prev_ema26']:.4f}) AND ema9({ema_data['ema9']:.4f}) > ema26({ema_data['ema26']:.4f})")
        print(f"  结果: {is_golden}")

        print(f"\n  死叉判断: prev_ema9({ema_data['prev_ema9']:.4f}) >= prev_ema26({ema_data['prev_ema26']:.4f}) AND ema9({ema_data['ema9']:.4f}) < ema26({ema_data['ema26']:.4f})")
        print(f"  结果: {is_death}")

        if is_golden:
            print("\n  ✅ 当前是金叉状态")
        elif is_death:
            print("\n  ✅ 当前是死叉状态")
        else:
            # 判断趋势
            if ema_data['ema9'] > ema_data['ema26']:
                print("\n  📈 当前 EMA9 > EMA26 (多头趋势，但不是刚发生的金叉)")
            else:
                print("\n  📉 当前 EMA9 < EMA26 (空头趋势，但不是刚发生的死叉)")

        return True

    except Exception as e:
        print(f"  ❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_all_symbols():
    """测试所有交易对的金叉/死叉状态"""
    print("\n" + "=" * 60)
    print("检查所有交易对的金叉/死叉状态")
    print("=" * 60)

    symbols = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'DOGE/USDT', 'SOL/USDT', 'LTC/USDT', 'HYPE/USDT']

    for symbol in symbols:
        try:
            ema_data = get_real_ema_data(symbol, '15m')
            if not ema_data:
                print(f"  {symbol}: ⚠️ 数据不足")
                continue

            is_golden, is_death = check_cross(ema_data)

            ema_diff = ema_data['ema9'] - ema_data['ema26']
            ema_diff_pct = abs(ema_diff) / ema_data['ema26'] * 100

            if is_golden:
                status = "🔴 金叉!"
            elif is_death:
                status = "🔵 死叉!"
            elif ema_data['ema9'] > ema_data['ema26']:
                status = f"📈 多头趋势 (+{ema_diff_pct:.3f}%)"
            else:
                status = f"📉 空头趋势 (-{ema_diff_pct:.3f}%)"

            print(f"  {symbol}: {status}")

        except Exception as e:
            print(f"  {symbol}: ❌ 错误 {e}")


def test_reversal_simulation():
    """模拟反转流程"""
    print("\n" + "=" * 60)
    print("模拟反转开仓流程")
    print("=" * 60)

    # 假设我们持有 LTC/USDT SHORT 仓位
    # 模拟平仓后的逻辑

    print("\n场景: 持有 LTC/USDT SHORT，发生金叉反转平仓")

    # 模拟 positions 列表
    position = {
        'id': 708,
        'symbol': 'LTC/USDT',
        'position_side': 'SHORT',
        'status': 'closed',  # 已平仓
        'close_reason': '金叉反转平仓(不检查强度)',  # 这是关键！
    }

    positions = [position]

    # 模拟 _execute_symbol 中的反转检测逻辑
    print("\n--- 反转检测逻辑 ---")

    has_open_position = any(p.get('status') == 'open' for p in positions)
    print(f"  has_open_position: {has_open_position}")

    reversal_direction = None
    for p in positions:
        p_status = p.get('status')
        p_reason = p.get('close_reason', '')
        print(f"  检查: id={p.get('id')}, status={p_status}, close_reason={p_reason}")

        if p_status == 'closed':
            if '金叉反转平仓' in p_reason:
                reversal_direction = 'long'
                print(f"  🔄 检测到金叉反转平仓，准备开多")
                break
            elif '死叉反转平仓' in p_reason:
                reversal_direction = 'short'
                print(f"  🔄 检测到死叉反转平仓，准备开空")
                break

    print(f"\n  reversal_direction: {reversal_direction}")

    # 关键条件判断
    print("\n--- 开仓条件判断 ---")
    print(f"  if not positions or not has_open_position:")
    print(f"     not positions = {not positions}")
    print(f"     not has_open_position = {not has_open_position}")
    print(f"     结果 = {not positions or not has_open_position}")

    if not positions or not has_open_position:
        if reversal_direction:
            print(f"\n  ✅ 条件满足，应该执行反转开仓: {reversal_direction}")
            print(f"     调用: execute_open_position(symbol='LTC/USDT', direction='{reversal_direction}', signal_type='reversal_cross', force_market=True)")
            return True
        else:
            print(f"\n  ❌ reversal_direction 为 None，不执行反转开仓")
            print(f"     这说明 close_reason 没有包含 '金叉反转平仓' 或 '死叉反转平仓'")
            return False
    else:
        print(f"\n  ❌ 条件不满足: 还有未平仓的持仓")
        return False


def test_db_position_close_reason():
    """检查数据库中的 close_reason 是否正确存储"""
    print("\n" + "=" * 60)
    print("检查数据库中反转平仓记录的 close_reason")
    print("=" * 60)

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 检查最近的金叉/死叉反转平仓
        cursor.execute("""
            SELECT id, symbol, position_side, status, notes, close_time
            FROM futures_positions
            WHERE notes LIKE '%反转平仓%'
            ORDER BY close_time DESC
            LIMIT 5
        """)
        positions = cursor.fetchall()

        print(f"\n找到 {len(positions)} 条反转平仓记录:")

        for p in positions:
            print(f"\n  ID: {p['id']}")
            print(f"  交易对: {p['symbol']}")
            print(f"  方向: {p['position_side']}")
            print(f"  状态: {p['status']}")
            print(f"  notes: {p['notes']}")
            print(f"  平仓时间: {p['close_time']}")

            # 检查 notes 中是否包含正确的反转关键词
            notes = p['notes'] or ''
            if '金叉反转平仓' in notes:
                print(f"  ✅ 包含 '金叉反转平仓'")
            elif '死叉反转平仓' in notes:
                print(f"  ✅ 包含 '死叉反转平仓'")
            elif '趋势反转平仓' in notes:
                print(f"  ⚠️ 只包含 '趋势反转平仓' (不会触发反转开仓)")
            else:
                print(f"  ❌ 不包含反转关键词")

        return True

    finally:
        cursor.close()
        conn.close()


def main():
    print("=" * 60)
    print("反转开仓逻辑测试")
    print(f"时间: {datetime.now()}")
    print("=" * 60)

    # 测试1：检查 LTC EMA 数据
    test_ltc_ema_data()

    # 测试2：检查所有交易对
    test_all_symbols()

    # 测试3：模拟反转流程
    test_reversal_simulation()

    # 测试4：检查数据库记录
    test_db_position_close_reason()

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    print("\n💡 结论:")
    print("  如果代码逻辑正确但反转开仓没有执行，可能的原因:")
    print("  1. 平仓时 check_smart_exit() 返回的是 '趋势反转平仓' 而不是 '金叉反转平仓'")
    print("  2. EMA 数据中 prev_ema9/prev_ema26 计算不正确")
    print("  3. 金叉/死叉条件不满足（EMA 只是在缓慢接近，没有真正交叉）")
    print("  4. 执行过程中有异常导致流程中断")


if __name__ == '__main__':
    main()
