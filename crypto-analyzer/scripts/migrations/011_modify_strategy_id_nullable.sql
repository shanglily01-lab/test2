-- ========================================
-- 修改策略资金管理表，允许 strategy_id 为 NULL
-- 用途：系统操作（如充值、提现）不需要策略ID
-- 作者：Auto
-- 日期：2025-11-27
-- ========================================

USE `binance-data`;

-- 修改 strategy_id 字段，允许为 NULL
ALTER TABLE `strategy_capital_management` 
MODIFY COLUMN `strategy_id` BIGINT NULL COMMENT '策略ID（NULL表示系统操作，如充值、提现）';

