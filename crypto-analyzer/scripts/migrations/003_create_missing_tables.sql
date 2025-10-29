-- ===================================================
-- 创建缺失的数据表
-- 用于修复Windows上的500错误
-- ===================================================

-- 1. 创建 futures_data 表（合约数据）
CREATE TABLE IF NOT EXISTS `futures_data` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对符号',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance' COMMENT '交易所',
    `open_interest` DECIMAL(20, 8) NULL COMMENT '持仓量',
    `funding_rate` DECIMAL(10, 6) NULL COMMENT '资金费率',
    `long_short_ratio` DECIMAL(10, 4) NULL COMMENT '多空比',
    `long_account_ratio` DECIMAL(10, 4) NULL COMMENT '多头账户占比',
    `short_account_ratio` DECIMAL(10, 4) NULL COMMENT '空头账户占比',
    `volume_24h` DECIMAL(20, 8) NULL COMMENT '24小时成交量',
    `timestamp` DATETIME NOT NULL COMMENT '数据时间',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_exchange` (`exchange`),
    INDEX `idx_symbol_timestamp` (`symbol`, `timestamp`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约数据表';

-- 2. 创建 news 表（新闻数据）
CREATE TABLE IF NOT EXISTS `news` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `title` VARCHAR(500) NOT NULL COMMENT '新闻标题',
    `content` TEXT NULL COMMENT '新闻内容',
    `url` VARCHAR(1000) NULL COMMENT '新闻链接',
    `source` VARCHAR(100) NOT NULL COMMENT '新闻来源',
    `symbol` VARCHAR(20) NULL COMMENT '相关币种',
    `sentiment_score` DECIMAL(5, 4) NULL COMMENT '情绪得分 (-1到1)',
    `published_at` DATETIME NOT NULL COMMENT '发布时间',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_published_at` (`published_at`),
    INDEX `idx_source` (`source`),
    INDEX `idx_sentiment` (`sentiment_score`),
    FULLTEXT INDEX `idx_title` (`title`),
    FULLTEXT INDEX `idx_content` (`content`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='新闻数据表';

-- 3. 验证表创建
SELECT
    TABLE_NAME as '表名',
    TABLE_ROWS as '记录数',
    CREATE_TIME as '创建时间'
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME IN ('futures_data', 'news')
ORDER BY TABLE_NAME;
