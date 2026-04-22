"""
定时更新账户统计数据
每5分钟从 futures_positions 重新计算账户的盈亏、胜率等统计信息
避免平仓时并发更新导致死锁
"""

from app.utils.config_loader import get_db_config
import pymysql
from loguru import logger
from datetime import datetime
import sys
import os
from dotenv import load_dotenv

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载环境变量
load_dotenv()

# 数据库配置（从环境变量读取）
MYSQL_CONFIG = {
    **get_db_config()
}


def update_account_statistics(account_id: int = None):
    """
    更新账户统计数据

    Args:
        account_id: 账户ID，如果为None则更新所有账户
    """
    conn = None
    try:
        conn = pymysql.connect(
            **MYSQL_CONFIG,
            autocommit=True,
            connect_timeout=10,
            read_timeout=30,
            write_timeout=30
        )
        cursor = conn.cursor(pymysql.cursors.DictCursor)

        # 获取需要更新的账户列表
        if account_id:
            cursor.execute("SELECT id, initial_balance FROM futures_trading_accounts WHERE id = %s", (account_id,))
        else:
            cursor.execute("SELECT id, initial_balance FROM futures_trading_accounts")

        accounts = cursor.fetchall()

        for account in accounts:
            acc_id = account['id']
            initial_balance = float(account['initial_balance'])

            # 1. 计算已平仓持仓的统计
            cursor.execute("""
                SELECT
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                    COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
                    COALESCE(SUM(margin), 0) as closed_margin
                FROM futures_positions
                WHERE account_id = %s AND status = 'closed' AND realized_pnl IS NOT NULL
            """, (acc_id,))

            closed_stats = cursor.fetchone()

            # 2. 计算未平仓持仓的占用保证金
            cursor.execute("""
                SELECT COALESCE(SUM(margin), 0) as open_margin
                FROM futures_positions
                WHERE account_id = %s AND status = 'open'
            """, (acc_id,))

            open_stats = cursor.fetchone()

            # 3. 计算统计数据
            total_trades = closed_stats['total_trades'] or 0
            winning_trades = closed_stats['winning_trades'] or 0
            losing_trades = closed_stats['losing_trades'] or 0
            total_realized_pnl = float(closed_stats['total_realized_pnl'])
            frozen_balance = float(open_stats['open_margin'])  # 未平仓持仓占用的保证金

            # 当前余额 = 初始余额 + 已实现盈亏
            current_balance = initial_balance + total_realized_pnl

            # 胜率
            win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

            # 4. 更新账户统计
            cursor.execute("""
                UPDATE futures_trading_accounts
                SET
                    current_balance = %s,
                    frozen_balance = %s,
                    realized_pnl = %s,
                    total_trades = %s,
                    winning_trades = %s,
                    losing_trades = %s,
                    win_rate = %s,
                    updated_at = NOW()
                WHERE id = %s
            """, (
                current_balance,
                frozen_balance,
                total_realized_pnl,
                total_trades,
                winning_trades,
                losing_trades,
                win_rate,
                acc_id
            ))

            logger.debug(
                f"✅ 账户{acc_id}统计已更新 | "
                f"余额: ${current_balance:.2f} | "
                f"冻结: ${frozen_balance:.2f} | "
                f"已实现: {total_realized_pnl:+.2f} | "
                f"交易: {total_trades} | "
                f"胜率: {win_rate:.1f}%"
            )

        cursor.close()
        logger.info(f"✅ 账户统计更新完成 | 更新了 {len(accounts)} 个账户")

    except Exception as e:
        logger.error(f"❌ 更新账户统计失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    """直接执行时更新所有账户"""
    logger.info("=" * 60)
    logger.info("开始更新账户统计...")
    logger.info("=" * 60)

    update_account_statistics()

    logger.info("=" * 60)
    logger.info("账户统计更新完成")
    logger.info("=" * 60)
