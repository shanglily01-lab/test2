#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
高级信号检测器 - Advanced Signal Detector

实现基于SIGNAL_TYPES_SPECIFICATION.md的多周期信号检测:
1. 上涨无力 + 突然放量下跌 (做空)
2. 底部反转 + 锤头线 (做多)
3. 其他反转和突破信号

Author: Claude
Date: 2026-01-26
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from loguru import logger
import pymysql
from dataclasses import dataclass


@dataclass
class KlineData:
    """K线数据结构"""
    timestamp: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


@dataclass
class SignalResult:
    """信号检测结果"""
    signal_type: str  # 'WEAK_RALLY_SHORT', 'BOTTOM_REVERSAL_LONG', etc
    direction: str  # 'LONG' or 'SHORT'
    strength: str  # 'STRONG', 'MEDIUM', 'WEAK'
    score: int  # 0-105
    entry_price: float
    stop_loss: float
    reason: str
    details: Dict  # 详细信息


class AdvancedSignalDetector:
    """高级信号检测器"""

    def __init__(self, db_config: dict):
        """
        初始化检测器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

        logger.info("✅ AdvancedSignalDetector 已初始化")

    def get_db_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            self.connection = pymysql.connect(
                host=self.db_config.get('host', 'localhost'),
                port=self.db_config.get('port', 3306),
                user=self.db_config.get('user', 'root'),
                password=self.db_config.get('password', ''),
                database=self.db_config.get('database', 'binance-data'),
                charset='utf8mb4',
                cursorclass=pymysql.cursors.DictCursor
            )
        return self.connection

    def fetch_klines(self, symbol: str, timeframe: str, limit: int = 100) -> List[KlineData]:
        """
        获取K线数据

        Args:
            symbol: 交易对
            timeframe: 时间周期 ('1h', '15m', '5m')
            limit: 获取数量

        Returns:
            K线数据列表
        """
        conn = self.get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            SELECT
                timestamp,
                open_price,
                high_price,
                low_price,
                close_price,
                volume
            FROM kline_data
            WHERE symbol = %s
            AND timeframe = %s
            AND exchange = 'binance_futures'
            ORDER BY timestamp DESC
            LIMIT %s
        ''', (symbol, timeframe, limit))

        rows = cursor.fetchall()

        # 转换为KlineData对象,并按时间正序排列
        klines = []
        for row in reversed(rows):
            klines.append(KlineData(
                timestamp=row['timestamp'],
                open_price=float(row['open_price']),
                high_price=float(row['high_price']),
                low_price=float(row['low_price']),
                close_price=float(row['close_price']),
                volume=float(row['volume'])
            ))

        cursor.close()
        return klines

    def detect_signals(self, symbol: str) -> Optional[SignalResult]:
        """
        检测交易信号 (主入口)

        按优先级顺序检测:
        1. 底部反转做多 (最高优先级)
        2. 顶部反转做空
        3. 上涨无力+突然下跌做空
        4. 趋势延续信号

        Args:
            symbol: 交易对

        Returns:
            SignalResult 或 None
        """
        try:
            # 获取K线数据
            klines_1h = self.fetch_klines(symbol, '1h', limit=24)
            klines_15m = self.fetch_klines(symbol, '15m', limit=96)
            klines_5m = self.fetch_klines(symbol, '5m', limit=288)

            if len(klines_5m) < 20:
                logger.warning(f"{symbol} K线数据不足,跳过检测")
                return None

            # 1. 优先检测底部反转 (做多)
            signal = self.detect_bottom_reversal_long(symbol, klines_1h, klines_15m, klines_5m)
            if signal and signal.score >= 50:
                logger.info(f"🟢 {symbol} 检测到底部反转做多信号: {signal.strength} ({signal.score}分)")
                return signal

            # 2. 检测上涨无力+突然下跌 (做空)
            signal = self.detect_weak_rally_short(symbol, klines_1h, klines_15m, klines_5m)
            if signal and signal.score >= 50:
                logger.info(f"🔴 {symbol} 检测到做空信号: {signal.strength} ({signal.score}分)")
                return signal

            # 未检测到有效信号
            return None

        except Exception as e:
            logger.error(f"检测信号时出错 {symbol}: {e}")
            return None

    def detect_weak_rally_short(
        self,
        symbol: str,
        klines_1h: List[KlineData],
        klines_15m: List[KlineData],
        klines_5m: List[KlineData]
    ) -> Optional[SignalResult]:
        """
        检测上涨无力+突然下跌做空信号

        Returns:
            SignalResult 或 None
        """
        if len(klines_1h) < 6 or len(klines_15m) < 8 or len(klines_5m) < 20:
            return None

        # ========== 第1步: 检查1H上涨无力 ==========
        last_6_1h = klines_1h[-6:]

        up_count = sum(1 for k in last_6_1h if k.close_price > k.open_price)
        changes = [(k.close_price - k.open_price) / k.open_price * 100 for k in last_6_1h]
        avg_change_pct = sum(abs(c) for c in changes) / len(changes)

        high_prices = [k.high_price for k in last_6_1h]
        low_prices = [k.low_price for k in last_6_1h]
        price_range_pct = (max(high_prices) - min(low_prices)) / last_6_1h[0].open_price * 100

        has_weak_rally = (up_count > 3 and avg_change_pct < 0.3 and price_range_pct < 2.0)

        # ========== 第2步: 检查15M下探有空间 ==========
        last_8_15m = klines_15m[-8:]

        has_downside_space = False
        for k in last_8_15m:
            if k.low_price < k.open_price * 0.99:  # 下探超过1%
                has_downside_space = True
                break

        # ========== 第3步: 检查5M触发信号 ==========
        current_5m = klines_5m[-1]
        last_2_5m = klines_5m[-2:] if len(klines_5m) >= 2 else []

        # 计算平均成交量
        avg_volume_20 = sum(k.volume for k in klines_5m[-20:]) / 20

        signal_condition = None

        # 条件A: 单根大跌
        if current_5m.close_price < current_5m.open_price:
            drop_pct = (current_5m.close_price - current_5m.open_price) / current_5m.open_price * 100
            volume_ratio = current_5m.volume / avg_volume_20 if avg_volume_20 > 0 else 0
            body_pct = abs(current_5m.close_price - current_5m.open_price) / (current_5m.high_price - current_5m.low_price) if (current_5m.high_price - current_5m.low_price) > 0 else 0

            if drop_pct <= -0.4 and volume_ratio >= 2.0 and body_pct > 0.3:
                signal_condition = {
                    'type': 'A',
                    'drop_pct': drop_pct,
                    'volume_ratio': volume_ratio,
                    'is_accelerating': False
                }

        # 条件B: 连续下跌 (更优先!)
        if len(last_2_5m) == 2:
            k1, k2 = last_2_5m

            drop1_pct = (k1.close_price - k1.open_price) / k1.open_price * 100
            drop2_pct = (k2.close_price - k2.open_price) / k2.open_price * 100

            if drop1_pct <= -0.2 and drop2_pct <= -0.2:
                total_drop_pct = drop1_pct + drop2_pct

                vol_ratio_1 = k1.volume / avg_volume_20 if avg_volume_20 > 0 else 0
                vol_ratio_2 = k2.volume / avg_volume_20 if avg_volume_20 > 0 else 0

                if total_drop_pct <= -0.4 and (vol_ratio_1 >= 2.0 or vol_ratio_2 >= 2.0):
                    # 条件B优先,覆盖条件A
                    signal_condition = {
                        'type': 'B',
                        'drop_pct': total_drop_pct,
                        'volume_ratio': max(vol_ratio_1, vol_ratio_2),
                        'is_accelerating': abs(drop2_pct) > abs(drop1_pct),
                        'both_high_volume': vol_ratio_1 >= 2.0 and vol_ratio_2 >= 2.0
                    }

        if not signal_condition:
            return None

        # ========== 第4步: 计算信号强度 ==========
        strength = 0

        # 1. 下跌幅度评分 (0-30分)
        drop_pct = abs(signal_condition['drop_pct'])
        if signal_condition['type'] == 'B':  # 连续下跌
            if drop_pct > 0.6:
                strength += 30
            elif drop_pct > 0.4:
                strength += 25
            strength += 5  # 连续下跌奖励
        else:  # 单根大跌
            if drop_pct > 0.6:
                strength += 30
            elif drop_pct > 0.4:
                strength += 20

        # 2. 量能评分 (0-40分)
        vol_ratio = signal_condition['volume_ratio']
        if vol_ratio > 10:
            strength += 40
        elif vol_ratio > 5:
            strength += 30
        elif vol_ratio > 2:
            strength += 20

        # 连续下跌且两根都放量,额外加分
        if signal_condition['type'] == 'B' and signal_condition.get('both_high_volume'):
            strength += 5

        # 3. 多周期确认评分 (0-30分)
        if has_weak_rally:
            strength += 10
        if has_downside_space:
            strength += 10
        if signal_condition['type'] == 'B' and signal_condition.get('is_accelerating'):
            strength += 5  # 加速下跌额外奖励

        # ========== 第5步: 判断信号等级 ==========
        if strength >= 70:
            strength_level = 'STRONG'
        elif strength >= 50:
            strength_level = 'MEDIUM'
        elif strength >= 30:
            strength_level = 'WEAK'
        else:
            return None  # 分数太低,不产生信号

        # ========== 第6步: 构造并返回信号 ==========
        condition_desc = '连续下跌' if signal_condition['type'] == 'B' else '单根大跌'
        reason = f"{condition_desc}{signal_condition['drop_pct']:.2f}% + 放量{signal_condition['volume_ratio']:.1f}x"

        if has_weak_rally:
            reason += " + 1H上涨无力"
        if has_downside_space:
            reason += " + 15M下探有空间"

        return SignalResult(
            signal_type='WEAK_RALLY_SHORT',
            direction='SHORT',
            strength=strength_level,
            score=strength,
            entry_price=current_5m.close_price,
            stop_loss=current_5m.close_price * 1.02,  # 止损2%
            reason=reason,
            details={
                'condition': signal_condition['type'],
                'drop_pct': signal_condition['drop_pct'],
                'volume_ratio': signal_condition['volume_ratio'],
                'has_1h_weak_rally': has_weak_rally,
                'has_15m_downside_space': has_downside_space,
                'is_accelerating': signal_condition.get('is_accelerating', False),
                'timestamp': current_5m.timestamp
            }
        )

    def detect_bottom_reversal_long(
        self,
        symbol: str,
        klines_1h: List[KlineData],
        klines_15m: List[KlineData],
        klines_5m: List[KlineData]
    ) -> Optional[SignalResult]:
        """
        检测底部反转做多信号

        Returns:
            SignalResult 或 None
        """
        if len(klines_1h) < 4 or len(klines_15m) < 8 or len(klines_5m) < 30:
            return None

        # ========== 第1步: 检查1H横盘筑底 ==========
        last_4_1h = klines_1h[-4:]

        changes = [(k.close_price - k.open_price) / k.open_price * 100 for k in last_4_1h]
        avg_change = sum(abs(c) for c in changes) / len(changes)

        has_consolidation = avg_change < 0.3  # 平均涨跌<0.3%

        # ========== 第2步: 检查15M长下影线 ==========
        last_8_15m = klines_15m[-8:]

        hammer_count_15m = 0
        for k in last_8_15m:
            if k.high_price != k.low_price:
                lower_shadow = min(k.open_price, k.close_price) - k.low_price
                lower_shadow_pct = lower_shadow / (k.high_price - k.low_price)
                if lower_shadow_pct > 0.5:
                    hammer_count_15m += 1

        has_15m_hammers = hammer_count_15m >= 2

        # ========== 第3步: 检查5M锤头线 (核心!) ==========
        last_6_5m = klines_5m[-6:]  # 最近30分钟
        current_5m = klines_5m[-1]

        # 计算平均成交量
        avg_volume_20 = sum(k.volume for k in klines_5m[-20:]) / 20

        # 计算24小时最低价
        min_price_24h = min(k.low_price for k in klines_5m[-288:] if k.low_price > 0)

        hammer_signals = []

        for k in last_6_5m:
            if k.high_price == k.low_price or k.high_price - k.low_price == 0:
                continue

            # 计算K线形态
            body = abs(k.close_price - k.open_price)
            total_length = k.high_price - k.low_price
            lower_shadow = min(k.open_price, k.close_price) - k.low_price

            lower_shadow_pct = lower_shadow / total_length
            volume_ratio = k.volume / avg_volume_20 if avg_volume_20 > 0 else 0

            # 判断是否是锤头线
            is_hammer = (
                lower_shadow_pct > 0.60 or  # 下影线>60%
                (body > 0 and lower_shadow > body * 2)  # 下影线>实体2倍
            )

            # 判断是否在底部
            is_at_bottom = k.low_price <= min_price_24h * 1.02  # 在最低价2%以内

            # 判断是否放量
            is_high_volume = volume_ratio >= 2.0

            # 判断收盘位置
            close_position = (k.close_price - k.low_price) / total_length if total_length > 0 else 0
            close_in_upper_half = close_position > 0.5

            if is_hammer and is_at_bottom:
                hammer_signals.append({
                    'timestamp': k.timestamp,
                    'lower_shadow_pct': lower_shadow_pct,
                    'volume_ratio': volume_ratio,
                    'is_high_volume': is_high_volume,
                    'close_in_upper_half': close_in_upper_half,
                    'price': k.close_price
                })

        if len(hammer_signals) < 3:
            return None  # 锤头线不够,不产生信号

        # ========== 第4步: 计算信号强度 ==========
        strength = 0

        # 最强的锤头线
        best_hammer = max(hammer_signals, key=lambda x: x['lower_shadow_pct'])
        avg_volume_ratio = sum(h['volume_ratio'] for h in hammer_signals) / len(hammer_signals)

        # 1. 下影线长度评分 (0-30分)
        if best_hammer['lower_shadow_pct'] > 0.70:
            strength += 30
        elif best_hammer['lower_shadow_pct'] > 0.60:
            strength += 20

        # 2. 量能评分 (0-40分)
        if avg_volume_ratio > 5:
            strength += 40
        elif avg_volume_ratio > 3:
            strength += 30
        elif avg_volume_ratio > 2:
            strength += 20

        # 3. 多周期确认评分 (0-30分)
        if has_consolidation:
            strength += 10
        if has_15m_hammers:
            strength += 10
        if len(hammer_signals) >= 4:
            strength += 10  # 锤头线>=4次

        # ========== 第5步: 判断信号等级 ==========
        if strength >= 70:
            strength_level = 'STRONG'
        elif strength >= 50:
            strength_level = 'MEDIUM'
        else:
            return None

        # ========== 第6步: 构造并返回信号 ==========
        reason = f"底部{len(hammer_signals)}次锤头线 + 放量{avg_volume_ratio:.1f}x"

        if has_consolidation:
            reason += " + 1H横盘筑底"
        if has_15m_hammers:
            reason += " + 15M长下影"

        return SignalResult(
            signal_type='BOTTOM_REVERSAL_LONG',
            direction='LONG',
            strength=strength_level,
            score=strength,
            entry_price=current_5m.close_price,
            stop_loss=min_price_24h * 0.99,  # 止损在24H最低点下方1%
            reason=reason,
            details={
                'hammer_count': len(hammer_signals),
                'best_lower_shadow_pct': best_hammer['lower_shadow_pct'],
                'avg_volume_ratio': avg_volume_ratio,
                'has_1h_consolidation': has_consolidation,
                'has_15m_hammers': has_15m_hammers,
                'min_price_24h': min_price_24h,
                'timestamp': current_5m.timestamp
            }
        )

    def close(self):
        """关闭数据库连接"""
        if self.connection and self.connection.open:
            self.connection.close()
            logger.info("数据库连接已关闭")
