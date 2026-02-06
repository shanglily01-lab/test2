"""
紧急熔断机制 - 防止类似01:38灾难再次发生
"""

import pymysql
from datetime import datetime, timedelta
from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class EmergencyCircuitBreaker:
    """紧急熔断器"""

    def __init__(self, db_config: dict):
        self.db_config = db_config

        # 熔断配置
        self.MAX_POSITIONS_PER_MINUTE = 3  # 每分钟最多开3仓
        self.MAX_STOP_LOSS_IN_WINDOW = 5    # 10分钟内最多5次止损
        self.STOP_LOSS_WINDOW_MINUTES = 10  # 止损检测窗口
        self.CIRCUIT_BREAK_DURATION = 30    # 熔断持续时间(分钟)

        # 快速反弹检测
        self.RAPID_REVERSAL_THRESHOLD = 0.02  # 2%反弹视为快速反转
        self.REVERSAL_CHECK_MINUTES = 5       # 检测最近5分钟

    def check_opening_rate_limit(self, account_id: int) -> Dict:
        """
        检查开仓速率限制

        Returns:
            {
                'allowed': bool,
                'reason': str,
                'current_count': int,
                'limit': int
            }
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 检查最近1分钟开仓数
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE account_id = %s
                AND open_time >= DATE_SUB(NOW(), INTERVAL 1 MINUTE)
            """, (account_id,))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            current_count = result['count'] if result else 0

            if current_count >= self.MAX_POSITIONS_PER_MINUTE:
                return {
                    'allowed': False,
                    'reason': f'开仓速率过快: 最近1分钟已开{current_count}仓，超过限制{self.MAX_POSITIONS_PER_MINUTE}',
                    'current_count': current_count,
                    'limit': self.MAX_POSITIONS_PER_MINUTE
                }

            return {
                'allowed': True,
                'reason': 'OK',
                'current_count': current_count,
                'limit': self.MAX_POSITIONS_PER_MINUTE
            }

        except Exception as e:
            logger.error(f"检查开仓速率失败: {e}")
            # 出错时保守处理，拒绝开仓
            return {
                'allowed': False,
                'reason': f'检查失败: {e}',
                'current_count': 0,
                'limit': self.MAX_POSITIONS_PER_MINUTE
            }

    def check_stop_loss_cascade(self, account_id: int) -> Dict:
        """
        检查连续止损熔断

        Returns:
            {
                'circuit_break': bool,
                'reason': str,
                'stop_loss_count': int,
                'threshold': int,
                'break_until': datetime or None
            }
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 检查最近N分钟的止损次数
            cursor.execute("""
                SELECT COUNT(*) as count
                FROM futures_positions
                WHERE account_id = %s
                AND close_time >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
                AND status = 'closed'
                AND notes LIKE '%%止损%%'
            """, (account_id, self.STOP_LOSS_WINDOW_MINUTES))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            stop_loss_count = result['count'] if result else 0

            if stop_loss_count >= self.MAX_STOP_LOSS_IN_WINDOW:
                break_until = datetime.now() + timedelta(minutes=self.CIRCUIT_BREAK_DURATION)
                return {
                    'circuit_break': True,
                    'reason': f'连续止损过多: {self.STOP_LOSS_WINDOW_MINUTES}分钟内{stop_loss_count}次止损，'
                             f'超过阈值{self.MAX_STOP_LOSS_IN_WINDOW}，熔断{self.CIRCUIT_BREAK_DURATION}分钟',
                    'stop_loss_count': stop_loss_count,
                    'threshold': self.MAX_STOP_LOSS_IN_WINDOW,
                    'break_until': break_until
                }

            return {
                'circuit_break': False,
                'reason': 'OK',
                'stop_loss_count': stop_loss_count,
                'threshold': self.MAX_STOP_LOSS_IN_WINDOW,
                'break_until': None
            }

        except Exception as e:
            logger.error(f"检查连续止损失败: {e}")
            return {
                'circuit_break': True,
                'reason': f'检查失败: {e}',
                'stop_loss_count': 0,
                'threshold': self.MAX_STOP_LOSS_IN_WINDOW,
                'break_until': datetime.now() + timedelta(minutes=5)
            }

    def check_rapid_market_reversal(self, symbol: str, position_side: str) -> Dict:
        """
        检查市场是否正在快速反转

        Returns:
            {
                'reversal_detected': bool,
                'reason': str,
                'price_change_pct': float
            }
        """
        try:
            conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
            cursor = conn.cursor()

            # 获取最近5分钟的价格变化
            cursor.execute("""
                SELECT
                    MIN(low_price) as lowest,
                    MAX(high_price) as highest,
                    (SELECT close_price FROM klines_1m
                     WHERE symbol = %s
                     ORDER BY open_time DESC LIMIT 1) as current_price
                FROM klines_1m
                WHERE symbol = %s
                AND open_time >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            """, (symbol, symbol, self.REVERSAL_CHECK_MINUTES))

            result = cursor.fetchone()
            cursor.close()
            conn.close()

            if not result or not result['lowest'] or not result['current_price']:
                return {
                    'reversal_detected': False,
                    'reason': '数据不足',
                    'price_change_pct': 0
                }

            # 计算反弹幅度
            if position_side == 'SHORT':
                # 做空时检查是否快速反弹
                price_change = (result['current_price'] - result['lowest']) / result['lowest']
            else:
                # 做多时检查是否快速下跌
                price_change = (result['highest'] - result['current_price']) / result['highest']

            if price_change >= self.RAPID_REVERSAL_THRESHOLD:
                return {
                    'reversal_detected': True,
                    'reason': f'{self.REVERSAL_CHECK_MINUTES}分钟内{"反弹" if position_side == "SHORT" else "下跌"}'
                             f'{price_change*100:.2f}%，可能正在反转',
                    'price_change_pct': price_change * 100
                }

            return {
                'reversal_detected': False,
                'reason': 'OK',
                'price_change_pct': price_change * 100
            }

        except Exception as e:
            logger.error(f"检查市场反转失败: {e}")
            return {
                'reversal_detected': False,
                'reason': f'检查失败: {e}',
                'price_change_pct': 0
            }

    def comprehensive_check(
        self,
        account_id: int,
        symbol: str,
        position_side: str
    ) -> Dict:
        """
        综合熔断检查

        Returns:
            {
                'allowed': bool,
                'reason': str,
                'checks': {
                    'rate_limit': {...},
                    'stop_loss_cascade': {...},
                    'market_reversal': {...}
                }
            }
        """
        # 1. 开仓速率检查
        rate_check = self.check_opening_rate_limit(account_id)

        # 2. 连续止损检查
        stop_loss_check = self.check_stop_loss_cascade(account_id)

        # 3. 市场反转检查
        reversal_check = self.check_rapid_market_reversal(symbol, position_side)

        # 汇总结果
        checks = {
            'rate_limit': rate_check,
            'stop_loss_cascade': stop_loss_check,
            'market_reversal': reversal_check
        }

        # 任何一项失败都拒绝开仓
        if not rate_check['allowed']:
            return {
                'allowed': False,
                'reason': rate_check['reason'],
                'checks': checks
            }

        if stop_loss_check['circuit_break']:
            return {
                'allowed': False,
                'reason': stop_loss_check['reason'],
                'checks': checks
            }

        if reversal_check['reversal_detected']:
            return {
                'allowed': False,
                'reason': reversal_check['reason'],
                'checks': checks
            }

        return {
            'allowed': True,
            'reason': 'All checks passed',
            'checks': checks
        }


# 测试代码
if __name__ == '__main__':
    from app.utils.config_loader import load_config

    config = load_config()
    db_config = config.get('database', {}).get('mysql', {})

    breaker = EmergencyCircuitBreaker(db_config)

    print("\n🛡️ 紧急熔断机制测试\n")
    print("="*80)

    # 测试开仓速率
    print("\n1. 开仓速率检查:")
    rate_result = breaker.check_opening_rate_limit(account_id=2)
    print(f"   允许: {rate_result['allowed']}")
    print(f"   原因: {rate_result['reason']}")
    print(f"   当前: {rate_result['current_count']}/{rate_result['limit']}")

    # 测试连续止损
    print("\n2. 连续止损检查:")
    stop_loss_result = breaker.check_stop_loss_cascade(account_id=2)
    print(f"   熔断: {stop_loss_result['circuit_break']}")
    print(f"   原因: {stop_loss_result['reason']}")
    print(f"   止损: {stop_loss_result['stop_loss_count']}/{stop_loss_result['threshold']}")

    # 测试市场反转
    print("\n3. 市场反转检查 (BTC/USDT SHORT):")
    reversal_result = breaker.check_rapid_market_reversal('BTC/USDT', 'SHORT')
    print(f"   检测到反转: {reversal_result['reversal_detected']}")
    print(f"   原因: {reversal_result['reason']}")
    print(f"   涨幅: {reversal_result['price_change_pct']:.2f}%")

    print("\n" + "="*80)
    print("✅ 测试完成\n")
