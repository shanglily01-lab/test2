# 15分钟 EMA 买入信号监控指南

## 🎯 功能说明

自动监控所有交易对的 15分钟 K线，当短期 EMA 向上穿过长期 EMA（金叉）时，自动发送买入信号提醒。

### 信号特点

- **金叉检测**: 短期 EMA 向上穿过长期 EMA
- **成交量确认**: 要求成交量放大（默认1.5倍以上）
- **信号强度**: 根据价格涨幅、成交量、EMA距离评估强度
- **防重复提醒**: 同一交易对1小时内不重复提醒

### 信号强度等级

| 强度 | 条件 | 建议 |
|------|------|------|
| **STRONG** 🔥 | 价格涨幅>2%, 成交量>3x, EMA距离<0.5% | 强烈买入信号 |
| **MEDIUM** ⚡ | 价格涨幅>1%, 成交量>2x, EMA距离<1% | 中等买入机会 |
| **WEAK** 💡 | 刚出现金叉，其他条件一般 | 观察或小仓位 |

## 📋 使用步骤

### 第1步：配置文件

将 `config_ema_example.yaml` 中的配置添加到你的 `config.yaml`：

```yaml
# EMA 买入信号监控
ema_signal:
  enabled: true                    # 启用 EMA 监控
  short_period: 9                  # 短期 EMA（默认9）
  long_period: 21                  # 长期 EMA（默认21）
  timeframe: '15m'                 # 时间周期
  volume_threshold: 1.5            # 成交量放大倍数

# 通知配置
notification:
  log: true                        # 日志通知
  file: true                       # 文件通知
  alert_file: 'signals/ema_alerts.txt'
```

### 第2步：运行测试

```bash
cd /home/tonny/code/test2/crypto-analyzer
python test_ema_signal.py
```

**检查输出**：
- EMA 计算是否正确
- 能否检测到信号
- 通知功能是否工作

### 第3步：启动监控

```bash
# 重启 scheduler（数据采集调度器）
python app/scheduler.py
```

启动后你会看到：

```
✓ EMA 买入信号监控 (EMA9/EMA21, 15m) - 每 15 分钟

[10:15:00] 开始扫描 EMA 买入信号...
  ✓ 发现 2 个 EMA 买入信号
  信号强度分布: 强 1, 中 1, 弱 0

🚀 BTC/USDT 出现 STRONG 买入信号！
   价格: $43250.00 | 涨幅: +2.30%
   短期EMA: 43100.50 | 长期EMA: 42980.20
   成交量放大: 3.50x
```

### 第4步：查看信号记录

所有信号会保存到 `signals/ema_alerts.txt`：

```bash
cat signals/ema_alerts.txt
```

## 🎛️ 配置调整

### 调整 EMA 周期

不同的 EMA 组合适合不同的交易风格：

| EMA组合 | 特点 | 适合 |
|---------|------|------|
| EMA9/EMA21 | 快速响应 | 短线交易 |
| EMA12/EMA26 | 经典MACD参数 | 中短线 |
| EMA20/EMA50 | 稳定信号 | 中长线 |

修改 `config.yaml`：

```yaml
ema_signal:
  short_period: 12                 # 改为12
  long_period: 26                  # 改为26
```

### 调整成交量阈值

```yaml
ema_signal:
  volume_threshold: 2.0            # 提高到2倍，减少假信号
```

### 更改时间周期

```yaml
ema_signal:
  timeframe: '5m'                  # 改为5分钟（更频繁）
  # 或
  timeframe: '1h'                  # 改为1小时（更稳定）
```

## 📧 配置通知

### 邮件通知

1. 在 `config.yaml` 中添加：

```yaml
notification:
  email: true
  email_config:
    smtp_server: 'smtp.gmail.com'
    smtp_port: 587
    sender: 'your_email@gmail.com'
    password: 'your_app_password'      # Gmail需要使用应用专用密码
    receivers:
      - 'receiver@gmail.com'
```

2. Gmail 设置：
   - 开启两步验证
   - 生成应用专用密码：https://myaccount.google.com/apppasswords
   - 使用应用专用密码替换上面的 `your_app_password`

### Telegram 通知

1. 创建 Telegram Bot：
   - 联系 @BotFather
   - 发送 `/newbot` 创建机器人
   - 获取 Bot Token

