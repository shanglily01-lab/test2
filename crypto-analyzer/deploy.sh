#!/bin/bash

###############################################################################
# Crypto Analyzer 一键部署脚本
# 适用系统: Ubuntu 20.04/22.04, Debian 11/12, CentOS 7/8, Amazon Linux 2/2023
# 功能: 自动安装所有依赖并部署系统
###############################################################################

set -e  # 遇到错误立即退出

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志函数
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

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID

        # Amazon Linux 特殊处理
        if [ "$OS" = "amzn" ]; then
            OS="amazon"
            log_info "检测到 Amazon Linux $OS_VERSION"
        fi
    else
        log_error "无法检测操作系统类型"
        exit 1
    fi

    log_info "检测到操作系统: $OS $OS_VERSION"
}

# 检查是否为root用户
check_root() {
    if [ "$EUID" -ne 0 ]; then
        log_error "请使用root权限运行此脚本"
        log_info "使用命令: sudo bash deploy.sh"
        exit 1
    fi
}

# 获取项目目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log_info "项目目录: $PROJECT_DIR"

# 配置变量
PYTHON_VERSION="3.11"
MYSQL_ROOT_PASSWORD="CryptoAnalyzer@2024"  # 可修改
MYSQL_DATABASE="crypto_analyzer"
MYSQL_USER="crypto_user"
MYSQL_PASSWORD="crypto_pass_2024"  # 可修改
APP_PORT="9020"
NGINX_PORT="80"

# 检查root权限
check_root

# 检测操作系统
detect_os

# 提示用户确认
echo -e "\n${YELLOW}===== 部署配置确认 =====${NC}"
echo "项目目录: $PROJECT_DIR"
echo "MySQL数据库: $MYSQL_DATABASE"
echo "MySQL用户: $MYSQL_USER"
echo "应用端口: $APP_PORT"
echo "Nginx端口: $NGINX_PORT"
echo -e "${YELLOW}==========================${NC}\n"

read -p "确认开始部署? (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_warn "部署已取消"
    exit 0
fi

###############################################################################
# 步骤1: 更新系统
###############################################################################
log_step "步骤1: 更新系统软件包"

if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get upgrade -y
    apt-get install -y software-properties-common curl wget git vim net-tools
elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
    yum update -y
    yum install -y epel-release
    yum install -y curl wget git vim net-tools
elif [ "$OS" = "amazon" ]; then
    yum update -y --skip-broken || yum update -y --nobest
    # curl 已预装，跳过安装避免冲突
    yum install -y wget git vim net-tools tar gzip || true
else
    log_error "不支持的操作系统: $OS"
    exit 1
fi

log_info "系统更新完成"

###############################################################################
# 步骤2: 安装Python 3.11
###############################################################################
log_step "步骤2: 安装Python 3.11"

if command -v python3.11 &> /dev/null; then
    log_info "Python 3.11 已安装"
else
    log_info "开始安装Python 3.11..."

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        # Ubuntu/Debian
        add-apt-repository -y ppa:deadsnakes/ppa || true
        apt-get update -y
        apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip

        # 设置python3.11为默认python3（可选）
        update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ] || [ "$OS" = "amazon" ]; then
        # CentOS/RHEL/Amazon Linux - 从源码编译
        yum groupinstall -y "Development Tools" || yum install -y gcc gcc-c++ make
        yum install -y openssl-devel bzip2-devel libffi-devel zlib-devel xz-devel

        cd /tmp
        wget https://www.python.org/ftp/python/3.11.8/Python-3.11.8.tgz
        tar xzf Python-3.11.8.tgz
        cd Python-3.11.8
        ./configure --enable-optimizations
        make altinstall
        cd $PROJECT_DIR
    fi
fi

# 验证Python版本
PYTHON_CMD=$(which python3.11 || which python3)
PYTHON_VERSION_CHECK=$($PYTHON_CMD --version)
log_info "Python版本: $PYTHON_VERSION_CHECK"

# 升级pip
$PYTHON_CMD -m pip install --upgrade pip

log_info "Python 3.11 安装完成"

###############################################################################
# 步骤3: 安装MySQL 8.0
###############################################################################
log_step "步骤3: 安装MySQL 8.0"

if command -v mysql &> /dev/null; then
    log_info "MySQL 已安装"
