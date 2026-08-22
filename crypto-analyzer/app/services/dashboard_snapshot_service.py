"""
Dashboard 快照服务
每5分钟预计算所有 Dashboard 所需数据并存入 dashboard_snapshot 表，
前端调用 GET /api/dashboard/snapshot 可在毫秒内获取完整数据。
"""
from app.utils.config_loader import get_db_config
import json
import time
import pymysql
import os
from datetime import datetime, timezone
from loguru import logger

DATA_CACHE_DB = "data_cache"


def _get_conn(database: str = None, read_timeout: int = 90):
    cfg = get_db_config()
    if database:
        cfg = {**cfg, "database": database}
    if read_timeout is not None:
        cfg = {**cfg, "read_timeout": read_timeout}
    return pymysql.connect(
        **cfg,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
    )


def _ensure_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_snapshot (
            snapshot_key VARCHAR(50) PRIMARY KEY,
            snapshot_json MEDIUMTEXT NOT NULL,
            updated_at   DATETIME    NOT NULL,
            compute_ms   INT         DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def _fetch_signals(cursor):
    """信号表走 technical_signals_cache（coin_kline_scores EVENT 已下线）。"""
    from app.api.technical_signals_api import _flatten_signal_row

    try:
        cursor.execute("""
            SELECT symbol, window_label, timeframe,
                   total_klines, bullish_count, bearish_count,
                   bullish_pct, bearish_pct, avg_bullish_strength, avg_bearish_strength,
                   total_volume, trend, updated_at
            FROM technical_signals_cache
        """)
        rows = cursor.fetchall() or []
    except Exception as e:
        logger.warning(f"[dashboard_snapshot] technical_signals_cache 读取失败: {e}")
        return []
    data_map = {}
    updated_map = {}
    for row in rows:
        s = row["symbol"]
        data_map.setdefault(s, {}).setdefault(row["window_label"], {})[row["timeframe"]] = {
            "total_klines": int(row["total_klines"] or 0),
            "bullish_count": int(row["bullish_count"] or 0),
            "bearish_count": int(row["bearish_count"] or 0),
            "bullish_pct": float(row["bullish_pct"] or 0),
            "bearish_pct": float(row["bearish_pct"] or 0),
            "avg_bullish_strength": float(row["avg_bullish_strength"] or 0),
            "avg_bearish_strength": float(row["avg_bearish_strength"] or 0),
            "total_volume": float(row["total_volume"] or 0),
            "trend": row["trend"],
        }
        ua = row.get("updated_at")
        if ua and (s not in updated_map or ua > updated_map[s]):
            updated_map[s] = ua

    table = []
    for symbol, d in data_map.items():
        ua = updated_map.get(symbol)
        ua_iso = ua.isoformat() if ua else None
        table.append(_flatten_signal_row(symbol, d, None, ua_iso or ""))
    table.sort(key=lambda r: abs(float(r.get("total_score") or 0)), reverse=True)
    return table[:20]


def _fetch_stats(cursor, signal_count: int = 0):
    cursor.execute("""
        SELECT
            COUNT(*) AS total_opened,
            SUM(CASE WHEN status <> 'OPEN' THEN COALESCE(realized_pnl, 0) ELSE 0 END) AS today_pnl,
            SUM(CASE WHEN status <> 'OPEN' AND COALESCE(realized_pnl, 0) > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN status <> 'OPEN' THEN 1 ELSE 0 END) AS closed_count
        FROM futures_positions
        WHERE account_id = 2
          AND DATE(open_time) = CURDATE()
    """)
    r = cursor.fetchone() or {}
    closed = int(r.get('closed_count') or 0)
    wins   = int(r.get('wins')         or 0)
    return {
        'today_signals': int(signal_count or 0),
        'today_open':    int(r.get('total_opened') or 0),
        'today_pnl':     float(r.get('today_pnl') or 0),
        'win_rate':      round(wins / closed * 100, 1) if closed > 0 else None,
    }


def _fetch_futures(cursor):
    # Bulk fetch latest OI per symbol (2 queries total instead of N*2)
    cursor.execute("""
        SELECT t1.symbol, t1.open_interest, t1.timestamp
        FROM futures_open_interest t1
        INNER JOIN (
            SELECT symbol, MAX(timestamp) AS max_ts
            FROM futures_open_interest
            WHERE exchange = 'binance_futures'
            GROUP BY symbol
        ) t2 ON t1.symbol = t2.symbol AND t1.timestamp = t2.max_ts
        WHERE t1.exchange = 'binance_futures'
    """)
    oi_map = {}
    for r in cursor.fetchall():
        oi_map[r['symbol']] = {
            'open_interest': float(r['open_interest']),
            'timestamp':     r['timestamp'].isoformat() if r['timestamp'] else None,
        }

    # Bulk fetch latest LSR per symbol
    cursor.execute("""
        SELECT t1.symbol,
               t1.long_account, t1.short_account, t1.long_short_ratio,
               t1.long_position, t1.short_position, t1.long_short_position_ratio,
               t1.timestamp
        FROM futures_long_short_ratio t1
        INNER JOIN (
            SELECT symbol, MAX(timestamp) AS max_ts
            FROM futures_long_short_ratio
            GROUP BY symbol
        ) t2 ON t1.symbol = t2.symbol AND t1.timestamp = t2.max_ts
    """)
    lsr_map = {}
    for r in cursor.fetchall():
        lsr_map[r['symbol']] = {
            'long_account':  float(r['long_account'])  if r['long_account']  is not None else None,
            'short_account': float(r['short_account']) if r['short_account'] is not None else None,
            'ratio':         float(r['long_short_ratio']) if r['long_short_ratio'] is not None else None,
            'long_position':  float(r['long_position'])  if r['long_position']  is not None else None,
            'short_position': float(r['short_position']) if r['short_position'] is not None else None,
            'position_ratio': float(r['long_short_position_ratio']) if r['long_short_position_ratio'] is not None else None,
            'timestamp':      r['timestamp'].isoformat() if r['timestamp'] else None,
        }

    symbols = set(oi_map) | set(lsr_map)
    result = []
    for sym in sorted(symbols):
        oi  = oi_map.get(sym, {})
        lsr = lsr_map.get(sym, {})
        result.append({
            'symbol':        sym,
            'open_interest': oi.get('open_interest'),
            'timestamp':     oi.get('timestamp') or lsr.get('timestamp'),
            'long_short_ratio': {
                'long_account':  lsr.get('long_account'),
                'short_account': lsr.get('short_account'),
                'ratio':         lsr.get('ratio'),
            } if lsr else None,
            'long_short_position_ratio': {
                'long_position':  lsr.get('long_position'),
                'short_position': lsr.get('short_position'),
                'ratio':          lsr.get('position_ratio'),
            } if lsr else None,
        })
    return result


def _fetch_winrate_history(cursor):
    """近10日每日胜率 + 盈亏，直接从 futures_positions 计算"""
    cursor.execute("""
        SELECT
            DATE(close_time) AS date,
            COUNT(*) AS total,
            SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
            COALESCE(SUM(realized_pnl), 0) AS total_pnl,
            COALESCE(SUM(margin), 0) AS total_margin
        FROM futures_positions
        WHERE account_id = 2
          AND status = 'closed'
          AND close_time >= CURDATE() - INTERVAL 10 DAY
          AND close_time < CURDATE() + INTERVAL 1 DAY
        GROUP BY DATE(close_time)
        ORDER BY date DESC
    """)
    rows = cursor.fetchall()
    # Build lookup map keyed by date string
    data_by_date = {}
    for r in rows:
        d = r['date']
        date_str = d.strftime('%m/%d') if hasattr(d, 'strftime') else str(d)[5:10]
        total = int(r['total'] or 0)
        wins = int(r['wins'] or 0)
        win_rate = round(wins / total * 100, 1) if total > 0 else None
        total_pnl = float(r['total_pnl'] or 0)
        total_margin = float(r['total_margin'] or 0)
        roi = round(total_pnl / total_margin * 100, 1) if total_margin > 0 else None
        data_by_date[date_str] = {
            'date': date_str, 'win_rate': win_rate, 'total_pnl': total_pnl,
            'roi': roi, 'total_trades': total, 'wins': wins,
            'capture_rate': win_rate,
        }
    # Build ordered 10-day list: today-9 ... today
    from datetime import date, timedelta
    today = date.today()
    result = []
    for i in range(9, -1, -1):
        d = today - timedelta(days=i)
        ds = d.strftime('%m/%d')
        if ds in data_by_date:
            result.append(data_by_date[ds])
        else:
            result.append({
                'date': ds, 'win_rate': None, 'total_pnl': 0,
                'roi': None, 'total_trades': 0, 'wins': 0,
                'capture_rate': None,
            })
    return result


def _fetch_news(cursor):
    cursor.execute("""
        SELECT title, source, sentiment, symbols, published_datetime, url
        FROM news_data
        WHERE published_datetime >= NOW() - INTERVAL 24 HOUR
        ORDER BY published_datetime DESC
        LIMIT 20
    """)
    result = []
    for r in cursor.fetchall():
        result.append({
            'title':        r['title'],
            'source':       r['source'],
            'sentiment':    r['sentiment'],
            'symbols':      r['symbols'],
            'published_at': r['published_datetime'].strftime('%Y-%m-%d %H:%M UTC') if r['published_datetime'] else '',
            'url':          r['url'],
        })
    return result


def _fetch_hyperliquid(cursor):
    # Aggregated stats
    cursor.execute("""
        SELECT
            COALESCE(SUM(total_trades), 0) AS total_count,
            COALESCE(SUM(long_trades),  0) AS long_count,
            COALESCE(SUM(short_trades), 0) AS short_count,
            COALESCE(SUM(net_flow),     0) AS net_flow_usd,
            COUNT(DISTINCT symbol)         AS unique_coins,
            MAX(updated_at)                AS last_updated
        FROM hyperliquid_symbol_aggregation
        WHERE period = '24h'
    """)
    agg = cursor.fetchone() or {}
    long_count  = int(agg.get('long_count')  or 0)
    short_count = int(agg.get('short_count') or 0)
    ls_ratio = round(long_count / short_count, 2) if short_count > 0 else 0

    # Unique wallets (uses idx_trade_time index)
    cursor.execute("""
        SELECT COUNT(DISTINCT address) AS unique_wallets
        FROM hyperliquid_wallet_trades
        WHERE trade_time >= NOW() - INTERVAL 24 HOUR
    """)
    wrow = cursor.fetchone() or {}

    # Recent large trades (uses idx_trade_time + idx_notional indexes)
    cursor.execute("""
        SELECT coin, side, price, size, notional_usd, closed_pnl, trade_time
        FROM hyperliquid_wallet_trades
        WHERE trade_time >= NOW() - INTERVAL 24 HOUR
          AND notional_usd >= 100000
        ORDER BY notional_usd DESC
        LIMIT 30
    """)
    trades = []
    for t in cursor.fetchall():
        trades.append({
            'coin':        t['coin'],
            'action':      t['side'],
            'side':        t['side'],
            'price':       float(t['price']),
            'size':        float(t['size']),
            'notional_usd': float(t['notional_usd']),
            'closed_pnl':  float(t['closed_pnl']),
            'timestamp':   t['trade_time'].isoformat() if t['trade_time'] else None,
        })

    return {
        'statistics': {
            'total_count':      int(agg.get('total_count') or 0),
            'long_count':       long_count,
            'short_count':      short_count,
            'net_flow_usd':     float(agg.get('net_flow_usd') or 0),
            'unique_wallets':   int(wrow.get('unique_wallets') or 0),
            'unique_coins':     int(agg.get('unique_coins') or 0),
            'long_short_ratio': ls_ratio,
        },
        'trades': trades,
    }


DASHBOARD_LIVE_SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]


