"""
计算加上手续费后的实际盈亏
"""
import sys

# Set UTF-8 encoding for Windows console
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')

# 基于之前的分析数据
total_orders = 289
total_pnl_before_fee = 393.42
fee_per_trade = 0.78

# 计算总手续费
total_fee = total_orders * fee_per_trade
print('=' * 80)
print('超级大脑盈亏分析 - 含手续费')
print('=' * 80)
print(f'\n总订单数: {total_orders}笔')
print(f'每笔手续费: {fee_per_trade} USDT')
print(f'总手续费: {total_fee:.2f} USDT')
print()
print(f'扣除手续费前盈亏: +${total_pnl_before_fee:.2f}')
print(f'总手续费: -${total_fee:.2f}')
total_pnl_after_fee = total_pnl_before_fee - total_fee
print(f'扣除手续费后盈亏: ${total_pnl_after_fee:.2f}')

if total_pnl_after_fee > 0:
    print(f'  ✅ 仍然盈利 ${total_pnl_after_fee:.2f}')
else:
    print(f'  ❌ 净亏损 ${total_pnl_after_fee:.2f}')

print()
print('=' * 80)
print('黑名单交易对分析')
print('=' * 80)

# 黑名单交易对
blacklist_orders = 22  # IP(2) + VIRTUAL(4) + LDO(5) + ATOM(5) + ADA(6) = 22
blacklist_loss = 201.44
blacklist_fee = blacklist_orders * fee_per_trade

print(f'\n黑名单订单数: {blacklist_orders}笔')
print(f'  交易亏损: -${blacklist_loss:.2f}')
print(f'  手续费: -${blacklist_fee:.2f}')
print(f'  总亏损: -${blacklist_loss + blacklist_fee:.2f}')

print()
print('=' * 80)
print('排除黑名单后的表现')
print('=' * 80)

# 排除黑名单后
remaining_orders = total_orders - blacklist_orders
remaining_pnl_before_fee = total_pnl_before_fee + blacklist_loss
remaining_fee = remaining_orders * fee_per_trade
remaining_pnl_after_fee = remaining_pnl_before_fee - remaining_fee

print(f'\n剩余订单数: {remaining_orders}笔')
print(f'扣除手续费前盈亏: +${remaining_pnl_before_fee:.2f}')
print(f'总手续费: -${remaining_fee:.2f}')
print(f'扣除手续费后盈亏: +${remaining_pnl_after_fee:.2f}')

print()
print('=' * 80)
print('黑名单效果对比')
print('=' * 80)

improvement = remaining_pnl_after_fee - total_pnl_after_fee
improvement_pct = (improvement / abs(total_pnl_after_fee)) * 100 if total_pnl_after_fee != 0 else 0

print(f'\n有黑名单: ${total_pnl_after_fee:.2f}')
print(f'无黑名单: ${remaining_pnl_after_fee:.2f}')
print(f'改善: +${improvement:.2f} ({improvement_pct:+.1f}%)')

print()
print('=' * 80)
print('结论')
print('=' * 80)
print()

if total_pnl_after_fee > 0:
    print(f'✅ 当前策略盈利: ${total_pnl_after_fee:.2f}')
else:
    print(f'❌ 当前策略亏损: ${total_pnl_after_fee:.2f}')

if remaining_pnl_after_fee > total_pnl_after_fee:
    print(f'✅ 排除黑名单后改善: +${improvement:.2f}')
    print(f'   优化后盈利: ${remaining_pnl_after_fee:.2f}')
else:
    print(f'⚠️  排除黑名单效果有限')

# ROI计算 (假设每笔400 USDT保证金)
margin_per_trade = 400
total_margin_used = total_orders * margin_per_trade
remaining_margin_used = remaining_orders * margin_per_trade

print()
print('=' * 80)
print('ROI 分析 (假设每笔400 USDT保证金)')
print('=' * 80)
print()
print(f'当前策略:')
print(f'  总投入: ${total_margin_used:,.0f}')
print(f'  净盈亏: ${total_pnl_after_fee:.2f}')
print(f'  ROI: {total_pnl_after_fee/total_margin_used*100:.3f}%')
print()
print(f'优化后策略:')
print(f'  总投入: ${remaining_margin_used:,.0f}')
print(f'  净盈亏: ${remaining_pnl_after_fee:.2f}')
print(f'  ROI: {remaining_pnl_after_fee/remaining_margin_used*100:.3f}%')
print()

print('=' * 80)
