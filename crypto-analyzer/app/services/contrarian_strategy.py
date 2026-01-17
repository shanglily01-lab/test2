#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
反向操作策略 (Contrarian Strategy)

核心理念：
在震荡市环境下，当V3/V2检测到强趋势信号时，反向开仓，利用均值回归特性获利

适用场景：
- 震荡市：价格在区间内来回波动
- 假突破：价格突破后快速反转
- 趋势尾巴：V3检测到信号时趋势已接近尾声

数据支持（2026-01-17）：
- 反向操作胜率：94.7% (18/19笔)
- 原始策略胜率：5.3% (1/19笔)
- 盈亏改善：从-$1,452 → +$1,452
"""

import pymysql
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from loguru import logger


class ContrarianStrategy:
    """反向操作策略"""

    def __init__(self, db_config: Dict):
        """
        初始化反向操作策略

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config

    def detect_market_regime(self, lookback_hours: int = 24, min_trades: int = 10) -> str:
        """
        检测市场环境：震荡市 vs 趋势市

        检测逻辑：
        1. 统计最近N小时的已平仓交易
        2. 计算反转平仓占比（progressive_sl, reversed等）
        3. 计算短期持仓占比（<2小时）
        4. 判断市场环境

        Args:
            lookback_hours: 回溯时长（小时）
            min_trades: 最小交易数（少于此数量不判断）

        Returns:
            'oscillating': 震荡市
            'trending': 趋势市
            'unknown': 数据不足
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor(pymysql.cursors.DictCursor)

            # 查询最近N小时的已平仓交易
            cursor.execute("""
                SELECT
                    notes as close_reason,
                    TIMESTAMPDIFF(MINUTE, open_time, close_time) as duration_minutes,
                    realized_pnl
                FROM futures_positions
                WHERE close_time >= NOW() - INTERVAL %s HOUR
                AND status = 'closed'
                ORDER BY close_time DESC
            """, (lookback_hours,))

            trades = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(trades) < min_trades:
                logger.info(f"市场环境检测: 数据不足（{len(trades)}<{min_trades}笔）")
                return 'unknown'

            # 统计反转平仓数量
            reversal_count = 0
            short_holding_count = 0  # <2小时持仓

            for trade in trades:
                close_reason = trade['close_reason'] or ''
                duration = trade['duration_minutes'] or 0

                # 检查是否是反转平仓
                if any(keyword in close_reason for keyword in [
                    'reversed', 'progressive_sl', 'trend_weak', 'severe_loss'
                ]):
                    reversal_count += 1

                # 检查持仓时长
                if duration < 120:  # <2小时
                    short_holding_count += 1

            # 计算指标
            reversal_rate = reversal_count / len(trades) * 100
            short_holding_rate = short_holding_count / len(trades) * 100

            logger.info(
                f"市场环境检测({len(trades)}笔): "
                f"反转率={reversal_rate:.1f}%, 短期持仓率={short_holding_rate:.1f}%"
            )

            # 判断市场环境
            if reversal_rate > 60 and short_holding_rate > 60:
                logger.info("📊 市场环境判定: 强震荡市")
                return 'oscillating'
            elif reversal_rate > 40 or short_holding_rate > 50:
                logger.info("📊 市场环境判定: 震荡市")
                return 'oscillating'
            else:
                logger.info("📊 市场环境判定: 趋势市")
                return 'trending'

        except Exception as e:
            logger.error(f"市场环境检测失败: {e}")
            return 'unknown'

    def should_use_contrarian(self, strategy: Dict, force_check: bool = False) -> bool:
        """
        判断是否应该使用反向操作策略

        Args:
            strategy: 策略配置
            force_check: 是否强制检查市场环境（否则使用配置）

        Returns:
            True: 使用反向操作
            False: 使用正常趋势跟随
        """
        # 检查策略是否启用反向操作
        contrarian_enabled = strategy.get('contrarianEnabled', False)
        if not contrarian_enabled:
            return False

        # 检查市场环境配置
        market_regime_mode = strategy.get('marketRegime', 'auto_detect')

        if market_regime_mode == 'always_contrarian':
            logger.info("🔄 反向操作: 强制启用")
            return True

        elif market_regime_mode == 'never_contrarian':
            logger.info("➡️ 趋势跟随: 强制启用")
            return False

        elif market_regime_mode == 'auto_detect' or force_check:
            # 自动检测市场环境
            lookback_hours = strategy.get('marketDetection', {}).get('lookbackHours', 24)
            min_trades = strategy.get('marketDetection', {}).get('minTrades', 10)

            market_regime = self.detect_market_regime(lookback_hours, min_trades)

            if market_regime == 'oscillating':
                logger.info("🔄 反向操作: 市场震荡，启用反向策略")
                return True
            elif market_regime == 'trending':
                logger.info("➡️ 趋势跟随: 市场趋势，使用正常策略")
                return False
            else:
                # 数据不足，默认使用正常策略
                logger.info("➡️ 趋势跟随: 市场环境不明，使用正常策略")
                return False

        return False

    def reverse_signal(self, direction: str) -> str:
        """
        反转信号方向

        Args:
            direction: 原始方向 'long' 或 'short'

        Returns:
            反转后的方向
        """
        if direction == 'long':
            return 'short'
        elif direction == 'short':
            return 'long'
        else:
            return direction

    def get_contrarian_params(self, strategy: Dict, original_direction: str) -> Dict:
        """
        获取反向操作参数

        Args:
            strategy: 策略配置
            original_direction: 原始信号方向

        Returns:
            反向操作参数
        """
        # 反转方向
        new_direction = self.reverse_signal(original_direction)

        # 反向操作风险参数（更保守）
        contrarian_risk = strategy.get('contrarianRisk', {})

        params = {
            'direction': new_direction,
            'stop_loss': contrarian_risk.get('stopLoss', 1.5),  # 快速止损1.5%
            'take_profit': contrarian_risk.get('takeProfit', 1.0),  # 快速止盈1.0%
            'trailing_activate': contrarian_risk.get('trailingActivate', 0.8),  # 0.8%激活
            'trailing_callback': contrarian_risk.get('trailingCallback', 0.3),  # 0.3%回调
            'limit_order_offset': contrarian_risk.get('limitOrderOffset', 0.5),  # 0.5%限价单
            'order_timeout_hours': contrarian_risk.get('orderTimeoutHours', 1),  # 1小时超时
        }

        return params

    def format_contrarian_reason(self, original_signal: str, original_direction: str,
                                  signal_strength: float, market_regime: str) -> str:
        """
        格式化反向操作入场原因

        Args:
            original_signal: 原始信号类型
            original_direction: 原始信号方向
            signal_strength: 信号强度
            market_regime: 市场环境

        Returns:
            格式化的入场原因
        """
        return (
            f"反向操作({market_regime}市): "
            f"原始{original_signal}信号{original_direction.upper()}强度{signal_strength:.2f}% "
            f"→ 反向{'SHORT' if original_direction == 'long' else 'LONG'}入场"
        )
