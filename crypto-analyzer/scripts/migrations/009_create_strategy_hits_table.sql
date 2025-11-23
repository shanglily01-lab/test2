-- 创建策略命中记录表
-- 用于实时记录策略信号命中情况，无论是否执行交易

CREATE TABLE IF NOT EXISTS `strategy_hits` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `strategy_id` BIGINT NOT NULL COMMENT '策略ID',
    `strategy_name` VARCHAR(100) NOT NULL COMMENT '策略名称',
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对',
    `account_id` INT NOT NULL COMMENT '账户ID',
    
    -- 信号信息
    `signal_type` VARCHAR(20) NOT NULL COMMENT '信号类型: BUY_LONG(做多买入), BUY_SHORT(做空买入), SELL(卖出)',
    `signal_source` VARCHAR(50) NOT NULL COMMENT '信号来源: ema_9_26, ma_ema5, ma_ema10等',
    `signal_timeframe` VARCHAR(10) NOT NULL COMMENT '信号时间周期: 5m, 15m, 1h',
    `signal_timestamp` DATETIME NOT NULL COMMENT '信号触发时间',
    
    -- 技术指标值
    `ema_short` DECIMAL(20, 8) COMMENT '短期EMA值',
    `ema_long` DECIMAL(20, 8) COMMENT '长期EMA值',
    `ma10` DECIMAL(20, 8) COMMENT 'MA10值',
    `ema10` DECIMAL(20, 8) COMMENT 'EMA10值',
    `ma5` DECIMAL(20, 8) COMMENT 'MA5值',
    `ema5` DECIMAL(20, 8) COMMENT 'EMA5值',
    `current_price` DECIMAL(20, 8) NOT NULL COMMENT '当前价格',
    
    -- 信号强度
    `ema_cross_strength_pct` DECIMAL(10, 4) COMMENT 'EMA交叉强度百分比',
    `ma10_ema10_strength_pct` DECIMAL(10, 4) COMMENT 'MA10/EMA10强度百分比',
    
    -- 成交量信息
    `volume_ratio` DECIMAL(10, 2) COMMENT '成交量放大倍数',
    `volume_condition_met` BOOLEAN COMMENT '成交量条件是否满足',
    
    -- 过滤条件检查结果
    `ma10_ema10_trend_ok` BOOLEAN COMMENT 'MA10/EMA10趋势过滤是否通过',
    `trend_confirm_ok` BOOLEAN COMMENT '趋势持续性检查是否通过',
    `signal_strength_ok` BOOLEAN COMMENT '信号强度过滤是否通过',
    
    -- 执行结果
    `executed` BOOLEAN DEFAULT FALSE COMMENT '是否已执行交易',
    `execution_result` VARCHAR(20) COMMENT '执行结果: SUCCESS, FAILED, SKIPPED',
    `execution_reason` VARCHAR(200) COMMENT '执行或跳过的原因',
    `position_id` INT COMMENT '如果执行成功，关联的持仓ID',
    `order_id` VARCHAR(100) COMMENT '如果执行成功，关联的订单ID',
    
    -- 其他信息
    `direction` VARCHAR(10) COMMENT '交易方向: long, short',
    `leverage` INT COMMENT '杠杆倍数',
    `position_size_pct` DECIMAL(5, 2) COMMENT '仓位大小百分比',
    
    -- 元数据
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    
    INDEX `idx_strategy_id` (`strategy_id`),
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_signal_timestamp` (`signal_timestamp`),
    INDEX `idx_signal_type` (`signal_type`),
    INDEX `idx_executed` (`executed`),
    INDEX `idx_created_at` (`created_at`),
    INDEX `idx_strategy_symbol_time` (`strategy_id`, `symbol`, `signal_timestamp` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略命中记录表 - 实时记录所有策略信号命中情况';

