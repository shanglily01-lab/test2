@echo off
echo ========================================
echo MySQL Initialize Script
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

REM Delete old data directory if exists
if exist "%MYSQL_HOME%\data" (
    echo Deleting old data directory...
    rmdir /s /q "%MYSQL_HOME%\data"
)

REM Create data directory
echo Creating data directory...
mkdir "%MYSQL_HOME%\data"

REM Create my.ini config file
echo Creating my.ini config file...
echo [mysqld]> "%MYSQL_HOME%\my.ini"
echo basedir=D:/mysql-9.3.0-winx64>> "%MYSQL_HOME%\my.ini"
echo datadir=D:/mysql-9.3.0-winx64/data>> "%MYSQL_HOME%\my.ini"
echo port=3306>> "%MYSQL_HOME%\my.ini"
echo character-set-server=utf8mb4>> "%MYSQL_HOME%\my.ini"
echo default-storage-engine=INNODB>> "%MYSQL_HOME%\my.ini"
echo max_connections=200>> "%MYSQL_HOME%\my.ini"
echo.>> "%MYSQL_HOME%\my.ini"
echo [mysql]>> "%MYSQL_HOME%\my.ini"
echo default-character-set=utf8mb4>> "%MYSQL_HOME%\my.ini"
echo.>> "%MYSQL_HOME%\my.ini"
echo [client]>> "%MYSQL_HOME%\my.ini"
echo port=3306>> "%MYSQL_HOME%\my.ini"
echo default-character-set=utf8mb4>> "%MYSQL_HOME%\my.ini"

echo Config file created: %MYSQL_HOME%\my.ini
echo.
echo ========================================
echo Initializing MySQL data directory...
echo ========================================
echo.

"%MYSQL_HOME%\bin\mysqld.exe" --initialize-insecure --console

echo.
echo ========================================
echo Exit code: %errorlevel%
echo ========================================
echo.
echo If failed, check the error message above.
echo.
echo If success, run start_mysql.bat next.
echo.

pause
