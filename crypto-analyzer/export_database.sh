#!/bin/bash

###############################################################################
# 数据库导出脚本
# 功能: 从本地导出数据库到SQL文件
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

# 从 config.yaml 读取数据库配置
DB_USER=$(grep -A 10 "mysql:" config.yaml | grep "user:" | head -1 | awk '{print $2}')
DB_PASSWORD=$(grep -A 10 "mysql:" config.yaml | grep "password:" | head -1 | awk '{print $2}')
DB_NAME=$(grep -A 10 "mysql:" config.yaml | grep "database:" | head -1 | awk '{print $2}')
DB_HOST=$(grep -A 10 "mysql:" config.yaml | grep "host:" | head -1 | awk '{print $2}')

log_info "从 config.yaml 读取的数据库配置:"
echo "  主机: $DB_HOST"
echo "  用户: $DB_USER"
echo "  数据库: $DB_NAME"
echo ""

# 生成备份文件名
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="database_backup_${TIMESTAMP}.sql"
BACKUP_DIR="./backups"

# 创建备份目录
mkdir -p $BACKUP_DIR

log_step "开始导出数据库"

# 导出数据库
log_info "导出数据库到: $BACKUP_DIR/$BACKUP_FILE"

if mysqldump -h"${DB_HOST}" -u"${DB_USER}" -p"${DB_PASSWORD}" \
    --single-transaction \
    --routines \
    --triggers \
    --events \
    "${DB_NAME}" > "$BACKUP_DIR/$BACKUP_FILE" 2>/dev/null; then

    log_info "✅ 数据库导出成功"

    # 压缩备份文件
    log_info "压缩备份文件..."
    gzip "$BACKUP_DIR/$BACKUP_FILE"
    BACKUP_FILE="${BACKUP_FILE}.gz"

    # 显示文件大小
    FILE_SIZE=$(du -h "$BACKUP_DIR/$BACKUP_FILE" | cut -f1)
    log_info "备份文件大小: $FILE_SIZE"

else
    log_error "❌ 数据库导出失败"
    exit 1
fi

log_step "导出完成!"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}    导出成功! 🎉${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "${BLUE}备份文件位置:${NC}"
echo -e "  $BACKUP_DIR/$BACKUP_FILE"
echo ""

echo -e "${BLUE}下一步操作:${NC}"
echo -e "  1. 将备份文件上传到服务器:"
echo -e "     scp $BACKUP_DIR/$BACKUP_FILE user@server:/path/to/crypto-analyzer/backups/"
echo ""
echo -e "  2. 在服务器上运行导入脚本:"
echo -e "     sudo bash import_database.sh $BACKUP_FILE"
echo ""

log_info "导出脚本执行完毕"
