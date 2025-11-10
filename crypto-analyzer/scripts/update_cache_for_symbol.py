#!/usr/bin/env python3
"""
手动更新指定交易对的缓存
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import yaml
from loguru import logger
from app.services.cache_update_service import CacheUpdateService
from app.database.db_service import DatabaseService

async def update_cache_for_symbols(symbols):
    """更新指定交易对的缓存"""
    # 加载配置
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    
    # 初始化服务
    cache_service = CacheUpdateService(config)
    db_service = DatabaseService(config.get('database', {}))
    
    print("\n" + "="*80)
    print("手动更新缓存")
    print("="*80)
    
    for symbol in symbols:
        print(f"\n更新 {symbol} 的缓存...")
        print("-" * 80)
        
        try:
            # 检查是否有足够的K线数据
            latest_1m = db_service.get_latest_kline(symbol, '1m')
            if not latest_1m:
                print(f"  [ERROR] 没有1分钟K线数据，无法更新价格统计缓存")
                continue
            
            print(f"  [OK] 最新1分钟K线: {latest_1m.timestamp}")
            
            # 更新价格统计缓存
            print(f"  [INFO] 更新价格统计缓存...")
            await cache_service.update_price_stats_cache([symbol])
            print(f"  [OK] 价格统计缓存已更新")
            
            # 检查是否有资金费率数据
            latest_funding = db_service.get_latest_funding_rate(symbol)
            if latest_funding:
                print(f"  [INFO] 更新资金费率统计缓存...")
                await cache_service.update_funding_rate_stats([symbol])
                print(f"  [OK] 资金费率统计缓存已更新")
            else:
                print(f"  [WARN] 没有资金费率数据，跳过资金费率缓存更新")
            
        except Exception as e:
            print(f"  [ERROR] 更新失败: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "="*80)
    print("缓存更新完成！")
    print("现在可以刷新 Dashboard 查看数据")
    print("="*80)

if __name__ == "__main__":
    symbols_to_update = ['AR/USDT']
    asyncio.run(update_cache_for_symbols(symbols_to_update))

