#!/bin/bash
# 启动策略执行器脚本

# 进入项目目录
cd "$(dirname "$0")"

echo "=========================================="
echo "启动策略执行器"
echo "=========================================="
echo ""

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    if ! command -v python &> /dev/null; then
        echo "[错误] 未找到Python，请先安装Python"
        exit 1
    else
        PYTHON_CMD=python
    fi
else
    PYTHON_CMD=python3
fi

echo "使用Python: $PYTHON_CMD"
echo ""

# 检查策略执行器文件是否存在
if [ ! -f "app/strategy_scheduler.py" ]; then
    echo "[错误] 未找到策略执行器文件: app/strategy_scheduler.py"
    exit 1
fi

# 创建logs目录
mkdir -p logs

echo "正在启动策略执行器..."
echo "日志文件: logs/strategy_scheduler.log"
echo ""
echo "提示:"
echo "  - 按 Ctrl+C 停止服务"
echo "  - 使用 screen 或 nohup 可以在后台运行"
echo "  - 查看日志: tail -f logs/strategy_scheduler.log"
echo ""
echo "=========================================="
echo ""

# 启动策略执行器
$PYTHON_CMD app/strategy_scheduler.py

