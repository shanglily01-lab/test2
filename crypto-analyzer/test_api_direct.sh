#!/bin/bash
# 直接测试 API 返回

echo "=== 测试 API 第1页 ==="
curl -s "http://localhost:8000/api/futures/trades?account_id=2&page=1&page_size=10" | python3 -c "
import sys
import json

data = json.load(sys.stdin)

if data.get('success'):
    trades = data.get('data', [])
    print(f'返回记录数: {len(trades)}')
    print(f'总记录数: {data.get(\"total_count\", 0)}')
    print(f'总页数: {data.get(\"total_pages\", 0)}')
    print()
    print('前10条:')
    for i, t in enumerate(trades, 1):
        marker = ' ← NIGHT' if t.get('position_id') == 5175 else (' ← BTC' if t.get('position_id') == 5110 else '')
        print(f'{i:2d}. {t[\"symbol\"]:<15} pnl={t.get(\"realized_pnl\", 0):<8.2f} time={t.get(\"trade_time\", \"N/A\")}{marker}')
else:
    print('API 返回失败:', data.get('message'))
"

echo ""
echo "=== 如果上面看到了 NIGHT 和 BTC，说明 API 正常 ==="
echo "=== 问题在浏览器缓存或前端 JavaScript ==="
