#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证特定交易对是否支持合约交易
"""
import ccxt

def check_futures_support(symbols_to_check):
    """检查指定的交易对是否支持合约交易"""

    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
        }
    })

    print("正在获取Binance合约市场数据...")
    markets = exchange.load_markets()

    print(f"\n合约市场总数: {len(markets)}")
    print(f"检查 {len(symbols_to_check)} 个交易对...\n")
    print("="*80)

    for symbol in symbols_to_check:
        # 检查多种可能的格式
        found = False
        found_format = None

        # 格式1: BTC/USDT
        if symbol in markets:
            found = True
            found_format = symbol

        # 格式2: BTC/USDT:USDT (永续合约)
        perp_symbol = f"{symbol}:USDT"
        if perp_symbol in markets:
            found = True
            found_format = perp_symbol

        # 格式3: BTCUSDT (无斜杠)
        no_slash = symbol.replace('/', '')
        if no_slash in markets:
            found = True
            found_format = no_slash

        if found:
            market = markets[found_format]
            print(f"[YES] {symbol:20} | 支持合约 | 格式: {found_format:25} | 活跃: {market.get('active', 'N/A')}")
        else:
            print(f"[NO]  {symbol:20} | 不支持合约")

    print("="*80)

def main():
    # 被移除的交易对
    removed_symbols = [
        'HYPE/USDT',
        'ACU/USDT',
        'FIGHT/USDT',
        'SPACE/USDT',
        'PIPPIN/USDT',
        'XMR/USDT',
        'XAU/USDT',
        'IP/USDT',
        'FARTCOIN/USDT',
        'ELSA/USDT',
        'MYX/USDT',
        'XAG/USDT',
        'SKR/USDT',
        'LIGHT/USDT',
        'MELANIA/USDT',
    ]

    try:
        check_futures_support(removed_symbols)
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
