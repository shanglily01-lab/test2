# Telegram通知设置指南

## 已添加的功能

### ✅ 模拟合约交易通知
现在模拟合约交易会发送以下TG通知：
- 📝 **限价单挂单**: 创建限价单时立即通知
- ✅ **订单成交**: 市价单或限价单成交时通知
- 🚀 **开仓通知**: 完整的开仓信息（已有功能）
- 💰 **平仓通知**: 完整的平仓信息（已有功能）

### ✅ 实盘交易通知
实盘交易的通知功能已有：
- 🚀 开仓通知
- 💰 平仓通知
- 🛡️ 止损单设置通知
- 🎯 止盈单设置通知
- ⚠️ 限价单超时取消通知

## 配置检查

### 1. 检查 config.yaml 配置

确保以下配置存在且正确：

```yaml
notifications:
  telegram:
    enabled: true
    bot_token: "YOUR_BOT_TOKEN"
    chat_id: "YOUR_CHAT_ID"
    # 通知开关
    notify_open: true        # 开仓通知
    notify_close: true       # 平仓通知
    notify_stop_loss: true   # 止损通知
    notify_take_profit: true # 止盈通知
```

### 2. 获取 Bot Token

1. 在 Telegram 中搜索 @BotFather
2. 发送 `/newbot` 创建新机器人
3. 按提示设置机器人名称
4. 获得 bot_token（格式: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）

### 3. 获取 Chat ID

**方法1: 使用 @userinfobot**
1. 在 Telegram 中搜索 @userinfobot
2. 点击 Start
3. 它会显示你的 Chat ID

**方法2: 使用 API**
```bash
# 先给你的机器人发送一条消息，然后运行:
curl https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

## 测试通知功能

### 运行测试脚本

```bash
cd /home/tonny01/test2/crypto-analyzer
python3 test_tg_notification.py
```

脚本会：
1. 验证 Telegram 配置
2. 发送3条测试消息:
   - 限价单挂单通知
   - 订单成交通知
   - 开仓通知
3. 检查你的 Telegram 是否收到消息

### 预期输出

```
============================================================
  测试Telegram通知功能
============================================================

✅ Telegram配置:
   bot_token: 1234567890...abc12
   chat_id: 123456789

✅ TradeNotifier 初始化成功

============================================================
  发送测试通知
============================================================

1️⃣ 测试限价单挂单通知...
   ✅ 限价单挂单通知发送成功

2️⃣ 测试订单成交通知...
   ✅ 订单成交通知发送成功

3️⃣ 测试开仓通知...
   ✅ 开仓通知发送成功

============================================================
  测试完成
============================================================

📱 请检查您的Telegram是否收到3条测试消息
```

## 常见问题

### Q1: 没有收到任何通知
**检查清单:**
- [ ] `notifications.telegram.enabled` 是否为 `true`
- [ ] `bot_token` 和 `chat_id` 是否正确
- [ ] 是否已给机器人发送过至少一条消息（Start）
- [ ] 机器人是否被 Telegram 封禁
- [ ] 网络是否能访问 Telegram API

**调试方法:**
```bash
# 查看日志
tail -f logs/app.log | grep -i telegram

# 或者查看主程序日志
journalctl -u your-service-name -f | grep -i telegram
```

### Q2: 只收到部分通知
**可能原因:**
- 某些通知开关被禁用，检查:
  - `notify_open`: 开仓通知
  - `notify_close`: 平仓通知
  - `notify_stop_loss`: 止损通知
  - `notify_take_profit`: 止盈通知

### Q3: 实盘有通知，模拟盘没有
**原因:** 需要重启服务使新代码生效

**解决方法:**
```bash
# 找到运行的进程
ps aux | grep "uvicorn\|gunicorn\|python.*main.py"

# 重启服务（根据你的部署方式）
# 方式1: systemd
sudo systemctl restart your-service-name

# 方式2: tmux/screen
# 进入对应会话，Ctrl+C 停止，然后重新启动

# 方式3: 直接kill进程
kill -9 <PID>
# 然后重新启动程序
```

### Q4: 通知延迟很大
**可能原因:**
- Telegram API 响应慢
- 网络连接问题
- 发送频率过高被限流

**建议:**
- 通知是异步的，不会阻塞交易
- 如果延迟持续存在，检查网络连接
- Telegram API 有速率限制（30 msg/sec per chat）

## 通知消息格式

### 限价单挂单
```
📝 【限价单挂单】BTC/USDT

📌 方向: 买入
💰 数量: 0.001000
💵 限价: $95,000.0000
📋 类型: 限价单 - 模拟合约

⏰ 2025-12-10 16:30:45
```

### 订单成交
```
🟢 【订单成交】ETH/USDT

📌 方向: 卖出
💰 数量: 0.050000
💵 价格: $3,500.0000
📋 类型: 市价单 - 模拟合约

⏰ 2025-12-10 16:31:20
```

### 开仓通知
```
🚀 【开仓】DOGE/USDT

📌 方向: 做多
💰 数量: 10000.000000
💵 价格: $0.1450
🔢 杠杆: 10x
💵 保证金: 145.00 USDT

🛡️ 止损: $0.1380 (-4.83%)
🎯 止盈: $0.1600 (10.34%)

⏰ 2025-12-10 16:32:10
```

## 代码修改记录

### 修改的文件
1. `app/trading/futures_trading_engine.py`
   - 添加 `trade_notifier` 参数
   - 开仓成功后发送通知
   - 限价单挂单后发送通知

2. `app/services/trade_notifier.py`
   - 新增 `notify_order_placed()` 方法

3. `app/main.py`
   - 调整初始化顺序
   - TG通知服务提前初始化
   - 传递给 FuturesTradingEngine

### Git Commits
- `c057ca0` - feat: 模拟合约交易添加Telegram通知功能
- `55acf94` - test: 添加Telegram通知测试脚本

## 下一步

如果测试成功，您应该能收到所有交易通知。

如果有任何问题，请查看日志文件或运行测试脚本诊断。
