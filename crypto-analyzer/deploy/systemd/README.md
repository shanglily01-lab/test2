# systemd 部署指南

把这 6 个 .service 文件部署到 Linux 服务器,实现:
- 进程崩溃自动重启
- 启动顺序管理 (app/main.py 必须先起)
- 资源限制 (内存上限,防泄漏)
- 日志统一管理 (journalctl + append log file)
- 系统重启后自动启动

---

## 6 个服务

| 单元名 | 入口 | 依赖 |
|--------|------|------|
| crypto-app-main | uvicorn app.main:app --port 9020 | mysql |
| crypto-smart-trader | smart_trader_service.py | crypto-app-main |
| crypto-coin-futures | coin_futures_trader_service.py | crypto-app-main |
| crypto-fast-collector | fast_collector_service.py | mysql |
| crypto-strategy-live | strategy_live.py | crypto-app-main |
| crypto-strategy-bigmid | strategy_bigmid.py | crypto-app-main |

---

## 部署步骤

### 1. 调整路径

文件里硬编码了 `/opt/crypto-analyzer` 和 `User=ubuntu`。如果你的实际路径不同,sed 替换:

```bash
# 假设项目实际在 /home/ec2-user/crypto-analyzer, 用户是 ec2-user
cd deploy/systemd/
for f in *.service; do
    sed -i 's|/opt/crypto-analyzer|/home/ec2-user/crypto-analyzer|g; s|User=ubuntu|User=ec2-user|g; s|Group=ubuntu|Group=ec2-user|g' "$f"
done
```

### 2. venv Python 路径

如果你用系统 python 而不是 venv,把每个文件的:
```
ExecStart=/opt/crypto-analyzer/venv/bin/python ...
```
改成:
```
ExecStart=/usr/bin/python3 ...
```

### 3. 拷到 systemd 目录

```bash
sudo cp deploy/systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

### 4. 启用 + 启动

```bash
# 启用开机自启
sudo systemctl enable crypto-app-main crypto-smart-trader crypto-coin-futures \
                     crypto-fast-collector crypto-strategy-live crypto-strategy-bigmid

# 首次启动 (按依赖顺序)
sudo systemctl start crypto-app-main
sleep 10
sudo systemctl start crypto-smart-trader crypto-coin-futures \
                    crypto-fast-collector crypto-strategy-live crypto-strategy-bigmid
```

### 5. 验证

```bash
# 看每个服务状态
for svc in crypto-app-main crypto-smart-trader crypto-coin-futures \
           crypto-fast-collector crypto-strategy-live crypto-strategy-bigmid; do
    echo "=== $svc ==="
    sudo systemctl status $svc --no-pager | head -10
    echo
done

# 看 log
sudo journalctl -u crypto-smart-trader -n 50 --no-pager

# 或者本地 log 文件
tail -50 /opt/crypto-analyzer/logs/smart_trader_systemd.log
```

---

## 常用操作

```bash
# 重启某个服务
sudo systemctl restart crypto-smart-trader

# 重启全部
sudo systemctl restart crypto-app-main crypto-smart-trader crypto-coin-futures \
                       crypto-fast-collector crypto-strategy-live crypto-strategy-bigmid

# 停掉某个
sudo systemctl stop crypto-fast-collector

# 看哪些自动重启过 (Restart counter)
sudo systemctl show crypto-smart-trader -p NRestarts

# 实时跟 log
sudo journalctl -u crypto-smart-trader -f
```

---

## 与 PID 文件锁的关系

代码层有 `app/utils/pid_lock.py` 防止重复启动,systemd 这层防进程崩溃自动重启。**两层防护互补**:

- systemd: 进程死了自动拉起 (RestartSec=15)
- PID 锁: 如果有人手动 nohup 启动了第二份,PID 锁会立即让第二份退出 (exit 1)

特别针对 fast_collector_service,**两层都必须有**,因为它是 Binance IP ban 的高危源。

---

## 健康检查 + 告警 (可选)

定时检查 + 失败告警:

```bash
# crontab -e 加一行 (每 5 分钟检查)
*/5 * * * * cd /opt/crypto-analyzer && python scripts/healthcheck.py >/dev/null 2>&1 || curl -s -X POST "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" -d "chat_id=${TG_CHAT_ID}" -d "text=⚠️ crypto-analyzer 服务异常,执行 systemctl status 检查"
```

---

## 切换:从 nohup → systemd

```bash
# 1. 停掉所有 nohup 进程
ps aux | grep -E "smart_trader_service|coin_futures_trader|fast_collector|strategy_live|strategy_bigmid|uvicorn app.main" | grep -v grep | awk '{print $2}' | xargs -r kill -TERM
sleep 5

# 2. 删 PID 文件 (systemd 启动会重写)
rm -f /opt/crypto-analyzer/logs/*.pid

# 3. 拷 systemd 文件 + 启用 (如上 Step 3-4)

# 4. 验证
sudo systemctl status crypto-*
```

---

*文档版本: v1.0 | 创建: 2026-05-15*
