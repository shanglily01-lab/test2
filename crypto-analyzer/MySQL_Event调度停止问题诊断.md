# MySQL Event 调度自动停止问题 - 诊断与修复

## 🔍 常见原因

### 1. Event Scheduler 全局开关关闭（最常见）

MySQL 重启后，Event Scheduler 可能不会自动启动。

#### 诊断命令
```sql
-- 检查 Event Scheduler 状态
SHOW VARIABLES LIKE 'event_scheduler';

-- 检查所有 Event 的状态
SHOW EVENTS;

-- 查看 Event 执行日志
SELECT * FROM mysql.event;
```

#### 解决方案
```sql
-- 临时启动（重启后失效）
SET GLOBAL event_scheduler = ON;

-- 永久启动（推荐）
-- 修改 my.cnf 或 my.ini
[mysqld]
event_scheduler = ON
```

### 2. Event 本身被禁用

Event 可能因为执行错误或手动禁用而停止。

#### 诊断命令
```sql
-- 查看所有 Event 及其状态
SELECT
    EVENT_SCHEMA,
    EVENT_NAME,
    STATUS,
    EVENT_TYPE,
    EXECUTE_AT,
    INTERVAL_VALUE,
    INTERVAL_FIELD,
    LAST_EXECUTED,
    STARTS,
    ENDS
FROM information_schema.EVENTS
WHERE EVENT_SCHEMA = 'your_database_name';
```

#### 解决方案
```sql
-- 启用特定 Event
ALTER EVENT event_name ENABLE;

-- 批量启用所有 Event
-- (需要逐个执行)
ALTER EVENT event1 ENABLE;
ALTER EVENT event2 ENABLE;
```

### 3. Event 执行出错

Event 内部的 SQL 出错会导致 Event 停止执行。

#### 诊断命令
```sql
-- 查看 Event 定义
SHOW CREATE EVENT event_name;

-- 手动执行 Event 中的 SQL，查看是否报错
-- (复制 Event 中的 SQL 语句手动执行)
```

#### 常见错误
- **表不存在**
- **权限不足**
- **语法错误**
- **死锁**

#### 解决方案
```sql
-- 修复 Event 定义
DROP EVENT IF EXISTS event_name;
CREATE EVENT event_name
ON SCHEDULE EVERY 1 HOUR
DO
BEGIN
    -- 添加错误处理
    DECLARE CONTINUE HANDLER FOR SQLEXCEPTION
    BEGIN
        -- 记录错误日志
        INSERT INTO event_error_log (event_name, error_time, error_msg)
        VALUES ('event_name', NOW(), CONCAT('Error: ', SQLSTATE));
    END;

    -- 你的 SQL 语句
    SELECT ...;
END;
```

### 4. 时区问题

Event 的执行时间基于服务器时区，可能与你的预期不符。

#### 诊断命令
```sql
-- 查看 MySQL 时区设置
SELECT @@global.time_zone, @@session.time_zone;

-- 查看当前时间
SELECT NOW(), UTC_TIMESTAMP();
```

#### 解决方案
```sql
-- 设置时区为 UTC
SET GLOBAL time_zone = '+00:00';

-- 或设置为本地时区
SET GLOBAL time_zone = '+08:00';  -- 中国时区

-- 修改 my.cnf
[mysqld]
default-time-zone = '+08:00'
```

### 5. 服务器重启

MySQL 服务器重启后，Event Scheduler 可能不会自动启动。

#### 解决方案
修改 MySQL 配置文件（永久生效）：

**Linux**: `/etc/mysql/my.cnf` 或 `/etc/my.cnf`
**Windows**: `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`

```ini
[mysqld]
event_scheduler = ON
```

重启 MySQL 服务：
```bash
# Linux
sudo systemctl restart mysql

# Windows
net stop MySQL80
net start MySQL80
```

### 6. 权限问题

执行 Event 的用户可能缺少必要权限。

#### 诊断命令
```sql
-- 查看当前用户权限
SHOW GRANTS FOR CURRENT_USER();

-- 查看 Event 定义者
SELECT DEFINER FROM information_schema.EVENTS
WHERE EVENT_NAME = 'event_name';
```

#### 解决方案
```sql
-- 授予 Event 权限
GRANT EVENT ON database_name.* TO 'user'@'host';
GRANT ALL PRIVILEGES ON database_name.* TO 'user'@'host';
FLUSH PRIVILEGES;
```

