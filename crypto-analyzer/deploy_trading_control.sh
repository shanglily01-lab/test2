#!/bin/bash
# 快速部署交易控制功能脚本

echo "=========================================="
echo "部署交易控制功能"
echo "=========================================="

# 1. 拉取最新代码
echo "1. 拉取最新代码..."
git pull

# 2. 创建数据库表（如果还没创建）
echo ""
echo "2. 创建交易控制表..."
python3 -c "
import pymysql
from dotenv import load_dotenv
import os

load_dotenv()

db_config = {
    'host': os.getenv('DB_HOST', '13.212.252.171'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'user': os.getenv('DB_USER', 'admin'),
    'password': os.getenv('DB_PASSWORD'),
    'database': os.getenv('DB_NAME', 'binance-data'),
    'charset': 'utf8mb4'
}

try:
    conn = pymysql.connect(**db_config)
    cursor = conn.cursor()

    # 检查表是否存在
    cursor.execute(\"SHOW TABLES LIKE 'trading_control'\")
    if cursor.fetchone():
        print('   ✓ trading_control 表已存在')
    else:
        print('   创建 trading_control 表...')
        with open('scripts/migrations/023_create_trading_control_table.sql', 'r', encoding='utf-8') as f:
            sql = f.read()
            statements = [s.strip() for s in sql.split(';') if s.strip()]
            for stmt in statements:
                cursor.execute(stmt)
        conn.commit()
        print('   ✓ trading_control 表创建成功')

    cursor.close()
    conn.close()
except Exception as e:
    print(f'   ✗ 数据库操作失败: {e}')
    exit(1)
"

if [ $? -ne 0 ]; then
    echo "数据库表创建失败，请检查错误"
    exit 1
fi

# 3. 重启服务
echo ""
echo "3. 重启服务..."
echo "   请手动重启以下服务:"
echo "   - FastAPI主服务 (app/main.py)"
echo "   - U本位合约服务 (smart_trader_service.py)"
echo "   - 币本位合约服务 (coin_futures_trader_service.py)"
echo ""
echo "重启命令示例:"
echo "   supervisorctl restart crypto-api"
echo "   supervisorctl restart smart-trader"
echo "   supervisorctl restart coin-futures-trader"
echo ""
echo "或者使用 pm2:"
echo "   pm2 restart crypto-api"
echo "   pm2 restart smart-trader"
echo "   pm2 restart coin-futures-trader"

echo ""
echo "=========================================="
echo "部署完成！"
echo "=========================================="
echo ""
echo "功能说明:"
echo "1. 访问合约交易页面 (http://13.212.252.171:9020/futures_trading)"
echo "2. 点击右上角的交易控制按钮"
echo "3. 可以停止/启动自动交易"
echo ""
echo "API端点:"
echo "- GET  /api/trading-control/status/{account_id}/{trading_type}"
echo "- POST /api/trading-control/toggle"
echo "- GET  /api/trading-control/all"
