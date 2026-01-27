#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断平仓卡住问题

分析为什么平仓方法被调用后没有返回结果
"""

import pymysql
import yaml
from loguru import logger
import time

def load_db_config():
    """从 config.yaml 加载数据库配置"""
    with open('config.yaml', 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f)
    return config['database']

def test_db_connection():
    """测试数据库连接"""
    logger.info("测试数据库连接...")
    db_config = load_db_config()

    try:
        start_time = time.time()
        conn = pymysql.connect(
            host=db_config['host'],
            port=db_config['port'],
            user=db_config['user'],
            password=db_config['password'],
            database=db_config['database'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=5,
            read_timeout=5,
            write_timeout=5
        )
        elapsed = time.time() - start_time
        logger.info(f"✅ 数据库连接成功 ({elapsed:.3f}秒)")
        return conn
    except Exception as e:
        logger.error(f"❌ 数据库连接失败: {e}")
        return None

def check_table_locks(conn):
    """检查表锁情况"""
    logger.info("\n检查表锁...")
    cursor = conn.cursor()

    try:
        cursor.execute("SHOW OPEN TABLES WHERE In_use > 0")
        locked_tables = cursor.fetchall()

        if locked_tables:
            logger.warning(f"⚠️ 发现 {len(locked_tables)} 个表被锁定:")
            for table in locked_tables:
                logger.warning(f"  {table}")
        else:
            logger.info("✅ 没有表被锁定")

        cursor.close()
    except Exception as e:
        logger.error(f"❌ 检查表锁失败: {e}")

def check_long_running_queries(conn):
    """检查长时间运行的查询"""
    logger.info("\n检查长时间运行的查询...")
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                ID,
                USER,
                HOST,
                DB,
                COMMAND,
                TIME,
                STATE,
                INFO
            FROM information_schema.PROCESSLIST
            WHERE COMMAND != 'Sleep'
            AND TIME > 10
            ORDER BY TIME DESC
        """)

        long_queries = cursor.fetchall()

        if long_queries:
            logger.warning(f"⚠️ 发现 {len(long_queries)} 个长时间运行的查询:")
            for query in long_queries:
                logger.warning(f"  ID: {query['ID']}, 用户: {query['USER']}, "
                             f"时间: {query['TIME']}秒, 状态: {query['STATE']}")
                if query['INFO']:
                    logger.warning(f"    SQL: {query['INFO'][:100]}...")
        else:
            logger.info("✅ 没有长时间运行的查询")

        cursor.close()
    except Exception as e:
        logger.error(f"❌ 检查长查询失败: {e}")

def check_database_performance(conn):
    """检查数据库性能指标"""
    logger.info("\n检查数据库性能...")
    cursor = conn.cursor()

    try:
        # 检查慢查询数量
        cursor.execute("SHOW GLOBAL STATUS LIKE 'Slow_queries'")
        slow_queries = cursor.fetchone()
        logger.info(f"慢查询数量: {slow_queries['Value']}")

        # 检查连接数
        cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
        connections = cursor.fetchone()
        logger.info(f"当前连接数: {connections['Value']}")

        cursor.execute("SHOW GLOBAL STATUS LIKE 'Max_used_connections'")
        max_connections = cursor.fetchone()
        logger.info(f"最大连接数: {max_connections['Value']}")

        # 检查表大小
        cursor.execute("""
            SELECT
                table_name,
                table_rows,
                ROUND(data_length / 1024 / 1024, 2) as data_mb,
                ROUND(index_length / 1024 / 1024, 2) as index_mb
            FROM information_schema.TABLES
            WHERE table_schema = %s
            AND table_name IN ('futures_positions', 'futures_orders', 'futures_trades')
        """, (conn.db.decode('utf-8'),))

        tables = cursor.fetchall()
        logger.info("\n关键表大小:")
        for table in tables:
            logger.info(f"  {table['table_name']}: {table['table_rows']} 行, "
                       f"数据 {table['data_mb']} MB, 索引 {table['index_mb']} MB")

        cursor.close()
    except Exception as e:
        logger.error(f"❌ 检查性能失败: {e}")

def test_close_position_query(conn):
    """测试平仓相关的SQL查询性能"""
    logger.info("\n测试平仓相关SQL性能...")
    cursor = conn.cursor()

    test_queries = [
        ("查询开仓持仓", """
            SELECT * FROM futures_positions
            WHERE status = 'open' AND account_id = 2
            LIMIT 10
        """),
        ("更新持仓状态", """
            SELECT COUNT(*) as cnt FROM futures_positions
            WHERE status = 'open' AND account_id = 2
        """),
        ("查询账户余额", """
            SELECT * FROM futures_trading_accounts WHERE id = 2
        """),
        ("更新总权益(子查询)", """
            SELECT
                current_balance,
                frozen_balance,
                (SELECT SUM(unrealized_pnl) FROM futures_positions WHERE account_id = 2 AND status = 'open') as total_unrealized
            FROM futures_trading_accounts WHERE id = 2
        """)
    ]

    for query_name, sql in test_queries:
        try:
            start_time = time.time()
            cursor.execute(sql)
            result = cursor.fetchall()
            elapsed = time.time() - start_time

            if elapsed > 1.0:
                logger.warning(f"⚠️ {query_name}: {elapsed:.3f}秒 (较慢)")
            else:
                logger.info(f"✅ {query_name}: {elapsed:.3f}秒")

        except Exception as e:
            logger.error(f"❌ {query_name} 失败: {e}")

    cursor.close()

def check_stuck_positions(conn):
    """检查可能卡住的持仓"""
    logger.info("\n检查可能卡住的持仓...")
    cursor = conn.cursor()

    try:
        # 查询最近更新时间超过5分钟但仍然open的持仓
        cursor.execute("""
            SELECT
                id,
                symbol,
                position_side,
                status,
                open_time,
                updated_at,
                TIMESTAMPDIFF(MINUTE, updated_at, NOW()) as minutes_since_update
            FROM futures_positions
            WHERE status = 'open'
            AND account_id = 2
            AND TIMESTAMPDIFF(MINUTE, updated_at, NOW()) > 5
            ORDER BY updated_at DESC
        """)

        stuck = cursor.fetchall()

        if stuck:
            logger.warning(f"⚠️ 发现 {len(stuck)} 个长时间未更新的持仓:")
            for pos in stuck:
                logger.warning(f"  #{pos['id']} {pos['symbol']} {pos['position_side']} | "
                             f"最后更新: {pos['minutes_since_update']} 分钟前")
        else:
            logger.info("✅ 没有长时间未更新的持仓")

        cursor.close()
    except Exception as e:
        logger.error(f"❌ 检查卡住持仓失败: {e}")

def main():
    """主函数"""
    logger.info("=" * 80)
    logger.info("诊断平仓卡住问题")
    logger.info("=" * 80)

    # 1. 测试数据库连接
    conn = test_db_connection()
    if not conn:
        logger.error("无法连接数据库，退出诊断")
        return

    try:
        # 2. 检查表锁
        check_table_locks(conn)

        # 3. 检查长查询
        check_long_running_queries(conn)

        # 4. 检查数据库性能
        check_database_performance(conn)

        # 5. 测试SQL性能
        test_close_position_query(conn)

        # 6. 检查卡住的持仓
        check_stuck_positions(conn)

    finally:
        conn.close()

    logger.info("=" * 80)
    logger.info("诊断完成")
    logger.info("=" * 80)

if __name__ == '__main__':
    main()
