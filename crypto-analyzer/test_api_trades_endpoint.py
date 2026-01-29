#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测试交易历史API端点是否正常返回数据"""

import requests
import json

# 测试API端点
url = "http://localhost:8000/api/paper-trading/trades"

print('=' * 100)
print('测试交易历史API端点')
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
            print(f"✅ 成功! 找到 {len(data['trades'])} 条交易记录")
            print()
            print('交易记录摘要:')
            for i, trade in enumerate(data['trades'], 1):
                pnl_str = ''
                if trade.get('realized_pnl') is not None:
                    pnl_str = f" | 盈亏: {trade['realized_pnl']:.2f} USDT"
                    if trade.get('pnl_pct') is not None:
                        pnl_str += f" ({trade['pnl_pct']:.2f}%)"

                print(f"  {i}. {trade['symbol']} {trade['side']} @ {trade['price']:.2f} "
                      f"x {trade['quantity']:.6f}{pnl_str}")
        else:
            print('⚠️  API返回成功，但没有交易记录')
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
print('  - 如果API返回了4条测试交易记录，说明后端正常工作')
print('  - 如果前端页面仍然显示"暂无交易记录"，可能需要:')
print('    1. 清除浏览器缓存')
print('    2. 刷新页面 (Ctrl+F5 强制刷新)')
print('    3. 检查浏览器控制台是否有JavaScript错误')
print()
