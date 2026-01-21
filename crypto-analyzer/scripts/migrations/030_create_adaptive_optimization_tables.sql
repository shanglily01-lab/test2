-- 创建超级大脑自适应优化相关表
-- 基于DATABASE_SCHEMA_REFERENCE.md中的表结构

-- symbol_risk_params - 每个交易对的风险参数
CREATE TABLE IF NOT EXISTS symbol_risk_params (
    id INT(11) NOT NULL AUTO_INCREMENT,
    symbol VARCHAR(20) NOT NULL,
    long_take_profit_pct DECIMAL(6,4) DEFAULT 0.0500,
    long_stop_loss_pct DECIMAL(6,4) DEFAULT 0.0200,
    short_take_profit_pct DECIMAL(6,4) DEFAULT 0.0500,
    short_stop_loss_pct DECIMAL(6,4) DEFAULT 0.0200,
    position_multiplier DECIMAL(5,2) DEFAULT 1.00,
    total_trades INT(11) DEFAULT 0,
    win_rate DECIMAL(5,4) DEFAULT 0.0000,
    avg_pnl DECIMAL(10,2) DEFAULT 0.00,
    total_pnl DECIMAL(15,2) DEFAULT 0.00,
    sharpe_ratio DECIMAL(6,3) DEFAULT 0.000,
    avg_volatility DECIMAL(6,4) DEFAULT 0.0000,
    max_drawdown DECIMAL(6,4) DEFAULT 0.0000,
    last_optimized TIMESTAMP NULL,
    optimization_count INT(11) DEFAULT 0,
    is_active TINYINT(1) DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY idx_symbol (symbol),
    KEY idx_win_rate (win_rate),
    KEY idx_last_optimized (last_optimized)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易对风险参数配置';

-- signal_position_multipliers - 信号仓位倍数
CREATE TABLE IF NOT EXISTS signal_position_multipliers (
    id INT(11) NOT NULL AUTO_INCREMENT,
    component_name VARCHAR(50) NOT NULL,
    position_side VARCHAR(10) NOT NULL,
    position_multiplier DECIMAL(5,2) DEFAULT 1.00,
    total_trades INT(11) DEFAULT 0,
    win_rate DECIMAL(5,4) DEFAULT 0.0000,
    avg_pnl DECIMAL(10,2) DEFAULT 0.00,
    total_pnl DECIMAL(15,2) DEFAULT 0.00,
    last_analyzed TIMESTAMP NULL,
    adjustment_count INT(11) DEFAULT 0,
    is_active TINYINT(1) DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_component (component_name),
    KEY idx_win_rate (win_rate)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号组件仓位倍数配置';
