#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
超级大脑V3完整开仓流程测试
模拟信号生成、K线确认、订单执行、数据库记录全流程
"""

import pymysql
import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv

# 设置控制台编码
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()


def test_v3_complete_flow():
    """测试V3完整开仓流程"""

    print("\n" + "="*100)
    print("超级大脑V3.0完整开仓流程测试")
    print("="*100 + "\n")

    # ========== 1. 测试参数配置 ==========
    db_config = {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME')
    }

    account_id = 2
    symbol = 'BTC/USDT'
    position_side = 'LONG'
    leverage = 10
    total_margin = 600.0  # $600保证金

    # ========== 2. 模拟V3信号评分 ==========
    print("步骤1: 信号评分")
    print("-" * 100)

    # 模拟Big4信号 (满分3分)
    big4_signal = 'BULL'
    big4_strength = 12  # 强度12 → 得分2.4分
    big4_score = 2.4

    # 模拟5H趋势 (满分7分)
    # 假设最近3根5H K线: 2根阳线
    klines_5h_bull_count = 2
    trend_5h_score = 4.0  # 2根阳线 → 4分

    # 模拟15M信号 (满分12分)
    # 假设最近8根15M K线: 6根阳线
    klines_15m_bull_count = 6
    signal_15m_score = 12.0  # 6根阳线 → 12分

    # 模拟量价配合 (满分10分)
    # 假设最近8根15M K线: 5根大阳线
    volume_price_score = 7.0  # 5根大阳线 → 7分

    # 模拟技术指标 (满分10分)
    # RSI=45, MACD金叉, 布林带中轨上方
    technical_score = 6.0  # 适中 → 6分

    # 计算总分
    total_score = big4_score + trend_5h_score + signal_15m_score + volume_price_score + technical_score
    max_score = 42
    score_pct = (total_score / max_score) * 100

    signal_breakdown = {
        'big4': big4_score,
        '5h_trend': trend_5h_score,
        '15m_signal': signal_15m_score,
        'volume_price': volume_price_score,
        'technical': technical_score
    }

    print(f"  Big4信号: {big4_signal} (强度{big4_strength}) → {big4_score}分")
    print(f"  5H趋势: {klines_5h_bull_count}/3根阳线 → {trend_5h_score}分")
    print(f"  15M信号: {klines_15m_bull_count}/8根阳线 → {signal_15m_score}分")
    print(f"  量价配合: 5/8根大阳线 → {volume_price_score}分")
    print(f"  技术指标: RSI适中+MACD金叉 → {technical_score}分")
    print(f"  ──────────────────────────────")
    print(f"  总分: {total_score:.1f}/{max_score} ({score_pct:.1f}%)")
    print(f"  ✅ 评分通过 (阈值25分, 实际{total_score:.1f}分)\n")

    # ========== 3. 模拟5M K线确认入场 ==========
    print("步骤2: 等待5M K线确认")
    print("-" * 100)

    # 模拟当前5M K线 (阳线)
    current_5m_kline = {
        'open': 95000.0,
        'close': 95300.0,  # 收盘价 > 开盘价 → 阳线
        'high': 95350.0,
        'low': 94980.0,
        'volume': 8500.0
    }

    is_bullish = current_5m_kline['close'] > current_5m_kline['open']

    print(f"  当前5M K线:")
    print(f"    开盘: ${current_5m_kline['open']:.2f}")
    print(f"    收盘: ${current_5m_kline['close']:.2f}")
    print(f"    最高: ${current_5m_kline['high']:.2f}")
    print(f"    最低: ${current_5m_kline['low']:.2f}")
    print(f"    成交量: {current_5m_kline['volume']:.0f}")

    if position_side == 'LONG' and is_bullish:
        print(f"  ✅ 做多信号 + 5M阳线确认 → 允许入场\n")
        entry_confirmed = True
        entry_price = current_5m_kline['close']
    elif position_side == 'SHORT' and not is_bullish:
        print(f"  ✅ 做空信号 + 5M阴线确认 → 允许入场\n")
        entry_confirmed = True
        entry_price = current_5m_kline['close']
    else:
        print(f"  ❌ K线方向不匹配，放弃开仓\n")
        entry_confirmed = False
        return False

    # ========== 4. 计算订单参数 ==========
    print("步骤3: 计算订单参数")
    print("-" * 100)

    quantity = (total_margin * leverage) / entry_price
    notional_value = quantity * entry_price

    # 计算止盈止损
    stop_loss_pct = 3.0
    take_profit_pct = 6.0

    if position_side == 'LONG':
        stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
        take_profit_price = entry_price * (1 + take_profit_pct / 100)
    else:
        stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
        take_profit_price = entry_price * (1 - take_profit_pct / 100)

    print(f"  交易对: {symbol}")
    print(f"  方向: {position_side}")
    print(f"  入场价: ${entry_price:.2f}")
    print(f"  数量: {quantity:.6f}")
    print(f"  杠杆: {leverage}x")
    print(f"  保证金: ${total_margin:.2f}")
    print(f"  名义价值: ${notional_value:.2f}")
    print(f"  止损价: ${stop_loss_price:.2f} (-{stop_loss_pct}%)")
    print(f"  止盈价: ${take_profit_price:.2f} (+{take_profit_pct}%)\n")

    # ========== 5. 模拟下单 ==========
    print("步骤4: 执行市价单")
    print("-" * 100)

    order_id = f"V3_ORDER_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    order_side = 'BUY' if position_side == 'LONG' else 'SELL'

    print(f"  订单ID: {order_id}")
    print(f"  订单方向: {order_side}")
    print(f"  订单价格: ${entry_price:.2f}")
    print(f"  订单数量: {quantity:.6f}")
    print(f"  ✅ 订单成交\n")

    # ========== 6. 写入数据库 ==========
    print("步骤5: 创建持仓记录")
    print("-" * 100)

    try:
        conn = pymysql.connect(**db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        # 插入持仓记录
        cursor.execute("""
            INSERT INTO futures_positions
            (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
             leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
             stop_loss_pct, take_profit_pct,
             entry_signal_type, entry_score, signal_components,
             entry_signal_time, source, status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW())
        """, (
            account_id,
            symbol,
            position_side,
            quantity,
            entry_price,
            entry_price,
            leverage,
            notional_value,
            total_margin,
            stop_loss_price,
            take_profit_price,
            stop_loss_pct,
            take_profit_pct,
            'v3_single_entry',
            total_score,
            json.dumps(signal_breakdown),
            datetime.now(),
            'v3_complete_test'
        ))

        position_id = cursor.lastrowid
        print(f"  ✅ 持仓记录已创建: ID={position_id}")

        # 冻结保证金
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET current_balance = current_balance - %s,
                frozen_balance = frozen_balance + %s,
                updated_at = NOW()
            WHERE id = %s
        """, (total_margin, total_margin, account_id))

        affected_rows = cursor.rowcount
        print(f"  ✅ 保证金已冻结: ${total_margin:.2f} (影响行数: {affected_rows})")

        conn.commit()

        # ========== 7. 验证数据库记录 ==========
        print("\n步骤6: 验证数据库记录")
        print("-" * 100)

        cursor.execute("""
            SELECT
                id, symbol, position_side, quantity, entry_price, avg_entry_price,
                leverage, margin, notional_value, stop_loss_price, take_profit_price,
                stop_loss_pct, take_profit_pct,
                entry_signal_type, entry_score, signal_components,
                status, created_at
            FROM futures_positions
            WHERE id = %s
        """, (position_id,))

        record = cursor.fetchone()

        if record:
            print(f"  ✅ 数据库记录验证成功!\n")
            print(f"  持仓详情:")
            print(f"    ID: {record['id']}")
            print(f"    交易对: {record['symbol']}")
            print(f"    方向: {record['position_side']}")
            print(f"    状态: {record['status']}")
            print(f"    数量: {record['quantity']:.6f}")
            print(f"    入场价: ${record['entry_price']:.2f}")
            print(f"    杠杆: {record['leverage']}x")
            print(f"    保证金: ${record['margin']:.2f}")
            print(f"    名义价值: ${record['notional_value']:.2f}")
            print(f"    止损价: ${record['stop_loss_price']:.2f} ({record['stop_loss_pct']:.1f}%)")
            print(f"    止盈价: ${record['take_profit_price']:.2f} ({record['take_profit_pct']:.1f}%)")
            print(f"    信号类型: {record['entry_signal_type']}")
            print(f"    信号评分: {record['entry_score']:.1f}")
            print(f"    创建时间: {record['created_at']}")

            print(f"\n  信号评分明细:")
            signal_comp = json.loads(record['signal_components'])
            for key, value in signal_comp.items():
                print(f"      {key}: {value:.1f}分")

        # 验证账户余额
        cursor.execute("""
            SELECT id, current_balance, frozen_balance
            FROM futures_trading_accounts
            WHERE id = %s
        """, (account_id,))

        account = cursor.fetchone()
        if account:
            print(f"\n  账户状态 (ID={account['id']}):")
            print(f"    当前余额: ${account['current_balance']:.2f}")
            print(f"    冻结保证金: ${account['frozen_balance']:.2f}")

        cursor.close()
        conn.close()

        print("\n" + "="*100)
        print("✅ 超级大脑V3完整开仓流程测试成功!")
        print("="*100)
        print(f"\n测试摘要:")
        print(f"  持仓ID: {position_id}")
        print(f"  交易对: {symbol}")
        print(f"  方向: {position_side}")
        print(f"  入场价: ${entry_price:.2f}")
        print(f"  数量: {quantity:.6f}")
        print(f"  保证金: ${total_margin:.2f}")
        print(f"  信号评分: {total_score:.1f}/{max_score} ({score_pct:.1f}%)")
        print(f"  止损价: ${stop_loss_price:.2f}")
        print(f"  止盈价: ${take_profit_price:.2f}")
        print("="*100 + "\n")

        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        if 'conn' in locals():
            conn.rollback()
            conn.close()
        return False


if __name__ == '__main__':
    success = test_v3_complete_flow()
    sys.exit(0 if success else 1)
