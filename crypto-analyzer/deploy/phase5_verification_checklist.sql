-- Phase 5 (test3 归档) 前置验证 SQL
-- 跑这些查询确认 test2 已经吸收了 test3 的全部功能,可以放心归档 test3
--
-- 用法:
--   mysql -h DB_HOST -u DB_USER -p DB_NAME < phase5_verification_checklist.sql

-- ============================================================
-- 1. test2 主策略最近 24h 必须有新单
-- ============================================================
SELECT
    'CHECK 1: test2 主策略 (smart_trader/BTC_MOMENTUM/PREDICTOR) 近 24h 开仓数' AS check_name,
    COUNT(*) AS n,
    CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'FAIL: 0 单,test2 主策略不工作' END AS result
FROM futures_positions
WHERE account_id = 2
  AND source IN ('smart_trader', 'BTC_MOMENTUM', 'PREDICTOR')
  AND open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR);

-- ============================================================
-- 2. test3 迁来的 strategy_live (topshort) 近 24h 必须有数据
-- ============================================================
SELECT
    'CHECK 2: strategy_live 近 24h 开仓数 (test3 迁来)' AS check_name,
    COUNT(*) AS n,
    CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'WARN: 0 单,确认 strategy_live 进程是否运行' END AS result
FROM futures_positions
WHERE account_id = 2
  AND source LIKE 'strategy_live%'
  AND open_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR);

-- ============================================================
-- 3. strategy_state 表近 24h 有更新 (strategy_live/bigmid 状态机)
-- ============================================================
SELECT
    'CHECK 3: strategy_state 表近 24h 更新数' AS check_name,
    COUNT(*) AS n,
    CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'WARN: strategy_state 无更新' END AS result
FROM strategy_state
WHERE updated_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR);

-- ============================================================
-- 4. Gemini swan worker 近 24h 有数据
-- ============================================================
SELECT
    'CHECK 4: gemini_swan_runs 近 24h 数据' AS check_name,
    COUNT(*) AS n,
    CASE WHEN COUNT(*) > 0 THEN 'PASS' ELSE 'WARN: 0 条,确认 gemini_swan_worker 是否启动' END AS result
FROM gemini_swan_runs
WHERE created_at >= DATE_SUB(NOW(), INTERVAL 24 HOUR);

-- ============================================================
-- 5. Big4 检测心跳 (近 10 分钟)
-- ============================================================
SELECT
    'CHECK 5: big4_trend_history 近 10 分钟写入数' AS check_name,
    COUNT(*) AS n,
    CASE WHEN COUNT(*) >= 1 THEN 'PASS' ELSE 'FAIL: Big4 检测停了,主进程可能挂' END AS result
FROM big4_trend_history
WHERE created_at >= DATE_SUB(NOW(), INTERVAL 10 MINUTE);

-- ============================================================
-- 6. K 线采集 (5m 必须在 15 分钟内有新数据)
-- ============================================================
SELECT
    'CHECK 6: 5m K线 最新 age (分钟)' AS check_name,
    ROUND((UNIX_TIMESTAMP(NOW())*1000 - MAX(open_time))/60000, 1) AS age_min,
    CASE
        WHEN (UNIX_TIMESTAMP(NOW())*1000 - MAX(open_time))/60000 < 15 THEN 'PASS'
        ELSE 'FAIL: fast_collector 停了或被 ban'
    END AS result
FROM kline_data
WHERE symbol = 'BTC/USDT' AND timeframe = '5m' AND exchange = 'binance_futures';

-- ============================================================
-- 7. live_trading_enabled 状态
-- ============================================================
SELECT
    'CHECK 7: live_trading_enabled 当前值' AS check_name,
    setting_value AS val,
    'INFO: 0=安全模拟, 1=已开实盘' AS result
FROM system_settings
WHERE setting_key = 'live_trading_enabled';

-- ============================================================
-- 8. 实盘对账状态 (近 6h 是否有 reconcile_closed)
-- ============================================================
SELECT
    'CHECK 8: 实盘对账 (reconcile_closed) 近 6h' AS check_name,
    COUNT(*) AS n,
    'INFO: 0 = 正常,>0 = 系统检测到交易所已平的实盘单' AS result
FROM live_futures_positions
WHERE close_reason = 'reconcile_closed'
  AND close_time >= DATE_SUB(NOW(), INTERVAL 6 HOUR);

-- ============================================================
-- 9. 各策略近 7 天表现总览 (人工审阅,无自动 PASS/FAIL)
-- ============================================================
SELECT
    'CHECK 9: 各 source 近 7 天表现' AS check_name,
    source,
    COUNT(*) AS n,
    ROUND(SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*)*100, 1) AS win_rate,
    ROUND(SUM(realized_pnl), 2) AS total_pnl,
    ROUND(AVG(realized_pnl), 2) AS avg_pnl
FROM futures_positions
WHERE status = 'closed'
  AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY source
ORDER BY n DESC;

-- ============================================================
-- 10. PID 文件锁状态 (确认 6 个服务都跑了一份)
--     这个 SQL 没法直接检查,需要在服务器跑:
--     ls -la /opt/crypto-analyzer/logs/*.pid
--     应该看到 6 个 .pid 文件,对应 6 个服务
-- ============================================================
SELECT '提示: 服务器跑 ls logs/*.pid 应见 6 个文件' AS check_10_manual;
