"""
测试实时采集功能
手动触发一次数据采集，看是否能成功保存到数据库
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import asyncio
import yaml
from datetime import datetime
from sqlalchemy import text
from app.collectors.price_collector import MultiExchangeCollector
from app.database.db_service import DatabaseService

print("=" * 100)
print("测试实时K线采集")
print("=" * 100 + "\n")

# 加载配置
with open('config.yaml', 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# 初始化
collector = MultiExchangeCollector(config)
db_service = DatabaseService(config.get('database', {}))

# 测试币种
test_symbol = 'BTC/USDT'

async def test_collect():
    print(f"1. 测试采集 {test_symbol} 的5分钟K线...\n")

    # 采集K线
    df = await collector.fetch_ohlcv(test_symbol, timeframe='5m', exchange='binance')

    if df is not None and len(df) > 0:
        print(f"✅ 成功获取K线数据，共 {len(df)} 根\n")

        # 显示最新一根K线
        latest = df.iloc[-1]
        print(f"最新K线数据:")
        print(f"  时间: {latest['timestamp']}")
        print(f"  开: {latest['open']}")
        print(f"  高: {latest['high']}")
        print(f"  低: {latest['low']}")
        print(f"  收: {latest['close']}")
        print(f"  成交量: {latest['volume']}")

        # 检查是否有 quote_volume
        if 'quote_volume' in latest.index:
            print(f"  成交额(quote_volume): ${latest['quote_volume']:,.2f}")
            print(f"\n✅ DataFrame 包含 quote_volume 字段")
        else:
            print(f"  成交额: ❌ 没有 quote_volume 字段")
            print(f"\n❌ DataFrame 不包含 quote_volume 字段")
            print(f"   这说明采集器修复可能没有生效")

        # 尝试保存到数据库
        print(f"\n2. 测试保存到数据库...\n")

        kline_data = {
            'symbol': test_symbol,
            'exchange': 'binance',
            'timeframe': '5m',
            'open_time': int(latest['timestamp'].timestamp() * 1000),
            'timestamp': latest['timestamp'],
            'open': latest['open'],
            'high': latest['high'],
            'low': latest['low'],
            'close': latest['close'],
            'volume': latest['volume'],
            'quote_volume': latest.get('quote_volume') if 'quote_volume' in latest.index else None
        }

        print(f"准备保存的数据:")
        for key, value in kline_data.items():
            if key == 'quote_volume':
                if value is not None:
                    print(f"  {key}: ${value:,.2f} ✅")
                else:
                    print(f"  {key}: NULL ❌")
            elif key not in ['open_time', 'timestamp']:
                print(f"  {key}: {value}")

        # 保存
        success = db_service.save_kline_data(kline_data)

        if success:
            print(f"\n✅ 保存成功")

            # 验证数据库中的数据
            print(f"\n3. 验证数据库中的数据...\n")

            session = db_service.get_session()
            try:
                sql = text("""
                    SELECT timestamp, close_price, volume, quote_volume
                    FROM kline_data
                    WHERE symbol = :symbol
                    AND timeframe = '5m'
                    ORDER BY timestamp DESC
                    LIMIT 1
                """)

                result = session.execute(sql, {"symbol": test_symbol}).fetchone()

                if result:
                    print(f"数据库中的最新记录:")
                    print(f"  时间: {result[0]}")
                    print(f"  收盘价: ${result[1]}")
                    print(f"  成交量: {result[2]}")

                    if result[3] and result[3] > 0:
                        print(f"  成交额: ${result[3]:,.2f} ✅")
                        print(f"\n🎉🎉🎉 成功！数据库中有 quote_volume 数据！")
                    else:
                        print(f"  成交额: NULL/0 ❌")
                        print(f"\n⚠️  数据库中的 quote_volume 仍然是 NULL")
                        print(f"   可能原因:")
                        print(f"   1. 采集器代码没有更新（需要重启 scheduler）")
                        print(f"   2. DataFrame 中没有 quote_volume")
                else:
                    print("⚠️  数据库中没有找到数据")

            finally:
                session.close()
        else:
            print(f"\n❌ 保存失败")
    else:
        print(f"❌ 获取K线失败")

# 运行测试
asyncio.run(test_collect())

print("\n" + "=" * 100)
print("\n结论:")
print("如果看到 '🎉🎉🎉 成功'，说明修复已生效")
print("如果仍然是 NULL，说明需要:")
print("  1. 确认代码已更新: git pull")
print("  2. 重启 scheduler")
print("  3. 等待新的K线生成")
print("\n" + "=" * 100)
