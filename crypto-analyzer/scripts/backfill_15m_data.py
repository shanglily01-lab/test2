"""
回填最近5小时的 15分钟 K线数据
Backfill recent 5 hours of 15m kline data

用途：
- 为 EMA 信号监控提供足够的历史数据（需要至少31条）
- 从 Binance 和 Gate.io 采集数据
- 采集最近 5 小时 = 20 条 15m K线数据
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
import pandas as pd
from loguru import logger
from app.collectors.gate_collector import GateCollector
from app.database.db_service import DatabaseService
from sqlalchemy import text


async def backfill_15m_data():
    """回填最近5小时的15分钟K线数据"""

    logger.info("=" * 80)
    logger.info("🔄 回填最近5小时的 15分钟 K线数据")
    logger.info("=" * 80 + "\n")

    # 加载配置
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # 获取监控币种
    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
    logger.info(f"📊 监控币种: {len(symbols)} 个")
    for symbol in symbols:
        logger.info(f"   - {symbol}")

    logger.info(f"\n⏰ 时间范围: 最近 5 小时")
    logger.info(f"📈 时间周期: 15m")
    logger.info(f"🏦 交易所: Binance, Gate.io")
    logger.info(f"📦 预计采集: {len(symbols)} 币种 × 20 条 = {len(symbols) * 20} 条数据\n")

    # 初始化数据库服务
    db_config = config.get('database', {})
    db_service = DatabaseService(db_config)
    logger.info("✅ 数据库服务初始化成功\n")

    # 初始化采集器
    from app.collectors.price_collector import PriceCollector
    binance_collector = PriceCollector(exchange_id='binance', config=config)
    gate_collector = GateCollector(config)

    logger.info("=" * 80)
    logger.info("开始采集数据...")
    logger.info("=" * 80 + "\n")

    total_success = 0
    total_failed = 0

    for symbol in symbols:
        logger.info(f"📊 处理 {symbol}...")

        # 清理格式
        symbol_clean = symbol.replace('/', '')

        try:
            # 1. 尝试从 Binance 采集
            klines_data = None
            used_exchange = None

            try:
                klines_data = await binance_collector.fetch_ohlcv(
                    symbol=symbol,
                    timeframe='15m',
                    limit=20  # 最近20条（5小时）
                )
                if klines_data is not None and len(klines_data) > 0:
                    used_exchange = 'binance'
                    logger.info(f"   ✓ Binance: 获取 {len(klines_data)} 条数据")
            except Exception as e:
                logger.debug(f"   Binance 失败: {e}")

            # 2. 如果 Binance 失败，尝试 Gate.io
            if klines_data is None or len(klines_data) == 0:
                try:
                    klines_data = await gate_collector.fetch_ohlcv(
                        symbol=symbol,
                        timeframe='15m',
                        limit=20
                    )
                    if klines_data is not None and len(klines_data) > 0:
                        used_exchange = 'gate'
                        logger.info(f"   ✓ Gate.io: 获取 {len(klines_data)} 条数据")
                except Exception as e:
                    logger.debug(f"   Gate.io 失败: {e}")

            # 3. 保存到数据库
            if klines_data is not None and len(klines_data) > 0:
                session = db_service.get_session()
                try:
                    saved_count = 0

                    for _, row in klines_data.iterrows():
                        # 将 pandas Timestamp 转换为毫秒时间戳
                        if isinstance(row['timestamp'], pd.Timestamp):
                            timestamp_ms = int(row['timestamp'].timestamp() * 1000)
                        else:
                            timestamp_ms = int(row['timestamp'])

                        # 检查是否已存在
                        check_query = text("""
                            SELECT id FROM kline_data
                            WHERE symbol = :symbol
                            AND exchange = :exchange
                            AND timeframe = :timeframe
                            AND open_time = :open_time
                        """)

                        existing = session.execute(check_query, {
                            'symbol': symbol,
                            'exchange': used_exchange,
                            'timeframe': '15m',
                            'open_time': timestamp_ms
                        }).fetchone()

                        if existing:
                            continue  # 已存在，跳过

                        # 插入新数据
                        insert_query = text("""
                            INSERT INTO kline_data (
                                symbol, exchange, timeframe,
                                open_time, close_time, timestamp,
                                open_price, high_price, low_price, close_price,
                                volume, quote_volume, number_of_trades,
                                taker_buy_base_volume, taker_buy_quote_volume,
                                created_at
                            ) VALUES (
                                :symbol, :exchange, :timeframe,
                                :open_time, :close_time, :timestamp,
                                :open_price, :high_price, :low_price, :close_price,
                                :volume, :quote_volume, :number_of_trades,
                                :taker_buy_base_volume, :taker_buy_quote_volume,
                                NOW()
                            )
                        """)

                        session.execute(insert_query, {
                            'symbol': symbol,
                            'exchange': used_exchange,
                            'timeframe': '15m',
                            'open_time': timestamp_ms,
                            'close_time': timestamp_ms + 15 * 60 * 1000,  # +15分钟
                            'timestamp': datetime.fromtimestamp(timestamp_ms / 1000),
                            'open_price': float(row['open']),
                            'high_price': float(row['high']),
                            'low_price': float(row['low']),
                            'close_price': float(row['close']),
                            'volume': float(row['volume']),
                            'quote_volume': float(row.get('quote_volume', 0)),
                            'number_of_trades': 0,
                            'taker_buy_base_volume': 0,
                            'taker_buy_quote_volume': 0
                        })
                        saved_count += 1

                    session.commit()

                    if saved_count > 0:
                        logger.info(f"   ✅ 保存 {saved_count} 条新数据到数据库")
                        total_success += saved_count
                    else:
                        logger.info(f"   ℹ️  数据已存在，无需保存")

                except Exception as e:
                    session.rollback()
                    logger.error(f"   ❌ 保存数据库失败: {e}")
                    total_failed += 1
                finally:
                    session.close()
            else:
                logger.warning(f"   ⚠️  未能获取数据")
                total_failed += 1

            # 短暂延迟，避免API限流
            await asyncio.sleep(0.5)

        except Exception as e:
            logger.error(f"   ❌ 处理失败: {e}")
            total_failed += 1
            import traceback
            traceback.print_exc()

    # 总结
    logger.info("\n" + "=" * 80)
    logger.info("📊 数据回填完成")
    logger.info("=" * 80 + "\n")

    logger.info(f"✅ 成功保存: {total_success} 条数据")
    logger.info(f"❌ 失败: {total_failed} 个币种")

    # 验证数据
    logger.info("\n" + "=" * 80)
    logger.info("🔍 数据验证")
    logger.info("=" * 80 + "\n")

    session = db_service.get_session()
    try:
        verify_query = text("""
            SELECT symbol, COUNT(*) as count
            FROM kline_data
            WHERE timeframe = '15m'
            GROUP BY symbol
            ORDER BY count DESC
        """)

        results = session.execute(verify_query).fetchall()

        logger.info(f"{'币种':<15} {'15m数据量':<12} {'状态'}")
        logger.info("-" * 50)

        for row in results:
            symbol = row.symbol
            count = row.count

            if count >= 31:
                status = "✅ 充足"
            elif count >= 20:
                status = "⚠️  接近"
            else:
                status = "❌ 不足"

            logger.info(f"{symbol:<15} {count:<12} {status}")

    finally:
        session.close()

    logger.info("\n" + "=" * 80)
    logger.info("✅ 回填完成！现在可以运行 EMA 信号监控了")
    logger.info("=" * 80 + "\n")

    logger.info("💡 下一步:")
    logger.info("   1. 运行测试: python test_ema_signal.py")
    logger.info("   2. 启动监控: python app/scheduler.py")
    logger.info("")


if __name__ == "__main__":
    try:
        asyncio.run(backfill_15m_data())
    except KeyboardInterrupt:
        logger.info("\n\n⚠️  用户中断")
    except Exception as e:
        logger.error(f"\n\n❌ 回填失败: {e}")
        import traceback
        traceback.print_exc()
