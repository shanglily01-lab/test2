@echo off
chcp 65001 >nul
echo ========================================
echo 安装 MySQL 为 Windows 服务
echo ========================================
echo.
echo 注意：此脚本需要以管理员身份运行！
echo.

REM 设置MySQL路径
set MYSQL_HOME=D:\mysql-9.3.0-winx64

REM 检查是否以管理员身份运行
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 错误：请右键点击此脚本，选择"以管理员身份运行"
    pause
    exit /b 1
)

echo 正在安装MySQL服务...

"%MYSQL_HOME%\bin\mysqld.exe" --install MySQL --defaults-file="%MYSQL_HOME%\my.ini"

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo MySQL服务安装成功！
    echo ========================================
    echo.
    echo 使用以下命令管理服务：
    echo   启动: net start MySQL
    echo   停止: net stop MySQL
    echo   删除: sc delete MySQL
) else (
    echo.
    echo 服务安装失败，可能服务已存在
    echo 尝试删除后重新安装: sc delete MySQL
)

pause
