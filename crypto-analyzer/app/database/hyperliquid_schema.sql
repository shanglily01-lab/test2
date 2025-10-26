-- Hyperliquid 聪明钱数据库表结构

-- 1. 交易者基本信息表
CREATE TABLE IF NOT EXISTS hyperliquid_traders (
    id INT AUTO_INCREMENT PRIMARY KEY,
    address VARCHAR(66) NOT NULL UNIQUE COMMENT '钱包地址',
    display_name VARCHAR(100) DEFAULT NULL COMMENT '显示名称',
    first_seen DATETIME NOT NULL COMMENT '首次发现时间',
    last_updated DATETIME NOT NULL COMMENT '最后更新时间',
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否活跃',
    notes TEXT COMMENT '备注',
    INDEX idx_address (address),
    INDEX idx_last_updated (last_updated)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 交易者基本信息';

-- 2. 交易者周度表现记录表
CREATE TABLE IF NOT EXISTS hyperliquid_weekly_performance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',
    week_start DATE NOT NULL COMMENT '周开始日期',
    week_end DATE NOT NULL COMMENT '周结束日期',

    -- 周度数据
    pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '本周盈亏(USD)',
    roi DECIMAL(10, 4) DEFAULT 0 COMMENT '本周ROI(%)',
    volume DECIMAL(20, 2) DEFAULT 0 COMMENT '本周交易量(USD)',
    account_value DECIMAL(20, 2) DEFAULT 0 COMMENT '账户价值(USD)',

    -- 排名数据
    pnl_rank INT DEFAULT NULL COMMENT 'PnL排名',
    roi_rank INT DEFAULT NULL COMMENT 'ROI排名',

    -- 元数据
    recorded_at DATETIME NOT NULL COMMENT '记录时间',
    data_source VARCHAR(50) DEFAULT 'leaderboard' COMMENT '数据来源',

    UNIQUE KEY uk_trader_week (trader_id, week_start),
    INDEX idx_address (address),
    INDEX idx_week (week_start),
    INDEX idx_pnl (pnl),
    INDEX idx_roi (roi),
    INDEX idx_pnl_rank (pnl_rank),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 交易者周度表现';

-- 3. 交易者月度表现记录表
CREATE TABLE IF NOT EXISTS hyperliquid_monthly_performance (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',
    month_start DATE NOT NULL COMMENT '月开始日期',
    month_end DATE NOT NULL COMMENT '月结束日期',

    -- 月度数据
    pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '本月盈亏(USD)',
    roi DECIMAL(10, 4) DEFAULT 0 COMMENT '本月ROI(%)',
    volume DECIMAL(20, 2) DEFAULT 0 COMMENT '本月交易量(USD)',
    account_value DECIMAL(20, 2) DEFAULT 0 COMMENT '账户价值(USD)',

    -- 排名数据
    pnl_rank INT DEFAULT NULL COMMENT 'PnL排名',
    roi_rank INT DEFAULT NULL COMMENT 'ROI排名',

    -- 元数据
    recorded_at DATETIME NOT NULL COMMENT '记录时间',
    data_source VARCHAR(50) DEFAULT 'leaderboard' COMMENT '数据来源',

    UNIQUE KEY uk_trader_month (trader_id, month_start),
    INDEX idx_address (address),
    INDEX idx_month (month_start),
    INDEX idx_pnl (pnl),
    INDEX idx_roi (roi),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 交易者月度表现';

-- 4. 交易者历史表现快照表
CREATE TABLE IF NOT EXISTS hyperliquid_performance_snapshots (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',
    snapshot_date DATE NOT NULL COMMENT '快照日期',

    -- 各周期数据
    day_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '日盈亏',
    day_roi DECIMAL(10, 4) DEFAULT 0 COMMENT '日ROI',
    day_volume DECIMAL(20, 2) DEFAULT 0 COMMENT '日交易量',

    week_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '周盈亏',
    week_roi DECIMAL(10, 4) DEFAULT 0 COMMENT '周ROI',
    week_volume DECIMAL(20, 2) DEFAULT 0 COMMENT '周交易量',

    month_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '月盈亏',
    month_roi DECIMAL(10, 4) DEFAULT 0 COMMENT '月ROI',
    month_volume DECIMAL(20, 2) DEFAULT 0 COMMENT '月交易量',

    alltime_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '历史总盈亏',
    alltime_roi DECIMAL(10, 4) DEFAULT 0 COMMENT '历史总ROI',
    alltime_volume DECIMAL(20, 2) DEFAULT 0 COMMENT '历史总交易量',

    account_value DECIMAL(20, 2) DEFAULT 0 COMMENT '账户价值',

    -- 元数据
    recorded_at DATETIME NOT NULL COMMENT '记录时间',

    UNIQUE KEY uk_trader_snapshot (trader_id, snapshot_date),
    INDEX idx_address (address),
    INDEX idx_snapshot_date (snapshot_date),
    INDEX idx_week_pnl (week_pnl),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 交易者表现快照(每日)';

-- 5. 排行榜历史记录表
CREATE TABLE IF NOT EXISTS hyperliquid_leaderboard_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    record_date DATE NOT NULL COMMENT '记录日期',
    period_type VARCHAR(20) NOT NULL COMMENT '周期类型: day/week/month/allTime',
    total_traders INT DEFAULT 0 COMMENT '总交易者数',
    total_positive_pnl INT DEFAULT 0 COMMENT '盈利交易者数',
    total_negative_pnl INT DEFAULT 0 COMMENT '亏损交易者数',
    avg_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '平均PnL',
    median_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '中位数PnL',
    top10_avg_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT 'Top10平均PnL',
    recorded_at DATETIME NOT NULL COMMENT '记录时间',

    UNIQUE KEY uk_date_period (record_date, period_type),
    INDEX idx_record_date (record_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 排行榜统计历史';

-- 创建视图：周度排行榜
CREATE OR REPLACE VIEW v_hyperliquid_weekly_leaderboard AS
SELECT
    wp.week_start,
    wp.week_end,
    t.address,
    t.display_name,
    wp.pnl,
    wp.roi,
    wp.volume,
    wp.account_value,
    wp.pnl_rank,
    wp.roi_rank,
    wp.recorded_at
FROM hyperliquid_weekly_performance wp
JOIN hyperliquid_traders t ON wp.trader_id = t.id
WHERE wp.week_start = (SELECT MAX(week_start) FROM hyperliquid_weekly_performance)
ORDER BY wp.pnl DESC;

-- 创建视图：交易者历史表现
CREATE OR REPLACE VIEW v_hyperliquid_trader_history AS
SELECT
    t.address,
    t.display_name,
    ps.snapshot_date,
    ps.week_pnl,
    ps.week_roi,
    ps.week_volume,
    ps.month_pnl,
    ps.month_roi,
    ps.alltime_pnl,
    ps.alltime_roi,
    ps.account_value
FROM hyperliquid_performance_snapshots ps
JOIN hyperliquid_traders t ON ps.trader_id = t.id
ORDER BY t.address, ps.snapshot_date DESC;

-- 6. 聪明钱包监控表
CREATE TABLE IF NOT EXISTS hyperliquid_monitored_wallets (
    id INT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',
    label VARCHAR(100) DEFAULT NULL COMMENT '标签',
    monitor_type VARCHAR(50) DEFAULT 'auto' COMMENT '监控类型: auto/manual',
    is_monitoring BOOLEAN DEFAULT TRUE COMMENT '是否监控中',

    -- 发现时的统计
    discovered_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '发现时PnL',
    discovered_roi DECIMAL(10, 4) DEFAULT 0 COMMENT '发现时ROI',
    discovered_account_value DECIMAL(20, 2) DEFAULT 0 COMMENT '发现时账户价值',
    discovered_at DATETIME NOT NULL COMMENT '发现时间',

    -- 当前状态
    last_check_at DATETIME DEFAULT NULL COMMENT '最后检查时间',
    last_trade_at DATETIME DEFAULT NULL COMMENT '最后交易时间',
    check_count INT DEFAULT 0 COMMENT '检查次数',

    -- 元数据
    created_at DATETIME NOT NULL COMMENT '创建时间',
    updated_at DATETIME NOT NULL COMMENT '更新时间',
    notes TEXT COMMENT '备注',

    UNIQUE KEY uk_trader_id (trader_id),
    INDEX idx_address (address),
    INDEX idx_is_monitoring (is_monitoring),
    INDEX idx_last_check (last_check_at),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 监控钱包列表';

-- 7. 钱包交易记录表
CREATE TABLE IF NOT EXISTS hyperliquid_wallet_trades (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',

    -- 交易信息
    coin VARCHAR(20) NOT NULL COMMENT '币种',
    side VARCHAR(10) NOT NULL COMMENT '方向: LONG/SHORT',
    action VARCHAR(20) NOT NULL COMMENT '操作类型: OPEN/CLOSE/ADD/REDUCE',
    price DECIMAL(20, 8) NOT NULL COMMENT '成交价格',
    size DECIMAL(20, 8) NOT NULL COMMENT '成交数量',
    notional_usd DECIMAL(20, 2) NOT NULL COMMENT '成交金额(USD)',

    -- PnL信息
    closed_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '已实现盈亏',

    -- 时间
    trade_time DATETIME NOT NULL COMMENT '交易时间',
    detected_at DATETIME NOT NULL COMMENT '检测时间',

    -- 原始数据
    raw_data JSON COMMENT '原始交易数据',

    INDEX idx_trader_id (trader_id),
    INDEX idx_address (address),
    INDEX idx_coin (coin),
    INDEX idx_trade_time (trade_time),
    INDEX idx_notional (notional_usd),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 钱包交易记录';

-- 8. 钱包持仓快照表
CREATE TABLE IF NOT EXISTS hyperliquid_wallet_positions (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',
    snapshot_time DATETIME NOT NULL COMMENT '快照时间',

    -- 持仓信息
    coin VARCHAR(20) NOT NULL COMMENT '币种',
    side VARCHAR(10) NOT NULL COMMENT '方向: LONG/SHORT',
    size DECIMAL(20, 8) NOT NULL COMMENT '持仓数量',
    entry_price DECIMAL(20, 8) NOT NULL COMMENT '入场价格',
    mark_price DECIMAL(20, 8) DEFAULT NULL COMMENT '标记价格',
    notional_usd DECIMAL(20, 2) NOT NULL COMMENT '仓位价值(USD)',

    -- PnL信息
    unrealized_pnl DECIMAL(20, 2) DEFAULT 0 COMMENT '未实现盈亏',
    leverage DECIMAL(10, 2) DEFAULT 1 COMMENT '杠杆倍数',

    -- 原始数据
    raw_data JSON COMMENT '原始持仓数据',

    UNIQUE KEY uk_trader_coin_time (trader_id, coin, snapshot_time),
    INDEX idx_address (address),
    INDEX idx_coin (coin),
    INDEX idx_snapshot_time (snapshot_time),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 钱包持仓快照';

-- 9. 钱包资金变动表
CREATE TABLE IF NOT EXISTS hyperliquid_wallet_fund_changes (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    trader_id INT NOT NULL COMMENT '交易者ID',
    address VARCHAR(66) NOT NULL COMMENT '钱包地址',

    -- 变动信息
    change_type VARCHAR(50) NOT NULL COMMENT '变动类型: DEPOSIT/WITHDRAW/PNL/FUNDING',
    amount DECIMAL(20, 2) NOT NULL COMMENT '变动金额(USD)',
    balance_before DECIMAL(20, 2) DEFAULT NULL COMMENT '变动前余额',
    balance_after DECIMAL(20, 2) DEFAULT NULL COMMENT '变动后余额',

    -- 时间
    change_time DATETIME NOT NULL COMMENT '变动时间',
    detected_at DATETIME NOT NULL COMMENT '检测时间',

    -- 原始数据
    raw_data JSON COMMENT '原始数据',
    notes TEXT COMMENT '备注',

    INDEX idx_trader_id (trader_id),
    INDEX idx_address (address),
    INDEX idx_change_type (change_type),
    INDEX idx_change_time (change_time),
    FOREIGN KEY (trader_id) REFERENCES hyperliquid_traders(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Hyperliquid 钱包资金变动记录';

-- 创建视图：活跃监控钱包
CREATE OR REPLACE VIEW v_hyperliquid_active_monitors AS
SELECT
    mw.id,
    mw.address,
    mw.label,
    t.display_name,
    mw.monitor_type,
    mw.discovered_pnl,
    mw.discovered_roi,
    mw.discovered_account_value,
    mw.discovered_at,
    mw.last_check_at,
    mw.last_trade_at,
    mw.check_count,
    TIMESTAMPDIFF(HOUR, mw.last_check_at, NOW()) as hours_since_check
FROM hyperliquid_monitored_wallets mw
JOIN hyperliquid_traders t ON mw.trader_id = t.id
WHERE mw.is_monitoring = TRUE
ORDER BY mw.last_check_at ASC;

-- 创建视图：钱包最新持仓
CREATE OR REPLACE VIEW v_hyperliquid_latest_positions AS
SELECT
    wp.trader_id,
    wp.address,
    t.display_name,
    mw.label,
    wp.coin,
    wp.side,
    wp.size,
    wp.entry_price,
    wp.mark_price,
    wp.notional_usd,
    wp.unrealized_pnl,
    wp.leverage,
    wp.snapshot_time
FROM hyperliquid_wallet_positions wp
JOIN hyperliquid_traders t ON wp.trader_id = t.id
LEFT JOIN hyperliquid_monitored_wallets mw ON wp.trader_id = mw.trader_id
WHERE (wp.trader_id, wp.coin, wp.snapshot_time) IN (
    SELECT trader_id, coin, MAX(snapshot_time)
    FROM hyperliquid_wallet_positions
    GROUP BY trader_id, coin
)
ORDER BY wp.snapshot_time DESC;
