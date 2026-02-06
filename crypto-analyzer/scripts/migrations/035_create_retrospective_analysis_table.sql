-- ============================================================================
-- 12小时复盘分析表
-- 创建日期: 2026-02-06
-- 描述: 存储每12小时的市场复盘分析结果
-- ============================================================================

CREATE TABLE IF NOT EXISTS retrospective_analysis (
    id INT PRIMARY KEY AUTO_INCREMENT,

    -- 分析时间范围
    analysis_time DATETIME NOT NULL COMMENT '分析执行时间',
    period_start DATETIME NOT NULL COMMENT '分析区间开始时间',
    period_end DATETIME NOT NULL COMMENT '分析区间结束时间',

    -- 市场走势分析
    btc_price_change_pct DECIMAL(10,4) COMMENT 'BTC价格变化%',
    btc_volatility_pct DECIMAL(10,4) COMMENT 'BTC波动率%',
    btc_direction VARCHAR(20) COMMENT 'BTC方向判断',

    eth_price_change_pct DECIMAL(10,4) COMMENT 'ETH价格变化%',
    eth_volatility_pct DECIMAL(10,4) COMMENT 'ETH波动率%',
    eth_direction VARCHAR(20) COMMENT 'ETH方向判断',

    bnb_price_change_pct DECIMAL(10,4) COMMENT 'BNB价格变化%',
    bnb_volatility_pct DECIMAL(10,4) COMMENT 'BNB波动率%',
    bnb_direction VARCHAR(20) COMMENT 'BNB方向判断',

    sol_price_change_pct DECIMAL(10,4) COMMENT 'SOL价格变化%',
    sol_volatility_pct DECIMAL(10,4) COMMENT 'SOL波动率%',
    sol_direction VARCHAR(20) COMMENT 'SOL方向判断',

    overall_market_direction VARCHAR(20) COMMENT '整体市场方向',

    -- Big4信号统计
    big4_signal_count INT COMMENT 'Big4信号更新次数',
    big4_bullish_count INT COMMENT 'BULLISH信号次数',
    big4_bearish_count INT COMMENT 'BEARISH信号次数',
    big4_neutral_count INT COMMENT 'NEUTRAL信号次数',

    -- 交易表现
    total_trades INT DEFAULT 0 COMMENT '总交易数',
    profit_trades INT DEFAULT 0 COMMENT '盈利交易数',
    loss_trades INT DEFAULT 0 COMMENT '亏损交易数',
    win_rate DECIMAL(5,2) COMMENT '胜率%',
    total_pnl DECIMAL(20,2) COMMENT '总盈亏USDT',

    -- 问题诊断
    counter_trend_trades INT DEFAULT 0 COMMENT '逆势交易数量',
    signal_mismatch_trades INT DEFAULT 0 COMMENT 'Big4信号不匹配数量',
    signal_lag_trades INT DEFAULT 0 COMMENT '信号滞后数量',
    false_breakout_trades INT DEFAULT 0 COMMENT '震荡市误判数量',

    -- 评价等级
    performance_rating VARCHAR(20) COMMENT '交易表现评价: 优秀/良好/一般/较差',

    -- 详细分析JSON
    market_analysis_json TEXT COMMENT '市场分析详细数据JSON',
    trading_analysis_json TEXT COMMENT '交易分析详细数据JSON',
    loss_analysis_json TEXT COMMENT '亏损分析详细数据JSON',

    -- 审计字段
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_analysis_time (analysis_time),
    INDEX idx_period (period_start, period_end),
    INDEX idx_win_rate (win_rate),
    INDEX idx_pnl (total_pnl)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='12小时复盘分析表';
