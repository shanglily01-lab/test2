@echo off
echo ========================================
echo Stop MySQL Service
echo ========================================

REM Set MySQL path
set MYSQL_HOME=D:\mysql-9.3.0-winx64

echo Stopping MySQL service...

"%MYSQL_HOME%\bin\mysqladmin.exe" -u root shutdown 2>nul

if %errorlevel% equ 0 (
    echo MySQL service stopped
) else (
    echo Trying to kill MySQL process...
    taskkill /F /IM mysqld.exe 2>nul
    if %errorlevel% equ 0 (
        echo MySQL process killed
    ) else (
        echo MySQL may not be running
    )
)

pause
