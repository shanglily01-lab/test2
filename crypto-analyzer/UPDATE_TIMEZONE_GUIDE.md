# MySQL 时区设置指南

## 问题

MySQL 数据库当前使用 SYSTEM 时区（服务器时区），可能与应用程序期望的 UTC+8 不一致。

## 解决方案

### 方案 1: 修改 MySQL 配置文件（永久生效）

**适用场景**: 有 root 权限，可以重启 MySQL

1. SSH 连接到 MySQL 服务器
2. 编辑 MySQL 配置文件:
   ```bash
   sudo vi /etc/mysql/my.cnf
   # 或
   sudo vi /etc/my.cnf
   ```

3. 在 `[mysqld]` 部分添加:
   ```ini
   [mysqld]
   default-time-zone='+08:00'
   ```

4. 重启 MySQL:
   ```bash
   sudo systemctl restart mysql
   # 或
   sudo systemctl restart mysqld
   ```

5. 验证:
   ```bash
   mysql -u admin -p -e "SELECT @@global.time_zone, NOW();"
   ```

### 方案 2: 修改应用程序数据库连接（推荐）

**适用场景**: 没有 MySQL root 权限，只能修改应用代码

在每次数据库连接时设置会话时区。

#### 步骤 1: 修改 config.yaml

在 `config.yaml` 中添加时区配置:

```yaml
database:
  type: mysql
  mysql:
    host: ${DB_HOST:localhost}
    port: ${DB_PORT:3306}
    user: ${DB_USER:root}
    password: ${DB_PASSWORD:}
    database: ${DB_NAME:binance-data}
    charset: utf8mb4
    init_command: "SET time_zone='+08:00'"  # 添加这一行
```

#### 步骤 2: 修改所有 pymysql.connect 调用

**原来的代码**:
```python
conn = pymysql.connect(**db_config)
```

**修改后的代码**:
```python
conn = pymysql.connect(
    **db_config,
    init_command="SET time_zone='+08:00'"
)
```

或者更通用的方式:
```python
# 如果 db_config 中没有 init_command，则添加
if 'init_command' not in db_config:
    db_config['init_command'] = "SET time_zone='+08:00'"
conn = pymysql.connect(**db_config)
```

### 方案 3: 创建数据库连接包装函数

创建一个统一的数据库连接函数:

```python
# app/utils/db.py
import pymysql
from typing import Dict

def get_db_connection(db_config: Dict, timezone: str = '+08:00'):
    """
    创建数据库连接并设置时区

    Args:
        db_config: 数据库配置
        timezone: 时区设置 (默认: +08:00 即 UTC+8)

    Returns:
        数据库连接对象
    """
    config = db_config.copy()

    # 添加时区设置
    if 'init_command' not in config:
        config['init_command'] = f"SET time_zone='{timezone}'"

    return pymysql.connect(**config)
```

然后在所有地方使用这个函数:
```python
from app.utils.db import get_db_connection

# 原来
conn = pymysql.connect(**db_config)

# 改为
conn = get_db_connection(db_config)
```

## 验证时区设置

连接到数据库后执行:

```sql
SELECT
    @@session.time_zone AS '会话时区',
    NOW() AS '当前时间(UTC+8)',
    UTC_TIMESTAMP() AS 'UTC时间';
```

预期输出:
```
会话时区: +08:00
当前时间(UTC+8): 2025-12-10 18:00:00
UTC时间: 2025-12-10 10:00:00
```

## 受影响的功能

设置时区后，以下功能会受影响:

1. **NOW()** - 返回当前 UTC+8 时间
2. **CURRENT_TIMESTAMP()** - 返回当前 UTC+8 时间
3. **TIMESTAMPDIFF()** - 时间差计算
4. **created_at / updated_at** - 默认值使用 UTC+8

## 推荐方案

对于当前项目，推荐使用 **方案 2**，原因:

1. ✅ 不需要 MySQL root 权限
2. ✅ 不需要重启 MySQL
3. ✅ 只影响应用程序连接
4. ✅ 配置集中在 config.yaml
5. ✅ 易于调试和回滚

## 实施步骤

1. 修改 `config.yaml` 添加 `init_command`
2. 重启应用程序
3. 验证时区设置
4. 观察日志中的时间是否正确

## 注意事项

⚠️ **数据迁移考虑**:
- 如果数据库中已有数据，修改时区不会改变已存储的时间戳
- DATETIME 字段存储的是绝对时间，不受时区影响
- TIMESTAMP 字段存储的是 UTC 时间，显示时会根据时区转换

⚠️ **限价单超时问题**:
- 当前问题: `TIMESTAMPDIFF(SECOND, created_at, NOW())` 计算错误
- 原因: created_at 使用 UTC+8，但 NOW() 使用服务器时区 (UTC+0)
- 解决: 统一时区后，计算将正确
