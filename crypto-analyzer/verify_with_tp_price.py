"""
使用 take_profit_price 重新计算 PnL,看是否匹配数据库的值
"""

print("=" * 80)
print("XMR/USDT LONG - 使用 take_profit_price 计算")
print("=" * 80)

entry_price_xmr = 606.30766667
tp_price_xmr = 618.43382000  # 止盈价格
mark_price_xmr = 619.10000000  # 实际市场价格
quantity_xmr = 3.29865530

# 使用 TP 价格计算
price_diff_tp = tp_price_xmr - entry_price_xmr
pnl_no_fee_tp = price_diff_tp * quantity_xmr
close_value_tp = tp_price_xmr * quantity_xmr
fee_tp = close_value_tp * 0.0004
realized_pnl_tp = pnl_no_fee_tp - fee_tp

print(f"Entry Price:         {entry_price_xmr:.8f}")
print(f"Take Profit Price:   {tp_price_xmr:.8f}")
print(f"Actual Mark Price:   {mark_price_xmr:.8f}")
print(f"Quantity:            {quantity_xmr:.8f}")
print(f"")
print(f"使用 TP 价格:")
print(f"  Price Diff:        {price_diff_tp:.8f}")
print(f"  PnL (无手续费):     {pnl_no_fee_tp:.2f} USDT")
print(f"  Close Value:       {close_value_tp:.2f} USDT")
print(f"  Fee (0.04%):       {fee_tp:.2f} USDT")
print(f"  Realized PnL:      {realized_pnl_tp:.2f} USDT")
print(f"")
print(f"数据库 realized_pnl:  39.18 USDT")
print(f"差异:                {realized_pnl_tp - 39.18:.2f} USDT")

if abs(realized_pnl_tp - 39.18) < 0.01:
    print(f"✅ 匹配! PnL 是使用 take_profit_price 计算的!")
else:
    print(f"❌ 不匹配")

print()
print("=" * 80)
print("CHZ/USDT SHORT - 使用 take_profit_price 计算")
print("=" * 80)

entry_price_chz = 0.05700914
tp_price_chz = 0.05586896  # 止盈价格
mark_price_chz = 0.05582000  # 实际市场价格
quantity_chz = 35082.09385372

# 使用 TP 价格计算
price_diff_tp_chz = entry_price_chz - tp_price_chz  # SHORT
pnl_no_fee_tp_chz = price_diff_tp_chz * quantity_chz
close_value_tp_chz = tp_price_chz * quantity_chz
fee_tp_chz = close_value_tp_chz * 0.0004
realized_pnl_tp_chz = pnl_no_fee_tp_chz - fee_tp_chz

print(f"Entry Price:         {entry_price_chz:.8f}")
print(f"Take Profit Price:   {tp_price_chz:.8f}")
print(f"Actual Mark Price:   {mark_price_chz:.8f}")
print(f"Quantity:            {quantity_chz:.8f}")
print(f"")
print(f"使用 TP 价格:")
print(f"  Price Diff:        {price_diff_tp_chz:.8f}")
print(f"  PnL (无手续费):     {pnl_no_fee_tp_chz:.2f} USDT")
print(f"  Close Value:       {close_value_tp_chz:.2f} USDT")
print(f"  Fee (0.04%):       {fee_tp_chz:.2f} USDT")
print(f"  Realized PnL:      {realized_pnl_tp_chz:.2f} USDT")
print(f"")
print(f"数据库 realized_pnl:  39.22 USDT")
print(f"差异:                {realized_pnl_tp_chz - 39.22:.2f} USDT")

if abs(realized_pnl_tp_chz - 39.22) < 0.01:
    print(f"✅ 匹配! PnL 是使用 take_profit_price 计算的!")
else:
    print(f"❌ 不匹配")

print()
print("=" * 80)
print("总结")
print("=" * 80)
print("问题: 平仓时使用了 take_profit_price 来计算 PnL,")
print("      而不是使用实际的市场价格 (mark_price)。")
print()
print("止盈触发后,价格可能会继续朝有利方向移动,")
print("应该使用实际平仓时的市场价格来计算最终 PnL,")
print("而不是使用预设的止盈价格。")
print()
print("XMR: 损失了 {:.2f} USDT ({:.2f}%)".format(
    41.38 - 39.18,
    (41.38 - 39.18) / 41.38 * 100
))
print("CHZ: 损失了 {:.2f} USDT ({:.2f}%)".format(
    40.93 - 39.22,
    (40.93 - 39.22) / 40.93 * 100
))
print("=" * 80)
