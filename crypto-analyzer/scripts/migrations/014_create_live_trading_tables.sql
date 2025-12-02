-- ============================================================
-- 实盘合约交易系统数据库表结构
-- 用于记录币安实盘合约交易
-- ============================================================

USE `binance-data`;

-- 1. 实盘交易账户表
CREATE TABLE IF NOT EXISTS live_trading_accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 账户信息
    account_name VARCHAR(100) NOT NULL COMMENT '账户名称',
    exchange VARCHAR(50) NOT NULL DEFAULT 'binance' COMMENT '交易所',
    account_type VARCHAR(20) NOT NULL DEFAULT 'futures' COMMENT '账户类型: spot/futures',

    -- API配置（加密存储或引用config.yaml）
    api_key_ref VARCHAR(100) COMMENT 'API Key引用（不存储实际key）',

    -- 风控设置
    max_position_value DECIMAL(20, 2) DEFAULT 1000.00 COMMENT '单笔最大持仓价值(USDT)',
    max_daily_loss DECIMAL(20, 2) DEFAULT 100.00 COMMENT '日最大亏损(USDT)',
    max_total_positions INT DEFAULT 5 COMMENT '最大同时持仓数',
    max_leverage INT DEFAULT 10 COMMENT '最大允许杠杆',

    -- 统计信息（从币安同步）
    total_balance DECIMAL(20, 8) DEFAULT 0 COMMENT '总余额',
    available_balance DECIMAL(20, 8) DEFAULT 0 COMMENT '可用余额',
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0 COMMENT '未实现盈亏',

    -- 本地统计
    total_trades INT DEFAULT 0 COMMENT '总交易次数',
    winning_trades INT DEFAULT 0 COMMENT '盈利次数',
    losing_trades INT DEFAULT 0 COMMENT '亏损次数',
    total_realized_pnl DECIMAL(20, 8) DEFAULT 0 COMMENT '累计已实现盈亏',

    -- 状态
    status VARCHAR(20) DEFAULT 'active' COMMENT '状态: active/paused/disabled',
    is_default BOOLEAN DEFAULT FALSE COMMENT '是否默认账户',

    -- 时间
    last_sync_time DATETIME COMMENT '最后同步时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_exchange (exchange),
    INDEX idx_status (status),
    UNIQUE KEY uk_account_name (account_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘交易账户表';

-- 2. 实盘持仓表
CREATE TABLE IF NOT EXISTS live_futures_positions (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对(如 BTC/USDT)',

    -- 持仓方向和杠杆
    position_side VARCHAR(10) NOT NULL COMMENT '持仓方向: LONG/SHORT',
    leverage INT NOT NULL DEFAULT 1 COMMENT '杠杆倍数',

    -- 仓位信息
    quantity DECIMAL(18, 8) NOT NULL COMMENT '持仓数量',
    notional_value DECIMAL(20, 2) COMMENT '名义价值',
    margin DECIMAL(20, 2) COMMENT '占用保证金',

    -- 价格信息
    entry_price DECIMAL(18, 8) NOT NULL COMMENT '开仓均价',
    mark_price DECIMAL(18, 8) COMMENT '标记价格',
    liquidation_price DECIMAL(18, 8) COMMENT '强平价格',
    close_price DECIMAL(18, 8) COMMENT '平仓价格',

    -- 盈亏
    unrealized_pnl DECIMAL(20, 8) DEFAULT 0 COMMENT '未实现盈亏',
    realized_pnl DECIMAL(20, 8) DEFAULT 0 COMMENT '已实现盈亏',

    -- 止盈止损
    stop_loss_price DECIMAL(18, 8) COMMENT '止损价格',
    take_profit_price DECIMAL(18, 8) COMMENT '止盈价格',

    -- 币安订单信息
    binance_order_id VARCHAR(50) COMMENT '币安开仓订单ID',
    sl_order_id VARCHAR(50) COMMENT '止损订单ID',
    tp_order_id VARCHAR(50) COMMENT '止盈订单ID',

    -- 时间
    open_time DATETIME NOT NULL COMMENT '开仓时间',
    close_time DATETIME COMMENT '平仓时间',

    -- 状态
    status VARCHAR(20) DEFAULT 'OPEN' COMMENT '状态: OPEN/CLOSED/LIQUIDATED',
    close_reason VARCHAR(50) COMMENT '平仓原因',

    -- 来源
    source VARCHAR(50) DEFAULT 'manual' COMMENT '来源: manual/signal/strategy',
    signal_id INT COMMENT '信号ID',
    strategy_id BIGINT COMMENT '策略ID',

    -- 备注
    notes TEXT COMMENT '备注',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_status (status),
    INDEX idx_open_time (open_time),
    INDEX idx_strategy_id (strategy_id),
    FOREIGN KEY (account_id) REFERENCES live_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘合约持仓表';

-- 3. 实盘订单表
CREATE TABLE IF NOT EXISTS live_futures_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    position_id INT COMMENT '关联持仓ID',

    -- 币安订单信息
    binance_order_id VARCHAR(50) NOT NULL COMMENT '币安订单ID',
    client_order_id VARCHAR(50) COMMENT '客户端订单ID',

    -- 交易对
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',

    -- 订单信息
    side VARCHAR(10) NOT NULL COMMENT 'BUY/SELL',
    position_side VARCHAR(10) COMMENT 'LONG/SHORT',
    order_type VARCHAR(20) NOT NULL COMMENT 'MARKET/LIMIT/STOP_MARKET/TAKE_PROFIT_MARKET',

    -- 价格和数量
    price DECIMAL(18, 8) COMMENT '委托价格',
    stop_price DECIMAL(18, 8) COMMENT '触发价格',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '委托数量',
    executed_quantity DECIMAL(18, 8) DEFAULT 0 COMMENT '已成交数量',
    avg_fill_price DECIMAL(18, 8) COMMENT '平均成交价',

    -- 手续费
    commission DECIMAL(20, 8) DEFAULT 0 COMMENT '手续费',
    commission_asset VARCHAR(10) DEFAULT 'USDT' COMMENT '手续费资产',

    -- 状态
    status VARCHAR(20) NOT NULL COMMENT 'NEW/FILLED/PARTIALLY_FILLED/CANCELED/REJECTED/EXPIRED',

    -- 盈亏（平仓订单）
    realized_pnl DECIMAL(20, 8) COMMENT '已实现盈亏',

    -- 来源
    source VARCHAR(50) DEFAULT 'manual' COMMENT '订单来源',
    strategy_id BIGINT COMMENT '策略ID',

    -- 时间
    order_time DATETIME COMMENT '下单时间',
    fill_time DATETIME COMMENT '成交时间',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_binance_order_id (binance_order_id),
    INDEX idx_status (status),
    INDEX idx_order_time (order_time),
    FOREIGN KEY (account_id) REFERENCES live_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘合约订单表';

-- 4. 实盘交易历史表
CREATE TABLE IF NOT EXISTS live_futures_trades (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    position_id INT COMMENT '持仓ID',
    order_id BIGINT COMMENT '订单ID',

    -- 币安交易信息
    binance_trade_id VARCHAR(50) COMMENT '币安成交ID',
    binance_order_id VARCHAR(50) COMMENT '币安订单ID',

    -- 交易信息
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    side VARCHAR(10) NOT NULL COMMENT 'BUY/SELL',
    position_side VARCHAR(10) COMMENT 'LONG/SHORT',

    -- 价格和数量
    price DECIMAL(18, 8) NOT NULL COMMENT '成交价格',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '成交数量',
    quote_quantity DECIMAL(20, 8) COMMENT '成交金额',

    -- 手续费
    commission DECIMAL(20, 8) DEFAULT 0 COMMENT '手续费',
    commission_asset VARCHAR(10) DEFAULT 'USDT' COMMENT '手续费资产',

    -- 盈亏
    realized_pnl DECIMAL(20, 8) COMMENT '已实现盈亏',

    -- 时间
    trade_time DATETIME NOT NULL COMMENT '成交时间',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_trade_time (trade_time),
    INDEX idx_binance_trade_id (binance_trade_id),
    FOREIGN KEY (account_id) REFERENCES live_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘交易历史表';

-- 5. 实盘日志表（用于审计和调试）
CREATE TABLE IF NOT EXISTS live_trading_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    account_id INT COMMENT '账户ID',

    -- 日志信息
    log_level VARCHAR(10) NOT NULL DEFAULT 'INFO' COMMENT '日志级别: DEBUG/INFO/WARNING/ERROR',
    action VARCHAR(50) NOT NULL COMMENT '操作类型: OPEN/CLOSE/CANCEL/SYNC等',
    symbol VARCHAR(20) COMMENT '交易对',

    -- 详情
    message TEXT NOT NULL COMMENT '日志消息',
    request_data JSON COMMENT '请求数据',
    response_data JSON COMMENT '响应数据',

    -- 时间
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_id (account_id),
    INDEX idx_log_level (log_level),
    INDEX idx_action (action),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实盘交易日志表';

-- 6. 创建默认实盘账户
INSERT INTO live_trading_accounts (
    account_name,
    exchange,
    account_type,
    api_key_ref,
    max_position_value,
    max_daily_loss,
    max_total_positions,
    max_leverage,
    status,
    is_default
) VALUES (
    '币安合约主账户',
    'binance',
    'futures',
    'config.yaml:exchanges.binance',
    1000.00,  -- 单笔最大1000U
    100.00,   -- 日最大亏损100U
    5,        -- 最多5个持仓
    10,       -- 最大10倍杠杆
    'active',
    TRUE
) ON DUPLICATE KEY UPDATE account_name=account_name;

-- 7. 为策略表添加market_type字段（区分模拟/实盘）
-- 检查字段是否存在
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'trading_strategies'
    AND COLUMN_NAME = 'market_type'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `trading_strategies` ADD COLUMN `market_type` VARCHAR(10) DEFAULT ''test'' COMMENT ''市场类型: test(模拟)/live(实盘)'' AFTER `enabled`',
    'SELECT ''Column market_type already exists'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 验证表是否创建成功
SHOW TABLES LIKE 'live%';

SELECT '实盘交易表创建完成！' AS status;
