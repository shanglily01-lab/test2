-- ============================================================
-- 模拟合约交易系统数据库表结构
-- 支持多空双向交易、杠杆、止盈止损
-- ============================================================

-- 1. 合约账户表（扩展paper_trading_accounts）
-- 使用现有的paper_trading_accounts表，account_type设为'futures'

-- 2. 合约持仓表
CREATE TABLE IF NOT EXISTS futures_positions (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对(如 BTC/USDT)',

    -- 持仓方向和杠杆
    position_side VARCHAR(10) NOT NULL COMMENT '持仓方向: LONG(多头), SHORT(空头)',
    leverage INT NOT NULL DEFAULT 1 COMMENT '杠杆倍数(1-125)',

    -- 仓位信息
    quantity DECIMAL(18, 8) NOT NULL COMMENT '持仓数量（币）',
    notional_value DECIMAL(20, 2) NOT NULL COMMENT '名义价值（quantity * entry_price）',
    margin DECIMAL(20, 2) NOT NULL COMMENT '占用保证金（notional_value / leverage）',

    -- 价格信息
    entry_price DECIMAL(18, 8) NOT NULL COMMENT '开仓均价',
    mark_price DECIMAL(18, 8) COMMENT '标记价格（用于计算未实现盈亏）',
    liquidation_price DECIMAL(18, 8) COMMENT '强平价格',

    -- 盈亏信息
    unrealized_pnl DECIMAL(20, 2) DEFAULT 0.00 COMMENT '未实现盈亏',
    unrealized_pnl_pct DECIMAL(10, 4) DEFAULT 0.00 COMMENT '未实现收益率(%)',
    realized_pnl DECIMAL(20, 2) DEFAULT 0.00 COMMENT '已实现盈亏',

    -- 止盈止损
    stop_loss_price DECIMAL(18, 8) COMMENT '止损价格',
    take_profit_price DECIMAL(18, 8) COMMENT '止盈价格',
    stop_loss_pct DECIMAL(5, 2) COMMENT '止损百分比(%)',
    take_profit_pct DECIMAL(5, 2) COMMENT '止盈百分比(%)',

    -- 资金费率累计
    total_funding_fee DECIMAL(20, 8) DEFAULT 0.00 COMMENT '累计资金费率',

    -- 统计信息
    open_time DATETIME NOT NULL COMMENT '开仓时间',
    last_update_time DATETIME COMMENT '最后更新时间',
    close_time DATETIME COMMENT '平仓时间',
    holding_hours INT DEFAULT 0 COMMENT '持仓小时数',

    -- 状态
    status VARCHAR(20) DEFAULT 'open' COMMENT '状态: open(持仓中), closed(已平仓), liquidated(已强平)',

    -- 来源
    source VARCHAR(50) DEFAULT 'manual' COMMENT '来源: manual(手动), signal(信号), auto(自动)',
    signal_id INT COMMENT '关联的信号ID',

    -- 备注
    notes TEXT COMMENT '备注',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_status (status),
    INDEX idx_position_side (position_side),
    INDEX idx_open_time (open_time),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟合约持仓表';

-- 3. 合约订单表
CREATE TABLE IF NOT EXISTS futures_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    order_id VARCHAR(50) UNIQUE NOT NULL COMMENT '订单ID',
    position_id INT COMMENT '关联的持仓ID',

    -- 交易对
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',

    -- 订单类型
    side VARCHAR(10) NOT NULL COMMENT '订单方向: OPEN_LONG(开多), OPEN_SHORT(开空), CLOSE_LONG(平多), CLOSE_SHORT(平空)',
    order_type VARCHAR(20) NOT NULL DEFAULT 'MARKET' COMMENT '订单类型: MARKET(市价), LIMIT(限价), STOP_MARKET(止损市价), TAKE_PROFIT_MARKET(止盈市价)',

    -- 杠杆
    leverage INT NOT NULL DEFAULT 1 COMMENT '杠杆倍数',

    -- 价格和数量
    price DECIMAL(18, 8) COMMENT '委托价格(限价单)',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '委托数量',
    executed_quantity DECIMAL(18, 8) DEFAULT 0 COMMENT '已成交数量',

    -- 保证金和金额
    margin DECIMAL(20, 2) COMMENT '占用保证金',
    total_value DECIMAL(20, 2) COMMENT '名义价值',
    executed_value DECIMAL(20, 2) DEFAULT 0 COMMENT '已成交价值',

    -- 手续费
    fee DECIMAL(20, 8) DEFAULT 0 COMMENT '手续费',
    fee_rate DECIMAL(10, 6) DEFAULT 0.0004 COMMENT '手续费率(默认0.04%)',

    -- 订单状态
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT '订单状态: PENDING, FILLED, PARTIALLY_FILLED, CANCELLED, REJECTED',

    -- 成交信息
    avg_fill_price DECIMAL(18, 8) COMMENT '平均成交价格',
    fill_time DATETIME COMMENT '成交时间',

    -- 止盈止损
    stop_price DECIMAL(18, 8) COMMENT '触发价格',
    stop_loss_price DECIMAL(18, 8) COMMENT '止损价格',
    take_profit_price DECIMAL(18, 8) COMMENT '止盈价格',

    -- 订单来源
    order_source VARCHAR(50) DEFAULT 'manual' COMMENT '订单来源: manual, signal, auto, stop_loss, take_profit',
    signal_id INT COMMENT '关联的信号ID',

    -- 盈亏(平仓时)
    realized_pnl DECIMAL(20, 2) COMMENT '已实现盈亏',
    pnl_pct DECIMAL(10, 4) COMMENT '收益率(%)',

    -- 备注
    notes TEXT COMMENT '订单备注',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_order_id (order_id),
    INDEX idx_status (status),
    INDEX idx_position_id (position_id),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES futures_positions(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟合约订单表';

-- 4. 合约交易历史表
CREATE TABLE IF NOT EXISTS futures_trades (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    order_id VARCHAR(50) NOT NULL COMMENT '订单ID',
    position_id INT COMMENT '持仓ID',
    trade_id VARCHAR(50) UNIQUE NOT NULL COMMENT '成交ID',

    -- 交易信息
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    side VARCHAR(10) NOT NULL COMMENT 'OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT',

    -- 价格和数量
    price DECIMAL(18, 8) NOT NULL COMMENT '成交价格',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '成交数量',
    notional_value DECIMAL(20, 2) NOT NULL COMMENT '名义价值',

    -- 杠杆和保证金
    leverage INT NOT NULL COMMENT '杠杆倍数',
    margin DECIMAL(20, 2) NOT NULL COMMENT '占用保证金',

    -- 手续费
    fee DECIMAL(20, 8) DEFAULT 0 COMMENT '手续费',
    fee_rate DECIMAL(10, 6) COMMENT '手续费率',

    -- 盈亏(平仓时)
    realized_pnl DECIMAL(20, 2) COMMENT '已实现盈亏',
    pnl_pct DECIMAL(10, 4) COMMENT '收益率(%)',
    roi DECIMAL(10, 4) COMMENT '投资回报率(基于保证金%)',

    -- 成本价(平仓时参考)
    entry_price DECIMAL(18, 8) COMMENT '开仓价',

    -- 时间
    trade_time DATETIME NOT NULL COMMENT '成交时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_order_id (order_id),
    INDEX idx_position_id (position_id),
    INDEX idx_trade_time (trade_time),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES futures_positions(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟合约交易历史表';

-- 5. 强平记录表
CREATE TABLE IF NOT EXISTS futures_liquidations (
    id INT AUTO_INCREMENT PRIMARY KEY,

    account_id INT NOT NULL COMMENT '账户ID',
    position_id INT NOT NULL COMMENT '持仓ID',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',

    -- 强平信息
    position_side VARCHAR(10) NOT NULL COMMENT '仓位方向',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '强平数量',
    entry_price DECIMAL(18, 8) NOT NULL COMMENT '开仓价',
    liquidation_price DECIMAL(18, 8) NOT NULL COMMENT '强平价',
    mark_price DECIMAL(18, 8) NOT NULL COMMENT '触发时标记价格',

    -- 损失
    loss_amount DECIMAL(20, 2) NOT NULL COMMENT '损失金额',
    margin_lost DECIMAL(20, 2) NOT NULL COMMENT '保证金损失',

    -- 杠杆
    leverage INT NOT NULL COMMENT '杠杆倍数',

    -- 时间
    liquidation_time DATETIME NOT NULL COMMENT '强平时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_liquidation_time (liquidation_time),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES futures_positions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约强平记录表';

-- 6. 资金费率记录表
CREATE TABLE IF NOT EXISTS futures_funding_fees (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    account_id INT NOT NULL COMMENT '账户ID',
    position_id INT NOT NULL COMMENT '持仓ID',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',

    -- 资金费率
    funding_rate DECIMAL(18, 8) NOT NULL COMMENT '资金费率',
    position_value DECIMAL(20, 2) NOT NULL COMMENT '仓位价值',
    funding_fee DECIMAL(20, 8) NOT NULL COMMENT '资金费用（正数=支出，负数=收入）',

    -- 时间
    funding_time DATETIME NOT NULL COMMENT '结算时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_position (account_id, position_id),
    INDEX idx_funding_time (funding_time),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE,
    FOREIGN KEY (position_id) REFERENCES futures_positions(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约资金费率记录表';

-- 7. 初始化默认合约账户
INSERT INTO paper_trading_accounts (
    account_name,
    account_type,
    initial_balance,
    current_balance,
    total_equity,
    stop_loss_pct,
    take_profit_pct,
    status,
    is_default
) VALUES (
    '默认合约账户',
    'futures',
    10000.00,
    10000.00,
    10000.00,
    5.00,   -- 默认止损5%
    15.00,  -- 默认止盈15%
    'active',
    FALSE
) ON DUPLICATE KEY UPDATE account_name=account_name;
