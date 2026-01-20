#!/bin/bash
# 超级大脑自适应系统部署脚本

echo "========================================================================"
echo "超级大脑自适应系统部署"
echo "========================================================================"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 数据库配置
DB_HOST="13.212.252.171"
DB_USER="admin"
DB_PASS="Tonny@1000"
DB_NAME="binance-data"

echo ""
echo "步骤1: 检查数据库连接"
echo "------------------------------------------------------------------------"
if mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "SELECT 1;" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ 数据库连接成功${NC}"
else
    echo -e "${RED}❌ 数据库连接失败，请检查配置${NC}"
    exit 1
fi

echo ""
echo "步骤2: 检查现有表"
echo "------------------------------------------------------------------------"
EXISTING_TABLES=$(mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "SHOW TABLES;" 2>/dev/null | grep -E "adaptive_params|trading_blacklist|optimization_history" || echo "")

if [ -z "$EXISTING_TABLES" ]; then
    echo -e "${YELLOW}⚠️  自适应系统表不存在，需要创建${NC}"
else
    echo -e "${GREEN}找到以下表:${NC}"
    echo "$EXISTING_TABLES"

    echo ""
    read -p "是否要删除并重新创建这些表? (y/N): " confirm
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        echo "删除现有表..."
        mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "
            DROP TABLE IF EXISTS optimization_history;
            DROP TABLE IF EXISTS adaptive_params;
            DROP TABLE IF EXISTS trading_blacklist;
        " 2>/dev/null
        echo -e "${GREEN}✅ 旧表已删除${NC}"
    else
        echo -e "${YELLOW}⚠️  保留现有表，跳过创建步骤${NC}"
        exit 0
    fi
fi

echo ""
echo "步骤3: 创建数据库表"
echo "------------------------------------------------------------------------"

# 检查SQL文件是否存在
if [ ! -f "app/database/adaptive_params_schema.sql" ]; then
    echo -e "${RED}❌ 找不到 app/database/adaptive_params_schema.sql${NC}"
    exit 1
fi

# 导入SQL
if mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME < app/database/adaptive_params_schema.sql 2>/dev/null; then
    echo -e "${GREEN}✅ 数据库表创建成功${NC}"
else
    echo -e "${RED}❌ 数据库表创建失败${NC}"
    exit 1
fi

echo ""
echo "步骤4: 验证表结构"
echo "------------------------------------------------------------------------"

# 验证adaptive_params表
echo "检查 adaptive_params 表..."
PARAM_COUNT=$(mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "SELECT COUNT(*) as count FROM adaptive_params;" 2>/dev/null | tail -n 1)
if [ "$PARAM_COUNT" -eq 8 ]; then
    echo -e "${GREEN}✅ adaptive_params 表: 8个参数已初始化${NC}"
else
    echo -e "${YELLOW}⚠️  adaptive_params 表: 参数数量 = $PARAM_COUNT (预期: 8)${NC}"
fi

# 验证trading_blacklist表
echo "检查 trading_blacklist 表..."
BLACKLIST_COUNT=$(mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "SELECT COUNT(*) as count FROM trading_blacklist;" 2>/dev/null | tail -n 1)
if [ "$BLACKLIST_COUNT" -ge 0 ]; then
    echo -e "${GREEN}✅ trading_blacklist 表: $BLACKLIST_COUNT 个黑名单交易对${NC}"
fi

# 验证optimization_history表
echo "检查 optimization_history 表..."
mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "SELECT 1 FROM optimization_history LIMIT 1;" > /dev/null 2>&1
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ optimization_history 表已创建${NC}"
fi

echo ""
echo "步骤5: 显示初始参数"
echo "------------------------------------------------------------------------"
mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "
SELECT
    param_key as '参数名',
    param_value as '值',
    param_type as '类型',
    description as '说明'
FROM adaptive_params
ORDER BY param_type, param_key;
" 2>/dev/null

echo ""
echo "步骤6: 显示黑名单"
echo "------------------------------------------------------------------------"
BLACKLIST=$(mysql -h $DB_HOST -u $DB_USER -p$DB_PASS -D $DB_NAME -e "
SELECT
    symbol as '交易对',
    reason as '原因',
    total_loss as '总亏损',
    is_active as '激活'
FROM trading_blacklist
WHERE is_active = TRUE
ORDER BY total_loss ASC;
" 2>/dev/null)

if [ -z "$BLACKLIST" ] || [ $(echo "$BLACKLIST" | wc -l) -le 1 ]; then
    echo -e "${YELLOW}当前黑名单为空${NC}"
else
    echo "$BLACKLIST"
fi

echo ""
echo "========================================================================"
echo -e "${GREEN}✅ 部署完成！${NC}"
echo "========================================================================"
echo ""
echo "接下来的步骤:"
echo "1. 重启智能交易服务:"
echo "   kill \$(pgrep -f smart_trader_service.py)"
echo "   nohup python3 smart_trader_service.py > logs/smart_trader.log 2>&1 &"
echo ""
echo "2. 查看启动日志:"
echo "   tail -50 logs/smart_trader.log"
echo ""
echo "3. 验证配置加载:"
echo "   tail -100 logs/smart_trader.log | grep '从数据库加载配置'"
echo ""
echo "4. 运行交易分析:"
echo "   python3 analyze_trading_performance.py"
echo ""
echo "========================================================================"
