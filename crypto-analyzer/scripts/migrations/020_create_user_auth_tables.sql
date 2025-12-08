-- ============================================================
-- 用户认证系统数据库表结构
-- 用于用户注册、登录、JWT认证
-- ============================================================

USE `binance-data`;

-- 1. 用户表
CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 基本信息
    username VARCHAR(50) NOT NULL COMMENT '用户名',
    email VARCHAR(100) NOT NULL COMMENT '邮箱',
    password_hash VARCHAR(255) NOT NULL COMMENT '密码哈希',

    -- 角色和状态
    role VARCHAR(20) DEFAULT 'user' COMMENT '角色: admin/user/viewer',
    status VARCHAR(20) DEFAULT 'active' COMMENT '状态: active/inactive/banned',

    -- 时间戳
    last_login DATETIME COMMENT '最后登录时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- 索引
    UNIQUE KEY uk_username (username),
    UNIQUE KEY uk_email (email),
    INDEX idx_status (status),
    INDEX idx_role (role)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户表';

-- 2. 刷新令牌表
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    user_id INT NOT NULL COMMENT '用户ID',

    -- 令牌信息
    token_hash VARCHAR(255) NOT NULL COMMENT '令牌哈希',
    device_info VARCHAR(255) COMMENT '设备信息',
    ip_address VARCHAR(45) COMMENT 'IP地址',

    -- 时间
    expires_at DATETIME NOT NULL COMMENT '过期时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at DATETIME COMMENT '撤销时间',

    -- 索引和外键
    UNIQUE KEY uk_token_hash (token_hash),
    INDEX idx_user_id (user_id),
    INDEX idx_expires_at (expires_at),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='刷新令牌表';

-- 3. 登录日志表 (可选，用于安全审计)
CREATE TABLE IF NOT EXISTS login_logs (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    user_id INT COMMENT '用户ID (登录失败可能为NULL)',
    username VARCHAR(50) COMMENT '尝试的用户名',

    -- 登录信息
    success BOOLEAN NOT NULL DEFAULT FALSE COMMENT '是否成功',
    ip_address VARCHAR(45) COMMENT 'IP地址',
    user_agent TEXT COMMENT '浏览器UA',
    failure_reason VARCHAR(100) COMMENT '失败原因',

    -- 时间
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- 索引
    INDEX idx_user_id (user_id),
    INDEX idx_ip_address (ip_address),
    INDEX idx_created_at (created_at),
    INDEX idx_success (success)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='登录日志表';

-- 4. 为现有表添加 user_id 字段
-- 4.1 trading_strategies 表
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'trading_strategies'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `trading_strategies` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Column user_id already exists in trading_strategies'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 4.2 live_trading_accounts 表
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'live_trading_accounts'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `live_trading_accounts` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Column user_id already exists in live_trading_accounts'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 4.3 paper_trading_accounts 表 (如果存在)
SET @table_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'paper_trading_accounts'
);

SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'paper_trading_accounts'
    AND COLUMN_NAME = 'user_id'
);

SET @sql_add_column = IF(@table_exists > 0 AND @column_exists = 0,
    'ALTER TABLE `paper_trading_accounts` ADD COLUMN `user_id` INT DEFAULT 1 COMMENT ''用户ID'' AFTER `id`, ADD INDEX idx_user_id (user_id)',
    'SELECT ''Skipping paper_trading_accounts'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 5. 创建默认管理员用户
-- 密码: admin123 (bcrypt哈希)
-- 注意: 生产环境请立即修改此密码!
INSERT INTO users (username, email, password_hash, role, status)
VALUES (
    'admin',
    'admin@example.com',
    '$2b$12$VAOoH5ZhOP9UE2e1xtpl3uGV8gsyvv.RmxAmejmDadFkk4B9rRc0O',  -- admin123
    'admin',
    'active'
) ON DUPLICATE KEY UPDATE username=username;

-- 验证表是否创建成功
SELECT 'users' as table_name, COUNT(*) as count FROM users
UNION ALL
SELECT 'refresh_tokens', COUNT(*) FROM refresh_tokens
UNION ALL
SELECT 'login_logs', COUNT(*) FROM login_logs;

SELECT '用户认证表创建完成！默认管理员: admin / admin123' AS status;
