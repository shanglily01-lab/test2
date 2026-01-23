#!/bin/bash
# 现货交易服务部署脚本

echo "=========================================="
echo "现货交易服务部署"
echo "=========================================="

# 1. 拉取最新代码
echo ""
echo "1. 拉取最新代码..."
git pull origin master

# 2. 检查Python依赖
echo ""
echo "2. 检查Python依赖..."
pip3 list | grep -E "pymysql|loguru|python-dotenv|pyyaml" || {
    echo "安装缺失的依赖..."
    pip3 install pymysql loguru python-dotenv pyyaml
}

# 3. 创建logs目录
echo ""
echo "3. 创建logs目录..."
mkdir -p logs

# 4. 检查现货交易表
echo ""
echo "4. 检查现货交易表..."
python3 check_spot_tables.py

# 5. 停止旧的现货交易服务（如果存在）
echo ""
echo "5. 停止旧的现货交易服务..."
if [ -f spot_trader.pid ]; then
    OLD_PID=$(cat spot_trader.pid)
    if ps -p $OLD_PID > /dev/null 2>&1; then
        echo "停止旧进程: $OLD_PID"
        kill $OLD_PID
        sleep 2
    fi
    rm -f spot_trader.pid
fi

# 也尝试通过进程名停止
pkill -f "spot_trader_service.py" 2>/dev/null || true
sleep 1

# 6. 启动新的现货交易服务
echo ""
echo "6. 启动现货交易服务..."
nohup python3 spot_trader_service.py > logs/spot_trader.log 2>&1 &
NEW_PID=$!
echo $NEW_PID > spot_trader.pid

echo ""
echo "✅ 现货交易服务已启动"
echo "   进程ID: $NEW_PID"
echo "   日志文件: logs/spot_trader.log"
echo ""

# 7. 等待服务启动
echo "7. 等待服务启动..."
sleep 3

# 8. 查看启动日志
echo ""
echo "8. 启动日志（最近20行）:"
echo "=========================================="
tail -n 20 logs/spot_trader.log

echo ""
echo "=========================================="
echo "部署完成！"
echo ""
echo "查看实时日志: tail -f logs/spot_trader.log"
echo "查看持仓状态: python3 check_spot_tables.py"
echo "停止服务: kill \$(cat spot_trader.pid)"
echo "=========================================="
