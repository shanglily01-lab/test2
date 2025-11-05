"""
测试 HTTP 响应数据
用于诊断 volume_24h 和 quote_volume_24h 在 HTTP 传输中是否被修改
"""
import requests
import json

print("\n" + "="*80)
print("测试 HTTP 响应数据")
print("="*80 + "\n")

try:
    # 发送 HTTP 请求
    print("正在请求 http://localhost:9020/api/dashboard ...")
    response = requests.get('http://localhost:9020/api/dashboard', timeout=10)

    if response.status_code != 200:
        print(f"❌ HTTP 请求失败，状态码: {response.status_code}")
        exit(1)

    # 解析 JSON
    data = response.json()

    if not data.get('success'):
        print("❌ API 返回失败")
        print(f"响应: {data}")
        exit(1)

    prices = data.get('data', {}).get('prices', [])

    if not prices:
        print("❌ prices 列表为空")
        exit(1)

    # 查找 BTC
    btc = None
    for p in prices:
        if p.get('full_symbol') == 'BTC/USDT':
            btc = p
            break

    if not btc:
        print("❌ 未找到 BTC/USDT 数据")
        exit(1)

    print("✓ 成功获取数据\n")

    # 显示 BTC 数据
    print("HTTP 响应中的 BTC 数据:")
    print("-" * 80)
    print(f"  symbol: {btc.get('symbol')}")
    print(f"  full_symbol: {btc.get('full_symbol')}")
    print(f"  price: {btc.get('price')}")
    print(f"  volume_24h: {btc.get('volume_24h')}")
    print(f"  quote_volume_24h: {btc.get('quote_volume_24h')}")
    print()

    # 判断数据
    vol = btc.get('volume_24h', 0)
    quote = btc.get('quote_volume_24h')

    print("数据分析:")
    print("-" * 80)

    if vol < 100000:
        print(f"  ✓ volume_24h 看起来正确 ({vol:,.2f} BTC)")
    else:
        print(f"  ❌ volume_24h 异常 ({vol:,.2f})，这个数值太大，可能是金额而不是数量")

    if quote is None:
        print(f"  ❌ quote_volume_24h 为 None（字段缺失）")
    elif quote == 0:
        print(f"  ⚠️ quote_volume_24h 为 0")
    elif quote > 1000000000:
        print(f"  ✓ quote_volume_24h 看起来正确 (${quote:,.2f})")
    else:
        print(f"  ⚠️ quote_volume_24h 可能不正确 (${quote:,.2f})")

    # 显示原始 JSON
    print("\n原始 JSON 数据（BTC 部分）:")
    print("-" * 80)
    json_str = json.dumps(btc, indent=2, ensure_ascii=False)
    print(json_str)

    # 总结
    print("\n" + "="*80)
    print("总结:")
    print("="*80)

    if vol < 100000 and quote and quote > 1000000000:
        print("✅ HTTP 响应数据正确")
        print("   volume_24h 是 BTC 数量，quote_volume_24h 是美元金额")
        print("   问题可能在前端 JavaScript 处理")
    else:
        print("❌ HTTP 响应数据不正确")
        print("   问题在 FastAPI 序列化或中间件")

    print("="*80 + "\n")

except requests.exceptions.ConnectionError:
    print("❌ 无法连接到 http://localhost:9020")
    print("   请确保后端服务正在运行")
except requests.exceptions.Timeout:
    print("❌ 请求超时")
except Exception as e:
    print(f"❌ 发生错误: {e}")
    import traceback
    traceback.print_exc()
