-- ========================================
-- 添加持仓量比字段到 futures_long_short_ratio 表
-- 区分账户数比和持仓量比
-- 日期：2025-11-05
-- ========================================

USE `binance-data`;

-- 1. 添加新字段：持仓量比相关数据
ALTER TABLE `futures_long_short_ratio`
ADD COLUMN `long_position` FLOAT NULL COMMENT '做多持仓量比例(%)' AFTER `short_account`,
ADD COLUMN `short_position` FLOAT NULL COMMENT '做空持仓量比例(%)' AFTER `long_position`,
ADD COLUMN `long_short_position_ratio` FLOAT NULL COMMENT '持仓量多空比率' AFTER `short_position`;

-- 2. 更新字段注释，明确区分账户数比和持仓量比
ALTER TABLE `futures_long_short_ratio`
MODIFY COLUMN `long_account` FLOAT NOT NULL COMMENT '做多账户数比例(%)',
MODIFY COLUMN `short_account` FLOAT NOT NULL COMMENT '做空账户数比例(%)',
MODIFY COLUMN `long_short_ratio` FLOAT NOT NULL COMMENT '账户数多空比率';

-- 3. 查看更新后的表结构
DESCRIBE `futures_long_short_ratio`;

-- 4. 验证字段
SELECT
    '表结构更新完成' as status,
    COUNT(*) as total_records,
    COUNT(long_short_ratio) as has_account_ratio,
    COUNT(long_short_position_ratio) as has_position_ratio
FROM `futures_long_short_ratio`;
