#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现货交易服务 - Big4底部抄底顶部卖出策略

核心策略:
1. 监听Big4的底部/顶部检测信号
2. 底部时: 扫描所有币种，按跌幅排序，买入跌幅最大的币种（每笔800U）
3. 顶部时: 卖出所有持仓
4. 一次性买入，不分批
"""

import time
import sys
import os
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from loguru import logger
import pymysql
from dotenv import load_dotenv
import yaml

# 导入Big4趋势检测器和WebSocket价格服务
# 添加项目根目录到sys.path
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.services.big4_trend_detector import Big4TrendDetector
from app.services.binance_ws_price import get_ws_price_service

# 加载环境变量
load_dotenv()


class SpotBottomTopTrader:
    """现货底部抄底顶部卖出交易器"""

    def __init__(self):
        # 数据库配置
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', 3306)),
            'user': os.getenv('DB_USER', 'root'),
            'password': os.getenv('DB_PASSWORD', ''),
            'database': os.getenv('DB_NAME', 'binance-data')
        }

        # Big4趋势检测器
        self.big4_detector = Big4TrendDetector()

        # WebSocket 现货价格服务
        self.ws_price_service = get_ws_price_service(market_type='spot')

        # 加载交易对列表
        self.symbols = self._load_symbols_from_config()

        # 交易配置
        self.AMOUNT_PER_TRADE = 800  # 每笔800 USDT
        self.MAX_POSITIONS = 30      # 最多30个持仓
        self.TAKE_PROFIT_PCT = 0.50  # 50% 止盈（备用）
        self.STOP_LOSS_PCT = 0.10    # 10% 止损（防极端情况）
        self.MIN_DROP_PCT = 3.0      # 最小跌幅3%才考虑买入

        # 状态追踪
        self.last_bottom_detected_at = None
        self.last_top_detected_at = None
        self.in_bottom_window = False

        logger.info("=" * 80)
        logger.info("🚀 现货底部抄底顶部卖出交易服务启动")
        logger.info(f"每笔金额: {self.AMOUNT_PER_TRADE} USDT")
        logger.info(f"最大持仓: {self.MAX_POSITIONS} 个")
        logger.info(f"止盈: {self.TAKE_PROFIT_PCT*100:.0f}%, 止损: {self.STOP_LOSS_PCT*100:.0f}%")
        logger.info(f"监控币种: {len(self.symbols)} 个")
        logger.info("=" * 80)

    def _load_symbols_from_config(self) -> List[str]:
        """从config.yaml加载交易对列表"""
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
                symbols = config.get('symbols', [])
            logger.info(f"✅ 从配置文件加载 {len(symbols)} 个交易对")
            return symbols
        except Exception as e:
            logger.error(f"加载配置失败: {e}")
            return []

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config['port'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def get_current_positions(self) -> List[Dict]:
        """获取当前持仓"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM paper_trading_positions
                WHERE status = 'open' AND account_id = 1
                ORDER BY created_at DESC
            """)

            positions = cursor.fetchall()
            cursor.close()
            conn.close()

            return positions if positions else []
        except Exception as e:
            logger.error(f"获取持仓失败: {e}")
            return []

    def scan_drop_opportunities(self) -> List[Dict]:
        """
        扫描所有币种，找出跌幅最大的币种

        返回: [(symbol, drop_pct, current_price), ...]
        """
        opportunities = []

        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 查询所有币种的24H数据
            for symbol in self.symbols:
                binance_symbol = symbol.replace('/', '')

                cursor.execute("""
                    SELECT change_24h, quote_volume_24h
                    FROM price_stats_24h
                    WHERE symbol = %s
                """, (binance_symbol,))

                result = cursor.fetchone()
                if not result:
                    continue

                change_24h = float(result['change_24h'] or 0)
                volume_24h = float(result['quote_volume_24h'] or 0)

                # 只考虑下跌的币种
                if change_24h < -self.MIN_DROP_PCT:
                    # 流动性过滤（成交额至少100万）
                    if volume_24h >= 1_000_000:
                        current_price = self.ws_price_service.get_price(symbol)
                        if current_price:
                            opportunities.append({
                                'symbol': symbol,
                                'drop_pct': abs(change_24h),
                                'change_24h': change_24h,
                                'current_price': current_price,
                                'volume_24h': volume_24h
                            })

            cursor.close()
            conn.close()

            # 按跌幅降序排序
            opportunities.sort(key=lambda x: x['drop_pct'], reverse=True)

            return opportunities

        except Exception as e:
            logger.error(f"扫描跌幅机会失败: {e}")
            return []

    def execute_bottom_buy(self):
        """
        执行底部抄底买入

        逻辑:
        1. 扫描所有币种，按跌幅排序
        2. 选择跌幅最大的前N个币种
        3. 每个币种买入800 USDT
        4. 一次性买入，不分批
        """
        # 检查当前持仓数
        current_positions = self.get_current_positions()
        current_symbols = {pos['symbol'] for pos in current_positions}
        available_slots = self.MAX_POSITIONS - len(current_positions)

        if available_slots <= 0:
            logger.info(f"⏸️  已达最大持仓数 ({len(current_positions)}/{self.MAX_POSITIONS})")
            return

        logger.info(f"📊 可用仓位: {available_slots} 个")

        # 扫描跌幅机会
        opportunities = self.scan_drop_opportunities()

        if not opportunities:
            logger.info("💤 未发现跌幅机会（跌幅<3%或流动性不足）")
            return

        # 显示前10个机会
        logger.info(f"📉 发现 {len(opportunities)} 个下跌币种，显示前10:")
        for i, opp in enumerate(opportunities[:10], 1):
            logger.info(f"  {i:2d}. {opp['symbol']:12} 跌幅:{opp['drop_pct']:5.2f}% 价格:{opp['current_price']:.6f} 量:{opp['volume_24h']/1e6:.1f}M")

        # 选择跌幅最大且未持仓的币种
        bought_count = 0
        for opp in opportunities:
            if bought_count >= available_slots:
                break

            symbol = opp['symbol']
            if symbol in current_symbols:
                logger.info(f"  ⏭️  {symbol} 已持仓，跳过")
                continue

            # 执行买入
            success = self._execute_spot_buy(
                symbol=symbol,
                price=opp['current_price'],
                amount=self.AMOUNT_PER_TRADE,
                drop_pct=opp['drop_pct']
            )

            if success:
                bought_count += 1
                current_symbols.add(symbol)

        if bought_count > 0:
            logger.success(f"✅ 本轮抄底买入 {bought_count} 个币种")
        else:
            logger.info("💤 本轮未买入新币种")

    def _get_latest_price_from_db(self, symbol: str) -> Optional[float]:
        """
        从数据库获取最新价格（作为WebSocket价格的备用）
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            binance_symbol = symbol.replace('/', '')

            cursor.execute("""
                SELECT close_price
                FROM kline_data
                WHERE symbol = %s AND timeframe = '1m' AND exchange = 'binance'
                ORDER BY open_time DESC
                LIMIT 1
            """, (binance_symbol,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if result:
                return float(result['close_price'])
            return None

        except Exception as e:
            logger.error(f"从数据库获取价格失败 {symbol}: {e}")
            return None

    def _execute_spot_buy(self, symbol: str, price: float, amount: float, drop_pct: float) -> bool:
        """
        执行现货买入

        Args:
            symbol: 交易对
            price: 买入价格
            amount: 买入金额（USDT）
            drop_pct: 跌幅百分比
        """
        try:
            quantity = amount / price

            conn = self._get_connection()
            cursor = conn.cursor()

            # 计算止盈止损价格
            take_profit_price = price * (1 + self.TAKE_PROFIT_PCT)
            stop_loss_price = price * (1 - self.STOP_LOSS_PCT)

            cursor.execute("""
                INSERT INTO paper_trading_positions (
                    account_id, symbol, position_side, quantity, available_quantity,
                    avg_entry_price, total_cost,
                    take_profit_price, stop_loss_price,
                    status, created_at, updated_at
                ) VALUES (
                    1, %s, 'LONG', %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW()
                )
            """, (
                symbol,
                quantity,
                quantity,  # available_quantity = quantity
                price,  # avg_entry_price
                amount,  # total_cost
                take_profit_price,
                stop_loss_price
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 买入: {symbol} @ {price:.6f}, 金额: {amount:.0f} USDT, 数量: {quantity:.2f}, 跌幅: {drop_pct:.2f}%")
            return True

        except Exception as e:
            logger.error(f"买入失败 {symbol}: {e}")
            return False

    def execute_top_sell(self):
        """
        执行顶部卖出

        逻辑:
        卖出所有持仓（强制全部卖出，不允许跳过）
        """
        positions = self.get_current_positions()

        if not positions:
            logger.info("💼 当前无持仓，无需卖出")
            return

        logger.info(f"🔴 Big4触顶信号 - 强制卖出所有持仓 ({len(positions)}个)")

        sold_count = 0
        failed_symbols = []

        for pos in positions:
            symbol = pos['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            # 如果WebSocket价格缺失，尝试从数据库获取最新价格
            if not current_price:
                logger.warning(f"⚠️  {symbol} WebSocket价格缺失，尝试从数据库获取...")
                current_price = self._get_latest_price_from_db(symbol)

            # 如果仍然获取不到价格，使用入场价作为兜底
            if not current_price:
                logger.error(f"❌ {symbol} 无法获取价格，使用入场价强制卖出")
                current_price = float(pos['entry_price'])

            success = self._execute_spot_sell(pos, current_price, "Big4顶部信号")
            if success:
                sold_count += 1
            else:
                failed_symbols.append(symbol)

        if sold_count > 0:
            logger.success(f"✅ 顶部卖出 {sold_count}/{len(positions)} 个币种")
        if failed_symbols:
            logger.error(f"❌ 卖出失败的币种: {', '.join(failed_symbols)}")

    def _execute_spot_sell(self, position: Dict, exit_price: float, reason: str) -> bool:
        """
        执行现货卖出
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            entry_price = float(position['entry_price'])
            quantity = float(position['quantity'])
            total_cost = float(position['total_cost'])

            # 计算盈亏
            exit_value = exit_price * quantity
            pnl = exit_value - total_cost
            pnl_pct = (exit_price - entry_price) / entry_price

            cursor.execute("""
                UPDATE paper_trading_positions
                SET status = 'closed',
                    current_price = %s,
                    unrealized_pnl = %s,
                    unrealized_pnl_pct = %s,
                    closed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
            """, (
                exit_price,
                pnl,
                pnl_pct,
                position['id']
            ))

            conn.commit()
            cursor.close()
            conn.close()

            profit_emoji = "📈" if pnl > 0 else "📉"
            logger.info(f"{profit_emoji} 卖出: {position['symbol']} @ {exit_price:.6f}, 盈亏: {pnl:.2f} USDT ({pnl_pct*100:.2f}%), 原因: {reason}")
            return True

        except Exception as e:
            logger.error(f"卖出失败 {position['symbol']}: {e}")
            return False

    def check_stop_profit_loss(self):
        """
        检查止盈止损（备用逻辑）

        主要在Big4信号之外提供保护
        """
        positions = self.get_current_positions()

        if not positions:
            return

        for pos in positions:
            symbol = pos['symbol']
            current_price = self.ws_price_service.get_price(symbol)

            if not current_price:
                continue

            entry_price = float(pos['entry_price'])
            take_profit = float(pos['take_profit_price'])
            stop_loss = float(pos['stop_loss_price'])

            # 止盈
            if current_price >= take_profit:
                profit_pct = (current_price - entry_price) / entry_price * 100
                logger.info(f"🎯 触发止盈: {symbol} @ {current_price:.6f} (目标: {take_profit:.6f}, +{profit_pct:.1f}%)")
                self._execute_spot_sell(pos, current_price, f'止盈{profit_pct:.1f}%')

            # 止损
            elif current_price <= stop_loss:
                loss_pct = (current_price - entry_price) / entry_price * 100
                logger.warning(f"🛑 触发止损: {symbol} @ {current_price:.6f} (止损: {stop_loss:.6f}, {loss_pct:.1f}%)")
                self._execute_spot_sell(pos, current_price, f'止损{loss_pct:.1f}%')

    async def run_forever(self):
        """主循环"""
        # 启动 WebSocket 价格服务
        asyncio.create_task(self.ws_price_service.start(self.symbols))

        # 等待价格数据准备
        await asyncio.sleep(5)

        logger.info("✅ WebSocket 价格服务已启动")
        logger.info("📈 监听Big4底部/顶部信号中...")

        cycle = 0
        while True:
            try:
                cycle += 1
                logger.info(f"\n{'='*80}")
                logger.info(f"📊 现货交易周期 #{cycle} - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
                logger.info(f"{'='*80}")

                # 1. 检测Big4信号
                big4_result = self.big4_detector.detect_market_trend()
                emergency = big4_result.get('emergency_intervention', {})

                bottom_detected = emergency.get('bottom_detected', False)
                top_detected = emergency.get('top_detected', False)

                logger.info(f"Big4状态: {big4_result['overall_signal']} | 强度: {big4_result['signal_strength']:.1f}")
                logger.info(f"紧急干预: 底部={bottom_detected}, 顶部={top_detected}")

                # 2. 底部检测 - 执行抄底
                if bottom_detected:
                    # 检查是否是新的底部信号
                    if self.last_bottom_detected_at is None or \
                       (datetime.now() - self.last_bottom_detected_at).total_seconds() > 3600:  # 1小时内不重复触发
                        logger.success("🟢 检测到Big4底部信号 - 开始抄底")
                        self.execute_bottom_buy()
                        self.last_bottom_detected_at = datetime.now()
                    else:
                        logger.info("⏸️  底部信号已在1小时内触发过，跳过")

                # 3. 顶部检测 - 执行卖出
                if top_detected:
                    # 检查是否是新的顶部信号
                    if self.last_top_detected_at is None or \
                       (datetime.now() - self.last_top_detected_at).total_seconds() > 3600:  # 1小时内不重复触发
                        logger.success("🔴 检测到Big4顶部信号 - 卖出所有持仓")
                        self.execute_top_sell()
                        self.last_top_detected_at = datetime.now()
                    else:
                        logger.info("⏸️  顶部信号已在1小时内触发过，跳过")

                # 4. 备用止盈止损检查
                self.check_stop_profit_loss()

                # 5. 显示当前持仓
                positions = self.get_current_positions()
                if positions:
                    logger.info(f"\n💼 当前持仓 ({len(positions)}个):")
                    for i, pos in enumerate(positions, 1):
                        symbol = pos['symbol']
                        entry_price = float(pos['entry_price'])
                        current_price = self.ws_price_service.get_price(symbol)

                        if current_price:
                            pnl_pct = (current_price - entry_price) / entry_price * 100
                            pnl_emoji = "📈" if pnl_pct > 0 else "📉"
                            logger.info(f"  {i:2d}. {symbol:12} 入:{entry_price:.6f} 现:{current_price:.6f} {pnl_emoji}{pnl_pct:+6.2f}%")
                else:
                    logger.info("\n💼 当前无持仓")

                # 6. 等待下一个周期 (5分钟)
                logger.info("⏳ 等待5分钟...")
                await asyncio.sleep(300)

            except Exception as e:
                logger.error(f"主循环异常: {e}")
                import traceback
                logger.error(traceback.format_exc())
                await asyncio.sleep(60)


def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("🌟 现货底部抄底顶部卖出交易服务")
    logger.info("=" * 80)

    service = SpotBottomTopTrader()
    asyncio.run(service.run_forever())


if __name__ == "__main__":
    main()
