-- ========================================
-- 性能优化：创建缓存表
-- 用途：将实时计算的数据预先计算好存储在数据库中
-- 作者：Claude
-- 日期：2025-10-26
-- ========================================

USE `binance-data`;

-- ========================================
-- 1. 技术指标缓存表
-- ========================================
CREATE TABLE IF NOT EXISTS technical_indicators_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL COMMENT '交易对，如 BTC/USDT',
    timeframe VARCHAR(10) NOT NULL DEFAULT '1h' COMMENT '时间周期',

    -- RSI指标
    rsi_value DECIMAL(10, 2) COMMENT 'RSI值 (0-100)',
    rsi_signal VARCHAR(20) COMMENT 'RSI信号: overbought/oversold/neutral',

    -- MACD指标
    macd_value DECIMAL(18, 8) COMMENT 'MACD值',
    macd_signal_line DECIMAL(18, 8) COMMENT 'MACD信号线',
    macd_histogram DECIMAL(18, 8) COMMENT 'MACD柱状图',
    macd_trend VARCHAR(20) COMMENT 'MACD趋势: bullish_cross/bearish_cross/neutral',

    -- 布林带
    bb_upper DECIMAL(18, 8) COMMENT '布林带上轨',
    bb_middle DECIMAL(18, 8) COMMENT '布林带中轨',
    bb_lower DECIMAL(18, 8) COMMENT '布林带下轨',
    bb_position VARCHAR(20) COMMENT '价格位置: above_upper/below_lower/middle',
    bb_width DECIMAL(10, 4) COMMENT '布林带宽度(%)',

    -- EMA均线
    ema_short DECIMAL(18, 8) COMMENT '短期EMA (默认12)',
    ema_long DECIMAL(18, 8) COMMENT '长期EMA (默认26)',
    ema_trend VARCHAR(20) COMMENT 'EMA趋势: bullish/bearish/neutral',

    -- KDJ指标
    kdj_k DECIMAL(10, 2) COMMENT 'K值',
    kdj_d DECIMAL(10, 2) COMMENT 'D值',
    kdj_j DECIMAL(10, 2) COMMENT 'J值',
    kdj_signal VARCHAR(20) COMMENT 'KDJ信号: overbought/oversold/neutral',

    -- 成交量分析
    volume_24h DECIMAL(20, 8) COMMENT '24小时成交量',
    volume_avg DECIMAL(20, 8) COMMENT '平均成交量',
    volume_ratio DECIMAL(10, 4) COMMENT '成交量比率 (当前/平均)',
    volume_signal VARCHAR(20) COMMENT '成交量信号: high/low/normal',

    -- 综合评分
    technical_score DECIMAL(5, 2) COMMENT '技术指标综合评分 (0-100)',
    technical_signal VARCHAR(20) COMMENT '技术信号: STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL',

    -- 元数据
    data_points INT COMMENT '用于计算的数据点数量',
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

    UNIQUE KEY uk_symbol_timeframe (`symbol`, timeframe),
    INDEX idx_updated_at (updated_at),
    INDEX idx_technical_score (technical_score),
    INDEX idx_technical_signal (technical_signal)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='技术指标缓存表 - 每5分钟更新';


-- ========================================
-- 2. 24小时价格统计缓存表
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
-- 3. Hyperliquid 币种聚合表
-- ========================================
CREATE TABLE IF NOT EXISTS hyperliquid_symbol_aggregation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL COMMENT '币种代码，如 BTC',
    period VARCHAR(10) NOT NULL DEFAULT '24h' COMMENT '统计周期: 24h/7d/30d',

    -- 资金流向
    net_flow DECIMAL(20, 2) COMMENT '净流入 (USD，正数=流入，负数=流出)',
    inflow DECIMAL(20, 2) COMMENT '总流入 (USD)',
    outflow DECIMAL(20, 2) COMMENT '总流出 (USD)',

    -- 交易统计
    long_trades INT COMMENT '做多交易数',
    short_trades INT COMMENT '做空交易数',
    total_trades INT COMMENT '总交易数',
    long_short_ratio DECIMAL(10, 4) COMMENT '多空比率',

    -- 成交量统计
    total_volume DECIMAL(20, 2) COMMENT '总交易量 (USD)',
    avg_trade_size DECIMAL(20, 2) COMMENT '平均交易规模 (USD)',
    max_trade_size DECIMAL(20, 2) COMMENT '最大单笔交易 (USD)',

    -- 钱包统计
    active_wallets INT COMMENT '活跃钱包数',
    unique_wallets INT COMMENT '参与交易的唯一钱包数',

    -- 盈亏统计
    total_pnl DECIMAL(20, 2) COMMENT '总盈亏 (USD)',
    avg_pnl DECIMAL(18, 2) COMMENT '平均盈亏 (USD)',
    win_rate DECIMAL(5, 2) COMMENT '胜率 (%)',

    -- 评分和信号
    hyperliquid_score DECIMAL(5, 2) COMMENT 'Hyperliquid评分 (0-100)',
    hyperliquid_signal VARCHAR(20) COMMENT '信号: STRONG_BULLISH/BULLISH/NEUTRAL/BEARISH/STRONG_BEARISH',
    sentiment VARCHAR(20) COMMENT '聪明钱情绪: bullish/bearish/neutral',

    -- 元数据
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

    UNIQUE KEY uk_symbol_period (symbol, period),
    INDEX idx_net_flow (net_flow),
    INDEX idx_total_volume (total_volume),
    INDEX idx_hyperliquid_score (hyperliquid_score),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid币种聚合数据 - 每10分钟更新';


