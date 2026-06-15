-- Fix kline_data base-volume overflow:
-- MySQL DECIMAL(20,8) only allows 12 integer digits, which is too small for
-- low-price high-supply futures symbols such as DOGS/USDT and NEIRO/USDT.
--
-- Execute during a quiet window. On this MySQL version, changing DECIMAL
-- precision may require ALGORITHM=COPY and can take time on a large table.
--
-- Pre-check: make sure no same ALTER is already running.
SHOW FULL PROCESSLIST;

-- Current column definitions.
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'kline_data'
  AND COLUMN_NAME IN ('volume', 'taker_buy_base_volume')
ORDER BY FIELD(COLUMN_NAME, 'volume', 'taker_buy_base_volume');

-- Main change.
ALTER TABLE kline_data
  MODIFY COLUMN volume DECIMAL(28,8) DEFAULT NULL,
  MODIFY COLUMN taker_buy_base_volume DECIMAL(28,8) DEFAULT NULL;

-- Verify.
SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
  AND TABLE_NAME = 'kline_data'
  AND COLUMN_NAME IN ('volume', 'taker_buy_base_volume')
ORDER BY FIELD(COLUMN_NAME, 'volume', 'taker_buy_base_volume');
