-- ============================================================
-- 修复 live_futures_positions 表 status 列长度
-- ============================================================

USE `binance-data`;

-- 增加 status 列长度到 50
ALTER TABLE live_futures_positions
MODIFY COLUMN status VARCHAR(50) DEFAULT 'OPEN' COMMENT '状态: OPEN/CLOSED/LIQUIDATED/PENDING/CANCELED';

-- 验证
DESCRIBE live_futures_positions;

SELECT 'status 列长度已更新！' AS message;
