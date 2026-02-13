#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量删除无效交易对"""
import yaml
import sys

if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 无效交易对列表（来自validate_trading_symbols.py）
INVALID_SYMBOLS = [
    'HYPE/USDT',
    'ACU/USDT',
    'FIGHT/USDT',
    'SPACE/USDT',
    'PIPPIN/USDT',
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

print('=' * 80)
print('批量删除无效交易对')
print('=' * 80)

# 备份
print('\n1. 创建备份: config.yaml.backup')
import shutil
shutil.copy('config.yaml', 'config.yaml.backup')
print('   ✅ 备份完成')

# 读取配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

original_count = len(config.get('symbols', []))

# 删除无效交易对
removed = []
for symbol in INVALID_SYMBOLS:
    if symbol in config['symbols']:
        config['symbols'].remove(symbol)
        removed.append(symbol)

# 保存
with open('config.yaml', 'w', encoding='utf-8') as f:
    yaml.dump(config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

print(f'\n2. 删除无效交易对:')
print(f'   原始数量: {original_count}')
print(f'   删除数量: {len(removed)}')
print(f'   剩余数量: {len(config["symbols"])}')

if removed:
    print(f'\n   已删除:')
    for symbol in removed:
        print(f'   - {symbol}')

print('\n✅ 完成')
print('\n如需恢复，运行: cp config.yaml.backup config.yaml')
