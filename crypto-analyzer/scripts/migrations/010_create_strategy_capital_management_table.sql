-- ========================================
-- 创建策略资金管理表
-- 用途：记录策略执行过程中的资金变化（冻结、解冻、盈亏、手续费等）
-- 作者：Auto
-- 日期：2025-11-27
-- ========================================

USE `binance-data`;

-- ========================================
-- 策略资金管理表
-- ========================================
CREATE TABLE IF NOT EXISTS `strategy_capital_management` (
    `id` BIGINT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
    
    -- 策略信息
    `strategy_id` BIGINT COMMENT '策略ID（NULL表示系统操作，如充值、提现）',
    `strategy_name` VARCHAR(100) COMMENT '策略名称',
    `account_id` INT NOT NULL COMMENT '账户ID',
    
    -- 交易信息
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对，如 BTC/USDT',
    `trade_record_id` BIGINT COMMENT '关联的交易记录ID（strategy_trade_records表）',
    `position_id` INT COMMENT '关联的持仓ID',
    `order_id` VARCHAR(50) COMMENT '关联的订单ID',
    
    -- 资金变化类型
    `change_type` VARCHAR(20) NOT NULL COMMENT '资金变化类型: FROZEN(冻结保证金), UNFROZEN(解冻保证金), REALIZED_PNL(已实现盈亏), FEE(手续费), DEPOSIT(充值), WITHDRAW(提现)',
    `action` VARCHAR(20) COMMENT '交易动作: BUY(买入/开仓), SELL(平仓), CLOSE(平仓)',
    `direction` VARCHAR(10) COMMENT '方向: long(做多), short(做空)',
    
    -- 金额信息
    `amount_change` DECIMAL(18, 8) NOT NULL COMMENT '金额变化（正数表示增加，负数表示减少）',
    `balance_before` DECIMAL(18, 8) COMMENT '变化前余额',
    `balance_after` DECIMAL(18, 8) COMMENT '变化后余额',
    `frozen_before` DECIMAL(18, 8) COMMENT '变化前冻结金额',
    `frozen_after` DECIMAL(18, 8) COMMENT '变化后冻结金额',
    `available_before` DECIMAL(18, 8) COMMENT '变化前可用余额',
    `available_after` DECIMAL(18, 8) COMMENT '变化后可用余额',
    
    -- 交易详情（用于记录开仓/平仓的详细信息）
    `entry_price` DECIMAL(18, 8) COMMENT '开仓价格',
    `exit_price` DECIMAL(18, 8) COMMENT '平仓价格',
    `quantity` DECIMAL(18, 8) COMMENT '数量',
    `leverage` INT COMMENT '杠杆倍数',
    `margin` DECIMAL(18, 8) COMMENT '保证金',
    `realized_pnl` DECIMAL(18, 8) COMMENT '已实现盈亏',
    `fee` DECIMAL(18, 8) COMMENT '手续费',
    
    -- 备注信息
    `reason` VARCHAR(200) COMMENT '资金变化原因，如：开仓冻结保证金、平仓解冻保证金、止损平仓等',
    `description` TEXT COMMENT '详细描述',
    
    -- 时间信息
    `change_time` DATETIME NOT NULL COMMENT '资金变化时间',
    `created_at` DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '记录创建时间',
    
    -- 索引
    INDEX `idx_strategy_id` (`strategy_id`),
    INDEX `idx_account_id` (`account_id`),
    INDEX `idx_symbol` (`symbol`),
    INDEX `idx_change_type` (`change_type`),
    INDEX `idx_change_time` (`change_time`),
    INDEX `idx_trade_record_id` (`trade_record_id`),
    INDEX `idx_position_id` (`position_id`),
    INDEX `idx_strategy_symbol_time` (`strategy_id`, `symbol`, `change_time`),
    INDEX `idx_account_change_time` (`account_id`, `change_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='策略资金管理表 - 记录策略资金变化';

