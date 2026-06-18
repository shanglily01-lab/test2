-- 中线策略 LLM 调用记录

ALTER TABLE midline_swing_runs
    ADD COLUMN IF NOT EXISTS prompt_text MEDIUMTEXT NULL AFTER summary_zh,
    ADD COLUMN IF NOT EXISTS raw_response MEDIUMTEXT NULL AFTER prompt_text;
