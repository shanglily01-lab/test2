#!/usr/bin/env python3
"""
Hyperliquid代币映射更新工具
用于手动或定时更新代币映射缓存
"""
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import logging
from app.services.hyperliquid_token_mapper import get_token_mapper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def main():
    """主函数"""
    print("=" * 60)
    print("Hyperliquid 代币映射更新工具")
    print("=" * 60)
    print()

    # 获取映射器实例
    mapper = get_token_mapper()

    # 显示当前状态
    stats = mapper.get_stats()
    print("📊 当前状态:")
    print(f"  - 缓存代币数量: {stats['total_tokens']}")
    print(f"  - 最后更新时间: {stats['last_update'] or '从未更新'}")
    print(f"  - 缓存是否有效: {'✅ 是' if stats['cache_valid'] else '❌ 否（需要更新）'}")
    print(f"  - 缓存文件路径: {stats['cache_file']}")
    print()

    # 更新映射
    print("🔄 开始更新代币映射...")
    success = mapper.update_token_mapping(force=True)

    if success:
        print("✅ 更新成功！")
        print()

        # 显示更新后的统计
        new_stats = mapper.get_stats()
        print("📈 更新后统计:")
        print(f"  - 代币总数: {new_stats['total_tokens']}")
        print(f"  - 更新时间: {new_stats['last_update']}")
        print()

        # 显示前30个代币
        print("📋 前30个代币映射:")
        print("-" * 60)
        all_tokens = mapper.get_all_tokens()
        for i in range(min(30, len(all_tokens))):
            idx = f"@{i}"
            symbol = all_tokens.get(idx, 'N/A')
            print(f"  {idx:6s} -> {symbol}")

        if len(all_tokens) > 30:
            print(f"  ... 还有 {len(all_tokens) - 30} 个代币")

        print("-" * 60)
        print()

        # 测试几个常见代币
        print("🔍 测试常见代币:")
        test_symbols = ['BTC', 'ETH', 'SOL', 'ALT', 'DOGE']
        for sym in test_symbols:
            idx = mapper.get_index(sym)
            if idx:
                formatted = mapper.format_symbol(idx)
                print(f"  {sym:6s} -> {idx:6s} (显示: {formatted})")
            else:
                print(f"  {sym:6s} -> 未找到")

        print()
        print("✨ 所有操作完成！")
        return 0

    else:
        print("❌ 更新失败！")
        print()
        print("可能的原因:")
        print("  1. 网络连接问题")
        print("  2. Hyperliquid API暂时不可用")
        print("  3. API返回数据格式变化")
        print()
        print("建议:")
        print("  - 检查网络连接")
        print("  - 稍后重试")
        print("  - 查看详细日志")
        return 1


if __name__ == "__main__":
    sys.exit(main())
