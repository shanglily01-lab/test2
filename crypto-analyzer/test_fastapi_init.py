"""
测试 FastAPI Paper Trading API 初始化问题
找出 API 层面的瓶颈
"""
import time
import sys

print("=" * 60)
print("FastAPI Paper Trading 初始化诊断")
print("=" * 60)

# 步骤 1: 导入基础模块
print("\n[步骤 1] 导入基础模块...")
start = time.time()
import yaml
from pathlib import Path
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")

# 步骤 2: 加载配置
print("\n[步骤 2] 加载配置文件...")
start = time.time()
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)
db_config = config.get('database', {}).get('mysql', {})
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")
print(f"   配置: {db_config.get('host')}:{db_config.get('port')}/{db_config.get('database')}")

# 步骤 3: 导入 PaperTradingEngine
print("\n[步骤 3] 导入 PaperTradingEngine...")
start = time.time()
from app.trading.paper_trading_engine import PaperTradingEngine
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")

# 步骤 4: 初始化 Engine
print("\n[步骤 4] 初始化 PaperTradingEngine...")
start = time.time()
engine = PaperTradingEngine(db_config)
print(f"   ✓ 耗时: {time.time() - start:.3f}秒")

# 步骤 5: 调用 get_current_price
print("\n[步骤 5] 调用 get_current_price (10次)...")
times = []
for i in range(10):
    start = time.time()
    price = engine.get_current_price('BTC/USDT')
    elapsed = time.time() - start
    times.append(elapsed)
    print(f"   第 {i+1} 次: {elapsed:.3f}秒, 价格: {price}")

print(f"\n   平均耗时: {sum(times)/len(times):.3f}秒")
print(f"   最快: {min(times):.3f}秒")
print(f"   最慢: {max(times):.3f}秒")

# 步骤 6: 模拟 FastAPI 的处理逻辑
print("\n[步骤 6] 模拟 FastAPI API 处理逻辑...")
start = time.time()

# 模拟 API 端点的逻辑
symbol = 'BTC/USDT'
price = engine.get_current_price(symbol)
from datetime import datetime
response = {
    "symbol": symbol,
    "price": float(price),
    "timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S')
}

elapsed = time.time() - start
print(f"   ✓ 耗时: {elapsed:.3f}秒")
print(f"   响应: {response}")

# 步骤 7: 导入完整的 paper_trading_api 模块
print("\n[步骤 7] 导入 paper_trading_api 模块...")
print("   这一步会初始化整个 API 模块，包括创建全局 engine 实例")
start = time.time()
try:
    from app.api import paper_trading_api
    elapsed = time.time() - start
    print(f"   ✓ 耗时: {elapsed:.3f}秒")

    if elapsed > 1.0:
        print(f"   ⚠️  模块导入耗时较长 ({elapsed:.3f}秒)")
        print("   可能的原因：模块初始化时执行了耗时操作")
except Exception as e:
    elapsed = time.time() - start
    print(f"   ✗ 导入失败: {e}")
    print(f"   耗时: {elapsed:.3f}秒")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("诊断完成！")
print("=" * 60)

# 分析结果
print("\n【分析结果】")
avg_price_call = sum(times) / len(times)

if avg_price_call < 0.01:
    print("✓ get_current_price() 性能正常 (<10ms)")
else:
    print(f"⚠️  get_current_price() 较慢 ({avg_price_call*1000:.1f}ms)")

print("\n【建议】")
print("如果 FastAPI 响应慢，但这个脚本运行快，问题可能在于：")
print("1. FastAPI 启动时的全局初始化")
print("2. 中间件或依赖注入的开销")
print("3. 网络层面的延迟")
print("\n下一步：")
print("- 检查 FastAPI 启动日志是否有错误")
print("- 使用浏览器 F12 Network 查看请求耗时分布")
print("- 检查是否有其他 API 端点也很慢")

print("\n按任意键退出...")
input()
