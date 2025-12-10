#!/usr/bin/env python3
"""
简单的Telegram通知测试（不依赖config_loader）
"""
import os
import requests

def test_telegram():
    """测试TG通知"""
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    print("=" * 60)
    print("  简单Telegram通知测试")
    print("=" * 60)
    print()

    if not bot_token:
        print("❌ 环境变量 TELEGRAM_BOT_TOKEN 未设置")
        return False

    if not chat_id:
        print("❌ 环境变量 TELEGRAM_CHAT_ID 未设置")
        return False

    print(f"✅ bot_token: {bot_token[:10]}...{bot_token[-5:]}")
    print(f"✅ chat_id: {chat_id}")
    print()

    # 测试发送消息
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    test_messages = [
        {
            "name": "限价单挂单",
            "text": """📝 <b>【限价单挂单】BTC/USDT</b>

📌 方向: 买入
💰 数量: 0.001000
💵 限价: $95,000.0000
📋 类型: 限价单 - 模拟合约

⏰ 测试消息"""
        },
        {
            "name": "订单成交",
            "text": """🟢 <b>【订单成交】ETH/USDT</b>

📌 方向: 卖出
💰 数量: 0.050000
💵 价格: $3,500.0000
📋 类型: 市价单 - 模拟合约

⏰ 测试消息"""
        },
        {
            "name": "开仓通知",
            "text": """🚀 <b>【开仓】DOGE/USDT</b>

📌 方向: 做多
💰 数量: 10000.000000
💵 价格: $0.1450
🔢 杠杆: 10x
💵 保证金: 145.00 USDT

🛡️ 止损: $0.1380 (-4.83%)
🎯 止盈: $0.1600 (10.34%)

⏰ 测试消息"""
        }
    ]

    success_count = 0
    for i, msg in enumerate(test_messages, 1):
        print(f"{i}️⃣ 发送 {msg['name']} 通知...")
        try:
            data = {
                'chat_id': chat_id,
                'text': msg['text'],
                'parse_mode': 'HTML'
            }
            response = requests.post(url, data=data, timeout=10)
            response.raise_for_status()
            print(f"   ✅ 成功")
            success_count += 1
        except Exception as e:
            print(f"   ❌ 失败: {e}")

    print()
    print("=" * 60)
    print(f"  测试完成: {success_count}/{len(test_messages)} 成功")
    print("=" * 60)
    print()

    if success_count == len(test_messages):
        print("🎉 所有测试通过！请检查Telegram是否收到消息")
        return True
    else:
        print("⚠️  部分测试失败，请检查配置和网络")
        return False

if __name__ == '__main__':
    success = test_telegram()
    exit(0 if success else 1)
