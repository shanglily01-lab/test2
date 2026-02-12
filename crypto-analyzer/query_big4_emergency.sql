-- 查询Big4紧急干预状态

-- 1. 查询当前有效的紧急干预记录
SELECT
    '【紧急干预记录】' as section,
    intervention_type,
    trigger_reason,
    block_long,
    block_short,
    created_at,
    expires_at,
    TIMESTAMPDIFF(MINUTE, NOW(), expires_at) as remaining_minutes
FROM emergency_intervention
WHERE account_id = 2
  AND trading_type = 'usdt_futures'
  AND expires_at > NOW()
ORDER BY created_at DESC;

-- 2. 查询反弹窗口
SELECT
    '【反弹窗口】' as section,
    symbols,
    window_start,
    window_end,
    TIMESTAMPDIFF(MINUTE, NOW(), window_end) as remaining_minutes,
    created_at
FROM bounce_window
WHERE account_id = 2
  AND trading_type = 'usdt_futures'
  AND window_end > NOW()
ORDER BY created_at DESC;

-- 3. 查询Big4最近的趋势记录
SELECT
    '【Big4趋势】' as section,
    overall_signal,
    signal_strength,
    bullish_count,
    bearish_count,
    bullish_weight,
    bearish_weight,
    recommendation,
    timestamp
FROM big4_market_trends
ORDER BY timestamp DESC
LIMIT 5;

-- 4. 查询各币种最近的分析
SELECT
    '【币种详情】' as section,
    symbol,
    signal,
    strength,
    net_power_1h,
    net_power_15m,
    timestamp
FROM big4_symbol_analysis
WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
ORDER BY timestamp DESC, symbol;
