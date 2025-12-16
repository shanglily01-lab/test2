-- ========================================
-- 创建 pending_positions 表（待开仓自检）
-- 或修复已存在表的 strategy_id 列类型
-- 作者：Auto
-- 日期：2025-12-16
-- ========================================

USE `binance-data`;

-- 创建表（如果不存在）
CREATE TABLE IF NOT EXISTS `pending_positions` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
    `direction` VARCHAR(10) NOT NULL COMMENT '方向: long/short',
    `signal_type` VARCHAR(50) COMMENT '信号类型',
    `signal_price` DECIMAL(20, 8) COMMENT '信号触发时价格',
    `signal_ema9` DECIMAL(20, 8) COMMENT '信号时EMA9',
    `signal_ema26` DECIMAL(20, 8) COMMENT '信号时EMA26',
    `signal_ema_diff_pct` DECIMAL(10, 4) COMMENT '信号时EMA差值百分比',
    `signal_reason` TEXT COMMENT '开仓原因',
    `strategy_id` BIGINT COMMENT '策略ID',
    `account_id` INT DEFAULT 2 COMMENT '账户ID',
    `leverage` INT DEFAULT 10 COMMENT '杠杆倍数',
    `margin_pct` DECIMAL(10, 4) COMMENT '保证金比例',
    `status` VARCHAR(20) DEFAULT 'pending' COMMENT '状态: pending/validated/expired/cancelled',
    `validation_count` INT DEFAULT 0 COMMENT '自检次数',
    `last_validation_time` DATETIME COMMENT '最后一次自检时间',
    `rejection_reason` TEXT COMMENT '拒绝原因',
    `expired_at` DATETIME COMMENT '过期时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX `idx_symbol_direction` (`symbol`, `direction`),
    INDEX `idx_strategy_id` (`strategy_id`),
    INDEX `idx_status` (`status`),
    INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='待开仓自检记录';

-- 如果表已存在但 strategy_id 类型不对，修复它
SET @column_type = (
    SELECT DATA_TYPE
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'pending_positions'
    AND COLUMN_NAME = 'strategy_id'
);

SET @sql_modify = IF(@column_type IS NOT NULL AND @column_type != 'bigint',
    'ALTER TABLE `pending_positions` MODIFY COLUMN `strategy_id` BIGINT NULL COMMENT ''策略ID''',
    'SELECT ''Column strategy_id is already BIGINT or does not exist'' AS message'
);

PREPARE stmt FROM @sql_modify;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;

-- 验证
SELECT COLUMN_NAME, DATA_TYPE, COLUMN_COMMENT
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'binance-data'
AND TABLE_NAME = 'pending_positions'
AND COLUMN_NAME = 'strategy_id';

SELECT 'pending_positions 表创建/修复完成！' AS status;
