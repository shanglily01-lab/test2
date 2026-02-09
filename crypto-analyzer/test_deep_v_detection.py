#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试深V反弹检测机制
验证2026-02-06 00:00 (北京时间08:00)的案例是否能被正确识别
"""

import sys
import os
from datetime import datetime, timedelta
import pymysql
from dotenv import load_dotenv

# 添加路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv()

print("=" * 100)
print("深V反弹检测机制测试")
print("=" * 100)
print()

# 数据库配置
db_config = {
    'host': os.getenv('DB_HOST'),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

def test_case_2026_02_06():
    """测试2026-02-06 00:00 (北京时间08:00)的真实案例"""

    print("[TEST] 测试案例: 2026-02-06 00:00 UTC (北京时间 08:00)")
    print("-" * 100)

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 目标时间: 2026-02-06 00:00:00 UTC
    target_time = datetime(2026, 2, 6, 0, 0, 0)
    target_ts = int(target_time.timestamp() * 1000)

    BIG4 = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT']

    LOWER_SHADOW_THRESHOLD = 3.0  # 3%下影线阈值

    results = {}

    for symbol in BIG4:
        print(f"\n{'='*100}")
        print(f"检测 {symbol}")
        print('='*100)

        # 1. 获取目标1H K线
        cursor.execute("""
            SELECT open_time, open_price, high_price, low_price, close_price, volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND exchange = 'binance_futures'
            AND open_time = %s
        """, (symbol, target_ts))

        h1_candle = cursor.fetchone()

        if not h1_candle:
            print(f"❌ 未找到目标K线")
            continue

        open_p = float(h1_candle['open_price'])
        high_p = float(h1_candle['high_price'])
        low_p = float(h1_candle['low_price'])
        close_p = float(h1_candle['close_price'])
        volume = float(h1_candle['volume'])

        # 计算K线特征
        total_range = high_p - low_p
        body = abs(close_p - open_p)
        body_low = min(open_p, close_p)
        body_high = max(open_p, close_p)

        upper_shadow = high_p - body_high
        lower_shadow = body_low - low_p

        lower_shadow_pct = (lower_shadow / low_p * 100) if low_p > 0 else 0
        upper_shadow_pct = (upper_shadow / high_p * 100) if high_p > 0 else 0
        body_pct = (body / open_p * 100) if open_p > 0 else 0

        drop_from_open = (open_p - low_p) / open_p * 100
        change_pct = (close_p - open_p) / open_p * 100

        print(f"\n1️⃣ 1H K线特征 (2026-02-06 00:00)")
        print(f"   开盘: ${open_p:,.2f}")
        print(f"   最高: ${high_p:,.2f}")
        print(f"   最低: ${low_p:,.2f}  (从开盘暴跌 {drop_from_open:.2f}%)")
        print(f"   收盘: ${close_p:,.2f}  (涨跌 {change_pct:+.2f}%)")
        print(f"   成交量: {volume:,.0f}")
        print()
        print(f"   下影线: {lower_shadow_pct:.2f}%  {'✅ >3%' if lower_shadow_pct >= LOWER_SHADOW_THRESHOLD else '❌ <3%'}")
        print(f"   上影线: {upper_shadow_pct:.2f}%")
        print(f"   实体: {body_pct:.2f}%")

        # 2. 检查大周期趋势
        print(f"\n2️⃣ 大周期趋势检查")

        # 获取前72H的K线
        cursor.execute("""
            SELECT high_price, low_price, open_time, open_price, close_price
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '1h'
            AND exchange = 'binance_futures'
            AND open_time <= %s
            ORDER BY open_time DESC
            LIMIT 72
        """, (symbol, target_ts))

        history_72h = cursor.fetchall()

        if len(history_72h) >= 24:
            # 计算72H和24H的最高点
            high_72h = max([float(k['high_price']) for k in history_72h])
            high_24h = max([float(k['high_price']) for k in history_72h[:24]])

            # 从高点到触底的跌幅
            drop_from_high_72h = (low_p - high_72h) / high_72h * 100
            drop_from_high_24h = (low_p - high_24h) / high_24h * 100

            print(f"   72H最高: ${high_72h:,.2f} → 跌幅: {drop_from_high_72h:.2f}%  {'✅ <-8%' if drop_from_high_72h <= -8.0 else '❌ >-8%'}")
            print(f"   24H最高: ${high_24h:,.2f} → 跌幅: {drop_from_high_24h:.2f}%  {'✅ <-4%' if drop_from_high_24h <= -4.0 else '❌ >-4%'}")

            # 检查是否首次触底
            is_first_bottom = True
            previous_long_shadows = []

            for prev_k in history_72h[:24]:
                if prev_k['open_time'] == h1_candle['open_time']:
                    continue

                prev_open = float(prev_k.get('open_price', 0))
                prev_close = float(prev_k.get('close_price', 0))
                prev_low = float(prev_k['low_price'])

                if prev_open > 0 and prev_close > 0 and prev_low > 0:
                    prev_body_low = min(prev_open, prev_close)
                    prev_shadow_pct = (prev_body_low - prev_low) / prev_low * 100

                    if prev_shadow_pct >= LOWER_SHADOW_THRESHOLD:
                        is_first_bottom = False
                        prev_time = datetime.fromtimestamp(int(prev_k['open_time']) / 1000)
                        previous_long_shadows.append(f"{prev_time.strftime('%m-%d %H:%M')} ({prev_shadow_pct:.1f}%)")

            print(f"   首次触底: {'✅ 24H内首次' if is_first_bottom else f'❌ 前有长影线: {previous_long_shadows[:3]}'}")

            # 综合判断
            is_sustained_drop = drop_from_high_72h <= -8.0
            is_accelerating = drop_from_high_24h <= -4.0

            is_true_deep_v = lower_shadow_pct >= LOWER_SHADOW_THRESHOLD and is_sustained_drop and is_accelerating and is_first_bottom

            print(f"\n3️⃣ 深V反转判断")
            print(f"   {'✅' if lower_shadow_pct >= LOWER_SHADOW_THRESHOLD else '❌'} 长下影线 >3%: {lower_shadow_pct:.2f}%")
            print(f"   {'✅' if is_sustained_drop else '❌'} 72H持续下跌 ≥8%: {drop_from_high_72h:.2f}%")
            print(f"   {'✅' if is_accelerating else '❌'} 24H加速下跌 ≥4%: {drop_from_high_24h:.2f}%")
            print(f"   {'✅' if is_first_bottom else '❌'} 首次触底")
            print()
            print(f"   {'🚀🚀🚀 真深V反转!' if is_true_deep_v else '❌ 不满足真深V条件'}")

            results[symbol] = {
                'lower_shadow_pct': lower_shadow_pct,
                'drop_72h': drop_from_high_72h,
                'drop_24h': drop_from_high_24h,
                'is_first_bottom': is_first_bottom,
                'is_true_deep_v': is_true_deep_v
            }
        else:
            print(f"   ❌ 数据不足 (只有{len(history_72h)}根K线)")
            results[symbol] = {'is_true_deep_v': False, 'reason': '数据不足'}

        # 4. 检查后续15M反弹
        print(f"\n4️⃣ 后续15M反弹检查")

        h1_start_time = target_ts
        h1_end_time = h1_start_time + 3600000 * 2  # 后2小时

        cursor.execute("""
            SELECT open_price, close_price, open_time
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = '15m'
            AND exchange = 'binance_futures'
            AND open_time >= %s
            AND open_time <= %s
            ORDER BY open_time ASC
            LIMIT 8
        """, (symbol, h1_start_time, h1_end_time))

        m15_candles = cursor.fetchall()

        if m15_candles:
            consecutive_green = 0
            max_consecutive = 0
            green_details = []

            for m15 in m15_candles:
                m15_open = float(m15['open_price'])
                m15_close = float(m15['close_price'])
                m15_time = datetime.fromtimestamp(int(m15['open_time']) / 1000)

                change = (m15_close - m15_open) / m15_open * 100

                if m15_close > m15_open:  # 阳线
                    consecutive_green += 1
                    max_consecutive = max(max_consecutive, consecutive_green)
                    green_details.append(f"{m15_time.strftime('%H:%M')}(+{change:.2f}%)")
                else:
                    if consecutive_green > 0:
                        green_details.append(f"| 中断 |")
                    consecutive_green = 0
                    green_details.append(f"{m15_time.strftime('%H:%M')}({change:.2f}%)")

            print(f"   后续15M K线: {len(m15_candles)}根")
            print(f"   最大连续阳线: {max_consecutive}根  {'✅ ≥3根' if max_consecutive >= 3 else '❌ <3根'}")
            print(f"   详情: {' '.join(green_details[:8])}")

            if 'is_true_deep_v' in results[symbol]:
                results[symbol]['consecutive_15m'] = max_consecutive
        else:
            print(f"   ❌ 无15M数据")

    cursor.close()
    conn.close()

    # 总结
    print(f"\n{'='*100}")
    print("📊 Big4检测总结")
    print('='*100)

    true_deep_v_count = sum(1 for r in results.values() if r.get('is_true_deep_v', False))

    print(f"\n真深V反转数量: {true_deep_v_count}/4")
    print()

    for symbol, result in results.items():
        if result.get('is_true_deep_v'):
            print(f"✅ {symbol:10} - 下影{result['lower_shadow_pct']:.1f}%, "
                  f"72H跌{result['drop_72h']:.1f}%, 24H跌{result['drop_24h']:.1f}%, "
                  f"{'首次触底' if result['is_first_bottom'] else '非首次'}, "
                  f"后续连续{result.get('consecutive_15m', 0)}阳")
        else:
            print(f"❌ {symbol:10} - {result.get('reason', '不满足条件')}")

    print()
    if true_deep_v_count >= 1:
        print("🚀 应该触发反弹窗口!")
        print("   - 创建bounce_window记录")
        print("   - 创建emergency_intervention记录(禁止做空2小时)")
        print("   - 45分钟内全市场抢反弹")
    else:
        print("⚠️ 不满足触发条件")

    return results


def check_bounce_window_records():
    """检查bounce_window表中的记录"""
    print(f"\n{'='*100}")
    print("📋 检查bounce_window表记录")
    print('='*100)

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    # 检查2026-02-06前后的记录
    cursor.execute("""
        SELECT id, symbol, trigger_time, window_start, window_end,
               lower_shadow_pct, trigger_price, bounce_entered, created_at
        FROM bounce_window
        WHERE trigger_time >= '2026-02-05 00:00:00'
        AND trigger_time <= '2026-02-07 00:00:00'
        ORDER BY trigger_time DESC
    """)

    records = cursor.fetchall()

    if records:
        print(f"\n找到 {len(records)} 条记录:")
        for r in records:
            window_duration = (r['window_end'] - r['window_start']).total_seconds() / 60
            print(f"\n  ID: {r['id']}")
            print(f"  币种: {r['symbol']}")
            print(f"  触发时间: {r['trigger_time']}")
            print(f"  窗口: {r['window_start'].strftime('%H:%M')} - {r['window_end'].strftime('%H:%M')} ({window_duration:.0f}分钟)")
            print(f"  下影线: {r['lower_shadow_pct']}%")
            print(f"  触发价: ${r['trigger_price']}")
            print(f"  已开仓: {'是' if r['bounce_entered'] else '否'}")
            print(f"  创建时间: {r['created_at']}")
    else:
        print("\n❌ 未找到记录")
        print("   可能原因:")
        print("   1. big4_trend_detector未运行")
        print("   2. 不满足检测条件（72H跌幅、24H跌幅、首次触底）")
        print("   3. 检测时间不对")

    cursor.close()
    conn.close()


def check_emergency_intervention():
    """检查emergency_intervention表记录"""
    print(f"\n{'='*100}")
    print("📋 检查emergency_intervention表记录")
    print('='*100)

    conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, intervention_type, block_long, block_short,
               trigger_reason, expires_at, created_at
        FROM emergency_intervention
        WHERE created_at >= '2026-02-05 00:00:00'
        AND created_at <= '2026-02-07 00:00:00'
        ORDER BY created_at DESC
    """)

    records = cursor.fetchall()

    if records:
        print(f"\n找到 {len(records)} 条记录:")
        for r in records:
            print(f"\n  ID: {r['id']}")
            print(f"  类型: {r['intervention_type']}")
            print(f"  禁止做多: {'是' if r['block_long'] else '否'}")
            print(f"  禁止做空: {'是' if r['block_short'] else '否'}")
            print(f"  触发原因: {r['trigger_reason']}")
            print(f"  失效时间: {r['expires_at']}")
            print(f"  创建时间: {r['created_at']}")
    else:
        print("\n❌ 未找到记录")

    cursor.close()
    conn.close()


if __name__ == '__main__':
    # 1. 测试2026-02-06案例
    results = test_case_2026_02_06()

    # 2. 检查数据库记录
    check_bounce_window_records()

    # 3. 检查紧急干预记录
    check_emergency_intervention()

    print(f"\n{'='*100}")
    print("测试完成!")
    print('='*100)
