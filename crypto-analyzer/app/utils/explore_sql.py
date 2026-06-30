"""探索/预测页 SQL 片段 — 列表查询禁止触碰 LONGTEXT / 宽行."""

from __future__ import annotations

# runs 列表：不读 prompt_text / raw_response（平均 ~100KB/行）
RUNS_LIST_SQL = """
SELECT id, asof_utc, model, universe_size, trades_opened,
       elapsed_s, status, error_msg, triggered_by,
       LEFT(summary_zh, 200) AS summary_short, created_at,
       has_prompt, has_raw
FROM {table}
ORDER BY id DESC LIMIT %s
"""

# 兼容 migration 023 未执行的生产库
RUNS_LIST_SQL_LEGACY = """
SELECT id, asof_utc, model, universe_size, trades_opened,
       elapsed_s, status, error_msg, triggered_by,
       LEFT(summary_zh, 200) AS summary_short, created_at,
       0 AS has_prompt, 0 AS has_raw
FROM {table}
ORDER BY id DESC LIMIT %s
"""

PREDICT_RUNS_LIST_SQL = """
SELECT id, asof_utc, model, symbol_count, predictions_made, orders_opened,
       elapsed_s, status, error_msg, triggered_by,
       LEFT(summary_zh, 200) AS summary_short, created_at,
       has_prompt, has_raw
FROM {table}
ORDER BY id DESC LIMIT %s
"""

PREDICT_RUNS_LIST_SQL_LEGACY = """
SELECT id, asof_utc, model, symbol_count, predictions_made, orders_opened,
       elapsed_s, status, error_msg, triggered_by,
       LEFT(summary_zh, 200) AS summary_short, created_at,
       0 AS has_prompt, 0 AS has_raw
FROM {table}
ORDER BY id DESC LIMIT %s
"""

OPEN_POSITIONS_LIST_SQL = """
SELECT id, symbol, position_side, leverage, quantity,
       entry_price, mark_price,
       stop_loss_price, take_profit_price,
       stop_loss_pct, take_profit_pct,
       margin, unrealized_pnl, unrealized_pnl_pct,
       open_time, planned_close_time,
       entry_reason, source
FROM futures_positions
WHERE source=%s AND status='open' AND account_id=2
ORDER BY open_time DESC
LIMIT %s
"""

CLOSED_POSITIONS_LIST_SQL = """
SELECT id, symbol, position_side,
       entry_price, mark_price,
       margin, realized_pnl,
       open_time, close_time, notes, source
FROM futures_positions
WHERE source=%s AND status='closed' AND account_id=2
  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
ORDER BY close_time DESC, id DESC
LIMIT %s
"""

# refresh_position_stats：每 source 单次扫描（原 4 次 COUNT 聚合）
POSITION_STATS_AGG_SQL = """
SELECT
  SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
  COALESCE(SUM(CASE WHEN status = 'open' THEN unrealized_pnl ELSE 0 END), 0) AS floating_pnl,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 1 DAY) THEN 1 ELSE 0 END) AS closed_24h,
  COALESCE(SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 1 DAY) THEN realized_pnl ELSE 0 END), 0) AS pnl_24h,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN 1 ELSE 0 END) AS closed_7d,
  COALESCE(SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) THEN realized_pnl ELSE 0 END), 0) AS pnl_7d,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) THEN 1 ELSE 0 END) AS closed_30d,
  COALESCE(SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) THEN realized_pnl ELSE 0 END), 0) AS pnl_30d,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND realized_pnl > 0 THEN 1 ELSE 0 END) AS wins_30d,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses_30d,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND position_side = 'LONG' THEN 1 ELSE 0 END) AS long_cnt,
  SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND position_side = 'SHORT' THEN 1 ELSE 0 END) AS short_cnt,
  COALESCE(SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND position_side = 'LONG' THEN realized_pnl ELSE 0 END), 0) AS long_pnl,
  COALESCE(SUM(CASE WHEN status = 'closed' AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND position_side = 'SHORT' THEN realized_pnl ELSE 0 END), 0) AS short_pnl
FROM `{main_db}`.futures_positions
WHERE source = %s AND account_id = %s
  AND (status = 'open' OR close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY))
"""

POSITION_STATS_ALL_SQL = """
SELECT
  SUM(CASE WHEN close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) THEN 1 ELSE 0 END) AS closed_30d,
  COALESCE(SUM(CASE WHEN close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) THEN realized_pnl ELSE 0 END), 0) AS pnl_30d,
  SUM(CASE WHEN close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) AND realized_pnl > 0 THEN 1 ELSE 0 END) AS wins_30d
FROM `{main_db}`.futures_positions
WHERE status = 'closed' AND account_id = %s
  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
"""


def runs_list_sql(table: str, *, has_flag_columns: bool = True) -> str:
    tpl = RUNS_LIST_SQL if has_flag_columns else RUNS_LIST_SQL_LEGACY
    return tpl.format(table=table)


def predict_runs_list_sql(table: str, *, has_flag_columns: bool = True) -> str:
    tpl = PREDICT_RUNS_LIST_SQL if has_flag_columns else PREDICT_RUNS_LIST_SQL_LEGACY
    return tpl.format(table=table)


def prompt_flags(prompt_text, raw_response) -> tuple:
    hp = 1 if prompt_text else 0
    hr = 1 if raw_response else 0
    return hp, hr
