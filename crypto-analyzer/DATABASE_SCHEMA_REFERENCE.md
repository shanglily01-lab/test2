# 数据库表结构参考手册

**数据库**: binance-data
**服务器**: 13.212.252.171:3306
**总表数**: 96

---

## 📋 超级大脑核心表

### futures_positions

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| account_id | int(11) | NO | MUL |  |  |
| user_id | int(11) | YES | MUL | 1 |  |
| symbol | varchar(20) | NO | MUL |  |  |
| position_side | varchar(10) | NO | MUL |  |  |
| leverage | int(11) | NO |  | 1 |  |
| quantity | decimal(18,8) | NO |  |  |  |
| notional_value | decimal(20,2) | NO |  |  |  |
| margin | decimal(20,2) | NO |  |  |  |
| entry_price | decimal(18,8) | NO |  |  |  |
| mark_price | decimal(18,8) | YES |  |  |  |
| liquidation_price | decimal(18,8) | YES |  |  |  |
| unrealized_pnl | decimal(20,2) | YES |  | 0.00 |  |
| unrealized_pnl_pct | decimal(10,4) | YES |  | 0.0000 |  |
| realized_pnl | decimal(20,2) | YES |  | 0.00 |  |
| stop_loss_price | decimal(18,8) | YES |  |  |  |
| take_profit_price | decimal(18,8) | YES |  |  |  |
| stop_loss_pct | decimal(5,2) | YES |  |  |  |
| take_profit_pct | decimal(5,2) | YES |  |  |  |
| entry_ema_diff | decimal(18,8) | YES |  |  |  |
| total_funding_fee | decimal(20,8) | YES |  | 0.00000000 |  |
| open_time | datetime | NO | MUL |  |  |
| last_update_time | datetime | YES |  |  |  |
| close_time | datetime | YES |  |  |  |
| holding_hours | int(11) | YES |  | 0 |  |
| status | varchar(20) | YES | MUL | open |  |
| source | varchar(50) | YES |  | manual |  |
| signal_id | int(11) | YES |  |  |  |
| strategy_id | bigint(20) | YES | MUL |  |  |
| notes | text | YES |  |  |  |
| created_at | datetime | NO |  | current_timestamp() |  |
| updated_at | datetime | NO |  | current_timestamp() | on update current_timestamp() |
| max_profit_pct | decimal(10,4) | YES |  | 0.0000 |  |
| max_profit_price | decimal(18,8) | YES |  |  |  |
| trailing_stop_activated | tinyint(1) | YES |  | 0 |  |
| trailing_stop_price | decimal(18,8) | YES |  |  |  |
| entry_signal_type | varchar(50) | YES |  |  |  |
| entry_score | int(11) | YES |  |  |  |
| signal_components | text | YES |  |  |  |
| entry_reason | varchar(500) | YES |  |  |  |
| live_position_id | int(11) | YES |  |  |  |

### signal_scoring_weights

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| signal_component | varchar(50) | NO | UNI |  |  |
| weight_long | decimal(5,2) | NO |  |  |  |
| weight_short | decimal(5,2) | NO |  |  |  |
| base_weight | decimal(5,2) | NO |  |  |  |
| performance_score | decimal(5,2) | YES |  |  |  |
| last_adjusted | timestamp | NO |  | current_timestamp() | on update current_timestamp() |
| adjustment_count | int(11) | YES |  | 0 |  |
| description | varchar(255) | YES |  |  |  |
| is_active | tinyint(1) | YES | MUL | 1 |  |
| updated_at | timestamp | NO |  | current_timestamp() | on update current_timestamp() |

### signal_component_performance

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| component_name | varchar(50) | NO | MUL |  |  |
| position_side | varchar(10) | NO | MUL |  |  |
| total_orders | int(11) | YES |  | 0 |  |
| win_orders | int(11) | YES |  | 0 |  |
| total_pnl | decimal(15,2) | YES |  | 0.00 |  |
| avg_pnl | decimal(10,2) | YES |  |  |  |
| win_rate | decimal(5,4) | YES |  |  |  |
| contribution_score | decimal(5,2) | YES |  |  |  |
| last_analyzed | timestamp | NO | MUL | current_timestamp() | on update current_timestamp() |
| updated_at | timestamp | NO |  | current_timestamp() | on update current_timestamp() |

