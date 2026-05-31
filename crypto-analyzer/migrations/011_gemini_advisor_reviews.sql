-- ============================================================
-- Migration 011: Gemini 顾问审核记录 (开仓 / 持仓)
-- 数据库: binance-data
-- ============================================================

CREATE TABLE IF NOT EXISTS `gemini_advisor_reviews` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `review_type` enum('open','hold') NOT NULL COMMENT '开仓审核 / 持仓审核',
  `decision` varchar(20) NOT NULL COMMENT 'open: approve|reject; hold: hold|observe|sell',
  `symbol` varchar(32) NOT NULL,
  `position_side` varchar(8) DEFAULT NULL,
  `source` varchar(64) DEFAULT NULL COMMENT '策略 source',
  `position_id` int(11) DEFAULT NULL COMMENT '持仓审核或开仓成功后的 id',
  `entry_price` decimal(20,8) DEFAULT NULL,
  `leverage` int(11) DEFAULT NULL,
  `hold_hours` decimal(10,2) DEFAULT NULL COMMENT '持仓审核时持仓时长',
  `roi_pct` decimal(10,2) DEFAULT NULL COMMENT '持仓审核时保证金 ROI%',
  `reason` varchar(500) DEFAULT NULL,
  `catalyst` varchar(500) DEFAULT NULL COMMENT '开仓理由/信号摘要',
  `extra_json` json DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `idx_review_type_created` (`review_type`, `created_at`),
  KEY `idx_symbol` (`symbol`),
  KEY `idx_position_id` (`position_id`),
  KEY `idx_decision` (`decision`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci
  COMMENT='Gemini 顾问审核记录';

INSERT INTO system_settings (setting_key, setting_value, description, updated_at)
VALUES (
  'gemini_open_advisor_enabled',
  '1',
  'Gemini 模拟开仓顾问: 1=开仓前审核, 不通过则不开仓',
  NOW()
)
ON DUPLICATE KEY UPDATE setting_key = setting_key;
