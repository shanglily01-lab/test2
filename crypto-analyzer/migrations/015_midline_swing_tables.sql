-- 中线做多/做空策略（Gemini / DeepSeek 共用 runs/verdicts，按 source 区分）

CREATE TABLE IF NOT EXISTS midline_swing_runs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(64) NOT NULL,
    asof_utc DATETIME NOT NULL,
    universe_size INT NOT NULL DEFAULT 0,
    signals_found INT NOT NULL DEFAULT 0,
    orders_placed INT NOT NULL DEFAULT 0,
    elapsed_s DOUBLE NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'ok',
    error_msg VARCHAR(512) NULL,
    triggered_by VARCHAR(32) NOT NULL DEFAULT 'scheduler',
    summary_zh VARCHAR(512) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_midline_runs_source_id (source, id DESC),
    INDEX idx_midline_runs_source_asof (source, asof_utc DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS midline_swing_verdicts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    run_id INT NOT NULL,
    source VARCHAR(64) NOT NULL,
    symbol VARCHAR(32) NOT NULL,
    side VARCHAR(8) NOT NULL,
    score DOUBLE NOT NULL DEFAULT 0,
    signal_detail JSON NULL,
    action_taken VARCHAR(32) NOT NULL DEFAULT 'skip',
    order_id INT NULL,
    position_id INT NULL,
    skip_reason VARCHAR(255) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_midline_verdicts_run (run_id),
    INDEX idx_midline_verdicts_source (source, created_at DESC),
        CONSTRAINT fk_midline_verdicts_run
        FOREIGN KEY (run_id) REFERENCES midline_swing_runs(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO system_settings (setting_key, setting_value) VALUES
('gemini_midline_long_enabled', '0'),
('gemini_midline_short_enabled', '0'),
('deepseek_midline_long_enabled', '0'),
('deepseek_midline_short_enabled', '0');
