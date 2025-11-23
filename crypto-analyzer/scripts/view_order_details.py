"""
查看订单详情
"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pymysql
import yaml

# 加载配置文件
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

connection = pymysql.connect(
    host=db_config.get('host', 'localhost'),
    port=db_config.get('port', 3306),
    user=db_config.get('user', 'root'),
    password=db_config.get('password', ''),
    database=db_config.get('database', 'binance-data'),
    charset='utf8mb4',
    cursorclass=pymysql.cursors.DictCursor
)

try:
    cursor = connection.cursor()
    
    # 查询最新的持仓
    cursor.execute("""
        SELECT * FROM futures_positions
        WHERE status = 'open'
        ORDER BY created_at DESC
        LIMIT 5
    """)
    positions = cursor.fetchall()
    
    print("=" * 80)
    print("当前持仓详情")
    print("=" * 80)
    
    for pos in positions:
        print(f"\n持仓ID: {pos['id']}")
        print(f"交易对: {pos['symbol']}")
        print(f"方向: {pos['position_side']}")
        print(f"数量: {pos['quantity']}")
        print(f"入场价: {pos['entry_price']}")
        print(f"标记价: {pos['mark_price']}")
        print(f"杠杆: {pos['leverage']}x")
        print(f"保证金: {pos['margin']} USDT")
        print(f"名义价值: {pos['notional_value']} USDT")
        print(f"未实现盈亏: {pos['unrealized_pnl']} USDT")
        print(f"止损价: {pos['stop_loss_price']}")
        print(f"止盈价: {pos['take_profit_price']}")
        print(f"强平价: {pos['liquidation_price']}")
        print(f"来源: {pos['source']}")
        print(f"创建时间: {pos['created_at']}")
        print("-" * 80)
    
    # 查询相关的订单
    if positions:
        position_id = positions[0]['id']
        cursor.execute("""
            SELECT * FROM futures_orders
            WHERE position_id = %s
            ORDER BY created_at DESC
        """, (position_id,))
        orders = cursor.fetchall()
        
        print(f"\n相关订单 (持仓ID: {position_id}):")
        for order in orders:
            print(f"\n订单ID: {order['order_id']}")
            print(f"类型: {order['order_type']}")
            print(f"方向: {order['side']}")
            print(f"价格: {order['price']}")
            print(f"数量: {order['quantity']}")
            print(f"已成交数量: {order['executed_quantity']}")
            print(f"状态: {order['status']}")
            print(f"手续费: {order['fee']} USDT")
            print(f"来源: {order['order_source']}")
            print(f"创建时间: {order['created_at']}")
            print("-" * 80)
    
finally:
    cursor.close()
    connection.close()

