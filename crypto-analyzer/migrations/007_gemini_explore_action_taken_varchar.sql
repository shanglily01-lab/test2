-- gemini_explore_verdicts.action_taken 原为 ENUM, 无法写入 skipped_weak_catalyst 等新值
-- 与 deepseek_explore_verdicts 对齐为 varchar(50)

ALTER TABLE `gemini_explore_verdicts`
  MODIFY COLUMN `action_taken` varchar(50) NOT NULL DEFAULT 'skipped_other'
  COMMENT 'opened / skipped_confidence / skipped_weak_catalyst / skipped_direction_lock / etc.';
