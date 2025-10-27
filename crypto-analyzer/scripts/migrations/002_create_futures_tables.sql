-- ============================================
-- 合约数据表创建脚本
-- ============================================
-- 用途: 创建Binance合约市场相关数据表
-- 包含: 持仓量、多空比、资金费率、清算数据
-- 执行方式: 在MySQL中执行或通过命令行 mysql -u用户名 -p数据库名 < 002_create_futures_tables.sql
-- ============================================

USE `binance-data`;

-- ============================================
-- 1. 持仓量数据表
-- ============================================
CREATE TABLE IF NOT EXISTS `futures_open_interest` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance_futures' COMMENT '交易所',

    -- 持仓量数据
    `open_interest` DECIMAL(20, 8) NOT NULL COMMENT '持仓量（合约张数）',
    `open_interest_value` DECIMAL(20, 2) COMMENT '持仓价值(USD)',

    -- 时间
    `timestamp` DATETIME NOT NULL COMMENT '数据时间戳',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    -- 索引
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_symbol_timestamp` (`symbol`, `timestamp`),
    INDEX `idx_exchange_symbol` (`exchange`, `symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约持仓量数据表';

-- ============================================
-- 2. 多空比数据表
-- ============================================
CREATE TABLE IF NOT EXISTS `futures_long_short_ratio` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance_futures' COMMENT '交易所',
    `period` VARCHAR(10) NOT NULL DEFAULT '5m' COMMENT '统计周期',

    -- 多空比数据
    `long_account` FLOAT NOT NULL COMMENT '做多账户比例(%)',
    `short_account` FLOAT NOT NULL COMMENT '做空账户比例(%)',
    `long_short_ratio` FLOAT NOT NULL COMMENT '多空比率',

    -- 时间
    `timestamp` DATETIME NOT NULL COMMENT '数据时间戳',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    -- 索引
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_symbol_timestamp` (`symbol`, `timestamp`),
    INDEX `idx_exchange_symbol` (`exchange`, `symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约多空比数据表';

-- ============================================
-- 3. 资金费率数据表
-- ============================================
CREATE TABLE IF NOT EXISTS `futures_funding_rate` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance_futures' COMMENT '交易所',

    -- 资金费率数据
    `funding_rate` DECIMAL(10, 8) NOT NULL COMMENT '资金费率',
    `funding_time` DATETIME NOT NULL COMMENT '资金费率结算时间',
    `mark_price` DECIMAL(18, 8) COMMENT '标记价格',

    -- 时间
    `timestamp` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '数据采集时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    -- 索引
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_funding_time` (`funding_time`),
    INDEX `idx_symbol_funding_time` (`symbol`, `funding_time`),
    INDEX `idx_exchange_symbol` (`exchange`, `symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约资金费率数据表';

-- ============================================
-- 4. 清算数据表
-- ============================================
CREATE TABLE IF NOT EXISTS `futures_liquidation` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance_futures' COMMENT '交易所',

    -- 清算数据
    `side` VARCHAR(10) NOT NULL COMMENT '方向: LONG/SHORT',
    `price` DECIMAL(18, 8) NOT NULL COMMENT '清算价格',
    `quantity` DECIMAL(20, 8) NOT NULL COMMENT '清算数量',
    `value` DECIMAL(20, 2) COMMENT '清算价值(USD)',

    -- 时间
    `liquidation_time` DATETIME NOT NULL COMMENT '清算时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    -- 索引
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_liquidation_time` (`liquidation_time`),
    INDEX `idx_symbol_time` (`symbol`, `liquidation_time`),
    INDEX `idx_side` (`side`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约清算数据表';

-- ============================================
-- 验证表是否创建成功
-- ============================================
SHOW TABLES LIKE 'futures%';

-- 查看表结构
DESCRIBE futures_open_interest;
DESCRIBE futures_long_short_ratio;
DESCRIBE futures_funding_rate;
DESCRIBE futures_liquidation;

SELECT '✅ 合约数据表创建完成！' AS status;
