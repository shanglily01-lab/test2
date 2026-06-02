-- Migration 017: GPT reversal + tactical four strategies + predict tables

-- GPT 顶空底多
CREATE TABLE IF NOT EXISTS `gpt_reversal_explore_runs` (
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
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 顶空底多探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_reversal_explore_verdicts` (
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
  CONSTRAINT `fk_gpt_reversal_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_reversal_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 顶空底多探索 - verdicts';

-- GPT 回调做多
CREATE TABLE IF NOT EXISTS `gpt_pullback_explore_runs` (
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
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 回调做多探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_pullback_explore_verdicts` (
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
  CONSTRAINT `fk_gpt_pullback_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_pullback_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 回调做多探索 - verdicts';

-- GPT 反弹做空
CREATE TABLE IF NOT EXISTS `gpt_rebound_explore_runs` (
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
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 反弹做空探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_rebound_explore_verdicts` (
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
  CONSTRAINT `fk_gpt_rebound_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_rebound_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 反弹做空探索 - verdicts';

-- GPT 追涨做多
CREATE TABLE IF NOT EXISTS `gpt_chase_explore_runs` (
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
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 追涨做多探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_chase_explore_verdicts` (
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
  CONSTRAINT `fk_gpt_chase_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_chase_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 追涨做多探索 - verdicts';

-- GPT 杀跌做空
CREATE TABLE IF NOT EXISTS `gpt_dump_explore_runs` (
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
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 杀跌做空探索 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_dump_explore_verdicts` (
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
  CONSTRAINT `fk_gpt_dump_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_dump_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 杀跌做空探索 - verdicts';

-- GPT 预测
CREATE TABLE IF NOT EXISTS `gpt_predict_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asof_utc` datetime NOT NULL,
  `model` varchar(100) NOT NULL DEFAULT '',
  `symbol_count` int(11) NOT NULL DEFAULT 0,
  `predictions_made` int(11) NOT NULL DEFAULT 0,
  `orders_opened` int(11) NOT NULL DEFAULT 0,
  `elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00,
  `status` varchar(20) NOT NULL DEFAULT 'ok',
  `error_msg` text DEFAULT NULL,
  `triggered_by` varchar(50) NOT NULL DEFAULT 'scheduler',
  `summary_zh` text DEFAULT NULL,
  `prompt_text` longtext DEFAULT NULL,
  `raw_response` longtext DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_asof_utc` (`asof_utc`),
  KEY `idx_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 预测 - 运行记录';

CREATE TABLE IF NOT EXISTS `gpt_predict_verdicts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_id` int(11) NOT NULL,
  `symbol` varchar(20) NOT NULL,
  `category` varchar(20) NOT NULL DEFAULT 'skip',
  `confidence` decimal(10,4) NOT NULL DEFAULT 0.0000,
  `catalyst` varchar(500) DEFAULT NULL,
  `data_signal` varchar(500) DEFAULT NULL,
  `risk_note` varchar(500) DEFAULT NULL,
  `price_at_pred` decimal(30,10) DEFAULT NULL,
  `action_taken` varchar(50) NOT NULL DEFAULT 'skipped_other',
  `position_id` int(11) DEFAULT NULL,
  `skip_reason` varchar(255) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_run_id` (`run_id`),
  CONSTRAINT `fk_gpt_predict_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `gpt_predict_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 预测 - verdicts';

INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
VALUES ('gpt_predict_enabled', '0', 'GPT 预测开关 (1=启用, 0=禁用)', 'migration_017', NOW())
ON DUPLICATE KEY UPDATE description=VALUES(description);

INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
VALUES ('gpt_predict_next_due_utc', '', 'GPT 预测下次应跑 UTC ISO', 'migration_017', NOW())
ON DUPLICATE KEY UPDATE description=VALUES(description);
