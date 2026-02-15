#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化Big4评分MySQL调度系统
"""
import sys
import os
from dotenv import load_dotenv
import pymysql

# 设置UTF-8输出
if sys.platform == 'win32':
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 加载环境变量
load_dotenv()


def execute_sql(cursor, sql, description=""):
    """执行SQL语句"""
    try:
        cursor.execute(sql)
        if description:
            print(f"✅ {description}")
        return True
    except Exception as e:
        print(f"❌ {description} 失败: {e}")
        return False


def setup_big4_scheduler():
    """设置Big4评分调度系统"""
    print("\n" + "="*80)
    print("🚀 初始化Big4评分MySQL调度系统")
    print("="*80)

    # 数据库配置
    db_config = {
        'host': os.getenv('DB_HOST', 'localhost'),
        'port': int(os.getenv('DB_PORT', 3306)),
        'user': os.getenv('DB_USER', 'root'),
        'password': os.getenv('DB_PASSWORD', ''),
        'database': os.getenv('DB_NAME', 'binance-data')
    }

    try:
        conn = pymysql.connect(**db_config)
        cursor = conn.cursor()

        # 1. 创建Big4评分表
        print("\n📋 步骤1: 创建Big4评分表...")
        execute_sql(cursor, """
            CREATE TABLE IF NOT EXISTS big4_kline_scores (
                id INT AUTO_INCREMENT PRIMARY KEY,
                exchange VARCHAR(20) DEFAULT 'binance_futures',
                total_score INT NOT NULL DEFAULT 0 COMMENT '总分',
                main_score INT NOT NULL DEFAULT 0 COMMENT '主分(1H+15M)',
                five_m_bonus INT NOT NULL DEFAULT 0 COMMENT '5M反向加分',
                h1_score INT NOT NULL DEFAULT 0 COMMENT '1H评分',
                h1_btc_bullish INT NOT NULL DEFAULT 0,
                h1_btc_bearish INT NOT NULL DEFAULT 0,
                h1_eth_bullish INT NOT NULL DEFAULT 0,
                h1_eth_bearish INT NOT NULL DEFAULT 0,
                h1_bnb_bullish INT NOT NULL DEFAULT 0,
                h1_bnb_bearish INT NOT NULL DEFAULT 0,
                h1_sol_bullish INT NOT NULL DEFAULT 0,
                h1_sol_bearish INT NOT NULL DEFAULT 0,
                m15_score INT NOT NULL DEFAULT 0 COMMENT '15M评分',
                m15_btc_bullish INT NOT NULL DEFAULT 0,
                m15_btc_bearish INT NOT NULL DEFAULT 0,
                m15_eth_bullish INT NOT NULL DEFAULT 0,
                m15_eth_bearish INT NOT NULL DEFAULT 0,
                m15_bnb_bullish INT NOT NULL DEFAULT 0,
                m15_bnb_bearish INT NOT NULL DEFAULT 0,
                m15_sol_bullish INT NOT NULL DEFAULT 0,
                m15_sol_bearish INT NOT NULL DEFAULT 0,
                direction VARCHAR(10) NOT NULL COMMENT '方向(LONG/SHORT/NEUTRAL)',
                strength_level VARCHAR(10) NOT NULL COMMENT '强度(strong/medium/weak)',
                reason TEXT COMMENT '评分原因',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY idx_exchange (exchange),
                KEY idx_total_score (total_score),
                KEY idx_direction (direction),
                KEY idx_updated_at (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Big4 K线评分表'
        """, "创建 big4_kline_scores 表")
        conn.commit()

        # 2. 删除旧的存储过程（如果存在）
        print("\n📋 步骤2: 创建存储过程...")
        cursor.execute("DROP PROCEDURE IF EXISTS calculate_big4_score")
        conn.commit()

        # 3. 创建计算Big4评分的存储过程
        execute_sql(cursor, """
            CREATE PROCEDURE calculate_big4_score()
            BEGIN
                DECLARE v_h1_btc_bullish INT DEFAULT 0;
                DECLARE v_h1_btc_bearish INT DEFAULT 0;
                DECLARE v_h1_eth_bullish INT DEFAULT 0;
                DECLARE v_h1_eth_bearish INT DEFAULT 0;
                DECLARE v_h1_bnb_bullish INT DEFAULT 0;
                DECLARE v_h1_bnb_bearish INT DEFAULT 0;
                DECLARE v_h1_sol_bullish INT DEFAULT 0;
                DECLARE v_h1_sol_bearish INT DEFAULT 0;

                DECLARE v_m15_btc_bullish INT DEFAULT 0;
                DECLARE v_m15_btc_bearish INT DEFAULT 0;
                DECLARE v_m15_eth_bullish INT DEFAULT 0;
                DECLARE v_m15_eth_bearish INT DEFAULT 0;
                DECLARE v_m15_bnb_bullish INT DEFAULT 0;
                DECLARE v_m15_bnb_bearish INT DEFAULT 0;
                DECLARE v_m15_sol_bullish INT DEFAULT 0;
                DECLARE v_m15_sol_bearish INT DEFAULT 0;

                DECLARE v_h1_total_bullish INT DEFAULT 0;
                DECLARE v_h1_total_bearish INT DEFAULT 0;
                DECLARE v_m15_total_bullish INT DEFAULT 0;
                DECLARE v_m15_total_bearish INT DEFAULT 0;

                DECLARE v_h1_score INT DEFAULT 0;
                DECLARE v_m15_score INT DEFAULT 0;
                DECLARE v_main_score INT DEFAULT 0;
                DECLARE v_total_score INT DEFAULT 0;
                DECLARE v_direction VARCHAR(10) DEFAULT 'NEUTRAL';
                DECLARE v_strength VARCHAR(10) DEFAULT 'weak';
                DECLARE v_reason TEXT DEFAULT '';

                -- 计算BTC 1H K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_h1_btc_bullish, v_h1_btc_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'BTC/USDT' AND timeframe = '1h' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算ETH 1H K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_h1_eth_bullish, v_h1_eth_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'ETH/USDT' AND timeframe = '1h' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算BNB 1H K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_h1_bnb_bullish, v_h1_bnb_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'BNB/USDT' AND timeframe = '1h' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算SOL 1H K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_h1_sol_bullish, v_h1_sol_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'SOL/USDT' AND timeframe = '1h' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算BTC 15M K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_m15_btc_bullish, v_m15_btc_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'BTC/USDT' AND timeframe = '15m' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算ETH 15M K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_m15_eth_bullish, v_m15_eth_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'ETH/USDT' AND timeframe = '15m' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算BNB 15M K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_m15_bnb_bullish, v_m15_bnb_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'BNB/USDT' AND timeframe = '15m' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 计算SOL 15M K线
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_m15_sol_bullish, v_m15_sol_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = 'SOL/USDT' AND timeframe = '15m' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                -- 汇总Big4的1H和15M数据
                SET v_h1_total_bullish = v_h1_btc_bullish + v_h1_eth_bullish + v_h1_bnb_bullish + v_h1_sol_bullish;
                SET v_h1_total_bearish = v_h1_btc_bearish + v_h1_eth_bearish + v_h1_bnb_bearish + v_h1_sol_bearish;
                SET v_m15_total_bullish = v_m15_btc_bullish + v_m15_eth_bullish + v_m15_bnb_bullish + v_m15_sol_bullish;
                SET v_m15_total_bearish = v_m15_btc_bearish + v_m15_eth_bearish + v_m15_bnb_bearish + v_m15_sol_bearish;

                -- 计算评分（每根阳线+5分，每根阴线-5分）
                SET v_h1_score = IFNULL((v_h1_total_bullish - v_h1_total_bearish) * 5, 0);
                SET v_m15_score = IFNULL((v_m15_total_bullish - v_m15_total_bearish) * 5, 0);
                SET v_main_score = v_h1_score + v_m15_score;
                SET v_total_score = v_main_score;  -- Big4暂不使用5M加分

                -- 判断方向和强度
                IF v_total_score > 15 THEN
                    SET v_direction = 'LONG';
                    SET v_strength = 'strong';
                ELSEIF v_total_score > 5 THEN
                    SET v_direction = 'LONG';
                    SET v_strength = 'medium';
                ELSEIF v_total_score < -15 THEN
                    SET v_direction = 'SHORT';
                    SET v_strength = 'strong';
                ELSEIF v_total_score < -5 THEN
                    SET v_direction = 'SHORT';
                    SET v_strength = 'medium';
                ELSE
                    SET v_direction = 'NEUTRAL';
                    SET v_strength = 'weak';
                END IF;

                SET v_reason = CONCAT(
                    '1H:(', v_h1_total_bullish, '阳', v_h1_total_bearish, '阴,', v_h1_score, '分) ',
                    '15M:(', v_m15_total_bullish, '阳', v_m15_total_bearish, '阴,', v_m15_score, '分)'
                );

                -- 插入或更新
                INSERT INTO big4_kline_scores (
                    exchange, total_score, main_score, five_m_bonus,
                    h1_score,
                    h1_btc_bullish, h1_btc_bearish,
                    h1_eth_bullish, h1_eth_bearish,
                    h1_bnb_bullish, h1_bnb_bearish,
                    h1_sol_bullish, h1_sol_bearish,
                    m15_score,
                    m15_btc_bullish, m15_btc_bearish,
                    m15_eth_bullish, m15_eth_bearish,
                    m15_bnb_bullish, m15_bnb_bearish,
                    m15_sol_bullish, m15_sol_bearish,
                    direction, strength_level, reason
                ) VALUES (
                    'binance_futures', v_total_score, v_main_score, 0,
                    v_h1_score,
                    v_h1_btc_bullish, v_h1_btc_bearish,
                    v_h1_eth_bullish, v_h1_eth_bearish,
                    v_h1_bnb_bullish, v_h1_bnb_bearish,
                    v_h1_sol_bullish, v_h1_sol_bearish,
                    v_m15_score,
                    v_m15_btc_bullish, v_m15_btc_bearish,
                    v_m15_eth_bullish, v_m15_eth_bearish,
                    v_m15_bnb_bullish, v_m15_bnb_bearish,
                    v_m15_sol_bullish, v_m15_sol_bearish,
                    v_direction, v_strength, v_reason
                )
                ON DUPLICATE KEY UPDATE
                    total_score = VALUES(total_score),
                    main_score = VALUES(main_score),
                    five_m_bonus = VALUES(five_m_bonus),
                    h1_score = VALUES(h1_score),
                    h1_btc_bullish = VALUES(h1_btc_bullish),
                    h1_btc_bearish = VALUES(h1_btc_bearish),
                    h1_eth_bullish = VALUES(h1_eth_bullish),
                    h1_eth_bearish = VALUES(h1_eth_bearish),
                    h1_bnb_bullish = VALUES(h1_bnb_bullish),
                    h1_bnb_bearish = VALUES(h1_bnb_bearish),
                    h1_sol_bullish = VALUES(h1_sol_bullish),
                    h1_sol_bearish = VALUES(h1_sol_bearish),
                    m15_score = VALUES(m15_score),
                    m15_btc_bullish = VALUES(m15_btc_bullish),
                    m15_btc_bearish = VALUES(m15_btc_bearish),
                    m15_eth_bullish = VALUES(m15_eth_bullish),
                    m15_eth_bearish = VALUES(m15_eth_bearish),
                    m15_bnb_bullish = VALUES(m15_bnb_bullish),
                    m15_bnb_bearish = VALUES(m15_bnb_bearish),
                    m15_sol_bullish = VALUES(m15_sol_bullish),
                    m15_sol_bearish = VALUES(m15_sol_bearish),
                    direction = VALUES(direction),
                    strength_level = VALUES(strength_level),
                    reason = VALUES(reason),
                    updated_at = CURRENT_TIMESTAMP;
            END
        """, "创建 calculate_big4_score 存储过程")
        conn.commit()

        # 4. 删除旧的定时任务
        print("\n📋 步骤3: 创建定时任务...")
        cursor.execute("DROP EVENT IF EXISTS update_big4_score_every_5min")
        conn.commit()

        # 5. 创建定时任务
        execute_sql(cursor, """
            CREATE EVENT update_big4_score_every_5min
            ON SCHEDULE EVERY 5 MINUTE
            STARTS CURRENT_TIMESTAMP
            ON COMPLETION PRESERVE
            ENABLE
            DO CALL calculate_big4_score()
        """, "创建定时任务 update_big4_score_every_5min")
        conn.commit()

        # 6. 确保event_scheduler已启用
        print("\n📋 步骤4: 启用event_scheduler...")
        execute_sql(cursor, "SET GLOBAL event_scheduler = ON", "启用 event_scheduler")
        conn.commit()

        cursor.close()
        conn.close()

        print("\n" + "="*80)
        print("✅ Big4评分调度系统设置完成！")
        print("="*80)
        print("\n📝 下一步操作:")
        print("  1. 运行 python trigger_big4_score_update.py 手动触发一次更新")
        print("  2. 定时任务将每5分钟自动执行一次")
        print("\n")

        return True

    except Exception as e:
        print(f"\n❌ 设置失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    setup_big4_scheduler()
