#!/usr/bin/env python3
"""
波动率计算器
用于在开仓时一次性计算合适的止损止盈百分比

特点:
1. 基于最近24小时的历史K线数据
2. 区分多空方向的不同风险
3. 计算结果在开仓时固定,持仓期间不变
4. 使用缓存避免重复计算
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Tuple, Optional
import statistics
import mysql.connector

logger = logging.getLogger(__name__)

# 数据库配置
DB_CONFIG = {
    'host': '13.212.252.171',
    'port': 3306,
    'user': 'admin',
    'password': 'Tonny@1000',
    'database': 'binance-data'
}


class VolatilityCalculator:
    """波动率计算器"""

    def __init__(self):
        self.cache = {}  # 缓存计算结果
        self.cache_ttl = 3600  # 缓存1小时(避免频繁查询K线)

    def get_sl_tp_for_position(
        self,
        symbol: str,
        position_side: str,
        entry_score: int = 50,
        signal_components: list = None,
        max_hold_minutes: int = 240
    ) -> Tuple[float, float, str]:
        """
        获取开仓时应该使用的止损止盈百分比

        参数:
            symbol: 交易对 (如 'BTC/USDT')
            position_side: 持仓方向 'LONG' 或 'SHORT'
            entry_score: 入场评分 (用于调整止损宽度)
            signal_components: 信号组件列表
            max_hold_minutes: 最大持仓时间(用于判断是趋势市还是中性市)

        返回:
            (止损百分比, 止盈百分比, 计算原因)

        示例:
            >>> calc = VolatilityCalculator()
            >>> sl, tp, reason = calc.get_sl_tp_for_position('AXS/USDT', 'SHORT', 75)
            >>> print(f"SL: {sl}%, TP: {tp}%")
            SL: 3.0%, TP: 5.0%
        """
        signal_components = signal_components or []

        # 🔥 紧急修复: 根据持仓时间判断市场类型,使用固定止损止盈
        # 中性市 (max_hold_minutes=120): 止损1%, 止盈1.5%
        # 趋势市 (max_hold_minutes=240): 止损3%, 止盈5%

        if max_hold_minutes <= 120:
            # 中性市场 (Big4强度30-60)
            final_sl = 1.0   # 1%止损
            final_tp = 1.5   # 1.5%止盈
            market_type = "中性市"
            reason = f"中性市场固定值 | 止损1% 止盈1.5% | 盈亏比1:1.5 | 持仓{max_hold_minutes}分钟"
        else:
            # 趋势市场 (Big4强度>60 或 正常市场)
            final_sl = 3.0   # 3%止损
            final_tp = 5.0   # 5%止盈
            market_type = "趋势市"
            reason = f"趋势市场固定值 | 止损3% 止盈5% | 盈亏比1:1.67 | 持仓{max_hold_minutes}分钟"

        logger.info(f"{symbol} {position_side} - {market_type} SL:{final_sl:.2f}% TP:{final_tp:.2f}% - {reason}")

        return round(final_sl, 2), round(final_tp, 2), reason

    def _get_volatility_cached(self, symbol: str) -> Optional[Dict]:
        """获取波动率数据(带缓存)"""
        cache_key = f"{symbol}_volatility"

        # 检查缓存
        if cache_key in self.cache:
            cached_time, cached_data = self.cache[cache_key]
            if (datetime.now() - cached_time).total_seconds() < self.cache_ttl:
                return cached_data

        # 计算新数据
        volatility = self._calculate_volatility(symbol)

        if volatility:
            self.cache[cache_key] = (datetime.now(), volatility)

        return volatility

    def _calculate_volatility(self, symbol: str) -> Optional[Dict]:
        """计算交易对的方向性波动率"""
        try:
            conn = mysql.connector.connect(**DB_CONFIG)
            cursor = conn.cursor(dictionary=True)

            # 查询最近24小时的1小时K线
            # 原因: 持仓时间4-6小时,用24小时数据既贴近当前市场,又有足够样本
            cursor.execute("""
                SELECT
                    open_price, high_price, low_price, close_price
                FROM kline_data
                WHERE symbol = %s
                AND timeframe = '1h'
                AND timestamp >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
                ORDER BY timestamp ASC
            """, (symbol,))

            klines = cursor.fetchall()
            cursor.close()

            if not klines or len(klines) < 12:
                logger.warning(f"{symbol} K线数据不足: {len(klines) if klines else 0}根")
                return None

            # 计算方向性波动
            upside_moves = []    # 向上波动
            downside_moves = []  # 向下波动

            for k in klines:
                open_p = float(k['open_price'])
                high_p = float(k['high_price'])
                low_p = float(k['low_price'])

                upside_pct = (high_p - open_p) / open_p * 100
                downside_pct = (open_p - low_p) / open_p * 100

                upside_moves.append(upside_pct)
                downside_moves.append(downside_pct)

            # 统计指标
            return {
                'avg_upside': statistics.mean(upside_moves),
                'avg_downside': statistics.mean(downside_moves),
                'upside_p75': statistics.quantiles(upside_moves, n=4)[2],
                'downside_p75': statistics.quantiles(downside_moves, n=4)[2],
                'max_upside': max(upside_moves),
                'max_downside': max(downside_moves),
                'kline_count': len(klines)
            }

        except Exception as e:
            logger.error(f"计算 {symbol} 波动率失败: {e}", exc_info=True)
            return None

    def _get_default_sl_tp(self, position_side: str, reason: str) -> Tuple[float, float, str]:
        """返回默认的止损止盈值"""
        # 保守的固定值
        default_sl = 3.0  # 3%止损(比原来的2%更宽松)
        default_tp = 6.0  # 6%止盈(保持1:2盈亏比)

        full_reason = f"使用默认值 ({reason}) | 盈亏比1:2.0"

        return default_sl, default_tp, full_reason

    def clear_cache(self):
        """清空缓存"""
        self.cache.clear()
        logger.info("波动率缓存已清空")

    def get_cache_stats(self) -> Dict:
        """获取缓存统计"""
        now = datetime.now()
        valid_count = sum(
            1 for cached_time, _ in self.cache.values()
            if (now - cached_time).total_seconds() < self.cache_ttl
        )

        return {
            'total_cached': len(self.cache),
            'valid_cached': valid_count,
            'ttl_seconds': self.cache_ttl
        }


# 全局单例
_calculator_instance = None

def get_volatility_calculator() -> VolatilityCalculator:
    """获取波动率计算器单例"""
    global _calculator_instance
    if _calculator_instance is None:
        _calculator_instance = VolatilityCalculator()
    return _calculator_instance
