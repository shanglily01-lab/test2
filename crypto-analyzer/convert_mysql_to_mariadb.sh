#!/bin/bash

###############################################################################
# MySQL 8.0 SQL文件转换为 MariaDB 兼容格式
# 功能: 将MySQL 8.0的排序规则转换为MariaDB 10.5兼容格式
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

# 检查参数
if [ -z "$1" ]; then
    log_error "请指定SQL文件"
    echo ""
    echo "使用方法:"
    echo "  bash convert_mysql_to_mariadb.sh <input_file> [output_file]"
    echo ""
    echo "示例:"
    echo "  bash convert_mysql_to_mariadb.sh binance-data.sql"
    echo "  bash convert_mysql_to_mariadb.sh binance-data.sql binance-data-mariadb.sql"
    echo "  bash convert_mysql_to_mariadb.sh binance-data.sql.gz binance-data-mariadb.sql.gz"
    exit 1
fi

INPUT_FILE="$1"
OUTPUT_FILE="${2:-${INPUT_FILE%.sql}-mariadb.sql}"

# 如果输入文件是.gz，输出文件也应该是.gz
if [[ "$INPUT_FILE" == *.gz ]] && [[ "$OUTPUT_FILE" != *.gz ]]; then
    OUTPUT_FILE="${OUTPUT_FILE%.sql}-mariadb.sql.gz"
fi

# 检查输入文件是否存在
if [ ! -f "$INPUT_FILE" ]; then
    log_error "文件不存在: $INPUT_FILE"
    exit 1
fi

log_info "输入文件: $INPUT_FILE"
log_info "输出文件: $OUTPUT_FILE"
echo ""

log_info "开始转换..."

# 转换逻辑
if [[ "$INPUT_FILE" == *.gz ]]; then
    # 处理压缩文件
    log_info "检测到压缩文件，解压并转换..."

    gunzip < "$INPUT_FILE" | \
        sed 's/utf8mb4_0900_ai_ci/utf8mb4_unicode_ci/g' | \
        sed 's/utf8_0900_ai_ci/utf8_unicode_ci/g' | \
        gzip > "$OUTPUT_FILE"
else
    # 处理普通文件
    sed 's/utf8mb4_0900_ai_ci/utf8mb4_unicode_ci/g' "$INPUT_FILE" | \
        sed 's/utf8_0900_ai_ci/utf8_unicode_ci/g' > "$OUTPUT_FILE"
fi

if [ $? -eq 0 ]; then
    log_info "✅ 转换成功"

    # 显示文件大小
    INPUT_SIZE=$(du -h "$INPUT_FILE" | cut -f1)
    OUTPUT_SIZE=$(du -h "$OUTPUT_FILE" | cut -f1)

    echo ""
    echo -e "${BLUE}文件信息:${NC}"
    echo "  原文件: $INPUT_FILE ($INPUT_SIZE)"
    echo "  新文件: $OUTPUT_FILE ($OUTPUT_SIZE)"
    echo ""

    echo -e "${BLUE}转换内容:${NC}"
    echo "  utf8mb4_0900_ai_ci → utf8mb4_unicode_ci"
    echo "  utf8_0900_ai_ci → utf8_unicode_ci"
    echo ""

    echo -e "${BLUE}下一步:${NC}"
    echo "  导入数据库: mysql -uroot -pTonny@1000 binance-data < $OUTPUT_FILE"
    echo ""
else
    log_error "❌ 转换失败"
    exit 1
fi
