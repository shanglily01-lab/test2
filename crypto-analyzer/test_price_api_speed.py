#!/usr/bin/env python3
"""
测试 Paper Trading Price API 的响应速度
"""

import requests
import time

def test_price_api():
    """测试价格 API 的响应速度"""

    print("=" * 80)
    print("测试 Paper Trading Price API 响应速度")
    print("=" * 80)
    print()

    base_url = "http://127.0.0.1:8000"

    # 测试的交易对
    test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'HYPE/USDT', 'DOGE/USDT']

    print("测试交易对价格查询速度:")
    print()
    print(f"{'交易对':<15} {'价格':<15} {'响应时间':<15} {'状态'}")
    print("-" * 80)

    for symbol in test_symbols:
        try:
            # 测试不带缓存参数
            start = time.time()
            response = requests.get(
                f"{base_url}/api/paper-trading/price",
                params={'symbol': symbol},
                timeout=30  # 增加到 30 秒
            )
            elapsed_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                price = data.get('price', 0)

                # 判断速度
                if elapsed_ms < 100:
                    speed_indicator = "✅ 很快"
                elif elapsed_ms < 500:
                    speed_indicator = "⚠️ 正常"
                elif elapsed_ms < 2000:
                    speed_indicator = "⚠️ 慢"
                else:
                    speed_indicator = "❌ 很慢"

                print(f"{symbol:<15} ${price:<14.2f} {elapsed_ms:<14.1f}ms {speed_indicator}")
            else:
                print(f"{symbol:<15} {'错误':<15} {elapsed_ms:<14.1f}ms ❌ HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            print(f"{symbol:<15} {'超时':<15} {'>10000':<14}ms ❌ 请求超时")
        except Exception as e:
            print(f"{symbol:<15} {'失败':<15} {'-':<14} ❌ {e}")

    print()
    print("=" * 80)
    print("测试强制刷新（force_refresh=true）")
    print("=" * 80)
    print()

    print(f"{'交易对':<15} {'价格':<15} {'响应时间':<15} {'状态'}")
    print("-" * 80)

    for symbol in ['SOL/USDT', 'HYPE/USDT']:
        try:
            start = time.time()
            response = requests.get(
                f"{base_url}/api/paper-trading/price",
                params={'symbol': symbol, 'force_refresh': 'true'},
                timeout=30  # 增加到 30 秒
            )
            elapsed_ms = (time.time() - start) * 1000

            if response.status_code == 200:
                data = response.json()
                price = data.get('price', 0)

                if elapsed_ms < 100:
                    speed_indicator = "✅ 很快"
                elif elapsed_ms < 500:
                    speed_indicator = "⚠️ 正常"
                elif elapsed_ms < 2000:
                    speed_indicator = "⚠️ 慢"
                else:
                    speed_indicator = "❌ 很慢"

                print(f"{symbol:<15} ${price:<14.2f} {elapsed_ms:<14.1f}ms {speed_indicator}")
            else:
                print(f"{symbol:<15} {'错误':<15} {elapsed_ms:<14.1f}ms ❌ HTTP {response.status_code}")

        except requests.exceptions.Timeout:
            print(f"{symbol:<15} {'超时':<15} {'>10000':<14}ms ❌ 请求超时")
        except Exception as e:
            print(f"{symbol:<15} {'失败':<15} {'-':<14} ❌ {e}")

    print()
    print("=" * 80)
    print("性能分析")
    print("=" * 80)
    print()
    print("响应时间标准:")
    print("  ✅ <100ms   : 很快，用户无感知")
    print("  ⚠️ 100-500ms: 正常，略有延迟")
    print("  ⚠️ 500-2s   : 慢，用户能感觉到延迟")
    print("  ❌ >2s      : 很慢，用户体验差")
    print()
    print("如果响应时间 >2s，可能原因:")
    print("  1. FastAPI 服务阻塞（检查是否有多个进程）")
    print("  2. 数据库连接慢（检查 MySQL 服务状态）")
    print("  3. 网络问题（检查防火墙、代理设置）")
    print()

if __name__ == "__main__":
    try:
        test_price_api()
    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
