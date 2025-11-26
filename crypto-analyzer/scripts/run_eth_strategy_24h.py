"""
运行 ETH 策略 - 检查最近24小时的数据
"""
import sys
from pathlib import Path
import asyncio
import json

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from loguru import logger
from app.services.strategy_executor import StrategyExecutor
from app.trading.futures_trading_engine import FuturesTradingEngine

# 加载配置
config_file = project_root / 'config.yaml'
with open(config_file, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

db_config = config.get('database', {}).get('mysql', {})

print("=" * 80)
print("运行 ETH 策略 - 检查最近24小时的数据")
print("=" * 80)
print()

# 加载策略配置
strategies_file = project_root / 'config' / 'strategies' / 'futures_strategies.json'
with open(strategies_file, 'r', encoding='utf-8') as f:
    all_strategies = json.load(f)

# 找到 ETH 策略
eth_strategy = None
for strategy in all_strategies:
    if strategy.get('name') == 'ETH' and 'ETH/USDT' in strategy.get('symbols', []):
        eth_strategy = strategy
        break

if not eth_strategy:
    print("错误: 未找到 ETH 策略")
    sys.exit(1)

print(f"找到策略: {eth_strategy.get('name')}")
print(f"策略ID: {eth_strategy.get('id')}")
print(f"交易对: {eth_strategy.get('symbols')}")
print(f"账户ID: {eth_strategy.get('account_id', 2)}")
print()

# 初始化
logger.info("初始化合约交易引擎...")
futures_engine = FuturesTradingEngine(db_config)
logger.info("初始化策略执行器...")
executor = StrategyExecutor(db_config, futures_engine)

# 执行策略
async def run_eth_strategy():
    try:
        account_id = eth_strategy.get('account_id', 2)
        logger.info(f"开始执行 ETH 策略 (账户ID: {account_id})...")
        result = await executor.execute_strategy(eth_strategy, account_id=account_id)
        
        print()
        print("=" * 80)
        print("执行结果:")
        print("=" * 80)
        
        if result.get('success'):
            results = result.get('results', [])
            print(f"执行成功！共执行了 {len(results)} 个操作")
            if results:
                print("\n操作详情:")
                for i, op in enumerate(results, 1):
                    print(f"  {i}. {op.get('action', 'unknown')} - {op.get('symbol', 'unknown')} - {op.get('direction', 'unknown')}")
                    if op.get('price'):
                        print(f"     价格: {op.get('price')}")
                    if op.get('quantity'):
                        print(f"     数量: {op.get('quantity')}")
                    if op.get('success'):
                        print(f"     状态: 成功")
                    else:
                        print(f"     状态: 失败")
            else:
                print("  没有执行任何交易操作（可能没有满足条件的信号）")
        else:
            print(f"执行失败: {result.get('message', '未知错误')}")
        
        print("=" * 80)
        
    except Exception as e:
        logger.error(f"执行出错: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(run_eth_strategy())


