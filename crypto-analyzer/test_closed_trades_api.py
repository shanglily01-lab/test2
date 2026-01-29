#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试已平仓交易历史API端点"""

import requests
import json

# 测试新的API端点
url = "http://localhost:8000/api/paper-trading/closed-trades"

print('=' * 100)
print('测试已平仓交易历史API端点')
print('=' * 100)
print()

print(f'请求URL: {url}')
print()

try:
    response = requests.get(url, timeout=5)

    print(f'响应状态码: {response.status_code}')
    print()

    if response.status_code == 200:
        data = response.json()
        print('API返回数据:')
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print()

        if data.get('trades'):
            print(f"✅ 成功! 找到 {len(data['trades'])} 条已平仓交易记录")
            print()
            print('已平仓交易记录详情:')
            print('-' * 100)
            for i, trade in enumerate(data['trades'], 1):
                cost_price = trade.get('cost_price', 0)
                sell_price = trade.get('price', 0)
                pnl = trade.get('realized_pnl', 0)
                pnl_pct = trade.get('pnl_pct', 0)

                print(f"\n{i}. {trade['symbol']} - 平仓交易")
                print(f"   交易时间: {trade['trade_time']}")
                print(f"   数量: {trade['quantity']:.6f}")
                print(f"   成本价: ${cost_price:.2f}")
                print(f"   卖出价: ${sell_price:.2f}")
                print(f"   盈亏: ${pnl:+.2f} USDT ({pnl_pct:+.2f}%)")
                print(f"   来源: {trade['order_source']}")
                if trade.get('stop_loss_price'):
                    print(f"   止损价: ${trade['stop_loss_price']:.2f}")
                if trade.get('take_profit_price'):
                    print(f"   止盈价: ${trade['take_profit_price']:.2f}")
        else:
            print('⚠️  API返回成功，但没有已平仓交易记录')
    else:
        print(f'❌ API返回错误状态码: {response.status_code}')
        print(f'响应内容: {response.text}')

except requests.exceptions.ConnectionError:
    print('❌ 无法连接到服务器')
    print('提示: 请确保FastAPI服务正在运行 (python app/main.py)')
except Exception as e:
    print(f'❌ 请求失败: {e}')

print()
print('=' * 100)
print()
print('说明:')
print('  - 此API仅返回已平仓的交易记录（SELL交易且有realized_pnl）')
print('  - 前端页面的"交易历史"标签页现在使用此API显示已平仓记录')
print('  - 每条记录包含: 成本价、卖出价、盈亏金额和盈亏百分比')
print()
