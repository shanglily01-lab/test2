# Windows 500错误诊断和修复指南

## 问题症状
- 所有API端点返回500错误
- `/api/dashboard` - 500
- `/api/ema-signals` - 500
- `/api/corporate-treasury/summary` - 500
- Dashboard页面能打开但无数据

## 根本原因
**数据库连接失败** - 所有500错误都是因为无法连接到MySQL数据库

---

## 🔍 步骤1：诊断数据库连接

### 1.1 拉取最新代码
```bash
git pull
```

### 1.2 测试数据库连接
```bash
python test_db_connection.py
```

**预期输出（成功）：**
```
============================================================
数据库连接测试
============================================================

数据库配置:
  Host: localhost
  Port: 3306
  User: root
  Database: binance-data

正在连接到 MySQL...
✅ 数据库连接成功！

检查数据表:
  ✅ price_data: 1,234 条记录
  ✅ futures_data: 567 条记录
  ...
```

**如果失败，会显示具体原因：**

---

## ❌ 常见错误和解决方案

### 错误1：MySQL服务未启动

**错误信息：**
```
❌ 数据库连接失败: (2003, "Can't connect to MySQL server on 'localhost' (10061)")
```

**解决方案：**
1. 打开"服务"管理器（Win+R → `services.msc`）
2. 找到 `MySQL` 或 `MySQL80` 服务
3. 右键 → 启动
4. 确认状态变为"正在运行"

**或者使用命令行：**
```bash
# 以管理员身份运行CMD
net start MySQL80
```

---

### 错误2：密码错误

**错误信息：**
```
❌ 数据库连接失败: (1045, "Access denied for user 'root'@'localhost' (using password: YES)")
```

**解决方案：**
1. 打开 `config.yaml`
2. 修改数据库密码：
   ```yaml
   database:
     mysql:
       password: "你的实际MySQL密码"
   ```
3. 保存文件
4. 重新运行测试

---

### 错误3：数据库不存在

**错误信息：**
```
❌ 数据库连接失败: (1049, "Unknown database 'binance-data'")
```

**解决方案：**

#### 方法A：使用MySQL Workbench
1. 打开MySQL Workbench
2. 连接到localhost
3. 点击"Create Schema" (创建架构)
4. 名称输入：`binance-data`
5. 点击Apply

#### 方法B：使用命令行
```bash
mysql -u root -p
# 输入密码后：
CREATE DATABASE `binance-data` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
SHOW DATABASES;  # 确认创建成功
EXIT;
```

---

### 错误4：表不存在

**测试输出：**
```
检查数据表:
  ❌ price_data: 表不存在
  ❌ ema_signals: 表不存在
  ...
```

**解决方案：运行数据库迁移**
```bash
# 执行所有迁移脚本
mysql -u root -p binance-data < scripts/migrations/001_create_cache_tables.sql
mysql -u root -p binance-data < scripts/migrations/002_create_ema_signals_table.sql

# 或者让系统自动创建（启动服务器时会自动创建基础表）
python app/main.py
```

---

### 错误5：表存在但数据为空

**测试输出：**
```
检查数据表:
  ⚠️  price_data: 0 条记录
  ⚠️  futures_data: 0 条记录
```

**这不影响服务器启动，但Dashboard会显示"暂无数据"**

**解决方案：运行数据采集**
```bash
# 采集价格数据
python scripts/collectors/collect_prices.py

# 或运行完整的调度器
python scheduler.py
```

---

## ✅ 步骤2：修复后重启服务器

### 2.1 确认数据库连接成功
```bash
python test_db_connection.py
```
应该显示 ✅ 数据库连接成功

### 2.2 重启服务器
```bash
python app/main.py
```

**检查启动日志，确认这些行：**
```
✅ 配置文件加载成功
✅ 价格缓存服务已启动
✅ 策略管理API路由已注册
✅ 模拟交易API路由已注册
✅ 主API路由已注册
📁 静态文件目录: C:\...\crypto-analyzer\static
📁 目录存在: True
✅ 静态文件目录已挂载: /static
启动FastAPI服务器...
INFO:     Uvicorn running on http://0.0.0.0:8000
```

**如果看到这些错误，说明数据库还是有问题：**
```
❌ 数据库初始化失败
ERROR: Access denied for user 'root'@'localhost'
```

### 2.3 测试API端点

打开浏览器访问：
```
http://localhost:8000/health
```

**应该返回：**
```json
{
  "status": "healthy",
  "modules": {...}
}
```

**如果还是500错误，检查服务器日志中的错误信息**

### 2.4 测试Dashboard
```
http://localhost:8000/dashboard
```

按F12打开开发者工具：
- **Network标签**：所有请求应该是200（不是500）
- **Console标签**：应该没有"500 Internal Server Error"

---

## 🎯 快速修复清单

- [ ] MySQL服务正在运行
- [ ] `config.yaml` 中的数据库密码正确
- [ ] 数据库 `binance-data` 存在
- [ ] 运行 `python test_db_connection.py` 成功
- [ ] 服务器启动无错误
- [ ] http://localhost:8000/health 返回200
- [ ] Dashboard能打开且Network标签无500错误

---

## 📞 如果还有问题

提供以下信息：

1. **test_db_connection.py 的完整输出**
2. **服务器启动日志（python app/main.py 的所有输出）**
3. **浏览器Console中的错误信息**
4. **浏览器Network标签中任一500错误的Response内容**

---

## 💡 为什么数据库这么重要？

所有这些API端点都需要数据库：
- `/api/dashboard` - 查询 price_data, futures_data, investment_recommendations, news
- `/api/ema-signals` - 查询 ema_signals 表
- `/api/corporate-treasury/summary` - 查询 corporate_treasury 表

**如果数据库连接失败，这些端点都会返回500错误**

这就是为什么修复数据库连接是解决所有500错误的关键！
