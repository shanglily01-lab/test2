-- ============================================================
-- 模拟现货交易系统数据库表结构
-- ============================================================

-- 1. 模拟账户表
CREATE TABLE IF NOT EXISTS paper_trading_accounts (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 账户信息
    account_name VARCHAR(100) NOT NULL COMMENT '账户名称',
    account_type VARCHAR(20) DEFAULT 'spot' COMMENT '账户类型: spot(现货), futures(期货)',

    -- 资金信息
    initial_balance DECIMAL(20, 2) NOT NULL DEFAULT 10000.00 COMMENT '初始资金(USDT)',
    current_balance DECIMAL(20, 2) NOT NULL DEFAULT 10000.00 COMMENT '当前可用余额(USDT)',
    frozen_balance DECIMAL(20, 2) DEFAULT 0.00 COMMENT '冻结资金',
    total_equity DECIMAL(20, 2) DEFAULT 10000.00 COMMENT '总权益(余额+持仓市值)',

    -- 统计数据
    total_profit_loss DECIMAL(20, 2) DEFAULT 0.00 COMMENT '总盈亏(USDT)',
    total_profit_loss_pct DECIMAL(10, 4) DEFAULT 0.00 COMMENT '总收益率(%)',
    realized_pnl DECIMAL(20, 2) DEFAULT 0.00 COMMENT '已实现盈亏',
    unrealized_pnl DECIMAL(20, 2) DEFAULT 0.00 COMMENT '未实现盈亏(持仓盈亏)',

    -- 交易统计
    total_trades INT DEFAULT 0 COMMENT '总交易次数',
    winning_trades INT DEFAULT 0 COMMENT '盈利交易次数',
    losing_trades INT DEFAULT 0 COMMENT '亏损交易次数',
    win_rate DECIMAL(5, 2) DEFAULT 0.00 COMMENT '胜率(%)',

    -- 最大回撤
    max_balance DECIMAL(20, 2) DEFAULT 10000.00 COMMENT '历史最高权益',
    max_drawdown DECIMAL(20, 2) DEFAULT 0.00 COMMENT '最大回撤金额',
    max_drawdown_pct DECIMAL(10, 4) DEFAULT 0.00 COMMENT '最大回撤率(%)',

    -- 策略配置
    strategy_name VARCHAR(100) COMMENT '使用的策略名称',
    auto_trading BOOLEAN DEFAULT FALSE COMMENT '是否启用自动交易',

    -- 风控参数
    max_position_size DECIMAL(5, 2) DEFAULT 20.00 COMMENT '单币种最大仓位(%)',
    stop_loss_pct DECIMAL(5, 2) DEFAULT 5.00 COMMENT '止损比例(%)',
    take_profit_pct DECIMAL(5, 2) DEFAULT 15.00 COMMENT '止盈比例(%)',
    max_daily_loss DECIMAL(20, 2) COMMENT '单日最大亏损',

    -- 状态
    status VARCHAR(20) DEFAULT 'active' COMMENT '账户状态: active, suspended, closed',
    is_default BOOLEAN DEFAULT FALSE COMMENT '是否为默认账户',

    -- 时间戳
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    INDEX idx_status (status),
    INDEX idx_account_name (account_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟交易账户表';

-- 2. 持仓表
CREATE TABLE IF NOT EXISTS paper_trading_positions (
    id INT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对(如 BTC/USDT)',

    -- 持仓信息
    position_side VARCHAR(10) DEFAULT 'LONG' COMMENT '持仓方向: LONG(多头), SHORT(空头)',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '持仓数量',
    available_quantity DECIMAL(18, 8) NOT NULL COMMENT '可用数量(未被挂单占用)',

    -- 成本信息
    avg_entry_price DECIMAL(18, 8) NOT NULL COMMENT '平均买入价格',
    total_cost DECIMAL(20, 2) NOT NULL COMMENT '总成本(USDT)',

    -- 当前市值
    current_price DECIMAL(18, 8) COMMENT '当前市场价格',
    market_value DECIMAL(20, 2) COMMENT '当前市值',

    -- 盈亏信息
    unrealized_pnl DECIMAL(20, 2) DEFAULT 0.00 COMMENT '未实现盈亏',
    unrealized_pnl_pct DECIMAL(10, 4) DEFAULT 0.00 COMMENT '未实现收益率(%)',

    -- 止盈止损
    stop_loss_price DECIMAL(18, 8) COMMENT '止损价格',
    take_profit_price DECIMAL(18, 8) COMMENT '止盈价格',

    -- 统计信息
    first_buy_time DATETIME COMMENT '首次买入时间',
    last_update_time DATETIME COMMENT '最后更新时间',
    holding_days INT DEFAULT 0 COMMENT '持有天数',

    -- 状态
    status VARCHAR(20) DEFAULT 'open' COMMENT '持仓状态: open(持仓中), closed(已平仓)',

    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_status (status),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟交易持仓表';

-- 3. 订单表
CREATE TABLE IF NOT EXISTS paper_trading_orders (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    order_id VARCHAR(50) UNIQUE NOT NULL COMMENT '订单ID',
    client_order_id VARCHAR(50) COMMENT '客户端订单ID',

    -- 交易对
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',

    -- 订单类型
    side VARCHAR(10) NOT NULL COMMENT '订单方向: BUY(买入), SELL(卖出)',
    order_type VARCHAR(20) NOT NULL DEFAULT 'MARKET' COMMENT '订单类型: MARKET(市价), LIMIT(限价), STOP_LOSS(止损), TAKE_PROFIT(止盈)',

    -- 价格和数量
    price DECIMAL(18, 8) COMMENT '委托价格(限价单)',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '委托数量',
    executed_quantity DECIMAL(18, 8) DEFAULT 0 COMMENT '已成交数量',

    -- 交易金额
    total_amount DECIMAL(20, 2) COMMENT '总金额(USDT)',
    executed_amount DECIMAL(20, 2) DEFAULT 0 COMMENT '已成交金额',

    -- 手续费
    fee DECIMAL(20, 8) DEFAULT 0 COMMENT '手续费',
    fee_asset VARCHAR(10) DEFAULT 'USDT' COMMENT '手续费币种',

    -- 订单状态
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' COMMENT '订单状态: PENDING(挂单中), FILLED(完全成交), PARTIALLY_FILLED(部分成交), CANCELLED(已撤销), REJECTED(已拒绝)',

    -- 成交信息
    avg_fill_price DECIMAL(18, 8) COMMENT '平均成交价格',
    fill_time DATETIME COMMENT '成交时间',

    -- 止盈止损触发价格
    stop_price DECIMAL(18, 8) COMMENT '止损/止盈触发价格',

    -- 订单来源
    order_source VARCHAR(50) DEFAULT 'manual' COMMENT '订单来源: manual(手动), signal(信号), auto(自动交易)',
    signal_id INT COMMENT '关联的信号ID',

    -- 备注
    notes TEXT COMMENT '订单备注',

    -- 时间戳
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_order_id (order_id),
    INDEX idx_status (status),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟交易订单表';

-- 4. 交易历史表
CREATE TABLE IF NOT EXISTS paper_trading_trades (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    -- 关联信息
    account_id INT NOT NULL COMMENT '账户ID',
    order_id VARCHAR(50) NOT NULL COMMENT '订单ID',
    trade_id VARCHAR(50) UNIQUE NOT NULL COMMENT '成交ID',

    -- 交易信息
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    side VARCHAR(10) NOT NULL COMMENT 'BUY(买入), SELL(卖出)',

    -- 价格和数量
    price DECIMAL(18, 8) NOT NULL COMMENT '成交价格',
    quantity DECIMAL(18, 8) NOT NULL COMMENT '成交数量',
    total_amount DECIMAL(20, 2) NOT NULL COMMENT '成交金额',

    -- 手续费
    fee DECIMAL(20, 8) DEFAULT 0 COMMENT '手续费',
    fee_asset VARCHAR(10) DEFAULT 'USDT' COMMENT '手续费币种',

    -- 盈亏(仅卖出时计算)
    realized_pnl DECIMAL(20, 2) COMMENT '已实现盈亏',
    pnl_pct DECIMAL(10, 4) COMMENT '收益率(%)',

    -- 成本价(买入时)
    cost_price DECIMAL(18, 8) COMMENT '成本价',

    -- 时间
    trade_time DATETIME NOT NULL COMMENT '成交时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_order_id (order_id),
    INDEX idx_trade_time (trade_time),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='模拟交易成交历史表';

-- 5. 账户资金变动历史表
CREATE TABLE IF NOT EXISTS paper_trading_balance_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,

    account_id INT NOT NULL COMMENT '账户ID',

    -- 资金快照
    balance DECIMAL(20, 2) NOT NULL COMMENT '可用余额',
    frozen_balance DECIMAL(20, 2) NOT NULL COMMENT '冻结余额',
    total_equity DECIMAL(20, 2) NOT NULL COMMENT '总权益',

    -- 盈亏
    realized_pnl DECIMAL(20, 2) NOT NULL COMMENT '已实现盈亏',
    unrealized_pnl DECIMAL(20, 2) NOT NULL COMMENT '未实现盈亏',
    total_pnl DECIMAL(20, 2) NOT NULL COMMENT '总盈亏',
    total_pnl_pct DECIMAL(10, 4) NOT NULL COMMENT '总收益率(%)',

    -- 变动原因
    change_type VARCHAR(50) COMMENT '变动类型: deposit(入金), withdraw(出金), trade(交易), fee(手续费), pnl_update(盈亏更新)',
    change_amount DECIMAL(20, 2) COMMENT '变动金额',
    related_order_id VARCHAR(50) COMMENT '关联订单ID',

    -- 备注
    notes TEXT COMMENT '备注',

    -- 时间
    snapshot_time DATETIME NOT NULL COMMENT '快照时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_time (account_id, snapshot_time),
    INDEX idx_change_type (change_type),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='账户资金变动历史表';

-- 6. 交易信号执行记录表
CREATE TABLE IF NOT EXISTS paper_trading_signal_executions (
    id INT AUTO_INCREMENT PRIMARY KEY,

    account_id INT NOT NULL COMMENT '账户ID',
    signal_id INT COMMENT '信号ID',

    -- 信号信息
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    signal_type VARCHAR(20) NOT NULL COMMENT '信号类型: BUY, SELL, HOLD',
    signal_strength VARCHAR(20) COMMENT '信号强度: STRONG, MEDIUM, WEAK',
    confidence_score DECIMAL(5, 2) COMMENT '置信度',

    -- 执行信息
    is_executed BOOLEAN DEFAULT FALSE COMMENT '是否已执行',
    execution_status VARCHAR(20) COMMENT '执行状态: success, failed, skipped',
    order_id VARCHAR(50) COMMENT '生成的订单ID',

    -- 执行决策
    decision VARCHAR(20) COMMENT '决策: execute(执行), skip(跳过), reject(拒绝)',
    decision_reason TEXT COMMENT '决策原因',

    -- 执行结果
    execution_price DECIMAL(18, 8) COMMENT '执行价格',
    execution_quantity DECIMAL(18, 8) COMMENT '执行数量',
    execution_amount DECIMAL(20, 2) COMMENT '执行金额',

    -- 时间
    signal_time DATETIME NOT NULL COMMENT '信号生成时间',
    execution_time DATETIME COMMENT '执行时间',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_account_symbol (account_id, symbol),
    INDEX idx_signal_time (signal_time),
    INDEX idx_execution_status (execution_status),
    FOREIGN KEY (account_id) REFERENCES paper_trading_accounts(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易信号执行记录表';

-- 7. 初始化默认账户
INSERT INTO paper_trading_accounts (
    account_name,
    account_type,
    initial_balance,
    current_balance,
    total_equity,
    status,
    is_default
) VALUES (
    '默认模拟账户',
    'spot',
    10000.00,
    10000.00,
    10000.00,
    'active',
    TRUE
) ON DUPLICATE KEY UPDATE account_name=account_name;
