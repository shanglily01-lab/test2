#!/usr/bin/env python3
"""
测试 EMA 信号监控功能

验证:
1. EMA 计算是否正确
2. 金叉检测是否正常
3. 信号强度评估是否合理
4. 通知功能是否工作
"""

import asyncio
import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
from loguru import logger
from app.database.db_service import DatabaseService
from app.trading.ema_signal_monitor import EMASignalMonitor
from app.services.notification_service import NotificationService


async def test_ema_signal():
    """测试 EMA 信号监控功能"""

    # 加载配置
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 强制使用 1h 周期进行测试（数据库中有数据）
    config['ema_signal'] = {
        'enabled': True,
        'short_period': 9,
        'long_period': 21,
        'timeframe': '1h',  # 使用 1小时周期（数据库中有 3,991 条记录）
        'volume_threshold': 1.5
    }

    if 'notification' not in config:
        config['notification'] = {
            'log': True,
            'file': True,
            'alert_file': 'signals/ema_alerts_test.txt',
            'email': False,
            'telegram': False
        }

    logger.info("=" * 80)
    logger.info("EMA 信号监控功能测试")
    logger.info("=" * 80)

    # 1. 初始化服务
    logger.info("\n📊 步骤1: 初始化服务")
    try:
        db_config = config.get('database', {})
        db_service = DatabaseService(db_config)
        logger.info("✓ 数据库服务初始化成功")

        ema_monitor = EMASignalMonitor(config, db_service)
        logger.info(f"✓ EMA 监控器初始化成功 (EMA{ema_monitor.short_period}/EMA{ema_monitor.long_period})")

        notification_service = NotificationService(config)
        logger.info("✓ 通知服务初始化成功")

    except Exception as e:
        logger.error(f"✗ 服务初始化失败: {e}")
        return

    # 2. 测试 EMA 计算
    logger.info("\n📊 步骤2: 测试 EMA 计算")
    test_prices = [100, 102, 101, 103, 105, 104, 106, 108, 107, 109, 111, 110]
    try:
        ema_9 = ema_monitor.calculate_ema(test_prices, 9)
        ema_21 = ema_monitor.calculate_ema(test_prices, 21)

        logger.info(f"✓ EMA计算成功")
        logger.info(f"  测试数据 (12个价格): {test_prices}")
        logger.info(f"  EMA9: {ema_9:.2f}" if ema_9 else "  EMA9: 数据不足")
        logger.info(f"  EMA21: {ema_21:.2f}" if ema_21 else "  EMA21: 数据不足")

    except Exception as e:
        logger.error(f"✗ EMA 计算失败: {e}")

    # 3. 测试信号检测
    logger.info("\n📊 步骤3: 扫描实际信号")
    try:
        # 测试单个交易对
        test_symbol = config.get('symbols', ['BTC/USDT'])[0]
        logger.info(f"测试交易对: {test_symbol}")

        signal = await ema_monitor.check_symbol(test_symbol)

        if signal:
            logger.info(f"✓ 发现买入信号！")
            logger.info(f"  交易对: {signal['symbol']}")
            logger.info(f"  信号强度: {signal['signal_strength'].upper()}")
            logger.info(f"  当前价格: ${signal['price']:.2f}")
            logger.info(f"  涨幅: {signal['price_change_pct']:+.2f}%")
            logger.info(f"  短期EMA: {signal['short_ema']:.2f}")
            logger.info(f"  长期EMA: {signal['long_ema']:.2f}")
            logger.info(f"  成交量放大: {signal['volume_ratio']:.2f}x")
        else:
            logger.info(f"  未发现买入信号")

    except Exception as e:
        logger.error(f"✗ 信号检测失败: {e}")

    # 4. 扫描所有交易对
    logger.info("\n📊 步骤4: 扫描所有交易对")
    try:
        signals = await ema_monitor.scan_all_symbols()

        if signals:
            logger.info(f"✓ 发现 {len(signals)} 个买入信号")

            # 统计信号强度
            strong = len([s for s in signals if s['signal_strength'] == 'strong'])
            medium = len([s for s in signals if s['signal_strength'] == 'medium'])
            weak = len([s for s in signals if s['signal_strength'] == 'weak'])

            logger.info(f"  信号强度分布:")
            logger.info(f"    强: {strong} 个")
            logger.info(f"    中: {medium} 个")
            logger.info(f"    弱: {weak} 个")

            # 显示信号详情
            for signal in signals:
                logger.info(f"\n  {signal['signal_strength'].upper()} 信号:")
                logger.info(f"    {signal['symbol']}: ${signal['price']:.2f} ({signal['price_change_pct']:+.2f}%)")
                logger.info(f"    成交量: {signal['volume_ratio']:.2f}x")

        else:
            logger.info("  未发现买入信号")

    except Exception as e:
        logger.error(f"✗ 扫描失败: {e}")

    # 5. 测试通知功能
    logger.info("\n📊 步骤5: 测试通知功能")
    try:
        if signals:
            logger.info("发送测试通知...")

            notification_service.send_batch_signals(
                signals,
                ema_monitor.format_alert_message
            )

            logger.info("✓ 通知已发送")
            logger.info(f"  检查文件: {config['notification']['alert_file']}")

        else:
            # 创建一个模拟信号用于测试
            test_signal = {
                'symbol': 'TEST/USDT',
                'timeframe': '15m',
                'signal_type': 'BUY',
                'signal_strength': 'medium',
                'timestamp': __import__('datetime').datetime.now(),
                'price': 100.00,
                'short_ema': 99.50,
                'long_ema': 99.00,
                'ema_config': 'EMA9/EMA21',
                'volume_ratio': 2.5,
                'price_change_pct': 1.5,
                'ema_distance_pct': 0.5,
                'details': {
                    'short_ema_prev': 99.00,
                    'long_ema_prev': 99.20,
                    'avg_volume': 1000000,
                    'current_volume': 2500000
                }
            }

            message = ema_monitor.format_alert_message(test_signal)
            notification_service.send_ema_signal(test_signal, message)

            logger.info("✓ 测试通知已发送（模拟信号）")

    except Exception as e:
        logger.error(f"✗ 通知测试失败: {e}")

    logger.info("\n" + "=" * 80)
    logger.info("测试完成")
    logger.info("=" * 80)

    # 6. 给出建议
    logger.info("\n💡 使用建议:")
    logger.info("1. 将 config_ema_example.yaml 中的配置添加到 config.yaml")
    logger.info("2. 根据需要调整 EMA 周期和阈值")
    logger.info("3. 配置邮件或 Telegram 通知（可选）")
    logger.info("4. 重启 scheduler.py 启用监控")
    logger.info("5. 查看 signals/ema_alerts.txt 文件获取历史信号")


if __name__ == '__main__':
    asyncio.run(test_ema_signal())
