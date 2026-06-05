-- Prevent update_coin_scores_every_5min / app scheduler overlap.
-- The scoring algorithm is unchanged; this only adds an advisory lock so a new
-- run exits immediately when the previous run is still active.

DELIMITER //

DROP PROCEDURE IF EXISTS update_all_coin_scores//

CREATE PROCEDURE update_all_coin_scores()
proc: BEGIN
    DECLARE done INT DEFAULT FALSE;
    DECLARE v_symbol VARCHAR(20);
    DECLARE v_lock_acquired INT DEFAULT 0;

    DECLARE cur CURSOR FOR
        SELECT symbol
        FROM trading_symbols
        WHERE enabled = 1 AND exchange = 'binance_futures'
        ORDER BY symbol;

    DECLARE CONTINUE HANDLER FOR NOT FOUND SET done = TRUE;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        IF v_lock_acquired = 1 THEN
            DO RELEASE_LOCK('update_all_coin_scores');
        END IF;
        RESIGNAL;
    END;

    SELECT GET_LOCK('update_all_coin_scores', 0) INTO v_lock_acquired;
    IF v_lock_acquired <> 1 THEN
        LEAVE proc;
    END IF;

    OPEN cur;
    read_loop: LOOP
        FETCH cur INTO v_symbol;
        IF done THEN
            LEAVE read_loop;
        END IF;
        CALL calculate_coin_score(v_symbol);
    END LOOP;
    CLOSE cur;

    DO RELEASE_LOCK('update_all_coin_scores');
END//

DELIMITER ;
