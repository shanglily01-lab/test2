-- ========================================
-- 清理旧的EMA信号数据
-- 保留最近30天的信号，删除更早的数据
-- 日期：2025-11-05
-- ========================================

USE `binance-data`;

-- 1. 查看当前数据分布
SELECT
    '清理前统计' as status,
    COUNT(*) as total_signals,
    MIN(timestamp) as oldest_signal,
    MAX(timestamp) as newest_signal,
    COUNT(CASE WHEN timestamp >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 END) as signals_7d,
    COUNT(CASE WHEN timestamp >= DATE_SUB(NOW(), INTERVAL 30 DAY) THEN 1 END) as signals_30d,
    COUNT(CASE WHEN timestamp < DATE_SUB(NOW(), INTERVAL 30 DAY) THEN 1 END) as signals_to_delete
FROM ema_signals;

-- 2. 删除30天前的旧数据
DELETE FROM ema_signals
WHERE timestamp < DATE_SUB(NOW(), INTERVAL 30 DAY);

-- 3. 查看清理后的统计
SELECT
    '清理后统计' as status,
    COUNT(*) as total_signals,
    MIN(timestamp) as oldest_signal,
    MAX(timestamp) as newest_signal,
    ROUND(
        (SELECT SUM(data_length + index_length) / 1024 / 1024
         FROM information_schema.tables
         WHERE table_schema = 'binance-data' AND table_name = 'ema_signals'),
        2
    ) as table_size_mb
FROM ema_signals;

-- 4. 优化表（回收空间）
OPTIMIZE TABLE ema_signals;
