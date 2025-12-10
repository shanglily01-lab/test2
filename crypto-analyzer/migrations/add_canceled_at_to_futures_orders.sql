-- 添加 canceled_at 字段到 futures_orders 表
-- 用于记录订单取消的时间

ALTER TABLE futures_orders
ADD COLUMN canceled_at DATETIME NULL DEFAULT NULL
COMMENT '订单取消时间'
AFTER updated_at;

-- 为已取消的订单回填时间（使用 updated_at 作为估算）
UPDATE futures_orders
SET canceled_at = updated_at
WHERE status = 'CANCELLED' AND canceled_at IS NULL;
