-- 下线趋势策略 / BTC 动量：清理 system_settings 遗留开关
DELETE FROM system_settings
WHERE setting_key IN (
    'trend_following_enabled',
    'btc_momentum_enabled',
    'signal_confirmation_enabled',
    'trading_mode'
);
