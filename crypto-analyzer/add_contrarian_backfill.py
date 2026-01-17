#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Add contrarian configuration backfill to editStrategy function"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Read the HTML file
with open('templates/trading_strategies.html', 'r', encoding='utf-8') as f:
    content = f.read()

# Contrarian backfill code to add
contrarian_backfill = '''
            // 反向操作策略配置 ⚡ 新增
            if (strategy.contrarianEnabled) {
                const contrarianEnabled = document.getElementById('contrarianEnabled');
                if (contrarianEnabled) {
                    contrarianEnabled.checked = true;
                    toggleContrarianConfig();
                }
            }

            // 市场环境模式
            if (strategy.marketRegime) {
                const marketRegimeRadio = document.querySelector(
                    `input[name="marketRegime"][value="${strategy.marketRegime}"]`
                );
                if (marketRegimeRadio) {
                    marketRegimeRadio.checked = true;
                    toggleMarketDetectionConfig();
                }
            }

            // 市场检测参数
            if (strategy.marketDetection) {
                const lookbackHours = document.getElementById('lookbackHours');
                const minTrades = document.getElementById('minTrades');
                if (lookbackHours) lookbackHours.value = strategy.marketDetection.lookbackHours || 24;
                if (minTrades) minTrades.value = strategy.marketDetection.minTrades || 10;
            }

            // 反向操作风险参数
            if (strategy.contrarianRisk) {
                const stopLoss = document.getElementById('contrarianStopLoss');
                const takeProfit = document.getElementById('contrarianTakeProfit');
                const limitOffset = document.getElementById('contrarianLimitOffset');

                if (stopLoss) stopLoss.value = strategy.contrarianRisk.stopLoss || 1.5;
                if (takeProfit) takeProfit.value = strategy.contrarianRisk.takeProfit || 1.0;
                if (limitOffset) limitOffset.value = strategy.contrarianRisk.limitOrderOffset || 0.5;
            }
'''

# Find insertion point (before showAddStrategyModal)
marker = '''            // 显示Modal表单
            showAddStrategyModal();'''

if marker in content:
    content = content.replace(marker, contrarian_backfill + '''
            // 显示Modal表单
            showAddStrategyModal();''')

    with open('templates/trading_strategies.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print('OK - Added contrarian backfill to editStrategy')
else:
    print('ERROR - Insertion point not found')
