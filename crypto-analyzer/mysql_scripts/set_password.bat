@echo off
chcp 65001 >nul
echo ========================================
echo 设置 MySQL root 密码
echo ========================================

REM 设置MySQL路径
set MYSQL_HOME=D:\mysql-9.3.0-winx64

set /p NEW_PASSWORD="请输入新的root密码: "

echo.
echo 正在设置密码...

"%MYSQL_HOME%\bin\mysql.exe" -u root -e "ALTER USER 'root'@'localhost' IDENTIFIED BY '%NEW_PASSWORD%'; FLUSH PRIVILEGES;"

if %errorlevel% equ 0 (
    echo.
    echo ========================================
    echo 密码设置成功！
    echo ========================================
    echo.
    echo 现在需要更新 my.ini 文件，移除 skip-grant-tables
    echo 然后重启MySQL服务
) else (
    echo.
    echo 密码设置失败，请检查MySQL是否正在运行
)

pause
