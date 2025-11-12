#!/bin/bash

###############################################################################
# MySQL/MariaDB 端口开放脚本
# 功能: 开放3306端口并配置MariaDB接受远程连接
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
    log_info "使用命令: sudo bash open_mysql_port.sh"
    exit 1
fi

# 从 config.yaml 读取数据库配置
DB_USER=$(grep -A 10 "mysql:" config.yaml | grep "user:" | head -1 | awk '{print $2}')
DB_PASSWORD=$(grep -A 10 "mysql:" config.yaml | grep "password:" | head -1 | awk '{print $2}')
DB_NAME=$(grep -A 10 "mysql:" config.yaml | grep "database:" | head -1 | awk '{print $2}')

log_info "数据库配置:"
echo "  用户: $DB_USER"
echo "  数据库: $DB_NAME"
echo ""

###############################################################################
# 步骤1: 检查并启动MariaDB
###############################################################################
log_step "步骤1: 检查MariaDB服务"

if systemctl is-active --quiet mariadb; then
    log_info "✅ MariaDB 服务正在运行"
elif systemctl is-active --quiet mysql; then
    log_info "✅ MySQL 服务正在运行"
else
    log_warn "MariaDB/MySQL 服务未运行"

    # 检查是否已安装
    if command -v mysql &> /dev/null; then
        log_info "尝试启动服务..."
        systemctl start mariadb 2>/dev/null || systemctl start mysql 2>/dev/null || true
        sleep 3

        if systemctl is-active --quiet mariadb || systemctl is-active --quiet mysql; then
            log_info "✅ 服务已启动"
        else
            log_error "服务启动失败"
            log_info "请先运行: sudo bash reinstall_mariadb.sh"
            exit 1
        fi
    else
        log_error "MariaDB/MySQL 未安装"
        log_info "请先运行: sudo bash reinstall_mariadb.sh"
        exit 1
    fi
fi

###############################################################################
# 步骤2: 配置MariaDB监听所有接口
###############################################################################
log_step "步骤2: 配置MariaDB监听所有接口"

# 查找MariaDB配置文件
MARIADB_CNF=""
if [ -f "/etc/my.cnf.d/mariadb-server.cnf" ]; then
    MARIADB_CNF="/etc/my.cnf.d/mariadb-server.cnf"
elif [ -f "/etc/mysql/mariadb.conf.d/50-server.cnf" ]; then
    MARIADB_CNF="/etc/mysql/mariadb.conf.d/50-server.cnf"
elif [ -f "/etc/my.cnf" ]; then
    MARIADB_CNF="/etc/my.cnf"
else
    log_warn "未找到MariaDB配置文件，创建新配置"
    mkdir -p /etc/my.cnf.d
    MARIADB_CNF="/etc/my.cnf.d/server.cnf"
fi

log_info "配置文件: $MARIADB_CNF"

# 备份原配置
if [ -f "$MARIADB_CNF" ]; then
    cp "$MARIADB_CNF" "${MARIADB_CNF}.backup.$(date +%Y%m%d_%H%M%S)"
    log_info "已备份原配置文件"
fi

# 检查是否已配置bind-address
if grep -q "^bind-address" "$MARIADB_CNF" 2>/dev/null; then
    log_info "修改现有 bind-address 配置..."
    sed -i 's/^bind-address.*/bind-address = 0.0.0.0/' "$MARIADB_CNF"
else
    log_info "添加 bind-address 配置..."

    # 确保有 [mysqld] 段
    if ! grep -q "^\[mysqld\]" "$MARIADB_CNF" 2>/dev/null; then
        echo -e "\n[mysqld]" >> "$MARIADB_CNF"
    fi

    # 在 [mysqld] 段后添加 bind-address
    sed -i '/^\[mysqld\]/a bind-address = 0.0.0.0' "$MARIADB_CNF"
fi

log_info "✅ MariaDB 配置已更新"

###############################################################################
# 步骤3: 配置防火墙
###############################################################################
log_step "步骤3: 配置防火墙"

