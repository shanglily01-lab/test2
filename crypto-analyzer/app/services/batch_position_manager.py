"""
分批建仓持仓管理器
提取V1和V2的公共逻辑，避免代码重复
"""
import pymysql
import json
from datetime import datetime
from typing import Dict, Optional
from decimal import Decimal
from loguru import logger

from app.services.volatility_calculator import get_volatility_calculator


class BatchPositionManager:
    """分批建仓持仓管理器 - 提供公共的持仓创建和管理功能"""

    def __init__(self, db_config: dict, account_id: int):
        """
        初始化管理器

        Args:
            db_config: 数据库配置
            account_id: 账户ID
        """
        self.db_config = db_config
        self.account_id = account_id

    def create_position(
        self,
        symbol: str,
        direction: str,
        quantity: Decimal,
        entry_price: Decimal,
        margin: Decimal,
        leverage: int,
        batch_num: int,
        batch_ratio: float,
        signal: Dict,
        signal_time: datetime,
        planned_close_time: Optional[datetime] = None,
        source: str = 'smart_trader_batch'
    ) -> int:
        """
        创建持仓记录（每批都是独立持仓）

        Args:
            symbol: 交易对
            direction: 方向 (LONG/SHORT)
            quantity: 数量
            entry_price: 入场价格
            margin: 保证金
            leverage: 杠杆
            batch_num: 批次序号 (0/1/2)
            batch_ratio: 批次比例 (0.3/0.3/0.4)
            signal: 信号数据
            signal_time: 信号时间
            planned_close_time: 计划平仓时间
            source: 来源标识

        Returns:
            position_id: 创建的持仓ID
        """
        conn = pymysql.connect(**self.db_config, cursorclass=pymysql.cursors.DictCursor)
        cursor = conn.cursor()

        try:
            # 1. 计算止盈止损
            stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct = self._calculate_sl_tp(
                symbol=symbol,
                direction=direction,
                entry_price=entry_price,
                signal=signal
            )

            # 2. 准备 batch_plan JSON
            batch_plan_json = json.dumps({
                'batches': [
                    {'ratio': batch_ratio, 'timeout_minutes': [30, 45, 60][i]}
                    for i in range(3)  # 假设总是3批
                ]
            })

            # 3. 准备 batch_filled JSON (当前批次)
            batch_filled_json = json.dumps({
                'batches': [{
                    'batch_num': batch_num,
                    'ratio': batch_ratio,
                    'price': float(entry_price),
                    'time': datetime.now().isoformat(),
                    'margin': float(margin),
                    'quantity': float(quantity)
                }]
            })

            # 4. 提取信号组件
            signal_components = signal.get('trade_params', {}).get('signal_components', {})
            entry_score = signal.get('trade_params', {}).get('entry_score', 30)
            signal_type = signal.get('trade_params', {}).get('signal_combination_key', 'batch_entry')

            # 5. 插入持仓记录
            cursor.execute("""
                INSERT INTO futures_positions
                (account_id, symbol, position_side, quantity, entry_price, avg_entry_price,
                 leverage, notional_value, margin, open_time, stop_loss_price, take_profit_price,
                 stop_loss_pct, take_profit_pct,
                 entry_signal_type, entry_score, signal_components,
                 batch_plan, batch_filled, entry_signal_time, planned_close_time,
                 source, status, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open', NOW(), NOW())
            """, (
                self.account_id,
                symbol,
                direction,
                float(quantity),
                float(entry_price),
                float(entry_price),
                leverage,
                float(quantity * entry_price),
                float(margin),
                stop_loss_price,
                take_profit_price,
                stop_loss_pct,
                take_profit_pct,
                signal_type,
                entry_score,
                json.dumps(signal_components),
                batch_plan_json,
                batch_filled_json,
                signal_time,
                planned_close_time,
                source
            ))

            position_id = cursor.lastrowid

            # 6. 冻结保证金
            self._freeze_margin(cursor, margin)

            conn.commit()
            logger.info(f"✅ 第{batch_num+1}批建仓完成，创建独立持仓记录 | ID:{position_id} | {symbol} {direction} {float(quantity):.8f} @ {float(entry_price):.2f}")

            return position_id

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ 创建持仓记录失败（第{batch_num+1}批）: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def _calculate_sl_tp(
        self,
        symbol: str,
        direction: str,
        entry_price: Decimal,
        signal: Dict
    ) -> tuple:
        """
        计算止盈止损

        Returns:
            (stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct)
        """
        volatility_calc = get_volatility_calculator()
        entry_score = signal.get('trade_params', {}).get('entry_score', 30)
        signal_components = list(signal.get('trade_params', {}).get('signal_components', {}).keys())

        stop_loss_pct, take_profit_pct, calc_reason = volatility_calc.get_sl_tp_for_position(
            symbol=symbol,
            position_side=direction,
            entry_score=entry_score,
            signal_components=signal_components
        )

        logger.info(f"[{symbol}] {direction} 止损止盈: SL={stop_loss_pct}% TP={take_profit_pct}% | {calc_reason}")

        # 转换为 Decimal 小数（避免 Decimal * float 类型错误）
        stop_loss_pct_decimal = Decimal(str(stop_loss_pct)) / Decimal('100')
        take_profit_pct_decimal = Decimal(str(take_profit_pct)) / Decimal('100')

        # 计算价格
        if direction == 'LONG':
            stop_loss_price = float(entry_price * (Decimal('1') - stop_loss_pct_decimal))
            take_profit_price = float(entry_price * (Decimal('1') + take_profit_pct_decimal))
        else:  # SHORT
            stop_loss_price = float(entry_price * (Decimal('1') + stop_loss_pct_decimal))
            take_profit_price = float(entry_price * (Decimal('1') - take_profit_pct_decimal))

        return stop_loss_price, take_profit_price, stop_loss_pct, take_profit_pct

    def _freeze_margin(self, cursor, margin: Decimal):
        """
        冻结保证金

        Args:
            cursor: 数据库游标
            margin: 保证金金额
        """
        cursor.execute("""
            UPDATE futures_trading_accounts
            SET current_balance = current_balance - %s,
                frozen_balance = frozen_balance + %s,
                updated_at = NOW()
            WHERE id = %s
        """, (float(margin), float(margin), self.account_id))

    def release_margin(self, margin: Decimal):
        """
        释放保证金

        Args:
            margin: 保证金金额
        """
        conn = pymysql.connect(**self.db_config)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET current_balance = current_balance + %s,
                    frozen_balance = frozen_balance - %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (float(margin), float(margin), self.account_id))

            conn.commit()
            logger.info(f"✅ 释放保证金: {float(margin):.2f} USDT")

        except Exception as e:
            conn.rollback()
            logger.error(f"❌ 释放保证金失败: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def get_position_count(self, symbol: str, direction: str) -> int:
        """
        统计持仓数量

        Args:
            symbol: 交易对
            direction: 方向

        Returns:
            持仓数量
        """
        conn = pymysql.connect(**self.db_config)
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT COUNT(*)
                FROM futures_positions
                WHERE symbol = %s
                  AND position_side = %s
                  AND status = 'open'
                  AND account_id = %s
            """, (symbol, direction, self.account_id))

            result = cursor.fetchone()
            return result[0] if result else 0

        finally:
            cursor.close()
            conn.close()
