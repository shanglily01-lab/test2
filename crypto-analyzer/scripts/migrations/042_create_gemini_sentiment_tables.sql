-- 042: Gemini 市场情绪 + 川普讲话分析 — 独立表
-- 2026-05-23
-- 每 8h 执行一轮, 不做下单依据

-- 情绪分析运行记录表
CREATE TABLE IF NOT EXISTS gemini_sentiment_runs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    asof_utc        DATETIME     NOT NULL COMMENT '数据时间戳 UTC',
    model           VARCHAR(64)  NOT NULL DEFAULT '' COMMENT 'Gemini 模型名',
    elapsed_s       FLOAT        NOT NULL DEFAULT 0 COMMENT '耗时秒',
    status          ENUM('ok','partial','error','skipped') NOT NULL DEFAULT 'ok' COMMENT '状态',
    error_msg       VARCHAR(500)          DEFAULT NULL COMMENT '错误信息',
    triggered_by    VARCHAR(32)  NOT NULL DEFAULT 'scheduler' COMMENT '触发者',

    -- 市场情绪分析
    sentiment_summary_zh TEXT    DEFAULT NULL COMMENT '市场情绪综合分析 (Gemini 原文)',
    market_sentiment_label VARCHAR(20) DEFAULT NULL COMMENT '整体情绪标签: bullish/bearish/neutral/anxious/euphoric',
    market_sentiment_score  DECIMAL(4,2) DEFAULT NULL COMMENT '情绪评分 0.00-1.00 (1=极度乐观)',
    market_direction_verdict VARCHAR(100) DEFAULT NULL COMMENT '大方向判断 (如: 短期看多但需警惕回调)',

    -- 川普分析
    trump_analysis_zh   TEXT    DEFAULT NULL COMMENT '川普推特/讲话深度解读 (Gemini 原文)',
    trump_impact_label  VARCHAR(20) DEFAULT NULL COMMENT '川普讲话方向影响: positive/negative/neutral/mixed',
    trump_impact_score  DECIMAL(4,2) DEFAULT NULL COMMENT '影响评分 -1.00~1.00 (负数=利空, 正数=利好)',
    trump_key_topics    VARCHAR(500) DEFAULT NULL COMMENT '川普讲话关键主题',
    trump_market_impact VARCHAR(200) DEFAULT NULL COMMENT '对市场方向的影响判断',

    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_asof (asof_utc),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Gemini 市场情绪 + 川普分析 — 运行记录 (每 8h)';

-- 系统设置开关
INSERT INTO system_settings (setting_key, setting_value, description, updated_by, updated_at)
VALUES ('gemini_sentiment_enabled', '1', 'Gemini 市场情绪+川普分析开关 (1=启用, 0=禁用)', 'system', NOW())
ON DUPLICATE KEY UPDATE
    setting_value = '1',
    description = 'Gemini 市场情绪+川普分析开关 (1=启用, 0=禁用)',
    updated_by = 'system',
    updated_at = NOW();
