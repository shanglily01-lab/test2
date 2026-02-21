#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号质量管理器 - 基于历史表现调整信号筛选阈值
不修改权重，而是根据信号表现调整开仓门槛
"""

from typing import Dict, Optional
from decimal import Decimal
from datetime import datetime
import pymysql
from loguru import logger


class SignalQualityManager:
    """信号质量管理器"""

    def __init__(self, db_config: dict):
        """
        初始化信号质量管理器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self.connection = None

        # 质量评级配置
        self.MIN_TRADES_FOR_QUALITY = 10  # 至少10笔才开始评级
        self.EXCELLENT_WIN_RATE = 65  # 优秀信号胜率阈值
        self.EXCELLENT_PL_RATIO = 1.5  # 优秀信号盈亏比阈值
        self.GOOD_WIN_RATE = 55
        self.GOOD_PL_RATIO = 1.2
        self.POOR_WIN_RATE = 45
        self.POOR_PL_RATIO = 0.8
        self.BAD_WIN_RATE = 40
        self.BAD_PL_RATIO = 0.7

        # 确保表存在
        self._ensure_table_exists()

        logger.info("✅ 信号质量管理器已初始化 | 最小样本:10笔 | 不修改权重，仅调整阈值")

    def _get_connection(self):
        """获取数据库连接"""
        if self.connection is None or not self.connection.open:
            try:
                self.connection = pymysql.connect(
                    **self.db_config,
                    charset='utf8mb4',
                    cursorclass=pymysql.cursors.DictCursor
                )
            except Exception as e:
                logger.error(f"❌ 数据库连接失败: {e}")
                raise
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

    def _ensure_table_exists(self):
        """确保信号质量表存在"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signal_quality_metrics (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    signal_combination VARCHAR(255) NOT NULL COMMENT '信号组合key',
                    side ENUM('LONG', 'SHORT') NOT NULL COMMENT '方向',

                    total_trades INT DEFAULT 0 COMMENT '总交易次数',
                    winning_trades INT DEFAULT 0 COMMENT '盈利次数',
                    losing_trades INT DEFAULT 0 COMMENT '亏损次数',
                    win_rate DECIMAL(5,2) DEFAULT 0 COMMENT '胜率%',

                    total_profit_loss DECIMAL(10,2) DEFAULT 0 COMMENT '总盈亏',
                    avg_profit DECIMAL(10,2) DEFAULT 0 COMMENT '平均盈利',
                    avg_loss DECIMAL(10,2) DEFAULT 0 COMMENT '平均亏损',
                    profit_loss_ratio DECIMAL(5,2) DEFAULT 0 COMMENT '盈亏比',

                    quality_score DECIMAL(5,2) DEFAULT 1.0 COMMENT '质量评分 0.5-1.5',
                    quality_level ENUM('excellent', 'good', 'neutral', 'poor', 'bad') DEFAULT 'neutral',
                    threshold_multiplier DECIMAL(5,2) DEFAULT 1.0 COMMENT '阈值乘数',

                    last_trade_at DATETIME COMMENT '最后交易时间',
                    first_trade_at DATETIME COMMENT '首次交易时间',
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

                    UNIQUE KEY idx_signal_side (signal_combination, side),
                    INDEX idx_quality_score (quality_score),
                    INDEX idx_last_trade (last_trade_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='信号质量统计表'
            """)

            conn.commit()
            cursor.close()

        except Exception as e:
            logger.warning(f"创建信号质量表失败(可能已存在): {e}")

    def calculate_quality_score(self, metrics: dict) -> dict:
        """
        根据历史表现计算信号质量评分

        Args:
            metrics: 信号统计数据

        Returns:
            {
                'quality_score': float,  # 0.5-1.5
                'quality_level': str,    # excellent/good/neutral/poor/bad
                'threshold_multiplier': float,  # 阈值乘数
                'reason': str  # 评级原因
            }
        """
        total_trades = metrics.get('total_trades', 0)
        win_rate = metrics.get('win_rate', 0)
        pl_ratio = metrics.get('profit_loss_ratio', 0)

        # 样本不足，返回中性
        if total_trades < self.MIN_TRADES_FOR_QUALITY:
            return {
                'quality_score': 1.0,
                'quality_level': 'neutral',
                'threshold_multiplier': 1.0,
                'reason': f'样本不足({total_trades}笔)'
            }

        # 优秀信号（胜率>65% 且 盈亏比>1.5）
        if win_rate > self.EXCELLENT_WIN_RATE and pl_ratio > self.EXCELLENT_PL_RATIO:
            return {
                'quality_score': 1.4,
                'quality_level': 'excellent',
                'threshold_multiplier': 0.7,  # 降低30%门槛: 35→25分
                'reason': f'优秀(胜率{win_rate:.1f}% 盈亏比{pl_ratio:.2f})'
            }

        # 良好信号（胜率>55% 且 盈亏比>1.2）
        elif win_rate > self.GOOD_WIN_RATE and pl_ratio > self.GOOD_PL_RATIO:
            return {
                'quality_score': 1.2,
                'quality_level': 'good',
                'threshold_multiplier': 0.83,  # 降低17%门槛: 35→29分
                'reason': f'良好(胜率{win_rate:.1f}% 盈亏比{pl_ratio:.2f})'
            }

        # 差信号（胜率<45% 或 盈亏比<0.8）
        elif win_rate < self.POOR_WIN_RATE or pl_ratio < self.POOR_PL_RATIO:
            return {
                'quality_score': 0.6,
                'quality_level': 'poor',
                'threshold_multiplier': 1.67,  # 提高67%门槛: 35→58分
                'reason': f'差(胜率{win_rate:.1f}% 盈亏比{pl_ratio:.2f})'
            }

        # 极差信号（胜率<40% 且 盈亏比<0.7）
        elif win_rate < self.BAD_WIN_RATE and pl_ratio < self.BAD_PL_RATIO:
            return {
                'quality_score': 0.5,
                'quality_level': 'bad',
                'threshold_multiplier': 2.0,  # 提高100%门槛: 35→70分（基本禁用）
                'reason': f'极差(胜率{win_rate:.1f}% 盈亏比{pl_ratio:.2f})'
            }

        # 中性信号
        else:
            return {
                'quality_score': 1.0,
                'quality_level': 'neutral',
                'threshold_multiplier': 1.0,
                'reason': f'中性(胜率{win_rate:.1f}% 盈亏比{pl_ratio:.2f})'
            }

    def get_signal_quality(self, signal_combination: str, side: str) -> dict:
        """
        获取信号质量评级

        Args:
            signal_combination: 信号组合key (如 "position_low+volume_power_bull")
            side: 方向 (LONG/SHORT)

        Returns:
            质量评分字典
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM signal_quality_metrics
                WHERE signal_combination = %s AND side = %s
            """, (signal_combination, side))

            metrics = cursor.fetchone()
            cursor.close()

            if metrics:
                # 计算质量评分
                quality = self.calculate_quality_score(metrics)
                return quality
            else:
                # 无历史记录，返回中性
                return {
                    'quality_score': 1.0,
                    'quality_level': 'neutral',
                    'threshold_multiplier': 1.0,
                    'reason': '无历史记录'
                }

        except Exception as e:
            logger.error(f"获取信号质量失败: {e}")
            return {
                'quality_score': 1.0,
                'quality_level': 'neutral',
                'threshold_multiplier': 1.0,
                'reason': f'查询失败:{e}'
            }

    def update_signal_quality(self, signal_combination: str, side: str, pnl: float):
        """
        平仓后更新信号质量统计

        Args:
            signal_combination: 信号组合key
            side: 方向
            pnl: 实现盈亏(USDT)
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 检查是否已存在
            cursor.execute("""
                SELECT * FROM signal_quality_metrics
                WHERE signal_combination = %s AND side = %s
            """, (signal_combination, side))

            existing = cursor.fetchone()

            if existing:
                # 更新统计
                total_trades = existing['total_trades'] + 1
                winning_trades = existing['winning_trades'] + (1 if pnl > 0 else 0)
                losing_trades = existing['losing_trades'] + (1 if pnl <= 0 else 0)
                win_rate = (winning_trades / total_trades) * 100

                # 更新盈亏
                total_pnl = float(existing['total_profit_loss']) + pnl

                # 计算平均盈利和亏损
                if pnl > 0:
                    avg_profit = (float(existing['avg_profit']) * existing['winning_trades'] + pnl) / winning_trades
                    avg_loss = float(existing['avg_loss'])
                else:
                    avg_profit = float(existing['avg_profit'])
                    if losing_trades > 0:
                        avg_loss = (float(existing['avg_loss']) * existing['losing_trades'] + abs(pnl)) / losing_trades
                    else:
                        avg_loss = abs(pnl)

                # 盈亏比
                pl_ratio = abs(avg_profit / avg_loss) if avg_loss != 0 else 0

                # 计算质量评分
                quality = self.calculate_quality_score({
                    'total_trades': total_trades,
                    'win_rate': win_rate,
                    'profit_loss_ratio': pl_ratio
                })

                cursor.execute("""
                    UPDATE signal_quality_metrics
                    SET total_trades = %s,
                        winning_trades = %s,
                        losing_trades = %s,
                        win_rate = %s,
                        total_profit_loss = %s,
                        avg_profit = %s,
                        avg_loss = %s,
                        profit_loss_ratio = %s,
                        quality_score = %s,
                        quality_level = %s,
                        threshold_multiplier = %s,
                        last_trade_at = NOW(),
                        updated_at = NOW()
                    WHERE signal_combination = %s AND side = %s
                """, (
                    total_trades, winning_trades, losing_trades, win_rate,
                    total_pnl, avg_profit, avg_loss, pl_ratio,
                    quality['quality_score'], quality['quality_level'], quality['threshold_multiplier'],
                    signal_combination, side
                ))

                logger.info(
                    f"✅ [QUALITY_UPDATE] {signal_combination[:30]}... {side} | "
                    f"盈亏:{pnl:+.2f}U | {total_trades}笔 胜率{win_rate:.1f}% | "
                    f"评级:{quality['quality_level']}({quality['quality_score']:.2f})"
                )
            else:
                # 首次记录
                is_win = 1 if pnl > 0 else 0
                win_rate = 100 if pnl > 0 else 0

                cursor.execute("""
                    INSERT INTO signal_quality_metrics (
                        signal_combination, side,
                        total_trades, winning_trades, losing_trades, win_rate,
                        total_profit_loss, avg_profit, avg_loss, profit_loss_ratio,
                        quality_score, quality_level, threshold_multiplier,
                        first_trade_at, last_trade_at
                    ) VALUES (
                        %s, %s, 1, %s, %s, %s,
                        %s, %s, %s, 0,
                        1.0, 'neutral', 1.0,
                        NOW(), NOW()
                    )
                """, (
                    signal_combination, side,
                    is_win, 1 - is_win, win_rate,
                    pnl, pnl if pnl > 0 else 0, abs(pnl) if pnl <= 0 else 0
                ))

                logger.info(f"✅ [QUALITY_NEW] {signal_combination[:30]}... {side} | 首笔盈亏:{pnl:+.2f}U")

            conn.commit()
            cursor.close()

        except Exception as e:
            logger.error(f"更新信号质量失败: {e}")

    def check_signal_quality_filter(
        self,
        symbol: str,
        side: str,
        score: float,
        signal_combination: str,
        base_threshold: float = 35
    ) -> tuple[bool, str]:
        """
        基于信号质量调整阈值并判断是否通过

        Args:
            symbol: 交易对
            side: 方向
            score: 信号评分
            signal_combination: 信号组合key
            base_threshold: 基础阈值

        Returns:
            (是否通过, 原因)
        """
        # 获取信号质量
        quality = self.get_signal_quality(signal_combination, side)

        # 调整后的阈值
        adjusted_threshold = base_threshold * quality['threshold_multiplier']

        # 判断是否通过
        passed = score >= adjusted_threshold

        if passed:
            reason = (
                f"✅ 质量筛选通过 | "
                f"评分:{score:.1f} >= 阈值:{adjusted_threshold:.1f} | "
                f"质量:{quality['quality_level']}({quality['quality_score']:.2f}) | "
                f"{quality['reason']}"
            )
        else:
            reason = (
                f"❌ 质量筛选拦截 | "
                f"评分:{score:.1f} < 阈值:{adjusted_threshold:.1f} | "
                f"质量:{quality['quality_level']}({quality['quality_score']:.2f}) | "
                f"{quality['reason']}"
            )

        return passed, reason
