"""
手动创建一个测试订单
"""
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from decimal import Decimal
from app.trading.futures_trading_engine import FuturesTradingEngine

# 加载配置文件
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

# 初始化交易引擎
engine = FuturesTradingEngine(db_config)

# 创建测试订单
print("=" * 80)
print("创建测试订单")
print("=" * 80)

# 使用BTC/USDT作为测试交易对
symbol = "BTC/USDT"
account_id = 2
position_side = "LONG"  # 做多
leverage = 5
quantity = Decimal("0.001")  # 0.001 BTC
stop_loss_pct = Decimal("3") / 100  # 3%止损
take_profit_pct = Decimal("6") / 100  # 6%止盈

print(f"交易对: {symbol}")
print(f"方向: {position_side}")
print(f"数量: {quantity}")
print(f"杠杆: {leverage}x")
print(f"止损: {stop_loss_pct * 100}%")
print(f"止盈: {take_profit_pct * 100}%")
print()

try:
    # 获取当前价格
    current_price = engine.get_current_price(symbol, use_realtime=True)
    print(f"当前价格: {current_price}")
    print()
    
    # 开仓
    print("正在创建订单...")
    result = engine.open_position(
        account_id=account_id,
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        leverage=leverage,
        limit_price=None,  # 市价单
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        source='manual_test',
        signal_id=None
    )
    
    if result.get('success'):
        print("=" * 80)
        print("[OK] 订单创建成功！")
        print("=" * 80)
        print(f"持仓ID: {result.get('position_id')}")
        print(f"订单ID: {result.get('order_id')}")
        print(f"入场价格: {result.get('entry_price')}")
        print(f"数量: {result.get('quantity')}")
        print(f"杠杆: {result.get('leverage')}x")
        print(f"保证金: {result.get('margin')}")
        print(f"止损价格: {result.get('stop_loss_price')}")
        print(f"止盈价格: {result.get('take_profit_price')}")
        print()
        print("可以在持仓页面查看这个订单")
    else:
        print("=" * 80)
        print("[FAIL] 订单创建失败")
        print("=" * 80)
        print(f"错误信息: {result.get('message')}")
        
except Exception as e:
    print("=" * 80)
    print("[ERROR] 创建订单时出错")
    print("=" * 80)
    print(f"错误: {e}")
    import traceback
    traceback.print_exc()

