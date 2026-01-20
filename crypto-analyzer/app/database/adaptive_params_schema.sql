-- 自适应参数表 - 存储动态调整的交易参数
-- 解决Linux服务器上无法修改config.yaml的问题

CREATE TABLE IF NOT EXISTS adaptive_params (
    id INT AUTO_INCREMENT PRIMARY KEY,
    param_key VARCHAR(100) NOT NULL UNIQUE COMMENT '参数键名, 如 long_stop_loss_pct',
    param_value DECIMAL(10, 6) NOT NULL COMMENT '参数值',
    param_type VARCHAR(50) NOT NULL COMMENT '参数类型: long/short',
    description VARCHAR(255) COMMENT '参数描述',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    updated_by VARCHAR(100) DEFAULT 'adaptive_optimizer' COMMENT '更新来源',
    INDEX idx_param_key (param_key),
    INDEX idx_param_type (param_type),
    INDEX idx_updated_at (updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='自适应参数表';

-- 插入初始默认参数
INSERT INTO adaptive_params (param_key, param_value, param_type, description) VALUES
-- LONG参数
('long_stop_loss_pct', 0.03, 'long', 'LONG止损百分比'),
('long_take_profit_pct', 0.02, 'long', 'LONG止盈百分比'),
('long_min_holding_minutes', 60, 'long', 'LONG最小持仓时间(分钟)'),
('long_position_size_multiplier', 1.0, 'long', 'LONG仓位倍数'),

-- SHORT参数
('short_stop_loss_pct', 0.03, 'short', 'SHORT止损百分比'),
('short_take_profit_pct', 0.02, 'short', 'SHORT止盈百分比'),
('short_min_holding_minutes', 60, 'short', 'SHORT最小持仓时间(分钟)'),
('short_position_size_multiplier', 1.0, 'short', 'SHORT仓位倍数')

ON DUPLICATE KEY UPDATE
    param_value = VALUES(param_value),
    updated_at = CURRENT_TIMESTAMP;

-- 黑名单表 - 存储动态黑名单
CREATE TABLE IF NOT EXISTS trading_blacklist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    symbol VARCHAR(50) NOT NULL UNIQUE COMMENT '交易对',
    reason VARCHAR(255) COMMENT '加入黑名单的原因',
    total_loss DECIMAL(15, 2) COMMENT '历史总亏损',
    win_rate DECIMAL(5, 4) COMMENT '胜率',
    order_count INT COMMENT '订单数',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '加入时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否激活',
    INDEX idx_symbol (symbol),
    INDEX idx_is_active (is_active),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易黑名单';

-- 插入已有黑名单（从config.yaml迁移）
INSERT INTO trading_blacklist (symbol, reason, total_loss, is_active) VALUES
('IP/USDT', '亏损 $79.34 (2笔订单, 0%胜率)', 79.34, TRUE),
('VIRTUAL/USDT', '亏损 $35.65 (4笔订单, 0%胜率)', 35.65, TRUE),
('LDO/USDT', '亏损 $35.88 (5笔订单, 0%胜率)', 35.88, TRUE),
('ATOM/USDT', '亏损 $27.56 (5笔订单, 20%胜率)', 27.56, TRUE),
('ADA/USDT', '亏损 $22.87 (6笔订单, 0%胜率)', 22.87, TRUE)
ON DUPLICATE KEY UPDATE
    reason = VALUES(reason),
    total_loss = VALUES(total_loss),
    updated_at = CURRENT_TIMESTAMP;

-- 优化历史记录表
CREATE TABLE IF NOT EXISTS optimization_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    optimization_date DATE NOT NULL COMMENT '优化日期',
    analysis_hours INT DEFAULT 24 COMMENT '分析时间范围(小时)',
    blacklist_added INT DEFAULT 0 COMMENT '新增黑名单数量',
    params_updated INT DEFAULT 0 COMMENT '更新参数数量',
    high_severity_issues INT DEFAULT 0 COMMENT '高严重性问题数量',
    report_summary TEXT COMMENT '优化报告摘要(JSON)',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_optimization_date (optimization_date),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='优化历史记录';
