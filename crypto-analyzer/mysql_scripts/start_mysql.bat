@echo off
echo ========================================
echo Start MySQL Service
echo ========================================

REM Set MySQL path
set MYSQL_HOME=D:\mysql-9.3.0-winx64

REM Check if path exists
if not exist "%MYSQL_HOME%\bin\mysqld.exe" (
    echo ERROR: mysqld.exe not found
    echo Please check MYSQL_HOME path
    pause
    exit /b 1
)

REM Check if data directory exists
if not exist "%MYSQL_HOME%\data" (
    echo ERROR: data directory not found
    echo Please run init_mysql.bat first
    pause
    exit /b 1
)

echo MySQL Path: %MYSQL_HOME%
echo.
echo Starting MySQL service...
echo Press Ctrl+C to stop
echo.

"%MYSQL_HOME%\bin\mysqld.exe" --console
