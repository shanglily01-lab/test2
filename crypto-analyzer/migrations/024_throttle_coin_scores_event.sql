-- 2026-07-04: coin_scores EVENT 从每 5 分钟改为每 15 分钟。
-- 根因: calculate_coin_score 在 kline_data(~1300万行) 上单 symbol 可达数十秒，
-- 每 5 分钟全量 500+ symbol 与 price_stats / WS INSERT / 探索页 API 叠峰导致整站卡死。
-- GET_LOCK 防重保留（见 migration 011）。

ALTER EVENT update_coin_scores_every_5min
    ON SCHEDULE EVERY 15 MINUTE
    DO CALL update_all_coin_scores();
