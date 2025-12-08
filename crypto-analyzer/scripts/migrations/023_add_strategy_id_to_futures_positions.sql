-- ============================================================
-- 添加 strategy_id 列到 futures_positions 表
-- ============================================================

USE `binance-data`;

-- 添加 strategy_id 列（如果不存在）
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'futures_positions'
    AND COLUMN_NAME = 'strategy_id'
);

SET @sql = IF(@column_exists = 0,
    'ALTER TABLE futures_positions ADD COLUMN strategy_id BIGINT COMMENT ''策略ID'' AFTER signal_id',
    'SELECT ''Column strategy_id already exists'' AS message'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 添加索引（如果不存在）
SET @index_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'futures_positions'
    AND INDEX_NAME = 'idx_strategy_id'
);

SET @sql_idx = IF(@index_exists = 0,
    'ALTER TABLE futures_positions ADD INDEX idx_strategy_id (strategy_id)',
    'SELECT ''Index idx_strategy_id already exists'' AS message'
);

PREPARE stmt FROM @sql_idx;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 添加复合索引（如果不存在）
SET @idx2_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.STATISTICS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'futures_positions'
    AND INDEX_NAME = 'idx_symbol_strategy'
);

SET @sql_idx2 = IF(@idx2_exists = 0,
    'ALTER TABLE futures_positions ADD INDEX idx_symbol_strategy (symbol, strategy_id)',
    'SELECT ''Index idx_symbol_strategy already exists'' AS message'
);

PREPARE stmt FROM @sql_idx2;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 验证
DESCRIBE futures_positions;

SELECT 'strategy_id 列添加完成！' AS status;
