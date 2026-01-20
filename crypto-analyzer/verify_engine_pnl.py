"""
验证 futures_trading_engine.py 的 PnL 计算是否正确
"""

# XMR/USDT LONG 订单
symbol_xmr = "XMR/USDT"
position_side_xmr = "LONG"
entry_price_xmr = 606.30766667
current_price_xmr = 619.10000000
quantity_xmr = 3.29865530
leverage_xmr = 5
margin_xmr = 400.00

# 按照 futures_trading_engine.py 的逻辑计算 (Line 977-982)
if position_side_xmr == 'LONG':
    pnl_xmr = (current_price_xmr - entry_price_xmr) * quantity_xmr
else:
    pnl_xmr = (entry_price_xmr - current_price_xmr) * quantity_xmr

# 计算手续费 (Line 1008-1009)
fee_rate = 0.0004
close_value_xmr = current_price_xmr * quantity_xmr
fee_xmr = close_value_xmr * fee_rate

# 实际盈亏 = pnl - 手续费 (Line 1012)
realized_pnl_xmr = pnl_xmr - fee_xmr

print("=" * 80)
print("XMR/USDT LONG 订单 PnL 计算验证")
print("=" * 80)
print(f"Entry Price:    {entry_price_xmr:.8f}")
print(f"Close Price:    {current_price_xmr:.8f}")
print(f"Quantity:       {quantity_xmr:.8f}")
print(f"")
print(f"Price Diff:     {current_price_xmr - entry_price_xmr:.8f}")
print(f"PnL (无手续费):  {pnl_xmr:.2f} USDT")
print(f"Close Value:    {close_value_xmr:.2f} USDT")
print(f"Fee (0.04%):    {fee_xmr:.2f} USDT")
print(f"Realized PnL:   {realized_pnl_xmr:.2f} USDT")
print(f"")
print(f"数据库中的值:    39.18 USDT")
print(f"差异:           {realized_pnl_xmr - 39.18:.2f} USDT")
print()

# CHZ/USDT SHORT 订单
symbol_chz = "CHZ/USDT"
position_side_chz = "SHORT"
entry_price_chz = 0.05700914
current_price_chz = 0.05582000
quantity_chz = 35082.09385372
leverage_chz = 5
margin_chz = 400.00

# 按照 futures_trading_engine.py 的逻辑计算
if position_side_chz == 'LONG':
    pnl_chz = (current_price_chz - entry_price_chz) * quantity_chz
else:
    pnl_chz = (entry_price_chz - current_price_chz) * quantity_chz

# 计算手续费
close_value_chz = current_price_chz * quantity_chz
fee_chz = close_value_chz * fee_rate

# 实际盈亏
realized_pnl_chz = pnl_chz - fee_chz

print("=" * 80)
print("CHZ/USDT SHORT 订单 PnL 计算验证")
print("=" * 80)
print(f"Entry Price:    {entry_price_chz:.8f}")
print(f"Close Price:    {current_price_chz:.8f}")
print(f"Quantity:       {quantity_chz:.8f}")
print(f"")
print(f"Price Diff:     {entry_price_chz - current_price_chz:.8f}")
print(f"PnL (无手续费):  {pnl_chz:.2f} USDT")
print(f"Close Value:    {close_value_chz:.2f} USDT")
print(f"Fee (0.04%):    {fee_chz:.2f} USDT")
print(f"Realized PnL:   {realized_pnl_chz:.2f} USDT")
print(f"")
print(f"数据库中的值:    39.22 USDT")
print(f"差异:           {realized_pnl_chz - 39.22:.2f} USDT")
print()
print("=" * 80)
