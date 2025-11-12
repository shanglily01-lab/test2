#!/bin/bash
echo "===== 系统状态 ====="
sudo systemctl status crypto-analyzer

echo -e "\n===== 端口监听 ====="
sudo netstat -tlnp | grep -E ':(9020|80)'

echo -e "\n===== MySQL状态 ====="
sudo systemctl status mysql | head -5

echo -e "\n===== Nginx状态 ====="
sudo systemctl status nginx | head -5

echo -e "\n===== 最新日志 ====="
tail -20 /var/log/crypto-analyzer.log
