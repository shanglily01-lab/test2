# 重启服务并测试TG通知

## 当前状态

✅ **代码已更新**:
- 模拟合约添加TG通知功能
- 环境变量配置正确（`.env` 文件）
- 测试脚本验证通过

⚠️ **需要重启**: 新代码需要重启服务才能生效

## 快速操作指南

### 步骤1: 验证配置 ✅

```bash
cd /home/tonny01/test2/crypto-analyzer

# 检查.env文件
grep TELEGRAM .env
# 应该输出:
# TELEGRAM_BOT_TOKEN=8518383275:AAGPS4pB2RK_2yzcGVfQgbZVhjf82helpfo
# TELEGRAM_CHAT_ID=6059784801

# 测试TG通知
python3 test_tg_simple.py
# 应该收到3条Telegram消息
```

### 步骤2: 找到并重启主程序

#### 选项A: 如果使用systemd

```bash
# 查找服务名称
sudo systemctl list-units --type=service | grep -E "crypto|analyzer|trading"

# 重启服务（替换为实际服务名）
sudo systemctl restart crypto-analyzer.service

# 查看日志
sudo journalctl -u crypto-analyzer.service -f --since "1 minute ago"
```

#### 选项B: 如果使用tmux

```bash
# 查看tmux会话
tmux ls

# 进入会话
tmux attach -t <session-name>

# 在会话中:
# 1. Ctrl+C 停止当前程序
# 2. 重新运行启动命令，例如:
cd /home/tonny01/test2/crypto-analyzer
source venv/bin/activate
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```

#### 选项C: 如果使用screen

```bash
# 查看screen会话
screen -ls

# 进入会话
screen -r <session-name>

# 在会话中:
# 1. Ctrl+C 停止当前程序
# 2. 重新运行启动命令
```

#### 选项D: 直接启动（如果没有运行）

```bash
cd /home/tonny01/test2/crypto-analyzer

# 激活虚拟环境
source venv/bin/activate

# 启动主程序
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8001

# 或者如果有其他启动脚本
./start.sh
```

### 步骤3: 验证服务启动

查看启动日志，应该看到：

```
✅ 配置文件加载成功: /home/tonny01/test2/crypto-analyzer/config.yaml
✅ Telegram通知服务初始化成功
✅ 实盘交易Telegram通知已启用 (chat_id: 605978...)
✅ 合约限价单自动执行服务初始化成功
```

### 步骤4: 测试模拟合约TG通知

在前端执行一笔模拟合约交易：

1. **创建限价单** → 应收到 📝 "限价单挂单" 通知
2. **限价单成交** → 应收到 ✅ "订单成交" 通知
3. **市价单开仓** → 应收到 ✅ "订单成交" 通知

## 常见启动命令参考

根据你的部署方式，常见的启动命令有：

### FastAPI/Uvicorn
```bash
# 开发模式
uvicorn app.main:app --reload --host 0.0.0.0 --port 8001

# 生产模式
uvicorn app.main:app --host 0.0.0.0 --port 8001 --workers 4
```

### Gunicorn + Uvicorn
```bash
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001
```

### 后台运行（nohup）
```bash
nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8001 > logs/app.log 2>&1 &
```

## 排查问题

### 问题1: 启动失败，提示 "No module named 'dotenv'"

**解决方案**: 激活虚拟环境
```bash
source venv/bin/activate
pip list | grep python-dotenv
# 如果没有安装
pip install python-dotenv
```

### 问题2: 启动成功但没有TG通知

**检查清单**:
```bash
# 1. 查看启动日志
tail -f logs/app.log | grep -i telegram

# 2. 检查config.yaml
grep -A 5 "telegram:" config.yaml

# 3. 确认.env文件被加载
grep TELEGRAM .env

# 4. 测试TG连接
python3 test_tg_simple.py
```

### 问题3: 端口被占用

```bash
# 查看端口占用
sudo lsof -i :8001

# 或
sudo netstat -tulpn | grep 8001

# 杀掉占用进程
sudo kill -9 <PID>
```

## 验证成功标志

✅ **启动日志**:
```
✅ Telegram通知服务初始化成功
✅ 实盘交易Telegram通知已启用 (chat_id: 605978...)
```

✅ **测试脚本成功**:
```bash
$ python3 test_tg_simple.py
🎉 所有测试通过！请检查Telegram是否收到消息
```

✅ **实际交易收到通知**:
- 模拟合约开仓 → 收到TG消息
- 实盘交易 → 收到TG消息

## 环境变量说明

项目使用 `.env` 文件存储敏感配置：

**`.env` 文件位置**: `/home/tonny01/test2/crypto-analyzer/.env`

**关键变量**:
```bash
TELEGRAM_BOT_TOKEN=8518383275:AAGPS4pB2RK_2yzcGVfQgbZVhjf82helpfo
TELEGRAM_CHAT_ID=6059784801
```

**config.yaml 中的占位符**:
```yaml
notifications:
  telegram:
    enabled: true
    bot_token: ${TELEGRAM_BOT_TOKEN:}
    chat_id: ${TELEGRAM_CHAT_ID:}
```

**加载流程**:
1. `config_loader.load_config()` 自动加载 `.env` 文件
2. 替换 `config.yaml` 中的 `${VAR}` 占位符
3. 最终配置包含实际的token和chat_id

## 下一步

1. ✅ 找到并重启主程序
2. ✅ 检查启动日志确认TG服务已启用
3. ✅ 执行一笔模拟合约交易
4. ✅ 验证收到TG通知

如有问题，查看 [TG_TROUBLESHOOTING.md](TG_TROUBLESHOOTING.md) 排查指南。
