#!/usr/bin/env python3
"""
测试Telegram通知功能
"""
import yaml
from pathlib import Path
from app.services.trade_notifier import init_trade_notifier

def test_telegram_notification():
    """测试TG通知"""
    # 加载配置
    config_path = Path(__file__).parent / 'config.yaml'
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    print("=" * 60)
    print("  测试Telegram通知功能")
    print("=" * 60)
    print()

    # 检查配置
    tg_config = config.get('notifications', {}).get('telegram', {})
    if not tg_config.get('enabled'):
        print("❌ Telegram通知未启用")
        print("   请在 config.yaml 中设置:")
        print("   notifications:")
        print("     telegram:")
        print("       enabled: true")
        return False

    bot_token = tg_config.get('bot_token')
    chat_id = tg_config.get('chat_id')

    if not bot_token or not chat_id:
        print("❌ Telegram配置不完整")
        print(f"   bot_token: {'已配置' if bot_token else '未配置'}")
        print(f"   chat_id: {'已配置' if chat_id else '未配置'}")
        return False

    print("✅ Telegram配置:")
    print(f"   bot_token: {bot_token[:10]}...{bot_token[-5:]}")
    print(f"   chat_id: {chat_id}")
    print()

    # 初始化通知器
    try:
        notifier = init_trade_notifier(config)
        print("✅ TradeNotifier 初始化成功")
    except Exception as e:
        print(f"❌ TradeNotifier 初始化失败: {e}")
        return False

    print()
    print("=" * 60)
    print("  发送测试通知")
    print("=" * 60)
    print()

    # 测试1: 限价单挂单通知
    print("1️⃣ 测试限价单挂单通知...")
    try:
        notifier.notify_order_placed(
            symbol='BTC/USDT',
            side='BUY',
            quantity=0.001,
            price=95000.00,
            order_type='限价单 - 模拟合约'
        )
        print("   ✅ 限价单挂单通知发送成功")
    except Exception as e:
        print(f"   ❌ 失败: {e}")

    print()

    # 测试2: 订单成交通知
    print("2️⃣ 测试订单成交通知...")
    try:
        notifier.notify_order_filled(
            symbol='ETH/USDT',
            side='SELL',
            quantity=0.05,
            price=3500.00,
            order_type='市价单 - 模拟合约'
        )
        print("   ✅ 订单成交通知发送成功")
    except Exception as e:
        print(f"   ❌ 失败: {e}")

    print()

    # 测试3: 开仓通知
    print("3️⃣ 测试开仓通知...")
    try:
        notifier.notify_open_position(
            symbol='DOGE/USDT',
            direction='long',
            quantity=10000,
            entry_price=0.145,
            margin=145.0,
            leverage=10,
            stop_loss=0.138,
            take_profit=0.160
        )
        print("   ✅ 开仓通知发送成功")
    except Exception as e:
        print(f"   ❌ 失败: {e}")

    print()
    print("=" * 60)
    print("  测试完成")
    print("=" * 60)
    print()
    print("📱 请检查您的Telegram是否收到3条测试消息")
    print()

    return True

if __name__ == '__main__':
    success = test_telegram_notification()
    exit(0 if success else 1)
