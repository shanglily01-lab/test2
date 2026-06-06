-- Persist replayable AI learning/audit payloads for Shadow and advisor reviews.

ALTER TABLE ai_shadow_compare_runs
  ADD COLUMN IF NOT EXISTS universe_json LONGTEXT NULL COMMENT 'Shadow input universe snapshot',
  ADD COLUMN IF NOT EXISTS global_ctx_json LONGTEXT NULL COMMENT 'Shadow global context snapshot',
  ADD COLUMN IF NOT EXISTS teacher_verdicts_json LONGTEXT NULL COMMENT 'Teacher verdicts used for comparison',
  ADD COLUMN IF NOT EXISTS shadow_verdicts_json LONGTEXT NULL COMMENT 'Rule engine verdict replay snapshot';

ALTER TABLE gemini_advisor_reviews
  ADD COLUMN IF NOT EXISTS prompt_text LONGTEXT NULL COMMENT 'User prompt sent to advisor',
  ADD COLUMN IF NOT EXISTS input_json LONGTEXT NULL COMMENT 'Market/position/request payload fed to advisor',
  ADD COLUMN IF NOT EXISTS raw_response LONGTEXT NULL COMMENT 'Raw model response before parsing/tempering',
  ADD COLUMN IF NOT EXISTS system_prompt TEXT NULL COMMENT 'System prompt sent to advisor';

ALTER TABLE deepseek_advisor_reviews
  ADD COLUMN IF NOT EXISTS prompt_text LONGTEXT NULL COMMENT 'User prompt sent to advisor',
  ADD COLUMN IF NOT EXISTS input_json LONGTEXT NULL COMMENT 'Market/position/request payload fed to advisor',
  ADD COLUMN IF NOT EXISTS raw_response LONGTEXT NULL COMMENT 'Raw model response before parsing/tempering',
  ADD COLUMN IF NOT EXISTS system_prompt TEXT NULL COMMENT 'System prompt sent to advisor';

ALTER TABLE gpt_advisor_reviews
  ADD COLUMN IF NOT EXISTS prompt_text LONGTEXT NULL COMMENT 'User prompt sent to advisor',
  ADD COLUMN IF NOT EXISTS input_json LONGTEXT NULL COMMENT 'Market/position/request payload fed to advisor',
  ADD COLUMN IF NOT EXISTS raw_response LONGTEXT NULL COMMENT 'Raw model response before parsing/tempering',
  ADD COLUMN IF NOT EXISTS system_prompt TEXT NULL COMMENT 'System prompt sent to advisor';
