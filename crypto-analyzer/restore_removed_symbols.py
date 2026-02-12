#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
恢复被错误移除的交易对
"""
import yaml

def restore_symbols(config_path: str):
    """恢复被错误移除的15个交易对"""

    # 被错误移除的交易对（都支持合约交易）
    symbols_to_restore = [
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

    # 读取配置
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    current_symbols = config.get('symbols', [])
    original_count = len(current_symbols)

    # 过滤掉已存在的
    new_symbols = [s for s in symbols_to_restore if s not in current_symbols]

    # 添加回去
    config['symbols'] = current_symbols + new_symbols

    # 写回文件
    with open(config_path, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"恢复前: {original_count} 个交易对")
    print(f"恢复后: {len(config['symbols'])} 个交易对")
    print(f"已恢复: {len(new_symbols)} 个交易对\n")

    if new_symbols:
        print("已恢复的交易对:")
        print("="*60)
        for i, symbol in enumerate(new_symbols, 1):
            print(f"{i:3d}. {symbol}")
        print("="*60)

def main():
    config_path = 'd:\\test2\\crypto-analyzer\\config.yaml'

    try:
        restore_symbols(config_path)
        print("\n已成功恢复被错误移除的交易对")
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    main()
