-- ============================================================
-- 创建模拟合约持仓表 (futures_positions)
-- 用于模拟盘的持仓记录
-- ============================================================

USE `binance-data`;

-- 模拟合约持仓表
CREATE TABLE IF NOT EXISTS futures_positions (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL DEFAULT 1 COMMENT '账户ID',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对(如 BTC/USDT)',

    -- 持仓方向和杠杆
    position_side VARCHAR(10) NOT NULL COMMENT '持仓方向: LONG/SHORT',
    direction VARCHAR(10) COMMENT '方向别名: long/short',
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
    stop_loss_pct DECIMAL(10, 4) COMMENT '止损百分比',
    take_profit_pct DECIMAL(10, 4) COMMENT '止盈百分比',

    -- 时间
    open_time DATETIME NOT NULL COMMENT '开仓时间',
    close_time DATETIME COMMENT '平仓时间',

    -- 状态
    status VARCHAR(20) DEFAULT 'open' COMMENT '状态: open/closed/liquidated',
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
    INDEX idx_symbol_strategy (symbol, strategy_id),
    INDEX idx_direction (direction)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟合约持仓表';

-- 验证表创建
SHOW TABLES LIKE 'futures_positions';

SELECT '模拟合约持仓表创建完成！' AS status;
