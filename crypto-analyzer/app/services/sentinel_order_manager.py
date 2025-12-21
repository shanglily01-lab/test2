"""
哨兵单管理器 (Sentinel Order Manager)

功能：
- 熔断后创建虚拟哨兵单（不实际开仓）
- 监控哨兵单的止盈/止损触发
- 统计连续盈利次数，达到2次连续盈利后恢复正常交易
- 恢复时删除所有未平仓的哨兵单

规则：
- 哨兵单跟随策略信号创建
- 止损止盈参数与策略配置一致
- 连续2单盈利即可恢复正常
- 亏损后重置连续盈利计数
"""

import pymysql
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple
from decimal import Decimal

logger = logging.getLogger(__name__)


class SentinelOrderManager:
    """哨兵单管理器"""

    CONSECUTIVE_WINS_REQUIRED = 2  # 连续盈利次数要求

    def __init__(self, db_config: Dict):
        """
        初始化哨兵单管理器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        # 连续盈利计数: {'long': 0, 'short': 0}
        self._consecutive_wins: Dict[str, int] = {
            'long': 0,
            'short': 0
        }

    def _get_local_time(self) -> datetime:
        """获取本地时间（新加坡时区 UTC+8）"""
        local_tz = timezone(timedelta(hours=8))
        return datetime.now(local_tz).replace(tzinfo=None)

    def _get_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config.get('host', 'localhost'),
            port=int(self.db_config.get('port', 3306)),
            user=self.db_config.get('user', 'root'),
            password=self.db_config.get('password', ''),
            database=self.db_config.get('database', 'binance-data'),
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def create_sentinel_order(
        self,
        direction: str,
        symbol: str,
        entry_price: float,
        stop_loss_pct: float,
        take_profit_pct: float,
        strategy_id: int = None
    ) -> Optional[int]:
        """
        创建哨兵单

        Args:
            direction: 方向 'long' 或 'short'
            symbol: 交易对
            entry_price: 入场价（当前市价）
            stop_loss_pct: 止损百分比
            take_profit_pct: 止盈百分比
            strategy_id: 策略ID

        Returns:
            哨兵单ID，失败返回None
        """
        try:
            # 计算止损止盈价格
            if direction == 'long':
                stop_loss_price = entry_price * (1 - stop_loss_pct / 100)
                take_profit_price = entry_price * (1 + take_profit_pct / 100)
            else:  # short
                stop_loss_price = entry_price * (1 + stop_loss_pct / 100)
                take_profit_price = entry_price * (1 - take_profit_pct / 100)

            connection = self._get_connection()
            now = self._get_local_time()

            with connection.cursor() as cursor:
                cursor.execute("""
                    INSERT INTO sentinel_orders
                    (direction, symbol, entry_price, stop_loss_price, take_profit_price,
                     stop_loss_pct, take_profit_pct, status, strategy_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'open', %s, %s)
                """, (
                    direction, symbol, entry_price, stop_loss_price, take_profit_price,
                    stop_loss_pct, take_profit_pct, strategy_id, now
                ))
                connection.commit()
                sentinel_id = cursor.lastrowid

            connection.close()

            logger.info(f"🔭 [哨兵] 创建哨兵单 #{sentinel_id}: {symbol} {direction.upper()} "
                       f"入场={entry_price:.4f}, 止损={stop_loss_price:.4f}, 止盈={take_profit_price:.4f}")

            return sentinel_id

        except Exception as e:
            logger.error(f"[哨兵] 创建哨兵单失败: {e}")
            return None

    def check_and_update_sentinel_orders(self, current_prices: Dict[str, float]) -> Dict[str, any]:
        """
        检查并更新所有活跃哨兵单的状态

        Args:
            current_prices: 当前价格字典 {symbol: price}

        Returns:
            更新结果 {'closed': [...], 'recovery': {'long': bool, 'short': bool}}
        """
        result = {
            'closed': [],
            'recovery': {'long': False, 'short': False}
        }

        try:
            connection = self._get_connection()
            now = self._get_local_time()

            # 获取所有活跃的哨兵单
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT id, direction, symbol, entry_price, stop_loss_price, take_profit_price
                    FROM sentinel_orders
                    WHERE status = 'open'
                    ORDER BY created_at ASC
                """)
                open_orders = cursor.fetchall()

            if not open_orders:
                connection.close()
                return result

            for order in open_orders:
                symbol = order['symbol']
                current_price = current_prices.get(symbol)

                if current_price is None:
                    continue

                direction = order['direction']
                stop_loss_price = float(order['stop_loss_price'])
                take_profit_price = float(order['take_profit_price'])

                # 检查是否触发止盈/止损
                close_reason = None
                new_status = None

                if direction == 'long':
                    if current_price <= stop_loss_price:
                        close_reason = 'stop_loss'
                        new_status = 'loss'
                    elif current_price >= take_profit_price:
                        close_reason = 'take_profit'
                        new_status = 'win'
                else:  # short
                    if current_price >= stop_loss_price:
                        close_reason = 'stop_loss'
                        new_status = 'loss'
                    elif current_price <= take_profit_price:
                        close_reason = 'take_profit'
                        new_status = 'win'

                if close_reason:
                    # 更新哨兵单状态
                    with connection.cursor() as cursor:
                        cursor.execute("""
                            UPDATE sentinel_orders
                            SET status = %s, close_price = %s, close_reason = %s, closed_at = %s
                            WHERE id = %s
                        """, (new_status, current_price, close_reason, now, order['id']))
                    connection.commit()

                    result['closed'].append({
                        'id': order['id'],
                        'direction': direction,
                        'symbol': symbol,
                        'status': new_status,
                        'close_reason': close_reason,
                        'entry_price': float(order['entry_price']),
                        'close_price': current_price
                    })

                    # 更新连续盈利计数
                    if new_status == 'win':
                        self._consecutive_wins[direction] += 1
                        logger.info(f"✅ [哨兵] {symbol} {direction.upper()} 止盈! "
                                   f"连续盈利: {self._consecutive_wins[direction]}/{self.CONSECUTIVE_WINS_REQUIRED}")

                        # 检查是否达到恢复条件
                        if self._consecutive_wins[direction] >= self.CONSECUTIVE_WINS_REQUIRED:
                            result['recovery'][direction] = True
                            logger.info(f"🎉 [哨兵] {direction.upper()} 方向连续{self._consecutive_wins[direction]}单盈利，触发恢复!")
                    else:
                        # 亏损，重置连续盈利计数
                        self._consecutive_wins[direction] = 0
                        logger.info(f"❌ [哨兵] {symbol} {direction.upper()} 止损! 连续盈利计数重置为0")

            connection.close()

        except Exception as e:
            logger.error(f"[哨兵] 检查哨兵单失败: {e}")

        return result

    def clear_open_sentinels(self, direction: str = None) -> int:
        """
        清除所有未平仓的哨兵单（恢复正常交易时调用）

        Args:
            direction: 方向，None表示清除所有方向

        Returns:
            删除的数量
        """
        try:
            connection = self._get_connection()

            with connection.cursor() as cursor:
                if direction:
                    cursor.execute("""
                        DELETE FROM sentinel_orders
                        WHERE status = 'open' AND direction = %s
                    """, (direction,))
                else:
                    cursor.execute("""
                        DELETE FROM sentinel_orders
                        WHERE status = 'open'
                    """)
                deleted_count = cursor.rowcount

            connection.commit()
            connection.close()

            if deleted_count > 0:
                logger.info(f"[哨兵] 已清除 {deleted_count} 个未平仓哨兵单"
                           f"{f' ({direction.upper()}方向)' if direction else ''}")

            # 重置连续盈利计数
            if direction:
                self._consecutive_wins[direction] = 0
            else:
                self._consecutive_wins = {'long': 0, 'short': 0}

            return deleted_count

        except Exception as e:
            logger.error(f"[哨兵] 清除哨兵单失败: {e}")
            return 0

    def get_open_sentinels(self, direction: str = None) -> List[Dict]:
        """
        获取所有未平仓的哨兵单

        Args:
            direction: 方向过滤，None表示所有方向

        Returns:
            哨兵单列表
        """
        try:
            connection = self._get_connection()

            with connection.cursor() as cursor:
                if direction:
                    cursor.execute("""
                        SELECT * FROM sentinel_orders
                        WHERE status = 'open' AND direction = %s
                        ORDER BY created_at ASC
                    """, (direction,))
                else:
                    cursor.execute("""
                        SELECT * FROM sentinel_orders
                        WHERE status = 'open'
                        ORDER BY created_at ASC
                    """)
                orders = cursor.fetchall()

            connection.close()
            return orders

        except Exception as e:
            logger.error(f"[哨兵] 获取哨兵单失败: {e}")
            return []

    def get_sentinel_stats(self, direction: str = None) -> Dict:
        """
        获取哨兵单统计信息

        Args:
            direction: 方向过滤

        Returns:
            统计信息
        """
        try:
            connection = self._get_connection()

            with connection.cursor() as cursor:
                # 统计各状态数量
                if direction:
                    cursor.execute("""
                        SELECT status, COUNT(*) as count
                        FROM sentinel_orders
                        WHERE direction = %s
                        GROUP BY status
                    """, (direction,))
                else:
                    cursor.execute("""
                        SELECT direction, status, COUNT(*) as count
                        FROM sentinel_orders
                        GROUP BY direction, status
                    """)
                stats = cursor.fetchall()

            connection.close()

            # 整理统计结果
            result = {
                'long': {'open': 0, 'win': 0, 'loss': 0, 'consecutive_wins': self._consecutive_wins['long']},
                'short': {'open': 0, 'win': 0, 'loss': 0, 'consecutive_wins': self._consecutive_wins['short']}
            }

            for row in stats:
                if 'direction' in row:
                    d = row['direction']
                    s = row['status']
                    result[d][s] = row['count']
                else:
                    # 单方向查询
                    result[direction][row['status']] = row['count']

            return result

        except Exception as e:
            logger.error(f"[哨兵] 获取统计失败: {e}")
            return {
                'long': {'open': 0, 'win': 0, 'loss': 0, 'consecutive_wins': 0},
                'short': {'open': 0, 'win': 0, 'loss': 0, 'consecutive_wins': 0}
            }

    def get_consecutive_wins(self, direction: str) -> int:
        """获取指定方向的连续盈利次数"""
        return self._consecutive_wins.get(direction, 0)

    def reset_consecutive_wins(self, direction: str = None):
        """重置连续盈利计数"""
        if direction:
            self._consecutive_wins[direction] = 0
        else:
            self._consecutive_wins = {'long': 0, 'short': 0}

    def is_recovery_triggered(self, direction: str) -> bool:
        """检查是否触发恢复条件"""
        return self._consecutive_wins.get(direction, 0) >= self.CONSECUTIVE_WINS_REQUIRED
