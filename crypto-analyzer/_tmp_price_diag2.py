"""Diagnose entry price source - check candidate_pool and kline for source of stale prices."""
import pymysql
from app.utils.config_loader import get_db_config

cfg = get_db_config()
conn = pymysql.connect(**cfg, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor, autocommit=True)
cur = conn.cursor()

# 1. Check candidate_pool_snapshot current_price for these symbols
print("=== candidate_pool_snapshot asof_utc & prices ===")
cur.execute("""
SELECT c.symbol, c.current_price, c.change_24h, s.asof_utc
FROM data_cache.candidate_pool_snapshot c
JOIN data_cache.candidate_pool_snapshot s ON s.symbol = c.symbol
WHERE c.symbol IN ('XPLUSDT','ARKMUSDT','STRKUSDT','SUIUSDT','STOUSDT','BIOUSDT','BEATUSDT','TRUMPUSDT','JUPUSDT','WIFUSDT')
ORDER BY c.symbol
""")
for r in cur.fetchall():
    print("  %-10s current_price=%-10s change_24h=%s asof=%s" % (
        r['symbol'], r['current_price'], r['change_24h'], r['asof_utc']))

# But wait, candidate_pool_snapshot doesn't have asof_utc as a column on every row
# Let me just get the price directly
print()
print("=== candidate_pool_snapshot prices ===")
cur.execute("""
SELECT symbol, current_price
FROM data_cache.candidate_pool_snapshot
WHERE symbol IN ('XPLUSDT','ARKMUSDT','STRKUSDT','SUIUSDT','STOUSDT','BIOUSDT','BEATUSDT')
ORDER BY symbol
""")
for r in cur.fetchall():
    print("  %-10s current_price=%s" % (r['symbol'], r['current_price']))

# 2. Check 1h kline close around 14:00-15:00 (the candle before open)
print()
print("=== 1h kline close (14:00-15:00 candle) ===")
cur.execute("""
SELECT symbol, close_price, FROM_UNIXTIME(open_time/1000) AS open_dt
FROM kline_data
WHERE exchange='binance_futures' AND timeframe='1h'
  AND open_time >= UNIX_TIMESTAMP('2026-06-05 14:00:00')*1000
  AND open_time < UNIX_TIMESTAMP('2026-06-05 15:00:00')*1000
  AND symbol IN ('XPLUSDT','ARKMUSDT','STRKUSDT','SUIUSDT','STOUSDT','BIOUSDT','BEATUSDT')
ORDER BY symbol, open_time
""")
rows1 = cur.fetchall()
for r in rows1:
    print("  %-10s close=%s @ %s" % (r['symbol'], r['close_price'], r['open_dt']))

# Also 15:00 candle (just started, may not be finished)
print()
print("=== 1h kline close (15:00-16:00 candle) ===")
cur.execute("""
SELECT symbol, close_price, FROM_UNIXTIME(open_time/1000) AS open_dt
FROM kline_data
WHERE exchange='binance_futures' AND timeframe='1h'
  AND open_time >= UNIX_TIMESTAMP('2026-06-05 15:00:00')*1000
  AND open_time < UNIX_TIMESTAMP('2026-06-05 16:00:00')*1000
  AND symbol IN ('XPLUSDT','ARKMUSDT','STRKUSDT','SUIUSDT','STOUSDT','BIOUSDT','BEATUSDT')
ORDER BY symbol, open_time
""")
for r in cur.fetchall():
    print("  %-10s close=%s @ %s" % (r['symbol'], r['close_price'], r['open_dt']))

# 3. Check the gemini_explore_runs asof_utc (the data timestamp for the LLM)
print()
print("=== 最新 gemini_explore 运行记录 ===")
cur.execute("""
SELECT id, asof_utc, status, triggered_by, universe_size, trades_opened, elapsed_s
FROM gemini_explore_runs
ORDER BY id DESC LIMIT 3
""")
for r in cur.fetchall():
    print("  id=%-4s asof_utc=%s status=%-8s triggered_by=%-10s trades=%s elapsed=%ss" % (
        r['id'], r['asof_utc'], r['status'], r['triggered_by'], r['trades_opened'], r['elapsed_s']))

# 4. Check the LIVE hub prices vs entry prices - match?
print()
print("=== XPLUSDT 1h kline prices for ALL recent candles ===")
cur.execute("""
SELECT close_price, FROM_UNIXTIME(open_time/1000) AS open_dt
FROM kline_data
WHERE exchange='binance_futures' AND timeframe='1h' AND symbol='XPLUSDT'
ORDER BY open_time DESC
LIMIT 24
""")
for r in cur.fetchall():
    print("  close=%-10s @ %s" % (r['close_price'], r['open_dt']))

conn.close()
