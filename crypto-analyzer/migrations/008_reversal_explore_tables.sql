-- ============================================================
-- Migration 008: 顶空底多 (反转探索) — Gemini / DeepSeek
-- ============================================================
-- 数据库: binance-data
-- 说明: 顶部做空、底部做多；仅模拟仓，无 system_settings 开关
-- futures_positions.source: gemini_reversal / deepseek_reversal
-- ============================================================

CREATE TABLE IF NOT EXISTS `gemini_reversal_explore_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asof_utc` datetime NOT NULL,
  `model` varchar(100) NOT NULL DEFAULT '',
  `universe_size` int(11) NOT NULL DEFAULT 0,
  `summary_zh` text DEFAULT NULL,
  `prompt_text` longtext DEFAULT NULL,
  `raw_response` longtext DEFAULT NULL,
  `trades_opened` int(11) NOT NULL DEFAULT 0,
  `elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00,
  `status` varchar(20) NOT NULL DEFAULT 'ok',
  `error_msg` text DEFAULT NULL,
  `triggered_by` varchar(50) NOT NULL DEFAULT 'scheduler',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_asof_utc` (`asof_utc`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='Gemini 顶空底多探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gemini_reversal_explore_verdicts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_id` int(11) NOT NULL,
  `symbol` varchar(20) NOT NULL,
  `category` varchar(20) NOT NULL DEFAULT 'skip',
  `confidence` decimal(10,4) NOT NULL DEFAULT 0.0000,
  `catalyst` varchar(500) DEFAULT NULL,
  `data_signal` varchar(500) DEFAULT NULL,
  `risk_note` varchar(500) DEFAULT NULL,
  `action_taken` varchar(50) NOT NULL DEFAULT 'skipped_other',
  `position_id` int(11) DEFAULT NULL,
  `skip_reason` varchar(255) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_run_id` (`run_id`),
  KEY `idx_symbol` (`symbol`),
  CONSTRAINT `fk_gemini_reversal_verdict_run` FOREIGN KEY (`run_id`)
    REFERENCES `gemini_reversal_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='Gemini 顶空底多探索 - verdicts';

CREATE TABLE IF NOT EXISTS `deepseek_reversal_explore_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asof_utc` datetime NOT NULL,
  `model` varchar(100) NOT NULL DEFAULT '',
  `universe_size` int(11) NOT NULL DEFAULT 0,
  `summary_zh` text DEFAULT NULL,
  `prompt_text` longtext DEFAULT NULL,
  `raw_response` longtext DEFAULT NULL,
  `trades_opened` int(11) NOT NULL DEFAULT 0,
  `elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00,
  `status` varchar(20) NOT NULL DEFAULT 'ok',
  `error_msg` text DEFAULT NULL,
  `triggered_by` varchar(50) NOT NULL DEFAULT 'scheduler',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_asof_utc` (`asof_utc`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='DeepSeek 顶空底多探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `deepseek_reversal_explore_verdicts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_id` int(11) NOT NULL,
  `symbol` varchar(20) NOT NULL,
  `category` varchar(20) NOT NULL DEFAULT 'skip',
  `confidence` decimal(10,4) NOT NULL DEFAULT 0.0000,
  `catalyst` varchar(500) DEFAULT NULL,
  `data_signal` varchar(500) DEFAULT NULL,
  `risk_note` varchar(500) DEFAULT NULL,
  `action_taken` varchar(50) NOT NULL DEFAULT 'skipped_other',
  `position_id` int(11) DEFAULT NULL,
  `skip_reason` varchar(255) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_run_id` (`run_id`),
  KEY `idx_symbol` (`symbol`),
  CONSTRAINT `fk_deepseek_reversal_verdict_run` FOREIGN KEY (`run_id`)
    REFERENCES `deepseek_reversal_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='DeepSeek 顶空底多探索 - verdicts';
