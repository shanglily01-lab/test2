#!/usr/bin/env python3
"""
清理未配置在config.yaml中的交易对
这些交易对导致持续的价格获取失败警告
"""
import pymysql
import yaml
import sys

def main():
    # 读取配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    db_config = config['database']

    # 连接数据库
    conn = pymysql.connect(
        host=db_config['host'],
        user=db_config['user'],
        password=db_config['password'],
        database=db_config['database'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor
    )

    cursor = conn.cursor()

    # 未配置的交易对
    unconfigured_symbols = [
        'PEPE/USDT', 'BONK/USDT', 'SHIB/USDT',
        'FLOKI/USDT', 'ATM/USDT', 'LUNC/USDT',
        'SPACE/USDT'  # 之前也有问题
    ]

    print("=" * 80)
    print("清理未配置的交易对")
    print("=" * 80)

    # 1. 查询这些交易对的数据
    placeholders = ','.join(['%s'] * len(unconfigured_symbols))

    cursor.execute(f'''
        SELECT symbol, status, COUNT(*) as count
        FROM paper_trading_positions
        WHERE account_id = 1 AND symbol IN ({placeholders})
        GROUP BY symbol, status
    ''', unconfigured_symbols)

    positions = cursor.fetchall()

    if not positions:
        print("\n✅ 没有找到需要清理的持仓数据")
        cursor.close()
        conn.close()
        return

    print("\n找到以下需要清理的持仓:")
    for pos in positions:
        print(f"  - {pos['symbol']:15} {pos['status']:10} {pos['count']} 条")

    # 确认
    print("\n⚠️  将要删除这些交易对的所有数据（持仓、交易记录、待成交订单）")
    response = input("确认删除? (yes/no): ")

    if response.lower() != 'yes':
        print("❌ 取消操作")
        cursor.close()
        conn.close()
        return

    print("\n开始清理...")

    # 2. 删除持仓
    cursor.execute(f'''
        DELETE FROM paper_trading_positions
        WHERE account_id = 1 AND symbol IN ({placeholders})
    ''', unconfigured_symbols)
    pos_deleted = cursor.rowcount
    print(f"  ✅ 删除持仓: {pos_deleted} 条")

    # 3. 删除交易记录
    cursor.execute(f'''
        DELETE FROM paper_trading_trades
        WHERE account_id = 1 AND symbol IN ({placeholders})
    ''', unconfigured_symbols)
    trades_deleted = cursor.rowcount
    print(f"  ✅ 删除交易记录: {trades_deleted} 条")

    # 4. 删除待成交订单
    cursor.execute(f'''
        DELETE FROM paper_trading_pending_orders
        WHERE account_id = 1 AND symbol IN ({placeholders})
    ''', unconfigured_symbols)
    pending_deleted = cursor.rowcount
    print(f"  ✅ 删除待成交订单: {pending_deleted} 条")

    # 提交
    conn.commit()

    print("\n" + "=" * 80)
    print("清理完成！")
    print("=" * 80)
    print(f"总共删除:")
    print(f"  - 持仓: {pos_deleted} 条")
    print(f"  - 交易记录: {trades_deleted} 条")
    print(f"  - 待成交订单: {pending_deleted} 条")

    # 5. 验证
    cursor.execute(f'''
        SELECT COUNT(*) as count
        FROM paper_trading_positions
        WHERE account_id = 1 AND symbol IN ({placeholders})
    ''', unconfigured_symbols)
    remaining = cursor.fetchone()['count']

    if remaining == 0:
        print(f"\n✅ 验证成功: 所有未配置的交易对已清理")
    else:
        print(f"\n⚠️  警告: 仍有 {remaining} 条记录残留")

    cursor.close()
    conn.close()

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
