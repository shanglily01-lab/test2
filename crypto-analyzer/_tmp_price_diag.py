"""Diagnose entry price vs actual market price for gemini_explore positions."""
import pymysql
from app.utils.config_loader import get_db_config

cfg = get_db_config()
conn = pymysql.connect(**cfg, charset='utf8mb4', cursorclass=pymysql.cursors.DictCursor, autocommit=True)
cur = conn.cursor()

# 1. Get the latest gemini_explore open positions
print("=== 当前 Gemini 探索 OPEN 仓 ===")
cur.execute("""
SELECT id, symbol, position_side, entry_price, mark_price, 
  open_time, unrealized_pnl, unrealized_pnl_pct
FROM futures_positions
WHERE source='gemini_explore' AND status='open' AND account_id=2
ORDER BY open_time DESC
LIMIT 20
""")
rows = cur.fetchall()
for r in rows:
    print("  %-10s %-6s entry=%-10s mark=%-10s open=%s pnl=%s pnl_pct=%s%%" % (
        r['symbol'], r['position_side'], r['entry_price'], r['mark_price'],
        r['open_time'], r['unrealized_pnl'], r['unrealized_pnl_pct']))

print()

# 2. Get 5m kline closest to open time for each symbol
print("=== 15:10-15:15 的 5m kline 收盘价 ===")
cur.execute("""
SELECT symbol, close_price, FROM_UNIXTIME(open_time/1000) AS open_dt
FROM kline_data
WHERE exchange='binance_futures' AND timeframe='5m'
  AND open_time >= UNIX_TIMESTAMP('2026-06-05 15:05:00')*1000
  AND open_time < UNIX_TIMESTAMP('2026-06-05 15:20:00')*1000
  AND symbol IN ('XPLUSDT','ARKMUSDT','STRKUSDT','SUIUSDT','1000STOUSDT','BIOUSDT','BEATUSDT','TRUMPUSDT','JUPUSDT','WIFUSDT')
ORDER BY symbol, open_time
""")
for r in cur.fetchall():
    print("  %-10s close=%-10s @ %s" % (r['symbol'], r['close_price'], r['open_dt']))

print()

# 3. Check if the entry price matches any kline close at open time
print("=== Entry vs 5m kline close price comparison ===")
cur.execute("""
SELECT p.symbol, p.position_side, p.entry_price, p.open_time,
  k.close_price AS kline_close_5m,
  FROM_UNIXTIME(k.open_time/1000) AS kline_dt
FROM futures_positions p
LEFT JOIN kline_data k ON k.exchange='binance_futures' AND k.timeframe='5m'
  AND k.symbol = p.symbol
  AND k.open_time <= UNIX_TIMESTAMP(p.open_time)*1000
  AND k.open_time >= UNIX_TIMESTAMP(p.open_time)*1000 - 5*60*1000
WHERE p.source='gemini_explore' AND p.status='open' AND p.account_id=2
  AND p.open_time >= '2026-06-05 15:10:00'
ORDER BY p.open_time DESC
""")
for r in cur.fetchall():
    print("  %-10s %-6s entry=%-10s open=%s  5m_kline=%-10s @ %s" % (
        r['symbol'], r['position_side'], r['entry_price'],
        r['open_time'], r['kline_close_5m'] or 'N/A',
        r['kline_dt'] or 'N/A'))

conn.close()
