#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
信号黑名单检查器 - 动态加载和检查信号黑名单
"""

import pymysql
from typing import Dict, List, Optional, Tuple
from loguru import logger
from datetime import datetime, timedelta
import re


class SignalBlacklistChecker:
    """信号黑名单检查器（动态加载，带缓存）"""

    def __init__(self, db_config: dict, cache_minutes: int = 5):
        """
        初始化黑名单检查器

        Args:
            db_config: 数据库配置
            cache_minutes: 缓存时间（分钟），默认5分钟
        """
        self.db_config = db_config
        self.cache_minutes = cache_minutes

        # 缓存
        self.blacklist_cache: List[Dict] = []
        self.cache_updated_at: Optional[datetime] = None

        # 初始加载
        self._reload_blacklist()

        logger.info(f"✅ 信号黑名单检查器已初始化 | 缓存时间:{cache_minutes}分钟 | 当前黑名单:{len(self.blacklist_cache)}条")

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            **self.db_config,
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def _reload_blacklist(self):
        """重新加载黑名单（从数据库）"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # 只加载激活的黑名单
            cursor.execute("""
                SELECT id, signal_type, position_side, reason, win_rate, total_loss, order_count, notes
                FROM signal_blacklist
                WHERE is_active = 1
                ORDER BY id DESC
            """)

            self.blacklist_cache = cursor.fetchall()
            self.cache_updated_at = datetime.now()

            cursor.close()
            conn.close()

            logger.info(f"🔄 信号黑名单已重新加载 | 共{len(self.blacklist_cache)}条记录")

        except Exception as e:
            logger.error(f"❌ 加载信号黑名单失败: {e}")
            # 保持旧缓存
            if not self.blacklist_cache:
                self.blacklist_cache = []

    def _check_cache_expiry(self):
        """检查缓存是否过期，如果过期则重新加载"""
        if not self.cache_updated_at:
            self._reload_blacklist()
            return

        elapsed = (datetime.now() - self.cache_updated_at).total_seconds() / 60
        if elapsed >= self.cache_minutes:
            self._reload_blacklist()

    def is_blacklisted(self, signal_combination: str, direction: str) -> Tuple[bool, Optional[str]]:
        """
        检查信号组合是否在黑名单中

        Args:
            signal_combination: 信号组合字符串，例如 "breakdown_short+跌势3%+高波动"
            direction: 方向，'LONG' 或 'SHORT'

        Returns:
            (是否黑名单, 原因)
        """
        # 检查缓存是否过期
        self._check_cache_expiry()

        if not signal_combination:
            return False, None

        # 遍历黑名单
        for blacklist_item in self.blacklist_cache:
            pattern = blacklist_item['signal_type']
            item_side = blacklist_item['position_side']

            # 方向不匹配则跳过
            if item_side != direction:
                continue

            # 模式匹配检查
            if self._pattern_match(pattern, signal_combination):
                reason = blacklist_item['reason']
                win_rate = blacklist_item['win_rate'] * 100 if blacklist_item['win_rate'] else 0
                total_loss = blacklist_item['total_loss'] or 0
                order_count = blacklist_item['order_count'] or 0

                detail = f"黑名单匹配: {pattern} | {reason} | 胜率{win_rate:.1f}% | 亏损{total_loss:.2f}U ({order_count}单)"
                return True, detail

        return False, None

    def _pattern_match(self, pattern: str, signal_combination: str) -> bool:
        """
        模式匹配（支持精确匹配和子串匹配）

        Args:
            pattern: 黑名单模式
            signal_combination: 信号组合

        Returns:
            是否匹配
        """
        if not pattern or not signal_combination:
            return False

        # 1. 精确匹配
        if pattern == signal_combination:
            return True

        # 2. 子串匹配（pattern是signal_combination的子串）
        # 例如: pattern="breakdown_short" 匹配 "breakdown_short+跌势3%"
        if pattern in signal_combination:
            return True

        # 3. 模糊匹配（处理+号分隔的组件）
        # 将信号组合拆分为组件
        signal_components = set(signal_combination.split('+'))
        pattern_components = set(pattern.split('+'))

        # 如果pattern的所有组件都在signal中，则匹配
        if pattern_components.issubset(signal_components):
            return True

        return False

    def get_margin_multiplier(self, signal_combination: str, direction: str, historical_win_rate: Optional[float] = None) -> float:
        """
        根据信号质量获取开仓金额乘数

        Args:
            signal_combination: 信号组合
            direction: 方向
            historical_win_rate: 历史胜率（0-1之间），如果提供则用于判断

        Returns:
            乘数（0.5表示减半，1.0表示正常）
        """
        # 1. 如果在黑名单中，直接返回0（不开仓）
        is_blocked, _ = self.is_blacklisted(signal_combination, direction)
        if is_blocked:
            return 0.0

        # 2. 如果有历史胜率数据，且胜率<50%，减半
        if historical_win_rate is not None and historical_win_rate < 0.5:
            return 0.5

        # 3. 默认正常开仓
        return 1.0

    def force_reload(self):
        """强制重新加载黑名单（忽略缓存）"""
        logger.info("🔄 强制重新加载信号黑名单...")
        self._reload_blacklist()

    def get_stats(self) -> Dict:
        """获取黑名单统计信息"""
        self._check_cache_expiry()

        return {
            'total_count': len(self.blacklist_cache),
            'cache_age_minutes': (datetime.now() - self.cache_updated_at).total_seconds() / 60 if self.cache_updated_at else 0,
            'last_updated': self.cache_updated_at.isoformat() if self.cache_updated_at else None,
            'blacklist': [
                {
                    'pattern': item['signal_type'],
                    'side': item['position_side'],
                    'reason': item['reason'],
                    'win_rate': item['win_rate'] * 100 if item['win_rate'] else 0,
                    'total_loss': item['total_loss'] or 0,
                    'order_count': item['order_count'] or 0
                }
                for item in self.blacklist_cache
            ]
        }
