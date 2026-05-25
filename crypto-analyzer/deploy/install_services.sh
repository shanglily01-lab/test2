#!/bin/bash
set -e

# ============================================================
# Crypto Analyzer - 安装 systemd 服务（在 Linux 服务器上运行）
# ============================================================
# 用法: sudo bash deploy/install_services.sh
# ============================================================

PROJECT_DIR="/home/test2/crypto-analyzer"

echo "========================================"
echo "安装 systemd 服务..."
echo "========================================"

# 1. 复制服务文件
cp "$PROJECT_DIR/deploy/crypto-fastapi.service" /etc/systemd/system/
cp "$PROJECT_DIR/deploy/crypto-smart-trader.service" /etc/systemd/system/
cp "$PROJECT_DIR/deploy/crypto-ws-kline.service" /etc/systemd/system/

# 2. 确保 logs 目录存在
mkdir -p "$PROJECT_DIR/logs"

# 修复权限，让 ec2-user 有写权限
chown -R ec2-user:ec2-user "$PROJECT_DIR/logs" 2>/dev/null || true

# 3. 重新加载 systemd
systemctl daemon-reload

echo ""
echo "========================================"
echo "服务安装完成！"
echo "========================================"
echo ""
echo "请用以下命令管理服务："
echo ""
echo "  启动 FastAPI:     systemctl start crypto-fastapi"
echo "  停止 FastAPI:     systemctl stop crypto-fastapi"
echo "  重启 FastAPI:     systemctl restart crypto-fastapi"
echo "  查看 FastAPI:     systemctl status crypto-fastapi"
echo "  FastAPI 日志:     journalctl -u crypto-fastapi -f"
echo ""
echo "  启动 Smart:       systemctl start crypto-smart-trader"
echo "  停止 Smart:       systemctl stop crypto-smart-trader"
echo "  重启 Smart:       systemctl restart crypto-smart-trader"
echo "  查看 Smart:       systemctl status crypto-smart-trader"
echo "  Smart 日志:       journalctl -u crypto-smart-trader -f"
echo ""
echo "  启动 WS Kline:    systemctl start crypto-ws-kline"
echo "  停止 WS Kline:    systemctl stop crypto-ws-kline"
echo "  重启 WS Kline:    systemctl restart crypto-ws-kline"
echo "  查看 WS Kline:    systemctl status crypto-ws-kline"
echo "  WS Kline 日志:    journalctl -u crypto-ws-kline -f"
echo ""
echo "  设置开机自启:"
echo "    systemctl enable crypto-ws-kline"
echo ""
echo "  全部状态:      systemctl list-units --type=service | grep crypto"
echo ""
