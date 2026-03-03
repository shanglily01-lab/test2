-- ============================================================
-- 数据采集情况缓存表 + 存储过程
-- 执行方式：mysql -u admin -p binance-data < sql/create_collection_status_cache.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS collection_status_cache (
    type_key           VARCHAR(50)   NOT NULL PRIMARY KEY,
    total_count        BIGINT        DEFAULT 0,
    latest_time        DATETIME      NULL,
    oldest_time        DATETIME      NULL,
    -- 通用计数字段（不同 type_key 对应不同语义，见存储过程注释）
    symbol_count       INT           DEFAULT 0,   -- price/kline: symbol数
    exchange_count     INT           DEFAULT 0,   -- price: 交易所数
    timeframe_count    INT           DEFAULT 0,   -- kline: 时间周期数
    source_count       INT           DEFAULT 0,   -- news: 新闻来源数
    etf_count          INT           DEFAULT 0,   -- etf: ETF品种数
    company_count      INT           DEFAULT 0,   -- treasury: 公司数
    wallet_count       INT           DEFAULT 0,   -- hyperliquid/smart_money: 钱包数
    trader_count       INT           DEFAULT 0,   -- hyperliquid: 交易员总数
    monitored_count    INT           DEFAULT 0,   -- hyperliquid: 监控钱包数
    coin_count         INT           DEFAULT 0,   -- hyperliquid: 币种数
    address_count      INT           DEFAULT 0,   -- smart_money: 地址总数
    token_count        INT           DEFAULT 0,   -- smart_money: token数
    blockchain_count   INT           DEFAULT 0,   -- smart_money: 链数
    signal_count       INT           DEFAULT 0,   -- smart_money: 活跃信号数
    latest_signal_time DATETIME      NULL,        -- smart_money: 最新信号时间
    -- K线各时间周期最新时间（用于多级状态判断）
    kline_latest_1m    DATETIME      NULL,
    kline_latest_5m    DATETIME      NULL,
    kline_latest_15m   DATETIME      NULL,
    kline_latest_1h    DATETIME      NULL,
    kline_latest_1d    DATETIME      NULL,
    updated_at         DATETIME      NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='数据采集情况缓存，每5分钟由存储过程刷新';

-- ============================================================
DROP PROCEDURE IF EXISTS update_collection_status_cache;

DELIMITER //

CREATE PROCEDURE update_collection_status_cache()
BEGIN
    DECLARE v_now    DATETIME DEFAULT NOW();

    -- 通用复用变量（每个 section 使用完即覆盖）
    DECLARE v_count  BIGINT   DEFAULT 0;
    DECLARE v_latest DATETIME DEFAULT NULL;
    DECLARE v_oldest DATETIME DEFAULT NULL;

    DECLARE v_int1   INT      DEFAULT 0;
    DECLARE v_int2   INT      DEFAULT 0;
    DECLARE v_int3   INT      DEFAULT 0;
    DECLARE v_int4   INT      DEFAULT 0;
    DECLARE v_int5   INT      DEFAULT 0;
    DECLARE v_int6   INT      DEFAULT 0;

    DECLARE v_dt1    DATETIME DEFAULT NULL;
    DECLARE v_dt2    DATETIME DEFAULT NULL;
    DECLARE v_dt3    DATETIME DEFAULT NULL;
    DECLARE v_dt4    DATETIME DEFAULT NULL;
    DECLARE v_dt5    DATETIME DEFAULT NULL;

    -- CONTINUE HANDLER 必须在所有 DECLARE 变量之后
    DECLARE CONTINUE HANDLER FOR SQLEXCEPTION BEGIN END;

    -- ========================================================
    -- 1. 实时价格数据（price_data）
    --    v_int1=symbol_count, v_int2=exchange_count
    -- ========================================================
    SELECT COUNT(*), MAX(timestamp), MIN(timestamp),
           COUNT(DISTINCT symbol), COUNT(DISTINCT exchange)
    INTO   v_count, v_latest, v_oldest, v_int1, v_int2
    FROM   price_data;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time,
         symbol_count, exchange_count, updated_at)
    VALUES ('price', v_count, v_latest, v_oldest, v_int1, v_int2, v_now);

    -- ========================================================
    -- 2. K线数据（kline_data）
    --    v_int1=symbol_count, v_int2=timeframe_count
    --    v_dt1..v_dt5 = 各时间周期最新 created_at
    -- ========================================================
    SELECT COUNT(*),
           MAX(CASE WHEN created_at IS NOT NULL THEN created_at ELSE timestamp END),
           MIN(CASE WHEN created_at IS NOT NULL THEN created_at ELSE timestamp END),
           COUNT(DISTINCT symbol),
           COUNT(DISTINCT timeframe),
           MAX(CASE WHEN timeframe = '1m'  AND created_at IS NOT NULL THEN created_at END),
           MAX(CASE WHEN timeframe = '5m'  AND created_at IS NOT NULL THEN created_at END),
           MAX(CASE WHEN timeframe = '15m' AND created_at IS NOT NULL THEN created_at END),
           MAX(CASE WHEN timeframe = '1h'  AND created_at IS NOT NULL THEN created_at END),
           MAX(CASE WHEN timeframe = '1d'  AND created_at IS NOT NULL THEN created_at END)
    INTO   v_count, v_latest, v_oldest, v_int1, v_int2,
           v_dt1, v_dt2, v_dt3, v_dt4, v_dt5
    FROM   kline_data;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time,
         symbol_count, timeframe_count,
         kline_latest_1m, kline_latest_5m, kline_latest_15m,
         kline_latest_1h, kline_latest_1d, updated_at)
    VALUES ('kline', v_count, v_latest, v_oldest, v_int1, v_int2,
            v_dt1, v_dt2, v_dt3, v_dt4, v_dt5, v_now);

    -- ========================================================
    -- 3. 合约数据（futures_open_interest）
    -- ========================================================
    SELECT COUNT(*), MAX(timestamp), MIN(timestamp)
    INTO   v_count, v_latest, v_oldest
    FROM   futures_open_interest;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time, updated_at)
    VALUES ('futures', v_count, v_latest, v_oldest, v_now);

    -- ========================================================
    -- 4. 新闻数据（news_data）
    --    v_int1=source_count
    -- ========================================================
    SELECT COUNT(*), MAX(published_datetime), MIN(published_datetime),
           COUNT(DISTINCT source)
    INTO   v_count, v_latest, v_oldest, v_int1
    FROM   news_data;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time, source_count, updated_at)
    VALUES ('news', v_count, v_latest, v_oldest, v_int1, v_now);

    -- ========================================================
    -- 5. ETF数据（crypto_etf_flows）
    --    v_int1=etf_count（ticker 种类数）
    --    trade_date 是 DATE 类型，MySQL 自动转换为 DATETIME 存储
    -- ========================================================
    SELECT COUNT(*), MAX(trade_date), MIN(trade_date),
           COUNT(DISTINCT ticker)
    INTO   v_count, v_latest, v_oldest, v_int1
    FROM   crypto_etf_flows;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time, etf_count, updated_at)
    VALUES ('etf', v_count, v_latest, v_oldest, v_int1, v_now);

    -- ========================================================
    -- 6. 企业金库数据（purchases + financing 合并）
    --    purchases: v_count/v_latest/v_oldest/v_int1(company_count)
    --    financing: v_int3/v_dt1/v_dt2/v_int4(company_count)
    --    merged: v_int2=MAX(company_count)
    -- ========================================================
    SELECT COUNT(*), MAX(updated_at), MIN(created_at), COUNT(DISTINCT company_id)
    INTO   v_count, v_latest, v_oldest, v_int1
    FROM   corporate_treasury_purchases;

    SELECT COUNT(*), MAX(updated_at), MIN(created_at), COUNT(DISTINCT company_id)
    INTO   v_int3, v_dt1, v_dt2, v_int4
    FROM   corporate_treasury_financing;

    SET v_count  = v_count + v_int3;
    SET v_int2   = GREATEST(COALESCE(v_int1, 0), COALESCE(v_int4, 0));  -- company_count

    -- 合并 latest_time：取两表 MAX(updated_at) 中较新的
    SET v_latest = CASE
        WHEN v_latest IS NULL THEN v_dt1
        WHEN v_dt1    IS NULL THEN v_latest
        ELSE GREATEST(v_latest, v_dt1)
    END;

    -- 合并 oldest_time：取两表 MIN(created_at) 中较旧的
    SET v_oldest = CASE
        WHEN v_oldest IS NULL THEN v_dt2
        WHEN v_dt2    IS NULL THEN v_oldest
        ELSE LEAST(v_oldest, v_dt2)
    END;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time, company_count, updated_at)
    VALUES ('treasury', v_count, v_latest, v_oldest, v_int2, v_now);

    -- ========================================================
    -- 7. Hyperliquid聪明钱（三表）
    --    trades: v_count/v_latest/v_oldest/v_int1(wallet)/v_int2(coin)
    --    traders: v_int3
    --    monitored: v_int4
    -- ========================================================
    SELECT COUNT(*), MAX(trade_time), MIN(trade_time),
           COUNT(DISTINCT address), COUNT(DISTINCT coin)
    INTO   v_count, v_latest, v_oldest, v_int1, v_int2
    FROM   hyperliquid_wallet_trades;

    SELECT COUNT(*) INTO v_int3 FROM hyperliquid_traders;

    SELECT COUNT(*) INTO v_int4
    FROM   hyperliquid_monitored_wallets
    WHERE  is_monitoring = TRUE;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time,
         wallet_count, coin_count, trader_count, monitored_count, updated_at)
    VALUES ('hyperliquid', v_count, v_latest, v_oldest,
            v_int1, v_int2, v_int3, v_int4, v_now);

    -- ========================================================
    -- 8. 链上聪明钱（三表）
    --    transactions: v_count/v_latest/v_oldest/v_int1(wallet)/v_int2(token)/v_int3(chain)
    --    signals(active): v_int4(signal_count)/v_dt1(latest_signal_time)/v_int5
    --    addresses: v_int6
    -- ========================================================
    SELECT COUNT(*), MAX(timestamp), MIN(timestamp),
           COUNT(DISTINCT address), COUNT(DISTINCT token_symbol), COUNT(DISTINCT blockchain)
    INTO   v_count, v_latest, v_oldest, v_int1, v_int2, v_int3
    FROM   smart_money_transactions;

    SELECT COUNT(*), MAX(timestamp), COUNT(DISTINCT token_symbol)
    INTO   v_int4, v_dt1, v_int5
    FROM   smart_money_signals
    WHERE  is_active = TRUE;

    SELECT COUNT(*) INTO v_int6 FROM smart_money_addresses;

    REPLACE INTO collection_status_cache
        (type_key, total_count, latest_time, oldest_time,
         wallet_count, token_count, blockchain_count,
         signal_count, latest_signal_time, address_count, updated_at)
    VALUES ('smart_money', v_count, v_latest, v_oldest,
            v_int1, v_int2, v_int3, v_int4, v_dt1, v_int6, v_now);

END //

DELIMITER ;

-- 立即执行一次初始化
CALL update_collection_status_cache();

SELECT CONCAT('collection_status_cache 已初始化，共 ', COUNT(*), ' 行') AS result
FROM collection_status_cache;
