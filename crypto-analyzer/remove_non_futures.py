#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从config.yaml中移除不支持合约交易的交易对
"""
import ccxt
import yaml
from typing import Set

def get_futures_symbols() -> Set[str]:
    """获取Binance支持合约交易的USDT交易对"""
    print("正在获取Binance合约市场数据...")

    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',  # 合约市场
        }
    })

    # 获取合约市场信息
    markets = exchange.load_markets()

    # 筛选USDT合约交易对
    futures_symbols = set()
    for symbol, market in markets.items():
        if market['quote'] == 'USDT' and market['active']:
            # 转换为现货格式（例如 BTCUSDT -> BTC/USDT）
            futures_symbols.add(symbol)

    print(f"找到 {len(futures_symbols)} 个支持合约的USDT交易对")
    return futures_symbols

def remove_non_futures_from_config(config_path: str):
    """从config.yaml中移除不支持合约交易的交易对"""

    # 1. 获取支持合约的交易对
    futures_symbols = get_futures_symbols()

    # 2. 读取配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    original_symbols = config.get('symbols', [])
    original_count = len(original_symbols)

    # 3. 过滤：只保留支持合约的交易对
    filtered_symbols = []
    removed_symbols = []

    for symbol in original_symbols:
        if symbol in futures_symbols:
            filtered_symbols.append(symbol)
        else:
            removed_symbols.append(symbol)

    # 4. 更新配置
    config['symbols'] = filtered_symbols

    # 5. 写回文件
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # 6. 输出结果
    print(f"\n移除前: {original_count} 个交易对")
    print(f"移除后: {len(filtered_symbols)} 个交易对")
    print(f"已移除: {len(removed_symbols)} 个不支持合约的交易对\n")

    if removed_symbols:
        print("已移除的不支持合约交易的交易对:")
        print("="*60)
        for i, symbol in enumerate(removed_symbols, 1):
            print(f"{i:3d}. {symbol}")
        print("="*60)

    return removed_symbols

def main():
    config_path = 'd:\\test2\\crypto-analyzer\\config.yaml'

    try:
        removed = remove_non_futures_from_config(config_path)
        print(f"\n已成功移除 {len(removed)} 个不支持合约的交易对")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
