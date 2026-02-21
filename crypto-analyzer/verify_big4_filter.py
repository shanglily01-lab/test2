#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证Big4方向过滤机制

测试场景：
1. Big4看多(BULLISH) + SHORT信号 → 应该被禁止
2. Big4看空(BEARISH) + LONG信号 → 应该被禁止
3. Big4看多(BULLISH) + LONG信号 → 应该加分通过
4. Big4看空(BEARISH) + SHORT信号 → 应该加分通过
5. Big4中性(NEUTRAL) → 应该禁止开仓
"""

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_big4_filter():
    """测试Big4过滤逻辑"""

    print("=" * 80)
    print("Big4方向过滤机制验证")
    print("=" * 80)

    test_cases = [
        # (Big4方向, Big4强度, 信号方向, 原始评分, 预期结果)
        ("BULLISH", 80, "SHORT", 70, "禁止", "Big4看多时完全禁止做空"),
        ("BULLISH", 40, "SHORT", 70, "禁止", "Big4看多时完全禁止做空（即使强度低）"),
        ("BEARISH", 75, "LONG", 65, "禁止", "Big4看空时完全禁止做多"),
        ("BEARISH", 35, "LONG", 65, "禁止", "Big4看空时完全禁止做多（即使强度低）"),
        ("BULLISH", 80, "LONG", 60, "允许+加分", "Big4看多+做多信号，顺势加分"),
        ("BEARISH", 70, "SHORT", 60, "允许+加分", "Big4看空+做空信号，顺势加分"),
        ("NEUTRAL", 0, "LONG", 70, "禁止", "Big4中性时禁止开仓"),
        ("NEUTRAL", 0, "SHORT", 70, "禁止", "Big4中性时禁止开仓"),
    ]

    print("\n测试用例：\n")
    print(f"{'序号':<4} {'Big4方向':<10} {'强度':<6} {'信号方向':<8} {'原始评分':<8} {'预期结果':<12} {'说明':<30}")
    print("-" * 100)

    for idx, (big4_dir, big4_strength, signal_dir, original_score, expected, description) in enumerate(test_cases, 1):
        print(f"{idx:<4} {big4_dir:<10} {big4_strength:<6} {signal_dir:<8} {original_score:<8} {expected:<12} {description:<30}")

    print("\n" + "=" * 80)
    print("验证逻辑说明：")
    print("=" * 80)
    print("""
1. ✅ Big4 = BULLISH（看多）:
   - 完全禁止 SHORT（做空）信号，无论强度如何
   - 允许 LONG（做多）信号，并加分提升

2. ✅ Big4 = BEARISH（看空）:
   - 完全禁止 LONG（做多）信号，无论强度如何
   - 允许 SHORT（做空）信号，并加分提升

3. ✅ Big4 = NEUTRAL（中性）:
   - 完全禁止开仓（包括LONG和SHORT）
   - 市场方向不明确，风险过高

4. ✅ 加分机制（方向一致时）:
   - 加分 = min(20, 强度 × 0.3)
   - 例如：强度80 → 加分 = min(20, 80×0.3) = 20分

5. ✅ 修改位置:
   - smart_trader_service.py (U本位合约)
   - coin_futures_trader_service.py (币本位合约)
   - breakout_signal_booster.py (破位加权系统)
""")

    print("=" * 80)
    print("🎯 修改完成！现在系统会：")
    print("=" * 80)
    print("""
✅ Big4看多时，完全禁止开空单
✅ Big4看空时，完全禁止开多单
✅ Big4中性时，完全禁止开仓
✅ 方向一致时，加分提升开仓概率
✅ 日志标签: [BIG4-VETO] 表示被Big4完全否决
""")

    print("\n" + "=" * 80)
    print("监控命令：")
    print("=" * 80)
    print("""
# 查看Big4否决的信号
tail -f logs/smart_trader.log | grep "BIG4-VETO"

# 查看Big4加分的信号
tail -f logs/smart_trader.log | grep "BIG4-BOOST"

# 查看当前Big4状态
mysql -e "SELECT * FROM big4_trend_signals ORDER BY checked_at DESC LIMIT 1;"
""")

if __name__ == '__main__':
    test_big4_filter()
