# 紧急待办：防止6小时超时事故再次发生

## 问题确认

你的服务在 **UTC 19:00 左右自动停止**，然后在 **UTC 01:21 自动重启**。

证据：
- 19:00-01:20: 完全没有交易活动
- 01:21: WebSocket重连、82个超时持仓批量平仓、新开仓恢复
- 01:21的日志特征是进程刚启动

## 立即检查（优先级1）

### 1. 确认是否有自动重启机制

**Windows任务计划程序**：
```
1. 打开"任务计划程序" (Task Scheduler)
2. 查找是否有在凌晨3点（北京时间，UTC 19:00）的定时任务
3. 查找是否有在早上9点（北京时间，UTC 01:21）的定时任务
```

**检查Python服务是如何运行的**：
- [ ] 是手动启动的Python脚本？
- [ ] 使用Windows服务？
- [ ] 使用第三方工具（如NSSM、WinSW）？
- [ ] 在WSL/Docker中运行？

### 2. 查看进程退出原因

**Windows事件查看器**：
```
1. 打开"事件查看器" (Event Viewer)
2. 查看"Windows日志" > "应用程序"
3. 筛选时间：2026-02-23 19:00 - 2026-02-24 02:00 (UTC)
4. 查找Python.exe崩溃或错误
```

**检查应用日志**：
```powershell
# 在crypto-analyzer目录下
# 查找服务日志中的EXIT/STOP关键词
Get-Content logs\*.log | Select-String -Pattern "EXIT|STOP|killed|terminated|Exception" -Context 2,2
```

### 3. 检查内存使用

服务可能因为**内存不足被系统杀死**：

```powershell
# 查看Python进程内存使用
Get-Process python* | Select-Object Name, Id, @{Name="Memory(MB)";Expression={$_.WorkingSet64/1MB}}
```

**检查任务管理器历史记录**：
- 打开任务管理器 > 性能 > 内存
- 查看是否有内存不足的历史

## 立即实施（优先级2）

### 1. 添加进程监控和自动重启

**方案A：使用NSSM（推荐）**

下载安装NSSM: https://nssm.cc/download

```cmd
# 安装为Windows服务
nssm install CryptoAnalyzer "C:\path\to\python.exe" "C:\path\to\main.py"

# 配置自动重启
nssm set CryptoAnalyzer AppExit Default Restart
nssm set CryptoAnalyzer AppRestartDelay 5000

# 启动服务
nssm start CryptoAnalyzer
```

**方案B：创建Windows任务计划程序监控**

创建一个PowerShell脚本 `monitor.ps1`:
```powershell
# 每分钟检查进程是否运行
while ($true) {
    $process = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {$_.CommandLine -like "*smart_trader*"}

    if (-not $process) {
        Write-Host "$(Get-Date) - 进程未运行，正在重启..."
        Start-Process -FilePath "python" -ArgumentList "main.py" -WorkingDirectory "D:\test2\crypto-analyzer"
    }

    Start-Sleep -Seconds 60
}
```

### 2. 添加心跳监控和告警

**在主循环中添加心跳文件**：

修改 `smart_trader_service.py` 的主循环：
```python
# 在 while self.running: 循环开始处添加
def _write_heartbeat(self):
    """写入心跳文件"""
    try:
        with open('heartbeat.txt', 'w') as f:
            f.write(f"{datetime.now().isoformat()}\n")
            f.write(f"positions: {self.get_open_positions_count()}\n")
    except Exception as e:
        logger.error(f"写入心跳文件失败: {e}")

# 在主循环中每次迭代都调用
while self.running:
    try:
        self._write_heartbeat()  # 🔥 添加心跳
        # ... 现有代码 ...
```

**外部监控脚本** `check_heartbeat.ps1`:
```powershell
# 每分钟检查心跳文件
while ($true) {
    if (Test-Path "heartbeat.txt") {
        $lastHeartbeat = Get-Content "heartbeat.txt" | Select-Object -First 1
        $lastTime = [datetime]::Parse($lastHeartbeat)
        $minutesAgo = ((Get-Date) - $lastTime).TotalMinutes

        if ($minutesAgo -gt 5) {
            # 超过5分钟没有心跳，发送告警
            Write-Host "⚠️ 服务无心跳超过 $minutesAgo 分钟！"
            # TODO: 发送Telegram告警
        }
    }

    Start-Sleep -Seconds 60
}
```

### 3. 配置崩溃自动重启

**修改主循环异常处理**：

```python
except Exception as e:
    logger.error(f"[ERROR] 主循环异常: {e}")
    import traceback
    logger.error(traceback.format_exc())

    # 发送告警
    if hasattr(self, 'telegram_notifier') and self.telegram_notifier:
        try:
            self.telegram_notifier.send_message(
                f"⚠️ 主循环异常\n\n"
                f"错误: {str(e)[:200]}\n"
                f"时间: {datetime.now()}\n"
                f"操作: 60秒后重试"
            )
        except:
            pass

    time.sleep(60)
```

## 验证清单

- [ ] 确认了19:00进程退出的原因
- [ ] 确认了01:21进程重启的原因
- [ ] 配置了自动重启机制（NSSM/任务计划）
- [ ] 添加了心跳监控
- [ ] 添加了进程崩溃告警
- [ ] 测试了自动重启是否工作

## 下一步

**今天就做**：
1. 查看Windows事件查看器和任务计划程序
2. 确认服务是如何启动/停止的
3. 配置NSSM或其他自动重启机制

**本周完成**：
1. 实施心跳监控
2. 添加崩溃告警到Telegram
3. 配置资源监控（内存/CPU）

**告诉我你的发现**，我会帮你进一步分析和修复！
