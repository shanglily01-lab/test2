-- Migration 019: 战术/反转探索 — 送模前预筛交易对记录与统计
CREATE TABLE IF NOT EXISTS `tactical_explore_screened` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `run_id` int(11) NOT NULL,
  `source` varchar(64) NOT NULL COMMENT 'gemini_pullback / gemini_reversal / gemini_pb_rb 等',
  `strategy_key` varchar(32) NOT NULL COMMENT 'pullback|rebound|reversal|top|bottom|pb_rb|ch_dm',
  `symbol` varchar(20) NOT NULL,
  `stage` enum('llm_pool','dropped') NOT NULL DEFAULT 'dropped',
  `screen_side` varchar(16) DEFAULT NULL COMMENT 'top/bottom/pullback/rebound/chase/dump',
  `score` decimal(12,4) DEFAULT NULL,
  `rsi_1h` decimal(8,2) DEFAULT NULL,
  `below_7d_high_pct` decimal(8,2) DEFAULT NULL,
  `above_7d_low_pct` decimal(8,2) DEFAULT NULL,
  `volume_note` varchar(120) DEFAULT NULL,
  `reason` varchar(255) DEFAULT NULL,
  `sent_to_llm` tinyint(1) NOT NULL DEFAULT 0,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_run_source` (`run_id`, `source`),
  KEY `idx_symbol_created` (`symbol`, `created_at`),
  KEY `idx_source_strategy` (`source`, `strategy_key`, `created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
