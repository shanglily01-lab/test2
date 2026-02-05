#!/bin/bash

echo "=========================================="
echo "Stopping trading services..."
echo "=========================================="

# 停止U本位服务
pkill -f smart_trader_service.py
echo "U本位服务已停止"

# 停止币本位服务  
pkill -f coin_futures_trader_service.py
echo "币本位服务已停止"

sleep 3

echo ""
echo "=========================================="
echo "Starting trading services with fixes..."
echo "=========================================="

# 启动U本位服务
nohup python smart_trader_service.py > logs/smart_trader_$(date +%Y%m%d_%H%M%S).log 2>&1 &
echo "U本位服务已启动 (PID: $!)"

# 启动币本位服务
nohup python coin_futures_trader_service.py > logs/coin_futures_$(date +%Y%m%d_%H%M%S).log 2>&1 &
echo "币本位服务已启动 (PID: $!)"

sleep 2

echo ""
echo "=========================================="
echo "Checking service status..."
echo "=========================================="

ps aux | grep -E "smart_trader_service|coin_futures_trader_service" | grep -v grep

echo ""
echo "修复内容:"
echo "✅ 1. 模式筛选: 阻止100% OTHER垃圾信号"
echo "✅ 2. 黑名单: ZAMA/CHZ/DASH/DOGE永久禁止"  
echo "✅ 3. 自动切换: 修复一次性切换BUG"
echo "✅ 4. 严格趋势: 需要17根K线判断趋势"
echo ""
echo "服务重启完成！"
echo "=========================================="
