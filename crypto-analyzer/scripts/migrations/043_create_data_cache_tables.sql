-- ============================================================
-- Migration 043: 创建 data_cache layer（预计算缓存表）
-- 目的：将高频复杂 SQL 查询替换为定时刷新的预计算数据
--
-- 使用方式：
--   mysql -u root -p dimension < 043_create_data_cache_tables.sql
--
-- 注意：需要先创建 data_cache 数据库
--   CREATE DATABASE IF NOT EXISTS data_cache
--     DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
-- ============================================================

CREATE DATABASE IF NOT EXISTS data_cache
  DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE data_cache;

-- ============================================================
-- 3.1 市场概览快照 (每 1 分钟更新)
-- 所有价格、行情、情绪数据的单行汇总
-- ============================================================
CREATE TABLE IF NOT EXISTS market_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,

    -- 核心币种价格
    btc_price       DECIMAL(20,8)  DEFAULT NULL,
    btc_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    eth_price       DECIMAL(20,8)  DEFAULT NULL,
    eth_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    sol_price       DECIMAL(20,8)  DEFAULT NULL,
    sol_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    bnb_price       DECIMAL(20,8)  DEFAULT NULL,
    bnb_change_24h  DECIMAL(10,2)  DEFAULT NULL,
    xrp_price       DECIMAL(20,8)  DEFAULT NULL,
    xrp_change_24h  DECIMAL(10,2)  DEFAULT NULL,

    -- 市场情绪
    big4_signal            VARCHAR(20)  DEFAULT NULL COMMENT 'BEARISH/BULLISH/NEUTRAL',
    fear_greed_value       INT          DEFAULT NULL,
    fear_greed_label       VARCHAR(20)  DEFAULT NULL,

    -- 元数据
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    compute_ms      INT          DEFAULT 0,

    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='市场概览快照 — 每 1min 更新, 供 Gemini/API/dashboard 直接读';


-- ============================================================
-- 3.2 24h 市场异动快照 (每 5 分钟更新)
-- 涨幅榜、跌幅榜、资金费率极端、成交量异动
-- ============================================================
CREATE TABLE IF NOT EXISTS market_movers_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    category        VARCHAR(20)  NOT NULL COMMENT 'gainers/losers/funding_high/funding_low/volume_spike',
    symbol          VARCHAR(32)  NOT NULL,
    value           DECIMAL(20,8) DEFAULT NULL COMMENT '涨跌幅%/费率%/成交额',
    rank_no         INT          DEFAULT 0,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_category (category, rank_no),
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='24h 市场异动 — 每 5min 更新, 替代 kline_data 复杂 JOIN';


-- ============================================================
-- 3.3 持仓统计快照 (每 30 分钟更新)
-- 各 source 的盈亏统计、持仓数、胜率
-- ============================================================
CREATE TABLE IF NOT EXISTS position_stats_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    source          VARCHAR(32)  NOT NULL COMMENT 'gemini_explore/gemini_predict/PREDICTOR/all',
    account_id      INT          NOT NULL DEFAULT 2,

    -- 总览
    open_count      INT          DEFAULT 0 COMMENT '当前持仓数',
    closed_24h      INT          DEFAULT 0,
    closed_7d       INT          DEFAULT 0,
    closed_30d      INT          DEFAULT 0,

    -- 盈亏
    pnl_24h         DECIMAL(20,4) DEFAULT 0,
    pnl_7d          DECIMAL(20,4) DEFAULT 0,
    pnl_30d         DECIMAL(20,4) DEFAULT 0,
    total_pnl       DECIMAL(20,4) DEFAULT 0,

    -- 胜率
    wins_30d        INT          DEFAULT 0,
    losses_30d      INT          DEFAULT 0,
    win_rate_30d    DECIMAL(5,2) DEFAULT 0,

    -- 多空分布
    long_count      INT          DEFAULT 0,
    short_count     INT          DEFAULT 0,
    long_pnl        DECIMAL(20,4) DEFAULT 0,
    short_pnl       DECIMAL(20,4) DEFAULT 0,

    -- 元数据
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_source_account (source, account_id),
    INDEX idx_updated (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='持仓统计快照 — 每 30min 更新, 替代重复聚合查询';


-- ============================================================
-- 3.4 候选交易对池快照 (每 6 分钟更新)
-- 预先算好 Gemini explore/predict 需要的候选池
-- ============================================================
CREATE TABLE IF NOT EXISTS candidate_pool_snapshot (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    symbol          VARCHAR(32)  NOT NULL,
    exchange        VARCHAR(20)  NOT NULL DEFAULT 'binance_futures',

    -- 24h 行情
    current_price   DECIMAL(20,8) DEFAULT NULL,
    change_24h      DECIMAL(10,2) DEFAULT NULL,
    quote_volume_24h DECIMAL(20,2) DEFAULT NULL,

    -- 资金费率
    funding_rate    DECIMAL(10,6) DEFAULT NULL,

    -- 技术指标 (预先算好)
    rsi_14          DECIMAL(8,2)  DEFAULT NULL,
    ema_9           DECIMAL(20,8) DEFAULT NULL,
    ema_21          DECIMAL(20,8) DEFAULT NULL,

    -- K 线当前 bars 的 JSON 摘要 (供 Gemini prompt 用)
    kline_1h_json   MEDIUMTEXT   DEFAULT NULL COMMENT '最近 12 根 1h K 线 JSON',
    kline_15m_json  MEDIUMTEXT   DEFAULT NULL COMMENT '最近 8 根 15m K 线 JSON',
    kline_1d_json   MEDIUMTEXT   DEFAULT NULL COMMENT '最近 7 根 1d K 线 JSON',

    -- 1h K 线叙事 (自然语言描述)
    narrative_1h    TEXT         DEFAULT NULL,
    narrative_15m   TEXT         DEFAULT NULL,
    narrative_1d    TEXT         DEFAULT NULL,

    -- 距 7d 高/低距离
    above_7d_low_pct  DECIMAL(10,2) DEFAULT NULL,
    below_7d_high_pct DECIMAL(10,2) DEFAULT NULL,

    -- 元数据
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE INDEX idx_symbol (symbol, exchange),
    INDEX idx_change_24h (change_24h),
    INDEX idx_volume (quote_volume_24h),
    INDEX idx_funding (funding_rate)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='候选交易对池 — 每 6min 更新, 替代 kline_data 四层子查询';


-- ============================================================
-- 3.5 系统设置缓存 (写时更新, 无需定时)
-- 高频读取的配置项，减少 system_settings 的 50+ 查询
-- ============================================================
CREATE TABLE IF NOT EXISTS settings_cache (
    setting_key     VARCHAR(64)  PRIMARY KEY,
    setting_value   VARCHAR(255) NOT NULL DEFAULT '',
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='系统设置缓存 — 写 system_settings 时同步更新, 应用层读这里';
