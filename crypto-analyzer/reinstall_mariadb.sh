#!/bin/bash

###############################################################################
# MariaDB 重新安装脚本
# 功能: 完全卸载并重新安装 MariaDB，按 config.yaml 配置数据库
###############################################################################

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}===================================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}===================================================${NC}\n"
}

# 检查root权限
if [ "$EUID" -ne 0 ]; then
    log_error "请使用root权限运行此脚本"
    log_info "使用命令: sudo bash reinstall_mariadb.sh"
    exit 1
fi

# 从 config.yaml 读取数据库配置
DB_USER=$(grep -A 10 "mysql:" config.yaml | grep "user:" | head -1 | awk '{print $2}')
DB_PASSWORD=$(grep -A 10 "mysql:" config.yaml | grep "password:" | head -1 | awk '{print $2}')
DB_NAME=$(grep -A 10 "mysql:" config.yaml | grep "database:" | head -1 | awk '{print $2}')

log_info "从 config.yaml 读取的数据库配置:"
echo "  用户: $DB_USER"
echo "  密码: $DB_PASSWORD"
echo "  数据库: $DB_NAME"
echo ""

read -p "确认使用此配置重新安装 MariaDB? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_warn "操作已取消"
    exit 0
fi

###############################################################################
# 步骤1: 停止并卸载 MariaDB
###############################################################################
log_step "步骤1: 停止并卸载 MariaDB"

# 停止服务
log_info "停止 MariaDB 服务..."
systemctl stop mariadb 2>/dev/null || true

# 卸载 MariaDB
log_info "卸载 MariaDB 软件包..."
yum remove -y mariadb105-server mariadb105 mariadb105-common mariadb105-libs

# 删除数据目录（小心！）
log_warn "删除 MariaDB 数据目录..."
rm -rf /var/lib/mysql/*
rm -rf /etc/my.cnf.d/*

log_info "MariaDB 卸载完成"

###############################################################################
# 步骤2: 重新安装 MariaDB
###############################################################################
log_step "步骤2: 重新安装 MariaDB 10.5"

log_info "安装 MariaDB..."
yum install -y mariadb105-server mariadb105

# 启动服务
log_info "启动 MariaDB 服务..."
systemctl start mariadb
systemctl enable mariadb

# 等待服务启动
sleep 3

log_info "MariaDB 安装完成"

###############################################################################
# 步骤3: 配置数据库
###############################################################################
log_step "步骤3: 配置数据库"

log_info "创建数据库和用户..."

# 创建 SQL 配置脚本
cat > /tmp/mariadb_setup.sql <<EOF
-- 设置 root 密码
ALTER USER 'root'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
FLUSH PRIVILEGES;

-- 创建数据库
CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 授权（如果用户是 root，跳过创建用户）
EOF

if [ "$DB_USER" = "root" ]; then
    cat >> /tmp/mariadb_setup.sql <<EOF
-- 使用 root 用户，授予所有权限
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO 'root'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO 'root'@'%';
EOF
else
    cat >> /tmp/mariadb_setup.sql <<EOF
-- 创建应用用户
CREATE USER IF NOT EXISTS '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}';
CREATE USER IF NOT EXISTS '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASSWORD}';

-- 授权
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost';
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%';
EOF
fi

cat >> /tmp/mariadb_setup.sql <<EOF
FLUSH PRIVILEGES;

-- 显示数据库
SHOW DATABASES;
EOF

# 执行 SQL 脚本
log_info "执行数据库配置..."
mysql < /tmp/mariadb_setup.sql

# 清理临时文件
rm -f /tmp/mariadb_setup.sql

log_info "数据库配置完成"

###############################################################################
# 步骤4: 测试连接
###############################################################################
log_step "步骤4: 测试数据库连接"

log_info "测试数据库连接..."
if mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "USE \`${DB_NAME}\`; SHOW TABLES;" 2>/dev/null; then
    log_info "✅ 数据库连接测试成功"
else
    log_error "❌ 数据库连接测试失败"
    exit 1
fi

###############################################################################
# 完成
###############################################################################
log_step "MariaDB 重新安装完成!"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}    安装成功! 🎉${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "${BLUE}数据库信息:${NC}"
echo -e "  数据库名: $DB_NAME"
echo -e "  用户名: $DB_USER"
echo -e "  密码: $DB_PASSWORD"
echo ""

echo -e "${BLUE}下一步操作:${NC}"
echo -e "  1. 初始化数据库表: python scripts/init/init_database.py"
echo -e "  2. 启动应用服务: sudo systemctl start crypto-analyzer"
echo -e "  3. 查看服务状态: ./status.sh"
echo ""

log_info "重新安装脚本执行完毕"
