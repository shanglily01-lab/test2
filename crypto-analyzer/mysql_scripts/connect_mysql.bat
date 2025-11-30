@echo off
echo ========================================
echo Connect to MySQL
echo ========================================

REM Set MySQL path
set MYSQL_HOME=D:\mysql-9.3.0-winx64

echo Connecting to MySQL...
echo User: root
echo.

"%MYSQL_HOME%\bin\mysql.exe" -u root -p
