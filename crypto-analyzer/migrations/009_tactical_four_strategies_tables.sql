-- ============================================================
-- Migration 009: 四战术策略表 (回调做多/反弹做空/追涨做多/杀跌做空)
-- Gemini + DeepSeek 各 4 组
-- ============================================================

-- 以下 runs 表结构相同，仅表名不同
CREATE TABLE IF NOT EXISTS `gemini_pullback_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_pullback_explore_verdicts` (
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
  CONSTRAINT `fk_gemini_pullback_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `gemini_pullback_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_rebound_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_rebound_explore_verdicts` (
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
  CONSTRAINT `fk_gemini_rebound_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `gemini_rebound_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_chase_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_chase_explore_verdicts` (
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
  CONSTRAINT `fk_gemini_chase_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `gemini_chase_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_dump_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `gemini_dump_explore_verdicts` (
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
  CONSTRAINT `fk_gemini_dump_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `gemini_dump_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_pullback_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_pullback_explore_verdicts` (
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
  CONSTRAINT `fk_deepseek_pullback_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `deepseek_pullback_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_rebound_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_rebound_explore_verdicts` (
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
  CONSTRAINT `fk_deepseek_rebound_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `deepseek_rebound_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_chase_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_chase_explore_verdicts` (
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
  CONSTRAINT `fk_deepseek_chase_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `deepseek_chase_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_dump_explore_runs` (
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS `deepseek_dump_explore_verdicts` (
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
  CONSTRAINT `fk_deepseek_dump_verdict` FOREIGN KEY (`run_id`)
    REFERENCES `deepseek_dump_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
