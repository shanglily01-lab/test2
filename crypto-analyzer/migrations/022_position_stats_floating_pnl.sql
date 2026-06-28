-- 探索页 /stats 浮盈：写入 position_stats_snapshot，避免 API 再扫 futures_positions
ALTER TABLE data_cache.position_stats_snapshot
  ADD COLUMN floating_pnl DECIMAL(20,4) DEFAULT 0 COMMENT 'open仓浮盈合计' AFTER open_count;