def _fetch_live_prices(cursor=None):
    """BTC/ETH/BNB/SOL 展示价：进程内 WS / DataHub，不读过期 market_snapshot。"""
    del cursor
    from app.utils.futures_price import build_ui_live_price_map

    price_map = build_ui_live_price_map(DASHBOARD_LIVE_SYMBOLS, max_age_seconds=8)
    return [
        {"symbol": s, "price": price_map.get(s)}
        for s in DASHBOARD_LIVE_SYMBOLS
    ]


def _fetch_recent_trades(cursor):
    """最新6条已平仓交易记录"""
    cursor.execute("""
        SELECT symbol, position_side AS direction, entry_price, close_price,
               realized_pnl, close_time, open_time
        FROM futures_positions
        WHERE account_id = 2
          AND status = 'closed'
          AND close_time IS NOT NULL
        ORDER BY close_time DESC
        LIMIT 6
    """)
    result = []
    for r in cursor.fetchall():
        pnl = float(r['realized_pnl']) if r['realized_pnl'] is not None else 0
        entry_price = float(r['entry_price']) if r['entry_price'] else 0
        open_time = r['open_time']
        close_time = r['close_time']
        result.append({
            'symbol':      r['symbol'],
            'direction':   (r['direction'] or 'LONG').upper(),
            'entry_price': entry_price,
            'close_price': float(r['close_price']) if r['close_price'] else 0,
            'realized_pnl': pnl,
            'close_time':  close_time.isoformat() if close_time else '',
            'open_time':   open_time.isoformat() if open_time else '',
        })
    return result