else
    log_info "开始安装MySQL 8.0..."

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        # Ubuntu/Debian
        apt-get install -y mysql-server mysql-client

        # 启动MySQL
        systemctl start mysql
        systemctl enable mysql

    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ]; then
        # CentOS/RHEL
        yum install -y mysql-server

        # 启动MySQL
        systemctl start mysqld
        systemctl enable mysqld

        # 获取临时密码
        TEMP_PASSWORD=$(grep 'temporary password' /var/log/mysqld.log | awk '{print $NF}')

    elif [ "$OS" = "amazon" ]; then
        # Amazon Linux 2023
        log_info "Amazon Linux 2023 使用 MariaDB (MySQL 兼容)"

        # Amazon Linux 2023 推荐使用 MariaDB
        yum install -y mariadb105-server mariadb105

        # 启动MariaDB
        systemctl start mariadb
        systemctl enable mariadb

        # MariaDB 默认没有密码，直接设置
        TEMP_PASSWORD=""
    fi
fi

# 等待MySQL启动
sleep 5

# 配置MySQL
log_info "配置MySQL数据库..."

# 创建配置SQL文件
cat > /tmp/mysql_setup.sql <<EOF
-- 设置root密码
ALTER USER 'root'@'localhost' IDENTIFIED BY '${MYSQL_ROOT_PASSWORD}';
FLUSH PRIVILEGES;

-- 创建数据库
CREATE DATABASE IF NOT EXISTS ${MYSQL_DATABASE} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 创建用户
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'localhost' IDENTIFIED BY '${MYSQL_PASSWORD}';
CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'%' IDENTIFIED BY '${MYSQL_PASSWORD}';

-- 授权
GRANT ALL PRIVILEGES ON ${MYSQL_DATABASE}.* TO '${MYSQL_USER}'@'localhost';
GRANT ALL PRIVILEGES ON ${MYSQL_DATABASE}.* TO '${MYSQL_USER}'@'%';
FLUSH PRIVILEGES;

-- 显示数据库
SHOW DATABASES;
EOF

# 执行MySQL配置
if [ -n "$TEMP_PASSWORD" ]; then
    # CentOS - 使用临时密码
    mysql --connect-expired-password -uroot -p"$TEMP_PASSWORD" < /tmp/mysql_setup.sql 2>/dev/null || \
    mysql -uroot -p"$MYSQL_ROOT_PASSWORD" < /tmp/mysql_setup.sql 2>/dev/null
else
    # Ubuntu - 使用sudo
    mysql < /tmp/mysql_setup.sql 2>/dev/null || \
    mysql -uroot -p"$MYSQL_ROOT_PASSWORD" < /tmp/mysql_setup.sql 2>/dev/null
fi

rm -f /tmp/mysql_setup.sql

log_info "MySQL配置完成"
log_info "数据库: $MYSQL_DATABASE"
log_info "用户: $MYSQL_USER"

###############################################################################
# 步骤4: 安装Nginx
###############################################################################
log_step "步骤4: 安装Nginx"

if command -v nginx &> /dev/null; then
    log_info "Nginx 已安装"
else
    log_info "开始安装Nginx..."

    if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
        apt-get install -y nginx
    elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ] || [ "$OS" = "amazon" ]; then
        # Amazon Linux 2023 使用 nginx
        yum install -y nginx
    fi

    # 启动Nginx
    systemctl start nginx
    systemctl enable nginx
fi

log_info "Nginx安装完成"

###############################################################################
# 步骤5: 配置Python虚拟环境
###############################################################################
log_step "步骤5: 配置Python虚拟环境"

cd $PROJECT_DIR

# 创建虚拟环境
if [ ! -d "venv" ]; then
    log_info "创建Python虚拟环境..."
    $PYTHON_CMD -m venv venv
else
    log_info "虚拟环境已存在"
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
log_info "安装Python依赖包..."
pip install --upgrade pip
pip install -r requirements.txt

log_info "Python依赖安装完成"

###############################################################################
# 步骤6: 配置数据库连接
###############################################################################
log_step "步骤6: 配置数据库连接"

# 备份原配置文件
if [ -f "config.yaml" ]; then
    cp config.yaml config.yaml.backup.$(date +%Y%m%d%H%M%S)
    log_info "已备份原配置文件"
