-- 2026-07-04: 下线 coin_scores（业务已不用）。
-- calculate_coin_score 扫 kline_data 单 symbol 可达数十秒，是探索页卡死主因之一。
-- 幂等：可重复执行。

DROP EVENT IF EXISTS update_coin_scores_every_5min;
DROP PROCEDURE IF EXISTS update_all_coin_scores;
DROP PROCEDURE IF EXISTS calculate_coin_score;
