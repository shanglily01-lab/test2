#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""将信号修复应用到币本位服务"""

import re

# 读取币本位服务文件
with open('coin_futures_trader_service.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 修复1: 添加信号方向验证调用
old_blacklist_check = r'''                # 检查信号黑名单 \(使用完整的信号组合键\)
                blacklist_key = f"\{signal_combination_key\}_\{side\}"
                if blacklist_key in self\.signal_blacklist:
                    logger\.info\(f"🚫 \{symbol\} 信号 \[\{signal_combination_key\}\] \{side\} 在黑名单中，跳过（历史表现差）"\)
                    return None

                return \{'''

new_blacklist_check = '''                # 检查信号黑名单 (使用完整的信号组合键)
                blacklist_key = f"{signal_combination_key}_{side}"
                if blacklist_key in self.signal_blacklist:
                    logger.info(f"🚫 {symbol} 信号 [{signal_combination_key}] {side} 在黑名单中，跳过（历史表现差）")
                    return None

                # 🔥 新增: 检查信号方向矛盾（防止逻辑错误）
                is_valid, contradiction_reason = self._validate_signal_direction(signal_components, side)
                if not is_valid:
                    logger.error(f"🚫 {symbol} 信号方向矛盾: {contradiction_reason} | 信号:{signal_combination_key} | 方向:{side}")
                    return None

                return {'''

if re.search(old_blacklist_check, content):
    content = re.sub(old_blacklist_check, new_blacklist_check, content)
    print('✓ 修复1: 添加信号方向验证调用')
else:
    print('⚠️ 修复1: 未找到匹配位置（可能已修复）')

# 修复2: 添加 _validate_signal_direction 函数（在 validate_signal_timeframe 之后）
validate_func = '''
    def _validate_signal_direction(self, signal_components: dict, side: str) -> tuple:
        """
        🔥 新增: 验证信号方向一致性,防止矛盾信号

        Args:
            signal_components: 信号组件字典
            side: 交易方向 (LONG/SHORT)

        Returns:
            (is_valid, reason) - 是否有效,原因描述
        """
        if not signal_components:
            return True, "无信号组件"

        # 定义空头信号（不应该出现在做多信号中）
        bearish_signals = {
            'breakdown_short',        # 破位做空
            'volume_power_bear',      # 1H+15M空头量能
            'volume_power_1h_bear',   # 1H空头量能
            'trend_1h_bear',          # 1H趋势看跌
            'trend_1d_bear',          # 1D趋势看跌
            'momentum_up_3pct',       # 上涨3%（可能是顶部反转）
            'consecutive_bear'        # 连续阴线
        }

        # 定义多头信号（不应该出现在做空信号中）
        bullish_signals = {
            'breakout_long',          # 突破做多
            'volume_power_bull',      # 1H+15M多头量能
            'volume_power_1h_bull',   # 1H多头量能
            'trend_1h_bull',          # 1H趋势看涨
            'trend_1d_bull',          # 1D趋势看涨
            'momentum_down_3pct',     # 下跌3%（可能是底部反转）
            'consecutive_bull'        # 连续阳线
        }

        signal_set = set(signal_components.keys())

        # 检查做多信号中的矛盾
        if side == 'LONG':
            conflicts = bearish_signals & signal_set
            if conflicts:
                # 特殊情况：如果只有momentum_up_3pct，可能是超跌反弹，允许
                if conflicts == {'momentum_up_3pct'} and 'position_low' in signal_set:
                    return True, "超跌反弹允许"
                return False, f"做多但包含空头信号: {', '.join(conflicts)}"

        # 检查做空信号中的矛盾
        elif side == 'SHORT':
            conflicts = bullish_signals & signal_set
            if conflicts:
                # 特殊情况：如果只有momentum_down_3pct，可能是超涨回调，允许
                if conflicts == {'momentum_down_3pct'} and 'position_high' in signal_set:
                    return True, "超涨回调允许"
                return False, f"做空但包含多头信号: {', '.join(conflicts)}"

        return True, "信号方向一致"
'''

# 在 validate_signal_timeframe 后添加函数
insert_marker = '        return True, "时间框架一致"\n\n    def calculate_volatility_adjusted_stop_loss'
if insert_marker in content and '_validate_signal_direction' not in content:
    content = content.replace(
        insert_marker,
        f'        return True, "时间框架一致"\n{validate_func}\n    def calculate_volatility_adjusted_stop_loss'
    )
    print('✓ 修复2: 添加 _validate_signal_direction 函数')
else:
    print('⚠️ 修复2: 函数已存在或未找到插入位置')

# 写回文件
with open('coin_futures_trader_service.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('\n✅ 币本位服务修复完成!')
