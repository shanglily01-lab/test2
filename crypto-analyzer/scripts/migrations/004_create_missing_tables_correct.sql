-- ===================================================
-- 创建缺失的数据表（正确版本）
-- 用于修复Windows上的500错误
-- ===================================================

-- 1. 创建 news_data 表（新闻数据）
CREATE TABLE IF NOT EXISTS `news_data` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `news_id` VARCHAR(100) UNIQUE COMMENT '新闻唯一标识',

    -- 新闻内容
    `title` VARCHAR(500) NOT NULL COMMENT '标题',
    `url` VARCHAR(500) NOT NULL UNIQUE COMMENT 'URL',
    `source` VARCHAR(100) COMMENT '来源(coindesk, cointelegraph等)',
    `description` VARCHAR(2000) COMMENT '描述/摘要',

    -- 时间
    `published_at` VARCHAR(100) COMMENT '发布时间(字符串格式)',
    `published_datetime` DATETIME COMMENT '发布时间(日期时间)',

    -- 关联币种
    `symbols` VARCHAR(200) COMMENT '关联的币种,逗号分隔(如BTC,ETH)',

    -- 情绪分析
    `sentiment` VARCHAR(20) COMMENT 'positive, negative, neutral',
    `sentiment_score` FLOAT COMMENT '情绪分数',

    -- 投票数据
    `votes_positive` INT DEFAULT 0,
    `votes_negative` INT DEFAULT 0,
    `votes_important` INT DEFAULT 0,

    -- 元数据
    `data_source` VARCHAR(50) COMMENT '数据来源(cryptopanic, rss, reddit)',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    INDEX `idx_news_id` (`news_id`),
    INDEX `idx_published_datetime` (`published_datetime`),
    INDEX `idx_symbols` (`symbols`),
    INDEX `idx_sentiment` (`sentiment`),
    INDEX `idx_source` (`source`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='新闻数据表';

-- 2. 创建 futures_open_interest 表（合约持仓量）
CREATE TABLE IF NOT EXISTS `futures_open_interest` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance_futures' COMMENT '交易所',

    -- 持仓量数据
    `open_interest` DECIMAL(20, 8) NOT NULL COMMENT '持仓量（合约张数）',
    `open_interest_value` DECIMAL(20, 2) COMMENT '持仓价值(USD)',

    -- 时间
    `timestamp` DATETIME NOT NULL COMMENT '时间戳',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_symbol_timestamp` (`symbol`, `timestamp`),
    INDEX `idx_exchange_symbol` (`exchange`, `symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约持仓量数据表';

-- 3. 创建 futures_long_short_ratio 表（合约多空比）
CREATE TABLE IF NOT EXISTS `futures_long_short_ratio` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
    `exchange` VARCHAR(20) NOT NULL DEFAULT 'binance_futures' COMMENT '交易所',
    `period` VARCHAR(10) NOT NULL DEFAULT '5m' COMMENT '统计周期',

    -- 多空比数据
    `long_account` FLOAT NOT NULL COMMENT '做多账户比例',
    `short_account` FLOAT NOT NULL COMMENT '做空账户比例',
    `long_short_ratio` FLOAT NOT NULL COMMENT '多空比率',

    -- 时间
    `timestamp` DATETIME NOT NULL COMMENT '时间戳',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',

    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_symbol_timestamp` (`symbol`, `timestamp`),
    INDEX `idx_exchange_symbol_period` (`exchange`, `symbol`, `period`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='合约多空比数据表';

-- 4. 验证表创建
SELECT
    TABLE_NAME as '表名',
    TABLE_ROWS as '记录数',
    CREATE_TIME as '创建时间'
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
    AND TABLE_NAME IN ('news_data', 'futures_open_interest', 'futures_long_short_ratio')
ORDER BY TABLE_NAME;
