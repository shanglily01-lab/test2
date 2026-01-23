#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
波动率配置更新器 - 问题4优化
基于15M K线统计,动态设置止盈参数
LONG使用阳线波动, SHORT使用阴线波动
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta
from loguru import logger
import pymysql
from .optimization_config import OptimizationConfig


class VolatilityProfileUpdater:
    """波动率配置更新器 - 基于15M K线动态止盈"""

    def __init__(self, db_config: dict):
        """
        初始化波动率更新器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None
        self.opt_config = OptimizationConfig(db_config)

        logger.info("✅ 波动率配置更新器已初始化")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                **self.db_config,
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        else:
            try:
                self.connection.ping(reconnect=True)
            except:
                self.connection = pymysql.connect(
                    **self.db_config,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
        return self.connection

    def analyze_candle_volatility(self, symbol: str, direction: str) -> Optional[Dict]:
        """
        分析K线波动率

        Args:
            symbol: 交易对符号
            direction: 方向 (LONG/SHORT)

        Returns:
            波动率统计字典
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        # 获取配置
        tp_config = self.opt_config.get_take_profit_config()
        candle_count = tp_config['candle_count']  # 分析20根
        select_count = tp_config['select_count']  # 选择10根

        # 查询最近N根15M K线
        cursor.execute("""
            SELECT open_price, high_price, low_price, close_price, volume
            FROM kline_data
            WHERE symbol = %s AND timeframe = '15m'
            ORDER BY open_time DESC
            LIMIT %s
        """, (symbol, candle_count))

        candles = cursor.fetchall()
        cursor.close()

        if not candles or len(candles) < candle_count:
            logger.warning(f"{symbol} 15M K线数据不足 (需要{candle_count}根, 实际{len(candles)}根)")
            return None

        # 根据方向选择阳线或阴线
        selected_candles = []
        for candle in candles:
            open_price = float(candle['open_price'])
            close_price = float(candle['close_price'])
            high_price = float(candle['high_price'])
            low_price = float(candle['low_price'])

            # LONG方向: 选择阳线 (close > open)
            if direction == 'LONG':
                if close_price > open_price:
                    range_pct = ((high_price - low_price) / open_price) * 100
                    selected_candles.append(range_pct)

            # SHORT方向: 选择阴线 (close < open)
            else:  # SHORT
                if close_price < open_price:
                    range_pct = ((high_price - low_price) / open_price) * 100
                    selected_candles.append(range_pct)

        # 如果选中的K线不足,返回None
        if len(selected_candles) < select_count:
            logger.warning(f"{symbol} {direction}方向K线不足 "
                          f"(需要{select_count}根, 实际{len(selected_candles)}根)")
            return None

        # 取最大的N根
        selected_candles = sorted(selected_candles, reverse=True)[:select_count]

        # 计算平均波动率
        avg_range_pct = sum(selected_candles) / len(selected_candles)

        return {
            'symbol': symbol,
            'direction': direction,
            'avg_range_pct': avg_range_pct,
            'candles_analyzed': len(selected_candles),
            'max_range_pct': max(selected_candles),
            'min_range_pct': min(selected_candles)
        }

    def update_symbol_volatility_profile(self, symbol: str) -> Dict:
        """
        更新单个交易对的波动率配置

        Args:
            symbol: 交易对符号

        Returns:
            更新结果
        """
        results = {
            'symbol': symbol,
            'long_updated': False,
            'short_updated': False,
            'long_data': None,
            'short_data': None
        }

        # 分析LONG方向 (阳线)
        long_stats = self.analyze_candle_volatility(symbol, 'LONG')
        if long_stats:
            self.opt_config.update_symbol_volatility_profile(
                symbol, 'LONG',
                long_stats['avg_range_pct'],
                long_stats['candles_analyzed']
            )
            results['long_updated'] = True
            results['long_data'] = long_stats

        # 分析SHORT方向 (阴线)
        short_stats = self.analyze_candle_volatility(symbol, 'SHORT')
        if short_stats:
            self.opt_config.update_symbol_volatility_profile(
                symbol, 'SHORT',
                short_stats['avg_range_pct'],
                short_stats['candles_analyzed']
            )
            results['short_updated'] = True
            results['short_data'] = short_stats

        return results

    def update_all_symbols_volatility(self, symbols: List[str] = None) -> Dict:
        """
        更新所有交易对的波动率配置

        Args:
            symbols: 交易对列表,默认None则从数据库获取

        Returns:
            更新结果统计
        """
        if symbols is None:
            # 从kline_data获取所有交易对
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT DISTINCT symbol
                FROM kline_data
                WHERE timeframe = '15m'
                AND open_time >= DATE_SUB(NOW(), INTERVAL 1 DAY)
            """)

            symbols = [row['symbol'] for row in cursor.fetchall()]
            cursor.close()

        if not symbols:
            logger.warning("没有需要更新的交易对")
            return {
                'total_symbols': 0,
                'long_updated': 0,
                'short_updated': 0,
                'failed': []
            }

        logger.info(f"🔍 开始更新 {len(symbols)} 个交易对的波动率配置")

        results = {
            'total_symbols': len(symbols),
            'long_updated': 0,
            'short_updated': 0,
            'failed': [],
            'details': []
        }

        for symbol in symbols:
            try:
                symbol_result = self.update_symbol_volatility_profile(symbol)

                if symbol_result['long_updated']:
                    results['long_updated'] += 1
                if symbol_result['short_updated']:
                    results['short_updated'] += 1

                results['details'].append(symbol_result)

            except Exception as e:
                logger.error(f"更新 {symbol} 波动率配置失败: {e}")
                results['failed'].append({'symbol': symbol, 'error': str(e)})

        logger.info(f"✅ 波动率配置更新完成:")
        logger.info(f"   总计: {results['total_symbols']} 个交易对")
        logger.info(f"   LONG更新: {results['long_updated']} 个")
        logger.info(f"   SHORT更新: {results['short_updated']} 个")
        logger.info(f"   失败: {len(results['failed'])} 个")

        return results

    def print_volatility_report(self, results: Dict):
        """打印波动率报告"""
        print("\n" + "=" * 100)
        print("📊 15M K线波动率配置报告")
        print("=" * 100)

        tp_config = self.opt_config.get_take_profit_config()
        fixed_coef = tp_config['fixed_coefficient']
        trailing_coef = tp_config['trailing_coefficient']

        print(f"\n配置: 分析{tp_config['candle_count']}根, 选择{tp_config['select_count']}根")
        print(f"系数: 固定止盈={fixed_coef}, 移动激活={trailing_coef}")

        print("\n成功更新的交易对:")
        for detail in results['details']:
            if detail['long_updated'] or detail['short_updated']:
                print(f"\n  {detail['symbol']}:")

                if detail['long_updated'] and detail['long_data']:
                    data = detail['long_data']
                    fixed_tp = data['avg_range_pct'] * fixed_coef
                    trailing_tp = data['avg_range_pct'] * trailing_coef
                    print(f"    LONG: 平均波动={data['avg_range_pct']:.4f}%, "
                          f"固定止盈={fixed_tp:.4f}%, 移动激活={trailing_tp:.4f}%")

                if detail['short_updated'] and detail['short_data']:
                    data = detail['short_data']
                    fixed_tp = data['avg_range_pct'] * fixed_coef
                    trailing_tp = data['avg_range_pct'] * trailing_coef
                    print(f"    SHORT: 平均波动={data['avg_range_pct']:.4f}%, "
                          f"固定止盈={fixed_tp:.4f}%, 移动激活={trailing_tp:.4f}%")

        if results['failed']:
            print("\n⚠️ 失败的交易对:")
            for fail in results['failed']:
                print(f"  {fail['symbol']}: {fail['error']}")

        print("\n" + "=" * 100)


# 测试代码
if __name__ == '__main__':
    db_config = {
        'host': '13.212.252.171',
        'port': 3306,
        'user': 'admin',
        'password': 'Tonny@1000',
        'database': 'binance-data'
    }

    updater = VolatilityProfileUpdater(db_config)

    # 测试单个交易对
    print("\n=== 测试单个交易对 (BTC/USDT) ===")
    result = updater.update_symbol_volatility_profile('BTC/USDT')
    print(f"LONG更新: {result['long_updated']}, SHORT更新: {result['short_updated']}")
    if result['long_data']:
        print(f"LONG平均波动: {result['long_data']['avg_range_pct']:.4f}%")
    if result['short_data']:
        print(f"SHORT平均波动: {result['short_data']['avg_range_pct']:.4f}%")

    # 测试全量更新
    print("\n=== 测试全量更新 ===")
    results = updater.update_all_symbols_volatility()
    updater.print_volatility_report(results)
