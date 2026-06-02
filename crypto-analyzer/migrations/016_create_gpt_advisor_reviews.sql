-- ============================================================
-- Migration 016: 创建 GPT 顾问审核表
-- ============================================================

CREATE TABLE IF NOT EXISTS `gpt_advisor_reviews` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `review_type` varchar(20) NOT NULL COMMENT 'open|hold',
  `decision` varchar(20) NOT NULL COMMENT 'approve|reject|hold|sell|observe',
  `symbol` varchar(30) NOT NULL,
  `position_side` varchar(10) DEFAULT NULL,
  `source` varchar(64) DEFAULT NULL,
  `position_id` bigint(20) DEFAULT NULL,
  `entry_price` decimal(18,8) DEFAULT NULL,
  `leverage` int(11) DEFAULT NULL,
  `hold_hours` decimal(10,3) DEFAULT NULL,
  `roi_pct` decimal(10,4) DEFAULT NULL,
  `reason` text DEFAULT NULL,
  `catalyst` text DEFAULT NULL,
  `extra_json` mediumtext DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_created_at` (`created_at`),
  KEY `idx_symbol_created` (`symbol`,`created_at`),
  KEY `idx_type_created` (`review_type`,`created_at`),
  KEY `idx_decision_created` (`decision`,`created_at`),
  KEY `idx_source_created` (`source`,`created_at`),
  KEY `idx_position_id` (`position_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci COMMENT='GPT 顾问审核记录';

INSERT INTO system_settings (setting_key, setting_value, description, updated_at)
VALUES
  ('gpt_open_advisor_enabled', '1', 'GPT 模拟开仓顾问: 1=开仓前审核, 不通过则不开仓', NOW()),
  ('gpt_position_advisor_enabled', '1', 'GPT 模拟持仓顾问: gpt_* 仓 >=30min 每15min hold/observe/sell', NOW())
ON DUPLICATE KEY UPDATE setting_key = setting_key;
