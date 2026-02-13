#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证config.yaml中的交易对是否在币安可交易"""
import ccxt
import yaml
import sys

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

print('=' * 120)
print('验证交易对有效性')
print('=' * 120)

# 读取config.yaml
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

configured_symbols = config.get('symbols', [])

print(f'\nconfig.yaml中配置的交易对数: {len(configured_symbols)}\n')

# 连接币安获取所有可交易的交易对
print('正在从币安获取可交易交易对列表...')
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {
        'defaultType': 'future',  # USDT合约
    }
})

try:
    markets = exchange.load_markets()
    binance_symbols = set(markets.keys())

    print(f'币安USDT合约可交易对数: {len(binance_symbols)}\n')

    # 验证每个配置的交易对
    invalid_symbols = []
    valid_symbols = []

    for symbol in configured_symbols:
        if symbol in binance_symbols:
            valid_symbols.append(symbol)
        else:
            invalid_symbols.append(symbol)

    print('=' * 120)
    print('验证结果')
    print('=' * 120)

    print(f'\n✅ 有效交易对: {len(valid_symbols)} 个')
    print(f'❌ 无效交易对: {len(invalid_symbols)} 个\n')

    if invalid_symbols:
        print('已下架或不存在的交易对:')
        print('-' * 120)
        for i, symbol in enumerate(invalid_symbols, 1):
            print(f'{i:3d}. {symbol}')

        print('\n⚠️  建议: 从config.yaml中删除这些交易对')

        # 生成删除命令
        print('\n可以手动删除，或创建backup后批量删除:')
        print(f'cp config.yaml config.yaml.backup')
        for symbol in invalid_symbols:
            print(f'# 删除 {symbol}')
    else:
        print('✅ 所有配置的交易对都有效！')

    # 检查是否有热门交易对未配置
    print('\n' + '=' * 120)
    print('建议添加的热门交易对（成交量TOP20，未在配置中）')
    print('=' * 120)

    # 获取24h成交量排序
    tickers = exchange.fetch_tickers()
    volume_sorted = sorted(
        [(symbol, ticker.get('quoteVolume', 0)) for symbol, ticker in tickers.items() if symbol.endswith('/USDT')],
        key=lambda x: x[1],
        reverse=True
    )

    suggested = []
    for symbol, volume in volume_sorted[:50]:  # 检查TOP50
        if symbol not in configured_symbols and volume > 0:
            suggested.append((symbol, volume))
            if len(suggested) >= 20:
                break

    if suggested:
        print('\n交易对                24h成交量(USDT)')
        print('-' * 120)
        for symbol, volume in suggested:
            print(f'{symbol:20} {volume:>20,.0f}')

except Exception as e:
    print(f'❌ 获取币安数据失败: {e}')
    print('可能原因: 网络问题或API限制')
    sys.exit(1)

print('\n' + '=' * 120)
print('完成')
print('=' * 120)