### adaptive_params

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| param_key | varchar(100) | NO | UNI |  |  |
| param_value | decimal(10,6) | NO |  |  |  |
| param_type | varchar(50) | NO | MUL |  |  |
| description | varchar(255) | YES |  |  |  |
| updated_at | timestamp | NO | MUL | current_timestamp() | on update current_timestamp() |
| updated_by | varchar(100) | YES |  | adaptive_optimizer |  |

### optimization_history

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | bigint(20) | NO | PRI |  | auto_increment |
| optimization_type | varchar(50) | NO | MUL |  |  |
| target_name | varchar(100) | NO |  |  |  |
| param_name | varchar(50) | NO |  |  |  |
| old_value | decimal(10,6) | YES |  |  |  |
| new_value | decimal(10,6) | YES |  |  |  |
| change_amount | decimal(10,6) | YES |  |  |  |
| sample_size | int(11) | YES |  |  |  |
| win_rate | decimal(5,4) | YES |  |  |  |
| reason | text | YES |  |  |  |
| optimized_at | timestamp | NO |  | current_timestamp() |  |

### signal_blacklist

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| signal_type | varchar(50) | NO | MUL |  |  |
| position_side | varchar(10) | NO | MUL |  |  |
| reason | varchar(255) | YES |  |  |  |
| total_loss | decimal(15,2) | YES |  |  |  |
| win_rate | decimal(5,4) | YES |  |  |  |
| order_count | int(11) | YES |  |  |  |
| created_at | timestamp | NO |  | current_timestamp() |  |
| updated_at | timestamp | NO |  | current_timestamp() | on update current_timestamp() |
| is_active | tinyint(1) | YES | MUL | 1 |  |
| notes | text | YES |  |  |  |

### trading_blacklist

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| symbol | varchar(50) | NO | UNI |  |  |
| reason | varchar(255) | YES |  |  |  |
| total_loss | decimal(15,2) | YES |  |  |  |
| win_rate | decimal(5,4) | YES |  |  |  |
| order_count | int(11) | YES |  |  |  |
| created_at | timestamp | NO | MUL | current_timestamp() |  |
| updated_at | timestamp | NO |  | current_timestamp() | on update current_timestamp() |
| is_active | tinyint(1) | YES | MUL | 1 |  |

---


## 补充表结构

### symbol_risk_params

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| symbol | varchar(20) | NO | UNI |  |  |
| long_take_profit_pct | decimal(6,4) | YES |  | 0.0500 |  |
| long_stop_loss_pct | decimal(6,4) | YES |  | 0.0200 |  |
| short_take_profit_pct | decimal(6,4) | YES |  | 0.0500 |  |
| short_stop_loss_pct | decimal(6,4) | YES |  | 0.0200 |  |
| position_multiplier | decimal(5,2) | YES |  | 1.00 |  |
| total_trades | int(11) | YES |  | 0 |  |
| win_rate | decimal(5,4) | YES | MUL | 0.0000 |  |
| avg_pnl | decimal(10,2) | YES |  | 0.00 |  |
| total_pnl | decimal(15,2) | YES |  | 0.00 |  |
| sharpe_ratio | decimal(6,3) | YES |  | 0.000 |  |
| avg_volatility | decimal(6,4) | YES |  | 0.0000 |  |
| max_drawdown | decimal(6,4) | YES |  | 0.0000 |  |
| last_optimized | timestamp | YES | MUL |  |  |
| optimization_count | int(11) | YES |  | 0 |  |
| is_active | tinyint(1) | YES |  | 1 |  |
| created_at | timestamp | NO |  | current_timestamp() |  |
| updated_at | timestamp | NO |  | current_timestamp() | on update current_timestamp() |

### signal_position_multipliers

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | int(11) | NO | PRI |  | auto_increment |
| component_name | varchar(50) | NO | MUL |  |  |
| position_side | varchar(10) | NO |  |  |  |
| position_multiplier | decimal(5,2) | YES |  | 1.00 |  |
| total_trades | int(11) | YES |  | 0 |  |
| win_rate | decimal(5,4) | YES | MUL | 0.0000 |  |
| avg_pnl | decimal(10,2) | YES |  | 0.00 |  |
| total_pnl | decimal(15,2) | YES |  | 0.00 |  |
| last_analyzed | timestamp | YES |  |  |  |
| adjustment_count | int(11) | YES |  | 0 |  |
| is_active | tinyint(1) | YES |  | 1 |  |
| created_at | timestamp | NO |  | current_timestamp() |  |
| updated_at | timestamp | NO |  | current_timestamp() | on update current_timestamp() |

