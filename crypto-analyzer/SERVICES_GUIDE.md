# 超级大脑合约交易系统 - 服务指南

## 服务架构

系统由3个核心服务组成，通过统一的主启动脚本 `main.py` 进行管理。

### 1. 智能交易服务 (smart_trader_service.py)
**功能**:
- 实时信号检测
- 智能开仓/平仓决策
- WebSocket实时价格监控
- K线强度分析
- 分批建仓优化
- 智能止盈止损

**职责**: 核心交易逻辑和实时交易执行

---

### 2. K线采集服务 (fast_collector_service.py)
**功能**:
- 智能分层采集K线数据 (5m/15m/1h/1d)
- 每5分钟检查一次，根据周期智能决定是否采集
- 节省93.5%的无效采集
- 为K线强度分析提供持续的数据更新

**采集策略**:
- 5m K线: 每5分钟采集 (每次都采集)
- 15m K线: 每15分钟采集 (每3次采集1次)
- 1h K线: 每1小时采集 (每12次采集1次)
- 1d K线: 每1天采集 (每288次采集1次)

**职责**: 维护最新的K线数据，支撑信号分析

---

### 3. 每日优化服务 (app/services/daily_optimizer_service.py)
**功能**:
- 每天凌晨1点自动执行
- 分析最近7天的交易数据
- 自动调整参数（止盈/止损/仓位等）
- 生成优化报告和建议

**优化内容**:
- 胜率分析
- 盈亏比优化
- 参数调整建议
- 策略优化建议

**职责**: 系统自我优化和持续改进

---

## 启动方式

### 启动后端服务

系统包含3个后台服务和1个Web API服务：

#### 1. 智能交易服务
```bash
nohup python3 smart_trader_service.py > /tmp/trader.log 2>&1 &
echo "智能交易服务 PID: $!"
```

#### 2. K线采集服务
```bash
nohup python3 fast_collector_service.py > /tmp/collector.log 2>&1 &
echo "K线采集服务 PID: $!"
```

#### 3. 每日优化服务
```bash
nohup python3 app/services/daily_optimizer_service.py > /tmp/optimizer.log 2>&1 &
echo "每日优化服务 PID: $!"
```

#### 4. Web API服务（可选）
```bash
# 启动FastAPI Web界面
cd app
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

---

## 停止服务

### 统一停止

如果使用 `main.py` 启动：

```bash
# 前台运行时: 按 Ctrl+C

# 后台运行时:
ps aux | grep "main.py" | grep -v grep | awk '{print $2}' | xargs kill
```

### 单独停止

如果单独启动的服务：

```bash
# 停止智能交易服务
pkill -f smart_trader_service.py

# 停止K线采集服务
pkill -f fast_collector_service.py

# 停止每日优化服务
pkill -f daily_optimizer_service.py
```

---

## 监控日志

### 主服务日志

```bash
# 实时查看主服务日志
tail -f logs/main_YYYY-MM-DD.log

# 或者后台运行的输出
tail -f /tmp/main.log
```

### 各服务日志

```bash
# 智能交易服务
tail -f logs/trader_YYYY-MM-DD.log

# K线采集服务
tail -f logs/smart_collector_YYYY-MM-DD.log

# 每日优化服务
tail -f logs/daily_optimizer_YYYY-MM-DD.log
```

---

## 服务状态检查

```bash
# 查看所有服务进程
ps aux | grep -E "(smart_trader|fast_collector|daily_optimizer)" | grep -v grep

# 查看主服务进程
ps aux | grep "main.py" | grep -v grep
```

---

## 最佳实践

### 生产环境部署

1. **使用主启动脚本**
   ```bash
   cd /home/test2/crypto-analyzer
   nohup python3 main.py > /tmp/main.log 2>&1 &
   ```

2. **配置系统服务（可选）**
   创建 systemd 服务文件 `/etc/systemd/system/crypto-trader.service`:
   ```ini
   [Unit]
   Description=Crypto Trading System
   After=network.target

   [Service]
   Type=simple
   User=test2
   WorkingDirectory=/home/test2/crypto-analyzer
   ExecStart=/usr/bin/python3 main.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

   启动服务:
   ```bash
   sudo systemctl enable crypto-trader
   sudo systemctl start crypto-trader
   sudo systemctl status crypto-trader
   ```

3. **定期检查日志**
   ```bash
   # 每天检查一次
   tail -100 logs/main_$(date +%Y-%m-%d).log
   ```

---

## 故障排查

### 服务无法启动

1. 检查依赖是否安装
   ```bash
   pip list | grep -E "(ccxt|loguru|schedule)"
   ```

2. 检查配置文件
   ```bash
   cat config.yaml | grep -A 10 "database:"
   ```

3. 检查数据库连接
   ```bash
   mysql -h$DB_HOST -P$DB_PORT -u$DB_USER -p$DB_PASSWORD -e "SHOW DATABASES;"
   ```

### 服务自动重启

如果发现服务频繁重启，检查对应的日志文件找出原因。

### 优化服务未执行

- 检查 `daily_optimizer_service.py` 是否正常运行
- 查看日志确认定时任务是否触发
- 默认执行时间: 每天 01:00

---

## 更新说明

**v2.3 (2026-01-27)**:
- 新增统一主启动脚本 `main.py`
- 将每日优化独立为单独服务
- 支持自动监控和重启
- 优化日志管理

**v2.2 (2026-01-27)**:
- 新增K线强度评分系统
- 智能分批建仓优化
- K线强度平仓监控

---

## 联系方式

如有问题，请查看日志文件或联系开发团队。
