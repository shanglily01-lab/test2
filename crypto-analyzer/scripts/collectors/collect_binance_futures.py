#!/usr/bin/env python3
"""
币安合约数据定时采集脚本
每1分钟采集一次合约数据，包括：
- 实时价格
- K线数据
- 资金费率
- 持仓量
- 多空比率
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import schedule
import time
import yaml
from datetime import datetime
from loguru import logger
from typing import List

from app.collectors.binance_futures_collector import BinanceFuturesCollector
from app.database.db_service import DatabaseService


class BinanceFuturesScheduler:
    """币安合约数据定时采集器"""

    def __init__(self, config_path: str = 'config.yaml'):
        """
        初始化采集器

        Args:
            config_path: 配置文件路径
        """
        # 加载配置
        config_file = project_root / config_path
        with open(config_file, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

        # 获取要监控的币种
        self.symbols = self.config.get('symbols', ['BTC/USDT', 'ETH/USDT'])

        # 初始化合约采集器
        futures_config = self.config.get('exchanges', {}).get('binance', {})
        self.collector = BinanceFuturesCollector(futures_config)
        logger.info(f"币安合约采集器初始化完成，监控 {len(self.symbols)} 个币种")

        # 初始化数据库
        db_config = self.config.get('database', {})
        self.db_service = DatabaseService(db_config)
        logger.info("数据库服务初始化完成")

        # 任务统计
        self.stats = {
            'total_runs': 0,
            'last_run': None,
            'last_error': None,
            'success_count': 0,
            'error_count': 0
        }

    async def collect_futures_data(self):
        """采集合约数据（主任务）"""
        try:
            start_time = datetime.now()
            logger.info(f"[{start_time.strftime('%H:%M:%S')}] 开始采集币安合约数据...")

            collected_count = 0
            error_count = 0

            for symbol in self.symbols:
                try:
                    # 获取所有合约数据
                    data = await self.collector.fetch_all_data(symbol, timeframe='1m')

                    if not data:
                        logger.warning(f"  ⊗ {symbol}: 未获取到数据")
                        error_count += 1
                        continue

                    # 1. 保存ticker数据
                    if data.get('ticker'):
                        ticker = data['ticker']
                        price_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'timestamp': ticker['timestamp'],
                            'price': ticker['price'],
                            'open': ticker['open'],
                            'high': ticker['high'],
                            'low': ticker['low'],
                            'close': ticker['close'],
                            'volume': ticker['volume'],
                            'quote_volume': ticker['quote_volume'],
                            'bid': 0,  # 合约ticker没有bid/ask
                            'ask': 0,
                            'change_24h': ticker['price_change_percent']
                        }
                        self.db_service.save_price_data(price_data)

                    # 2. 保存K线数据
                    if data.get('kline'):
                        kline = data['kline']
                        kline_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'timeframe': '1m',
                            'open_time': int(kline['open_time']),
                            'timestamp': kline['timestamp'],
                            'open': kline['open'],
                            'high': kline['high'],
                            'low': kline['low'],
                            'close': kline['close'],
                            'volume': kline['volume']
                        }
                        self.db_service.save_kline_data(kline_data)

                    # 3. 保存资金费率
                    if data.get('funding_rate'):
                        funding = data['funding_rate']
                        funding_data = {
                            'exchange': 'binance_futures',
                            'symbol': symbol,
                            'funding_rate': funding['funding_rate'],
                            'funding_time': funding['funding_time'],
                            'timestamp': funding['timestamp'],
                            'mark_price': funding['mark_price'],
                            'index_price': funding['index_price'],
                            'next_funding_time': funding['next_funding_time']
                        }
                        self.db_service.save_funding_rate_data(funding_data)

                    # 4. 保存持仓量
                    if data.get('open_interest'):
                        oi = data['open_interest']
                        oi_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'open_interest': oi['open_interest'],
                            'open_interest_value': oi.get('open_interest_value'),
                            'timestamp': oi['timestamp']
                        }
                        self.db_service.save_open_interest_data(oi_data)

                    # 5. 保存多空比
                    if data.get('long_short_ratio'):
                        ls = data['long_short_ratio']
                        ls_data = {
                            'symbol': symbol,
                            'exchange': 'binance_futures',
                            'period': '5m',
                            'long_account': ls['long_account'],
                            'short_account': ls['short_account'],
                            'long_short_ratio': ls['long_short_ratio'],
                            'timestamp': ls['timestamp']
                        }
                        self.db_service.save_long_short_ratio_data(ls_data)

                    # 日志输出
                    price = data['ticker']['price'] if data.get('ticker') else 0
                    funding_rate = data['funding_rate']['funding_rate'] * 100 if data.get('funding_rate') else 0
                    oi = data['open_interest']['open_interest'] if data.get('open_interest') else 0
                    ls_ratio = data['long_short_ratio']['long_short_ratio'] if data.get('long_short_ratio') else 0

                    logger.info(
                        f"  ✓ {symbol}: "
                        f"价格=${price:,.2f}, "
                        f"费率={funding_rate:+.4f}%, "
                        f"持仓={oi:,.0f}, "
                        f"多空比={ls_ratio:.2f}"
                    )

                    collected_count += 1

                    # 延迟避免API限流和网络错误
                    await asyncio.sleep(0.5)  # 增加到0.5秒，提高稳定性

                except Exception as e:
                    logger.error(f"  ✗ {symbol}: {e}")
                    error_count += 1

            # 更新统计
            self.stats['total_runs'] += 1
            self.stats['last_run'] = datetime.now()
            self.stats['success_count'] += collected_count
            self.stats['error_count'] += error_count

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(
                f"  ✓ 采集完成: 成功 {collected_count}/{len(self.symbols)}, "
                f"失败 {error_count}, "
                f"耗时 {elapsed:.1f}秒"
            )

        except Exception as e:
            logger.error(f"采集任务失败: {e}")
            self.stats['last_error'] = str(e)
            self.stats['error_count'] += 1

    async def collect_all_symbols(self):
        """采集所有可用的合约币种（每小时执行一次）"""
        try:
            logger.info("正在获取所有USDT永续合约列表...")
            symbols = await self.collector.get_all_futures_symbols()

            if symbols:
                logger.info(f"发现 {len(symbols)} 个USDT永续合约")
                # 可以将这些币种保存到配置或数据库中
                logger.info(f"前20个: {symbols[:20]}")
            else:
                logger.warning("未获取到合约列表")

        except Exception as e:
            logger.error(f"获取合约列表失败: {e}")

    def print_status(self):
        """打印运行状态"""
        logger.info("\n" + "=" * 80)
        logger.info("币安合约采集器运行状态")
        logger.info("=" * 80)
        logger.info(f"总运行次数: {self.stats['total_runs']}")
        logger.info(f"成功采集: {self.stats['success_count']} 次")
        logger.info(f"失败次数: {self.stats['error_count']} 次")

        if self.stats['last_run']:
            logger.info(f"最后运行: {self.stats['last_run'].strftime('%Y-%m-%d %H:%M:%S')}")

        if self.stats['last_error']:
            logger.info(f"最后错误: {self.stats['last_error'][:100]}")

        logger.info("=" * 80 + "\n")

    def schedule_tasks(self):
        """设置定时任务"""
        logger.info("设置定时任务...")

        # 1. 每1分钟采集一次合约数据
        schedule.every(1).minutes.do(
            lambda: asyncio.run(self.collect_futures_data())
        )
        logger.info("  ✓ 合约数据采集 - 每 1 分钟")

        # 2. 每小时获取一次所有合约列表（可选）
        schedule.every(1).hours.do(
            lambda: asyncio.run(self.collect_all_symbols())
        )
        logger.info("  ✓ 合约列表更新 - 每 1 小时")

        # 3. 每30分钟打印一次状态
        schedule.every(30).minutes.do(self.print_status)
        logger.info("  ✓ 状态报告 - 每 30 分钟")

        logger.info("定时任务设置完成\n")

    async def run_initial_collection(self):
        """首次启动时执行一次采集"""
        logger.info("\n" + "=" * 80)
        logger.info("首次采集开始...")
        logger.info("=" * 80 + "\n")

        await self.collect_futures_data()

        logger.info("\n" + "=" * 80)
        logger.info("首次采集完成")
        logger.info("=" * 80 + "\n")

    def start(self):
        """启动采集器"""
        logger.info("\n" + "=" * 80)
        logger.info("币安合约数据采集器启动")
        logger.info("=" * 80)
        logger.info(f"监控币种: {', '.join(self.symbols)}")
        logger.info(f"采集间隔: 1 分钟")
        logger.info(f"数据库: {self.config.get('database', {}).get('type', 'mysql')}")
        logger.info("=" * 80 + "\n")

        # 设置定时任务
        self.schedule_tasks()

        # 首次采集
        asyncio.run(self.run_initial_collection())

        logger.info("\n采集器已启动，按 Ctrl+C 停止\n")

        # 保持运行
        try:
            while True:
                schedule.run_pending()
                time.sleep(1)

        except KeyboardInterrupt:
            logger.info("\n\n收到停止信号，正在关闭...")
            self.stop()

    def stop(self):
        """停止采集器"""
        logger.info("关闭数据库连接...")
        self.db_service.close()
        logger.info("采集器已停止")


def main():
    """主函数"""
    # 配置日志
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    logger.add(
        log_dir / "binance_futures_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}"
    )

    # 创建并启动采集器
    scheduler = BinanceFuturesScheduler(config_path='config.yaml')
    scheduler.start()


if __name__ == '__main__':
    main()
