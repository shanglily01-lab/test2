-- ========================================
-- 修复 futures_orders 表中 strategy_id 列的类型
-- 将小类型（如 TINYINT, SMALLINT, INT）改为 BIGINT
-- 作者：Auto
-- 日期：2025-12-02
-- ========================================

USE `binance-data`;

-- 检查 strategy_id 列是否存在且类型不是 BIGINT
SET @column_type = (
    SELECT DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'futures_orders'
    AND COLUMN_NAME = 'strategy_id'
);

-- 如果列存在且不是 BIGINT，则修改为 BIGINT
SET @sql_modify = IF(@column_type IS NOT NULL AND @column_type != 'bigint',
    'ALTER TABLE `futures_orders` MODIFY COLUMN `strategy_id` BIGINT NULL COMMENT ''策略ID''',
    'SELECT ''Column strategy_id is already BIGINT or does not exist'' AS message'
);

PREPARE stmt FROM @sql_modify;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 验证修改
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'binance-data'
AND TABLE_NAME = 'futures_orders'
AND COLUMN_NAME = 'strategy_id';

SELECT 'strategy_id 列类型修复完成！' AS status;
