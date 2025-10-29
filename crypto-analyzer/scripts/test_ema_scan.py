#!/usr/bin/env python3
"""
EMA信号手动扫描测试工具

使用方法:
    python scripts/test_ema_scan.py
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import asyncio
import yaml
from app.trading.ema_signal_monitor import EMASignalMonitor
from app.database.db_service import DatabaseService

async def main():
    print("=" * 80)
    print("EMA 信号手动扫描测试")
    print("=" * 80)
    print()

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    ema_config = config.get('ema_signal', {})

    if not ema_config.get('enabled', False):
        print("❌ EMA监控未启用！")
        print("   请在 config.yaml 中设置 ema_signal.enabled = true")
        return

    print("📋 配置信息:")
    print(f"   短期EMA: {ema_config.get('short_period', 9)}")
    print(f"   长期EMA: {ema_config.get('long_period', 21)}")
    print(f"   时间周期: {ema_config.get('timeframe', '15m')}")
    print(f"   成交量阈值: {ema_config.get('volume_threshold', 1.5)}")
    print()

    # 初始化服务
    print("🔧 初始化服务...")
    try:
        db_service = DatabaseService(config)
        print("   ✅ 数据库服务初始化成功")

        ema_monitor = EMASignalMonitor(config, db_service)
        print(f"   ✅ EMA监控初始化成功 (监控 {len(ema_monitor.symbols)} 个交易对)")
        print()
    except Exception as e:
        print(f"   ❌ 初始化失败: {e}")
        import traceback
        traceback.print_exc()
        return

    # 扫描信号
    print("🔍 开始扫描 EMA 信号...")
    print("-" * 80)
    print()

    try:
        signals = await ema_monitor.scan_all_symbols()

        if not signals:
            print("ℹ️  当前没有发现 EMA 交叉信号")
            print()
            print("可能原因:")
            print("  1. 市场目前没有EMA交叉")
            print("  2. K线数据不足（需要至少 21 根K线）")
            print("  3. 成交量不满足阈值要求")
            print()
            print("建议:")
            print("  - 等待市场出现EMA交叉")
            print("  - 或者调整config.yaml中的 volume_threshold 参数")
        else:
            print(f"✅ 发现 {len(signals)} 个信号:")
            print()
            print(f"{'交易对':<15} {'信号类型':<10} {'短期EMA':<12} {'长期EMA':<12} {'成交量比':<10} {'当前价格'}")
            print("-" * 80)

            for signal in signals:
                symbol = signal['symbol']
                signal_type = signal['signal_type']
                short_ema = signal['short_ema']
                long_ema = signal['long_ema']
                volume_ratio = signal.get('volume_ratio', 0)
                current_price = signal.get('current_price', 0)

                print(f"{symbol:<15} {signal_type:<10} {short_ema:<12.4f} {long_ema:<12.4f} {volume_ratio:<10.2f}x {current_price:.4f}")

            print()
            print("💾 信号已保存到数据库")

    except Exception as e:
        print(f"❌ 扫描失败: {e}")
        import traceback
        traceback.print_exc()
        return

    print()
    print("=" * 80)
    print("✅ 测试完成")
    print()
    print("下一步:")
    print("  1. 运行 python scripts/check_ema_signals.py 查看数据库中的信号")
    print("  2. 确保 scheduler.py 在运行，以便自动监控")


if __name__ == '__main__':
    asyncio.run(main())
