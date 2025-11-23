# 策略执行器启动说明

## ⚠️ 重要提示

**策略执行器必须运行，否则策略不会自动执行交易！**

## 快速启动

### Windows系统

双击运行：
```
start_strategy_scheduler.bat
```

或者在命令行运行：
```bash
python app/strategy_scheduler.py
```

### Linux/Mac系统

运行启动脚本：
```bash
chmod +x start_strategy_scheduler.sh
./start_strategy_scheduler.sh
```

或者直接运行：
```bash
python app/strategy_scheduler.py
```

## 后台运行（服务器推荐）

### 方法1：使用 screen（推荐）

```bash
# 创建新的screen会话
screen -S strategy

# 在screen中运行策略执行器
python app/strategy_scheduler.py

# 按 Ctrl+A 然后按 D 来分离会话（服务继续运行）
# 重新连接：screen -r strategy
```

### 方法2：使用 nohup

```bash
nohup python app/strategy_scheduler.py > logs/strategy_scheduler.log 2>&1 &
```

查看日志：
```bash
tail -f logs/strategy_scheduler.log
```

### 方法3：使用 systemd（Linux生产环境）

创建服务文件 `/etc/systemd/system/strategy-scheduler.service`：

```ini
[Unit]
Description=Strategy Scheduler Service
After=network.target mysql.service

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/crypto-analyzer
ExecStart=/usr/bin/python3 /path/to/crypto-analyzer/app/strategy_scheduler.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启动服务：
```bash
sudo systemctl daemon-reload
sudo systemctl enable strategy-scheduler
sudo systemctl start strategy-scheduler
sudo systemctl status strategy-scheduler
```

## 验证是否运行

### 1. 检查进程

**Linux/Mac:**
```bash
ps aux | grep strategy_scheduler
```

**Windows:**
```bash
tasklist | findstr python
```

### 2. 检查日志

查看日志文件，应该看到：
```
初始化策略执行器...
  ✓ 策略执行器初始化成功
🔄 策略实时监控服务已启动（间隔: 5秒）
找到 X 个启用的策略，开始检查...
```

### 3. 运行检查脚本

```bash
python scripts/check_server_status.py
```

## 常见问题

### Q: 策略执行器启动了，但没有订单？

**原因：**
1. 市场没有EMA交叉信号（这是正常的，需要等待信号）
2. 信号被过滤条件过滤掉了

**解决方法：**
- 运行诊断脚本：`python scripts/diagnose_strategy_signals.py`
- 检查策略命中记录：`python scripts/check_strategy_execution.py`

### Q: 如何确认策略执行器在运行？

运行检查脚本：
```bash
python scripts/check_server_status.py
```

### Q: 策略执行器意外停止怎么办？

使用 `systemd` 或 `supervisor` 配置自动重启，或者使用 `screen` 保持会话。

## 日志位置

- 日志文件：`logs/strategy_scheduler.log` 或 `logs/scheduler_YYYY-MM-DD.log`
- 实时查看：`tail -f logs/strategy_scheduler.log`

## 停止服务

### 前台运行
按 `Ctrl+C` 停止

### screen会话
```bash
screen -r strategy
# 然后按 Ctrl+C 停止
```

### systemd服务
```bash
sudo systemctl stop strategy-scheduler
```

### 查找并结束进程
```bash
# Linux/Mac
ps aux | grep strategy_scheduler
kill <PID>

# Windows
tasklist | findstr python
taskkill /PID <PID> /F
```

## 需要同时运行的服务

1. **策略执行器（必须）** ⭐
   - 执行交易策略，检测信号，自动交易
   - 运行：`python app/strategy_scheduler.py`

2. **数据采集器（可选）**
   - 采集市场数据（价格、K线等）
   - 运行：`python app/scheduler.py`
   - 如果已经有数据，可以不运行

3. **Web服务（可选）**
   - 提供Web界面和API
   - 运行：`uvicorn app.main:app --host 0.0.0.0 --port 8000`
   - 如果只需要自动交易，可以不运行

## 更多信息

详细部署指南请查看：`docs/服务器部署指南.md`

