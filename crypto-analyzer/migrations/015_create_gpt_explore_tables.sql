-- ============================================================
-- Migration 015: 创建 GPT 探索相关表
-- ============================================================

CREATE TABLE IF NOT EXISTS `gpt_explore_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asof_utc` datetime NOT NULL,
  `model` varchar(100) NOT NULL DEFAULT '',
  `universe_size` int(11) NOT NULL DEFAULT 0,
  `summary_zh` text DEFAULT NULL,
  `trades_opened` int(11) NOT NULL DEFAULT 0,
  `elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00,
  `status` varchar(20) NOT NULL DEFAULT 'ok',
  `error_msg` text DEFAULT NULL,
  `triggered_by` varchar(50) NOT NULL DEFAULT 'scheduler',
  `prompt_text` longtext DEFAULT NULL,
  `raw_response` longtext DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_asof_utc` (`asof_utc`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_explore_verdicts` (
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
  KEY `idx_action_taken` (`action_taken`),
  KEY `idx_position_id` (`position_id`),
  CONSTRAINT `fk_gpt_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 探索 - 每个候选交易对的 LLM 判断结果';

INSERT INTO system_settings (setting_key, setting_value, description, updated_at)
VALUES ('gpt_explore_enabled', '0', 'GPT 探索开关 (1=启用, 0=禁用)', NOW())
ON DUPLICATE KEY UPDATE setting_key = setting_key;
