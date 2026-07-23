-- 中线策略 v2：kill switch + 默认参数（§7.2）
-- 旧四路 kill switch 可保留行但不再生效；新调度只认 midline_long/short

INSERT INTO system_settings (setting_key, setting_value)
VALUES
  ('midline_long_enabled', '0'),
  ('midline_short_enabled', '0')
ON DUPLICATE KEY UPDATE setting_value=setting_value;

-- 默认周期 4h、限价 ±1%（已有行则覆盖为 v2 默认，便于上线）
INSERT INTO system_settings (setting_key, setting_value)
VALUES
  ('midline_interval_hours', '4'),
  ('midline_limit_long_offset_pct', '1.0'),
  ('midline_limit_short_offset_pct', '1.0')
ON DUPLICATE KEY UPDATE setting_value=VALUES(setting_value);
