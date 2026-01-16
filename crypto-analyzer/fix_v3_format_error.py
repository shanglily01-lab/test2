#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
修复V3趋势质量监控中的格式化字符串错误
"""

import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def fix_format_errors():
    """修复strategy_executor_v2.py中的格式化错误"""
    file_path = "d:/test2/crypto-analyzer/app/services/strategy_executor_v2.py"

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 修复1: 第3244行 - 错误的条件格式化语法
    old_line1 = 'f"(入场{entry_ema_diff:.2f}% 比例{ema_diff_ratio:.2f if ema_diff_ratio else 0:.2f}) "'
    new_line1 = 'f"(入场{entry_ema_diff:.2f}% 比例{ratio_display}) "'

    # 在logger.info之前添加ratio_display变量
    old_block = """        logger.info(
            f"[V3趋势监控] {symbol} "
            f"评分={score}/100 "
            f"方向={details.get('ema_direction')} "
            f"强度={details.get('current_ema_diff', 0):.2f}% "
            f"(入场{entry_ema_diff:.2f}% 比例{ema_diff_ratio:.2f if ema_diff_ratio else 0:.2f}) "
            f"变化={details.get('ema_trend')} "
            f"1H={'✓' if details.get('1h_support') else '✗'} "
            f"RSI={details.get('rsi', 50):.1f}({details.get('rsi_state', 'unknown')}) "
            f"盈亏={current_pnl_pct:+.2f}%"
        )"""

    new_block = """        # 安全处理ema_diff_ratio，避免None值
        ratio_display = f"{ema_diff_ratio:.2f}" if ema_diff_ratio is not None else "0.00"

        logger.info(
            f"[V3趋势监控] {symbol} "
            f"评分={score}/100 "
            f"方向={details.get('ema_direction')} "
            f"强度={details.get('current_ema_diff', 0):.2f}% "
            f"(入场{entry_ema_diff:.2f}% 比例{ratio_display}) "
            f"变化={details.get('ema_trend')} "
            f"1H={'✓' if details.get('1h_support') else '✗'} "
            f"RSI={details.get('rsi', 50):.1f}({details.get('rsi_state', 'unknown')}) "
            f"盈亏={current_pnl_pct:+.2f}%"
        )"""

    if old_block in content:
        content = content.replace(old_block, new_block)
        print("✅ 修复logger.info中的ema_diff_ratio格式化错误")
    else:
        print("⚠️ 未找到logger.info中的旧代码块")

    # 检查是否还有其他类似问题
    if ":.2f if" in content and "else 0:.2f" in content:
        print("⚠️ 警告：代码中仍存在其他类似的格式化错误")

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"✅ 文件已更新: {file_path}")

if __name__ == "__main__":
    print("开始修复V3格式化错误...")
    print("=" * 60)
    fix_format_errors()
    print("=" * 60)
    print("✅ 修复完成！")
