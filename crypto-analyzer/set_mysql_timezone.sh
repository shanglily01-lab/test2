#!/bin/bash
# 设置MySQL时区为UTC+8

echo "======================================================================"
echo "  设置MySQL时区为UTC+8 (Asia/Shanghai)"
echo "======================================================================"
echo ""

# 数据库连接信息
DB_HOST="13.212.252.171"
DB_PORT="3306"
DB_USER="admin"
DB_PASS="Tonny@1000"
DB_NAME="binance-data"

echo "1️⃣ 检查当前时区设置..."
mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p"$DB_PASS" -e "
SELECT
    @@global.time_zone as '全局时区',
    @@session.time_zone as '会话时区',
    NOW() as '当前时间';
" 2>&1 | grep -v "Warning"

echo ""
echo "2️⃣ 设置全局时区为 +08:00 (UTC+8)..."
mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p"$DB_PASS" -e "
SET GLOBAL time_zone = '+08:00';
SET time_zone = '+08:00';
" 2>&1 | grep -v "Warning"

if [ $? -eq 0 ]; then
    echo "✅ 时区设置成功"
else
    echo "❌ 时区设置失败"
    exit 1
fi

echo ""
echo "3️⃣ 验证时区设置..."
mysql -h $DB_HOST -P $DB_PORT -u $DB_USER -p"$DB_PASS" -e "
SELECT
    @@global.time_zone as '全局时区',
    @@session.time_zone as '会话时区',
    NOW() as '当前时间(UTC+8)',
    UTC_TIMESTAMP() as 'UTC时间';
" 2>&1 | grep -v "Warning"

echo ""
echo "======================================================================"
echo "  设置完成"
echo "======================================================================"
echo ""
echo "⚠️  注意:"
echo "  - 时区设置会在MySQL重启后失效"
echo "  - 需要在 my.cnf 中添加配置以永久生效"
echo ""
echo "永久配置方法:"
echo "  1. 编辑 /etc/mysql/my.cnf 或 /etc/my.cnf"
echo "  2. 在 [mysqld] 部分添加:"
echo "     default-time-zone='+08:00'"
echo "  3. 重启MySQL: sudo systemctl restart mysql"
