#!/bin/bash

echo "===== 部署诊断脚本 ====="
echo ""

echo "1. 操作系统信息："
cat /etc/os-release | grep -E "^ID=|^VERSION_ID=|^PRETTY_NAME="
echo ""

echo "2. Python 3.11 检查："
if command -v python3.11 &> /dev/null; then
    echo "✅ Python 3.11 已安装: $(python3.11 --version)"
    which python3.11
else
    echo "❌ Python 3.11 未安装"
fi
echo ""

echo "3. MySQL 检查："
if command -v mysql &> /dev/null; then
    echo "✅ MySQL 客户端已安装: $(mysql --version)"
else
    echo "❌ MySQL 客户端未安装"
fi

if systemctl list-units --all | grep -q mysqld; then
    echo "✅ MySQL 服务存在"
    systemctl status mysqld --no-pager | head -5
else
    echo "❌ MySQL 服务不存在"
fi
echo ""

echo "4. Nginx 检查："
if command -v nginx &> /dev/null; then
    echo "✅ Nginx 已安装: $(nginx -v 2>&1)"
else
    echo "❌ Nginx 未安装"
fi

if systemctl list-units --all | grep -q nginx; then
    echo "✅ Nginx 服务存在"
    systemctl status nginx --no-pager | head -5
else
    echo "❌ Nginx 服务不存在"
fi
echo ""

echo "5. 虚拟环境检查："
if [ -d "venv" ]; then
    echo "✅ 虚拟环境已创建"
    ls -lh venv/
else
    echo "❌ 虚拟环境未创建"
fi
echo ""

echo "6. Systemd 服务检查："
if [ -f "/etc/systemd/system/crypto-analyzer.service" ]; then
    echo "✅ crypto-analyzer 服务文件已创建"
    ls -lh /etc/systemd/system/crypto-analyzer.service
else
    echo "❌ crypto-analyzer 服务文件不存在"
fi
echo ""

echo "7. 已安装的包："
echo "Python 相关："
rpm -qa | grep -i python | head -10
echo ""
echo "MySQL 相关："
rpm -qa | grep -i mysql
echo ""
echo "Nginx 相关："
rpm -qa | grep -i nginx
echo ""

echo "8. 端口监听："
sudo netstat -tlnp 2>/dev/null | grep -E ":(80|9020|3306)" || echo "相关端口未监听"
echo ""

echo "===== 诊断完成 ====="
