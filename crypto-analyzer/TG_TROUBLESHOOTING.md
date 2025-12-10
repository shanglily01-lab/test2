# Telegram通知问题排查指南

## 问题描述

您遇到的问题：**模拟合约交易没有收到任何Telegram通知**

## 根本原因

有两个原因导致通知失败：

### 1. 环境变量未正确替换

**问题**: 在Windows客户端运行测试时，`config.yaml` 中的占位符 `${TELEGRAM_BOT_TOKEN:}` 和 `${TELEGRAM_CHAT_ID:}` 没有被替换成实际值。

**原因**: 测试脚本直接使用 `yaml.safe_load()` 加载配置，不会自动替换环境变量。

**解决方案**:
- 在服务器上运行测试（环境变量已设置）
- 使用 `test_tg_simple.py` 直接读取环境变量
- 在Windows上设置环境变量

### 2. 模拟合约代码没有发送通知

**问题**: `FuturesTradingEngine` 之前没有集成 `TradeNotifier`，导致模拟合约交易不发送通知。

**解决方案**: 已在最新代码中修复（commit: c057ca0）

## 快速诊断

### 步骤1: 检查配置

```bash
cd /home/tonny01/test2/crypto-analyzer
python3 check_tg_config.py
```

**预期输出** (正常情况):
```
======================================================================
  Telegram通知配置检查
======================================================================

📋 步骤1: 检查环境变量
----------------------------------------------------------------------
✅ TELEGRAM_BOT_TOKEN: 8518383275...elpfo
✅ TELEGRAM_CHAT_ID: 6059784801

📋 步骤2: 检查 config.yaml
----------------------------------------------------------------------
✅ 找到 notifications: 配置
✅ 找到 telegram: 配置
✅ telegram.enabled: true
✅ 使用环境变量占位符: ${TELEGRAM_BOT_TOKEN:}
✅ 使用环境变量占位符: ${TELEGRAM_CHAT_ID:}

======================================================================
  建议
======================================================================

✅ 配置看起来正常！

📱 测试通知功能:
   python3 test_tg_simple.py
```

### 步骤2: 测试通知发送

```bash
python3 test_tg_simple.py
```

**预期输出**:
```
============================================================
  简单Telegram通知测试
============================================================

✅ bot_token: 8518383275...elpfo
✅ chat_id: 6059784801

1️⃣ 发送 限价单挂单 通知...
   ✅ 成功
2️⃣ 发送 订单成交 通知...
   ✅ 成功
3️⃣ 发送 开仓通知 通知...
   ✅ 成功

============================================================
  测试完成: 3/3 成功
============================================================

🎉 所有测试通过！请检查Telegram是否收到消息
```

## 修复历史

### 已修复的问题

1. **模拟合约开仓无TG通知** (commit: c057ca0)
   - 在 `FuturesTradingEngine` 添加 `trade_notifier` 参数
   - 市价单开仓后发送 `notify_order_filled()` 通知
   - 限价单挂单后发送 `notify_order_placed()` 通知

2. **初始化顺序问题** (commit: c057ca0)
   - TG通知服务提前初始化
   - 传递给 `FuturesTradingEngine` 和其他服务

3. **测试脚本环境变量问题** (commit: bdbe475, f72a761)
   - 修复 `test_tg_notification.py` 使用 `config_loader`
   - 新增 `test_tg_simple.py` 直接读取环境变量
   - 新增 `check_tg_config.py` 诊断配置

## 在Windows客户端上测试

### 问题
Windows上运行测试脚本失败，因为：
1. 环境变量未设置
2. 没有Python依赖包

### 解决方案

#### 选项1: 设置环境变量后测试

**PowerShell:**
```powershell
$env:TELEGRAM_BOT_TOKEN="8518383275:AAGPS4pB2RK_2yzcGVfQgbZVhjf82helpfo"
$env:TELEGRAM_CHAT_ID="6059784801"
python test_tg_simple.py
```

**CMD:**
```cmd
set TELEGRAM_BOT_TOKEN=8518383275:AAGPS4pB2RK_2yzcGVfQgbZVhjf82helpfo
set TELEGRAM_CHAT_ID=6059784801
python test_tg_simple.py
```

