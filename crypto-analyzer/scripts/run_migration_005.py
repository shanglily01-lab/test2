#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Migration 005: Create DeepSeek predict tables (deepseek_predict_runs, deepseek_predict_verdicts)
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import os
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)

import pymysql
from app.utils.config_loader import get_db_config

def main():
    config = get_db_config()
    conn = pymysql.connect(**config, cursorclass=pymysql.cursors.DictCursor, charset='utf8mb4')
    try:
        with conn.cursor() as cur:
            # 1. deepseek_predict_runs
            cur.execute("""
CREATE TABLE IF NOT EXISTS `deepseek_predict_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asof_utc` datetime NOT NULL COMMENT 'snapshot time (UTC)',
  `model` varchar(100) NOT NULL DEFAULT '' COMMENT 'DeepSeek model name',
  `symbol_count` int(11) NOT NULL DEFAULT 0 COMMENT 'candidate symbols analyzed',
  `predictions_made` int(11) NOT NULL DEFAULT 0 COMMENT 'predictions made this round',
  `orders_opened` int(11) NOT NULL DEFAULT 0 COMMENT 'orders opened this round',
  `elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT 'elapsed seconds',
  `status` varchar(20) NOT NULL DEFAULT 'ok' COMMENT 'ok/partial/error/skipped',
  `error_msg` text DEFAULT NULL COMMENT 'error message',
  `triggered_by` varchar(50) NOT NULL DEFAULT 'scheduler' COMMENT 'scheduler/scheduler_init/manual/api',
  `summary_zh` text DEFAULT NULL COMMENT 'market summary (Chinese)',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_asof_utc` (`asof_utc`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='DeepSeek Predict - run records'
""")
            conn.commit()
            print("[OK] deepseek_predict_runs table created")

            # 2. deepseek_predict_verdicts
            cur.execute("""
CREATE TABLE IF NOT EXISTS `deepseek_predict_verdicts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_id` int(11) NOT NULL COMMENT 'associated run id',
  `symbol` varchar(20) NOT NULL COMMENT 'symbol e.g. BTCUSDT',
  `category` varchar(20) NOT NULL DEFAULT 'skip' COMMENT 'bullish/bearish/skip',
  `confidence` decimal(10,4) NOT NULL DEFAULT 0.0000 COMMENT 'confidence (0~1)',
  `catalyst` varchar(500) DEFAULT NULL COMMENT 'catalyst/trigger',
  `data_signal` varchar(500) DEFAULT NULL COMMENT 'data signal description',
  `risk_note` varchar(500) DEFAULT NULL COMMENT 'risk note',
  `price_at_pred` decimal(30,10) DEFAULT NULL COMMENT 'price at prediction time',
  `action_taken` varchar(50) NOT NULL DEFAULT 'skipped_other' COMMENT 'opened/skipped_big4/etc.',
  `position_id` int(11) DEFAULT NULL COMMENT 'futures_positions.id if opened',
  `skip_reason` varchar(255) DEFAULT NULL COMMENT 'skip reason',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_run_id` (`run_id`),
  KEY `idx_symbol` (`symbol`),
  KEY `idx_action_taken` (`action_taken`),
  KEY `idx_position_id` (`position_id`),
  CONSTRAINT `fk_deepseek_predict_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `deepseek_predict_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='DeepSeek Predict - LLM verdicts per symbol'
""")
            conn.commit()
            print("[OK] deepseek_predict_verdicts table created")

            print("=" * 60)
            print("Migration 005 completed successfully!")
            print("=" * 60)
    finally:
        conn.close()

if __name__ == '__main__':
    main()
