#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""创建现货交易表"""
import pymysql
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

conn = pymysql.connect(
    host='13.212.252.171',
    port=3306,
    user='admin',
    password='Tonny@1000',
    database='binance-data'
)

cursor = conn.cursor()

print("=" * 120)
print("创建现货交易表")
print("=" * 120)

# 1. 现货持仓表
print("\n1. 创建 spot_positions...")
cursor.execute("""
CREATE TABLE IF NOT EXISTS spot_positions (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL COMMENT '交易对 (如 BTC/USDT)',
    entry_price DECIMAL(20, 8) NOT NULL COMMENT '首次买入价格',
    avg_entry_price DECIMAL(20, 8) NOT NULL COMMENT '平均成本价',
    quantity DECIMAL(20, 8) NOT NULL COMMENT '持仓数量',
    total_cost DECIMAL(20, 4) NOT NULL COMMENT '总成本 (USDT)',
    current_batch INT NOT NULL DEFAULT 1 COMMENT '当前批次 (1-5)',
    take_profit_price DECIMAL(20, 8) COMMENT '止盈价格',
    stop_loss_price DECIMAL(20, 8) COMMENT '止损价格',
    exit_price DECIMAL(20, 8) COMMENT '平仓价格',
    pnl DECIMAL(20, 4) COMMENT '盈亏金额 (USDT)',
    pnl_pct DECIMAL(10, 6) COMMENT '盈亏百分比',
    close_reason VARCHAR(50) COMMENT '平仓原因 (止盈/止损/手动)',
    signal_strength DECIMAL(5, 2) COMMENT '开仓信号强度 (0-100)',
    signal_details TEXT COMMENT '信号详情',
    status VARCHAR(20) NOT NULL DEFAULT 'active' COMMENT '状态 (active/closed)',
    created_at DATETIME NOT NULL COMMENT '创建时间',
    updated_at DATETIME NOT NULL COMMENT '更新时间',
    closed_at DATETIME COMMENT '平仓时间',
    INDEX idx_symbol (symbol),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现货持仓表'
""")
conn.commit()
print("✅ spot_positions 创建成功")

# 2. 现货加仓记录表
print("\n2. 创建 spot_batch_history...")
cursor.execute("""
CREATE TABLE IF NOT EXISTS spot_batch_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    position_id INT NOT NULL COMMENT '关联的持仓ID',
    batch_number INT NOT NULL COMMENT '批次号 (1-5)',
    buy_price DECIMAL(20, 8) NOT NULL COMMENT '买入价格',
    quantity DECIMAL(20, 8) NOT NULL COMMENT '买入数量',
    cost DECIMAL(20, 4) NOT NULL COMMENT '本批成本 (USDT)',
    signal_strength DECIMAL(5, 2) COMMENT '加仓时信号强度',
    signal_details TEXT COMMENT '加仓信号详情',
    created_at DATETIME NOT NULL COMMENT '加仓时间',
    FOREIGN KEY (position_id) REFERENCES spot_positions(id) ON DELETE CASCADE,
    INDEX idx_position_id (position_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现货加仓记录表'
""")
conn.commit()
print("✅ spot_batch_history 创建成功")

# 3. 现货交易日志表
print("\n3. 创建 spot_trading_logs...")
cursor.execute("""
CREATE TABLE IF NOT EXISTS spot_trading_logs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    action VARCHAR(20) NOT NULL COMMENT '操作类型 (open/add_batch/close)',
    price DECIMAL(20, 8) NOT NULL COMMENT '成交价格',
    quantity DECIMAL(20, 8) NOT NULL COMMENT '数量',
    amount DECIMAL(20, 4) NOT NULL COMMENT '金额 (USDT)',
    position_id INT COMMENT '关联的持仓ID',
    batch_number INT COMMENT '批次号 (仅add_batch时有值)',
    details TEXT COMMENT '操作详情',
    created_at DATETIME NOT NULL COMMENT '创建时间',
    INDEX idx_symbol (symbol),
    INDEX idx_action (action),
    INDEX idx_position_id (position_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现货交易日志表'
""")
conn.commit()
print("✅ spot_trading_logs 创建成功")

# 4. 现货信号历史表
print("\n4. 创建 spot_signals_history...")
cursor.execute("""
CREATE TABLE IF NOT EXISTS spot_signals_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    signal_strength DECIMAL(5, 2) NOT NULL COMMENT '信号强度 (0-100)',
    best_strategy VARCHAR(50) COMMENT '最佳策略 (A/B/C/D)',
    score_a DECIMAL(5, 2) COMMENT '策略A得分 (趋势突破)',
    score_b DECIMAL(5, 2) COMMENT '策略B得分 (超卖反弹)',
    score_c DECIMAL(5, 2) COMMENT '策略C得分 (趋势跟随)',
    score_d DECIMAL(5, 2) COMMENT '策略D得分 (多重共振)',
    details TEXT COMMENT '信号详情',
    is_executed BOOLEAN DEFAULT FALSE COMMENT '是否已执行',
    position_id INT COMMENT '关联的持仓ID',
    created_at DATETIME NOT NULL COMMENT '创建时间',
    INDEX idx_symbol (symbol),
    INDEX idx_signal_strength (signal_strength),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现货信号历史表'
""")
conn.commit()
print("✅ spot_signals_history 创建成功")

# 5. 现货资金使用统计表
print("\n5. 创建 spot_capital_usage...")
cursor.execute("""
CREATE TABLE IF NOT EXISTS spot_capital_usage (
    id INT AUTO_INCREMENT PRIMARY KEY,
    total_capital DECIMAL(20, 4) NOT NULL COMMENT '总资金',
    available_capital DECIMAL(20, 4) NOT NULL COMMENT '可用资金',
    locked_capital DECIMAL(20, 4) NOT NULL COMMENT '锁定资金 (持仓中)',
    active_positions INT NOT NULL DEFAULT 0 COMMENT '活跃持仓数',
    total_pnl DECIMAL(20, 4) COMMENT '总盈亏',
    realized_pnl DECIMAL(20, 4) COMMENT '已实现盈亏',
    unrealized_pnl DECIMAL(20, 4) COMMENT '未实现盈亏',
    snapshot_time DATETIME NOT NULL COMMENT '快照时间',
    INDEX idx_snapshot_time (snapshot_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='现货资金使用统计表'
""")
conn.commit()
print("✅ spot_capital_usage 创建成功")

# 6. 初始化资金记录
print("\n6. 初始化资金记录...")
cursor.execute("""
INSERT INTO spot_capital_usage (
    total_capital, available_capital, locked_capital, active_positions,
    total_pnl, realized_pnl, unrealized_pnl, snapshot_time
) VALUES (50000.00, 50000.00, 0.00, 0, 0.00, 0.00, 0.00, NOW())
""")
conn.commit()
print("✅ 初始资金记录创建成功")

print("\n" + "=" * 120)
print("验证表创建")
print("=" * 120)

cursor.execute("SHOW TABLES LIKE 'spot_%'")
tables = cursor.fetchall()

print(f"\n现货交易表: {len(tables)} 个\n")
for table in tables:
    table_name = table[0]
    cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
    count = cursor.fetchone()[0]
    print(f"  ✅ {table_name:30s} - {count} 条记录")

cursor.close()
conn.close()

print("\n✅ 现货交易系统数据库初始化完成!")
print("\n下一步: 启动现货交易服务")
print("  python spot_trader_service.py")
