-- 为 ema_signals 表添加 volume_type 字段
-- 用于记录成交量类型：放量（>1）或缩量（<1）

ALTER TABLE `ema_signals` 
ADD COLUMN `volume_type` VARCHAR(10) NULL COMMENT '成交量类型：放量或缩量' 
AFTER `volume_ratio`;

-- 更新现有记录的 volume_type（根据 volume_ratio 判断）
UPDATE `ema_signals` 
SET `volume_type` = CASE 
    WHEN `volume_ratio` > 1 THEN '放量'
    WHEN `volume_ratio` < 1 THEN '缩量'
    ELSE '平量'
END
WHERE `volume_type` IS NULL;

