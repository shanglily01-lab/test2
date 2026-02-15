#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
初始化代币评分MySQL调度系统
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


def setup_scheduler():
    """设置评分调度系统"""
    print("\n" + "="*80)
    print("🚀 初始化代币评分MySQL调度系统")
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

        # 1. 创建交易对配置表
        print("\n📋 步骤1: 创建交易对配置表...")
        execute_sql(cursor, """
            CREATE TABLE IF NOT EXISTS trading_symbols (
                id INT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                exchange VARCHAR(20) DEFAULT 'binance_futures',
                enabled TINYINT(1) DEFAULT 1 COMMENT '是否启用',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY idx_symbol_exchange (symbol, exchange)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='交易对配置表'
        """, "创建 trading_symbols 表")
        conn.commit()

        # 2. 创建评分表
        print("\n📋 步骤2: 创建评分表...")
        execute_sql(cursor, """
            CREATE TABLE IF NOT EXISTS coin_kline_scores (
                id INT AUTO_INCREMENT PRIMARY KEY,
                symbol VARCHAR(20) NOT NULL,
                exchange VARCHAR(20) DEFAULT 'binance_futures',
                total_score INT NOT NULL DEFAULT 0 COMMENT '总分',
                main_score INT NOT NULL DEFAULT 0 COMMENT '主分(1H+15M)',
                five_m_bonus INT NOT NULL DEFAULT 0 COMMENT '5M反向加分',
                h1_score INT NOT NULL DEFAULT 0 COMMENT '1H评分',
                h1_bullish_count INT NOT NULL DEFAULT 0 COMMENT '1H阳线数',
                h1_bearish_count INT NOT NULL DEFAULT 0 COMMENT '1H阴线数',
                h1_level VARCHAR(10) COMMENT '1H级别',
                m15_score INT NOT NULL DEFAULT 0 COMMENT '15M评分',
                m15_bullish_count INT NOT NULL DEFAULT 0 COMMENT '15M阳线数',
                m15_bearish_count INT NOT NULL DEFAULT 0 COMMENT '15M阴线数',
                m15_level VARCHAR(10) COMMENT '15M级别',
                m5_bullish_count INT NOT NULL DEFAULT 0 COMMENT '5M阳线数',
                m5_bearish_count INT NOT NULL DEFAULT 0 COMMENT '5M阴线数',
                direction VARCHAR(10) NOT NULL COMMENT '方向(LONG/SHORT/NEUTRAL)',
                strength_level VARCHAR(10) NOT NULL COMMENT '强度(strong/medium/weak)',
                reason TEXT COMMENT '评分原因',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY idx_symbol_exchange (symbol, exchange),
                KEY idx_total_score (total_score),
                KEY idx_direction (direction),
                KEY idx_strength (strength_level),
                KEY idx_updated_at (updated_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='代币K线评分表'
        """, "创建 coin_kline_scores 表")
        conn.commit()

        # 3. 删除旧的存储过程（如果存在）
        print("\n📋 步骤3: 创建存储过程...")
        cursor.execute("DROP PROCEDURE IF EXISTS calculate_coin_score")
        cursor.execute("DROP PROCEDURE IF EXISTS update_all_coin_scores")
        conn.commit()

        # 4. 创建计算单个代币评分的存储过程
        execute_sql(cursor, """
            CREATE PROCEDURE calculate_coin_score(IN p_symbol VARCHAR(20))
            BEGIN
                DECLARE v_h1_bullish INT DEFAULT 0;
                DECLARE v_h1_bearish INT DEFAULT 0;
                DECLARE v_h1_score INT DEFAULT 0;
                DECLARE v_h1_level VARCHAR(10) DEFAULT '中性';
                DECLARE v_m15_bullish INT DEFAULT 0;
                DECLARE v_m15_bearish INT DEFAULT 0;
                DECLARE v_m15_score INT DEFAULT 0;
                DECLARE v_m15_level VARCHAR(10) DEFAULT '中性';
                DECLARE v_m5_bullish INT DEFAULT 0;
                DECLARE v_m5_bearish INT DEFAULT 0;
                DECLARE v_main_score INT DEFAULT 0;
                DECLARE v_five_m_bonus INT DEFAULT 0;
                DECLARE v_total_score INT DEFAULT 0;
                DECLARE v_direction VARCHAR(10) DEFAULT 'NEUTRAL';
                DECLARE v_strength VARCHAR(10) DEFAULT 'weak';
                DECLARE v_reason TEXT DEFAULT '';

                -- 1H K线统计
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_h1_bullish, v_h1_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = p_symbol AND timeframe = '1h' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                SET v_h1_score = IFNULL((v_h1_bullish - v_h1_bearish) * 5, 0);
                SET v_h1_level = CASE
                    WHEN v_h1_score >= 20 THEN '强多'
                    WHEN v_h1_score >= 10 THEN '中多'
                    WHEN v_h1_score > 0 THEN '弱多'
                    WHEN v_h1_score = 0 THEN '中性'
                    WHEN v_h1_score > -10 THEN '弱空'
                    WHEN v_h1_score >= -20 THEN '中空'
                    ELSE '强空'
                END;

                -- 15M K线统计
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_m15_bullish, v_m15_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = p_symbol AND timeframe = '15m' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 10
                ) t;

                SET v_m15_score = IFNULL((v_m15_bullish - v_m15_bearish) * 5, 0);
                SET v_m15_level = CASE
                    WHEN v_m15_score >= 20 THEN '强多'
                    WHEN v_m15_score >= 10 THEN '中多'
                    WHEN v_m15_score > 0 THEN '弱多'
                    WHEN v_m15_score = 0 THEN '中性'
                    WHEN v_m15_score > -10 THEN '弱空'
                    WHEN v_m15_score >= -20 THEN '中空'
                    ELSE '强空'
                END;

                -- 5M K线统计
                SELECT
                    IFNULL(SUM(CASE WHEN close_price > open_price THEN 1 ELSE 0 END), 0),
                    IFNULL(SUM(CASE WHEN close_price <= open_price THEN 1 ELSE 0 END), 0)
                INTO v_m5_bullish, v_m5_bearish
                FROM (
                    SELECT open_price, close_price
                    FROM kline_data
                    WHERE symbol = p_symbol AND timeframe = '5m' AND exchange = 'binance_futures'
                    ORDER BY open_time DESC LIMIT 3
                ) t;

                SET v_main_score = IFNULL(v_h1_score + v_m15_score, 0);
                SET v_five_m_bonus = 0;

                -- 5M反向加分
                IF v_main_score > 0 THEN
                    IF v_m5_bearish = 3 AND v_m5_bullish = 0 THEN
                        SET v_five_m_bonus = 10;
                    ELSEIF v_m5_bearish >= 2 THEN
                        SET v_five_m_bonus = 5;
                    END IF;
                END IF;

                IF v_main_score < 0 THEN
                    IF v_m5_bullish = 3 AND v_m5_bearish = 0 THEN
                        SET v_five_m_bonus = 10;
                    ELSEIF v_m5_bullish >= 2 THEN
                        SET v_five_m_bonus = 5;
                    END IF;
                END IF;

                SET v_total_score = v_main_score + v_five_m_bonus;

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
                    '1H:', v_h1_level, '(', v_h1_bullish, '阳', v_h1_bearish, '阴) ',
                    '15M:', v_m15_level, '(', v_m15_bullish, '阳', v_m15_bearish, '阴) ',
                    '5M:(', v_m5_bullish, '阳', v_m5_bearish, '阴)'
                );

                -- 插入或更新
                INSERT INTO coin_kline_scores (
                    symbol, exchange, total_score, main_score, five_m_bonus,
                    h1_score, h1_bullish_count, h1_bearish_count, h1_level,
                    m15_score, m15_bullish_count, m15_bearish_count, m15_level,
                    m5_bullish_count, m5_bearish_count,
                    direction, strength_level, reason
                ) VALUES (
                    p_symbol, 'binance_futures', v_total_score, v_main_score, v_five_m_bonus,
                    v_h1_score, v_h1_bullish, v_h1_bearish, v_h1_level,
                    v_m15_score, v_m15_bullish, v_m15_bearish, v_m15_level,
                    v_m5_bullish, v_m5_bearish,
                    v_direction, v_strength, v_reason
                )
                ON DUPLICATE KEY UPDATE
                    total_score = VALUES(total_score),
                    main_score = VALUES(main_score),
                    five_m_bonus = VALUES(five_m_bonus),
                    h1_score = VALUES(h1_score),
                    h1_bullish_count = VALUES(h1_bullish_count),
                    h1_bearish_count = VALUES(h1_bearish_count),
                    h1_level = VALUES(h1_level),
                    m15_score = VALUES(m15_score),
                    m15_bullish_count = VALUES(m15_bullish_count),
                    m15_bearish_count = VALUES(m15_bearish_count),
                    m15_level = VALUES(m15_level),
                    m5_bullish_count = VALUES(m5_bullish_count),
                    m5_bearish_count = VALUES(m5_bearish_count),
                    direction = VALUES(direction),
                    strength_level = VALUES(strength_level),
                    reason = VALUES(reason),
                    updated_at = CURRENT_TIMESTAMP;
            END
        """, "创建 calculate_coin_score 存储过程")
        conn.commit()

        # 5. 创建批量更新存储过程
        execute_sql(cursor, """
            CREATE PROCEDURE update_all_coin_scores()
            BEGIN
                DECLARE done INT DEFAULT FALSE;
                DECLARE v_symbol VARCHAR(20);
                DECLARE cur CURSOR FOR
                    SELECT symbol FROM trading_symbols
                    WHERE enabled = 1 AND exchange = 'binance_futures'
                    ORDER BY symbol;
                DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;

                OPEN cur;
                read_loop: LOOP
                    FETCH cur INTO v_symbol;
                    IF done THEN LEAVE read_loop; END IF;
                    CALL calculate_coin_score(v_symbol);
                END LOOP;
                CLOSE cur;
            END
        """, "创建 update_all_coin_scores 存储过程")
        conn.commit()

        # 6. 删除旧的定时任务
        print("\n📋 步骤4: 创建定时任务...")
        cursor.execute("DROP EVENT IF EXISTS update_coin_scores_every_5min")
        conn.commit()

        # 7. 创建定时任务
        execute_sql(cursor, """
            CREATE EVENT update_coin_scores_every_5min
            ON SCHEDULE EVERY 5 MINUTE
            STARTS CURRENT_TIMESTAMP
            ON COMPLETION PRESERVE
            ENABLE
            DO CALL update_all_coin_scores()
        """, "创建定时任务 update_coin_scores_every_5min")
        conn.commit()

        # 8. 确保event_scheduler已启用
        print("\n📋 步骤5: 启用event_scheduler...")
        execute_sql(cursor, "SET GLOBAL event_scheduler = ON", "启用 event_scheduler")
        conn.commit()

        cursor.close()
        conn.close()

        print("\n" + "="*80)
        print("✅ MySQL评分调度系统设置完成！")
        print("="*80)
        print("\n📝 下一步操作:")
        print("  1. 运行 python sync_symbols_to_db.py 同步交易对")
        print("  2. 运行 python trigger_score_update.py 手动触发一次更新")
        print("  3. 定时任务将每5分钟自动执行一次")
        print("\n")

        return True

    except Exception as e:
        print(f"\n❌ 设置失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    setup_scheduler()