### market_observations

| 字段名 | 类型 | 允许NULL | 键 | 默认值 | 额外 |
|--------|------|----------|-----|--------|------|
| id | bigint(20) | NO | PRI |  | auto_increment |
| timestamp | timestamp | NO | MUL | current_timestamp() | on update current_timestamp() |
| overall_trend | varchar(20) | YES |  |  |  |
| market_strength | decimal(5,2) | YES |  |  |  |
| bullish_count | int(11) | YES |  |  |  |
| bearish_count | int(11) | YES |  |  |  |
| neutral_count | int(11) | YES |  |  |  |
| btc_price | decimal(12,2) | YES |  |  |  |
| btc_trend | varchar(20) | YES |  |  |  |
| eth_price | decimal(12,2) | YES |  |  |  |
| eth_trend | varchar(20) | YES |  |  |  |
| warnings | text | YES |  |  |  |
| created_at | timestamp | NO |  | current_timestamp() |  |


---

## 📊 所有表列表

1. `adaptive_params`
2. `blockchain_gas_daily`
3. `blockchain_gas_daily_summary`
4. `corporate_treasury_companies`
5. `corporate_treasury_financing`
6. `corporate_treasury_purchases`
7. `corporate_treasury_stock_prices`
8. `corporate_treasury_summary`
9. `crypto_etf_daily_summary`
10. `crypto_etf_events`
11. `crypto_etf_flows`
12. `crypto_etf_products`
13. `crypto_etf_sentiment`
14. `ema_signals`
15. `funding_rate_data`
16. `funding_rate_stats`
17. `futures_funding_fees`
18. `futures_liquidations`
19. `futures_long_short_ratio`
20. `futures_open_interest`
21. `futures_orders`
22. `futures_positions`
23. `futures_positions_backup_20260121_133838`
24. `futures_trades`
25. `hyperliquid_leaderboard_history`
26. `hyperliquid_monitored_wallets`
27. `hyperliquid_monthly_performance`
28. `hyperliquid_performance_snapshots`
29. `hyperliquid_symbol_aggregation`
30. `hyperliquid_traders`
31. `hyperliquid_wallet_fund_changes`
32. `hyperliquid_wallet_positions`
33. `hyperliquid_wallet_trades`
34. `hyperliquid_weekly_performance`
35. `investment_recommendations`
36. `investment_recommendations_cache`
37. `kline_data`
38. `live_futures_orders`
39. `live_futures_positions`
40. `live_futures_trades`
41. `live_trading_accounts`
42. `live_trading_logs`
43. `login_logs`
44. `market_observations`
45. `market_regime`
46. `market_regime_changes`
47. `news_data`
48. `news_sentiment_aggregation`
49. `optimization_history`
50. `optimization_history_old`
51. `orderbook_data`
52. `paper_trading_accounts`
53. `paper_trading_balance_history`
54. `paper_trading_orders`
55. `paper_trading_pending_orders`
56. `paper_trading_positions`
57. `paper_trading_signal_executions`
58. `paper_trading_trades`
59. `pending_positions`
60. `price_data`
61. `price_stats_24h`
62. `refresh_tokens`
63. `sentinel_orders`
64. `signal_blacklist`
65. `signal_component_performance`
66. `signal_position_multipliers`
67. `signal_scoring_weights`
68. `smart_money_addresses`
69. `smart_money_signals`
70. `smart_money_transactions`
71. `strategy_capital_management`
72. `strategy_execution_result_details`
73. `strategy_execution_results`
74. `strategy_hits`
75. `strategy_regime_params`
76. `strategy_test_records`
77. `strategy_test_result_details`
78. `strategy_test_results`
79. `strategy_trade_records`
80. `symbol_risk_params`
81. `system_status`
82. `technical_indicators_cache`
83. `trade_data`
84. `trades`
85. `trading_blacklist`
86. `trading_cooldowns`
87. `trading_strategies`
88. `user_api_keys`
89. `users`
90. `v_etf_daily_flows`
91. `v_etf_latest_flows`
92. `v_etf_weekly_summary`
93. `v_hyperliquid_active_monitors`
94. `v_hyperliquid_latest_positions`
95. `v_hyperliquid_trader_history`
96. `v_hyperliquid_weekly_leaderboard`

---

*导出时间: <class 'datetime.date'>*
