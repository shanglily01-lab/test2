-- 删除未配置在config.yaml中的交易对数据
-- 这些交易对导致持续的价格获取失败警告

-- 删除持仓
DELETE FROM paper_trading_positions
WHERE symbol IN ('PEPE/USDT', 'BONK/USDT', 'SHIB/USDT', 'FLOKI/USDT', 'ATM/USDT', 'LUNC/USDT')
  AND account_id = 1;

-- 删除交易记录
DELETE FROM paper_trading_trades
WHERE symbol IN ('PEPE/USDT', 'BONK/USDT', 'SHIB/USDT', 'FLOKI/USDT', 'ATM/USDT', 'LUNC/USDT')
  AND account_id = 1;

-- 删除待成交订单
DELETE FROM paper_trading_pending_orders
WHERE symbol IN ('PEPE/USDT', 'BONK/USDT', 'SHIB/USDT', 'FLOKI/USDT', 'ATM/USDT', 'LUNC/USDT')
  AND account_id = 1;

-- 验证删除结果
SELECT '=== 验证删除结果 ===' as info;

SELECT 'Remaining positions:' as info;
SELECT symbol, COUNT(*) as count
FROM paper_trading_positions
WHERE account_id = 1
GROUP BY symbol
ORDER BY symbol;

SELECT 'Remaining trades:' as info;
SELECT symbol, COUNT(*) as count
FROM paper_trading_trades
WHERE account_id = 1
GROUP BY symbol
ORDER BY symbol;

SELECT 'Check unconfigured symbols removed:' as info;
SELECT
    COUNT(*) as total_unconfigured_count,
    SUM(CASE WHEN symbol = 'PEPE/USDT' THEN 1 ELSE 0 END) as pepe_count,
    SUM(CASE WHEN symbol = 'BONK/USDT' THEN 1 ELSE 0 END) as bonk_count,
    SUM(CASE WHEN symbol = 'SHIB/USDT' THEN 1 ELSE 0 END) as shib_count,
    SUM(CASE WHEN symbol = 'FLOKI/USDT' THEN 1 ELSE 0 END) as floki_count,
    SUM(CASE WHEN symbol = 'ATM/USDT' THEN 1 ELSE 0 END) as atm_count,
    SUM(CASE WHEN symbol = 'LUNC/USDT' THEN 1 ELSE 0 END) as lunc_count
FROM paper_trading_positions
WHERE symbol IN ('PEPE/USDT', 'BONK/USDT', 'SHIB/USDT', 'FLOKI/USDT', 'ATM/USDT', 'LUNC/USDT')
  AND account_id = 1;
