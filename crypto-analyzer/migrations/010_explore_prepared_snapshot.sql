-- ============================================================
-- Migration 010: Gemini/DeepSeek 探索共用预计算包 (universe + global_ctx)
-- 数据库: data_cache
-- 由 scheduler 每 15 分钟 refresh，各策略只读
-- ============================================================

CREATE TABLE IF NOT EXISTS `explore_prepared_snapshot` (
  `id` tinyint(4) NOT NULL DEFAULT 1,
  `symbol_count` int(11) NOT NULL DEFAULT 0,
  `universe_json` longtext NOT NULL COMMENT 'symbol -> 行情/K线叙事/tech',
  `global_ctx_json` json NOT NULL COMMENT 'Big4/大盘/宏观',
  `built_at` datetime NOT NULL,
  `build_elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00,
  `status` varchar(20) NOT NULL DEFAULT 'ok',
  `error_msg` varchar(500) DEFAULT NULL,
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp(),
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='探索/战术策略共用预计算 universe (单例行 id=1)';
