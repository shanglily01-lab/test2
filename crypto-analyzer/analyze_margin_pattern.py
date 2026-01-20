"""
分析保证金和 PnL 之间的关系,看是否有固定比例
"""

# 所有盈利订单都是 39.18 或 39.22 USDT
# 假设开仓保证金是 400 USDT, 杠杆是 5x

margin = 400.00
leverage = 5

# 计算如果是基于保证金的固定比例会是多少
# 39.18 / 400 = 0.09795 = 9.795%
# 39.22 / 400 = 0.09805 = 9.805%

pnl_1 = 39.18
pnl_2 = 39.22

pct_1 = pnl_1 / margin * 100
pct_2 = pnl_2 / margin * 100

print("=" * 80)
print("PnL 占保证金的百分比分析")
print("=" * 80)
print(f"保证金: {margin} USDT")
print(f"杠杆: {leverage}x")
print(f"")
print(f"PnL = 39.18 USDT")
print(f"  占保证金比例: {pct_1:.4f}%")
print(f"  如果是价格变化: {pct_1 / leverage:.4f}%")
print(f"")
print(f"PnL = 39.22 USDT")
print(f"  占保证金比例: {pct_2:.4f}%")
print(f"  如果是价格变化: {pct_2 / leverage:.4f}%")
print()

# 检查 XMR 订单的实际价格变化
print("=" * 80)
print("XMR/USDT LONG 实际数据验证")
print("=" * 80)
entry_price = 606.30766667
close_price = 619.10000000
price_change_pct = (close_price - entry_price) / entry_price * 100
expected_roi = price_change_pct * leverage  # ROI on margin

print(f"Entry Price: {entry_price:.8f}")
print(f"Close Price: {close_price:.8f}")
print(f"Price Change: {price_change_pct:.4f}%")
print(f"Expected ROI (5x leverage): {expected_roi:.4f}%")
print(f"Expected PnL (无手续费): {margin * expected_roi / 100:.2f} USDT")
print(f"")
print(f"实际数据库 PnL: 39.18 USDT")
print(f"理论 PnL (扣费前): 42.20 USDT")
print(f"理论 PnL (扣费后): 41.38 USDT")
print()

# 现在我要检查是否 realized_pnl 实际上被误算成了 unrealized_pnl
# 或者是否有某个地方使用了错误的数量

print("=" * 80)
print("怀疑: PnL 是否使用了错误的 quantity?")
print("=" * 80)

quantity_correct = 3.29865530
price_diff = close_price - entry_price  # 12.79233333

# 如果 PnL = 39.18 (扣费后),那么扣费前大约是 39.18 + 0.82 = 40.00
# 40.00 / 12.79233333 = 3.126 (这应该是使用的数量)

if price_diff > 0:
    # 扣费后的 PnL 大约是 39.18
    # 扣费前的 PnL 大约是 39.18 + 0.82 = 40.00
    pnl_before_fee = 39.18 + 0.82
    implied_quantity = pnl_before_fee / price_diff

    print(f"数据库 realized_pnl: 39.18 USDT")
    print(f"推测扣费前 PnL: {pnl_before_fee:.2f} USDT")
    print(f"价格差: {price_diff:.8f}")
    print(f"推测使用的数量: {implied_quantity:.8f}")
    print(f"实际数量: {quantity_correct:.8f}")
    print(f"数量差异: {quantity_correct - implied_quantity:.8f}")
    print(f"")
    print(f"如果数量少了 {quantity_correct - implied_quantity:.8f},")
    print(f"那么 PnL 就会少: {(quantity_correct - implied_quantity) * price_diff:.2f} USDT")

print()
print("=" * 80)
