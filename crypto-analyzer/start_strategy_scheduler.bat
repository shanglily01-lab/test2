@echo off
REM 启动策略执行器脚本 (Windows)

echo ==========================================
echo 启动策略执行器
echo ==========================================
echo.

REM 检查Python是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到Python，请先安装Python
    pause
    exit /b 1
)

REM 检查策略执行器文件是否存在
if not exist "app\strategy_scheduler.py" (
    echo [错误] 未找到策略执行器文件: app\strategy_scheduler.py
    pause
    exit /b 1
)

REM 创建logs目录
if not exist "logs" mkdir logs

echo 正在启动策略执行器...
echo 日志文件: logs\strategy_scheduler.log
echo.
echo 提示:
echo   - 按 Ctrl+C 停止服务
echo   - 查看日志: type logs\strategy_scheduler.log
echo.
echo ==========================================
echo.

REM 启动策略执行器
python app\strategy_scheduler.py

pause

