-- 中线策略运行时参数（四路 gemini/deepseek × long/short 共用）
INSERT IGNORE INTO system_settings (setting_key, setting_value, description)
VALUES
  ('midline_interval_hours', '6', '中线扫描执行周期（小时），1-48'),
  ('midline_limit_long_offset_pct', '3.0', '中线做多限价偏移百分点（市价−N%），0.1-5'),
  ('midline_limit_short_offset_pct', '3.0', '中线做空限价偏移百分点（市价+N%），0.1-5');
