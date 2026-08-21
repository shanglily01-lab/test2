"""合约自选 / 手动下单常量 — docs/REQUIREMENTS_LOGIC_ZH.md §7.5"""
from __future__ import annotations

WATCHLIST_SOURCE = "manual_watchlist"
WATCHLIST_SOURCES = frozenset({WATCHLIST_SOURCE})
WATCHLIST_ACCOUNT_ID = 2
WATCHLIST_LEVERAGE = 5
WATCHLIST_DEFAULT_MARGIN_USD = 500.0
WATCHLIST_SL_PCT = 3.0
WATCHLIST_TP_PCT = 5.0
WATCHLIST_LIMIT_TIMEOUT_MINUTES = 8 * 60
WATCHLIST_MAX_SYMBOLS = 50
# 页面最新价：浏览器直连币安 U 本位公开 WS（miniTicker ~1s）；不在 crypto-app-main 再开 WS。
WATCHLIST_PRICE_WS_STREAM = "miniTicker"
WATCHLIST_BOOK_REFRESH_SECONDS = 30


def is_watchlist_source(source: str) -> bool:
    return (source or "").strip().lower() in WATCHLIST_SOURCES
