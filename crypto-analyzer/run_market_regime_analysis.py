#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
运行6小时市场状态分析
影响超级大脑的交易决策
"""

import sys
import io

# Fix Windows encoding
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, 'app/services')

from market_regime_manager import MarketRegimeManager

# 数据库配置
db_config = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}

def main():
    manager = MarketRegimeManager(db_config)

    print('\n分析6小时市场状态...')

    # 分析市场状态
    regime = manager.analyze_market_regime()

    # 打印报告
    manager.print_regime_report(regime)

    # 保存到文件
    from datetime import datetime
    report_file = f"logs/regime_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

    import os
    os.makedirs('logs', exist_ok=True)

    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('=' * 100 + '\n')
        f.write('6小时市场状态报告\n')
        f.write('=' * 100 + '\n\n')
        f.write(f"时间: {regime['timestamp'].strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"市场状态: {regime['regime'].upper()}\n")
        f.write(f"市场强度: {regime['strength']:.1f}/100\n")
        f.write(f"交易倾向: {regime['bias'].upper()}\n\n")

        f.write(f"大盘走势 (6小时):\n")
        f.write(f"  BTC变化: {regime['btc_6h_change']:+.2f}%\n")
        f.write(f"  ETH变化: {regime['eth_6h_change']:+.2f}%\n")
        f.write(f"  趋势一致性: {regime['trend_consistency']:.1f}%\n\n")

        f.write(f"策略调整:\n")
        f.write(f"  仓位倍数: {regime['position_adjustment']:.2f}x\n")
        f.write(f"  分数阈值: {regime['score_threshold_adjustment']:+d}\n\n")

        f.write(f"交易建议:\n")
        for rec in regime['recommendations']:
            f.write(f"  - {rec}\n")

    print(f"报告已保存: {report_file}\n")

if __name__ == '__main__':
    main()
