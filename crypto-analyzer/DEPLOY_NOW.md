# 快速部署指南

## 已完成的修改

✅ **smart_trader_service.py** - 添加监控和自动重启功能
✅ **coin_futures_trader_service.py** - 添加监控和自动重启功能
✅ **语法检查通过** - 无编译错误

---

## 立即部署 (3步骤，5分钟)

### 步骤1: 提交到Git (1分钟)

```bash
cd d:/test2/crypto-analyzer

git add smart_trader_service.py coin_futures_trader_service.py
git commit -m "feat: 添加SmartExitOptimizer自动监控和重启机制"
git push origin master
```

### 步骤2: 部署到服务器 (2分钟)

```bash
# SSH到服务器
ssh user@your-server
cd /path/to/crypto-analyzer

# 拉取最新代码
git pull origin master

# 重启服务
pm2 restart smart_trader
pm2 restart coin_futures_trader
```

### 步骤3: 验证运行 (2分钟)

```bash
# 查看启动日志
pm2 logs smart_trader --lines 50

# 应该看到:
# ✅ 智能平仓优化器已启动
# ✅ 智能平仓监控已启动，统一监控 N 个持仓

# 等待1-2分钟，查看是否有健康检查日志
pm2 logs smart_trader --lines 100 | grep "健康检查"

# 如果当前有超时持仓，应该看到自动重启:
# ❌ SmartExitOptimizer异常: 发现N个超时未平仓持仓
# ========== 重启SmartExitOptimizer监控 ==========
# ✅ SmartExitOptimizer重启完成
```

---

## 预期效果

部署后，系统将自动:

1. **每分钟检查** SmartExitOptimizer监控状态
2. **发现问题立即重启** (监控丢失/超时持仓)
3. **1分钟内恢复** 所有持仓监控
4. **Telegram告警** 通知监控状态

---

## 当前超时持仓处理

如果数据库中还有超时持仓 (WCT, WLD, TRUMP, HYPE, G, DUSK)，部署后:

1. 服务重启后，`_start_smart_exit_monitoring()` 恢复监控
2. 监控恢复后，SmartExitOptimizer立即检测到超时
3. 1-2分钟内，所有超时持仓被强制平仓

**或者** (如果监控恢复失败):

1. 健康检查在1分钟内检测到超时持仓
2. 自动重启SmartExitOptimizer
3. 重启后，超时持仓被强制平仓

---

## 故障排查

### 如果看不到健康检查日志

**原因**: 可能10分钟内还没打印心跳日志

**解决**: 等待10分钟，或手动触发检查

### 如果重启失败

**检查日志**:
```bash
pm2 logs smart_trader --lines 200 | grep -E "ERROR|Exception"
```

**常见问题**:
1. event_loop 未初始化 - 检查服务启动逻辑
2. 数据库连接失败 - 检查数据库配置

---

## 回滚方案 (如果需要)

如果部署后出现问题，可以快速回滚:

```bash
# SSH到服务器
ssh user@your-server
cd /path/to/crypto-analyzer

# 回滚到上一个版本
git reset --hard HEAD~1

# 重启服务
pm2 restart smart_trader
pm2 restart coin_futures_trader
```

---

**准备就绪，可以立即部署！**
