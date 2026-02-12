-- 查询Big4紧急干预状态

-- 1. 查询当前有效的紧急干预记录
SELECT
    '【紧急干预记录】' as info,
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

-- 2. 查询Big4最近的趋势记录
SELECT
    '【Big4趋势历史】' as info,
    overall_signal,
    signal_strength,
    bullish_count,
    bearish_count,
    recommendation,
    btc_signal,
    btc_strength,
    eth_signal,
    eth_strength,
    bnb_signal,
    bnb_strength,
    sol_signal,
    sol_strength,
    created_at
FROM big4_trend_history
ORDER BY created_at DESC
LIMIT 5;
