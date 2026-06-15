-- Speed up futures account/page statistics that filter closed positions by
-- account and recent close time.

CREATE INDEX idx_fp_account_status_close_time
ON futures_positions (account_id, status, close_time);

-- Speed up futures trade history count and newest-first pagination.
CREATE INDEX idx_ft_account_side_trade_time
ON futures_trades (account_id, side, trade_time);
