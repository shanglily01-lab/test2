#!/usr/bin/env python3
"""
测试交易控制API
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

print("=" * 80)
print("测试交易控制API")
print("=" * 80)

# 测试1: 获取U本位合约状态
print("\n1. 测试 GET /api/trading-control/status/2/usdt_futures")
response = client.get("/api/trading-control/status/2/usdt_futures")
print(f"   状态码: {response.status_code}")
print(f"   响应: {response.json()}")

# 测试2: 获取币本位合约状态
print("\n2. 测试 GET /api/trading-control/status/3/coin_futures")
response = client.get("/api/trading-control/status/3/coin_futures")
print(f"   状态码: {response.status_code}")
print(f"   响应: {response.json()}")

# 测试3: 切换U本位合约状态
print("\n3. 测试 POST /api/trading-control/toggle (停止U本位)")
response = client.post("/api/trading-control/toggle", json={
    "account_id": 2,
    "trading_type": "usdt_futures",
    "trading_enabled": False,
    "updated_by": "test_script"
})
print(f"   状态码: {response.status_code}")
if response.status_code == 200:
    print(f"   响应: {response.json()}")
else:
    print(f"   错误: {response.text}")

# 测试4: 再次查询确认状态已改变
print("\n4. 确认状态已改变")
response = client.get("/api/trading-control/status/2/usdt_futures")
print(f"   状态码: {response.status_code}")
print(f"   响应: {response.json()}")

# 测试5: 恢复U本位合约状态
print("\n5. 测试 POST /api/trading-control/toggle (启动U本位)")
response = client.post("/api/trading-control/toggle", json={
    "account_id": 2,
    "trading_type": "usdt_futures",
    "trading_enabled": True,
    "updated_by": "test_script"
})
print(f"   状态码: {response.status_code}")
if response.status_code == 200:
    print(f"   响应: {response.json()}")
else:
    print(f"   错误: {response.text}")

# 测试6: 获取所有状态
print("\n6. 测试 GET /api/trading-control/all")
response = client.get("/api/trading-control/all")
print(f"   状态码: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"   总数: {len(data['data'])}条")
    for item in data['data']:
        print(f"   - account_id={item['account_id']}, type={item['trading_type']}, enabled={item['trading_enabled']}")
else:
    print(f"   错误: {response.text}")

print("\n" + "=" * 80)
print("测试完成")
print("=" * 80)
