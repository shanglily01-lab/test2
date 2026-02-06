"""
安全的交易模式切换管理器
在原有TradingModeSwitcher基础上增加多重安全保护
"""

import pymysql
from datetime import datetime, timedelta
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class SafeModeSwitcher:
    """安全的交易模式切换管理器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 安全配置
        self.CONFIRMATION_COUNT = 3  # 需要连续3次确认
        self.CONFIRMATION_INTERVAL_MINUTES = 15  # 每次检测间隔15分钟
        self.MIN_COOLDOWN_MINUTES = 360  # 最小冷却期6小时

        # 提高阈值，增加缓冲区
        self.RANGE_THRESHOLD = 40  # 震荡模式: 强度 < 40
        self.TREND_THRESHOLD = 70  # 趋势模式: 强度 >= 70
        # 缓冲区: 40-70 之间不切换

    def get_current_mode(self, account_id: int, trading_type: str) -> Optional[Dict]:
        """获取当前交易模式"""
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM trading_mode_config
                WHERE account_id = %s
                AND trading_type = %s
            """, (account_id, trading_type))

            config = cursor.fetchone()
            cursor.close()
            conn.close()

            return config

        except Exception as e:
            logger.error(f"获取交易模式失败: {e}")
            return None

    def check_open_positions(self, account_id: int) -> int:
        """
        检查当前是否有持仓

        Returns:
            持仓数量
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE account_id = %s
                AND status = 'open'
            """, (account_id,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            return result['count'] if result else 0

        except Exception as e:
            logger.error(f"检查持仓失败: {e}")
            return 0

    def get_recent_big4_signals(self, count: int = 3) -> List[Dict]:
        """
        获取最近N次Big4检测结果

        Args:
            count: 获取数量

        Returns:
            Big4信号列表，按时间倒序
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    overall_signal,
                    signal_strength,
                    created_at
                FROM big4_trend_history
                ORDER BY created_at DESC
                LIMIT %s
            """, (count,))

            results = cursor.fetchall()
            cursor.close()
            conn.close()

            return results

        except Exception as e:
            logger.error(f"获取Big4历史失败: {e}")
            return []

    def validate_confirmation(self, target_mode: str) -> bool:
        """
        验证模式切换确认
        需要最近3次Big4检测都满足切换条件

        Args:
            target_mode: 目标模式 (trend/range)

        Returns:
            是否通过确认
        """
        recent_signals = self.get_recent_big4_signals(self.CONFIRMATION_COUNT)

        if len(recent_signals) < self.CONFIRMATION_COUNT:
            logger.warning(f"Big4历史记录不足{self.CONFIRMATION_COUNT}条，无法确认切换")
            return False

        # 检查时间间隔（确保是独立的检测，而不是同一次检测）
        for i in range(len(recent_signals) - 1):
            time_diff = (recent_signals[i]['created_at'] - recent_signals[i+1]['created_at']).total_seconds() / 60
            if time_diff < self.CONFIRMATION_INTERVAL_MINUTES - 2:  # 允许2分钟误差
                logger.warning(f"Big4检测时间间隔过短({time_diff:.1f}分钟)，可能是重复检测")
                return False

        # 验证是否连续满足条件
        if target_mode == 'range':
            # 震荡模式：需要连续3次都是 NEUTRAL 且强度 < 40
            for signal in recent_signals:
                if signal['overall_signal'] != 'NEUTRAL' or signal['signal_strength'] >= self.RANGE_THRESHOLD:
                    logger.info(f"❌ 震荡确认失败: {signal['overall_signal']}({signal['signal_strength']:.1f})")
                    return False
            logger.info(f"✅ 震荡模式确认通过: 连续{self.CONFIRMATION_COUNT}次 NEUTRAL + 强度<{self.RANGE_THRESHOLD}")
            return True

        elif target_mode == 'trend':
            # 趋势模式：需要连续3次都是 BULLISH/BEARISH 且强度 >= 70
            for signal in recent_signals:
                if signal['overall_signal'] not in ['BULLISH', 'BEARISH'] or signal['signal_strength'] < self.TREND_THRESHOLD:
                    logger.info(f"❌ 趋势确认失败: {signal['overall_signal']}({signal['signal_strength']:.1f})")
                    return False
            logger.info(f"✅ 趋势模式确认通过: 连续{self.CONFIRMATION_COUNT}次 趋势信号 + 强度>={self.TREND_THRESHOLD}")
            return True

        return False

    def safe_auto_switch_check(
        self,
        account_id: int,
        trading_type: str,
        big4_signal: str,
        big4_strength: float
    ) -> Optional[Dict]:
        """
        安全的自动模式切换检查

        Args:
            account_id: 账户ID
            trading_type: 交易类型
            big4_signal: 当前Big4信号
            big4_strength: 当前Big4强度

        Returns:
            切换建议字典或None
            {
                'suggested_mode': 'trend' or 'range',
                'reason': '切换原因',
                'safety_checks': {...}  # 安全检查结果
            }
        """
        current_config = self.get_current_mode(account_id, trading_type)
        if not current_config:
            return None

        # 检查1: 是否启用自动切换
        if not current_config.get('auto_switch_enabled'):
            return None

        current_mode = current_config['mode_type']

        # 检查2: 持仓检查 - 有持仓时的策略
        open_positions_count = self.check_open_positions(account_id)

        # 🔥 修改: 允许在Big4明显变化时，即使有持仓也能切换
        # 原因: 高位震荡时用趋势模式的3%止损非常危险
        if open_positions_count > 0:
            # 判断Big4是否发生显著变化（需要紧急切换）
            needs_urgent_switch = False

            # 情况1: 趋势市→中性市 (强度从>60降到<40)
            if current_mode == 'trend' and big4_strength < self.RANGE_THRESHOLD:
                needs_urgent_switch = True
                logger.warning(
                    f"⚠️ [SAFE-MODE-SWITCH] Big4转为震荡({big4_strength:.1f}), "
                    f"虽有{open_positions_count}个持仓但允许切换(保护资金)"
                )

            # 情况2: 中性市→趋势市 (强度从<40升到>70)
            elif current_mode == 'range' and big4_strength >= self.TREND_THRESHOLD:
                needs_urgent_switch = True
                logger.warning(
                    f"⚠️ [SAFE-MODE-SWITCH] Big4转为趋势({big4_strength:.1f}), "
                    f"虽有{open_positions_count}个持仓但允许切换(抓住机会)"
                )

            # 如果不是紧急切换，则禁止
            if not needs_urgent_switch:
                logger.info(f"📊 [SAFE-MODE-SWITCH] 有{open_positions_count}个持仓且无紧急切换需求，保持{current_mode}模式")
                return None

        # 检查3: 冷却期检查
        if current_config.get('last_switch_time'):
            last_switch = current_config['last_switch_time']
            cooldown_minutes = max(
                current_config.get('switch_cooldown_minutes', 120),
                self.MIN_COOLDOWN_MINUTES
            )
            cooldown_end = last_switch + timedelta(minutes=cooldown_minutes)

            if datetime.now() < cooldown_end:
                remaining = (cooldown_end - datetime.now()).total_seconds() / 60
                logger.info(f"⏳ [SAFE-MODE-SWITCH] 冷却期中，剩余{remaining:.1f}分钟")
                return None

        # 检查4: 判断建议的模式（使用更严格的阈值）
        suggested_mode = None

        # 震荡市判断: NEUTRAL 且强度 < 40
        if big4_signal == 'NEUTRAL' and big4_strength < self.RANGE_THRESHOLD:
            if current_mode != 'range':
                suggested_mode = 'range'

        # 趋势市判断: BULLISH/BEARISH 且强度 >= 70
        elif big4_signal in ['BULLISH', 'BEARISH'] and big4_strength >= self.TREND_THRESHOLD:
            if current_mode != 'trend':
                suggested_mode = 'trend'

        if not suggested_mode:
            # 在缓冲区内(40-70)，不做切换
            if self.RANGE_THRESHOLD <= big4_strength < self.TREND_THRESHOLD:
                logger.info(f"📊 [SAFE-MODE-SWITCH] 强度在缓冲区({big4_strength:.1f})，保持{current_mode}模式")
            return None

        # 检查5: 连续确认验证
        if not self.validate_confirmation(suggested_mode):
            logger.warning(f"⚠️ [SAFE-MODE-SWITCH] 切换到{suggested_mode}模式未通过连续确认")
            return None

        # 所有检查通过
        return {
            'suggested_mode': suggested_mode,
            'reason': f'Big4: {big4_signal}({big4_strength:.1f}), 连续{self.CONFIRMATION_COUNT}次确认',
            'safety_checks': {
                'auto_enabled': True,
                'open_positions': 0,
                'cooldown_passed': True,
                'confirmation_passed': True,
                'current_mode': current_mode,
                'target_mode': suggested_mode
            }
        }

    def switch_mode(
        self,
        account_id: int,
        trading_type: str,
        new_mode: str,
        trigger: str = 'manual',
        reason: str = '',
        big4_signal: str = '',
        big4_strength: float = 0,
        switched_by: str = 'system'
    ) -> bool:
        """
        切换交易模式

        Args:
            account_id: 账户ID
            trading_type: 交易类型
            new_mode: 新模式 (trend/range)
            trigger: 触发方式 (manual/auto)
            reason: 切换原因
            big4_signal: Big4信号
            big4_strength: Big4强度
            switched_by: 操作人

        Returns:
            是否切换成功
        """
        try:
            current_config = self.get_current_mode(account_id, trading_type)
            if not current_config:
                logger.error(f"未找到账户配置: account_id={account_id}")
                return False

            old_mode = current_config['mode_type']

            # 相同模式不切换
            if old_mode == new_mode:
                logger.info(f"当前已是{new_mode}模式，无需切换")
                return True

            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            # 更新模式配置
            cursor.execute("""
                UPDATE trading_mode_config
                SET mode_type = %s,
                    last_switch_time = NOW(),
                    updated_by = %s,
                    updated_at = NOW()
                WHERE account_id = %s
                AND trading_type = %s
            """, (new_mode, switched_by, account_id, trading_type))

            # 记录切换日志
            cursor.execute("""
                INSERT INTO trading_mode_switch_log (
                    account_id, trading_type,
                    from_mode, to_mode, switch_trigger,
                    big4_signal, big4_strength,
                    reason, switched_by, switched_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                )
            """, (
                account_id, trading_type,
                old_mode, new_mode, trigger,
                big4_signal, big4_strength,
                reason, switched_by
            ))

            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ [SAFE-MODE-SWITCH] 模式切换成功: {old_mode} → {new_mode} (原因: {reason})")
            return True

        except Exception as e:
            logger.error(f"模式切换失败: {e}")
            return False