## 🛠️ 快速诊断脚本

创建一个诊断脚本 `diagnose_mysql_events.sql`:

```sql
-- ==================================================
-- MySQL Event 调度器诊断脚本
-- ==================================================

-- 1. 检查 Event Scheduler 全局状态
SELECT '=== Event Scheduler 全局状态 ===' AS '';
SHOW VARIABLES LIKE 'event_scheduler';

-- 2. 检查所有 Event
SELECT '=== 所有 Event 列表 ===' AS '';
SELECT
    EVENT_SCHEMA AS '数据库',
    EVENT_NAME AS 'Event名称',
    STATUS AS '状态',
    EVENT_TYPE AS '类型',
    EXECUTE_AT AS '执行时间',
    INTERVAL_VALUE AS '间隔值',
    INTERVAL_FIELD AS '间隔单位',
    LAST_EXECUTED AS '上次执行',
    STARTS AS '开始时间',
    ENDS AS '结束时间'
FROM information_schema.EVENTS
ORDER BY EVENT_SCHEMA, EVENT_NAME;

-- 3. 检查时区设置
SELECT '=== 时区设置 ===' AS '';
SELECT
    @@global.time_zone AS '全局时区',
    @@session.time_zone AS '会话时区',
    NOW() AS '当前时间',
    UTC_TIMESTAMP() AS 'UTC时间';

-- 4. 检查最近的 Event 执行记录（如果有日志表）
SELECT '=== 检查 Event 执行权限 ===' AS '';
SHOW GRANTS FOR CURRENT_USER();

-- 5. 建议操作
SELECT '=== 建议操作 ===' AS '';
SELECT
    CASE
        WHEN (SELECT VARIABLE_VALUE FROM information_schema.GLOBAL_VARIABLES
              WHERE VARIABLE_NAME = 'event_scheduler') = 'OFF'
        THEN '⚠️ Event Scheduler 已关闭！执行: SET GLOBAL event_scheduler = ON;'
        ELSE '✅ Event Scheduler 正在运行'
    END AS '状态检查';
```

## 🔧 修复脚本

创建修复脚本 `fix_mysql_events.sql`:

```sql
-- ==================================================
-- MySQL Event 调度器修复脚本
-- ==================================================

-- 1. 启动 Event Scheduler
SET GLOBAL event_scheduler = ON;

-- 2. 启用所有 Event（根据实际情况修改）
-- ALTER EVENT your_event_name1 ENABLE;
-- ALTER EVENT your_event_name2 ENABLE;

-- 3. 验证
SELECT
    EVENT_NAME,
    STATUS,
    LAST_EXECUTED
FROM information_schema.EVENTS
WHERE EVENT_SCHEMA = DATABASE();

-- 4. 确认 Event Scheduler 状态
SHOW VARIABLES LIKE 'event_scheduler';
```

## 📋 检查清单

使用此清单逐项检查：

- [ ] **Event Scheduler 是否启动？**
  ```sql
  SHOW VARIABLES LIKE 'event_scheduler';
  ```

- [ ] **Event 状态是否为 ENABLED？**
  ```sql
  SELECT EVENT_NAME, STATUS FROM information_schema.EVENTS;
  ```

- [ ] **Event 最后执行时间是否正常？**
  ```sql
  SELECT EVENT_NAME, LAST_EXECUTED FROM information_schema.EVENTS;
  ```

- [ ] **时区设置是否正确？**
  ```sql
  SELECT @@global.time_zone, NOW();
  ```

- [ ] **用户是否有 Event 权限？**
  ```sql
  SHOW GRANTS FOR CURRENT_USER();
  ```

- [ ] **my.cnf/my.ini 中是否配置了 event_scheduler=ON？**

## 🚀 推荐配置

### 完整的 my.cnf 配置

```ini
[mysqld]
# Event Scheduler
event_scheduler = ON

# 时区设置（根据实际情况调整）
default-time-zone = '+08:00'

# 连接超时设置（避免连接断开）
wait_timeout = 28800
interactive_timeout = 28800
```

### 创建 Event 的最佳实践

