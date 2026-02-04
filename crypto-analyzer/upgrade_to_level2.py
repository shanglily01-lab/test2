#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将表现差的交易对升级到黑名单2级"""

import sys
sys.path.insert(0, '.')

from app.services.optimization_config import OptimizationConfig

db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

opt_config = OptimizationConfig(db_config)

# 需要升级到黑名单2级的交易对
symbols_to_upgrade = [
    'G/USDT',       # 胜率20%, 净亏损-$79.75
    'FIGHT/USDT',   # 胜率0%, 净亏损-$123.79
    'ZAMA/USDT'     # 胜率33.3%, 净亏损-$148.80
]

print("=" * 80)
print("upgrade to level 2")
print("=" * 80)

for symbol in symbols_to_upgrade:
    print(f"\nProcessing {symbol}...")
    
    # 获取当前评级
    current_rating = opt_config.get_symbol_rating(symbol)
    
    if current_rating:
        current_level = current_rating['rating_level']
        hard_stop_loss = current_rating.get('hard_stop_loss_count', 0)
        total_loss = current_rating.get('total_loss_amount', 0)
        win_rate = current_rating.get('win_rate', 0)
        total_trades = current_rating.get('total_trades', 0)
        
        print(f"  Current level: {current_level}")
        print(f"  Hard stop loss: {hard_stop_loss}")
        print(f"  Total loss: ${total_loss:.2f}")
        print(f"  Win rate: {win_rate:.1f}%")
        print(f"  Total trades: {total_trades}")
        
        # 升级到黑名单2级
        opt_config.update_symbol_rating(
            symbol=symbol,
            new_level=2,
            reason=f"Manual upgrade: Poor performance (win rate {win_rate:.1f}%, loss ${total_loss:.2f})",
            hard_stop_loss_count=hard_stop_loss,
            total_loss_amount=total_loss,
            total_profit_amount=current_rating.get('total_profit_amount', 0),
            win_rate=win_rate,
            total_trades=total_trades
        )
        
        print(f"  => Upgraded to Level 2")
    else:
        print(f"  Warning: No rating found, creating new Level 2")
        opt_config.update_symbol_rating(
            symbol=symbol,
            new_level=2,
            reason="Manual upgrade: New blacklist level 2",
            hard_stop_loss_count=0,
            total_loss_amount=0,
            total_profit_amount=0,
            win_rate=0,
            total_trades=0
        )
        print(f"  => Created Level 2")

print("\n" + "=" * 80)
print("Summary:")
print("=" * 80)

# 统计各等级数量
ratings = opt_config.get_all_symbol_ratings()
level_counts = {0: 0, 1: 0, 2: 0, 3: 0}

for rating in ratings:
    level = rating['rating_level']
    level_counts[level] += 1

print(f"\nLevel 0 (whitelist): {level_counts[0]}")
print(f"Level 1 (blacklist 1): {level_counts[1]}")
print(f"Level 2 (blacklist 2): {level_counts[2]}")
print(f"Level 3 (blacklist 3): {level_counts[3]}")

# 显示黑名单2级的所有交易对
print("\nLevel 2 symbols:")
level2_symbols = [r['symbol'] for r in ratings if r['rating_level'] == 2]
for symbol in sorted(level2_symbols):
    print(f"  - {symbol}")

print("\n" + "=" * 80)
