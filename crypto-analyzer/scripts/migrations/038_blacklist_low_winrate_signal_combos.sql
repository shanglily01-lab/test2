-- ========================================
-- 禁用低胜率信号组合（P1-4 优化）
-- 基于复盘数据，以下信号组合胜率低于15%，
-- 与其让优化器缓慢降权，直接禁止开仓
-- ========================================

-- 1. position_high + volatility_high 做空 (胜率 5.9%, 17单)
INSERT INTO signal_blacklist
    (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active, notes)
VALUES
    ('position_high + volatility_high', 'SHORT',
     '胜率仅5.9%，高位+高波动组合做空严重亏损',
     0.059, -128.77, 17, 1,
     '2026-05-22 P1-4优化禁用，来自复盘信号健康度报告')
ON DUPLICATE KEY UPDATE
    reason     = VALUES(reason),
    win_rate   = VALUES(win_rate),
    is_active  = 1,
    notes      = VALUES(notes),
    updated_at = NOW();

-- 2. momentum_up_3pct + volatility_high 做多 (胜率 14.3%, 7单)
INSERT INTO signal_blacklist
    (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active, notes)
VALUES
    ('momentum_up_3pct + volatility_high', 'LONG',
     '胜率仅14.3%，涨势3%+高波动组合做多严重亏损',
     0.143, -15.28, 7, 1,
     '2026-05-22 P1-4优化禁用，来自复盘信号健康度报告')
ON DUPLICATE KEY UPDATE
    reason     = VALUES(reason),
    win_rate   = VALUES(win_rate),
    is_active  = 1,
    notes      = VALUES(notes),
    updated_at = NOW();

-- 3. position_24h_low + position_mid + volatility_high 做多 (胜率 0.0%, 6单)
INSERT INTO signal_blacklist
    (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active, notes)
VALUES
    ('position_24h_low + position_mid + volatility_high', 'LONG',
     '胜率0.0%，24H低位+中位+高波动做多全部亏损',
     0.0, -31.34, 6, 1,
     '2026-05-22 P1-4优化禁用，来自复盘信号健康度报告')
ON DUPLICATE KEY UPDATE
    reason     = VALUES(reason),
    win_rate   = VALUES(win_rate),
    is_active  = 1,
    notes      = VALUES(notes),
    updated_at = NOW();

-- 4. consecutive_bear + trend_1h_bear 做空 (胜率 25.0%, 12单，接近15%线)
-- 虽然胜率25%略高于15%，但总亏损-31.52U，且可能继续恶化，先加入观察
INSERT INTO signal_blacklist
    (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active, notes)
VALUES
    ('consecutive_bear + trend_1h_bear', 'SHORT',
     '胜率25.0%接近底部，连阴+1H看跌做空趋势不延续可能继续恶化',
     0.25, -31.52, 12, 1,
     '2026-05-22 P1-4优化禁用，来自复盘信号健康度报告')
ON DUPLICATE KEY UPDATE
    reason     = VALUES(reason),
    win_rate   = VALUES(win_rate),
    is_active  = 1,
    notes      = VALUES(notes),
    updated_at = NOW();

-- 5. consecutive_bear + trend_1h_bear + volatility_high 做空 (胜率 31.2%, 16单)
-- 胜率31.2%但亏损-71.87U，高波动放大了亏损幅度
INSERT INTO signal_blacklist
    (signal_type, position_side, reason, win_rate, total_loss, order_count, is_active, notes)
VALUES
    ('consecutive_bear + trend_1h_bear + volatility_high', 'SHORT',
     '胜率31.2%但亏损-71.87U，连阴+1H看跌+高波动做空亏损放大',
     0.312, -71.87, 16, 1,
     '2026-05-22 P1-4优化禁用，来自复盘信号健康度报告')
ON DUPLICATE KEY UPDATE
    reason     = VALUES(reason),
    win_rate   = VALUES(win_rate),
    is_active  = 1,
    notes      = VALUES(notes),
    updated_at = NOW();

-- 验证
SELECT id, signal_type, position_side, win_rate, total_loss, order_count, reason
FROM signal_blacklist
WHERE is_active = 1
  AND updated_at >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
ORDER BY win_rate ASC;
