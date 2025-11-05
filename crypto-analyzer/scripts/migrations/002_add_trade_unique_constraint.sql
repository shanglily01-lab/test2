-- ========================================
-- 给 hyperliquid_wallet_trades 添加唯一约束
-- 防止重复记录相同的交易
-- 日期：2025-11-05
-- ========================================

USE `hyperliquid-data`;

-- 1. 先清理现有的重复数据（保留最早的记录）
DELETE t1 FROM hyperliquid_wallet_trades t1
INNER JOIN hyperliquid_wallet_trades t2
WHERE
    t1.id > t2.id
    AND t1.address = t2.address
    AND t1.coin = t2.coin
    AND t1.side = t2.side
    AND t1.trade_time = t2.trade_time
    AND ROUND(t1.notional_usd, 2) = ROUND(t2.notional_usd, 2);

-- 2. 添加唯一索引，防止未来插入重复数据
-- 注意：由于 notional_usd 是 DECIMAL，我们使用复合索引
-- 如果完全相同的交易（地址、币种、方向、时间、金额）被认为是重复
ALTER TABLE hyperliquid_wallet_trades
ADD UNIQUE INDEX uk_trade_dedup (
    address,
    coin,
    side,
    trade_time,
    notional_usd
);

-- 3. 检查结果
SELECT
    '去重完成' as status,
    COUNT(*) as total_trades,
    COUNT(DISTINCT CONCAT(address, coin, side, trade_time)) as unique_trades
FROM hyperliquid_wallet_trades;

-- 4. 显示最近的交易（验证去重效果）
SELECT
    address,
    coin,
    side,
    notional_usd,
    trade_time,
    COUNT(*) as count
FROM hyperliquid_wallet_trades
WHERE trade_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
GROUP BY address, coin, side, trade_time, ROUND(notional_usd, 2)
HAVING count > 1
ORDER BY count DESC
LIMIT 10;
