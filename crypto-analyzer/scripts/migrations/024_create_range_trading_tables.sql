-- ============================================================================
-- 震荡市交易功能相关表
-- 创建日期: 2026-02-03
-- 描述: 支持震荡市交易模式切换和区间识别
-- ============================================================================

-- 1. 交易模式配置表
CREATE TABLE IF NOT EXISTS trading_mode_config (
    id INT PRIMARY KEY AUTO_INCREMENT,
    account_id INT NOT NULL COMMENT '账户ID (2=U本位, 3=币本位)',
    trading_type VARCHAR(50) NOT NULL COMMENT '交易类型 (usdt_futures/coin_futures)',
    mode_type ENUM('trend', 'range', 'auto') DEFAULT 'trend' COMMENT '交易模式: trend=趋势, range=震荡, auto=自动',
    is_active BOOLEAN DEFAULT FALSE COMMENT '是否启用',

    -- 震荡市参数
    range_min_score INT DEFAULT 50 COMMENT '最低信号分数',
    range_position_size DECIMAL(5,2) DEFAULT 3.00 COMMENT '单笔仓位百分比',
    range_max_positions INT DEFAULT 8 COMMENT '最大持仓数',
    range_take_profit DECIMAL(5,2) DEFAULT 2.50 COMMENT '止盈百分比',
    range_stop_loss DECIMAL(5,2) DEFAULT 2.00 COMMENT '止损百分比',
    range_max_hold_hours INT DEFAULT 4 COMMENT '最大持仓小时数',

    -- 模式切换控制
    auto_switch_enabled BOOLEAN DEFAULT FALSE COMMENT '是否允许自动切换',
    last_switch_time DATETIME COMMENT '上次切换时间',
    switch_cooldown_minutes INT DEFAULT 120 COMMENT '切换冷却时间(分钟)',

    -- 审计字段
    updated_by VARCHAR(100) DEFAULT 'system',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uk_account_type (account_id, trading_type),
    INDEX idx_mode_type (mode_type),
    INDEX idx_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易模式配置表';

-- 2. 震荡区间识别记录表
CREATE TABLE IF NOT EXISTS range_market_zones (
    id INT PRIMARY KEY AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    timeframe VARCHAR(10) DEFAULT '1h' COMMENT 'K线周期',

    -- 区间信息
    support_price DECIMAL(20,8) NOT NULL COMMENT '支撑位价格',
    resistance_price DECIMAL(20,8) NOT NULL COMMENT '阻力位价格',
    range_pct DECIMAL(5,2) COMMENT '区间幅度百分比',

    -- 可信度指标
    touch_count INT DEFAULT 1 COMMENT '触及次数',
    bounce_count INT DEFAULT 0 COMMENT '反弹次数',
    confidence_score DECIMAL(5,2) DEFAULT 50.0 COMMENT '可信度分数 (0-100)',

    -- 状态管理
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否有效',
    breakout_direction ENUM('up', 'down', 'none') DEFAULT 'none' COMMENT '突破方向',

    -- 时间管理
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '检测时间',
    last_touch_at TIMESTAMP COMMENT '最后触及时间',
    expires_at TIMESTAMP COMMENT '过期时间',
    invalidated_at TIMESTAMP COMMENT '失效时间',

    INDEX idx_symbol (symbol),
    INDEX idx_active (is_active),
    INDEX idx_confidence (confidence_score),
    INDEX idx_detected_at (detected_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='震荡区间识别记录';

-- 3. 震荡市交易记录表
CREATE TABLE IF NOT EXISTS range_trading_history (
    id INT PRIMARY KEY AUTO_INCREMENT,
    position_id INT COMMENT '关联的持仓ID',
    symbol VARCHAR(20) NOT NULL,
    zone_id INT COMMENT '关联的区间ID',

    -- 交易信息
    entry_reason ENUM('support_bounce', 'resistance_bounce', 'bollinger_lower', 'bollinger_upper', 'grid') COMMENT '开仓原因',
    entry_price DECIMAL(20,8),
    target_price DECIMAL(20,8) COMMENT '目标价格',
    actual_exit_price DECIMAL(20,8) COMMENT '实际平仓价格',

    -- 结果统计
    pnl DECIMAL(20,8) COMMENT '盈亏',
    hold_time_minutes INT COMMENT '持仓时长(分钟)',
    exit_reason ENUM('take_profit', 'stop_loss', 'timeout', 'manual', 'mode_switch') COMMENT '平仓原因',

    -- 审计
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP,

    INDEX idx_position (position_id),
    INDEX idx_symbol (symbol),
    INDEX idx_zone (zone_id),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='震荡市交易历史记录';

-- 4. 模式切换日志表
CREATE TABLE IF NOT EXISTS trading_mode_switch_log (
    id INT PRIMARY KEY AUTO_INCREMENT,
    account_id INT NOT NULL,
    trading_type VARCHAR(50) NOT NULL,

    -- 切换信息
    from_mode ENUM('trend', 'range', 'auto'),
    to_mode ENUM('trend', 'range', 'auto'),
    switch_trigger ENUM('manual', 'auto', 'schedule') DEFAULT 'manual',

    -- 市场状态
    big4_signal VARCHAR(20) COMMENT 'Big4信号',
    big4_strength DECIMAL(5,2) COMMENT 'Big4强度',
    market_volatility DECIMAL(10,4) COMMENT '市场波动率',

    -- 原因和结果
    reason TEXT COMMENT '切换原因',
    switched_by VARCHAR(100) DEFAULT 'system',
    switched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account (account_id),
    INDEX idx_switched_at (switched_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模式切换日志';

-- 插入默认配置
INSERT INTO trading_mode_config (account_id, trading_type, mode_type, is_active, updated_by) VALUES
(2, 'usdt_futures', 'trend', TRUE, 'system'),
(3, 'coin_futures', 'trend', TRUE, 'system')
ON DUPLICATE KEY UPDATE updated_at = CURRENT_TIMESTAMP;
