-- 查询缓存表数据
SELECT
    symbol,
    current_price,
    volume_24h,
    quote_volume_24h,
    updated_at
FROM price_stats_24h
WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT')
ORDER BY symbol;

-- 查询原始K线数据汇总（最近1小时）
SELECT
    symbol,
    COUNT(*) as kline_count,
    SUM(volume) as total_volume,
    SUM(quote_volume) as total_quote_volume,
    AVG(close) as avg_price
FROM kline_data
WHERE symbol IN ('BTC/USDT', 'ETH/USDT', 'SOL/USDT')
  AND timeframe = '5m'
  AND timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
GROUP BY symbol
ORDER BY symbol;

-- 查看几条原始K线样本
SELECT
    symbol,
    timestamp,
    close as price,
    volume,
    quote_volume,
    (quote_volume / volume) as implied_price
FROM kline_data
WHERE symbol = 'BTC/USDT'
  AND timeframe = '5m'
ORDER BY timestamp DESC
LIMIT 5;
