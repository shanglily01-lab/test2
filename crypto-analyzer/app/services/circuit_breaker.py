#!/usr/bin/env python3
"""
紧急停止机制
当最近5笔交易中有3笔硬止损时：
1. 暂停所有交易（模拟盘+实盘）
2. 平掉所有持仓
3. 4小时后再恢复
"""

import pymysql
from loguru import logger
from typing import Dict, Tuple
from datetime import datetime, timedelta
import json
import asyncio


class CircuitBreaker:
    """紧急停止机制"""

    CHECK_RECENT_TRADES = 5  # 检查最近5笔
    HARD_STOP_THRESHOLD = 3  # 3笔硬止损触发
    COOLDOWN_HOURS = 4  # 4小时后恢复

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self._is_active = False
        self._activated_at = None

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
        """检查是否触发：最近5笔中3笔硬止损"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT symbol, position_side, realized_pnl, unrealized_pnl_pct, notes, close_time
                FROM futures_positions
                WHERE status = 'closed' AND account_id = %s
                ORDER BY close_time DESC
                LIMIT %s
            """, (account_id, self.CHECK_RECENT_TRADES))

            recent_trades = cursor.fetchall()
            cursor.close()
            conn.close()

            if len(recent_trades) < self.CHECK_RECENT_TRADES:
                return False, ""

            # 统计硬止损：根据实际盈亏百分比判断（亏损 >= 2%）
            hard_stop_count = 0
            hard_stop_trades = []

            for trade in recent_trades:
                # 使用 unrealized_pnl_pct（基于保证金的盈亏百分比）判断
                # 如果 unrealized_pnl_pct <= -12.5%（对应价格亏损约2.5%，5倍杠杆），则认为是硬止损
                pnl_pct = float(trade.get('unrealized_pnl_pct') or 0)

                if pnl_pct <= -12.5:  # 保证金亏损 >= 12.5%（约等于2.5%价格亏损 * 5倍杠杆）
                    hard_stop_count += 1
                    hard_stop_trades.append({
                        'symbol': trade['symbol'],
                        'side': trade['position_side'],
                        'pnl': float(trade['realized_pnl']),
                        'pnl_pct': pnl_pct,
                        'time': trade['close_time']
                    })

            if hard_stop_count >= self.HARD_STOP_THRESHOLD:
                reason = f"[紧急停止] 最近{self.CHECK_RECENT_TRADES}笔中{hard_stop_count}笔硬止损\n"
                for t in hard_stop_trades:
                    reason += f"  - {t['symbol']} {t['side']}: ${t['pnl']:.2f} ({t['pnl_pct']:.2f}%) at {t['time']}\n"
                return True, reason

            return False, ""

        except Exception as e:
            logger.error(f"检查失败: {e}", exc_info=True)
            return False, ""

    async def activate(self, reason: str, account_id: int = 2):
        """紧急停止：暂停交易+平仓"""
        if self._is_active:
            logger.warning("已经停止，跳过")
            return

        logger.critical(f"\n{'=' * 80}\n[紧急停止]\n{'=' * 80}")
        logger.critical(reason)

        self._is_active = True
        self._activated_at = datetime.now()

        # 1. 暂停所有策略
        await self._pause_all_strategies()

        # 2. 平掉所有持仓
        await self._close_all_positions(account_id)

        logger.critical(
            f"[紧急停止] 完成\n"
            f"   - 策略已暂停\n"
            f"   - 持仓已平仓\n"
            f"   - {self.COOLDOWN_HOURS}小时后恢复: {self._activated_at + timedelta(hours=self.COOLDOWN_HOURS)}\n"
            f"{'=' * 80}"
        )

    async def _pause_all_strategies(self):
        """暂停所有策略"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT id, name, market_type FROM trading_strategies WHERE enabled = 1")
            strategies = cursor.fetchall()

            if not strategies:
                logger.info("无需暂停")
                cursor.close()
                conn.close()
                return

            cursor.execute("UPDATE trading_strategies SET enabled = 0 WHERE enabled = 1")
            conn.commit()
            cursor.close()
            conn.close()

            logger.warning(f"已暂停 {len(strategies)} 个策略:")
            for s in strategies:
                logger.warning(f"  - [{s['market_type']}] {s['name']}")

        except Exception as e:
            logger.error(f"暂停失败: {e}", exc_info=True)

    async def _close_all_positions(self, account_id: int):
        """平掉所有持仓"""
        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, symbol, position_side, strategy_id
                FROM futures_positions
                WHERE status = 'open' AND account_id = %s
            """, (account_id,))

            positions = cursor.fetchall()
            cursor.close()
            conn.close()

            if not positions:
                logger.info("无持仓")
                return

            logger.warning(f"开始平仓 {len(positions)} 个...")

            # 创建交易引擎
            from app.trading.futures_trading_engine import FuturesTradingEngine
            futures_engine = FuturesTradingEngine(self.db_config)

            # 同步平仓（close_position是同步函数）
            success_count = 0
            failed_positions = []

            for position in positions:
                try:
                    result = futures_engine.close_position(
                        position_id=position['id'],
                        close_reason="emergency_stop"
                    )
                    if result and result.get('success'):
                        success_count += 1
                        logger.info(f"✓ 平仓成功: {position['symbol']} {position['position_side']}")
                    else:
                        failed_positions.append(position)
                        logger.error(f"✗ 平仓失败: {position['symbol']} {position['position_side']}")
                except Exception as e:
                    failed_positions.append(position)
                    logger.error(f"✗ 平仓异常: {position['symbol']} {position['position_side']}: {e}")

            logger.warning(f"平仓完成: {success_count}/{len(positions)}")

            if failed_positions:
                logger.critical(f"⚠️ {len(failed_positions)}个持仓平仓失败:")
                for p in failed_positions:
                    logger.critical(f"  - {p['symbol']} {p['position_side']} (ID: {p['id']})")

        except Exception as e:
            logger.error(f"平仓失败: {e}", exc_info=True)

    def check_should_resume(self) -> Tuple[bool, str]:
        """检查是否可以恢复"""
        if not self._is_active:
            return False, "未停止"

        if not self._activated_at:
            return False, "时间未知"

        now = datetime.now()
        elapsed = now - self._activated_at
        cooldown = timedelta(hours=self.COOLDOWN_HOURS)

        if elapsed >= cooldown:
            return True, f"已过{elapsed.total_seconds() / 3600:.1f}小时"
        else:
            remaining = cooldown - elapsed
            return False, f"剩余{remaining.total_seconds() / 3600:.1f}小时"

    async def resume(self):
        """恢复交易"""
        if not self._is_active:
            logger.warning("未停止，无需恢复")
            return

        should_resume, msg = self.check_should_resume()
        if not should_resume:
            logger.warning(f"不满足恢复条件: {msg}")
            return

        logger.info(f"\n{'=' * 80}\n[恢复交易]: {msg}\n{'=' * 80}")

        try:
            conn = self.get_db_connection()
            cursor = conn.cursor()
            cursor.execute("UPDATE trading_strategies SET enabled = 1 WHERE enabled = 0")
            affected = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()

            logger.info(f"已恢复 {affected} 个策略")

        except Exception as e:
            logger.error(f"恢复失败: {e}", exc_info=True)

        self._is_active = False
        self._activated_at = None

        logger.info(f"交易已恢复\n{'=' * 80}")

    @property
    def is_active(self) -> bool:
        """是否已停止"""
        return self._is_active

    def get_status(self) -> Dict:
        """获取状态"""
        if not self._is_active:
            return {'active': False, 'message': '未停止'}

        should_resume, msg = self.check_should_resume()

        return {
            'active': True,
            'activated_at': self._activated_at.isoformat() if self._activated_at else None,
            'cooldown_hours': self.COOLDOWN_HOURS,
            'should_resume': should_resume,
            'status_message': msg
        }
