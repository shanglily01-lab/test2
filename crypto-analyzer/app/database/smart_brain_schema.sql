-- 超级大脑优化所需的数据库扩展
-- 为 futures_positions 表添加新字段

-- 分批建仓相关字段
ALTER TABLE futures_positions
ADD COLUMN IF NOT EXISTS batch_plan JSON COMMENT '分批建仓计划 {"batches": [{"ratio": 0.3, "filled": false, "price": null}]}',
ADD COLUMN IF NOT EXISTS batch_filled JSON COMMENT '已完成批次记录',
ADD COLUMN IF NOT EXISTS entry_signal_time DATETIME COMMENT '信号发出时间',
ADD COLUMN IF NOT EXISTS avg_entry_price DECIMAL(20,8) COMMENT '平均入场价（加权平均）';

-- 平仓优化相关字段
ALTER TABLE futures_positions
ADD COLUMN IF NOT EXISTS planned_close_time DATETIME COMMENT '计划平仓时间',
ADD COLUMN IF NOT EXISTS close_extended BOOLEAN DEFAULT FALSE COMMENT '是否延长平仓时间',
ADD COLUMN IF NOT EXISTS extended_close_time DATETIME COMMENT '延长后的平仓时间',
ADD COLUMN IF NOT EXISTS max_profit_pct DECIMAL(10,4) COMMENT '最高盈利百分比（用于回撤止盈）',
ADD COLUMN IF NOT EXISTS max_profit_price DECIMAL(20,8) COMMENT '最高盈利时的价格',
ADD COLUMN IF NOT EXISTS max_profit_time DATETIME COMMENT '达到最高盈利的时间';

-- 添加索引优化查询
CREATE INDEX IF NOT EXISTS idx_entry_signal_time ON futures_positions(entry_signal_time);
CREATE INDEX IF NOT EXISTS idx_planned_close_time ON futures_positions(planned_close_time);
CREATE INDEX IF NOT EXISTS idx_close_extended ON futures_positions(close_extended);

-- 注释说明
ALTER TABLE futures_positions COMMENT = '合约持仓表（支持超级大脑智能建仓/平仓优化）';