#### 选项2: 直接在服务器上测试（推荐）

```bash
# SSH到服务器
ssh user@server

# 运行测试
cd /home/tonny01/test2/crypto-analyzer
python3 test_tg_simple.py
```

## 服务器上的生产环境

### 确认通知功能已启用

#### 方法1: 检查主程序启动日志

```bash
# 如果使用systemd
journalctl -u your-service-name -n 100 | grep -i telegram

# 或查看日志文件
tail -f logs/app.log | grep -i telegram
```

**预期看到**:
```
✅ Telegram通知服务初始化成功
✅ 实盘交易Telegram通知已启用 (chat_id: 605978...)
```

#### 方法2: 运行测试脚本

```bash
python3 test_tg_simple.py
```

检查Telegram是否收到3条测试消息。

### 重启服务使新代码生效

**重要**: 模拟合约TG通知功能需要重启服务才能生效！

```bash
# 方法1: systemd服务
sudo systemctl restart your-service-name

# 方法2: tmux/screen会话
# 进入会话，Ctrl+C停止，然后重新启动

# 方法3: 直接kill并重启
ps aux | grep "python.*main.py"
kill -9 <PID>
# 然后重新启动程序
```

### 验证新功能

重启后，执行一笔模拟合约交易：

1. 在前端创建限价单 → 应收到"限价单挂单"通知
2. 限价单成交 → 应收到"订单成交"通知
3. 市价单开仓 → 应收到"订单成交"通知

## 通知类型总览

### 模拟合约通知（新增✨）
- 📝 限价单挂单: `notify_order_placed()`
- ✅ 订单成交: `notify_order_filled()`

### 实盘交易通知（已有）
- 🚀 开仓: `notify_open_position()`
- 💰 平仓: `notify_close_position()`
- 🛡️ 止损单设置: `notify_stop_loss_set()`
- 🎯 止盈单设置: `notify_take_profit_set()`
- ⚠️ 限价单超时取消: 在 `live_order_monitor.py` 中

## 常见错误和解决方案

### 错误1: "No module named 'dotenv'"

**原因**: 在服务器上直接运行测试脚本，没有激活虚拟环境

**解决方案**:
```bash
# 使用简单测试脚本（不需要虚拟环境）
python3 test_tg_simple.py

# 或激活虚拟环境
source venv/bin/activate
python3 test_tg_notification.py
```

### 错误2: "404 Client Error: Not Found"

**原因**: `bot_token` 是占位符 `${TELEGRAM_BOT_TOKEN:}` 而不是实际值

**解决方案**:
1. 设置环境变量
2. 使用 `config_loader.load_config()` 加载配置
3. 或使用 `test_tg_simple.py` 直接读取环境变量

### 错误3: "unexpected keyword argument 'stop_loss'"

**原因**: 测试脚本参数名错误

**解决方案**: 使用正确的参数名:
- `stop_loss_price` (不是 `stop_loss`)
- `take_profit_price` (不是 `take_profit`)

已在最新版本修复。

### 错误4: 没有收到任何通知

**可能原因**:
1. 环境变量未设置
2. `config.yaml` 中 `enabled: false`
3. 服务没有重启，使用旧代码
4. 网络无法访问Telegram API

**排查步骤**:
```bash
# 1. 检查配置
python3 check_tg_config.py

# 2. 测试发送
python3 test_tg_simple.py

# 3. 检查服务日志
journalctl -u your-service-name -f | grep -i telegram

# 4. 测试网络
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getMe
```

## 总结

✅ **问题已解决**:
1. 修复了模拟合约没有TG通知的问题
2. 创建了3个诊断/测试脚本
3. 更新了文档

🚀 **下一步**:
1. 在服务器上运行 `python3 check_tg_config.py` 确认配置
2. 运行 `python3 test_tg_simple.py` 测试通知
3. 重启主程序使新代码生效
4. 执行一笔模拟交易验证通知功能

📱 **验证成功标志**:
- 收到测试脚本发送的3条TG消息
- 模拟合约开仓后收到TG通知
- 实盘交易正常收到TG通知
