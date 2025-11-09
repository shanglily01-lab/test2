-- 修复 futures_orders 和 futures_trades 表中 side 字段长度不足的问题
-- 执行方法: mysql -u root -p binance-data < fix_futures_orders_side_length.sql

USE `binance-data`;

-- 修改 futures_orders 表的 side 字段长度
ALTER TABLE futures_orders 
MODIFY COLUMN side VARCHAR(20) NOT NULL COMMENT '订单方向: OPEN_LONG(开多), OPEN_SHORT(开空), CLOSE_LONG(平多), CLOSE_SHORT(平空)';

-- 修改 futures_trades 表的 side 字段长度
ALTER TABLE futures_trades 
MODIFY COLUMN side VARCHAR(20) NOT NULL COMMENT 'OPEN_LONG, OPEN_SHORT, CLOSE_LONG, CLOSE_SHORT';

