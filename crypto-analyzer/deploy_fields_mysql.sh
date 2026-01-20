#!/bin/bash
# 直接用 MySQL 命令添加字段（最快最可靠的方式）

echo "=========================================="
echo "🚀 添加字段到 futures_positions 表"
echo "=========================================="

# 从 .env 读取数据库配置
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-3306}"
DB_USER="${DB_USER:-root}"
DB_NAME="${DB_NAME:-binance-data}"

echo ""
echo "📡 数据库配置:"
echo "  Host: $DB_HOST"
echo "  Port: $DB_PORT"
echo "  User: $DB_USER"
echo "  Database: $DB_NAME"
echo ""

# 方法1: 使用 add_signal_fields.sql 文件
echo "📝 方法1: 使用 SQL 文件"
echo "执行命令:"
echo "mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p $DB_NAME < add_signal_fields.sql"
echo ""

# 方法2: 直接执行 SQL
echo "📝 方法2: 直接执行 SQL"
echo ""
echo "添加 entry_score 字段:"
echo "ALTER TABLE futures_positions ADD COLUMN entry_score INT COMMENT '开仓得分' AFTER entry_signal_type;"
echo ""
echo "添加 signal_components 字段:"
echo "ALTER TABLE futures_positions ADD COLUMN signal_components TEXT COMMENT '信号组成（JSON格式）' AFTER entry_score;"
echo ""

# 提示用户选择
echo "=========================================="
echo "请选择执行方式:"
echo "  1. 使用 SQL 文件 (推荐)"
echo "  2. 手动复制上面的 SQL 执行"
echo "  3. 使用 Python 脚本"
echo "=========================================="
echo ""
echo "推荐命令:"
echo "mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p $DB_NAME < add_signal_fields.sql"
echo ""
echo "或者使用 Python:"
echo "python deploy_signal_fields_v2.py"