fi

# 更新config.yaml中的数据库配置
log_info "更新数据库配置..."

# 使用sed更新配置（更安全）
if grep -q "^database:" config.yaml; then
    # 已有database配置，更新它
    sed -i "/^database:/,/^[a-z]/ {
        s|host:.*|host: localhost|
        s|port:.*|port: 3306|
        s|user:.*|user: ${MYSQL_USER}|
        s|password:.*|password: ${MYSQL_PASSWORD}|
        s|database:.*|database: ${MYSQL_DATABASE}|
    }" config.yaml
else
    # 没有database配置，添加它
    cat >> config.yaml <<EOF

# Database Configuration
database:
  host: localhost
  port: 3306
  user: ${MYSQL_USER}
  password: ${MYSQL_PASSWORD}
  database: ${MYSQL_DATABASE}
EOF
fi

log_info "数据库配置完成"

###############################################################################
# 步骤7: 初始化数据库
###############################################################################
log_step "步骤7: 初始化数据库表结构"

# 检查是否有初始化脚本
if [ -f "scripts/init/init_database.py" ]; then
    log_info "运行数据库初始化脚本..."
    python scripts/init/init_database.py || log_warn "数据库初始化脚本执行失败，可能是表已存在"
else
    log_warn "未找到数据库初始化脚本"
fi

###############################################################################
# 步骤8: 配置Systemd服务
###############################################################################
log_step "步骤8: 配置Systemd服务"

# 创建systemd服务文件
cat > /etc/systemd/system/crypto-analyzer.service <<EOF
[Unit]
Description=Crypto Analyzer Trading System
After=network.target mysql.service
Wants=mysql.service

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $APP_PORT --no-access-log
Restart=always
RestartSec=10
StandardOutput=append:/var/log/crypto-analyzer.log
StandardError=append:/var/log/crypto-analyzer-error.log

# 资源限制
LimitNOFILE=65536
Nice=-10

[Install]
WantedBy=multi-user.target
EOF

# 重载systemd
systemctl daemon-reload
systemctl enable crypto-analyzer.service

log_info "Systemd服务配置完成"

###############################################################################
# 步骤9: 配置Nginx反向代理
###############################################################################
log_step "步骤9: 配置Nginx反向代理"

