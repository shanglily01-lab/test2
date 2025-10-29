#!/usr/bin/env python3
"""
立即扫描一次EMA信号 - 测试工具
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import asyncio
import yaml
from loguru import logger
from app.database.db_service import DatabaseService
from app.trading.ema_signal_monitor import EMASignalMonitor

async def main():
    """立即扫描EMA信号"""

    print("=" * 70)
    print("EMA信号扫描 - 立即执行")
    print("=" * 70)

    # 加载配置
    config_path = project_root / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取EMA配置
    ema_config = config.get('ema_signal', {})
    print(f"\nEMA配置:")
    print(f"  启用状态: {ema_config.get('enabled', True)}")
    print(f"  时间周期: {ema_config.get('timeframe', '15m')}")
    print(f"  短周期EMA: {ema_config.get('short_period', 9)}")
    print(f"  长周期EMA: {ema_config.get('long_period', 21)}")
    print(f"  监控币种: {config.get('symbols', [])}")

    if not ema_config.get('enabled', True):
        print("\n⚠️  EMA信号监控未启用，请在config.yaml中设置 ema_signal.enabled = true")
        return

    # 初始化数据库服务
    print("\n初始化数据库服务...")
    db_service = DatabaseService(config.get('database', {}))
    print("✅ 数据库连接成功")

    # 初始化EMA监控器
    print("\n初始化EMA监控器...")
    ema_monitor = EMASignalMonitor(config, db_service)
    print(f"✅ EMA监控器初始化成功 (EMA{ema_monitor.short_period}/EMA{ema_monitor.long_period})")

    # 扫描信号
    print("\n开始扫描所有币种的EMA信号...")
    print("-" * 70)

    signals = await ema_monitor.scan_all_symbols()

    print("-" * 70)
    print(f"\n扫描结果: 发现 {len(signals)} 个信号")

    if signals:
        # 统计信号类型和强度
        buy_signals = [s for s in signals if s['signal_type'] == 'BUY']
        sell_signals = [s for s in signals if s['signal_type'] == 'SELL']

        strong = len([s for s in signals if s['signal_strength'] == 'strong'])
        medium = len([s for s in signals if s['signal_strength'] == 'medium'])
        weak = len([s for s in signals if s['signal_strength'] == 'weak'])

        print(f"\n信号类型:")
        print(f"  买入信号: {len(buy_signals)}")
        print(f"  卖出信号: {len(sell_signals)}")

        print(f"\n信号强度:")
        print(f"  强: {strong}")
        print(f"  中: {medium}")
        print(f"  弱: {weak}")

        print(f"\n详细信号:")
        for i, signal in enumerate(signals, 1):
            print(f"\n  {i}. {signal['symbol']} - {signal['signal_type']} ({signal['signal_strength']})")
            print(f"     价格: ${signal['price']:.4f}")
            print(f"     短EMA: {signal['short_ema']:.4f}, 长EMA: {signal['long_ema']:.4f}")
            print(f"     涨跌幅: {signal['price_change_pct']:+.2f}%")
            print(f"     成交量倍数: {signal['volume_ratio']:.1f}x")
            print(f"     EMA距离: {signal['ema_distance_pct']:.2f}%")
    else:
        print("\n✓ 未发现符合条件的EMA信号")
        print("\n可能原因:")
        print("  1. 市场当前没有明显的EMA交叉信号")
        print("  2. K线数据不足（需要至少30根K线）")
        print("  3. 信号强度过滤太严格")
        print("\n建议:")
        print("  - 等待15分钟后再次扫描")
        print("  - 检查数据库中的K线数据是否充足")
        print("  - 调整config.yaml中的EMA参数")

    print("\n" + "=" * 70)
    print("扫描完成")
    print("=" * 70)

if __name__ == '__main__':
    asyncio.run(main())
