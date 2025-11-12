# Hyperliquid 聪明钱包监控调度器

## 概述

Hyperliquid 聪明钱包监控任务已从主调度器中分离，现在由独立的调度器 `app/hyperliquid_scheduler.py` 运行。这样可以完全避免长时间运行的监控任务阻塞主数据采集调度器。

## 为什么需要独立调度器？

1. **避免阻塞**: Hyperliquid 全量扫描任务（8000+ 个地址）执行时间较长，会阻塞主调度器的单线程执行
2. **独立管理**: 可以单独启动、停止、重启 Hyperliquid 监控，不影响主数据采集
3. **资源隔离**: 两个调度器独立运行，互不干扰

## 监控策略

- **高优先级钱包**: 每5分钟监控 (PnL>10K, ROI>50%, 7天内活跃, 限200个)
- **中优先级钱包**: 每1小时监控 (PnL>5K, ROI>30%, 30天内活跃, 限500个)
- **全量扫描**: 每6小时监控所有活跃钱包 (8000+个)

## 启动方式

### 方式1: 直接运行 Python 脚本

```bash
python app/hyperliquid_scheduler.py
```

### 方式2: 使用启动脚本 (Linux/Mac)

```bash
chmod +x start_hyperliquid.sh
./start_hyperliquid.sh
```

### 方式3: 后台运行 (Linux/Mac)

```bash
nohup python app/hyperliquid_scheduler.py > logs/hyperliquid_scheduler.log 2>&1 &
```

### 方式4: 使用 systemd 服务 (Linux)

创建服务文件 `/etc/systemd/system/hyperliquid-scheduler.service`:

```ini
[Unit]
Description=Hyperliquid Smart Wallet Monitor Scheduler
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/crypto-analyzer
ExecStart=/usr/bin/python3 app/hyperliquid_scheduler.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

然后启动服务:

```bash
sudo systemctl enable hyperliquid-scheduler
sudo systemctl start hyperliquid-scheduler
sudo systemctl status hyperliquid-scheduler
```

## 日志文件

日志文件保存在 `logs/hyperliquid_scheduler_YYYY-MM-DD.log`，按天轮转，保留30天。

## 停止方式

- 前台运行: 按 `Ctrl+C`
- 后台运行: 使用 `ps` 查找进程ID，然后 `kill <PID>`
- systemd 服务: `sudo systemctl stop hyperliquid-scheduler`

## 注意事项

1. **配置要求**: 确保 `config.yaml` 中 `hyperliquid.enabled: true`
2. **数据库连接**: 使用与主调度器相同的数据库配置
3. **API 限流**: 监控任务会自动处理 API 限流，但全量扫描仍需要较长时间
4. **资源占用**: 全量扫描时 CPU 和网络占用较高，建议在服务器资源充足时运行

## 与主调度器的关系

- **主调度器** (`app/scheduler.py`): 负责数据采集（价格、K线、新闻、资金费率等）
- **Hyperliquid 调度器** (`app/hyperliquid_scheduler.py`): 专门负责 Hyperliquid 聪明钱包监控

两个调度器可以同时运行，互不干扰。

