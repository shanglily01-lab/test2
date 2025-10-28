-- 创建EMA信号表
-- 用于存储EMA金叉/死叉信号，供Dashboard显示

CREATE TABLE IF NOT EXISTS `ema_signals` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
    `timeframe` VARCHAR(10) NOT NULL COMMENT '时间周期 (5m, 15m, 1h)',
    `signal_type` VARCHAR(10) NOT NULL COMMENT '信号类型 (BUY, SELL)',
    `signal_strength` VARCHAR(20) NOT NULL COMMENT '信号强度 (weak, medium, strong)',
    `timestamp` DATETIME NOT NULL COMMENT '信号时间',
    `price` DECIMAL(20, 8) NOT NULL COMMENT '当前价格',
    `short_ema` DECIMAL(20, 8) NOT NULL COMMENT '短期EMA值',
    `long_ema` DECIMAL(20, 8) NOT NULL COMMENT '长期EMA值',
    `ema_config` VARCHAR(50) NOT NULL COMMENT 'EMA配置 (如 EMA9/EMA21)',
    `volume_ratio` DECIMAL(10, 2) NOT NULL COMMENT '成交量放大倍数',
    `price_change_pct` DECIMAL(10, 4) NOT NULL COMMENT '价格变动百分比',
    `ema_distance_pct` DECIMAL(10, 4) NOT NULL COMMENT 'EMA距离百分比',
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_timestamp` (`timestamp`),
    INDEX `idx_signal_type` (`signal_type`),
    INDEX `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='EMA信号记录表';

-- 创建索引优化查询
ALTER TABLE `ema_signals` ADD INDEX `idx_symbol_timestamp` (`symbol`, `timestamp` DESC);
ALTER TABLE `ema_signals` ADD INDEX `idx_signal_type_timestamp` (`signal_type`, `timestamp` DESC);
