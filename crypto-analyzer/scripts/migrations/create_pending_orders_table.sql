-- 创建待成交订单表（如果不存在）
-- 执行方法: mysql -u root -p binance-data < create_pending_orders_table.sql

CREATE TABLE IF NOT EXISTS paper_trading_pending_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    order_id VARCHAR(50) UNIQUE NOT NULL COMMENT '订单ID',

    -- 交易对
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',

    -- 订单类型
    side VARCHAR(10) NOT NULL COMMENT '订单方向: BUY(买入), SELL(卖出)',

    -- 价格和数量
    quantity DECIMAL(18, 8) NOT NULL COMMENT '委托数量',
    trigger_price DECIMAL(18, 8) NOT NULL COMMENT '触发价格',

    -- 冻结信息
    frozen_amount DECIMAL(20, 2) DEFAULT 0.00 COMMENT '冻结资金(USDT)',
    frozen_quantity DECIMAL(18, 8) DEFAULT 0.00 COMMENT '冻结数量',

    -- 订单状态
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT '订单状态: PENDING(待触发), EXECUTED(已执行), CANCELLED(已撤销)',
    executed BOOLEAN DEFAULT FALSE COMMENT '是否已执行',

    -- 执行信息
    executed_at DATETIME COMMENT '执行时间',
    executed_order_id VARCHAR(50) COMMENT '执行后的订单ID',

    -- 订单来源
    order_source VARCHAR(50) DEFAULT 'auto' COMMENT '订单来源: manual(手动), signal(信号), auto(自动交易)',

    -- 时间戳
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_order_id (order_id),
    INDEX idx_status (status),
    INDEX idx_executed (executed),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待成交订单表';