def update_dashboard_snapshot():
    """
    计算所有 Dashboard 数据并写入 dashboard_snapshot 表。
    调度器每5分钟调用一次，写入耗时通常 <500ms。
    """
    t0 = time.time()
    conn = None
    try:
        conn = _get_conn()
        cursor = conn.cursor()
        _ensure_table(cursor)
        conn.commit()

        signals         = _fetch_signals(cursor)
        stats           = _fetch_stats(cursor, signal_count=len(signals))
        futures         = _fetch_futures(cursor)
        news            = _fetch_news(cursor)
        hyperliquid     = _fetch_hyperliquid(cursor)
        winrate_history = _fetch_winrate_history(cursor)
        live_prices     = _fetch_live_prices(cursor)

        snapshot = {
            'signals':         signals,
            'stats':           stats,
            'futures':         futures,
            'news':            news,
            'hyperliquid':     hyperliquid,
            'winrate_history': winrate_history,
            'live_prices':     live_prices,
            'updated_at':      datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }

        snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
        compute_ms = int((time.time() - t0) * 1000)

        cursor.execute("""
            INSERT INTO dashboard_snapshot (snapshot_key, snapshot_json, updated_at, compute_ms)
            VALUES ('main', %s, NOW(), %s)
            ON DUPLICATE KEY UPDATE
                snapshot_json = VALUES(snapshot_json),
                updated_at    = VALUES(updated_at),
                compute_ms    = VALUES(compute_ms)
        """, (snapshot_json, compute_ms))
        conn.commit()
        cursor.close()
        logger.info(f"[dashboard_snapshot] updated in {compute_ms}ms, "
                    f"signals={len(signals)}, futures={len(futures)}, "
                    f"news={len(news)}, hl_trades={len(hyperliquid['trades'])}, "
                    f"live_prices={len(live_prices)}")
    except Exception as e:
        logger.error(f"[dashboard_snapshot] update failed: {e}")
        raise
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
