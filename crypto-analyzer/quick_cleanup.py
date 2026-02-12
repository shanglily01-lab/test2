#!/usr/bin/env python3
"""快速清理 - 无需确认直接删除"""
import pymysql
import yaml

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
    charset='utf8mb4'
)

cursor = conn.cursor()

# 未配置的交易对
symbols_to_remove = [
    'PEPE/USDT', 'BONK/USDT', 'SHIB/USDT',
    'FLOKI/USDT', 'ATM/USDT', 'LUNC/USDT', 'SPACE/USDT'
]

print("正在清理未配置的交易对...")

for symbol in symbols_to_remove:
    # 删除持仓
    cursor.execute(
        "DELETE FROM paper_trading_positions WHERE account_id = 1 AND symbol = %s",
        (symbol,)
    )
    pos_count = cursor.rowcount

    # 删除交易记录
    cursor.execute(
        "DELETE FROM paper_trading_trades WHERE account_id = 1 AND symbol = %s",
        (symbol,)
    )
    trade_count = cursor.rowcount

    # 删除待成交订单
    cursor.execute(
        "DELETE FROM paper_trading_pending_orders WHERE account_id = 1 AND symbol = %s",
        (symbol,)
    )
    pending_count = cursor.rowcount

    if pos_count > 0 or trade_count > 0 or pending_count > 0:
        print(f"✅ {symbol:15} - 持仓:{pos_count} 交易:{trade_count} 订单:{pending_count}")

conn.commit()
cursor.close()
conn.close()

print("\n✅ 清理完成！警告应该会停止。")