```sql
CREATE EVENT IF NOT EXISTS my_scheduled_task
ON SCHEDULE
    EVERY 1 HOUR
    STARTS CURRENT_TIMESTAMP
    ENDS CURRENT_TIMESTAMP + INTERVAL 1 YEAR
ON COMPLETION PRESERVE  -- 重要：执行完成后保留 Event
ENABLE
COMMENT '定时任务描述'
DO
BEGIN
    -- 错误处理
    DECLARE CONTINUE HANDLER FOR SQLEXCEPTION
    BEGIN
        -- 记录错误（需要预先创建错误日志表）
        INSERT IGNORE INTO event_error_log (event_name, error_time, error_msg)
        VALUES ('my_scheduled_task', NOW(), 'Error occurred');
    END;

    -- 实际任务
    -- 你的 SQL 语句

END;
```

## 📊 监控 Event 运行

### 创建监控表

```sql
-- 创建 Event 执行日志表
CREATE TABLE IF NOT EXISTS event_execution_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_name VARCHAR(64),
    execution_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    status ENUM('success', 'error') DEFAULT 'success',
    message TEXT,
    INDEX idx_event_time (event_name, execution_time)
) ENGINE=InnoDB;

-- 创建 Event 错误日志表
CREATE TABLE IF NOT EXISTS event_error_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    event_name VARCHAR(64),
    error_time DATETIME DEFAULT CURRENT_TIMESTAMP,
    error_msg TEXT,
    INDEX idx_event_error (event_name, error_time)
) ENGINE=InnoDB;
```

### 在 Event 中记录执行日志

```sql
CREATE EVENT my_task_with_logging
ON SCHEDULE EVERY 1 HOUR
DO
BEGIN
    DECLARE v_error INT DEFAULT 0;

    -- 错误处理
    DECLARE CONTINUE HANDLER FOR SQLEXCEPTION
    BEGIN
        SET v_error = 1;
        INSERT INTO event_error_log (event_name, error_msg)
        VALUES ('my_task_with_logging', 'Execution failed');
    END;

    -- 执行任务
    -- ... 你的 SQL ...

    -- 记录执行日志
    IF v_error = 0 THEN
        INSERT INTO event_execution_log (event_name, status, message)
        VALUES ('my_task_with_logging', 'success', 'Task completed successfully');
    ELSE
        INSERT INTO event_execution_log (event_name, status, message)
        VALUES ('my_task_with_logging', 'error', 'Task failed');
    END IF;
END;
```

## 🎯 常见场景解决方案

### 场景1: 半夜 Event 停止执行

**原因**: Event Scheduler 在 MySQL 重启后未自动启动

**解决方案**:
1. 修改 `my.cnf` 添加 `event_scheduler = ON`
2. 重启 MySQL 服务
3. 验证 Event Scheduler 状态

### 场景2: Event 显示 DISABLED

**原因**: Event 执行出错或手动禁用

**解决方案**:
```sql
-- 启用 Event
ALTER EVENT event_name ENABLE;

-- 检查 Event 定义是否正确
SHOW CREATE EVENT event_name;

-- 手动执行 Event 中的 SQL，查看是否报错
```

### 场景3: Event 一直不执行

**原因**: 时区设置错误或执行时间设置不正确

**解决方案**:
```sql
-- 检查当前时间
SELECT NOW(), UTC_TIMESTAMP();

-- 检查 Event 的下次执行时间
SELECT
    EVENT_NAME,
    EXECUTE_AT,
    STARTS,
    INTERVAL_VALUE,
    INTERVAL_FIELD
FROM information_schema.EVENTS
WHERE EVENT_NAME = 'your_event';

-- 修改 Event 执行时间
ALTER EVENT your_event
ON SCHEDULE EVERY 1 HOUR
STARTS CURRENT_TIMESTAMP;
```

## 📝 总结

1. ✅ **确保 Event Scheduler 启动** - 在 my.cnf 中永久配置
2. ✅ **启用所有需要的 Event** - 检查 STATUS 字段
3. ✅ **添加错误处理** - 防止 Event 因错误停止
4. ✅ **记录执行日志** - 便于监控和排查问题
5. ✅ **设置正确的时区** - 避免时间混淆
6. ✅ **定期检查** - 使用诊断脚本监控 Event 状态

---

**需要帮助?** 运行诊断脚本，将结果发给我，我可以帮你分析具体问题！
