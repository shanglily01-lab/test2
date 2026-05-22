-- 039: 新增实盘平仓独立开关 live_close_enabled
-- 之前 live_trading_enabled 同时控制开仓和平仓。
-- 现在拆分为:
--   live_trading_enabled -> 仅控制实盘开仓
--   live_close_enabled  -> 仅控制实盘平仓

INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
VALUES ('live_close_enabled', '1', '实盘平仓开关 (1=启用, 0=禁用)', 'system', NOW())
ON DUPLICATE KEY UPDATE
    setting_value = '1',
    description = '实盘平仓开关 (1=启用, 0=禁用)',
    updated_by = 'system',
    updated_at = NOW();
