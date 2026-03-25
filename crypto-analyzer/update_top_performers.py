"""
每日更新盈利Top 30交易对
从历史持仓数据中统计每个交易对的表现，选出Top 30
U本位开仓将只在这30个交易对中进行
"""

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
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', '3306')),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
    'database': os.getenv('DB_NAME', 'binance-data')
}


def update_top_performing_symbols(account_id: int = 2, top_n: int = 30):
    """
    更新盈利Top N交易对

    Args:
        account_id: 账户ID (2=U本位, 3=币本位)
        top_n: 保留前N名 (默认50)
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

        logger.info(f"=" * 80)
        logger.info(f"开始更新Top {top_n}交易对统计 (账户ID: {account_id})")
        logger.info(f"=" * 80)

        # 1. 统计每个交易对的历史表现
        logger.info("📊 统计所有交易对的历史表现...")
        cursor.execute("""
            SELECT
                symbol,
                COUNT(*) as total_trades,
                SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as winning_trades,
                SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losing_trades,
                COALESCE(SUM(realized_pnl), 0) as total_realized_pnl,
                COALESCE(AVG(realized_pnl), 0) as avg_pnl_per_trade,
                COALESCE(MAX(realized_pnl), 0) as max_single_profit,
                COALESCE(MIN(realized_pnl), 0) as max_single_loss,
                CASE
                    WHEN SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) > 0
                    THEN (SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*))
                    ELSE 0
                END as win_rate,
                CASE
                    WHEN SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END) > 0
                    THEN SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) /
                         SUM(CASE WHEN realized_pnl < 0 THEN ABS(realized_pnl) ELSE 0 END)
                    ELSE NULL
                END as profit_factor
            FROM futures_positions
            WHERE
                account_id = %s
                AND status = 'closed'
                AND realized_pnl IS NOT NULL
            GROUP BY symbol
            HAVING total_trades >= 5  -- 至少5笔交易才纳入统计
            ORDER BY total_realized_pnl DESC
            LIMIT %s
        """, (account_id, top_n))

        top_symbols = cursor.fetchall()

        if not top_symbols:
            logger.warning("⚠️ 没有找到符合条件的交易对")
            return

        logger.info(f"✅ 找到 {len(top_symbols)} 个符合条件的交易对")

        # 2. 清空原有数据
        logger.info("🗑️  清空旧数据...")
        cursor.execute("TRUNCATE TABLE top_performing_symbols")

        # 3. 插入新的Top N数据
        logger.info(f"📥 插入Top {top_n}数据...")
        insert_count = 0
        for rank, symbol_data in enumerate(top_symbols, start=1):
            cursor.execute("""
                INSERT INTO top_performing_symbols (
                    symbol, total_realized_pnl, total_trades,
                    winning_trades, losing_trades, win_rate,
                    avg_pnl_per_trade, max_single_profit, max_single_loss,
                    profit_factor, rank_score, last_updated
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
            """, (
                symbol_data['symbol'],
                float(symbol_data['total_realized_pnl']),
                symbol_data['total_trades'],
                symbol_data['winning_trades'],
                symbol_data['losing_trades'],
                float(symbol_data['win_rate']),
                float(symbol_data['avg_pnl_per_trade']),
                float(symbol_data['max_single_profit']),
                float(symbol_data['max_single_loss']),
                float(symbol_data['profit_factor']) if symbol_data['profit_factor'] else None,
                rank,
                datetime.now()
            ))
            insert_count += 1

            # 打印Top 10详情
            if rank <= 10:
                logger.info(
                    f"#{rank:2d} {symbol_data['symbol']:12s} | "
                    f"盈利: {symbol_data['total_realized_pnl']:+10.2f} USDT | "
                    f"交易: {symbol_data['total_trades']:4d} | "
                    f"胜率: {symbol_data['win_rate']:5.1f}% | "
                    f"均盈: {symbol_data['avg_pnl_per_trade']:+8.2f}"
                )

        logger.info(f"=" * 80)
        logger.info(f"✅ Top {top_n}交易对更新完成！共插入 {insert_count} 条记录")
        logger.info(f"=" * 80)

        # 4. 显示统计摘要
        cursor.execute("""
            SELECT
                COUNT(*) as total_count,
                SUM(total_realized_pnl) as total_pnl,
                AVG(win_rate) as avg_win_rate,
                MIN(total_realized_pnl) as min_pnl,
                MAX(total_realized_pnl) as max_pnl
            FROM top_performing_symbols
        """)
        summary = cursor.fetchone()

        logger.info(f"📈 统计摘要:")
        logger.info(f"   交易对数量: {summary['total_count']}")
        logger.info(f"   总盈利: {summary['total_pnl']:+.2f} USDT")
        logger.info(f"   平均胜率: {summary['avg_win_rate']:.1f}%")
        logger.info(f"   盈利范围: {summary['min_pnl']:+.2f} ~ {summary['max_pnl']:+.2f} USDT")

        cursor.close()

    except Exception as e:
        logger.error(f"❌ 更新Top交易对失败: {e}")
        import traceback
        logger.error(traceback.format_exc())

    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    """直接执行时更新U本位Top 50"""
    logger.info("开始更新盈利Top 50交易对...")
    update_top_performing_symbols(account_id=2, top_n=50)
    logger.info("更新完成！")
