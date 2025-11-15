#!/usr/bin/env python3
"""
立即采集Gas数据脚本
可以手动运行来立即采集Gas数据，不依赖调度器
"""

import asyncio
import sys
from pathlib import Path
from datetime import date, timedelta

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from app.collectors.blockchain_gas_collector import BlockchainGasCollector
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


async def collect_gas_now(target_date=None):
    """
    立即采集Gas数据
    
    Args:
        target_date: 目标日期，默认为昨天。格式: 'YYYY-MM-DD' 或 date 对象
    """
    print("=" * 70)
    print("Gas数据采集脚本")
    print("=" * 70)
    
    try:
        # 解析目标日期
        if target_date is None:
            target_date = date.today() - timedelta(days=1)
            print(f"\n[INFO] 未指定日期，默认采集昨天的数据: {target_date}")
        elif isinstance(target_date, str):
            target_date = date.fromisoformat(target_date)
            print(f"\n[INFO] 采集指定日期的数据: {target_date}")
        elif isinstance(target_date, date):
            print(f"\n[INFO] 采集指定日期的数据: {target_date}")
        else:
            raise ValueError(f"无效的日期格式: {target_date}")
        
        # 初始化采集器
        print("\n[INFO] 初始化Gas采集器...")
        collector = BlockchainGasCollector()
        
        # 显示要采集的链
        chains = ['ethereum', 'bsc', 'polygon', 'arbitrum', 'optimism', 'avalanche']
        print(f"[INFO] 将采集以下链的数据: {', '.join(chains)}")
        print(f"[INFO] 目标日期: {target_date}")
        print("\n" + "-" * 70)
        print("开始采集...")
        print("-" * 70 + "\n")
        
        # 采集所有链
        await collector.collect_all_chains(target_date)
        
        print("\n" + "=" * 70)
        print("[SUCCESS] Gas数据采集完成！")
        print("=" * 70)
        print("\n提示：")
        print("1. 可以在Gas统计页面 (/blockchain_gas) 查看采集的数据")
        print("2. 如果看到 '使用基础估算数据' 的警告，建议配置API密钥获取更准确的数据")
        print("3. 自动采集任务会在每天01:00执行")
        print("\n")
        
    except KeyboardInterrupt:
        print("\n\n[WARN] 用户中断采集")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] 采集失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """主函数，支持命令行参数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='立即采集Gas数据')
    parser.add_argument(
        '--date',
        type=str,
        help='目标日期，格式: YYYY-MM-DD (默认: 昨天)',
        default=None
    )
    parser.add_argument(
        '--days-ago',
        type=int,
        help='采集N天前的数据 (例如: --days-ago 1 表示昨天)',
        default=None
    )
    
    args = parser.parse_args()
    
    # 确定目标日期
    target_date = None
    if args.date:
        target_date = args.date
    elif args.days_ago is not None:
        target_date = date.today() - timedelta(days=args.days_ago)
    
    # 运行采集
    asyncio.run(collect_gas_now(target_date))


if __name__ == "__main__":
    main()

