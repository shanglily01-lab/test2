-- ============================================================
-- Migration 004: 创建 DeepSeek 探索相关表
-- ============================================================
-- 数据库: binance-data
-- 说明: DeepSeek 探索功能需要独立的 run/verdict 表来存储
--       每轮探索的运行记录和 LLM 判断结果。
-- ============================================================

-- 1. deepseek_explore_runs - 每轮探索的运行记录
CREATE TABLE IF NOT EXISTS `deepseek_explore_runs` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `asof_utc` datetime NOT NULL COMMENT '本轮探索的快照时间 (UTC)',
  `model` varchar(100) NOT NULL DEFAULT '' COMMENT '使用的 DeepSeek 模型名',
  `universe_size` int(11) NOT NULL DEFAULT 0 COMMENT '本轮审核的候选交易对数量',
  `summary_zh` text DEFAULT NULL COMMENT '本轮的市场概况摘要 (中文)',
  `trades_opened` int(11) NOT NULL DEFAULT 0 COMMENT '本轮实际开仓数',
  `elapsed_s` decimal(10,2) NOT NULL DEFAULT 0.00 COMMENT '本轮总耗时 (秒)',
  `status` varchar(20) NOT NULL DEFAULT 'ok' COMMENT '运行状态: ok / partial / error / skipped',
  `error_msg` text DEFAULT NULL COMMENT '出错时的错误信息',
  `triggered_by` varchar(50) NOT NULL DEFAULT 'scheduler' COMMENT '触发来源: scheduler / scheduler_init / manual / api',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp() COMMENT '记录创建时间',
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp() ON UPDATE current_timestamp() COMMENT '最后更新时间',
  PRIMARY KEY (`id`),
  KEY `idx_asof_utc` (`asof_utc`),
  KEY `idx_status` (`status`),
  KEY `idx_created_at` (`created_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='DeepSeek 探索 - 运行记录';


-- 2. deepseek_explore_verdicts - 每条候选交易对的 LLM 判断结果
CREATE TABLE IF NOT EXISTS `deepseek_explore_verdicts` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `run_id` int(11) NOT NULL COMMENT '关联的 run id',
  `symbol` varchar(20) NOT NULL COMMENT '交易对，如 BTCUSDT',
  `category` varchar(20) NOT NULL DEFAULT 'skip' COMMENT '判断类别: bullish / bearish / skip',
  `confidence` decimal(10,4) NOT NULL DEFAULT 0.0000 COMMENT '置信度 (0~1)',
  `catalyst` varchar(500) DEFAULT NULL COMMENT '催化剂/触发因素',
  `data_signal` varchar(500) DEFAULT NULL COMMENT '数据信号描述',
  `risk_note` varchar(500) DEFAULT NULL COMMENT '风险提示',
  `action_taken` varchar(50) NOT NULL DEFAULT 'skipped_other' COMMENT '实际执行动作: opened / skipped_big4 / skipped_dedup / skipped_confidence / skipped_max_positions / etc.',
  `position_id` int(11) DEFAULT NULL COMMENT '如果 opened, 关联的 futures_positions.id',
  `skip_reason` varchar(255) DEFAULT NULL COMMENT '如果跳过的原因',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_run_id` (`run_id`),
  KEY `idx_symbol` (`symbol`),
  KEY `idx_action_taken` (`action_taken`),
  KEY `idx_position_id` (`position_id`),
  CONSTRAINT `fk_deepseek_verdict_run` FOREIGN KEY (`run_id`) REFERENCES `deepseek_explore_runs` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='DeepSeek 探索 - 每个候选交易对的 LLM 判断结果';
