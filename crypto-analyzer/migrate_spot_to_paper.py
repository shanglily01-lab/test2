#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据迁移脚本：将 spot_positions 数据迁移到 paper_trading_positions
"""
import sys
import os
import io
import pymysql
from datetime import datetime
from dotenv import load_dotenv

# 设置stdout编码为UTF-8（Windows兼容）
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

# 数据库配置
DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor
}

# 默认账户ID（如果paper_trading_accounts中没有账户，会先创建一个）
DEFAULT_ACCOUNT_ID = 1


def get_connection():
    """获取数据库连接"""
    return pymysql.connect(**DB_CONFIG)


def ensure_default_account(conn):
    """确保存在默认账户"""
    cursor = conn.cursor()

    # 检查是否存在默认账户
    cursor.execute("""
        SELECT id FROM paper_trading_accounts WHERE id = %s
    """, (DEFAULT_ACCOUNT_ID,))

    if cursor.fetchone():
        print(f"✅ 默认账户 ID={DEFAULT_ACCOUNT_ID} 已存在")
        cursor.close()
        return DEFAULT_ACCOUNT_ID

    # 创建默认账户
    cursor.execute("""
        INSERT INTO paper_trading_accounts (
            id, account_name, account_type, initial_balance,
            current_balance, total_equity, status, is_default, created_at
        ) VALUES (
            %s, '现货交易账户', 'spot', 10000.00,
            10000.00, 10000.00, 'active', 1, NOW()
        )
    """, (DEFAULT_ACCOUNT_ID,))

    conn.commit()
    print(f"✅ 创建默认账户 ID={DEFAULT_ACCOUNT_ID}")
    cursor.close()
    return DEFAULT_ACCOUNT_ID


def migrate_spot_positions(conn, account_id):
    """迁移 spot_positions 到 paper_trading_positions"""
    cursor = conn.cursor()

    # 1. 查询所有 spot_positions 数据
    cursor.execute("""
        SELECT
            symbol, entry_price, avg_entry_price, quantity, total_cost,
            take_profit_price, stop_loss_price, exit_price, pnl, pnl_pct,
            close_reason, status, created_at, updated_at, closed_at
        FROM spot_positions
        ORDER BY id
    """)

    spot_positions = cursor.fetchall()
    print(f"\n📊 找到 {len(spot_positions)} 条 spot_positions 记录")

    if not spot_positions:
        print("⚠️  spot_positions 表为空，无需迁移")
        cursor.close()
        return 0

    # 2. 迁移数据
    migrated_count = 0
    skipped_count = 0

    for pos in spot_positions:
        symbol = pos['symbol']
        status = 'open' if pos['status'] in ['active', 'open'] else 'closed'

        # 检查是否已存在相同记录
        cursor.execute("""
            SELECT id FROM paper_trading_positions
            WHERE account_id = %s AND symbol = %s AND created_at = %s
        """, (account_id, symbol, pos['created_at']))

        if cursor.fetchone():
            print(f"  ⏭️  跳过: {symbol} (已存在)")
            skipped_count += 1
            continue

        # 插入到 paper_trading_positions
        try:
            # 计算当前市值和未实现盈亏（如果是open状态）
            current_price = pos['entry_price'] if status == 'closed' else pos['avg_entry_price']
            quantity = float(pos['quantity'])
            total_cost = float(pos['total_cost'])
            market_value = float(quantity * current_price) if status == 'open' else None

            # 未实现盈亏
            if status == 'open' and market_value:
                unrealized_pnl = market_value - total_cost
                unrealized_pnl_pct = (unrealized_pnl / total_cost * 100) if total_cost > 0 else 0
            else:
                unrealized_pnl = pos['pnl'] or 0
                unrealized_pnl_pct = pos['pnl_pct'] or 0

            cursor.execute("""
                INSERT INTO paper_trading_positions (
                    account_id, symbol, position_side, quantity, available_quantity,
                    avg_entry_price, total_cost, current_price, market_value,
                    unrealized_pnl, unrealized_pnl_pct,
                    stop_loss_price, take_profit_price,
                    first_buy_time, last_update_time,
                    status, created_at, updated_at
                ) VALUES (
                    %s, %s, 'LONG', %s, %s,
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s,
                    %s, %s, %s
                )
            """, (
                account_id, symbol, pos['quantity'], pos['quantity'],
                pos['avg_entry_price'], pos['total_cost'], current_price, market_value,
                unrealized_pnl, unrealized_pnl_pct,
                pos['stop_loss_price'], pos['take_profit_price'],
                pos['created_at'], pos['updated_at'],
                status, pos['created_at'], pos['updated_at']
            ))

            print(f"  ✅ 迁移: {symbol} (status={status})")
            migrated_count += 1

        except Exception as e:
            print(f"  ❌ 错误: {symbol} - {e}")
            conn.rollback()
            continue

    conn.commit()
    cursor.close()

    print(f"\n迁移完成:")
    print(f"  ✅ 成功迁移: {migrated_count} 条")
    print(f"  ⏭️  跳过重复: {skipped_count} 条")

    return migrated_count


def create_trades_from_closed_positions(conn, account_id):
    """为已平仓的持仓创建对应的交易记录"""
    cursor = conn.cursor()

    # 查询已平仓的记录
    cursor.execute("""
        SELECT
            id, symbol, quantity, avg_entry_price, total_cost,
            current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
            created_at, updated_at
        FROM paper_trading_positions
        WHERE account_id = %s AND status = 'closed'
    """, (account_id,))

    closed_positions = cursor.fetchall()
    print(f"\n📝 为 {len(closed_positions)} 条已平仓记录创建交易记录...")

    created_count = 0
    for pos in closed_positions:
        symbol = pos['symbol']

        # 检查是否已有对应的卖出交易记录
        cursor.execute("""
            SELECT id FROM paper_trading_trades
            WHERE account_id = %s AND symbol = %s
            AND side = 'SELL' AND trade_time >= %s
        """, (account_id, symbol, pos['created_at']))

        if cursor.fetchone():
            print(f"  ⏭️  跳过: {symbol} (交易记录已存在)")
            continue

        try:
            # 生成唯一的订单ID和交易ID
            order_id = f"MIGRATE_ORDER_{pos['id']}_{int(pos['updated_at'].timestamp())}"
            trade_id = f"MIGRATE_TRADE_{pos['id']}_{int(pos['updated_at'].timestamp())}"

            # 卖出价格 = 成本价 + 盈亏/数量
            exit_price = float(pos['current_price']) if pos['current_price'] else float(pos['avg_entry_price'])

            # 创建卖出交易记录
            cursor.execute("""
                INSERT INTO paper_trading_trades (
                    account_id, order_id, trade_id, symbol, side,
                    price, quantity, total_amount, realized_pnl, pnl_pct,
                    cost_price, trade_time, created_at
                ) VALUES (
                    %s, %s, %s, %s, 'SELL',
                    %s, %s, %s, %s, %s,
                    %s, %s, %s
                )
            """, (
                account_id, order_id, trade_id, symbol,
                exit_price, pos['quantity'], float(pos['market_value'] or 0),
                float(pos['unrealized_pnl'] or 0), float(pos['unrealized_pnl_pct'] or 0),
                pos['avg_entry_price'], pos['updated_at'], pos['updated_at']
            ))

            print(f"  ✅ 创建交易记录: {symbol}")
            created_count += 1

        except Exception as e:
            print(f"  ❌ 错误: {symbol} - {e}")
            continue

    conn.commit()
    cursor.close()

    print(f"\n交易记录创建完成: {created_count} 条")
    return created_count


def backup_spot_table(conn):
    """备份 spot_positions 表"""
    cursor = conn.cursor()

    backup_table_name = f"spot_positions_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    try:
        cursor.execute(f"""
            CREATE TABLE {backup_table_name} LIKE spot_positions
        """)

        cursor.execute(f"""
            INSERT INTO {backup_table_name} SELECT * FROM spot_positions
        """)

        conn.commit()
        print(f"\n✅ 备份表创建成功: {backup_table_name}")
        cursor.close()
        return backup_table_name

    except Exception as e:
        print(f"\n❌ 备份失败: {e}")
        cursor.close()
        return None


def main():
    """主函数"""
    print("=" * 70)
    print("数据迁移：spot_positions → paper_trading_positions")
    print("=" * 70)

    conn = get_connection()

    try:
        # 1. 备份原表
        backup_table = backup_spot_table(conn)
        if not backup_table:
            print("\n⚠️  警告：备份失败，但继续执行迁移...")

        # 2. 确保默认账户存在
        account_id = ensure_default_account(conn)

        # 3. 迁移持仓数据
        migrated = migrate_spot_positions(conn, account_id)

        # 4. 为已平仓记录创建交易记录
        if migrated > 0:
            create_trades_from_closed_positions(conn, account_id)

        print("\n" + "=" * 70)
        print("✅ 迁移完成！")
        print("=" * 70)
        print(f"\n后续步骤:")
        print(f"1. 检查 paper_trading_positions 表数据是否正确")
        print(f"2. 更新代码中的表名引用（spot_positions → paper_trading_positions）")
        print(f"3. 测试完成后可以删除备份表: {backup_table}")
        print(f"4. 可选：删除或重命名原 spot_positions 表")

    except Exception as e:
        print(f"\n❌ 迁移失败: {e}")
        import traceback
        traceback.print_exc()

    finally:
        conn.close()


if __name__ == '__main__':
    main()
