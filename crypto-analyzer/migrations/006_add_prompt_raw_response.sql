-- ============================================================
-- Migration 006: 给 AI 运行记录表加 prompt_text / raw_response 列
-- ============================================================
-- 数据库: binance-data
-- 说明: 保存发给 AI 模型的完整 prompt 和模型返回的原始 JSON，
--       便于复盘分析开仓决策依据。
-- ============================================================

-- 1. gemini_predict_runs
ALTER TABLE `gemini_predict_runs`
  ADD COLUMN `prompt_text` longtext DEFAULT NULL COMMENT '发给 Gemini 的完整 prompt（含市场数据）' AFTER `summary_zh`,
  ADD COLUMN `raw_response` longtext DEFAULT NULL COMMENT 'Gemini 返回的原始 JSON' AFTER `prompt_text`;

-- 2. gemini_explore_runs
ALTER TABLE `gemini_explore_runs`
  ADD COLUMN `prompt_text` longtext DEFAULT NULL COMMENT '发给 Gemini 的完整 prompt（含市场数据）' AFTER `summary_zh`,
  ADD COLUMN `raw_response` longtext DEFAULT NULL COMMENT 'Gemini 返回的原始 JSON' AFTER `prompt_text`;

-- 3. deepseek_predict_runs
ALTER TABLE `deepseek_predict_runs`
  ADD COLUMN `prompt_text` longtext DEFAULT NULL COMMENT '发给 DeepSeek 的完整 prompt（含市场数据）' AFTER `summary_zh`,
  ADD COLUMN `raw_response` longtext DEFAULT NULL COMMENT 'DeepSeek 返回的原始 JSON' AFTER `prompt_text`;

-- 4. deepseek_explore_runs
ALTER TABLE `deepseek_explore_runs`
  ADD COLUMN `prompt_text` longtext DEFAULT NULL COMMENT '发给 DeepSeek 的完整 prompt（含市场数据）' AFTER `summary_zh`,
  ADD COLUMN `raw_response` longtext DEFAULT NULL COMMENT 'DeepSeek 返回的原始 JSON' AFTER `prompt_text`;
