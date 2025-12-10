-- ============================================================
-- 添加 cancellation_reason 列到 futures_orders 表
-- ============================================================

USE `binance-data`;

-- 添加取消原因列
ALTER TABLE futures_orders
ADD COLUMN cancellation_reason VARCHAR(100) DEFAULT NULL COMMENT '取消原因: manual(手动取消)/strategy_signal(策略信号取消)/risk_control(风控取消)/system(系统取消)/expired(订单过期)' AFTER notes;

-- 验证
DESCRIBE futures_orders;

SELECT '已添加 cancellation_reason 列到 futures_orders 表！' AS message;
