#!/bin/bash
# 一键部署：停服务 → 加字段 → 重启服务

set -e  # 遇到错误立即退出

echo "=========================================="
echo "🚀 一键部署信号评分字段"
echo "=========================================="

# 数据库配置
DB_HOST="13.212.252.171"
DB_PORT="3306"
DB_USER="admin"
DB_PASS="Tonny@1000"
DB_NAME="binance-data"

# 1. 停止服务
echo ""
echo "🛑 步骤1: 停止 smart_trader_service.py"
pkill -f smart_trader_service.py || true
sleep 2

# 确认进程已停止
if pgrep -f smart_trader_service.py > /dev/null; then
    echo "❌ 服务未能停止，请手动停止后重试"
    exit 1
fi
echo "✅ 服务已停止"

# 2. 添加数据库字段
echo ""
echo "📝 步骤2: 添加数据库字段"

mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" "$DB_NAME" << 'EOF'
-- 添加 entry_score 字段
ALTER TABLE futures_positions
ADD COLUMN entry_score INT COMMENT '开仓得分' AFTER entry_signal_type;

-- 添加 signal_components 字段
ALTER TABLE futures_positions
ADD COLUMN signal_components TEXT COMMENT '信号组成（JSON格式）' AFTER entry_score;

-- 验证字段
SELECT
    Field,
    Type,
    Comment
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA='binance-data'
  AND TABLE_NAME='futures_positions'
  AND Field IN ('entry_score', 'signal_components');
EOF

if [ $? -eq 0 ]; then
    echo "✅ 字段添加成功"
else
    echo "❌ 字段添加失败"
    exit 1
fi

# 3. 重启服务
echo ""
echo "🚀 步骤3: 重启服务"
cd /root/crypto-analyzer

nohup python smart_trader_service.py > /dev/null 2>&1 &
sleep 3

# 检查服务是否启动
if pgrep -f smart_trader_service.py > /dev/null; then
    echo "✅ 服务已启动"
    PID=$(pgrep -f smart_trader_service.py)
    echo "   进程ID: $PID"
else
    echo "❌ 服务启动失败"
    exit 1
fi

# 4. 显示状态
echo ""
echo "=========================================="
echo "🎉 部署完成！"
echo "=========================================="
echo ""
echo "📊 查看日志:"
echo "   tail -f logs/smart_trader_*.log"
echo ""
echo "🧪 运行测试:"
echo "   python test_scoring_weight_system.py"
echo ""
echo "🔍 验证字段:"
echo "   mysql -h $DB_HOST -u $DB_USER -p'$DB_PASS' $DB_NAME -e \"SHOW COLUMNS FROM futures_positions WHERE Field IN ('entry_score', 'signal_components');\""