# Amazon Linux 2023 默认使用 firewalld
if command -v firewall-cmd &> /dev/null; then
    log_info "检测到 Firewalld"

    # 检查firewalld是否运行
    if systemctl is-active --quiet firewalld; then
        log_info "Firewalld 正在运行，添加规则..."

        # 添加3306端口
        firewall-cmd --permanent --add-port=3306/tcp
        firewall-cmd --reload

        log_info "✅ Firewalld 规则已添加"

        # 显示规则
        log_info "当前开放的端口:"
        firewall-cmd --list-ports
    else
        log_info "启动 Firewalld..."
        systemctl start firewalld
        systemctl enable firewalld

        # 添加规则
        firewall-cmd --permanent --add-port=3306/tcp
        firewall-cmd --permanent --add-service=ssh
        firewall-cmd --reload

        log_info "✅ Firewalld 已配置"
    fi

elif command -v ufw &> /dev/null; then
    log_info "检测到 UFW"

    # 开放3306端口
    ufw allow 3306/tcp

    log_info "✅ UFW 规则已添加"

else
    log_warn "未检测到防火墙管理工具"
    log_info "Amazon Linux 2023 主要依赖 AWS Security Groups"
fi

###############################################################################
# 步骤4: 配置数据库用户权限
###############################################################################
log_step "步骤4: 配置数据库用户权限"

log_info "授予远程访问权限..."

# 创建SQL脚本
cat > /tmp/grant_remote.sql <<EOF
-- 授予用户远程访问权限
GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASSWORD}';

-- 刷新权限
FLUSH PRIVILEGES;

-- 显示用户权限
SHOW GRANTS FOR '${DB_USER}'@'%';
EOF

# 执行SQL
if mysql -u"${DB_USER}" -p"${DB_PASSWORD}" < /tmp/grant_remote.sql 2>/dev/null; then
    log_info "✅ 用户权限已配置"
else
    log_warn "使用root用户配置权限..."
    mysql -e "GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'%' IDENTIFIED BY '${DB_PASSWORD}'; FLUSH PRIVILEGES;" 2>/dev/null || true
fi

rm -f /tmp/grant_remote.sql

###############################################################################
# 步骤5: 重启MariaDB服务
###############################################################################
log_step "步骤5: 重启MariaDB服务"

log_info "重启MariaDB以应用配置..."
systemctl restart mariadb 2>/dev/null || systemctl restart mysql 2>/dev/null

# 等待服务启动
sleep 3

if systemctl is-active --quiet mariadb || systemctl is-active --quiet mysql; then
    log_info "✅ MariaDB 服务已重启"
else
    log_error "❌ MariaDB 服务重启失败"
    exit 1
fi

###############################################################################
# 步骤6: 验证端口
###############################################################################
log_step "步骤6: 验证配置"

log_info "检查端口监听..."
if ss -tlnp | grep -q ":3306"; then
    log_info "✅ 端口3306正在监听"
    ss -tlnp | grep ":3306"
else
    log_error "❌ 端口3306未监听"
    exit 1
fi

log_info "检查防火墙规则..."
if command -v firewall-cmd &> /dev/null && systemctl is-active --quiet firewalld; then
    if firewall-cmd --list-ports | grep -q "3306/tcp"; then
        log_info "✅ 防火墙规则已生效"
    else
        log_warn "⚠️  防火墙规则未找到"
    fi
fi

log_info "检查用户权限..."
mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "SHOW GRANTS FOR '${DB_USER}'@'%';" 2>/dev/null || log_warn "无法显示权限"

###############################################################################
# 完成
###############################################################################
log_step "配置完成!"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}    3306端口已开放! 🎉${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "${BLUE}连接信息:${NC}"
echo -e "  主机: $(hostname -I | awk '{print $1}')"
echo -e "  端口: 3306"
echo -e "  用户: $DB_USER"
echo -e "  密码: $DB_PASSWORD"
echo -e "  数据库: $DB_NAME"
echo ""

echo -e "${YELLOW}⚠️  重要提醒:${NC}"
echo -e "${YELLOW}如果使用 AWS EC2，还需要在 Security Group 中开放3306端口:${NC}"
echo ""
echo -e "1. 登录 AWS Console"
echo -e "2. 进入 EC2 > Security Groups"
echo -e "3. 找到该实例的 Security Group"
echo -e "4. 添加入站规则:"
echo -e "   - 类型: MySQL/Aurora (3306)"
echo -e "   - 协议: TCP"
echo -e "   - 端口范围: 3306"
echo -e "   - 来源: 0.0.0.0/0 (所有IP) 或指定IP"
echo ""

echo -e "${BLUE}测试连接:${NC}"
echo -e "  mysql -h <服务器IP> -u${DB_USER} -p${DB_PASSWORD} ${DB_NAME}"
echo ""

log_info "配置脚本执行完毕"
