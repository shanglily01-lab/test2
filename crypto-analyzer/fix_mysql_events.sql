-- ==================================================
-- MySQL Event Scheduler 快速修复脚本
-- 使用方法: mysql -u root -p < fix_mysql_events.sql
-- 警告: 请先运行 diagnose_mysql_events.sql 确认问题
-- ==================================================

SELECT '
================================================================================
开始修复 MySQL Event Scheduler
================================================================================
' AS '';

-- 1. 启动 Event Scheduler
SELECT '【步骤1】启动 Event Scheduler...' AS '';
SET GLOBAL event_scheduler = ON;

SELECT
    CASE
        WHEN VARIABLE_VALUE = 'ON' THEN '✅ Event Scheduler 已成功启动'
        ELSE '❌ 启动失败，请检查权限'
    END AS '结果'
FROM information_schema.GLOBAL_VARIABLES
WHERE VARIABLE_NAME = 'event_scheduler';

-- 2. 显示所有被禁用的 Event
SELECT '
【步骤2】检查被禁用的 Event...' AS '';

SELECT
    CONCAT('ALTER EVENT `', EVENT_SCHEMA, '`.`', EVENT_NAME, '` ENABLE;') AS '修复命令',
    EVENT_NAME AS 'Event名称',
    STATUS AS '当前状态'
FROM information_schema.EVENTS
WHERE STATUS = 'DISABLED';

-- 提示: 需要手动执行上述 ALTER EVENT 命令来启用被禁用的 Event
SELECT '
⚠️ 注意: 请手动复制并执行上述 ALTER EVENT 命令来启用被禁用的 Event
或者根据实际情况选择性启用
' AS '';

-- 3. 设置时区（可选，根据实际情况决定是否执行）
SELECT '
【步骤3】设置时区（可选）...' AS '';

-- 如果需要设置为中国时区，取消下面的注释
-- SET GLOBAL time_zone = '+08:00';

SELECT
    @@global.time_zone AS '当前全局时区',
    NOW() AS '当前时间',
    UTC_TIMESTAMP() AS 'UTC时间';

-- 4. 验证修复结果
SELECT '
【步骤4】验证修复结果...' AS '';

SELECT
    EVENT_NAME AS 'Event名称',
    STATUS AS '状态',
    CASE
        WHEN STATUS = 'ENABLED' THEN '✅'
        ELSE '❌'
    END AS '图标',
    LAST_EXECUTED AS '上次执行',
    CASE
        WHEN EVENT_TYPE = 'ONE TIME' THEN EXECUTE_AT
        ELSE CONCAT('EVERY ', INTERVAL_VALUE, ' ', INTERVAL_FIELD)
    END AS '执行计划'
FROM information_schema.EVENTS
WHERE EVENT_SCHEMA = DATABASE()
   OR EVENT_SCHEMA IN (
       SELECT DISTINCT EVENT_SCHEMA
       FROM information_schema.EVENTS
       WHERE EVENT_SCHEMA NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
   )
ORDER BY EVENT_SCHEMA, EVENT_NAME;

-- 5. 最终检查
SELECT '
【步骤5】最终健康检查...' AS '';

SELECT
    '✅ Event Scheduler 状态' AS '检查项',
    (SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_VARIABLES
     WHERE VARIABLE_NAME = 'event_scheduler') AS '当前值'
UNION ALL
SELECT
    '✅ 启用的 Event 数量',
    CAST(COUNT(*) AS CHAR)
FROM information_schema.EVENTS
WHERE STATUS = 'ENABLED'
UNION ALL
SELECT
    '❌ 禁用的 Event 数量',
    CAST(COUNT(*) AS CHAR)
FROM information_schema.EVENTS
WHERE STATUS = 'DISABLED';

SELECT '
================================================================================
修复完成！
================================================================================

【重要提示】
1. Event Scheduler 当前会话已启动，但重启 MySQL 后会失效
2. 要永久启动，请在 my.cnf 或 my.ini 中添加：
   [mysqld]
   event_scheduler = ON

3. 然后重启 MySQL 服务：
   Linux: sudo systemctl restart mysql
   Windows: net stop MySQL80 && net start MySQL80

4. 如有被禁用的 Event，请手动执行 ALTER EVENT 命令启用

【下一步】
- 监控 Event 执行情况
- 定期运行诊断脚本检查健康状态
- 考虑添加 Event 执行日志记录

================================================================================
' AS '';
