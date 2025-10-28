"""
诊断EMA信号 - 检查为什么没有信号产生
Diagnose EMA Signals - Check why no signals are generated
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import yaml
import pandas as pd
from loguru import logger
from app.database.db_service import DatabaseService
from sqlalchemy import text


async def diagnose_ema_signals():
    """诊断EMA信号状态"""

    logger.info("=" * 80)
    logger.info("🔍 诊断 EMA 信号监控")
    logger.info("=" * 80 + "\n")

    # 加载配置
    config_path = project_root / "config.yaml"
    with open(config_path, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)

    # EMA 配置
    ema_config = config.get('ema_signal', {})
    short_period = ema_config.get('short_period', 9)
    long_period = ema_config.get('long_period', 21)
    timeframe = ema_config.get('timeframe', '15m')
    volume_threshold = ema_config.get('volume_threshold', 1.5)

    logger.info(f"📊 EMA 配置:")
    logger.info(f"   - 短期 EMA: {short_period}")
    logger.info(f"   - 长期 EMA: {long_period}")
    logger.info(f"   - 时间周期: {timeframe}")
    logger.info(f"   - 成交量阈值: {volume_threshold}x")
    logger.info("")

    # 初始化数据库
    db_config = config.get('database', {})
    db_service = DatabaseService(db_config)

    # 获取监控币种
    symbols = config.get('symbols', ['BTC/USDT', 'ETH/USDT'])
    logger.info(f"🎯 监控币种: {len(symbols)} 个\n")

    # 检查每个币种
    for symbol in symbols:
        logger.info(f"{'='*80}")
        logger.info(f"📊 {symbol}")
        logger.info(f"{'='*80}")

        session = db_service.get_session()
        try:
            # 获取最近的K线数据
            query = text("""
                SELECT
                    open_time,
                    timestamp,
                    open_price,
                    high_price,
                    low_price,
                    close_price,
                    volume
                FROM kline_data
                WHERE symbol = :symbol
                AND timeframe = :timeframe
                ORDER BY open_time DESC
                LIMIT :limit
            """)

            result = session.execute(query, {
                'symbol': symbol,
                'timeframe': timeframe,
                'limit': max(long_period + 10, 50)  # 获取足够的数据
            })

            rows = result.fetchall()

            if not rows:
                logger.warning(f"   ❌ 无数据\n")
                continue

            logger.info(f"   ✅ 数据条数: {len(rows)}")

            # 转换为DataFrame（需要反转顺序，因为查询是DESC）
            df = pd.DataFrame([
                {
                    'timestamp': row[1],
                    'open': float(row[2]),
                    'high': float(row[3]),
                    'low': float(row[4]),
                    'close': float(row[5]),
                    'volume': float(row[6])
                }
                for row in reversed(rows)
            ])

            if len(df) < long_period:
                logger.warning(f"   ⚠️  数据不足: {len(df)} < {long_period}\n")
                continue

            # 计算EMA
            short_ema = df['close'].ewm(span=short_period, adjust=False).mean()
            long_ema = df['close'].ewm(span=long_period, adjust=False).mean()

            # 获取最近的值
            current_close = df['close'].iloc[-1]
            current_short_ema = short_ema.iloc[-1]
            current_long_ema = long_ema.iloc[-1]

            prev_short_ema = short_ema.iloc[-2]
            prev_long_ema = long_ema.iloc[-2]

            # 计算EMA距离
            ema_distance = abs(current_short_ema - current_long_ema)
            ema_distance_pct = (ema_distance / current_long_ema) * 100

            # 计算成交量比率
            avg_volume = df['volume'].iloc[-20:-1].mean() if len(df) > 20 else df['volume'].mean()
            current_volume = df['volume'].iloc[-1]
            volume_ratio = current_volume / avg_volume if avg_volume > 0 else 0

            # 显示当前状态
            logger.info(f"\n   📈 当前状态:")
            logger.info(f"      价格: ${current_close:.4f}")
            logger.info(f"      EMA{short_period}: ${current_short_ema:.4f}")
            logger.info(f"      EMA{long_period}: ${current_long_ema:.4f}")
            logger.info(f"      EMA距离: {ema_distance_pct:.2f}%")

            # EMA趋势
            if current_short_ema > current_long_ema:
                logger.info(f"      EMA状态: 🟢 多头排列 (EMA{short_period} > EMA{long_period})")
            else:
                logger.info(f"      EMA状态: 🔴 空头排列 (EMA{short_period} < EMA{long_period})")

            # 检查是否有金叉
            is_golden_cross = (
                prev_short_ema <= prev_long_ema and
                current_short_ema > current_long_ema
            )

            # 检查是否有死叉
            is_death_cross = (
                prev_short_ema >= prev_long_ema and
                current_short_ema < current_long_ema
            )

            logger.info(f"\n   📊 成交量:")
            logger.info(f"      当前成交量: {current_volume:.2f}")
            logger.info(f"      平均成交量: {avg_volume:.2f}")
            logger.info(f"      成交量比率: {volume_ratio:.2f}x")

            if volume_ratio >= volume_threshold:
                logger.info(f"      ✅ 成交量满足阈值 (>= {volume_threshold}x)")
            else:
                logger.info(f"      ❌ 成交量不足 (< {volume_threshold}x)")

            logger.info(f"\n   🎯 信号检测:")

            if is_golden_cross:
                logger.info(f"      🟡 检测到 EMA 金叉!")
                logger.info(f"         前一根: EMA{short_period}={prev_short_ema:.4f} <= EMA{long_period}={prev_long_ema:.4f}")
                logger.info(f"         当前根: EMA{short_period}={current_short_ema:.4f} > EMA{long_period}={current_long_ema:.4f}")

                if volume_ratio >= volume_threshold:
                    logger.info(f"      ✅ 成交量确认 - 这是一个有效的买入信号!")
                else:
                    logger.info(f"      ⚠️  成交量不足 - 信号未触发 (需要 >= {volume_threshold}x)")

            elif is_death_cross:
                logger.info(f"      🔵 检测到 EMA 死叉")
                logger.info(f"         前一根: EMA{short_period}={prev_short_ema:.4f} >= EMA{long_period}={prev_long_ema:.4f}")
                logger.info(f"         当前根: EMA{short_period}={current_short_ema:.4f} < EMA{long_period}={current_long_ema:.4f}")
                logger.info(f"      ℹ️  死叉不产生信号（系统仅监控金叉买入信号）")

            else:
                logger.info(f"      ⭕ 无交叉信号")

                # 判断距离金叉还有多远
                if current_short_ema < current_long_ema:
                    distance_to_cross = ((current_long_ema - current_short_ema) / current_long_ema) * 100
                    logger.info(f"      ℹ️  空头排列中，距离金叉还需上涨 {distance_to_cross:.2f}%")
                else:
                    logger.info(f"      ℹ️  多头排列中，等待回调后的下一次金叉")

            # 显示最近5根K线的EMA变化趋势
            logger.info(f"\n   📉 最近5根K线 EMA 趋势:")
            logger.info(f"      {'时间':<20} {'收盘价':<10} {'EMA9':<10} {'EMA21':<10} {'状态'}")
            logger.info(f"      {'-'*70}")

            for i in range(max(0, len(df)-5), len(df)):
                ts = df['timestamp'].iloc[i]
                close = df['close'].iloc[i]
                s_ema = short_ema.iloc[i]
                l_ema = long_ema.iloc[i]
                status = "🟢多头" if s_ema > l_ema else "🔴空头"

                logger.info(f"      {str(ts):<20} {close:<10.4f} {s_ema:<10.4f} {l_ema:<10.4f} {status}")

            logger.info("")

        except Exception as e:
            logger.error(f"   ❌ 处理失败: {e}")
            import traceback
            traceback.print_exc()

        finally:
            session.close()

    logger.info("\n" + "=" * 80)
    logger.info("🎯 诊断总结")
    logger.info("=" * 80)
    logger.info("\n如果没有信号，可能的原因:")
    logger.info("1. ⭕ 当前没有发生 EMA 金叉（最常见）")
    logger.info("2. ❌ 发生了金叉但成交量不足（< 1.5x 平均成交量）")
    logger.info("3. 🔴 当前处于空头排列，等待价格上涨")
    logger.info("4. 🟢 已经在多头排列中，需要等待回调后的下一次金叉")
    logger.info("\n💡 建议:")
    logger.info("- 如果想测试信号，可以降低 volume_threshold 到 1.0")
    logger.info("- 或者等待市场出现真实的 EMA 金叉信号")
    logger.info("- 持续运行 scheduler 会自动捕捉新的交叉信号")
    logger.info("")


if __name__ == '__main__':
    try:
        asyncio.run(diagnose_ema_signals())
    except KeyboardInterrupt:
        logger.info("\n用户中断")
    except Exception as e:
        logger.error(f"\n❌ 诊断失败: {e}")
        import traceback
        traceback.print_exc()
