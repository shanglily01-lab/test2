# ===================================
# 加密货币分析系统 - Windows 启动脚本
# ===================================

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  加密货币交易分析系统 - 服务器启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. 清理旧的Python进程
Write-Host "步骤 1: 清理旧的Python进程..." -ForegroundColor Yellow
taskkill /F /IM python.exe 2>$null
if ($?) {
    Write-Host "✅ 已清理旧进程" -ForegroundColor Green
} else {
    Write-Host "⚠️  没有发现旧进程" -ForegroundColor Gray
}

# 2. 等待进程完全清理
Write-Host ""
Write-Host "步骤 2: 等待进程完全清理..." -ForegroundColor Yellow
Start-Sleep -Seconds 3
Write-Host "✅ 完成" -ForegroundColor Green

# 3. 检查端口8000是否已释放
Write-Host ""
Write-Host "步骤 3: 检查端口8000..." -ForegroundColor Yellow
$port8000 = netstat -ano | findstr :8000
if ($port8000) {
    Write-Host "❌ 警告: 端口8000仍被占用!" -ForegroundColor Red
    Write-Host "占用端口的进程:" -ForegroundColor Red
    netstat -ano | findstr :8000
    Write-Host ""
    Write-Host "请手动终止这些进程，或运行: taskkill /F /PID <PID号>" -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "✅ 端口8000已释放" -ForegroundColor Green
}

# 4. 启动服务器
Write-Host ""
Write-Host "步骤 4: 启动服务器..." -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 确保在正确的目录
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptPath

# 启动Python服务器
python app/main.py

# 如果服务器退出，显示提示
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "服务器已停止" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Cyan
