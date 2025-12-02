-- ========================================
-- 为 futures_orders 表添加策略ID和超时字段
-- 用途：支持限价单超时自动转市价功能
-- 作者：Auto
-- 日期：2025-12-02
-- ========================================

USE `binance-data`;

-- 添加 strategy_id 字段（如果不存在）
-- 用于关联策略，以便获取限价单超时配置
SET @dbname = 'binance-data';
SET @tablename = 'futures_orders';

-- 检查并添加 strategy_id 字段
SET @column_exists_strategy_id = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = @dbname
    AND TABLE_NAME = @tablename
    AND COLUMN_NAME = 'strategy_id'
);

SET @sql_strategy_id = IF(@column_exists_strategy_id = 0,
    'ALTER TABLE `futures_orders` ADD COLUMN `strategy_id` INT NULL COMMENT ''策略ID'' AFTER `account_id`',
    'SELECT ''Column strategy_id already exists'' AS message'
);

PREPARE stmt FROM @sql_strategy_id;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 检查并添加 timeout_minutes 字段
SET @column_exists_timeout = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = @dbname
    AND TABLE_NAME = @tablename
    AND COLUMN_NAME = 'timeout_minutes'
);

SET @sql_timeout = IF(@column_exists_timeout = 0,
    'ALTER TABLE `futures_orders` ADD COLUMN `timeout_minutes` INT NULL DEFAULT 0 COMMENT ''限价单超时时间（分钟），0表示不超时'' AFTER `strategy_id`',
    'SELECT ''Column timeout_minutes already exists'' AS message'
);

PREPARE stmt FROM @sql_timeout;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 添加索引（如果不存在）
SET @index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = @dbname
    AND TABLE_NAME = @tablename
    AND INDEX_NAME = 'idx_strategy_id'
);

SET @sql_index = IF(@index_exists = 0,
    'ALTER TABLE `futures_orders` ADD INDEX `idx_strategy_id` (`strategy_id`)',
    'SELECT ''Index idx_strategy_id already exists'' AS message'
);

PREPARE stmt FROM @sql_index;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 验证修改
DESCRIBE `futures_orders`;

SELECT 'futures_orders 表字段添加完成！' AS status;
