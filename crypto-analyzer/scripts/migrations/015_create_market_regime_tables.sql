-- ============================================================
-- 行情识别与策略切换系统数据库表
-- 用于自动识别趋势/震荡行情并切换策略参数
-- ============================================================

USE `binance-data`;

-- 1. 行情状态记录表
-- 记录每个交易对在不同时间周期的行情类型
CREATE TABLE IF NOT EXISTS `market_regime` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `timeframe` VARCHAR(10) NOT NULL DEFAULT '15m' COMMENT '时间周期',

    -- 行情类型
    `regime_type` VARCHAR(30) NOT NULL COMMENT '行情类型: strong_uptrend/weak_uptrend/strong_downtrend/weak_downtrend/ranging',
    `regime_score` DECIMAL(5, 2) DEFAULT 0 COMMENT '行情得分 (-100到100, 正为多头, 负为空头, 接近0为震荡)',

    -- 判断依据
    `ema_diff_pct` DECIMAL(8, 4) COMMENT 'EMA9与EMA26差值百分比',
    `adx_value` DECIMAL(8, 4) COMMENT 'ADX值(趋势强度)',
    `trend_bars` INT COMMENT '趋势持续K线数',
    `volatility` DECIMAL(8, 4) COMMENT '波动率',

    -- 额外信息
    `details` JSON COMMENT '详细分析数据',

    -- 时间
    `detected_at` DATETIME NOT NULL COMMENT '检测时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- 索引
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timeframe` (`timeframe`),
    INDEX `idx_regime_type` (`regime_type`),
    INDEX `idx_symbol_timeframe` (`symbol`, `timeframe`),
    INDEX `idx_detected_at` (`detected_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='行情状态记录表';


-- 2. 策略参数预设表
-- 存储不同行情类型对应的策略参数配置
CREATE TABLE IF NOT EXISTS `strategy_regime_params` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `strategy_id` INT NOT NULL COMMENT '关联的策略ID',
    `regime_type` VARCHAR(30) NOT NULL COMMENT '行情类型',

    -- 是否启用该行情类型的交易
    `enabled` BOOLEAN DEFAULT TRUE COMMENT '是否在该行情类型下启用交易',

    -- 策略参数（JSON格式，会覆盖基础策略配置）
    `params` JSON NOT NULL COMMENT '该行情类型下的策略参数',

    -- 描述
    `description` VARCHAR(500) COMMENT '参数说明',

    -- 时间
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,
    `updated_at` DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- 索引
    UNIQUE KEY `uk_strategy_regime` (`strategy_id`, `regime_type`),
    INDEX `idx_regime_type` (`regime_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略行情参数预设表';


-- 3. 行情切换日志表
-- 记录行情切换事件，便于回溯分析
CREATE TABLE IF NOT EXISTS `market_regime_changes` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `timeframe` VARCHAR(10) NOT NULL DEFAULT '15m' COMMENT '时间周期',

    -- 切换信息
    `old_regime` VARCHAR(30) COMMENT '原行情类型',
    `new_regime` VARCHAR(30) NOT NULL COMMENT '新行情类型',
    `old_score` DECIMAL(5, 2) COMMENT '原行情得分',
    `new_score` DECIMAL(5, 2) COMMENT '新行情得分',

    -- 时间
    `changed_at` DATETIME NOT NULL COMMENT '切换时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- 索引
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_changed_at` (`changed_at`),
    INDEX `idx_symbol_timeframe` (`symbol`, `timeframe`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='行情切换日志表';


-- 4. 为 trading_strategies 表添加行情自适应开关
-- 检查并添加 adaptive_regime 字段
SET @column_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = 'binance-data'
    AND TABLE_NAME = 'trading_strategies'
    AND COLUMN_NAME = 'adaptive_regime'
);

SET @sql_add_column = IF(@column_exists = 0,
    'ALTER TABLE `trading_strategies` ADD COLUMN `adaptive_regime` BOOLEAN DEFAULT FALSE COMMENT ''是否启用行情自适应'' AFTER `market_type`',
    'SELECT ''Column adaptive_regime already exists'' AS message'
);

PREPARE stmt FROM @sql_add_column;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;


-- 5. 插入默认的行情参数预设（示例）
-- 注意：这里使用 INSERT IGNORE 避免重复插入

-- 为策略ID=1 设置默认的行情参数
INSERT IGNORE INTO `strategy_regime_params` (`strategy_id`, `regime_type`, `enabled`, `params`, `description`)
VALUES
-- 强趋势上涨：积极做多
(1, 'strong_uptrend', TRUE, JSON_OBJECT(
    'sustainedTrend', TRUE,
    'sustainedTrendMinStrength', 0.3,
    'sustainedTrendMaxStrength', 2.0,
    'allowLong', TRUE,
    'allowShort', FALSE,
    'stopLossPercent', 3.0,
    'takeProfitPercent', 8.0
), '强趋势上涨：启用持续趋势做多，较宽止盈'),

-- 弱趋势上涨：谨慎做多
(1, 'weak_uptrend', TRUE, JSON_OBJECT(
    'sustainedTrend', FALSE,
    'allowLong', TRUE,
    'allowShort', FALSE,
    'stopLossPercent', 2.5,
    'takeProfitPercent', 5.0
), '弱趋势上涨：只在金叉时做多，较窄止盈'),

-- 强趋势下跌：积极做空
(1, 'strong_downtrend', TRUE, JSON_OBJECT(
    'sustainedTrend', TRUE,
    'sustainedTrendMinStrength', 0.3,
    'sustainedTrendMaxStrength', 2.0,
    'allowLong', FALSE,
    'allowShort', TRUE,
    'stopLossPercent', 3.0,
    'takeProfitPercent', 8.0
), '强趋势下跌：启用持续趋势做空，较宽止盈'),

-- 弱趋势下跌：谨慎做空
(1, 'weak_downtrend', TRUE, JSON_OBJECT(
    'sustainedTrend', FALSE,
    'allowLong', FALSE,
    'allowShort', TRUE,
    'stopLossPercent', 2.5,
    'takeProfitPercent', 5.0
), '弱趋势下跌：只在死叉时做空，较窄止盈'),

-- 震荡行情：不交易或只做小波段
(1, 'ranging', FALSE, JSON_OBJECT(
    'sustainedTrend', FALSE,
    'allowLong', FALSE,
    'allowShort', FALSE
), '震荡行情：暂停交易，等待趋势明确');


-- 验证表创建
SELECT '✅ 行情识别系统表创建完成！' AS status;

SHOW TABLES LIKE 'market_regime%';
SHOW TABLES LIKE 'strategy_regime_params';

-- 查看示例数据
SELECT * FROM strategy_regime_params WHERE strategy_id = 1;
