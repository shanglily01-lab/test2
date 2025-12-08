-- ============================================================
-- 用户API密钥表
-- 存储每个用户的交易所API密钥（加密存储）
-- ============================================================

USE `binance-data`;

-- 1. 创建用户API密钥表
CREATE TABLE IF NOT EXISTS user_api_keys (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 关联用户
    user_id INT NOT NULL COMMENT '用户ID',

    -- 交易所信息
    exchange VARCHAR(50) NOT NULL DEFAULT 'binance' COMMENT '交易所: binance/gate/okx等',
    account_name VARCHAR(100) NOT NULL COMMENT '账户名称（用户自定义）',

    -- API密钥（加密存储）
    api_key VARCHAR(500) NOT NULL COMMENT 'API Key（加密）',
    api_secret VARCHAR(500) NOT NULL COMMENT 'API Secret（加密）',

    -- 权限和状态
    permissions VARCHAR(255) DEFAULT 'spot,futures' COMMENT '权限: spot,futures,margin等',
    is_testnet TINYINT(1) DEFAULT 0 COMMENT '是否测试网',
    status VARCHAR(20) DEFAULT 'active' COMMENT '状态: active/inactive/revoked',

    -- 风控设置
    max_position_value DECIMAL(20,2) DEFAULT 1000.00 COMMENT '单笔最大持仓价值(USDT)',
    max_daily_loss DECIMAL(20,2) DEFAULT 100.00 COMMENT '每日最大亏损(USDT)',
    max_leverage INT DEFAULT 10 COMMENT '最大杠杆',

    -- 时间戳
    last_used_at DATETIME COMMENT '最后使用时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- 索引
    UNIQUE KEY uk_user_exchange_account (user_id, exchange, account_name),
    INDEX idx_user_id (user_id),
    INDEX idx_exchange (exchange),
    INDEX idx_status (status),

    -- 外键
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户API密钥表';

-- 2. 更新 live_trading_accounts 表，添加 api_key_id 字段
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'live_trading_accounts'
    AND COLUMN_NAME = 'api_key_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `live_trading_accounts` ADD COLUMN `api_key_id` INT COMMENT ''关联用户API密钥ID'' AFTER `user_id`',
    'SELECT ''Column api_key_id already exists in live_trading_accounts'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 3. 为 futures_positions 添加 user_id 字段（通过 account_id 关联）
-- 注意：futures_positions 通过 account_id 关联到 paper_trading_accounts，
-- 而 paper_trading_accounts 已有 user_id，所以不需要直接在 futures_positions 加 user_id

-- 4. 为 live_futures_positions 添加 user_id 字段（方便直接查询）
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'live_futures_positions'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `live_futures_positions` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `account_id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Column user_id already exists in live_futures_positions'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 5. 为 live_futures_orders 添加 user_id 字段
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'live_futures_orders'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `live_futures_orders` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `account_id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Column user_id already exists in live_futures_orders'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 6. 为 futures_positions 添加 user_id 字段（方便直接查询）
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'futures_positions'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `futures_positions` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `account_id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Column user_id already exists in futures_positions'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 7. 为 futures_orders 添加 user_id 字段
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'futures_orders'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `futures_orders` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `account_id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Column user_id already exists in futures_orders'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 验证表是否创建成功
SELECT 'user_api_keys' as table_name, COUNT(*) as count FROM user_api_keys
UNION ALL
SELECT 'Column user_id in futures_positions',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA='binance-data' AND TABLE_NAME='futures_positions' AND COLUMN_NAME='user_id')
UNION ALL
SELECT 'Column user_id in live_futures_positions',
    (SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
     WHERE TABLE_SCHEMA='binance-data' AND TABLE_NAME='live_futures_positions' AND COLUMN_NAME='user_id');

SELECT '用户API密钥表创建完成！' AS status;
