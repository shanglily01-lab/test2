-- 区块链Gas消耗统计表
-- 用于存储六大主链每天的gas消耗量和价值

CREATE TABLE IF NOT EXISTS blockchain_gas_daily (
    id INT AUTO_INCREMENT PRIMARY KEY,
    
    -- 链信息
    chain_name VARCHAR(50) NOT NULL COMMENT '链名称: ethereum, bsc, polygon, arbitrum, optimism, avalanche',
    chain_display_name VARCHAR(50) NOT NULL COMMENT '链显示名称: Ethereum, BSC, Polygon, Arbitrum, Optimism, Avalanche',
    
    -- 日期
    date DATE NOT NULL COMMENT '统计日期',
    
    -- Gas消耗数据
    total_gas_used DECIMAL(30, 0) NOT NULL DEFAULT 0 COMMENT '总Gas消耗量',
    total_transactions BIGINT NOT NULL DEFAULT 0 COMMENT '总交易笔数',
    avg_gas_per_tx DECIMAL(20, 2) NOT NULL DEFAULT 0 COMMENT '平均每笔交易Gas消耗',
    
    -- Gas价格数据
    avg_gas_price DECIMAL(30, 0) NOT NULL DEFAULT 0 COMMENT '平均Gas价格(Wei/Gwei)',
    max_gas_price DECIMAL(30, 0) DEFAULT NULL COMMENT '最高Gas价格',
    min_gas_price DECIMAL(30, 0) DEFAULT NULL COMMENT '最低Gas价格',
    
    -- 价值数据
    native_token_price_usd DECIMAL(18, 8) DEFAULT NULL COMMENT '原生代币价格(USD)',
    total_gas_value_usd DECIMAL(24, 2) NOT NULL DEFAULT 0 COMMENT '总Gas价值(USD)',
    avg_gas_value_usd DECIMAL(18, 8) NOT NULL DEFAULT 0 COMMENT '平均每笔交易Gas价值(USD)',
    
    -- 区块数据
    total_blocks BIGINT NOT NULL DEFAULT 0 COMMENT '总区块数',
    avg_block_size DECIMAL(20, 2) DEFAULT NULL COMMENT '平均区块大小',
    
    -- 活跃度数据
    active_addresses BIGINT DEFAULT NULL COMMENT '活跃地址数',
    new_addresses BIGINT DEFAULT NULL COMMENT '新增地址数',
    
    -- 元数据
    data_source VARCHAR(100) DEFAULT NULL COMMENT '数据来源',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    
    -- 唯一索引：每个链每天只有一条记录
    UNIQUE KEY uk_chain_date (chain_name, date),
    -- 索引
    INDEX idx_date (date),
    INDEX idx_chain_name (chain_name),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='区块链Gas消耗日统计表';

-- 创建汇总视图（按日期汇总所有链的数据）
CREATE OR REPLACE VIEW blockchain_gas_daily_summary AS
SELECT 
    date,
    COUNT(DISTINCT chain_name) as chain_count,
    SUM(total_gas_used) as total_gas_used_all_chains,
    SUM(total_transactions) as total_transactions_all_chains,
    SUM(total_gas_value_usd) as total_gas_value_usd_all_chains,
    AVG(avg_gas_value_usd) as avg_gas_value_usd_per_tx,
    SUM(active_addresses) as total_active_addresses
FROM blockchain_gas_daily
GROUP BY date
ORDER BY date DESC;

