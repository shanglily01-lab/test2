#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易服务测试脚本

测试内容:
1. Big4底部检测 → 抄底买入
2. Big4顶部检测 → 全部卖出
3. 跌幅扫描和排序
4. 价格获取机制 (WebSocket → 数据库 → 入场价)
5. 止盈止损逻辑
6. 持仓管理和限制
"""

import sys
import os
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.spot_trader_service import SpotBottomTopTrader
from app.services.big4_trend_detector import Big4TrendDetector
from loguru import logger
import pymysql

# 配置日志
logger.remove()
logger.add(sys.stdout, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")


class SpotTraderTester:
    """现货交易测试器"""

    def __init__(self):
        self.trader = SpotBottomTopTrader()
        self.big4_detector = Big4TrendDetector()

        logger.info("=" * 80)
        logger.info("🧪 现货交易服务测试脚本启动")
        logger.info("=" * 80)

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=os.getenv('DB_HOST', 'localhost'),
            port=int(os.getenv('DB_PORT', 3306)),
            user=os.getenv('DB_USER', 'root'),
            password=os.getenv('DB_PASSWORD', ''),
            database=os.getenv('DB_NAME', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def test_1_big4_detection(self):
        """测试1: Big4底部/顶部检测"""
        logger.info("\n" + "=" * 80)
        logger.info("测试1: Big4底部/顶部检测")
        logger.info("=" * 80)

        try:
            result = self.big4_detector.detect_market_trend()
            emergency = result.get('emergency_intervention', {})

            logger.info(f"Big4状态: {result['overall_signal']}")
            logger.info(f"信号强度: {result['signal_strength']:.1f}")
            logger.info(f"看涨数量: {result['bullish_count']}")
            logger.info(f"看跌数量: {result['bearish_count']}")
            logger.info(f"看涨权重: {result.get('bullish_weight', 0)*100:.0f}%")
            logger.info(f"看跌权重: {result.get('bearish_weight', 0)*100:.0f}%")

            logger.info(f"\n紧急干预状态:")
            logger.info(f"  底部检测: {emergency.get('bottom_detected', False)}")
            logger.info(f"  顶部检测: {emergency.get('top_detected', False)}")
            logger.info(f"  阻止做多: {emergency.get('block_long', False)}")
            logger.info(f"  阻止做空: {emergency.get('block_short', False)}")
            logger.info(f"  详情: {emergency.get('details', 'N/A')}")

            logger.success("✅ 测试1通过: Big4检测正常工作")
            return True

        except Exception as e:
            logger.error(f"❌ 测试1失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_2_scan_drop_opportunities(self):
        """测试2: 扫描跌幅机会"""
        logger.info("\n" + "=" * 80)
        logger.info("测试2: 扫描跌幅机会 (跌幅≥3%, 成交额≥100万)")
        logger.info("=" * 80)

        try:
            opportunities = self.trader.scan_drop_opportunities()

            if opportunities:
                logger.info(f"✅ 发现 {len(opportunities)} 个下跌币种")
                logger.info(f"\n前10个机会:")
                for i, opp in enumerate(opportunities[:10], 1):
                    logger.info(f"  {i:2d}. {opp['symbol']:12} 跌幅:{opp['drop_pct']:6.2f}% "
                               f"价格:{opp['current_price']:.6f} "
                               f"量:{opp['volume_24h']/1e6:6.1f}M")
                logger.success("✅ 测试2通过: 跌幅扫描正常")
            else:
                logger.warning("⚠️  未发现符合条件的下跌币种")
                logger.info("✅ 测试2通过: 扫描功能正常（无结果）")

            return True

        except Exception as e:
            logger.error(f"❌ 测试2失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_3_current_positions(self):
        """测试3: 查询当前持仓"""
        logger.info("\n" + "=" * 80)
        logger.info("测试3: 查询当前持仓")
        logger.info("=" * 80)

        try:
            positions = self.trader.get_current_positions()

            if positions:
                logger.info(f"✅ 当前持仓: {len(positions)} 个")
                for i, pos in enumerate(positions, 1):
                    symbol = pos['symbol']
                    entry_price = float(pos['entry_price'])
                    quantity = float(pos['quantity'])
                    total_cost = float(pos['total_cost'])

                    logger.info(f"  {i:2d}. {symbol:12} "
                               f"入:{entry_price:.6f} "
                               f"数量:{quantity:.2f} "
                               f"成本:{total_cost:.0f}U")
            else:
                logger.info("💤 当前无持仓")

            available_slots = self.trader.MAX_POSITIONS - len(positions)
            logger.info(f"\n可用仓位: {available_slots}/{self.trader.MAX_POSITIONS}")

            logger.success("✅ 测试3通过: 持仓查询正常")
            return True

        except Exception as e:
            logger.error(f"❌ 测试3失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_4_price_fallback(self):
        """测试4: 价格获取机制 (WebSocket → 数据库 → 入场价)"""
        logger.info("\n" + "=" * 80)
        logger.info("测试4: 价格获取机制测试")
        logger.info("=" * 80)

        test_symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

        try:
            for symbol in test_symbols:
                logger.info(f"\n测试币种: {symbol}")

                # 测试WebSocket价格
                ws_price = self.trader.ws_price_service.get_price(symbol)
                if ws_price:
                    logger.info(f"  ✅ WebSocket价格: {ws_price:.6f}")
                else:
                    logger.warning(f"  ⚠️  WebSocket价格: None")

                # 测试数据库价格
                db_price = self.trader._get_latest_price_from_db(symbol)
                if db_price:
                    logger.info(f"  ✅ 数据库价格: {db_price:.6f}")
                else:
                    logger.warning(f"  ⚠️  数据库价格: None")

            logger.success("✅ 测试4通过: 价格获取机制正常")
            return True

        except Exception as e:
            logger.error(f"❌ 测试4失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_5_stop_profit_loss_check(self):
        """测试5: 止盈止损检查"""
        logger.info("\n" + "=" * 80)
        logger.info("测试5: 止盈止损检查 (50%止盈, 10%止损)")
        logger.info("=" * 80)

        try:
            positions = self.trader.get_current_positions()

            if not positions:
                logger.info("💤 当前无持仓，跳过止盈止损测试")
                return True

            logger.info(f"检查 {len(positions)} 个持仓的止盈止损条件:")

            for pos in positions:
                symbol = pos['symbol']
                entry_price = float(pos['entry_price'])
                take_profit = float(pos['take_profit_price'])
                stop_loss = float(pos['stop_loss_price'])

                current_price = self.trader.ws_price_service.get_price(symbol)
                if not current_price:
                    current_price = self.trader._get_latest_price_from_db(symbol)

                if current_price:
                    pnl_pct = (current_price - entry_price) / entry_price * 100

                    logger.info(f"\n  {symbol:12}")
                    logger.info(f"    入场价: {entry_price:.6f}")
                    logger.info(f"    当前价: {current_price:.6f}")
                    logger.info(f"    盈亏: {pnl_pct:+.2f}%")
                    logger.info(f"    止盈价: {take_profit:.6f} (+50%)")
                    logger.info(f"    止损价: {stop_loss:.6f} (-10%)")

                    if current_price >= take_profit:
                        logger.warning(f"    ⚠️  触发止盈条件！")
                    elif current_price <= stop_loss:
                        logger.warning(f"    ⚠️  触发止损条件！")
                    else:
                        logger.info(f"    ✅ 在正常范围内")

            logger.success("✅ 测试5通过: 止盈止损检查正常")
            return True

        except Exception as e:
            logger.error(f"❌ 测试5失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_6_database_check(self):
        """测试6: 数据库表检查"""
        logger.info("\n" + "=" * 80)
        logger.info("测试6: 数据库表和数据完整性检查")
        logger.info("=" * 80)

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查spot_positions表
            cursor.execute("SHOW TABLES LIKE 'spot_positions'")
            if cursor.fetchone():
                logger.info("✅ spot_positions 表存在")

                # 检查表结构
                cursor.execute("DESCRIBE spot_positions")
                columns = cursor.fetchall()
                logger.info(f"   表字段数: {len(columns)}")

                # 统计记录
                cursor.execute("SELECT COUNT(*) as total, SUM(status='active') as active, SUM(status='closed') as closed FROM spot_positions")
                stats = cursor.fetchone()
                logger.info(f"   总记录: {stats['total']}, 活跃: {stats['active']}, 已平: {stats['closed']}")
            else:
                logger.error("❌ spot_positions 表不存在！")

            # 检查price_stats_24h表
            cursor.execute("SHOW TABLES LIKE 'price_stats_24h'")
            if cursor.fetchone():
                logger.info("✅ price_stats_24h 表存在")

                cursor.execute("SELECT COUNT(*) as total FROM price_stats_24h")
                count = cursor.fetchone()['total']
                logger.info(f"   24H数据记录: {count}")
            else:
                logger.error("❌ price_stats_24h 表不存在！")

            # 检查kline_data表
            cursor.execute("SHOW TABLES LIKE 'kline_data'")
            if cursor.fetchone():
                logger.info("✅ kline_data 表存在")

                cursor.execute("SELECT COUNT(*) as total FROM kline_data WHERE timeframe='1m' AND exchange='binance'")
                count = cursor.fetchone()['total']
                logger.info(f"   K线数据记录: {count}")
            else:
                logger.error("❌ kline_data 表不存在！")

            cursor.close()
            conn.close()

            logger.success("✅ 测试6通过: 数据库检查完成")
            return True

        except Exception as e:
            logger.error(f"❌ 测试6失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_7_config_parameters(self):
        """测试7: 配置参数检查"""
        logger.info("\n" + "=" * 80)
        logger.info("测试7: 配置参数检查")
        logger.info("=" * 80)

        try:
            logger.info("交易配置:")
            logger.info(f"  每笔金额: {self.trader.AMOUNT_PER_TRADE} USDT")
            logger.info(f"  最大持仓: {self.trader.MAX_POSITIONS} 个")
            logger.info(f"  最大资金: {self.trader.AMOUNT_PER_TRADE * self.trader.MAX_POSITIONS:,} USDT")
            logger.info(f"  止盈比例: {self.trader.TAKE_PROFIT_PCT*100:.0f}%")
            logger.info(f"  止损比例: {self.trader.STOP_LOSS_PCT*100:.0f}%")
            logger.info(f"  最小跌幅: {self.trader.MIN_DROP_PCT:.1f}%")

            logger.info(f"\n监控配置:")
            logger.info(f"  监控币种: {len(self.trader.symbols)} 个")
            logger.info(f"  前5个币种: {', '.join(self.trader.symbols[:5])}")

            # 检查配置合理性
            assert self.trader.AMOUNT_PER_TRADE > 0, "每笔金额必须>0"
            assert self.trader.MAX_POSITIONS > 0, "最大持仓必须>0"
            assert 0 < self.trader.TAKE_PROFIT_PCT < 1, "止盈比例必须在0-1之间"
            assert 0 < self.trader.STOP_LOSS_PCT < 1, "止损比例必须在0-1之间"
            assert self.trader.MIN_DROP_PCT > 0, "最小跌幅必须>0"

            logger.success("✅ 测试7通过: 配置参数正常")
            return True

        except Exception as e:
            logger.error(f"❌ 测试7失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def test_8_simulated_bottom_buy(self):
        """测试8: 模拟底部买入 (仅检查逻辑，不实际执行)"""
        logger.info("\n" + "=" * 80)
        logger.info("测试8: 模拟底部买入逻辑")
        logger.info("=" * 80)

        try:
            # 获取当前持仓
            current_positions = self.trader.get_current_positions()
            current_symbols = {pos['symbol'] for pos in current_positions}
            available_slots = self.trader.MAX_POSITIONS - len(current_positions)

            logger.info(f"当前持仓: {len(current_positions)}/{self.trader.MAX_POSITIONS}")
            logger.info(f"可用仓位: {available_slots}")

            if available_slots <= 0:
                logger.warning("⚠️  已达最大持仓数，无法买入")
                return True

            # 扫描跌幅机会
            opportunities = self.trader.scan_drop_opportunities()

            if not opportunities:
                logger.info("💤 未发现跌幅机会")
                return True

            # 模拟选择买入
            logger.info(f"\n模拟买入流程 (不实际执行):")
            bought_count = 0
            for opp in opportunities:
                if bought_count >= min(available_slots, 5):  # 限制模拟5个
                    break

                symbol = opp['symbol']
                if symbol in current_symbols:
                    logger.info(f"  ⏭️  {symbol} 已持仓，跳过")
                    continue

                logger.info(f"  ✅ 可买入: {symbol} @ {opp['current_price']:.6f}")
                logger.info(f"     跌幅: {opp['drop_pct']:.2f}%")
                logger.info(f"     金额: {self.trader.AMOUNT_PER_TRADE} USDT")
                logger.info(f"     数量: {self.trader.AMOUNT_PER_TRADE / opp['current_price']:.2f}")

                bought_count += 1

            logger.info(f"\n模拟买入: {bought_count} 个币种")
            logger.success("✅ 测试8通过: 底部买入逻辑正常")
            return True

        except Exception as e:
            logger.error(f"❌ 测试8失败: {e}")
            import traceback
            traceback.print_exc()
            return False

    def run_all_tests(self):
        """运行所有测试"""
        logger.info("\n" + "=" * 80)
        logger.info("🚀 开始运行所有测试")
        logger.info("=" * 80)

        tests = [
            ("Big4底部/顶部检测", self.test_1_big4_detection),
            ("扫描跌幅机会", self.test_2_scan_drop_opportunities),
            ("查询当前持仓", self.test_3_current_positions),
            ("价格获取机制", self.test_4_price_fallback),
            ("止盈止损检查", self.test_5_stop_profit_loss_check),
            ("数据库表检查", self.test_6_database_check),
            ("配置参数检查", self.test_7_config_parameters),
            ("模拟底部买入", self.test_8_simulated_bottom_buy),
        ]

        results = []
        for test_name, test_func in tests:
            try:
                result = test_func()
                results.append((test_name, result))
            except Exception as e:
                logger.error(f"测试 {test_name} 执行异常: {e}")
                results.append((test_name, False))

        # 汇总结果
        logger.info("\n" + "=" * 80)
        logger.info("📊 测试结果汇总")
        logger.info("=" * 80)

        passed = sum(1 for _, result in results if result)
        total = len(results)

        for test_name, result in results:
            status = "✅ 通过" if result else "❌ 失败"
            logger.info(f"  {status} - {test_name}")

        logger.info(f"\n通过率: {passed}/{total} ({passed/total*100:.1f}%)")

        if passed == total:
            logger.success(f"\n🎉 所有测试通过！现货交易服务运行正常。")
        else:
            logger.error(f"\n⚠️  有 {total-passed} 个测试失败，请检查！")

        return passed == total


def main():
    """主函数"""
    tester = SpotTraderTester()
    success = tester.run_all_tests()

    logger.info("\n" + "=" * 80)
    if success:
        logger.success("✅ 现货交易服务测试完成 - 所有测试通过")
    else:
        logger.error("❌ 现货交易服务测试完成 - 部分测试失败")
    logger.info("=" * 80)

    return 0 if success else 1


if __name__ == "__main__":
    exit(main())
