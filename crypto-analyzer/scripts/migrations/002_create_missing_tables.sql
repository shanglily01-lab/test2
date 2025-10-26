-- ========================================
-- 创建缺失的两张缓存表
-- price_stats_24h 和 funding_rate_stats
-- ========================================

USE `binance-data`;

-- ========================================
-- 1. 24小时价格统计缓存表
-- ========================================
CREATE TABLE IF NOT EXISTS price_stats_24h (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE COMMENT '交易对',

    -- 当前价格
    current_price DECIMAL(18, 8) NOT NULL COMMENT '当前价格',

    -- 24小时统计
    price_24h_ago DECIMAL(18, 8) COMMENT '24小时前价格',
    change_24h DECIMAL(10, 4) COMMENT '24小时涨跌幅 (%)',
    change_24h_abs DECIMAL(18, 8) COMMENT '24小时涨跌额 (绝对值)',
    high_24h DECIMAL(18, 8) COMMENT '24小时最高价',
    low_24h DECIMAL(18, 8) COMMENT '24小时最低价',

    -- 成交量统计
    volume_24h DECIMAL(20, 8) COMMENT '24小时成交量 (基础货币)',
    quote_volume_24h DECIMAL(24, 2) COMMENT '24小时成交额 (USDT)',

    -- 交易统计
    trades_count_24h INT COMMENT '24小时交易笔数',
    avg_trade_size DECIMAL(18, 8) COMMENT '平均交易规模',

    -- 价格区间
    price_range_24h DECIMAL(18, 8) COMMENT '24小时价格波动范围',
    price_range_pct DECIMAL(10, 4) COMMENT '24小时价格波动百分比',

    -- 趋势判断
    trend VARCHAR(20) COMMENT '趋势: strong_up/up/sideways/down/strong_down',

    -- 元数据
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

    INDEX idx_change_24h (change_24h),
    INDEX idx_volume_24h (quote_volume_24h),
    INDEX idx_updated_at (updated_at),
    INDEX idx_trend (trend)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='24小时价格统计缓存 - 每1分钟更新';


-- ========================================
-- 2. 资金费率统计表
-- ========================================
CREATE TABLE IF NOT EXISTS funding_rate_stats (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE COMMENT '交易对',

    -- 当前资金费率
    current_rate DECIMAL(10, 6) COMMENT '当前资金费率',
    current_rate_pct DECIMAL(10, 6) COMMENT '当前资金费率 (%)',

    -- 历史统计
    rate_24h_ago DECIMAL(10, 6) COMMENT '24小时前费率',
    rate_avg_7d DECIMAL(10, 6) COMMENT '7天平均费率',
    rate_avg_30d DECIMAL(10, 6) COMMENT '30天平均费率',

    -- 极值
    rate_max_7d DECIMAL(10, 6) COMMENT '7天最高费率',
    rate_min_7d DECIMAL(10, 6) COMMENT '7天最低费率',

    -- 趋势判断
    trend VARCHAR(20) COMMENT '趋势: strongly_bullish/bullish/neutral/bearish/strongly_bearish',
    market_sentiment VARCHAR(20) COMMENT '市场情绪: overheated/normal/oversold',

    -- 评分
    funding_score DECIMAL(5, 2) COMMENT '资金费率评分 (0-100)',

    -- 元数据
    exchange VARCHAR(20) COMMENT '交易所',
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

    INDEX idx_current_rate (current_rate),
    INDEX idx_trend (trend),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='资金费率统计表 - 每5分钟更新';


-- 验证创建结果
SHOW TABLES LIKE 'price_stats_24h';
SHOW TABLES LIKE 'funding_rate_stats';

SELECT
    '缺失表创建完成！' AS status,
    COUNT(*) AS tables_created
FROM information_schema.tables
WHERE table_schema = 'binance-data'
AND table_name IN ('price_stats_24h', 'funding_rate_stats');
