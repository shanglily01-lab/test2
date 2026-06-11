-- PaperLimitSyncService: track paper FILLED -> live open sync state on futures_orders.
-- Without these columns every [PaperSync] tick fails on SELECT/UPDATE.

ALTER TABLE futures_orders
  ADD COLUMN live_sync_status VARCHAR(16) DEFAULT NULL
    COMMENT '实盘同步: NULL=待处理 SYNCED/SKIPPED/FAILED' AFTER updated_at,
  ADD COLUMN live_synced_at DATETIME DEFAULT NULL
    COMMENT '实盘同步完成时间' AFTER live_sync_status,
  ADD COLUMN live_position_id VARCHAR(64) DEFAULT NULL
    COMMENT '关联实盘持仓ID' AFTER live_synced_at;

CREATE INDEX idx_live_sync_pending ON futures_orders (status, live_sync_status, fill_time);
