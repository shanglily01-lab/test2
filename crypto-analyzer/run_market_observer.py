#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
运行市场观察器
实时监控BTC, ETH, SOL, BNB, DOGE的走势
"""

import sys
import io

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, 'app/services')

from market_observer import MarketObserver

# 数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def main():
    observer = MarketObserver(db_config)

    print('\n🔍 正在分析市场...')

    # 分析市场状态
    state = observer.analyze_market_state()

    # 打印报告
    observer.print_market_report(state)

    # 保存到文件
    from datetime import datetime
    report_file = f"logs/market_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    import os
    os.makedirs('logs', exist_ok=True)

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('=' * 100 + '\n')
        f.write('市场观察报告\n')
        f.write('=' * 100 + '\n\n')
        f.write(f"时间: {state['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"整体趋势: {state['overall_trend'].upper()}\n")
        f.write(f"市场强度: {state['market_strength']:.1f}/100\n\n")

        for symbol, data in state['symbols_analysis'].items():
            f.write(f"\n{symbol}:\n")
            f.write(f"  价格: ${data['current_price']:.2f}\n")
            f.write(f"  趋势: {data['trend']}\n")
            f.write(f"  变化: 1H={data['price_changes']['1h']:.2f}%, ")
            f.write(f"4H={data['price_changes']['4h']:.2f}%, ")
            f.write(f"1D={data['price_changes']['1d']:.2f}%\n")
            f.write(f"  RSI: {data['rsi']:.1f}\n")
            if data['warnings']:
                f.write(f"  预警: {', '.join(data['warnings'])}\n")

        if state['warnings']:
            f.write('\n预警汇总:\n')
            for warning in state['warnings']:
                f.write(f"  - {warning}\n")

    print(f"\n📄 报告已保存: {report_file}")

if __name__ == '__main__':
    main()
