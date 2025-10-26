-- 投资建议表
-- Investment Recommendations Table

USE `binance-data`;

-- 创建投资建议表
CREATE TABLE IF NOT EXISTS `investment_recommendations` (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `symbol` VARCHAR(20) NOT NULL COMMENT '交易对，如 BTC/USDT',
    `recommendation` VARCHAR(20) NOT NULL COMMENT '投资建议：强烈买入、买入、持有、卖出、强烈卖出',
    `confidence` DECIMAL(5, 2) NOT NULL COMMENT '置信度 0-100',
    `reasoning` TEXT COMMENT '推理原因',

    -- 各数据源评分
    `technical_score` DECIMAL(5, 2) COMMENT '技术指标评分 0-100',
    `news_sentiment_score` DECIMAL(5, 2) COMMENT '新闻情绪评分 0-100',
    `funding_rate_score` DECIMAL(5, 2) COMMENT '资金费率评分 0-100',
    `smart_money_score` DECIMAL(5, 2) COMMENT '聪明钱评分 0-100',
    `onchain_score` DECIMAL(5, 2) COMMENT '链上数据评分 0-100',

    -- 技术指标详情
    `rsi` DECIMAL(10, 4) COMMENT 'RSI指标值',
    `macd` DECIMAL(10, 4) COMMENT 'MACD指标值',
    `macd_signal` DECIMAL(10, 4) COMMENT 'MACD信号线',
    `macd_histogram` DECIMAL(10, 4) COMMENT 'MACD柱状图',

    -- 价格信息
    `current_price` DECIMAL(18, 8) COMMENT '当前价格',
    `price_change_24h` DECIMAL(10, 4) COMMENT '24小时价格变化%',

    -- 风险评估
    `risk_level` VARCHAR(10) COMMENT '风险等级: LOW, MEDIUM, HIGH',
    `volatility` DECIMAL(10, 4) COMMENT '波动率',

    -- 时间戳
    `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',

    -- 索引
    KEY `idx_symbol` (`symbol`),
    KEY `idx_updated_at` (`updated_at`),
    KEY `idx_confidence` (`confidence`),

    -- 唯一约束（每个币种只保留最新的一条建议）
    UNIQUE KEY `uk_symbol` (`symbol`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='投资建议表';

-- 插入示例数据（用于测试）
INSERT INTO `investment_recommendations`
    (`symbol`, `recommendation`, `confidence`, `reasoning`,
     `technical_score`, `news_sentiment_score`, `funding_rate_score`, `smart_money_score`, `onchain_score`,
     `rsi`, `current_price`, `risk_level`)
VALUES
    ('BTC/USDT', '持有', 40.00,
     '技术指标中性\n新闻情绪中性\n资金费率接近0\n市场缺乏明确方向',
     45.00, 50.00, 50.00, 35.00, 50.00,
     52.5, 95000.00, 'MEDIUM'),

    ('ETH/USDT', '持有', 42.00,
     '技术指标略偏多\n但其他指标中性\n等待更明确信号',
     48.00, 52.00, 48.00, 38.00, 50.00,
     54.2, 3500.00, 'MEDIUM'),

    ('SOL/USDT', '持有', 38.00,
     '技术指标偏空\n但置信度不足\n建议观望',
     42.00, 48.00, 52.00, 32.00, 48.00,
     48.5, 180.00, 'MEDIUM'),

    ('BNB/USDT', '持有', 41.00,
     '市场横盘整理\n各指标均衡\n等待突破',
     46.00, 49.00, 51.00, 37.00, 49.00,
     51.8, 620.00, 'MEDIUM')
ON DUPLICATE KEY UPDATE
    recommendation = VALUES(recommendation),
    confidence = VALUES(confidence),
    reasoning = VALUES(reasoning),
    technical_score = VALUES(technical_score),
    news_sentiment_score = VALUES(news_sentiment_score),
    funding_rate_score = VALUES(funding_rate_score),
    smart_money_score = VALUES(smart_money_score),
    onchain_score = VALUES(onchain_score),
    rsi = VALUES(rsi),
    current_price = VALUES(current_price),
    risk_level = VALUES(risk_level),
    updated_at = CURRENT_TIMESTAMP;

-- 查询验证
SELECT
    symbol,
    recommendation,
    confidence,
    technical_score,
    news_sentiment_score,
    funding_rate_score,
    smart_money_score,
    updated_at
FROM investment_recommendations
ORDER BY symbol;
