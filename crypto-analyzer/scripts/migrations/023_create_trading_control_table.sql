-- 交易控制表：用于控制U本位合约和币本位合约的交易开关
CREATE TABLE IF NOT EXISTS trading_control (
    id INT AUTO_INCREMENT PRIMARY KEY,
    account_id INT NOT NULL COMMENT '账户ID: 2=U本位合约, 3=币本位合约',
    trading_enabled BOOLEAN DEFAULT TRUE COMMENT '交易是否启用',
    trading_type VARCHAR(50) NOT NULL COMMENT '交易类型: usdt_futures/coin_futures',
    updated_by VARCHAR(100) COMMENT '更新人',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_account_type (account_id, trading_type),
    INDEX idx_account_id (account_id),
    INDEX idx_trading_type (trading_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易控制开关表';

-- 插入默认数据
INSERT INTO trading_control (account_id, trading_type, trading_enabled, updated_by)
VALUES
    (2, 'usdt_futures', TRUE, 'system'),
    (3, 'coin_futures', TRUE, 'system')
ON DUPLICATE KEY UPDATE
    trading_enabled = VALUES(trading_enabled),
    updated_at = CURRENT_TIMESTAMP;
