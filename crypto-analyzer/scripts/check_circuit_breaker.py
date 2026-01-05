#!/usr/bin/env python3
"""检查熔断器状态"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from app.utils.config_loader import load_config
from app.services.circuit_breaker import CircuitBreaker


async def main():
    """主函数"""
    print("=" * 80)
    print("熔断器状态检查")
    print("=" * 80)

    # 加载配置
    config = load_config()
    db_config = config['database']['mysql']

    # 初始化熔断器
    breaker = CircuitBreaker(db_config)

    # 检查当前状态
    status = breaker.get_status()

    if status['active']:
        print("\n⚠️  熔断器状态: 已激活")
        print(f"激活时间: {status['activated_at']}")
        print(f"冷却时间: {status['cooldown_hours']} 小时")
        print(f"状态: {status['status_message']}")

        if status['should_resume']:
            print(f"\n✅ 可以恢复交易")
            response = input("是否立即恢复交易? (y/n): ")
            if response.lower() == 'y':
                await breaker.resume()
                print("✅ 交易已恢复")
            else:
                print("取消恢复")
        else:
            print(f"\n⏳ 冷却中，请等待...")

    else:
        print("\n✅ 熔断器状态: 未激活")

        # 检查是否应该触发
        should_trigger, reason = breaker.check_should_trigger()
        if should_trigger:
            print("\n⚠️  警告: 检测到熔断条件!")
            print(reason)
            response = input("是否立即触发熔断? (y/n): ")
            if response.lower() == 'y':
                await breaker.activate(reason)
                print("🔴 熔断已激活")
            else:
                print("取消触发")
        else:
            print("✅ 无熔断风险")

    print("\n" + "=" * 80)


if __name__ == '__main__':
    asyncio.run(main())
