# MySQL 连接断开问题修复指南

## 问题描述

系统在长时间运行（特别是半夜）时，MySQL 连接会因为空闲超时而断开，导致调度任务停止运行。

### 根本原因

1. **MySQL 服务器超时设置**: MySQL 默认 `wait_timeout` 为 28800 秒（8小时），超过这个时间的空闲连接会被服务器关闭
2. **连接未保活**: 应用层没有定期 ping 连接或使用连接池
3. **未处理重连**: 代码中缺少连接断开后的自动重连机制

## ✅ 解决方案

### 方案1: 使用连接池（推荐）

已创建 `app/database/connection_pool.py` 连接池管理器，提供：

- ✅ 自动连接保活
- ✅ 连接健康检查
- ✅ 自动重连机制
- ✅ 线程安全

#### 基本用法

```python
from app.database.connection_pool import get_db_connection

# 方法1: 使用上下文管理器（推荐）
with get_db_connection(db_config) as conn:
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM table")
    results = cursor.fetchall()
```

#### 在现有代码中使用

**修改前:**
```python
import pymysql

def some_function():
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()
    cursor.execute("SELECT 1")
    result = cursor.fetchone()
    conn.close()
    return result
```

**修改后:**
```python
from app.database.connection_pool import get_db_connection

def some_function():
    with get_db_connection(db_config) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
    return result
```

### 方案2: OptimizationConfig 已包含重连逻辑

`OptimizationConfig` 类已经实现了 `ping(reconnect=True)` 机制：

```python
def _get_connection(self):
    """获取数据库连接"""
    if self.connection is None or not self.connection.open:
        self.connection = pymysql.connect(**self.db_config, ...)
    else:
        try:
            self.connection.ping(reconnect=True)  # 自动重连
        except:
            self.connection = pymysql.connect(**self.db_config, ...)
    return self.connection
```

### 方案3: 使用增强连接包装器

```python
from app.database.connection_pool import RobustConnection

# 创建增强连接
robust_conn = RobustConnection(db_config)

# 自动处理重连
results = robust_conn.execute("SELECT * FROM table")

# 提交事务
robust_conn.execute("INSERT INTO ...", params=(...), commit=True)

# 关闭
robust_conn.close()
```

## 🔧 修改现有服务

### 需要修改的关键文件

#### 1. smart_trader_service.py

**修改位置**: 数据库查询部分

```python
# 修改前
conn = pymysql.connect(**self.db_config)

# 修改后
from app.database.connection_pool import get_db_connection
with get_db_connection(self.db_config) as conn:
    # ... 使用连接
```

#### 2. coin_futures_trader_service.py

同样的修改方式

#### 3. 所有定时任务和后台服务

所有长时间运行的服务都应该使用连接池或增强连接

## 🎯 推荐的修改优先级

### 高优先级（立即修改）

1. **定时任务服务**
   - `smart_trader_service.py`
   - `coin_futures_trader_service.py`
   - `app/services/signal_analysis_background_service.py`

2. **后台监控服务**
   - `app/services/live_order_monitor.py`
   - `app/services/big4_emergency_monitor.py`

### 中优先级（逐步修改）

3. **API 接口**
   - `app/api/*.py` 中的所有 API

4. **数据采集服务**
   - `app/collectors/*.py`

### 低优先级（可选）

5. **一次性脚本**
   - 各种独立脚本（运行时间短，影响小）

## 💡 最佳实践

### 1. 统一使用连接池

```python
# 在主服务初始化时创建全局连接池
from app.database.connection_pool import get_global_pool

class SomeService:
    def __init__(self, db_config):
        self.pool = get_global_pool(db_config, pool_size=10)

    def query_data(self):
        with self.pool.get_connection() as conn:
            cursor = conn.cursor()
            # ... 执行查询
```

### 2. 添加重试机制

```python
from tenacity import retry, stop_after_attempt, wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def query_with_retry():
    with get_db_connection(db_config) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ...")
        return cursor.fetchall()
```

### 3. 异常处理

```python
try:
    with get_db_connection(db_config) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ...")
except pymysql.OperationalError as e:
    logger.error(f"数据库操作错误: {e}")
    # 连接池会自动处理重连
except Exception as e:
    logger.error(f"未知错误: {e}")
```

## 🚀 快速修复脚本

我已经创建了一个示例脚本，展示如何修改现有服务：

```python
# 示例: 修改 smart_trader_service.py 中的数据库连接

# 在文件顶部添加导入
from app.database.connection_pool import get_global_pool, get_db_connection

# 在 __init__ 方法中初始化连接池
def __init__(self, ...):
    # ... 其他初始化
    self.db_pool = get_global_pool(self.db_config, pool_size=10)

# 修改所有数据库查询
def some_query_method(self):
    with self.db_pool.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT ...")
        return cursor.fetchall()
```

## 📊 监控建议

添加连接池监控：

```python
import logging

# 定期检查连接池状态
def check_pool_health():
    pool = get_global_pool()
    logger.info(f"连接池状态: 可用连接数={len(pool.connections)}")
```

## ⚠️ 注意事项

1. **不要在连接池外长时间持有连接** - 总是使用 `with` 语句
2. **事务要及时提交或回滚** - 避免长事务
3. **合理设置连接池大小** - 根据并发需求调整（推荐 5-20）
4. **定期监控连接池状态** - 确保连接健康

## 🔍 故障排查

### 问题: 仍然出现连接断开

**检查项:**
1. MySQL 服务器 `wait_timeout` 设置（建议 ≥ 28800）
2. 连接池是否正确初始化
3. 是否有代码绕过连接池直接创建连接

**查看 MySQL 超时设置:**
```sql
SHOW VARIABLES LIKE '%timeout%';
```

**调整 MySQL 超时（可选）:**
```sql
SET GLOBAL wait_timeout = 28800;
SET GLOBAL interactive_timeout = 28800;
```

### 问题: 连接池耗尽

**解决方法:**
1. 增加连接池大小
2. 检查是否有未释放的连接
3. 使用 `with` 语句确保连接归还

## 📝 总结

| 方案 | 优点 | 缺点 | 适用场景 |
|------|------|------|----------|
| 连接池 | 性能好、自动管理 | 需要修改代码 | **长期运行的服务**（推荐） |
| ping(reconnect=True) | 简单、代码改动小 | 每次都需要 ping | 已有持久连接的服务 |
| 增强连接包装器 | 自动重连、易用 | 每次都创建新连接 | 独立脚本、临时任务 |

**推荐方案**: 对于长期运行的服务，统一使用连接池（方案1）

---

**需要帮助?**
- 查看 `app/database/connection_pool.py` 中的完整实现
- 参考本文档中的示例代码
- 逐步修改现有服务，优先处理关键服务
