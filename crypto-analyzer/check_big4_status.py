#!/usr/bin/env python3
"""检查Big4当前状态"""
import sys
sys.path.insert(0, '/home/test2/crypto-analyzer')

from app.services.big4_trend_detector import Big4TrendDetector
from datetime import datetime
import json

def check_big4_status():
    """检查Big4当前状态"""
    detector = Big4TrendDetector()

    print("=" * 80)
    print("Big4 (BTC/ETH/BNB/SOL) 当前市场状态")
    print("=" * 80)
    print(f"检测时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    try:
        result = detector.detect_market_trend()

        # 1. 整体趋势
        print("【整体趋势】")
        print(f"  信号: {result['overall_signal']}")
        print(f"  强度: {result['signal_strength']:.1f}")
        print(f"  看涨权重: {result['bullish_weight']*100:.0f}%")
        print(f"  看跌权重: {result['bearish_weight']*100:.0f}%")
        print(f"  看涨数量: {result['bullish_count']}/4")
        print(f"  看跌数量: {result['bearish_count']}/4")
        print(f"  建议: {result['recommendation']}")
        print()

        # 2. 紧急干预状态
        emergency = result['emergency_intervention']
        print("【紧急干预状态】")
        print(f"  触底检测: {'🔴 是' if emergency['bottom_detected'] else '✅ 否'}")
        print(f"  触顶检测: {'🔴 是' if emergency['top_detected'] else '✅ 否'}")
        print(f"  禁止做多: {'🚫 是' if emergency['block_long'] else '✅ 否'}")
        print(f"  禁止做空: {'🚫 是' if emergency['block_short'] else '✅ 否'}")

        if emergency['expires_at']:
            print(f"  失效时间: {emergency['expires_at']}")

        if emergency['details']:
            print(f"  详情: {emergency['details']}")

        if emergency.get('bounce_opportunity'):
            print(f"  反弹机会: ✅ 是")
            if emergency.get('bounce_symbols'):
                print(f"  反弹币种: {', '.join(emergency['bounce_symbols'])}")
            if emergency.get('bounce_window_end'):
                print(f"  窗口结束: {emergency['bounce_window_end']}")

        print()

        # 3. 各币种详情
        print("【各币种详情】")
        for symbol, detail in result['details'].items():
            signal_emoji = {
                'BULLISH': '🟢',
                'BEARISH': '🔴',
                'NEUTRAL': '⚪'
            }.get(detail['signal'], '⚪')

            print(f"\n  {signal_emoji} {symbol}")
            print(f"    信号: {detail['signal']}")
            print(f"    强度: {detail['strength']:.1f}")
            print(f"    1H净力量: {detail.get('net_power_1h', 'N/A')}")
            print(f"    15M净力量: {detail.get('net_power_15m', 'N/A')}")

            if 'recent_change_pct' in detail:
                pct = detail['recent_change_pct']
                pct_emoji = '📈' if pct > 0 else '📉' if pct < 0 else '➡️'
                print(f"    近期变化: {pct_emoji} {pct:+.2f}%")

        print()
        print("=" * 80)

    except Exception as e:
        print(f"❌ 检测失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    check_big4_status()