-- ========================================
-- 4. 投资建议缓存表 (最终结果)
-- ========================================
CREATE TABLE IF NOT EXISTS investment_recommendations_cache (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL UNIQUE COMMENT '交易对',

    -- 综合评分 (5维度)
    total_score DECIMAL(5, 2) COMMENT '总评分 (0-100)',
    technical_score DECIMAL(5, 2) COMMENT '技术指标评分 (0-100)',
    news_score DECIMAL(5, 2) COMMENT '新闻情绪评分 (0-100)',
    funding_score DECIMAL(5, 2) COMMENT '资金费率评分 (0-100)',
    hyperliquid_score DECIMAL(5, 2) COMMENT 'Hyperliquid评分 (0-100)',
    ethereum_score DECIMAL(5, 2) COMMENT '以太坊链上评分 (0-100)',

    -- 综合信号
    `signal` VARCHAR(20) NOT NULL COMMENT '信号: STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL',
    confidence DECIMAL(5, 2) COMMENT '置信度 (0-100)',

    -- 价格和目标
    current_price DECIMAL(18, 8) COMMENT '当前价格',
    entry_price DECIMAL(18, 8) COMMENT '建议入场价',
    stop_loss DECIMAL(18, 8) COMMENT '止损价',
    take_profit DECIMAL(18, 8) COMMENT '止盈价',

    -- 潜在收益/风险
    potential_gain_pct DECIMAL(10, 4) COMMENT '潜在收益 (%)',
    potential_loss_pct DECIMAL(10, 4) COMMENT '潜在损失 (%)',
    risk_reward_ratio DECIMAL(10, 4) COMMENT '风险回报比',

    -- 风险评估
    risk_level VARCHAR(20) COMMENT '风险等级: LOW/MEDIUM/HIGH/VERY_HIGH',
    risk_factors TEXT COMMENT '风险因素 (JSON数组)',

    -- 分析依据
    reasons TEXT COMMENT '推荐理由 (JSON数组)',
    analysis_summary TEXT COMMENT '分析摘要',

    -- 数据源标记 (用于判断数据完整性)
    has_technical BOOLEAN DEFAULT FALSE COMMENT '是否有技术指标数据',
    has_news BOOLEAN DEFAULT FALSE COMMENT '是否有新闻数据',
    has_funding BOOLEAN DEFAULT FALSE COMMENT '是否有资金费率数据',
    has_hyperliquid BOOLEAN DEFAULT FALSE COMMENT '是否有Hyperliquid数据',
    has_ethereum BOOLEAN DEFAULT FALSE COMMENT '是否有以太坊链上数据',
    data_completeness DECIMAL(5, 2) COMMENT '数据完整性 (0-100%)',

    -- 元数据
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

    INDEX idx_signal (`signal`),
    INDEX idx_total_score (total_score),
    INDEX idx_confidence (confidence),
    INDEX idx_risk_level (risk_level),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='投资建议缓存表 - 每5分钟更新';


-- ========================================
-- 5. 新闻情绪聚合表 (可选优化)
-- ========================================
CREATE TABLE IF NOT EXISTS news_sentiment_aggregation (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(20) NOT NULL COMMENT '币种代码',
    period VARCHAR(10) NOT NULL DEFAULT '24h' COMMENT '统计周期: 24h/7d',

    -- 新闻统计
    total_news INT COMMENT '新闻总数',
    positive_news INT COMMENT '正面新闻数',
    negative_news INT COMMENT '负面新闻数',
    neutral_news INT COMMENT '中性新闻数',

    -- 情绪指标
    sentiment_index DECIMAL(10, 4) COMMENT '情绪指数 (-100 到 +100)',
    avg_sentiment_score DECIMAL(5, 4) COMMENT '平均情绪分数',
    sentiment_trend VARCHAR(20) COMMENT '情绪趋势: improving/declining/stable',

    -- 重要事件
    major_events_count INT COMMENT '重大事件数量',
    critical_news TEXT COMMENT '关键新闻标题 (JSON数组)',

    -- 新闻来源分布
    sources_count INT COMMENT '新闻来源数量',
    top_sources TEXT COMMENT '主要来源 (JSON数组)',

    -- 评分
    news_score DECIMAL(5, 2) COMMENT '新闻评分 (0-100)',

    -- 元数据
    updated_at DATETIME NOT NULL COMMENT '最后更新时间',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',

    UNIQUE KEY uk_symbol_period (symbol, period),
    INDEX idx_sentiment_index (sentiment_index),
    INDEX idx_news_score (news_score),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='新闻情绪聚合表 - 每15分钟更新';


-- ========================================
-- 6. 资金费率统计表 (可选优化)
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


-- ========================================
-- 创建完成后显示表信息
-- ========================================
SHOW TABLES LIKE '%cache%';
SHOW TABLES LIKE '%aggregation%';
SHOW TABLES LIKE '%stats%';

SELECT
    '数据库缓存表创建成功！' AS status,
    COUNT(*) AS new_tables_count
FROM information_schema.tables
WHERE table_schema = 'binance-data'
AND table_name IN (
    'technical_indicators_cache',
    'price_stats_24h',
    'hyperliquid_symbol_aggregation',
    'investment_recommendations_cache',
    'news_sentiment_aggregation',
    'funding_rate_stats'
);
