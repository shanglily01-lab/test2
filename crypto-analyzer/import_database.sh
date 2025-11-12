#!/bin/bash

###############################################################################
# 数据库导入脚本
# 功能: 将SQL备份文件导入到服务器数据库
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
    log_info "使用命令: sudo bash import_database.sh <backup_file>"
    exit 1
fi

# 检查备份文件参数
if [ -z "$1" ]; then
    log_error "请指定备份文件"
    log_info "使用方法: sudo bash import_database.sh <backup_file>"
    echo ""
    echo "示例:"
    echo "  sudo bash import_database.sh backups/database_backup_20250112_120000.sql.gz"
    exit 1
fi

BACKUP_FILE="$1"

# 检查备份文件是否存在
if [ ! -f "$BACKUP_FILE" ]; then
    log_error "备份文件不存在: $BACKUP_FILE"
    exit 1
fi

# 从 config.yaml 读取数据库配置
DB_USER=$(grep -A 10 "mysql:" config.yaml | grep "user:" | head -1 | awk '{print $2}')
DB_PASSWORD=$(grep -A 10 "mysql:" config.yaml | grep "password:" | head -1 | awk '{print $2}')
DB_NAME=$(grep -A 10 "mysql:" config.yaml | grep "database:" | head -1 | awk '{print $2}')

log_info "从 config.yaml 读取的数据库配置:"
echo "  用户: $DB_USER"
echo "  数据库: $DB_NAME"
echo ""

log_info "备份文件: $BACKUP_FILE"
echo ""

read -p "确认导入此备份到数据库 ${DB_NAME}? (这将覆盖现有数据!) (y/n): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    log_warn "操作已取消"
    exit 0
fi

###############################################################################
# 步骤1: 备份现有数据库（如果存在）
###############################################################################
log_step "步骤1: 备份现有数据库"

if mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "USE \`${DB_NAME}\`;" 2>/dev/null; then
    log_info "发现现有数据库，先备份..."

    SAFETY_BACKUP="./backups/safety_backup_$(date +%Y%m%d_%H%M%S).sql.gz"
    mkdir -p ./backups

    mysqldump -u"${DB_USER}" -p"${DB_PASSWORD}" \
        --single-transaction \
        "${DB_NAME}" | gzip > "$SAFETY_BACKUP"

    log_info "✅ 安全备份已保存: $SAFETY_BACKUP"
else
    log_info "数据库不存在，跳过备份"
fi

###############################################################################
# 步骤2: 准备导入文件
###############################################################################
log_step "步骤2: 准备导入文件"

TEMP_SQL="/tmp/import_temp.sql"

# 检查文件是否是压缩的
if [[ "$BACKUP_FILE" == *.gz ]]; then
    log_info "解压备份文件..."
    gunzip -c "$BACKUP_FILE" > "$TEMP_SQL"
else
    log_info "使用未压缩的备份文件..."
    cp "$BACKUP_FILE" "$TEMP_SQL"
fi

log_info "✅ 导入文件已准备"

###############################################################################
# 步骤3: 重建数据库
###############################################################################
log_step "步骤3: 重建数据库"

log_info "删除现有数据库..."
mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "DROP DATABASE IF EXISTS \`${DB_NAME}\`;" 2>/dev/null || true

log_info "创建新数据库..."
mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

log_info "✅ 数据库已重建"

###############################################################################
# 步骤4: 导入数据
###############################################################################
log_step "步骤4: 导入数据"

log_info "开始导入数据，这可能需要几分钟..."

if mysql -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}" < "$TEMP_SQL" 2>/dev/null; then
    log_info "✅ 数据导入成功"
else
    log_error "❌ 数据导入失败"
    log_warn "尝试恢复安全备份..."

    if [ -f "$SAFETY_BACKUP" ]; then
        mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "DROP DATABASE IF EXISTS \`${DB_NAME}\`;"
        mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        gunzip -c "$SAFETY_BACKUP" | mysql -u"${DB_USER}" -p"${DB_PASSWORD}" "${DB_NAME}"
        log_info "已恢复到导入前的状态"
    fi

    rm -f "$TEMP_SQL"
    exit 1
fi

# 清理临时文件
rm -f "$TEMP_SQL"

###############################################################################
# 步骤5: 验证导入
###############################################################################
log_step "步骤5: 验证导入"

log_info "检查表结构..."
TABLE_COUNT=$(mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "USE \`${DB_NAME}\`; SHOW TABLES;" 2>/dev/null | wc -l)
TABLE_COUNT=$((TABLE_COUNT - 1))  # 减去表头

log_info "✅ 共导入 $TABLE_COUNT 个表"

log_info "显示表列表:"
mysql -u"${DB_USER}" -p"${DB_PASSWORD}" -e "USE \`${DB_NAME}\`; SHOW TABLES;" 2>/dev/null

###############################################################################
# 完成
###############################################################################
log_step "数据库导入完成!"

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}    导入成功! 🎉${NC}"
echo -e "${GREEN}========================================${NC}\n"

echo -e "${BLUE}导入信息:${NC}"
echo -e "  数据库名: $DB_NAME"
echo -e "  表数量: $TABLE_COUNT"
echo -e "  备份文件: $BACKUP_FILE"
if [ -f "$SAFETY_BACKUP" ]; then
    echo -e "  安全备份: $SAFETY_BACKUP"
fi
echo ""

echo -e "${BLUE}下一步操作:${NC}"
echo -e "  1. 重启应用服务: sudo systemctl restart crypto-analyzer"
echo -e "  2. 查看服务状态: ./status.sh"
echo -e "  3. 查看日志: ./logs.sh"
echo ""

log_info "导入脚本执行完毕"
