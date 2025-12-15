-- 028_add_entry_reason_to_futures_positions.sql
-- 添加开仓原因字段到 futures_positions 表
-- 用于记录每笔交易的详细开仓原因

ALTER TABLE futures_positions
ADD COLUMN entry_reason VARCHAR(500) NULL AFTER entry_signal_type;

-- 添加注释
-- entry_reason 保存详细的开仓原因，例如：
-- - 金叉/死叉信号: EMA9上穿EMA26, EMA差值:0.125%
-- - 连续趋势(5M放大): 连续趋势信号(long, 15M差值0.150%, 5M连续放大)
-- - 持续趋势入场(long): 趋势方向正确且强度足够
-- - 震荡反向信号: 震荡反向做多(连续4阴线+放量1.35)
