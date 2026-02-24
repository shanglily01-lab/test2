#!/bin/bash
# 检查进程历史和重启记录

echo "=========================================="
echo "检查服务重启记录"
echo "=========================================="

# 1. 检查systemd服务状态（如果使用systemd）
echo -e "\n1. Systemd服务重启记录:"
if command -v systemctl &> /dev/null; then
    sudo journalctl -u crypto-analyzer --since "2026-02-23 19:00" --until "2026-02-24 02:00" 2>/dev/null || echo "未找到systemd服务"
else
    echo "系统未使用systemd"
fi

# 2. 检查supervisor日志（如果使用supervisor）
echo -e "\n2. Supervisor日志:"
if [ -f "/var/log/supervisor/crypto-analyzer-stderr.log" ]; then
    echo "最近的错误日志:"
    tail -100 /var/log/supervisor/crypto-analyzer-stderr.log
else
    echo "未找到supervisor日志"
fi

# 3. 检查系统日志（OOM killer）
echo -e "\n3. 检查OOM Killer记录:"
sudo journalctl --since "2026-02-23 19:00" --until "2026-02-24 02:00" | grep -i "killed process" 2>/dev/null || echo "未发现OOM事件"

# 4. 检查dmesg（系统内核日志）
echo -e "\n4. 内核日志（OOM相关）:"
sudo dmesg -T | grep -A 5 -i "out of memory" | tail -20 2>/dev/null || echo "未发现OOM记录"

# 5. 检查Python进程崩溃记录
echo -e "\n5. Python崩溃记录:"
sudo journalctl --since "2026-02-23 19:00" --until "2026-02-24 02:00" | grep -i "python.*killed\|python.*segfault" 2>/dev/null || echo "未发现Python崩溃记录"

# 6. 检查是否有定时重启任务
echo -e "\n6. 定时任务（cron）:"
echo "当前用户的cron:"
crontab -l 2>/dev/null | grep -v "^#" || echo "无cron任务"
echo -e "\nRoot用户的cron:"
sudo crontab -l 2>/dev/null | grep -v "^#" || echo "无root cron任务"

# 7. 检查服务日志
echo -e "\n7. 服务日志中的退出记录:"
if [ -f "logs/smart_trader.log" ]; then
    echo "查找退出/停止相关日志:"
    grep -E "EXIT|STOP|stopped|killed|terminated" logs/smart_trader.log | tail -20
else
    echo "未找到服务日志"
fi

echo -e "\n=========================================="
echo "检查完成"
echo "=========================================="