# 创建Nginx配置
cat > /etc/nginx/sites-available/crypto-analyzer <<EOF
server {
    listen $NGINX_PORT;
    server_name _;

    # 日志
    access_log /var/log/nginx/crypto-analyzer-access.log;
    error_log /var/log/nginx/crypto-analyzer-error.log;

    # 客户端最大上传大小
    client_max_body_size 100M;

    # 静态文件
    location /static/ {
        alias $PROJECT_DIR/static/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # API和Web应用
    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;

        # WebSocket支持
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";

        # 超时设置
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
EOF

# 创建软链接
if [ "$OS" = "ubuntu" ] || [ "$OS" = "debian" ]; then
    ln -sf /etc/nginx/sites-available/crypto-analyzer /etc/nginx/sites-enabled/
    # 删除默认站点
    rm -f /etc/nginx/sites-enabled/default
elif [ "$OS" = "centos" ] || [ "$OS" = "rhel" ] || [ "$OS" = "amazon" ]; then
    # CentOS/Amazon Linux没有sites-available目录
    mkdir -p /etc/nginx/conf.d
    cp /etc/nginx/sites-available/crypto-analyzer /etc/nginx/conf.d/crypto-analyzer.conf
fi

# 测试Nginx配置
nginx -t

# 重载Nginx
systemctl reload nginx

log_info "Nginx配置完成"

###############################################################################
# 步骤10: 配置防火墙
###############################################################################
log_step "步骤10: 配置防火墙"

# 检测防火墙类型
if command -v ufw &> /dev/null; then
    # Ubuntu - UFW
    log_info "配置UFW防火墙..."
    ufw allow $NGINX_PORT/tcp
    ufw allow 22/tcp
    ufw --force enable

elif command -v firewall-cmd &> /dev/null; then
    # CentOS/Amazon Linux - Firewalld
    log_info "配置Firewalld防火墙..."
    firewall-cmd --permanent --add-port=$NGINX_PORT/tcp
    firewall-cmd --permanent --add-service=ssh
    firewall-cmd --reload

else
    log_warn "未检测到防火墙，请手动配置"
    log_info "Amazon Linux 2023 默认使用 Security Groups，请在 AWS 控制台配置"
fi

###############################################################################
# 步骤11: 创建管理脚本
###############################################################################
log_step "步骤11: 创建管理脚本"

# 创建启动脚本
cat > $PROJECT_DIR/start.sh <<'EOF'
#!/bin/bash
sudo systemctl start crypto-analyzer
sudo systemctl status crypto-analyzer
EOF
chmod +x $PROJECT_DIR/start.sh

# 创建停止脚本
cat > $PROJECT_DIR/stop.sh <<'EOF'
#!/bin/bash
sudo systemctl stop crypto-analyzer
echo "服务已停止"
EOF
chmod +x $PROJECT_DIR/stop.sh

# 创建重启脚本
cat > $PROJECT_DIR/restart.sh <<'EOF'
#!/bin/bash
sudo systemctl restart crypto-analyzer
sudo systemctl status crypto-analyzer
EOF
chmod +x $PROJECT_DIR/restart.sh

# 创建查看日志脚本
cat > $PROJECT_DIR/logs.sh <<'EOF'
#!/bin/bash
tail -f /var/log/crypto-analyzer.log
EOF
chmod +x $PROJECT_DIR/logs.sh

# 创建状态检查脚本
cat > $PROJECT_DIR/status.sh <<'EOF'
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
EOF
chmod +x $PROJECT_DIR/status.sh

log_info "管理脚本创建完成"

###############################################################################
# 步骤12: 启动服务
###############################################################################
log_step "步骤12: 启动服务"

# 启动应用
systemctl start crypto-analyzer

# 等待服务启动
log_info "等待服务启动..."
sleep 5

# 检查服务状态
if systemctl is-active --quiet crypto-analyzer; then
    log_info "✅ Crypto Analyzer服务启动成功"
else
    log_error "❌ 服务启动失败，请查看日志:"
    log_error "journalctl -u crypto-analyzer -n 50"
fi

###############################################################################
# 完成
###############################################################################
log_step "部署完成!"

# 获取服务器IP
SERVER_IP=$(hostname -I | awk '{print $1}')

echo -e "\n${GREEN}========================================${NC}"
echo -e "${GREEN}    部署成功! 🎉🎉🎉${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "${BLUE}系统访问信息:${NC}"
echo -e "  访问地址: http://$SERVER_IP"
echo -e "  或: http://localhost (本机访问)"
echo -e "  端口: $NGINX_PORT"
echo ""

echo -e "${BLUE}数据库信息:${NC}"
echo -e "  数据库: $MYSQL_DATABASE"
echo -e "  用户: $MYSQL_USER"
echo -e "  密码: $MYSQL_PASSWORD"
echo -e "  Root密码: $MYSQL_ROOT_PASSWORD"
echo ""

echo -e "${BLUE}管理命令:${NC}"
echo -e "  启动服务: ./start.sh 或 sudo systemctl start crypto-analyzer"
echo -e "  停止服务: ./stop.sh 或 sudo systemctl stop crypto-analyzer"
echo -e "  重启服务: ./restart.sh 或 sudo systemctl restart crypto-analyzer"
echo -e "  查看状态: ./status.sh"
echo -e "  查看日志: ./logs.sh 或 tail -f /var/log/crypto-analyzer.log"
echo ""

echo -e "${BLUE}配置文件:${NC}"
echo -e "  应用配置: $PROJECT_DIR/config.yaml"
echo -e "  Nginx配置: /etc/nginx/sites-available/crypto-analyzer"
echo -e "  Systemd配置: /etc/systemd/system/crypto-analyzer.service"
echo ""

echo -e "${YELLOW}注意事项:${NC}"
echo -e "  1. 请妥善保管数据库密码"
echo -e "  2. 建议修改config.yaml中的API密钥"
echo -e "  3. 如需开启HTTPS，请配置SSL证书"
echo -e "  4. 定期备份数据库: mysqldump -u${MYSQL_USER} -p${MYSQL_PASSWORD} ${MYSQL_DATABASE} > backup.sql"
echo ""

echo -e "${GREEN}========================================${NC}\n"

# 显示服务状态
./status.sh

log_info "部署脚本执行完毕"
