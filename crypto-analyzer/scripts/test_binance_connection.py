#!/usr/bin/env python3
"""
测试币安实盘API连接

用法：
    python scripts/test_binance_connection.py
"""

import sys
import os

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
from decimal import Decimal


def main():
    print("=" * 60)
    print("币安实盘API连接测试")
    print("=" * 60)

    # 1. 加载配置
    print("\n[1] 加载配置文件...")
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        binance_config = config.get('exchanges', {}).get('binance', {})
        api_key = binance_config.get('api_key', '').strip()
        api_secret = binance_config.get('api_secret', '').strip()

        if api_key and api_secret:
            print(f"   API Key: {api_key[:8]}...{api_key[-4:]}")
            print("   API Secret: ********")
        else:
            print("   错误: 未找到API配置！")
            return False

        db_config = config.get('database', {}).get('mysql', {})
        print(f"   数据库: {db_config.get('host')}:{db_config.get('port')}/{db_config.get('database')}")

    except Exception as e:
        print(f"   错误: {e}")
        return False

    # 2. 初始化实盘引擎
    print("\n[2] 初始化实盘交易引擎...")
    try:
        from app.trading.binance_futures_engine import BinanceFuturesEngine
        engine = BinanceFuturesEngine(db_config)
        print("   成功！")
    except Exception as e:
        print(f"   错误: {e}")
        return False

    # 3. 测试API连接
    print("\n[3] 测试API连接...")
    try:
        result = engine.test_connection()
        if result.get('success'):
            print("   连接成功！")
            print(f"   服务器时间: {result.get('server_time')}")
            print(f"   账户余额: {result.get('balance'):.4f} USDT")
            print(f"   可用余额: {result.get('available'):.4f} USDT")
        else:
            print(f"   连接失败: {result.get('error')}")
            return False
    except Exception as e:
        print(f"   错误: {e}")
        return False

    # 4. 获取当前价格
    print("\n[4] 测试获取价格...")
    test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
    for symbol in test_symbols:
        try:
            price = engine.get_current_price(symbol)
            print(f"   {symbol}: {price}")
        except Exception as e:
            print(f"   {symbol}: 错误 - {e}")

    # 5. 获取持仓
    print("\n[5] 获取当前持仓...")
    try:
        positions = engine.get_open_positions()
        if positions:
            print(f"   当前持仓数: {len(positions)}")
            for pos in positions:
                symbol = pos.get('symbol')
                side = pos.get('position_side')
                qty = pos.get('quantity')
                entry = pos.get('entry_price')
                pnl = pos.get('unrealized_pnl')
                print(f"   - {symbol} {side}: {qty} @ {entry}, 未实现盈亏: {pnl}")
        else:
            print("   当前无持仓")
    except Exception as e:
        print(f"   错误: {e}")

    # 6. 获取账户详情
    print("\n[6] 获取账户详情...")
    try:
        account = engine.get_account_info()
        if account.get('success'):
            print(f"   总保证金余额: {account.get('total_margin_balance'):.4f} USDT")
            print(f"   可用余额: {account.get('available_balance'):.4f} USDT")
            print(f"   总未实现盈亏: {account.get('total_unrealized_profit'):.4f} USDT")
            print(f"   钱包余额: {account.get('total_wallet_balance'):.4f} USDT")
        else:
            print(f"   错误: {account.get('error')}")
    except Exception as e:
        print(f"   错误: {e}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)

    print("\n下一步操作:")
    print("1. 执行数据库迁移脚本创建实盘交易表:")
    print("   mysql -u root -p binance-data < scripts/migrations/014_create_live_trading_tables.sql")
    print("\n2. 在策略配置中将 market_type 设置为 'live' 启用实盘交易")
    print("\n3. 建议先使用小额资金测试，确认无误后再增加资金")

    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
