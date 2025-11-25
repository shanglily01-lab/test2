"""
强制检查策略执行 - 手动执行一次策略检查
"""
import sys
from pathlib import Path
import asyncio

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from app.services.strategy_executor import StrategyExecutor
from app.trading.futures_trading_engine import FuturesTradingEngine

# 加载配置
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

print("=" * 80)
print("强制检查策略执行")
print("=" * 80)
print()

# 初始化
futures_engine = FuturesTradingEngine(db_config)
executor = StrategyExecutor(db_config, futures_engine)

# 执行策略检查
async def check():
    try:
        print("开始执行策略检查...")
        await executor.check_and_execute_strategies()
        print("策略检查完成")
    except Exception as e:
        print(f"执行出错: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(check())

