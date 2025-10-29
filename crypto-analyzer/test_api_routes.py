#!/usr/bin/env python3
"""
测试API路由 - 诊断500错误
"""

import sys
import requests
import json
from pathlib import Path

print("=" * 70)
print("API路由诊断工具")
print("=" * 70)

base_url = "http://localhost:8000"

# 测试的API端点
test_endpoints = [
    "/health",
    "/api",
    "/api/dashboard",
    "/api/ema-signals",
    "/api/corporate-treasury/summary",
]

print(f"\n目标服务器: {base_url}")
print(f"测试端点数: {len(test_endpoints)}\n")

results = []

for endpoint in test_endpoints:
    url = f"{base_url}{endpoint}"
    print(f"📍 测试: {endpoint}")
    print(f"   URL: {url}")

    try:
        response = requests.get(url, timeout=10)
        status = response.status_code

        if status == 200:
            print(f"   ✅ 状态: {status} OK")
            try:
                data = response.json()
                if 'success' in data:
                    print(f"   📊 success: {data.get('success')}")
                if 'message' in data:
                    print(f"   💬 message: {data.get('message')}")
                if endpoint == '/api/ema-signals' and 'data' in data:
                    print(f"   📈 EMA信号数: {len(data.get('data', []))}")
                if endpoint == '/api/corporate-treasury/summary' and 'data' in data:
                    print(f"   🏢 公司数: {len(data.get('data', []))}")
            except:
                print(f"   ⚠️  响应不是JSON格式")
        else:
            print(f"   ❌ 状态: {status} ERROR")
            try:
                error_data = response.json()
                print(f"   📋 错误详情:")
                if 'detail' in error_data:
                    print(f"      detail: {error_data['detail']}")
                if 'type' in error_data:
                    print(f"      type: {error_data['type']}")
                if 'error' in error_data:
                    print(f"      error: {error_data['error']}")
                if 'traceback' in error_data:
                    print(f"   📜 完整堆栈:")
                    print("   " + "-" * 66)
                    for line in error_data['traceback'].split('\n'):
                        print(f"   {line}")
                    print("   " + "-" * 66)
            except:
                print(f"   ⚠️  错误响应不是JSON格式")
                print(f"   响应文本: {response.text[:200]}")

        results.append({
            'endpoint': endpoint,
            'status': status,
            'success': status == 200
        })

    except requests.exceptions.ConnectionError:
        print(f"   ❌ 连接失败: 无法连接到服务器")
        print(f"   💡 请确认服务器是否运行在 {base_url}")
        results.append({
            'endpoint': endpoint,
            'status': 'CONNECTION_ERROR',
            'success': False
        })
    except requests.exceptions.Timeout:
        print(f"   ❌ 超时: 请求超过10秒")
        results.append({
            'endpoint': endpoint,
            'status': 'TIMEOUT',
            'success': False
        })
    except Exception as e:
        print(f"   ❌ 异常: {e}")
        results.append({
            'endpoint': endpoint,
            'status': 'EXCEPTION',
            'success': False
        })

    print()

# 总结
print("=" * 70)
print("测试总结")
print("=" * 70)

success_count = sum(1 for r in results if r['success'])
total_count = len(results)

print(f"\n通过: {success_count}/{total_count}")
print(f"失败: {total_count - success_count}/{total_count}\n")

if success_count == total_count:
    print("✅ 所有API端点正常工作！")
else:
    print("❌ 以下端点有问题:")
    for r in results:
        if not r['success']:
            print(f"   - {r['endpoint']}: {r['status']}")

    print("\n💡 建议:")
    print("   1. 确认服务器已拉取最新代码: git pull")
    print("   2. 确认服务器已重启: python app/main.py")
    print("   3. 检查服务器启动日志是否有错误")
    print("   4. 查看上面的详细错误堆栈信息")

print("\n" + "=" * 70)
