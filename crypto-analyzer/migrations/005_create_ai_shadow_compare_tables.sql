-- ============================================================
-- Migration 005: AI Shadow 对比表 (Teacher vs 规则引擎, 不开仓)
-- ============================================================
-- 数据库: binance-data
-- 说明: Gemini/DeepSeek explore 每轮结束后, 用相同候选数据跑本地规则,
--       与 LLM verdict 对比一致率/差异, 用于后续蒸馏超级策略.
-- ============================================================

CREATE TABLE IF NOT EXISTS `ai_shadow_compare_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `teacher_source` varchar(32) NOT NULL COMMENT 'gemini_explore / deepseek_explore / gemini_predict / deepseek_predict',
  `teacher_run_id` int(11) NOT NULL COMMENT '对应 teacher 的 run id',
  `rules_version` varchar(16) NOT NULL DEFAULT 'v1',
  `universe_size` int(11) NOT NULL DEFAULT 0,
  `compared_count` int(11) NOT NULL DEFAULT 0 COMMENT '参与对比的 symbol 数',
  `category_match` int(11) NOT NULL DEFAULT 0 COMMENT 'category 完全一致数',
  `direction_match` int(11) NOT NULL DEFAULT 0 COMMENT '同向(含 skip)数, 同 category_match',
  `teacher_tradeable` int(11) NOT NULL DEFAULT 0 COMMENT 'teacher conf>=阈值且非 skip',
  `shadow_tradeable` int(11) NOT NULL DEFAULT 0,
  `tradeable_agree` int(11) NOT NULL DEFAULT 0 COMMENT '可交易判定同向',
  `disagree_samples` json DEFAULT NULL COMMENT '差异样例 [{symbol,teacher,shadow,reason}]',
  `elapsed_ms` int(11) NOT NULL DEFAULT 0,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_teacher` (`teacher_source`, `teacher_run_id`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='AI Shadow 对比 — 每轮 Teacher run 的汇总';

CREATE TABLE IF NOT EXISTS `ai_shadow_verdicts` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `shadow_run_id` int(11) NOT NULL,
  `symbol` varchar(20) NOT NULL,
  `teacher_category` varchar(20) NOT NULL DEFAULT 'skip',
  `teacher_confidence` decimal(10,4) NOT NULL DEFAULT 0.0000,
  `shadow_category` varchar(20) NOT NULL DEFAULT 'skip',
  `shadow_confidence` decimal(10,4) NOT NULL DEFAULT 0.0000,
  `category_match` tinyint(1) NOT NULL DEFAULT 0,
  `diff_reason` varchar(500) DEFAULT NULL,
  `shadow_signals` json DEFAULT NULL COMMENT '触发的规则标签',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_shadow_run_id` (`shadow_run_id`),
  KEY `idx_symbol` (`symbol`),
  KEY `idx_category_match` (`category_match`),
  CONSTRAINT `fk_shadow_verdict_run` FOREIGN KEY (`shadow_run_id`)
    REFERENCES `ai_shadow_compare_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='AI Shadow 对比 — 逐 symbol 明细';
