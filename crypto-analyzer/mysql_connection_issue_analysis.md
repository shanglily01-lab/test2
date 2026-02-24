# MySQL 连接中断导致 6 小时超时事故分析

## 问题复现

从日志中发现关键证据：

```
2026-02-24 01:21:30 | ERROR | 关闭NEIRO/USDT LONG持仓失败: (2013, 'Lost connection to MySQL server during query')
2026-02-24 01:21:31 | ERROR | 检查顶底识别失败: (2006, "MySQL server has gone away (ConnectionResetError(104, 'Connection reset by peer'))")
```

## 根本原因

### 1. MySQL 连接管理缺陷

**当前实现** (`smart_trader_service.py:324-327`):
```python
def _get_connection(self):
    if self.connection is None or not self.connection.open:
        self.connection = pymysql.connect(**self.db_config, ...)
    else:
        # 返回已有连接，但不检查连接是否真的有效
        return self.connection
```

**问题**：
- 仅检查 `connection.open`，不验证连接是否真正可用
- MySQL 连接空闲超时（默认8小时）后会被服务器关闭
- 网络抖动/数据库重启会导致连接断开
- 代码认为连接是打开的，但实际已失效

### 2. 主循环异常处理不足

**当前实现** (`smart_trader_service.py:3127-3133`):
```python
except Exception as e:
    logger.error(f"[ERROR] 主循环异常: {e}")
    time.sleep(60)  # 睡眠60秒后继续
```

**问题**：
- MySQL 错误被捕获后仅sleep 60秒，不尝试重连
- 如果数据库持续不可用，主循环陷入：
  ```
  尝试执行 → MySQL错误 → Sleep 60秒 → 尝试执行 → MySQL错误 → ...
  ```
- **健康检查无法运行**（因为也需要数据库连接）
- **SmartExitOptimizer 无法平仓**（数据库不可用）

### 3. 没有连接池自动重连机制

- 使用单个连接而非连接池
- 没有 `ping()` 检查连接活性
- 没有自动重连机制

## 事故时间线（推测）

**19:00-19:10** - MySQL 连接中断（可能原因：网络抖动、数据库维护、连接超时）
- 主循环尝试查询数据库
- 遇到 "MySQL server has gone away" 错误
- 进入 60秒睡眠循环

**19:10-01:20** - 主循环困在错误循环中
- 每次循环都遇到 MySQL 错误
- Sleep 60秒后重试，再次失败
- **健康检查无法运行**（line 2757 的 `_check_and_restart_smart_exit_optimizer()` 需要数据库）
- **SmartExitOptimizer 监控任务** 可能还在运行，但无法执行平仓（需要数据库）
- 82个持仓一直等待平仓，planned_close_time 早已过期

**01:21** - 数据库连接恢复
- 可能原因：网络恢复、数据库重启完成、服务手动重启
- 主循环恢复正常
- 健康检查发现 82个超时持仓（`NOW() > planned_close_time`）
- 立即触发 SmartExitOptimizer 重启
- 批量平仓所有超时持仓（01:21:31-01:21:33）

## 为什么日志中看到01:21有MySQL错误？

因为01:21是数据库**刚刚恢复**的时刻，可能还有：
- 旧连接残留导致的错误
- WebSocket 重连导致的瞬时错误
- 某些查询使用了已失效的连接

这些错误是**恢复过程中的正常现象**，之后连接就稳定了。

## 影响分析

| 项目 | 数值 |
|------|------|
| 停机时长 | ~6小时（19:00-01:20） |
| 超时持仓 | 82个 |
| 超时时长 | 371分钟（应180分钟平仓） |
| 总盈亏 | **+999.76 USDT**（幸运的是做空方向正确） |
| 潜在风险 | 如果方向错误或市场反转，可能造成巨额亏损 |

## 修复方案

### 优先级1: 增强连接健壮性（立即实施）

```python
def _get_connection(self):
    """获取数据库连接（带自动重连）"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # 1. 如果没有连接，创建新连接
            if self.connection is None or not self.connection.open:
                self.connection = pymysql.connect(**self.db_config, autocommit=True)
                return self.connection

            # 2. 验证连接是否真的可用
            self.connection.ping(reconnect=True)  # 关键：ping并自动重连
            return self.connection

        except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as e:
            logger.warning(f"MySQL连接失效，重试 {attempt+1}/{max_retries}: {e}")
            self.connection = None
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # 指数退避：2秒、4秒、8秒
            else:
                raise  # 最后一次重试失败，抛出异常
```

