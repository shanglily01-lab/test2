-- 信号黑名单表
-- 用于禁用表现差的信号类型和方向组合

CREATE TABLE IF NOT EXISTS signal_blacklist (
    id INT AUTO_INCREMENT PRIMARY KEY,
    signal_type VARCHAR(50) NOT NULL COMMENT '信号类型（如SMART_BRAIN_15）',
    position_side VARCHAR(10) NOT NULL COMMENT '方向（LONG/SHORT）',
    reason VARCHAR(255) COMMENT '禁用原因',
    total_loss DECIMAL(15,2) COMMENT '历史总亏损',
    win_rate DECIMAL(5,4) COMMENT '胜率',
    order_count INT COMMENT '订单数量',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    is_active BOOLEAN DEFAULT TRUE COMMENT '是否激活',
    notes TEXT COMMENT '备注',
    UNIQUE KEY unique_signal_side (signal_type, position_side),
    INDEX idx_active (is_active),
    INDEX idx_signal_type (signal_type),
    INDEX idx_side (position_side)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号黑名单表';

-- 示例：禁用表现差的信号
-- INSERT INTO signal_blacklist (signal_type, position_side, reason, total_loss, win_rate, order_count, is_active)
-- VALUES
-- ('SMART_BRAIN_15', 'LONG', '胜率8.3%, 总亏损$-1026.91', 1026.91, 0.083, 24, TRUE),
-- ('SMART_BRAIN_20', 'LONG', '胜率12.5%, 总亏损$-523.45', 523.45, 0.125, 32, TRUE);
