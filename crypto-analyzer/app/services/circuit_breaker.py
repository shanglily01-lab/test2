#!/usr/bin/env python3
"""
熔断机制 - Circuit Breaker
当最近3笔交易中有2笔硬止损时，触发熔断：
1. 暂停所有交易（模拟盘+实盘）
2. 平掉所有持仓
3. 4小时后自动恢复交易
"""

import pymysql
from loguru import logger
from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta
import json
import asyncio


class CircuitBreaker:
    """熔断机制"""

    # 熔断触发条件
    CHECK_RECENT_TRADES = 3  # 检查最近N笔交易
    HARD_STOP_THRESHOLD = 2  # 硬止损次数阈值
    COOLDOWN_HOURS = 4  # 熔断后冷却时间（小时）

    def __init__(self, db_config: dict):
        """
        初始化熔断器

        Args:
            db_config: 数据库配置
        """
        self.db_config = db_config
        self._is_active = False  # 熔断是否激活
        self._activated_at = None  # 熔断激活时间

        logger.info(f"熔断机制初始化: 最近{self.CHECK_RECENT_TRADES}笔中{self.HARD_STOP_THRESHOLD}笔硬止损触发, 冷却{self.COOLDOWN_HOURS}小时")

    def get_db_connection(self):
        """获取数据库连接"""
        return pymysql.connect(
            host=self.db_config['host'],
            port=self.db_config['port'],
            user=self.db_config['user'],
            password=self.db_config['password'],
            database=self.db_config['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor
        )

    def check_should_trigger(self, account_id: int = 2) -> Tuple[bool, str]:
        """
        检查是否应该触发熔断

        Args:
            account_id: 账户ID

        Returns:
            (是否触发, 触发原因)
        """
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查询最近N笔已平仓交易
            cursor.execute(f"""
                SELECT
                    symbol,
                    position_side,
                    realized_pnl,
                    notes,
                    close_time
                FROM futures_positions
                WHERE status = 'closed'
                AND account_id = %s
                ORDER BY close_time DESC
                LIMIT %s
            """, (account_id, self.CHECK_RECENT_TRADES))

            recent_trades = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(recent_trades) < self.CHECK_RECENT_TRADES:
                return False, ""

            # 统计硬止损次数
            hard_stop_count = 0
            hard_stop_trades = []

            for trade in recent_trades:
                notes = trade.get('notes', '') or ''
                if 'hard_stop_loss' in notes:
                    hard_stop_count += 1
                    hard_stop_trades.append({
                        'symbol': trade['symbol'],
                        'side': trade['position_side'],
                        'pnl': float(trade['realized_pnl']),
                        'time': trade['close_time']
                    })

            # 判断是否触发熔断
            if hard_stop_count >= self.HARD_STOP_THRESHOLD:
                reason = (
                    f"🔴 熔断触发: 最近{self.CHECK_RECENT_TRADES}笔交易中{hard_stop_count}笔硬止损\n"
                    f"硬止损记录:\n"
                )
                for t in hard_stop_trades:
                    reason += f"  - {t['symbol']} {t['side']}: ${t['pnl']:.2f} at {t['time']}\n"

                return True, reason

            return False, ""

        except Exception as e:
            logger.error(f"检查熔断条件失败: {e}", exc_info=True)
            return False, ""

    async def activate(self, reason: str, account_id: int = 2):
        """
        激活熔断机制

        Args:
            reason: 触发原因
            account_id: 账户ID
        """
        if self._is_active:
            logger.warning("熔断已激活，跳过重复激活")
            return

        logger.critical(f"\n{'=' * 80}\n⚠️  熔断机制激活\n{'=' * 80}")
        logger.critical(reason)

        self._is_active = True
        self._activated_at = datetime.now()

        # 1. 暂停所有策略
        await self._pause_all_strategies()

        # 2. 平掉所有持仓
        await self._close_all_positions(account_id)

        # 3. 记录熔断日志
        self._log_circuit_break(reason)

        logger.critical(
            f"🔴 熔断已激活\n"
            f"   - 所有策略已暂停\n"
            f"   - 所有持仓已平仓\n"
            f"   - 将在{self.COOLDOWN_HOURS}小时后({self._activated_at + timedelta(hours=self.COOLDOWN_HOURS)})自动恢复\n"
            f"{'=' * 80}"
        )

    async def _pause_all_strategies(self):
        """暂停所有策略（模拟盘+实盘）"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查询所有启用的策略
            cursor.execute("""
                SELECT id, name, market_type
                FROM trading_strategies
                WHERE enabled = 1
            """)

            strategies = cursor.fetchall()

            if not strategies:
                logger.info("没有启用的策略需要暂停")
                cursor.close()
                conn.close()
                return

            # 暂停所有策略
            cursor.execute("""
                UPDATE trading_strategies
                SET enabled = 0
                WHERE enabled = 1
            """)

            conn.commit()
            cursor.close()
            conn.close()

            logger.warning(f"已暂停 {len(strategies)} 个策略:")
            for s in strategies:
                logger.warning(f"  - [{s['market_type']}] {s['name']} (ID: {s['id']})")

        except Exception as e:
            logger.error(f"暂停策略失败: {e}", exc_info=True)

    async def _close_all_positions(self, account_id: int):
        """平掉所有持仓"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            # 查询所有开仓持仓
            cursor.execute("""
                SELECT id, symbol, position_side, strategy_id
                FROM futures_positions
                WHERE status = 'open'
                AND account_id = %s
            """, (account_id,))

            positions = cursor.fetchall()
            cursor.close()
            conn.close()

            if not positions:
                logger.info("没有持仓需要平仓")
                return

            logger.warning(f"开始平仓 {len(positions)} 个持仓...")

            # 导入策略执行器
            from app.services.strategy_executor_v2 import StrategyExecutorV2
            executor = StrategyExecutorV2(self.db_config)

            # 平仓所有持仓
            closed_count = 0
            for position in positions:
                try:
                    # 获取策略配置
                    conn = self.get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute("""
                        SELECT config FROM trading_strategies
                        WHERE id = %s
                    """, (position['strategy_id'],))

                    result = cursor.fetchone()
                    cursor.close()
                    conn.close()

                    if not result:
                        logger.warning(f"找不到策略配置 ID: {position['strategy_id']}")
                        continue

                    strategy = json.loads(result['config'])

                    # 执行平仓
                    close_reason = "circuit_breaker|熔断机制强制平仓"
                    await executor.execute_close_position(position, close_reason, strategy)

                    closed_count += 1
                    logger.info(f"✓ 已平仓: {position['symbol']} {position['position_side']}")

                except Exception as e:
                    logger.error(f"平仓失败 {position['symbol']}: {e}")

            logger.warning(f"平仓完成: {closed_count}/{len(positions)}")

        except Exception as e:
            logger.error(f"批量平仓失败: {e}", exc_info=True)

    def _log_circuit_break(self, reason: str):
        """记录熔断日志到文件"""
        try:
            import os
            log_dir = "logs/circuit_breaker"
            os.makedirs(log_dir, exist_ok=True)

            log_file = os.path.join(log_dir, f"circuit_break_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

            with open(log_file, 'w', encoding='utf-8') as f:
                f.write(f"熔断时间: {datetime.now()}\n")
                f.write(f"冷却时间: {self.COOLDOWN_HOURS}小时\n")
                f.write(f"恢复时间: {self._activated_at + timedelta(hours=self.COOLDOWN_HOURS)}\n")
                f.write(f"\n{reason}\n")

            logger.info(f"熔断日志已保存: {log_file}")

        except Exception as e:
            logger.error(f"保存熔断日志失败: {e}")

    def check_should_resume(self) -> Tuple[bool, str]:
        """
        检查是否应该恢复交易

        Returns:
            (是否恢复, 说明信息)
        """
        if not self._is_active:
            return False, "熔断未激活"

        if not self._activated_at:
            return False, "熔断激活时间未知"

        now = datetime.now()
        elapsed = now - self._activated_at
        cooldown_duration = timedelta(hours=self.COOLDOWN_HOURS)

        if elapsed >= cooldown_duration:
            return True, f"冷却期已过({elapsed.total_seconds() / 3600:.1f}小时)"
        else:
            remaining = cooldown_duration - elapsed
            return False, f"冷却中，剩余{remaining.total_seconds() / 3600:.1f}小时"

    async def resume(self):
        """恢复交易"""
        if not self._is_active:
            logger.warning("熔断未激活，无需恢复")
            return

        should_resume, msg = self.check_should_resume()
        if not should_resume:
            logger.warning(f"不满足恢复条件: {msg}")
            return

        logger.info(f"\n{'=' * 80}\n✅ 熔断恢复: {msg}\n{'=' * 80}")

        # 恢复所有策略
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE trading_strategies
                SET enabled = 1
                WHERE enabled = 0
            """)

            affected = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"✅ 已恢复 {affected} 个策略")

        except Exception as e:
            logger.error(f"恢复策略失败: {e}", exc_info=True)

        # 重置熔断状态
        self._is_active = False
        self._activated_at = None

        logger.info(f"✅ 熔断已解除，交易恢复\n{'=' * 80}")

    @property
    def is_active(self) -> bool:
        """熔断是否激活"""
        return self._is_active

    def get_status(self) -> Dict:
        """获取熔断状态"""
        if not self._is_active:
            return {
                'active': False,
                'message': '熔断未激活'
            }

        should_resume, msg = self.check_should_resume()

        return {
            'active': True,
            'activated_at': self._activated_at.isoformat() if self._activated_at else None,
            'cooldown_hours': self.COOLDOWN_HOURS,
            'should_resume': should_resume,
            'status_message': msg
        }