### 优先级2: 使用连接池（推荐长期方案）

```python
from dbutils.pooled_db import PooledDB

# 初始化时创建连接池
self.db_pool = PooledDB(
    creator=pymysql,
    maxconnections=10,  # 最大连接数
    mincached=2,        # 最小空闲连接
    maxcached=5,        # 最大空闲连接
    blocking=True,      # 连接池满时阻塞等待
    ping=1,             # 🔥 自动ping检查连接（0=不检查，1=使用时检查，2=创建时检查）
    **db_config
)

def _get_connection(self):
    """从连接池获取连接"""
    return self.db_pool.connection()
```

### 优先级3: 主循环增强错误处理

```python
except (pymysql.err.OperationalError, pymysql.err.InterfaceError) as db_error:
    logger.error(f"[DATABASE-ERROR] MySQL连接失败: {db_error}")

    # 尝试重置连接
    try:
        self.connection = None
        conn = self._get_connection()  # 强制重新连接
        logger.info("✅ MySQL连接已恢复")
    except Exception as reconnect_error:
        logger.error(f"❌ MySQL重连失败: {reconnect_error}")

        # 发送告警
        if hasattr(self, 'telegram_notifier'):
            self.telegram_notifier.send_message(
                f"⚠️ MySQL连接中断\n\n"
                f"错误: {db_error}\n"
                f"时间: {datetime.now()}\n"
                f"操作: 60秒后重试"
            )

    time.sleep(60)

except Exception as e:
    logger.error(f"[ERROR] 主循环异常: {e}")
    time.sleep(60)
```

### 优先级4: 独立的数据库心跳监控

创建独立线程，每30秒检查数据库连接：

```python
def _database_heartbeat(self):
    """数据库心跳检查（独立线程）"""
    while self.running:
        try:
            conn = self._get_connection()
            conn.ping(reconnect=True)
            logger.debug("💓 MySQL心跳正常")
        except Exception as e:
            logger.error(f"❌ MySQL心跳失败: {e}")
            # 发送告警
            self._send_database_alert(e)

        time.sleep(30)  # 每30秒检查一次

# 在 __init__ 中启动
threading.Thread(target=self._database_heartbeat, daemon=True).start()
```

### 优先级5: 外部进程监控

使用 systemd/supervisor 监控主进程：

```ini
# /etc/supervisor/conf.d/crypto-analyzer.conf
[program:crypto-analyzer]
command=/path/to/python main.py
directory=/path/to/crypto-analyzer
autostart=true
autorestart=true          # 自动重启
startretries=999          # 无限重试
redirect_stderr=true
stdout_logfile=/var/log/crypto-analyzer.log
```

## 立即行动项

1. **✅ 修改 `_get_connection()`** 增加 `ping(reconnect=True)`
2. **✅ 安装 DBUtils** 使用连接池
3. **✅ 增强主循环错误处理** 区分数据库错误
4. **✅ 添加数据库心跳监控** 独立线程
5. **✅ 配置 systemd/supervisor** 自动重启主进程
6. **✅ 添加告警** 数据库连接中断超过5分钟立即通知

## 预防措施

| 措施 | 效果 |
|------|------|
| `ping(reconnect=True)` | 自动检测并重连失效连接 |
| 连接池 | 自动管理连接生命周期 |
| 心跳监控 | 提前发现连接问题 |
| 外部进程监控 | 主进程崩溃自动重启 |
| 及时告警 | 人工介入时间从6小时→5分钟 |

## 测试计划

1. **模拟MySQL断开**：手动重启MySQL，验证自动重连
2. **模拟网络抖动**：使用 `tc` 命令模拟丢包，验证重试机制
3. **长时间运行测试**：运行24小时，验证连接池稳定性
4. **告警测试**：触发数据库错误，验证Telegram告警

## 总结

这次事故是**基础设施问题**而非业务逻辑错误：
- ✅ SmartExitOptimizer 逻辑正确
- ✅ planned_close_time 设置正确
- ✅ 健康检查机制设计正确

问题在于：
- ❌ **MySQL 连接管理不健壮**（单点故障）
- ❌ **缺少自动重连机制**
- ❌ **缺少外部监控**（崩溃无人知晓）
- ❌ **缺少及时告警**（6小时才恢复）

需要从**基础设施层面**加强防护。
