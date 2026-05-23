-- 044: gemini_sentiment_runs - 修复 market_direction_verdict 长度过短问题
-- 2026-05-24
-- Gemini 返回的中文方向判断超过 100 字符，改为 TEXT

ALTER TABLE gemini_sentiment_runs
    MODIFY COLUMN market_direction_verdict TEXT DEFAULT NULL COMMENT '大方向判断 (Gemini 原文, 已由代码截断至 500 字)';
