#!/usr/bin/env python3
"""
检查Telegram配置和通知功能状态
"""
import os
import sys
from pathlib import Path

def check_config():
    """检查配置"""
    print("=" * 70)
    print("  Telegram通知配置检查")
    print("=" * 70)
    print()

    # 1. 检查环境变量
    print("📋 步骤1: 检查环境变量")
    print("-" * 70)
    bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHAT_ID')

    if bot_token:
        print(f"✅ TELEGRAM_BOT_TOKEN: {bot_token[:10]}...{bot_token[-5:]}")
    else:
        print("❌ TELEGRAM_BOT_TOKEN: 未设置")

    if chat_id:
        print(f"✅ TELEGRAM_CHAT_ID: {chat_id}")
    else:
        print("❌ TELEGRAM_CHAT_ID: 未设置")

    print()

    # 2. 检查config.yaml
    print("📋 步骤2: 检查 config.yaml")
    print("-" * 70)
    config_path = Path(__file__).parent / 'config.yaml'
    if not config_path.exists():
        print(f"❌ config.yaml 不存在: {config_path}")
        return False

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # 检查notifications配置
        if 'notifications:' in content:
            print("✅ 找到 notifications: 配置")
        else:
            print("❌ 未找到 notifications: 配置")
            return False

        if 'telegram:' in content:
            print("✅ 找到 telegram: 配置")
        else:
            print("❌ 未找到 telegram: 配置")
            return False

        if 'enabled: true' in content:
            print("✅ telegram.enabled: true")
        else:
            print("⚠️  telegram.enabled 可能未启用")

        # 检查环境变量占位符
        if '${TELEGRAM_BOT_TOKEN' in content:
            print("✅ 使用环境变量占位符: ${TELEGRAM_BOT_TOKEN:}")
        else:
            print("⚠️  未使用环境变量占位符")

        if '${TELEGRAM_CHAT_ID' in content:
            print("✅ 使用环境变量占位符: ${TELEGRAM_CHAT_ID:}")
        else:
            print("⚠️  未使用环境变量占位符")

    except Exception as e:
        print(f"❌ 读取config.yaml失败: {e}")
        return False

    print()

    # 3. 尝试加载配置（使用config_loader）
    print("📋 步骤3: 测试配置加载")
    print("-" * 70)
    try:
        # 检查是否可以导入config_loader
        try:
            from app.utils.config_loader import load_config
            config = load_config(config_path)
            print("✅ config_loader 可用，配置加载成功")

            # 检查配置值
            tg_config = config.get('notifications', {}).get('telegram', {})
            loaded_token = tg_config.get('bot_token', '')
            loaded_chat_id = tg_config.get('chat_id', '')

            if loaded_token and not loaded_token.startswith('${'):
                print(f"✅ bot_token 已正确加载: {loaded_token[:10]}...{loaded_token[-5:]}")
            else:
                print(f"❌ bot_token 未正确加载: {loaded_token}")

            if loaded_chat_id and not str(loaded_chat_id).startswith('${'):
                print(f"✅ chat_id 已正确加载: {loaded_chat_id}")
            else:
                print(f"❌ chat_id 未正确加载: {loaded_chat_id}")

        except ImportError as ie:
            print(f"⚠️  无法导入 config_loader: {ie}")
            print("   这在开发环境是正常的，生产环境会自动加载")

    except Exception as e:
        print(f"❌ 配置加载失败: {e}")

    print()

    # 4. 给出建议
    print("=" * 70)
    print("  建议")
    print("=" * 70)

    if not bot_token or not chat_id:
        print()
        print("❌ 环境变量未设置，请执行:")
        print()
        print("  Linux/Mac:")
        print("    export TELEGRAM_BOT_TOKEN='你的bot_token'")
        print("    export TELEGRAM_CHAT_ID='你的chat_id'")
        print()
        print("  Windows PowerShell:")
        print("    $env:TELEGRAM_BOT_TOKEN='你的bot_token'")
        print("    $env:TELEGRAM_CHAT_ID='你的chat_id'")
        print()
        print("  或者在 .env 文件中设置")
        print()
        return False
    else:
        print()
        print("✅ 配置看起来正常！")
        print()
        print("📱 测试通知功能:")
        print("   python3 test_tg_simple.py")
        print()
        return True

if __name__ == '__main__':
    success = check_config()
    sys.exit(0 if success else 1)
