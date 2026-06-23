-- 手动白名单/黑名单锁定：自动评级刷新不得覆盖 rating_locked=1 的行
-- MariaDB 10.5：若列已存在会报错，可忽略

ALTER TABLE trading_symbol_rating
  ADD COLUMN rating_locked TINYINT(1) NOT NULL DEFAULT 0
    COMMENT '1=手动锁定，定时评级不覆盖等级'
    AFTER level_change_reason;
