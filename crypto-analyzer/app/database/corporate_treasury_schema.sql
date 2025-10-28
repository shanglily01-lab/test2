-- 企业金库公司信息表
CREATE TABLE IF NOT EXISTS corporate_treasury_companies (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_name VARCHAR(100) NOT NULL UNIQUE COMMENT '公司名称',
    ticker_symbol VARCHAR(20) COMMENT '股票代码',
    category VARCHAR(50) COMMENT '分类：mining, holding, payment等',
    description TEXT COMMENT '公司简介',
    official_website VARCHAR(255) COMMENT '官网',
    is_active TINYINT(1) DEFAULT 1 COMMENT '是否活跃监控',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_company_name (company_name),
    INDEX idx_ticker (ticker_symbol)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业金库公司基础信息';

-- 企业金库购买记录表
CREATE TABLE IF NOT EXISTS corporate_treasury_purchases (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL COMMENT '公司ID',
    purchase_date DATE NOT NULL COMMENT '购买日期',
    asset_type VARCHAR(20) NOT NULL COMMENT '资产类型：BTC, ETH',
    quantity DECIMAL(20, 8) NOT NULL COMMENT '购买数量',
    average_price DECIMAL(20, 2) COMMENT '平均购买价格(USD)',
    total_amount DECIMAL(20, 2) COMMENT '购买总金额(USD)',
    cumulative_holdings DECIMAL(20, 8) COMMENT '累计持仓量',
    announcement_url VARCHAR(500) COMMENT '公告链接',
    notes TEXT COMMENT '备注',
    data_source VARCHAR(50) DEFAULT 'manual' COMMENT '数据来源',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES corporate_treasury_companies(id),
    INDEX idx_company_date (company_id, purchase_date),
    INDEX idx_asset_type (asset_type),
    INDEX idx_purchase_date (purchase_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业金库购买记录';

-- 企业融资记录表
CREATE TABLE IF NOT EXISTS corporate_treasury_financing (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL COMMENT '公司ID',
    financing_date DATE NOT NULL COMMENT '融资日期',
    financing_type VARCHAR(50) COMMENT '融资类型：equity, convertible_note, loan, atm等',
    amount DECIMAL(20, 2) COMMENT '融资金额(USD)',
    purpose TEXT COMMENT '用途说明',
    announcement_url VARCHAR(500) COMMENT '公告链接',
    notes TEXT COMMENT '备注',
    data_source VARCHAR(50) DEFAULT 'manual' COMMENT '数据来源',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES corporate_treasury_companies(id),
    INDEX idx_company_date (company_id, financing_date),
    INDEX idx_financing_type (financing_type),
    INDEX idx_financing_date (financing_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业融资记录';

-- 企业股价数据表
CREATE TABLE IF NOT EXISTS corporate_treasury_stock_prices (
    id INT AUTO_INCREMENT PRIMARY KEY,
    company_id INT NOT NULL COMMENT '公司ID',
    trade_date DATE NOT NULL COMMENT '交易日期',
    open_price DECIMAL(10, 2) COMMENT '开盘价',
    close_price DECIMAL(10, 2) COMMENT '收盘价',
    high_price DECIMAL(10, 2) COMMENT '最高价',
    low_price DECIMAL(10, 2) COMMENT '最低价',
    volume BIGINT COMMENT '成交量',
    market_cap DECIMAL(20, 2) COMMENT '市值',
    change_pct DECIMAL(10, 4) COMMENT '涨跌幅(%)',
    data_source VARCHAR(50) DEFAULT 'manual' COMMENT '数据来源',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (company_id) REFERENCES corporate_treasury_companies(id),
    UNIQUE KEY unique_company_date (company_id, trade_date),
    INDEX idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='企业股价数据';

-- 企业金库汇总视图（最新持仓和统计）
CREATE OR REPLACE VIEW corporate_treasury_summary AS
SELECT
    c.id,
    c.company_name,
    c.ticker_symbol,
    c.category,
    -- BTC持仓
    (SELECT cumulative_holdings
     FROM corporate_treasury_purchases
     WHERE company_id = c.id AND asset_type = 'BTC'
     ORDER BY purchase_date DESC LIMIT 1) as btc_holdings,
    (SELECT SUM(total_amount)
     FROM corporate_treasury_purchases
     WHERE company_id = c.id AND asset_type = 'BTC') as btc_total_investment,
    -- ETH持仓
    (SELECT cumulative_holdings
     FROM corporate_treasury_purchases
     WHERE company_id = c.id AND asset_type = 'ETH'
     ORDER BY purchase_date DESC LIMIT 1) as eth_holdings,
    (SELECT SUM(total_amount)
     FROM corporate_treasury_purchases
     WHERE company_id = c.id AND asset_type = 'ETH') as eth_total_investment,
    -- 最近购买
    (SELECT MAX(purchase_date)
     FROM corporate_treasury_purchases
     WHERE company_id = c.id) as last_purchase_date,
    -- 最近融资
    (SELECT SUM(amount)
     FROM corporate_treasury_financing
     WHERE company_id = c.id) as total_financing,
    (SELECT MAX(financing_date)
     FROM corporate_treasury_financing
     WHERE company_id = c.id) as last_financing_date,
    -- 最新股价
    (SELECT close_price
     FROM corporate_treasury_stock_prices
     WHERE company_id = c.id
     ORDER BY trade_date DESC LIMIT 1) as latest_stock_price,
    (SELECT change_pct
     FROM corporate_treasury_stock_prices
     WHERE company_id = c.id
     ORDER BY trade_date DESC LIMIT 1) as latest_change_pct,
    c.is_active
FROM corporate_treasury_companies c
WHERE c.is_active = 1;

-- 插入两家公司的基础信息
INSERT INTO corporate_treasury_companies (company_name, ticker_symbol, category, description, is_active) VALUES
('Strategy Inc', 'MSTR', 'holding', 'MicroStrategy旗下商业智能公司，全球最大的比特币企业持有者', 1),
('BitMine', NULL, 'mining', '比特币挖矿公司', 1)
ON DUPLICATE KEY UPDATE
    ticker_symbol = VALUES(ticker_symbol),
    category = VALUES(category),
    description = VALUES(description),
    is_active = VALUES(is_active);