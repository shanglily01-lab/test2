#!/usr/bin/env python3
"""
Gate.io 价格采集脚本
专门采集 Gate.io 特定币种 (如 HYPE)
"""

import asyncio
import yaml
import sys
from datetime import datetime
sys.path.insert(0, '.')

from app.collectors.gate_collector import GateCollector
from app.database.db_service import DatabaseService
from loguru import logger

# Gate.io 特定币种 (在币安没有交易对的币种)
GATE_SYMBOLS = [
    'HYPE/USDT',   # Hyperliquid Token
    # 可以添加更多只在 Gate.io 的币种
]

async def collect_prices_once(collector, db):
    """采集一次价格数据"""
    results = []

    for symbol in GATE_SYMBOLS:
        try:
            # 获取价格
            ticker = await collector.fetch_ticker(symbol)

            if ticker:
                # 保存到 price_data 表
                db.save_price_data(
                    symbol=symbol,
                    exchange='gate',
                    price=ticker['price'],
                    timestamp=ticker['timestamp']
                )

                logger.info(
                    f"✅ {symbol}: ${ticker['price']:,.4f} "
                    f"(24h: {ticker['change_24h']:+.2f}%, "
                    f"Vol: {ticker['volume']:,.2f})"
                )

                results.append({
                    'symbol': symbol,
                    'price': ticker['price'],
                    'success': True
                })
            else:
                logger.warning(f"⚠️  {symbol}: 获取失败")
                results.append({
                    'symbol': symbol,
                    'success': False
                })

        except Exception as e:
            logger.error(f"❌ {symbol} 采集错误: {e}")
            results.append({
                'symbol': symbol,
                'error': str(e),
                'success': False
            })

    return results

async def collect_gate_prices_continuous():
    """持续采集 Gate.io 价格"""

    # 加载配置
    logger.info("加载配置文件...")
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 初始化
    gate_config = config.get('exchanges', {}).get('gate', {})
    if not gate_config.get('enabled', False):
        logger.warning("⚠️  Gate.io 在配置中未启用，请检查 config.yaml")

    collector = GateCollector(gate_config)
    db = DatabaseService(config.get('database', {}))

    logger.info(f"🚀 开始采集 Gate.io 价格")
    logger.info(f"📊 监控币种: {', '.join(GATE_SYMBOLS)}")
    logger.info(f"⏱️  采集间隔: 60秒")
    logger.info("-" * 80)

    cycle = 0
    while True:
        cycle += 1
        start_time = datetime.now()

        logger.info(f"\n{'='*80}")
        logger.info(f"第 {cycle} 轮采集 - {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"{'='*80}")

        # 采集价格
        results = await collect_prices_once(collector, db)

        # 统计
        success_count = sum(1 for r in results if r['success'])
        fail_count = len(results) - success_count

        logger.info(f"\n{'='*80}")
        logger.info(f"本轮采集完成: 成功 {success_count}/{len(results)}, 失败 {fail_count}")
        logger.info(f"{'='*80}")

        # 等待60秒
        logger.info("⏳ 等待60秒...\n")
        await asyncio.sleep(60)

async def main():
    """主函数"""
    print("""
╔════════════════════════════════════════════════════════════════╗
║           Gate.io 价格采集器                                   ║
║                                                                 ║
║  功能: 采集 Gate.io 特定币种价格 (如 HYPE)                     ║
║  间隔: 60秒                                                     ║
║  币种: HYPE/USDT (可在脚本中添加更多)                          ║
╚════════════════════════════════════════════════════════════════╝
    """)

    try:
        await collect_gate_prices_continuous()
    except KeyboardInterrupt:
        logger.info("\n\n👋 收到停止信号，正在退出...")
    except Exception as e:
        logger.error(f"\n\n❌ 采集器异常退出: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(main())
