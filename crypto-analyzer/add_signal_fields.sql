-- 添加信号评分相关字段到 futures_positions 表
-- 如果字段已存在会报错，这是正常的

-- 添加 entry_score 字段
ALTER TABLE futures_positions
ADD COLUMN entry_score INT COMMENT '开仓得分' AFTER entry_signal_type;

-- 添加 signal_components 字段
ALTER TABLE futures_positions
ADD COLUMN signal_components TEXT COMMENT '信号组成（JSON格式）' AFTER entry_score;

-- 验证字段是否添加成功
SHOW COLUMNS FROM futures_positions LIKE 'entry_score';
SHOW COLUMNS FROM futures_positions LIKE 'signal_components';
