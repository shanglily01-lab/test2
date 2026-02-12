#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从config.yaml中移除稳定币交易对
"""
import yaml

# 稳定币列表（基础币种）
STABLECOINS = {
    # 美元稳定币
    'USDC', 'USDT', 'TUSD', 'BUSD', 'USDP', 'USDE', 'FDUSD', 'DAI',
    'FRAX', 'XUSD', 'USD1', 'RLUSD', 'BFUSD', 'USDJ', 'GUSD', 'PYUSD',
    'USDS', 'USTC', 'UST', 'SUSD', 'LUSD', 'HUSD',

    # 法币锚定币
    'EUR', 'AEUR', 'EURI', 'EUROC',
    'GBP', 'GBPT',
    'JPY', 'JPYC',
    'AUD', 'AUDT',
    'CNH', 'CNHT',

    # 其他锚定币
    'PAXG',  # 黄金锚定
    'XAUT',  # 黄金锚定
    'WBTC',  # 比特币包装币
    'WETH',  # 以太坊包装币
    'WBETH', # 质押以太坊
    'BNSOL', # 质押SOL

    # 其他稳定币
    'U',     # 单字母U可能是稳定币
}

def remove_stablecoins_from_config(config_path: str):
    """从config.yaml中移除稳定币交易对"""

    # 读取配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    original_symbols = config.get('symbols', [])
    original_count = len(original_symbols)

    # 过滤掉稳定币
    filtered_symbols = []
    removed_symbols = []

    for symbol in original_symbols:
        # 提取基础币种（例如 "BTC/USDT" -> "BTC"）
        base = symbol.split('/')[0]

        # 检查是否是稳定币
        if base in STABLECOINS:
            removed_symbols.append(symbol)
        else:
            filtered_symbols.append(symbol)

    # 更新配置
    config['symbols'] = filtered_symbols

    # 写回文件
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    # 输出结果
    print(f"移除稳定币前: {original_count} 个交易对")
    print(f"移除稳定币后: {len(filtered_symbols)} 个交易对")
    print(f"已移除: {len(removed_symbols)} 个稳定币交易对\n")

    if removed_symbols:
        print("已移除的稳定币交易对:")
        print("="*60)
        for i, symbol in enumerate(removed_symbols, 1):
            print(f"{i:3d}. {symbol}")
        print("="*60)

    return removed_symbols

def main():
    config_path = 'd:\\test2\\crypto-analyzer\\config.yaml'

    try:
        removed = remove_stablecoins_from_config(config_path)
        print(f"\n✅ 已成功移除 {len(removed)} 个稳定币交易对")
    except Exception as e:
        print(f"❌ 错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
