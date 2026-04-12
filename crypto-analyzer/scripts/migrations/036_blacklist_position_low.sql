-- 禁用 position_low 多头信号（历史胜率仅 18.8%，属于差信号）
-- 配合 signal_blacklist_checker.py 的单组件包含匹配逻辑，
-- 任何含有 position_low 的多头信号组合均被拦截。

INSERT INTO signal_blacklist
    (signal_type, position_side, reason, win_rate, is_active, notes)
VALUES
    ('position_low', 'LONG', '胜率仅18.8%，低位不代表反弹，禁止作为多头触发条件', 0.188, 1,
     '2026-04-12 手动禁用，来自复盘信号健康度报告')
ON DUPLICATE KEY UPDATE
    reason     = VALUES(reason),
    win_rate   = VALUES(win_rate),
    is_active  = 1,
    notes      = VALUES(notes),
    updated_at = NOW();
