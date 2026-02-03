"""
交易模式切换管理器
管理趋势模式和震荡模式之间的切换
"""

import pymysql
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class TradingModeSwitcher:
    """交易模式切换管理器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def get_current_mode(self, account_id: int, trading_type: str) -> Optional[Dict]:
        """
        获取当前交易模式

        Args:
            account_id: 账户ID (2=U本位, 3=币本位)
            trading_type: 交易类型 (usdt_futures/coin_futures)

        Returns:
            模式配置字典或None
        """
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
            new_mode: 新模式 (trend/range/auto)
            trigger: 触发方式 (manual/auto/schedule)
            reason: 切换原因
            big4_signal: Big4信号
            big4_strength: Big4强度
            switched_by: 操作人

        Returns:
            是否切换成功
        """
        try:
            # 检查冷却时间
            current_config = self.get_current_mode(account_id, trading_type)
            if not current_config:
                logger.error(f"未找到账户配置: account_id={account_id}, trading_type={trading_type}")
                return False

            # 如果是自动切换,检查冷却时间
            if trigger == 'auto':
                if current_config['last_switch_time']:
                    last_switch = current_config['last_switch_time']
                    cooldown_minutes = current_config['switch_cooldown_minutes']
                    cooldown_end = last_switch + timedelta(minutes=cooldown_minutes)

                    if datetime.now() < cooldown_end:
                        remaining = (cooldown_end - datetime.now()).total_seconds() / 60
                        logger.warning(f"模式切换冷却中,剩余{remaining:.1f}分钟")
                        return False

            old_mode = current_config['mode_type']

            # 相同模式不切换
            if old_mode == new_mode:
                logger.info(f"当前已是{new_mode}模式,无需切换")
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

            logger.info(f"✅ 模式切换成功: {old_mode} → {new_mode} (原因: {reason})")
            return True

        except Exception as e:
            logger.error(f"模式切换失败: {e}")
            return False

    def auto_switch_check(
        self,
        account_id: int,
        trading_type: str,
        big4_signal: str,
        big4_strength: float
    ) -> Optional[str]:
        """
        自动模式切换检查

        Args:
            account_id: 账户ID
            trading_type: 交易类型
            big4_signal: Big4信号
            big4_strength: Big4强度

        Returns:
            建议的模式或None(不需要切换)
        """
        current_config = self.get_current_mode(account_id, trading_type)
        if not current_config:
            return None

        # 只有设置为auto模式才自动切换
        if current_config['mode_type'] != 'auto':
            return None

        if not current_config['auto_switch_enabled']:
            return None

        # 震荡市判断: NEUTRAL信号且强度<50
        is_ranging = (big4_signal == 'NEUTRAL' and big4_strength < 50)

        # 趋势市判断: BULLISH或BEARISH且强度>=60
        is_trending = (big4_signal in ['BULLISH', 'BEARISH'] and big4_strength >= 60)

        current_mode = current_config['mode_type']

        if is_ranging and current_mode != 'range':
            return 'range'
        elif is_trending and current_mode != 'trend':
            return 'trend'

        return None

    def update_mode_parameters(
        self,
        account_id: int,
        trading_type: str,
        parameters: Dict
    ) -> bool:
        """
        更新模式参数

        Args:
            account_id: 账户ID
            trading_type: 交易类型
            parameters: 参数字典

        Returns:
            是否更新成功
        """
        try:
            conn = pymysql.connect(**self.db_config)
            cursor = conn.cursor()

            # 构建UPDATE语句
            update_fields = []
            values = []

            param_map = {
                'range_min_score': 'range_min_score',
                'range_position_size': 'range_position_size',
                'range_max_positions': 'range_max_positions',
                'range_take_profit': 'range_take_profit',
                'range_stop_loss': 'range_stop_loss',
                'range_max_hold_hours': 'range_max_hold_hours',
                'auto_switch_enabled': 'auto_switch_enabled',
                'switch_cooldown_minutes': 'switch_cooldown_minutes'
            }

            for param_key, db_field in param_map.items():
                if param_key in parameters:
                    update_fields.append(f"{db_field} = %s")
                    values.append(parameters[param_key])

            if not update_fields:
                return True

            # 添加WHERE条件的值
            values.extend([account_id, trading_type])

            sql = f"""
                UPDATE trading_mode_config
                SET {', '.join(update_fields)}, updated_at = NOW()
                WHERE account_id = %s AND trading_type = %s
            """

            cursor.execute(sql, values)
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 更新模式参数成功: account_id={account_id}")
            return True

        except Exception as e:
            logger.error(f"更新模式参数失败: {e}")
            return False

    def get_mode_statistics(self, account_id: int, trading_type: str, days: int = 7) -> Dict:
        """
        获取模式切换统计

        Args:
            account_id: 账户ID
            trading_type: 交易类型
            days: 统计天数

        Returns:
            统计信息字典
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 切换次数统计
            cursor.execute("""
                SELECT
                    COUNT(*) as total_switches,
                    SUM(CASE WHEN switch_trigger = 'auto' THEN 1 ELSE 0 END) as auto_switches,
                    SUM(CASE WHEN switch_trigger = 'manual' THEN 1 ELSE 0 END) as manual_switches
                FROM trading_mode_switch_log
                WHERE account_id = %s
                AND trading_type = %s
                AND switched_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            """, (account_id, trading_type, days))

            switch_stats = cursor.fetchone()

            # 模式时长统计
            cursor.execute("""
                SELECT
                    to_mode,
                    COUNT(*) as switch_count,
                    AVG(TIMESTAMPDIFF(MINUTE,
                        switched_at,
                        LEAD(switched_at) OVER (ORDER BY switched_at)
                    )) as avg_duration_minutes
                FROM trading_mode_switch_log
                WHERE account_id = %s
                AND trading_type = %s
                AND switched_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
                GROUP BY to_mode
            """, (account_id, trading_type, days))

            mode_durations = cursor.fetchall()

            cursor.close()
            conn.close()

            return {
                'total_switches': switch_stats['total_switches'] or 0,
                'auto_switches': switch_stats['auto_switches'] or 0,
                'manual_switches': switch_stats['manual_switches'] or 0,
                'mode_durations': mode_durations or []
            }

        except Exception as e:
            logger.error(f"获取模式统计失败: {e}")
            return {}
