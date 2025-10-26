-- 加密货币 ETF 数据库表结构
-- 追踪 Bitcoin ETF 和 Ethereum ETF 的每日资金流向

-- 1. ETF 产品基本信息表
CREATE TABLE IF NOT EXISTS crypto_etf_products (
    id INT AUTO_INCREMENT PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL UNIQUE COMMENT 'ETF代码，如 IBIT, FBTC',
    full_name VARCHAR(200) NOT NULL COMMENT '完整名称',
    provider VARCHAR(100) NOT NULL COMMENT '发行方，如 BlackRock, Fidelity',
    asset_type VARCHAR(20) NOT NULL COMMENT '资产类型: BTC, ETH',
    etf_type VARCHAR(20) NOT NULL COMMENT 'ETF类型: spot, futures',
    launch_date DATE COMMENT '上市日期',

    -- 官方信息
    official_website VARCHAR(500) COMMENT '官网链接',
    expense_ratio DECIMAL(5, 4) COMMENT '费用率 (%)',

    -- 元数据
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否活跃',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_ticker (ticker),
    INDEX idx_asset_type (asset_type),
    INDEX idx_provider (provider)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='加密货币ETF产品信息';

-- 2. ETF 每日资金流向表
CREATE TABLE IF NOT EXISTS crypto_etf_flows (
    id INT AUTO_INCREMENT PRIMARY KEY,
    etf_id INT NOT NULL COMMENT 'ETF产品ID',
    ticker VARCHAR(20) NOT NULL COMMENT 'ETF代码',
    trade_date DATE NOT NULL COMMENT '交易日期',

    -- 资金流向数据 (USD)
    net_inflow DECIMAL(20, 2) DEFAULT 0 COMMENT '净流入 (USD)',
    gross_inflow DECIMAL(20, 2) DEFAULT 0 COMMENT '总流入 (USD)',
    gross_outflow DECIMAL(20, 2) DEFAULT 0 COMMENT '总流出 (USD)',

    -- 持仓数据
    aum DECIMAL(20, 2) COMMENT '管理资产规模 (USD)',
    btc_holdings DECIMAL(20, 8) COMMENT 'BTC持仓量',
    eth_holdings DECIMAL(20, 8) COMMENT 'ETH持仓量',
    shares_outstanding DECIMAL(20, 2) COMMENT '流通股数',

    -- 价格数据
    nav DECIMAL(18, 8) COMMENT '单位净值',
    close_price DECIMAL(18, 8) COMMENT '收盘价',
    volume DECIMAL(20, 2) COMMENT '交易量',

    -- 元数据
    data_source VARCHAR(50) DEFAULT 'manual' COMMENT '数据来源',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_etf_date (etf_id, trade_date),
    INDEX idx_ticker_date (ticker, trade_date),
    INDEX idx_trade_date (trade_date),
    INDEX idx_net_inflow (net_inflow),
    FOREIGN KEY (etf_id) REFERENCES crypto_etf_products(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='加密货币ETF每日资金流向';

-- 3. ETF 每日汇总表 (按资产类型)
CREATE TABLE IF NOT EXISTS crypto_etf_daily_summary (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL COMMENT '交易日期',
    asset_type VARCHAR(20) NOT NULL COMMENT '资产类型: BTC, ETH',

    -- 汇总数据 (USD)
    total_net_inflow DECIMAL(20, 2) DEFAULT 0 COMMENT '总净流入',
    total_gross_inflow DECIMAL(20, 2) DEFAULT 0 COMMENT '总流入',
    total_gross_outflow DECIMAL(20, 2) DEFAULT 0 COMMENT '总流出',
    total_aum DECIMAL(20, 2) COMMENT '总AUM',
    total_holdings DECIMAL(20, 8) COMMENT '总持仓量',

    -- 统计数据
    etf_count INT DEFAULT 0 COMMENT '统计的ETF数量',
    inflow_count INT DEFAULT 0 COMMENT '净流入ETF数量',
    outflow_count INT DEFAULT 0 COMMENT '净流出ETF数量',

    -- 排名数据
    top_inflow_ticker VARCHAR(20) COMMENT '最大流入ETF',
    top_inflow_amount DECIMAL(20, 2) COMMENT '最大流入金额',
    top_outflow_ticker VARCHAR(20) COMMENT '最大流出ETF',
    top_outflow_amount DECIMAL(20, 2) COMMENT '最大流出金额',

    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_date_asset (trade_date, asset_type),
    INDEX idx_trade_date (trade_date),
    INDEX idx_asset_type (asset_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='加密货币ETF每日汇总';

-- 4. ETF 市场情绪指标表
CREATE TABLE IF NOT EXISTS crypto_etf_sentiment (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trade_date DATE NOT NULL COMMENT '交易日期',
    asset_type VARCHAR(20) NOT NULL COMMENT '资产类型: BTC, ETH',

    -- 情绪指标
    sentiment_score DECIMAL(5, 2) COMMENT '情绪评分 (0-100)',
    flow_trend VARCHAR(20) COMMENT '资金流向趋势: strong_inflow, inflow, neutral, outflow, strong_outflow',

    -- 连续统计
    consecutive_inflow_days INT DEFAULT 0 COMMENT '连续流入天数',
    consecutive_outflow_days INT DEFAULT 0 COMMENT '连续流出天数',

    -- 移动平均
    ma_5day_inflow DECIMAL(20, 2) COMMENT '5日均流入',
    ma_10day_inflow DECIMAL(20, 2) COMMENT '10日均流入',
    ma_20day_inflow DECIMAL(20, 2) COMMENT '20日均流入',

    -- 累计数据
    mtd_net_inflow DECIMAL(20, 2) COMMENT '月累计净流入',
    ytd_net_inflow DECIMAL(20, 2) COMMENT '年累计净流入',

    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_date_asset (trade_date, asset_type),
    INDEX idx_trade_date (trade_date),
    INDEX idx_sentiment_score (sentiment_score)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETF市场情绪指标';

-- 5. ETF 历史事件表
CREATE TABLE IF NOT EXISTS crypto_etf_events (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_date DATE NOT NULL COMMENT '事件日期',
    asset_type VARCHAR(20) COMMENT '相关资产: BTC, ETH, ALL',
    event_type VARCHAR(50) NOT NULL COMMENT '事件类型: new_launch, milestone, record_inflow, record_outflow',

    -- 事件详情
    title VARCHAR(200) NOT NULL COMMENT '事件标题',
    description TEXT COMMENT '事件描述',
    impact_level VARCHAR(20) COMMENT '影响程度: high, medium, low',

    -- 相关数据
    related_etf_id INT COMMENT '相关ETF ID',
    related_ticker VARCHAR(20) COMMENT '相关ETF代码',
    amount DECIMAL(20, 2) COMMENT '相关金额',

    -- 元数据
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_event_date (event_date),
    INDEX idx_asset_type (asset_type),
    INDEX idx_event_type (event_type),
    FOREIGN KEY (related_etf_id) REFERENCES crypto_etf_products(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='ETF重要事件记录';

-- ============================================================
-- 初始化数据：主要的加密货币 ETF 产品
-- ============================================================

-- Bitcoin 现货 ETF (美国)
INSERT IGNORE INTO crypto_etf_products (ticker, full_name, provider, asset_type, etf_type, launch_date, expense_ratio, is_active) VALUES
-- BlackRock (贝莱德)
('IBIT', 'iShares Bitcoin Trust', 'BlackRock', 'BTC', 'spot', '2024-01-11', 0.0025, TRUE),

-- Fidelity (富达)
('FBTC', 'Fidelity Wise Origin Bitcoin Fund', 'Fidelity', 'BTC', 'spot', '2024-01-11', 0.0025, TRUE),

-- ARK Invest & 21Shares
('ARKB', 'ARK 21Shares Bitcoin ETF', 'ARK Invest', 'BTC', 'spot', '2024-01-11', 0.0021, TRUE),

-- Bitwise
('BITB', 'Bitwise Bitcoin ETF', 'Bitwise', 'BTC', 'spot', '2024-01-11', 0.0020, TRUE),

-- Grayscale (已有产品转型)
('GBTC', 'Grayscale Bitcoin Trust', 'Grayscale', 'BTC', 'spot', '2024-01-11', 0.0150, TRUE),

-- VanEck
('HODL', 'VanEck Bitcoin Trust', 'VanEck', 'BTC', 'spot', '2024-01-11', 0.0025, TRUE),

-- Invesco & Galaxy
('BTCO', 'Invesco Galaxy Bitcoin ETF', 'Invesco', 'BTC', 'spot', '2024-01-11', 0.0025, TRUE),

-- WisdomTree
('BTCW', 'WisdomTree Bitcoin Fund', 'WisdomTree', 'BTC', 'spot', '2024-01-11', 0.0025, TRUE),

-- Franklin Templeton
('EZBC', 'Franklin Bitcoin ETF', 'Franklin Templeton', 'BTC', 'spot', '2024-01-11', 0.0019, TRUE),

-- Valkyrie
('BRRR', 'Valkyrie Bitcoin Fund', 'Valkyrie', 'BTC', 'spot', '2024-01-11', 0.0025, TRUE),

-- Hashdex
('DEFI', 'Hashdex Bitcoin ETF', 'Hashdex', 'BTC', 'spot', '2024-01-11', 0.0090, TRUE);

-- Ethereum 现货 ETF (美国)
INSERT IGNORE INTO crypto_etf_products (ticker, full_name, provider, asset_type, etf_type, launch_date, expense_ratio, is_active) VALUES
-- BlackRock
('ETHA', 'iShares Ethereum Trust', 'BlackRock', 'ETH', 'spot', '2024-07-23', 0.0025, TRUE),

-- Fidelity
('FETH', 'Fidelity Ethereum Fund', 'Fidelity', 'ETH', 'spot', '2024-07-23', 0.0025, TRUE),

-- Grayscale
('ETHE', 'Grayscale Ethereum Trust', 'Grayscale', 'ETH', 'spot', '2024-07-23', 0.0250, TRUE),

-- Grayscale Mini
('ETH', 'Grayscale Ethereum Mini Trust', 'Grayscale', 'ETH', 'spot', '2024-07-23', 0.0015, TRUE),

-- Bitwise
('ETHW', 'Bitwise Ethereum ETF', 'Bitwise', 'ETH', 'spot', '2024-07-23', 0.0020, TRUE),

-- VanEck
('ETHV', 'VanEck Ethereum ETF', 'VanEck', 'ETH', 'spot', '2024-07-23', 0.0020, TRUE),

-- Invesco & Galaxy
('QETH', 'Invesco Galaxy Ethereum ETF', 'Invesco', 'ETH', 'spot', '2024-07-23', 0.0025, TRUE),

-- Franklin Templeton
('EZET', 'Franklin Ethereum ETF', 'Franklin Templeton', 'ETH', 'spot', '2024-07-23', 0.0019, TRUE),

-- 21Shares
('CETH', '21Shares Core Ethereum ETF', '21Shares', 'ETH', 'spot', '2024-07-23', 0.0021, TRUE);

-- ============================================================
-- 视图：快速查询每日汇总
-- ============================================================

CREATE OR REPLACE VIEW v_etf_daily_flows AS
SELECT
    f.trade_date,
    p.asset_type,
    p.ticker,
    p.provider,
    f.net_inflow,
    f.gross_inflow,
    f.gross_outflow,
    f.aum,
    f.btc_holdings,
    f.eth_holdings,
    f.volume,
    f.nav,
    f.close_price
FROM crypto_etf_flows f
JOIN crypto_etf_products p ON f.etf_id = p.id
WHERE p.is_active = TRUE
ORDER BY f.trade_date DESC, f.net_inflow DESC;

-- 视图：最新一天的数据
CREATE OR REPLACE VIEW v_etf_latest_flows AS
SELECT
    p.ticker,
    p.full_name,
    p.provider,
    p.asset_type,
    f.trade_date,
    f.net_inflow,
    f.gross_inflow,
    f.gross_outflow,
    f.aum,
    f.btc_holdings,
    f.eth_holdings,
    RANK() OVER (PARTITION BY p.asset_type ORDER BY f.net_inflow DESC) as inflow_rank
FROM crypto_etf_flows f
JOIN crypto_etf_products p ON f.etf_id = p.id
WHERE f.trade_date = (SELECT MAX(trade_date) FROM crypto_etf_flows)
  AND p.is_active = TRUE;

-- 视图：每周汇总
CREATE OR REPLACE VIEW v_etf_weekly_summary AS
SELECT
    DATE_SUB(f.trade_date, INTERVAL WEEKDAY(f.trade_date) DAY) as week_start,
    p.asset_type,
    SUM(f.net_inflow) as weekly_net_inflow,
    AVG(f.net_inflow) as avg_daily_inflow,
    SUM(f.gross_inflow) as weekly_gross_inflow,
    SUM(f.gross_outflow) as weekly_gross_outflow,
    COUNT(DISTINCT f.trade_date) as trading_days,
    AVG(f.aum) as avg_aum
FROM crypto_etf_flows f
JOIN crypto_etf_products p ON f.etf_id = p.id
WHERE p.is_active = TRUE
GROUP BY week_start, p.asset_type
ORDER BY week_start DESC, p.asset_type;