2. 获取 Chat ID：
   - 联系 @userinfobot
   - 发送任意消息获取你的 Chat ID

3. 在 `config.yaml` 中添加：

```yaml
notification:
  telegram: true
  telegram_config:
    bot_token: 'YOUR_BOT_TOKEN'
    chat_id: 'YOUR_CHAT_ID'
```

## 📊 信号示例

### 强信号示例

```
🔥 BTC/USDT 买入信号 (STRONG)

⏰ 时间: 2025-01-27 10:15:00
📊 周期: 15m
💰 价格: $43250.00 (+2.30%)

📈 EMA 金叉:
   • 短期 EMA9: 43100.50
   • 长期 EMA21: 42980.20
   • EMA 距离: 0.28%

📊 成交量:
   • 当前: 25000000.00
   • 平均: 7142857.14
   • 放大倍数: 3.50x

💡 建议: 短期 EMA 向上穿过长期 EMA，考虑买入机会
```

### 中等信号示例

```
⚡ ETH/USDT 买入信号 (MEDIUM)

⏰ 时间: 2025-01-27 10:30:00
📊 周期: 15m
💰 价格: $2280.50 (+1.20%)

📈 EMA 金叉:
   • 短期 EMA9: 2275.30
   • 长期 EMA21: 2268.80
   • EMA 距离: 0.29%

📊 成交量:
   • 当前: 15000000.00
   • 平均: 6500000.00
   • 放大倍数: 2.31x

💡 建议: 短期 EMA 向上穿过长期 EMA，考虑买入机会
```

## ⚠️ 注意事项

### 1. 信号不是100%准确

- EMA 金叉是常用技术指标，但不保证盈利
- 建议结合其他指标（RSI、MACD、成交量等）
- 设置止损，控制风险

### 2. 市场波动

- 震荡市场可能产生假信号
- 强烈趋势市场信号更可靠
- 注意市场大环境

### 3. 监控频率

- 默认每15分钟扫描一次
- 避免过于频繁（增加服务器负担）
- 避免过于稀疏（错过信号）

### 4. 数据依赖

- 需要足够的历史 K线数据
- 确保数据采集正常运行
- 检查数据库连接

## 🔧 问题排查

### 问题1: 未检测到信号

**原因**:
- 当前市场没有金叉
- 成交量阈值太高
- EMA 周期不合适

**解决**:
```yaml
# 降低成交量阈值
volume_threshold: 1.2              # 从1.5降低到1.2

# 或调整 EMA 周期
short_period: 5                    # 更灵敏
```

### 问题2: 信号太多

**原因**:
- 市场震荡
- 阈值太低

**解决**:
```yaml
# 提高成交量阈值
volume_threshold: 2.0              # 提高到2.0

# 或使用更大的 EMA 周期
short_period: 12
long_period: 26
```

### 问题3: 通知未收到

**原因**:
- 通知服务未启用
- 配置错误

**解决**:
```bash
# 检查日志
tail -f logs/scheduler.log

# 检查信号文件
ls -lh signals/ema_alerts.txt
```

## 📈 优化建议

### 短期交易（日内）

```yaml
ema_signal:
  short_period: 5                  # 更快响应
  long_period: 13
  timeframe: '5m'                  # 5分钟周期
  volume_threshold: 2.0            # 更严格
```

### 中期交易（波段）

```yaml
ema_signal:
  short_period: 9
  long_period: 21
  timeframe: '15m'                 # 15分钟周期
  volume_threshold: 1.5
```

### 长期交易（趋势）

```yaml
ema_signal:
  short_period: 20
  long_period: 50
  timeframe: '1h'                  # 1小时周期
  volume_threshold: 1.3            # 相对宽松
```

## 📚 相关文件

- [app/trading/ema_signal_monitor.py](app/trading/ema_signal_monitor.py) - EMA 监控核心逻辑
- [app/services/notification_service.py](app/services/notification_service.py) - 通知服务
- [test_ema_signal.py](test_ema_signal.py) - 测试脚本
- [config_ema_example.yaml](config_ema_example.yaml) - 配置示例

## 🚀 快速开始

```bash
# 1. 添加配置
cat config_ema_example.yaml >> config.yaml

# 2. 测试功能
python test_ema_signal.py

# 3. 启动监控
python app/scheduler.py

# 4. 查看信号
tail -f signals/ema_alerts.txt
```

---

**祝交易顺利！** 📈
