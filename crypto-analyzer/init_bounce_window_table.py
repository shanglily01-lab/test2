#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化反弹窗口追踪表
用于记录45分钟反弹交易窗口期
"""
import pymysql
import os
import sys
from dotenv import load_dotenv

# Fix Windows encoding
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME'),
    'charset': 'utf8mb4'
}

conn = pymysql.connect(**db_config)
cursor = conn.cursor()

print('=' * 100)
print('创建反弹窗口追踪表: bounce_window')
print('=' * 100)

# 创建表
create_table_sql = """
CREATE TABLE IF NOT EXISTS bounce_window (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL COMMENT '账户ID',
    trading_type VARCHAR(50) NOT NULL COMMENT '交易类型: usdt_futures/coin_futures',
    symbol VARCHAR(20) NOT NULL COMMENT '币种: BTC/USDT',
    trigger_time DATETIME NOT NULL COMMENT '触发时间: 检测到1H长下影线的时间',
    window_start DATETIME NOT NULL COMMENT '窗口开始时间',
    window_end DATETIME NOT NULL COMMENT '窗口结束时间: 45分钟后',
    lower_shadow_pct DECIMAL(10,2) NOT NULL COMMENT '1H下影线百分比',
    trigger_price DECIMAL(20,8) NOT NULL COMMENT '触发价格: 1H K线收盘价',
    bounce_entered BOOLEAN DEFAULT FALSE COMMENT '是否已开仓',
    entry_time DATETIME NULL COMMENT '开仓时间',
    entry_price DECIMAL(20,8) NULL COMMENT '开仓价格',
    position_id INT NULL COMMENT '关联的持仓ID',
    notes TEXT NULL COMMENT '备注信息',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_window_end (window_end),
    INDEX idx_trading_type (trading_type),
    INDEX idx_trigger_time (trigger_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='反弹窗口追踪表';
"""

try:
    cursor.execute(create_table_sql)
    print('✅ 表创建成功: bounce_window')
    print()
    print('表结构说明:')
    print('  - trigger_time: 检测到1H长下影线的时间')
    print('  - window_start: 反弹窗口开始时间 (通常等于trigger_time)')
    print('  - window_end: 反弹窗口结束时间 (trigger_time + 45分钟)')
    print('  - lower_shadow_pct: 1H长下影线百分比 (>3%触发)')
    print('  - bounce_entered: 是否已开仓反弹交易')
    print('  - entry_time/entry_price: 实际开仓时间和价格')
    print('  - position_id: 关联的持仓记录ID')
    print()
    print('使用场景:')
    print('  1. big4_trend_detector检测到1H长下影线 -> 创建bounce_window记录')
    print('  2. smart_trader_service查询未过期的bounce_window')
    print('  3. 在窗口期内开LONG仓 (EMERGENCY_BOUNCE类型)')
    print('  4. 开仓后更新bounce_entered=TRUE, 记录entry信息')
    print()

    conn.commit()

    # 查看表结构
    cursor.execute("DESCRIBE bounce_window")
    columns = cursor.fetchall()

    print('\n表字段详情:')
    print(f"{'字段名':20} {'类型':20} {'空值':10} {'键':10} {'默认值':15}")
    print('-' * 80)
    for col in columns:
        print(f"{col[0]:20} {col[1]:20} {col[2]:10} {col[3]:10} {str(col[4])[:15]:15}")

except Exception as e:
    print(f'❌ 表创建失败: {e}')
    conn.rollback()

cursor.close()
conn.close()

print('\n' + '=' * 100)
print('初始化完成')
print('=' * 100)
