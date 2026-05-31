-- Big4 综合行情 LLM 分析 (Gemini / DeepSeek 共用表)
CREATE TABLE IF NOT EXISTS big4_analysis_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    provider ENUM('gemini', 'deepseek') NOT NULL,
    asof_utc DATETIME NOT NULL,
    model VARCHAR(64) DEFAULT NULL,
    elapsed_s DECIMAL(10, 2) DEFAULT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ok',
    error_msg VARCHAR(500) DEFAULT NULL,
    triggered_by VARCHAR(32) DEFAULT 'scheduler',
    big4_quant_signal VARCHAR(32) DEFAULT NULL,
    overall_label VARCHAR(32) DEFAULT NULL,
    overall_score DECIMAL(6, 3) DEFAULT NULL,
    direction_verdict VARCHAR(500) DEFAULT NULL,
    analysis_summary_zh MEDIUMTEXT,
    per_coin_json MEDIUMTEXT,
    prompt_text MEDIUMTEXT,
    raw_response MEDIUMTEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_provider_created (provider, created_at),
    INDEX idx_provider_status (provider, status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
