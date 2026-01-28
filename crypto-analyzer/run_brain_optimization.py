#!/usr/bin/env python3
"""运行超级大脑自优化"""
import sys
sys.path.insert(0, 'd:/test2/crypto-analyzer')

import asyncio
from datetime import datetime, timedelta
from app.services.auto_parameter_optimizer import AutoParameterOptimizer
import json

DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

async def run_optimization():
    """运行优化"""
    print("=" * 120)
    print("[BRAIN] 超级大脑自优化系统")
    print("=" * 120)
    print()

    optimizer = AutoParameterOptimizer(DB_CONFIG)

    # 基于昨天的数据进行优化
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    print(f"[DATA] 分析日期: {yesterday}")
    print(f"[TIME] 开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("正在分析历史交易数据并优化参数...")
    print()

    try:
        result = await optimizer.optimize_based_on_review(yesterday)

        if result['success']:
            print("\n" + "=" * 120)
            print("[SUCCESS] 优化成功!")
            print("=" * 120)
            print()

            print("[STATS] 优化统计:")
            if 'stats' in result:
                stats = result['stats']
                print(f"  - 样本数量: {stats.get('sample_size', 'N/A')}")
                print(f"  - 胜率: {stats.get('win_rate', 'N/A')}")
                print(f"  - 平均盈亏比: {stats.get('avg_pnl_ratio', 'N/A')}")
            print()

            print("[CONFIG] 当前参数配置:")
            print(json.dumps(result.get('current_params', {}), indent=2, ensure_ascii=False))
            print()

            if 'optimizations' in result:
                print("[CHANGES] 本次优化调整:")
                for opt in result['optimizations']:
                    print(f"  - {opt}")
                print()

        else:
            print("\n" + "=" * 120)
            print("[ERROR] 优化失败")
            print("=" * 120)
            print()
            print(f"错误: {result.get('error', '未知错误')}")
            if 'message' in result:
                print(f"消息: {result['message']}")

        optimizer.close()

    except Exception as e:
        print("\n" + "=" * 120)
        print("[ERROR] 运行出错")
        print("=" * 120)
        print(f"错误: {str(e)}")
        import traceback
        print(traceback.format_exc())

    print()
    print(f"[TIME] 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 120)

if __name__ == '__main__':
    asyncio.run(run_optimization())
