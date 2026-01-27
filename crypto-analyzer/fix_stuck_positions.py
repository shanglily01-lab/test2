#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复卡住的持仓 - 手动平仓工具

问题：某些持仓反复触发止损但平仓未完成，导致重复尝试平仓
解决：手动将这些持仓标记为已平仓
"""

import pymysql
from datetime import datetime
from decimal import Decimal
import yaml
from loguru import logger

def load_db_config():
    """从 config.yaml 加载数据库配置"""
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config['database']

def get_stuck_positions(cursor):
    """
    查找可能卡住的持仓
    标准：status='open' 且触发了止损条件但未平仓
    """
    cursor.execute("""
        SELECT
            p.id,
            p.symbol,
            p.position_side,
            p.quantity,
            p.entry_price,
            p.stop_loss_price,
            p.take_profit_price,
            p.margin,
            p.leverage,
            p.open_time,
            p.updated_at
        FROM futures_positions p
        WHERE p.status = 'open'
        AND p.account_id = 2
        ORDER BY p.updated_at ASC
    """)

    return cursor.fetchall()

def check_should_close(position, current_price):
    """
    检查持仓是否应该被平仓（触发止损/止盈）

    Returns:
        (should_close, reason, pnl_pct)
    """
    pos_id = position['id']
    symbol = position['symbol']
    side = position['position_side']
    entry_price = float(position['entry_price'])
    stop_loss = float(position['stop_loss_price']) if position['stop_loss_price'] else None
    take_profit = float(position['take_profit_price']) if position['take_profit_price'] else None

    # 计算盈亏百分比
    if side == 'LONG':
        pnl_pct = (current_price - entry_price) / entry_price * 100

        # 检查止损
        if stop_loss and current_price <= stop_loss:
            return True, 'stop_loss', pnl_pct

        # 检查止盈
        if take_profit and current_price >= take_profit:
            return True, 'take_profit', pnl_pct

    else:  # SHORT
        pnl_pct = (entry_price - current_price) / entry_price * 100

        # 检查止损
        if stop_loss and current_price >= stop_loss:
            return True, 'stop_loss', pnl_pct

        # 检查止盈
        if take_profit and current_price <= take_profit:
            return True, 'take_profit', pnl_pct

    return False, None, pnl_pct

def get_current_price_from_db(cursor, symbol):
    """从数据库K线数据获取当前价格"""
    cursor.execute("""
        SELECT close_price
        FROM kline_data
        WHERE symbol = %s AND timeframe = '5m'
        ORDER BY open_time DESC
        LIMIT 1
    """, (symbol,))

    result = cursor.fetchone()
    if result and result['close_price']:
        return float(result['close_price'])
    return None

def manual_close_position(conn, cursor, position, current_price, reason):
    """
    手动平仓持仓
    """
    pos_id = position['id']
    symbol = position['symbol']
    side = position['position_side']
    entry_price = Decimal(str(position['entry_price']))
    quantity = Decimal(str(position['quantity']))
    margin = Decimal(str(position['margin']))
    leverage = position['leverage']
    account_id = 2

    current_price_dec = Decimal(str(current_price))

    # 计算盈亏
    if side == 'LONG':
        pnl = (current_price_dec - entry_price) * quantity
    else:  # SHORT
        pnl = (entry_price - current_price_dec) * quantity

    # 手续费
    fee = current_price_dec * quantity * Decimal('0.0004')
    realized_pnl = pnl - fee

    # 盈亏百分比
    open_value = entry_price * quantity
    pnl_pct = (pnl / open_value * 100) if open_value > 0 else 0
    roi = (pnl / margin * 100) if margin > 0 else 0

    logger.info(f"手动平仓: #{pos_id} {symbol} {side} | 入场${float(entry_price):.4f} → 平仓${current_price:.4f} | "
                f"盈亏{float(realized_pnl):.2f} USDT ({float(pnl_pct):.2f}%) | 原因: {reason}")

    try:
        # 1. 更新持仓状态
        cursor.execute("""
            UPDATE futures_positions
            SET status = 'closed',
                close_time = %s,
                realized_pnl = %s,
                notes = %s,
                updated_at = %s
            WHERE id = %s
        """, (datetime.utcnow(), float(realized_pnl), f"manual_{reason}", datetime.utcnow(), pos_id))

        # 2. 创建平仓订单记录
        import uuid
        order_id = f"MANUAL-{pos_id}"
        close_side = f"CLOSE_{side}"

        cursor.execute("""
            INSERT INTO futures_orders (
                account_id, order_id, position_id, symbol,
                side, order_type, leverage,
                price, quantity, executed_quantity,
                total_value, executed_value,
                fee, fee_rate, status,
                avg_fill_price, fill_time,
                realized_pnl, pnl_pct,
                order_source, notes
            ) VALUES (
                %s, %s, %s, %s,
                %s, 'MARKET', %s,
                %s, %s, %s,
                %s, %s,
                %s, %s, 'FILLED',
                %s, %s,
                %s, %s,
                'manual_fix', %s
            )
        """, (
            account_id, order_id, pos_id, symbol,
            close_side, leverage,
            float(current_price), float(quantity), float(quantity),
            float(current_price * quantity), float(current_price * quantity),
            float(fee), 0.0004,
            float(current_price), datetime.utcnow(),
            float(realized_pnl), float(pnl_pct),
            f"manual_close_{reason}"
        ))

        # 3. 创建交易记录
        trade_id = str(uuid.uuid4())
        cursor.execute("""
            INSERT INTO futures_trades (
                trade_id, position_id, account_id, symbol, side,
                price, quantity, notional_value, leverage, margin,
                fee, realized_pnl, pnl_pct, roi, entry_price,
                order_id, trade_time, created_at
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s
            )
        """, (
            trade_id, pos_id, account_id, symbol, close_side,
            float(current_price), float(quantity), float(current_price * quantity), leverage, float(margin),
            float(fee), float(realized_pnl), float(pnl_pct), float(roi), float(entry_price),
            order_id, datetime.utcnow(), datetime.utcnow()
        ))

        # 4. 更新账户余额
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET current_balance = current_balance + %s + %s,
                frozen_balance = frozen_balance - %s,
                realized_pnl = realized_pnl + %s,
                total_trades = total_trades + 1,
                winning_trades = winning_trades + IF(%s > 0, 1, 0),
                losing_trades = losing_trades + IF(%s < 0, 1, 0)
            WHERE id = %s
        """, (
            float(margin), float(realized_pnl), float(margin),
            float(realized_pnl), float(realized_pnl), float(realized_pnl),
            account_id
        ))

        # 5. 更新胜率
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET win_rate = (winning_trades / GREATEST(total_trades, 1)) * 100
            WHERE id = %s
        """, (account_id,))

        conn.commit()
        logger.info(f"✅ 持仓 #{pos_id} 手动平仓成功")
        return True

    except Exception as e:
        conn.rollback()
        logger.error(f"❌ 持仓 #{pos_id} 手动平仓失败: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("开始检查并修复卡住的持仓")
    logger.info("=" * 80)

    # 加载数据库配置
    db_config = load_db_config()

    # 连接数据库
    conn = pymysql.connect(
        host=db_config['host'],
        port=db_config['port'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    cursor = conn.cursor()

    try:
        # 1. 获取所有开仓持仓
        positions = get_stuck_positions(cursor)
        logger.info(f"找到 {len(positions)} 个开仓持仓")

        if not positions:
            logger.info("没有需要处理的持仓")
            return

        # 2. 检查每个持仓是否应该平仓
        stuck_positions = []

        for pos in positions:
            pos_id = pos['id']
            symbol = pos['symbol']

            # 获取当前价格
            current_price = get_current_price_from_db(cursor, symbol)
            if not current_price:
                logger.warning(f"持仓 #{pos_id} {symbol} 无法获取当前价格，跳过")
                continue

            # 检查是否应该平仓
            should_close, reason, pnl_pct = check_should_close(pos, current_price)

            if should_close:
                logger.info(f"⚠️ 发现卡住的持仓: #{pos_id} {symbol} {pos['position_side']} | "
                          f"当前价格: {current_price:.6f} | 盈亏: {pnl_pct:.2f}% | 原因: {reason}")
                stuck_positions.append((pos, current_price, reason))

        if not stuck_positions:
            logger.info("✅ 所有持仓状态正常，无需修复")
            return

        # 3. 询问用户是否确认平仓
        logger.info("=" * 80)
        logger.info(f"共发现 {len(stuck_positions)} 个卡住的持仓需要手动平仓")
        logger.info("=" * 80)

        for pos, price, reason in stuck_positions:
            logger.info(f"  #{pos['id']} {pos['symbol']} {pos['position_side']} - {reason}")

        confirm = input("\n是否继续手动平仓这些持仓? (yes/no): ").strip().lower()

        if confirm != 'yes':
            logger.info("取消操作")
            return

        # 4. 执行手动平仓
        success_count = 0
        for pos, price, reason in stuck_positions:
            if manual_close_position(conn, cursor, pos, price, reason):
                success_count += 1

        logger.info("=" * 80)
        logger.info(f"手动平仓完成: 成功 {success_count}/{len(stuck_positions)} 个")
        logger.info("=" * 80)

    finally:
        cursor.close()
        conn.close()

if __name__ == '__main__':
    main()
