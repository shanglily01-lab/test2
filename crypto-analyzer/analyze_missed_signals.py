#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
分析错过的交易信号
计算实际评分，找出为什么没有开仓
"""
from loguru import logger
from app.analyzers.kline_strength_scorer import KlineStrengthScorer


def analyze_signal(symbol, direction, net_1h, net_15m, net_5m):
    """分析单个信号的评分"""

    # 模拟K线强度数据
    strength_1h = {'net_power': net_1h, 'bull_pct': 50}
    strength_15m = {'net_power': net_15m, 'bull_pct': 50}
    strength_5m = {'net_power': net_5m, 'bull_pct': 50}

    # 创建评分器
    scorer = KlineStrengthScorer()

    # 计算评分
    result = scorer.calculate_strength_score(strength_1h, strength_15m, strength_5m)

    return result


def main():
    """主函数"""
    logger.info("=" * 100)
    logger.info("分析错过的交易信号")
    logger.info("=" * 100)

    # 错过的信号数据
    missed_signals = [
        {"symbol": "VET/USDT", "direction": "LONG", "net_1h": 9, "net_15m": 3, "net_5m": -7},
        {"symbol": "FOGO/USDT", "direction": "SHORT", "net_1h": -6, "net_15m": -8, "net_5m": -18},
        {"symbol": "AXS/USDT", "direction": "SHORT", "net_1h": -5, "net_15m": -5, "net_5m": 0},
        {"symbol": "ZAMA/USDT", "direction": "SHORT", "net_1h": -5, "net_15m": -2, "net_5m": -12},
        {"symbol": "TRB/USDT", "direction": "LONG", "net_1h": 5, "net_15m": 5, "net_5m": -2},
        {"symbol": "SPACE/USDT", "direction": "SHORT", "net_1h": -4, "net_15m": -7, "net_5m": -10},
        {"symbol": "DASH/USDT", "direction": "SHORT", "net_1h": -4, "net_15m": -3, "net_5m": 1},
        {"symbol": "LINEA/USDT", "direction": "LONG", "net_1h": 4, "net_15m": -11, "net_5m": -19},
        {"symbol": "FHE/USDT", "direction": "LONG", "net_1h": 4, "net_15m": 4, "net_5m": -8},
        {"symbol": "RLC/USDT", "direction": "SHORT", "net_1h": -4, "net_15m": -6, "net_5m": -9},
    ]

    threshold = 30  # 开仓阈值

    logger.info(f"\n开仓阈值: {threshold}分\n")

    results = []

    for signal in missed_signals:
        result = analyze_signal(
            signal['symbol'],
            signal['direction'],
            signal['net_1h'],
            signal['net_15m'],
            signal['net_5m']
        )

        result['symbol'] = signal['symbol']
        result['expected_direction'] = signal['direction']
        results.append(result)

        # 判断是否达到开仓阈值
        passed = result['total_score'] >= threshold
        status = "✅ 达标" if passed else "❌ 未达标"

        logger.info(f"\n{signal['symbol']:12s} {signal['direction']:5s}  {status}")
        logger.info(f"  1H净力量: {signal['net_1h']:+3d} → 1H评分: {result['score_1h']:2d}分")
        logger.info(f"  15M净力量: {signal['net_15m']:+3d} → 15M评分: {result['score_15m']:+3d}分")
        logger.info(f"  5M净力量: {signal['net_5m']:+3d} → 5M评分: {result['score_5m']:+2d}分")
        logger.info(f"  ────────────────────────")
        logger.info(f"  总评分: {result['total_score']}分 (需要{threshold}分)")
        logger.info(f"  差距: {result['total_score'] - threshold:+d}分")
        logger.info(f"  方向: {result['direction']} (预期: {signal['direction']})")
        logger.info(f"  强度: {result['strength']}")
        logger.info(f"  一致性: {'是' if result['consistency'] else '否'}")
        logger.info(f"  原因: {', '.join(result['reasons'])}")

    # 统计分析
    logger.info("\n" + "=" * 100)
    logger.info("统计分析")
    logger.info("=" * 100)

    passed_count = sum(1 for r in results if r['total_score'] >= threshold)
    failed_count = len(results) - passed_count

    logger.info(f"\n总信号数: {len(results)}")
    logger.info(f"达标信号: {passed_count} ({passed_count/len(results)*100:.1f}%)")
    logger.info(f"未达标信号: {failed_count} ({failed_count/len(results)*100:.1f}%)")

    # 分析未达标原因
    logger.info("\n" + "=" * 100)
    logger.info("未达标原因分析")
    logger.info("=" * 100)

    failed_signals = [r for r in results if r['total_score'] < threshold]

    if failed_signals:
        avg_gap = sum(threshold - r['total_score'] for r in failed_signals) / len(failed_signals)
        logger.info(f"\n平均分数差距: {avg_gap:.1f}分")

        # 分析主要扣分原因
        conflict_15m = sum(1 for r in failed_signals if r['score_15m'] < 0)
        conflict_5m = sum(1 for r in failed_signals if r['score_5m'] < 0)
        weak_1h = sum(1 for r in failed_signals if r['score_1h'] < 15)

        logger.info(f"\n扣分原因统计:")
        logger.info(f"  15M信号冲突: {conflict_15m}/{len(failed_signals)} ({conflict_15m/len(failed_signals)*100:.0f}%)")
        logger.info(f"  5M信号冲突: {conflict_5m}/{len(failed_signals)} ({conflict_5m/len(failed_signals)*100:.0f}%)")
        logger.info(f"  1H强度较弱(<15分): {weak_1h}/{len(failed_signals)} ({weak_1h/len(failed_signals)*100:.0f}%)")

    # 具体案例分析
    logger.info("\n" + "=" * 100)
    logger.info("典型案例分析")
    logger.info("=" * 100)

    # 案例1: VET/USDT - 1H很强但5M冲突
    vet = next(r for r in results if r['symbol'] == 'VET/USDT')
    logger.info(f"\n案例1: {vet['symbol']} (1H强势但5M冲突)")
    logger.info(f"  1H净力量+9 → 20分 (强多)")
    logger.info(f"  15M净力量+3 → 5分 (趋势微弱)")
    logger.info(f"  5M净力量-7 → -5分 (信号冲突)")
    logger.info(f"  总分: {vet['total_score']}分 (差{threshold - vet['total_score']}分)")
    logger.info(f"  问题: 5M短期回调扣分，导致总分不足")

    # 案例2: FOGO/USDT - 15M和5M都很强，但1H中等
    fogo = next(r for r in results if r['symbol'] == 'FOGO/USDT')
    logger.info(f"\n案例2: {fogo['symbol']} (15M/5M强势但1H中等)")
    logger.info(f"  1H净力量-6 → 15分 (偏空)")
    logger.info(f"  15M净力量-8 → 15分 (强化趋势)")
    logger.info(f"  5M净力量-18 → 5分 (入场时机优)")
    logger.info(f"  总分: {fogo['total_score']}分 (差{threshold - fogo['total_score']}分)")
    logger.info(f"  问题: 1H净力量-6只有15分，不是强空(需要-8)")

    # 建议
    logger.info("\n" + "=" * 100)
    logger.info("优化建议")
    logger.info("=" * 100)

    logger.info("""
1. 降低开仓阈值
   当前: 30分
   建议: 25-28分
   理由: 很多高质量信号(1H强+15M确认)得分在25-29分区间

2. 调整5M评分权重
   问题: 5M短期回调导致扣5分，影响较大
   建议: 5M冲突扣分从-5改为-3
   理由: 5M是短期噪音，不应该对中长期趋势影响太大

3. 1H评分档位优化
   当前: 净力量需要±8才能得20分(强势)
   建议: 净力量±6也给18分(次强)
   理由: ±6已经是明确趋势，不应该和±5混在一起

4. 增加15M权重
   问题: 15M强化趋势最多+15分，权重偏低
   建议: 15M超强(净力量>8)时给+18分
   理由: 15M是短中期确认，非常重要

5. 一致性奖励
   建议: 三周期方向一致时额外+3分
   理由: 多周期共振是高质量信号的标志
""")

    logger.info("\n" + "=" * 100)
    logger.info("分析完成")
    logger.info("=" * 100)


if __name__ == '__main__':
    main()
