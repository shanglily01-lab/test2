"""
数据缓存层 — 定时刷新所有预计算缓存表

定时任务（由 scheduler.py 注册）：
  - refresh_market_snapshot:   每 1 分钟
  - refresh_market_movers:     每 5 分钟
  - refresh_candidate_pool:    每 6 分钟
  - refresh_position_stats:    每 30 分钟
  - sync_settings_cache:       写时触发（由 system_settings 修改时调用）

所有刷新任务都使用简短、表亲和的 SQL，避免 kline_data 亿级 JOIN。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pymysql
import pymysql.cursors
from loguru import logger

from app.utils.config_loader import get_db_config

DATA_CACHE_DB = "data_cache"
# 主数据库名 — 懒加载, 避免 import 时抛出异常
MAIN_DB = None

def _ensure_main_db():
    global MAIN_DB
    if MAIN_DB is None:
        cfg = get_db_config()
        MAIN_DB = cfg.get("database", "binance-data")

EXCLUDED_PREFIXES = (
    "USDCUSDT", "USDTUSDT", "BUSDUSDT", "DAIUSDT",
    "FDUSDUSDT", "TUSDUSDT", "PAXUSDT",
)


# ============================================================
# DB 连接
# ============================================================
def _get_conn(database: str = None) -> pymysql.Connection:
    cfg = get_db_config()
    if database:
        cfg["database"] = database
    return pymysql.connect(
        **cfg,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _is_excluded(symbol: str) -> bool:
    s = symbol.upper().replace("/", "")
    for p in EXCLUDED_PREFIXES:
        if s.startswith(p) or s.endswith(p):
            return True
    return False


# ============================================================
# 3.1 市场概览快照 (1min)
# ============================================================
def refresh_market_snapshot() -> dict:
    """
    汇总 BTC/ETH/SOL 价格、Big4 信号、恐惧贪婪指数, 写入 market_snapshot。
    """
    t0 = time.time()
    stat = {"status": "ok", "elapsed_ms": 0}
    try:
        _ensure_main_db()
        conn = _get_conn()
        with conn.cursor() as cur:
            prices = {}
            core_symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
            placeholders = ",".join("%s" for _ in core_symbols)
            cur.execute(
                f"SELECT symbol, current_price, change_24h "
                f"FROM `{MAIN_DB}`.price_stats_24h "
                f"WHERE symbol IN ({placeholders})",
                core_symbols,
            )
            for r in cur.fetchall():
                prices[r["symbol"]] = r

            # 2) Big4 信号
            big4 = None
            try:
                cur.execute(
                    "SELECT overall_signal FROM "
                    f"`{MAIN_DB}`.big4_trend_history "
                    "ORDER BY created_at DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    big4 = row.get("overall_signal", "NEUTRAL")
            except Exception:
                pass

            # 3) 恐惧贪婪
            fg_val, fg_label = None, None
            try:
                cur.execute(
                    "SELECT value, value_classification FROM "
                    f"`{MAIN_DB}`.fear_greed_index "
                    "ORDER BY update_time DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    fg_val = int(row["value"]) if row.get("value") else None
                    fg_label = row.get("value_classification")
            except Exception:
                pass

            # 写入 data_cache.market_snapshot
            p = lambda sym: prices.get(sym, {}) or {}
            sql = """
                REPLACE INTO data_cache.market_snapshot
                    (id, btc_price, btc_change_24h,
                     eth_price, eth_change_24h,
                     sol_price, sol_change_24h,
                     bnb_price, bnb_change_24h,
                     xrp_price, xrp_change_24h,
                     big4_signal, fear_greed_value, fear_greed_label,
                     compute_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s)
            """
            cur.execute(sql, (
                1,
                p("BTC/USDT").get("current_price"), p("BTC/USDT").get("change_24h"),
                p("ETH/USDT").get("current_price"), p("ETH/USDT").get("change_24h"),
                p("SOL/USDT").get("current_price"), p("SOL/USDT").get("change_24h"),
                p("BNB/USDT").get("current_price"), p("BNB/USDT").get("change_24h"),
                p("XRP/USDT").get("current_price"), p("XRP/USDT").get("change_24h"),
                big4, fg_val, fg_label,
                int((time.time() - t0) * 1000),
            ))

        elapsed = int((time.time() - t0) * 1000)
        stat["elapsed_ms"] = elapsed
        logger.debug(f"[cache] market_snapshot refreshed in {elapsed}ms")
    except Exception as e:
        stat["status"] = f"error: {e}"
        logger.error(f"[cache] refresh_market_snapshot failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stat


# ============================================================
# 3.2 市场异动快照 (5min)
# ============================================================
def refresh_market_movers() -> dict:
    """
    涨跌榜 + 资金费率极端 + 成交量异动, 写入 market_movers_snapshot。
    先用 DELETE 清理旧数据, 再批量 INSERT。
    """
    t0 = time.time()
    stat = {"status": "ok", "elapsed_ms": 0}
    try:
        _ensure_main_db()
        conn = _get_conn()
        with conn.cursor() as cur:
            # 清空旧数据
            cur.execute("DELETE FROM data_cache.market_movers_snapshot")

            main_db = MAIN_DB
            inserted = 0

            # --- 涨幅榜 gainers ---
            cur.execute(
                f"SELECT symbol, change_24h AS val "
                f"FROM `{MAIN_DB}`.price_stats_24h "
                f"WHERE quote_volume_24h >= 5000000 "
                f"  AND symbol LIKE '%%/USDT' "
                f"ORDER BY change_24h DESC LIMIT 20"
            )
            for rank, r in enumerate(cur.fetchall(), 1):
                sym = r["symbol"]
                if _is_excluded(sym):
                    continue
                cur.execute(
                    "INSERT INTO data_cache.market_movers_snapshot "
                    "(category, symbol, value, rank_no) VALUES (%s, %s, %s, %s)",
                    ("gainers", sym, r["val"], rank),
                )
                inserted += 1

            # --- 跌幅榜 losers ---
            cur.execute(
                f"SELECT symbol, change_24h AS val "
                f"FROM `{MAIN_DB}`.price_stats_24h "
                f"WHERE quote_volume_24h >= 5000000 "
                f"  AND symbol LIKE '%%/USDT' "
                f"ORDER BY change_24h ASC LIMIT 20"
            )
            for rank, r in enumerate(cur.fetchall(), 1):
                sym = r["symbol"]
                if _is_excluded(sym):
                    continue
                cur.execute(
                    "INSERT INTO data_cache.market_movers_snapshot "
                    "(category, symbol, value, rank_no) VALUES (%s, %s, %s, %s)",
                    ("losers", sym, r["val"], rank),
                )
                inserted += 1

            # --- 资金费率最高 ---
            cur.execute(
                f"SELECT t.symbol, t.funding_rate AS val "
                f"FROM `{MAIN_DB}`.funding_rate_data t "
                f"INNER JOIN ("
                f"  SELECT symbol, MAX(funding_time) AS max_ft "
                f"  FROM `{MAIN_DB}`.funding_rate_data "
                f"  GROUP BY symbol"
                f") latest ON t.symbol = latest.symbol AND t.funding_time = latest.max_ft "
                f"ORDER BY t.funding_rate DESC LIMIT 10"
            )
            for rank, r in enumerate(cur.fetchall(), 1):
                sym = r["symbol"].replace("/", "")
                if _is_excluded(sym):
                    continue
                cur.execute(
                    "INSERT INTO data_cache.market_movers_snapshot "
                    "(category, symbol, value, rank_no) VALUES (%s, %s, %s, %s)",
                    ("funding_high", sym, r["val"], rank),
                )
                inserted += 1

            # --- 资金费率最低 ---
            cur.execute(
                f"SELECT t.symbol, t.funding_rate AS val "
                f"FROM `{MAIN_DB}`.funding_rate_data t "
                f"INNER JOIN ("
                f"  SELECT symbol, MAX(funding_time) AS max_ft "
                f"  FROM `{MAIN_DB}`.funding_rate_data "
                f"  GROUP BY symbol"
                f") latest ON t.symbol = latest.symbol AND t.funding_time = latest.max_ft "
                f"ORDER BY t.funding_rate ASC LIMIT 10"
            )
            for rank, r in enumerate(cur.fetchall(), 1):
                sym = r["symbol"].replace("/", "")
                if _is_excluded(sym):
                    continue
                cur.execute(
                    "INSERT INTO data_cache.market_movers_snapshot "
                    "(category, symbol, value, rank_no) VALUES (%s, %s, %s, %s)",
                    ("funding_low", sym, r["val"], rank),
                )
                inserted += 1

        elapsed = int((time.time() - t0) * 1000)
        stat["inserted"] = inserted
        stat["elapsed_ms"] = elapsed
        logger.debug(f"[cache] market_movers_snapshot refreshed in {elapsed}ms ({inserted} rows)")
    except Exception as e:
        stat["status"] = f"error: {e}"
        logger.error(f"[cache] refresh_market_movers failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stat


# ============================================================
# RSI / EMA 工具
# ============================================================
def _calc_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if not closes or len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    return 100 - 100 / (1 + avg_gain / avg_loss)


def _calc_ema(values: List[float], period: int) -> Optional[float]:
    if not values or len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


# ============================================================
# K 线叙事生成
# ============================================================
def _make_kline_narrative(k_rows: List[Dict], timeframe_label: str) -> str:
    """生成 K 线自然语言描述."""
    if not k_rows:
        return f"[{timeframe_label}] 无数据"
    lines = []
    prices = []
    total_vol = 0.0
    up_count = down_count = 0
    for r in k_rows:
        try:
            t = datetime.utcfromtimestamp(float(r["open_time"]) / 1000)
            o = float(r["open_price"])
            h = float(r["high_price"])
            l = float(r["low_price"])
            c = float(r["close_price"])
            v = float(r.get("volume") or 0)
            pct = (c - o) / o * 100 if o > 0 else 0
            prices.append(c)
            total_vol += v
            if pct >= 0.1:
                up_count += 1
            elif pct <= -0.1:
                down_count += 1
            if len(lines) < 4:
                t_str = t.strftime("%H:%M") if timeframe_label in ("15m", "1h") else t.strftime("%m-%d")
                conv = "↑" if pct >= 0.1 else ("↓" if pct <= -0.1 else "→")
                lines.append(
                    f"  {t_str}: O={o:.6g} H={h:.6g} L={l:.6g} C={c:.6g} "
                    f"({pct:+.2%}){conv} Vol={v:.4g}"
                )
        except Exception:
            continue
    n = len(prices)
    desc = "数据不足"
    if n >= 3:
        change_overall = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
        trend = "上升" if prices[-1] > prices[0] else "下降"
        if up_count > down_count * 1.5 and change_overall > 1:
            desc = f"偏多 ({up_count}阳/{down_count}阴, 整体{change_overall:+.1f}%)"
        elif down_count > up_count * 1.5 and change_overall < -1:
            desc = f"偏空 ({up_count}阳/{down_count}阴, 整体{change_overall:+.1f}%)"
        elif change_overall > 3:
            desc = f"强势{trend} ({change_overall:+.1f}%)"
        elif change_overall < -3:
            desc = f"强势{trend} ({change_overall:+.1f}%)"
        else:
            desc = f"震荡 ({trend}趋势, {change_overall:+.1f}%)"
    header = f"[{timeframe_label} · 最近 {len(k_rows)} 根] (总成交量 {total_vol:.4g})"
    body = "\n".join(lines[:4])
    if len(lines) > 4:
        body += f"\n  ... 还有 {len(lines) - 4} 根 (略)"
    return f"{header}\n{body}\n形态: {desc}"


# ============================================================
# 3.4 候选交易对池 (6min)
# ============================================================
_CANDIDATE_POOL_UPSERT_SQL = """
INSERT INTO data_cache.candidate_pool_snapshot
  (symbol, exchange, current_price, change_24h, quote_volume_24h,
   funding_rate, rsi_14, ema_9, ema_21,
   kline_1h_json, kline_15m_json, kline_1d_json,
   narrative_1h, narrative_15m, narrative_1d,
   above_7d_low_pct, below_7d_high_pct)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
  current_price=VALUES(current_price),
  change_24h=VALUES(change_24h),
  quote_volume_24h=VALUES(quote_volume_24h),
  funding_rate=VALUES(funding_rate),
  rsi_14=VALUES(rsi_14),
  ema_9=VALUES(ema_9),
  ema_21=VALUES(ema_21),
  kline_1h_json=VALUES(kline_1h_json),
  kline_15m_json=VALUES(kline_15m_json),
  kline_1d_json=VALUES(kline_1d_json),
  narrative_1h=VALUES(narrative_1h),
  narrative_15m=VALUES(narrative_15m),
  narrative_1d=VALUES(narrative_1d),
  above_7d_low_pct=VALUES(above_7d_low_pct),
  below_7d_high_pct=VALUES(below_7d_high_pct),
  updated_at=CURRENT_TIMESTAMP
"""


def refresh_candidate_pool() -> dict:
    """
    一次性刷新所有候选交易对的数据:
      - 24h 行情 (从 price_stats_24h)
      - 资金费率 (从 funding_rate_data)
      - 技术指标 (1h RSI, EMA)
      - K 线 JSON + 叙事 (从 kline_data)

    使用 UPSERT 就地更新, 刷新过程中表始终有上一版数据可供探索读取;
    本轮结束后才删除已下架 symbol (避免先 DELETE 导致空窗)。
    """
    t0 = time.time()
    stat = {"status": "ok", "elapsed_ms": 0, "symbols": 0}
    try:
        _ensure_main_db()
        conn = _get_conn()
        with conn.cursor() as cur:
            main_db = MAIN_DB
            refreshed_symbols: List[str] = []

            # 1) 获取所有候选 symbol (有成交量的 /USDT 交易对)
            cur.execute(
                f"SELECT symbol, current_price, change_24h, "
                f"       quote_volume_24h "
                f"FROM `{MAIN_DB}`.price_stats_24h "
                f"WHERE symbol LIKE '%%/USDT' "
                f"  AND quote_volume_24h >= 1000000 "
                f"ORDER BY quote_volume_24h DESC"
            )
            candidates = cur.fetchall()

            # 3) 一次性获取所有资金费率
            cur.execute(
                f"SELECT t.symbol, t.funding_rate "
                f"FROM `{MAIN_DB}`.funding_rate_data t "
                f"INNER JOIN ("
                f"  SELECT symbol, MAX(funding_time) AS max_ft "
                f"  FROM `{MAIN_DB}`.funding_rate_data "
                f"  GROUP BY symbol"
                f") latest ON t.symbol = latest.symbol AND t.funding_time = latest.max_ft"
            )
            funding_map = {}
            for r in cur.fetchall():
                funding_map[r["symbol"]] = r.get("funding_rate")

            upserted = 0
            for c in candidates:
                sym = c["symbol"]
                if _is_excluded(sym):
                    continue

                # 获取 K 线数据
                kline_1h = _fetch_klines(cur, main_db, sym, "1h", 12)
                kline_15m = _fetch_klines(cur, main_db, sym, "15m", 8)
                kline_1d = _fetch_klines(cur, main_db, sym, "1d", 7)

                # 技术指标
                closes_1h = [float(r["close_price"]) for r in kline_1h if r.get("close_price")]
                rsi_14 = _calc_rsi(closes_1h, 14) if len(closes_1h) >= 15 else None
                ema_9 = _calc_ema(closes_1h, 9) if len(closes_1h) >= 9 else None
                ema_21 = _calc_ema(closes_1h, 21) if len(closes_1h) >= 21 else None

                # 距 7d 高/低距离
                above_low = below_high = None
                sym_price = float(c["current_price"]) if c.get("current_price") else None
                if sym_price and kline_1d:
                    try:
                        highs = [float(r["high_price"]) for r in kline_1d if r.get("high_price")]
                        lows = [float(r["low_price"]) for r in kline_1d if r.get("low_price")]
                        if highs and lows:
                            h7 = max(highs)
                            l7 = min(lows)
                            if l7 > 0:
                                above_low = round((sym_price - l7) / l7 * 100, 2)
                            if h7 > 0:
                                below_high = round((sym_price - h7) / h7 * 100, 2)
                    except Exception:
                        pass

                # 格式化 K 线到 JSON
                kline_1h_json = json.dumps(
                    [{"ot": r["open_time"], "o": float(r["open_price"]),
                      "h": float(r["high_price"]), "l": float(r["low_price"]),
                      "c": float(r["close_price"]), "v": float(r.get("volume") or 0)}
                     for r in kline_1h] if kline_1h else [],
                    default=str,
                )
                # 叙事文本 (前 4 root)
                narr_1h = _make_kline_narrative(kline_1h, "1h") if kline_1h else ""
                narr_15m = _make_kline_narrative(kline_15m, "15m") if kline_15m else ""
                narr_1d = _make_kline_narrative(kline_1d, "1d") if kline_1d else ""

                # 资金费率 (按 symbol 匹配，注意 /USDT 格式)
                fr = funding_map.get(sym) or funding_map.get(sym.replace("/", ""))

                cur.execute(
                    _CANDIDATE_POOL_UPSERT_SQL,
                    (
                        sym, "binance_futures",
                        c.get("current_price"), c.get("change_24h"), c.get("quote_volume_24h"),
                        fr, rsi_14, ema_9, ema_21,
                        kline_1h_json, None, None,
                        narr_1h, narr_15m, narr_1d,
                        above_low, below_high,
                    ),
                )
                refreshed_symbols.append(sym)
                upserted += 1

                # 每 200 个 symbol 提交一次
                if upserted % 200 == 0:
                    conn.commit()

            # 2) 移除本轮未出现的 symbol (已下架/不再满足成交量), 不先删全表
            if refreshed_symbols:
                placeholders = ",".join(["%s"] * len(refreshed_symbols))
                cur.execute(
                    "DELETE FROM data_cache.candidate_pool_snapshot "
                    "WHERE exchange='binance_futures' "
                    f"AND symbol NOT IN ({placeholders})",
                    refreshed_symbols,
                )

            conn.commit()

        elapsed = int((time.time() - t0) * 1000)
        stat["symbols"] = upserted
        stat["elapsed_ms"] = elapsed
        logger.info(
            f"[cache] candidate_pool_snapshot refreshed in {elapsed}ms "
            f"({upserted} symbols, upsert)"
        )
    except Exception as e:
        stat["status"] = f"error: {e}"
        logger.error(f"[cache] refresh_candidate_pool failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stat


def _fetch_klines(cur, main_db: str, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    try:
        cur.execute(
            f"SELECT open_time, open_price, high_price, low_price, close_price, volume "
            f"FROM `{MAIN_DB}`.kline_data "
            f"WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
            f"ORDER BY open_time DESC LIMIT %s",
            (symbol, timeframe, limit),
        )
        return list(reversed(cur.fetchall()))
    except Exception:
        return []


# ============================================================
# 3.3 持仓统计快照 (30min)
# ============================================================
def refresh_position_stats() -> dict:
    """
    从 futures_positions 一次性聚合各 source 的统计，写入 position_stats_snapshot。
    """
    t0 = time.time()
    stat = {"status": "ok", "elapsed_ms": 0}
    try:
        _ensure_main_db()
        conn = _get_conn()
        with conn.cursor() as cur:

            # 清空旧数据
            cur.execute("DELETE FROM data_cache.position_stats_snapshot")

            sources = ["gemini_explore", "gemini_predict", "PREDICTOR"]
            account_id = 2

            def _agg(src: str) -> dict:
                # 当前持仓数
                cur.execute(
                    f"SELECT COUNT(*) AS cnt FROM `{MAIN_DB}`.futures_positions "
                    f"WHERE source=%s AND status='open' AND account_id=%s",
                    (src, account_id),
                )
                open_count = (cur.fetchone() or {}).get("cnt", 0) or 0

                # 30d 聚合
                cur.execute(
                    f"SELECT COUNT(*) AS total, "
                    f"  COALESCE(SUM(realized_pnl), 0) AS total_pnl, "
                    f"  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins, "
                    f"  SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) AS losses, "
                    f"  SUM(CASE WHEN position_side='LONG' THEN 1 ELSE 0 END) AS long_cnt, "
                    f"  SUM(CASE WHEN position_side='SHORT' THEN 1 ELSE 0 END) AS short_cnt, "
                    f"  SUM(CASE WHEN position_side='LONG' THEN COALESCE(realized_pnl, 0) ELSE 0 END) AS long_pnl, "
                    f"  SUM(CASE WHEN position_side='SHORT' THEN COALESCE(realized_pnl, 0) ELSE 0 END) AS short_pnl "
                    f"FROM `{MAIN_DB}`.futures_positions "
                    f"WHERE source=%s AND status='closed' AND account_id=%s "
                    f"  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)",
                    (src, account_id),
                )
                r30 = cur.fetchone() or {}

                # 7d
                cur.execute(
                    f"SELECT COUNT(*) AS total, COALESCE(SUM(realized_pnl),0) AS pnl "
                    f"FROM `{MAIN_DB}`.futures_positions "
                    f"WHERE source=%s AND status='closed' AND account_id=%s "
                    f"  AND close_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)",
                    (src, account_id),
                )
                r7 = cur.fetchone() or {}

                # 24h
                cur.execute(
                    f"SELECT COUNT(*) AS total, COALESCE(SUM(realized_pnl),0) AS pnl "
                    f"FROM `{MAIN_DB}`.futures_positions "
                    f"WHERE source=%s AND status='closed' AND account_id=%s "
                    f"  AND close_time >= DATE_SUB(NOW(), INTERVAL 1 DAY)",
                    (src, account_id),
                )
                r24 = cur.fetchone() or {}

                total30 = int(r30.get("total", 0) or 0)
                wins30 = int(r30.get("wins", 0) or 0)
                losses30 = int(r30.get("losses", 0) or 0)

                return {
                    "open_count": open_count,
                    "closed_24h": int(r24.get("total", 0) or 0),
                    "closed_7d": int(r7.get("total", 0) or 0),
                    "closed_30d": total30,
                    "pnl_24h": float(r24.get("pnl", 0) or 0),
                    "pnl_7d": float(r7.get("pnl", 0) or 0),
                    "pnl_30d": float(r30.get("total_pnl", 0) or 0),
                    "total_pnl": 0,
                    "wins_30d": wins30,
                    "losses_30d": losses30,
                    "win_rate_30d": round(wins30 / total30 * 100, 2) if total30 > 0 else 0,
                    "long_count": int(r30.get("long_cnt", 0) or 0),
                    "short_count": int(r30.get("short_cnt", 0) or 0),
                    "long_pnl": float(r30.get("long_pnl", 0) or 0),
                    "short_pnl": float(r30.get("short_pnl", 0) or 0),
                }

            for src in sources:
                d = _agg(src)
                cur.execute(
                    "INSERT INTO data_cache.position_stats_snapshot "
                    "(source, account_id, open_count, closed_24h, closed_7d, closed_30d, "
                    " pnl_24h, pnl_7d, pnl_30d, total_pnl, "
                    " wins_30d, losses_30d, win_rate_30d, "
                    " long_count, short_count, long_pnl, short_pnl) "
                    "VALUES (%s, %s, %s, %s, %s, %s, "
                    " %s, %s, %s, %s, "
                    " %s, %s, %s, %s, %s, %s, %s)",
                    (
                        src, account_id,
                        d["open_count"], d["closed_24h"], d["closed_7d"], d["closed_30d"],
                        d["pnl_24h"], d["pnl_7d"], d["pnl_30d"], 0,
                        d["wins_30d"], d["losses_30d"], d["win_rate_30d"],
                        d["long_count"], d["short_count"], d["long_pnl"], d["short_pnl"],
                    ),
                )

            # "all" — 综合统计
            cur.execute(
                f"SELECT COUNT(*) AS total, "
                f"  COALESCE(SUM(realized_pnl),0) AS total_pnl, "
                f"  SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) AS wins, "
                f"  COUNT(*) AS cnt "
                f"FROM `{MAIN_DB}`.futures_positions "
                f"WHERE status='closed' AND account_id=%s "
                f"  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)",
                (account_id,),
            )
            r_all = cur.fetchone() or {}
            all_total = int(r_all.get("cnt", 0) or 0)
            all_wins = int(r_all.get("wins", 0) or 0)
            cur.execute(
                "INSERT INTO data_cache.position_stats_snapshot "
                "(source, account_id, closed_30d, pnl_30d, "
                " wins_30d, losses_30d, win_rate_30d) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "all", account_id,
                    all_total,
                    float(r_all.get("total_pnl", 0) or 0),
                    all_wins,
                    all_total - all_wins,
                    round(all_wins / all_total * 100, 2) if all_total > 0 else 0,
                ),
            )

            conn.commit()

        elapsed = int((time.time() - t0) * 1000)
        stat["elapsed_ms"] = elapsed
        logger.info(f"[cache] position_stats_snapshot refreshed in {elapsed}ms")
    except Exception as e:
        stat["status"] = f"error: {e}"
        logger.error(f"[cache] refresh_position_stats failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stat


# ============================================================
# 3.5 系统设置缓存 (写时触发)
# ============================================================
def sync_settings_cache(setting_key: str = None) -> dict:
    """
    将 system_settings 同步到 data_cache.settings_cache。
    如果 setting_key 为 None, 则全量同步。
    """
    t0 = time.time()
    stat = {"status": "ok", "elapsed_ms": 0}
    try:
        _ensure_main_db()
        conn = _get_conn()
        with conn.cursor() as cur:
            if setting_key:
                cur.execute(
                    f"SELECT setting_key, setting_value "
                    f"FROM `{MAIN_DB}`.system_settings WHERE setting_key=%s",
                    (setting_key,),
                )
            else:
                cur.execute(
                    f"SELECT setting_key, setting_value "
                    f"FROM `{MAIN_DB}`.system_settings"
                )

            rows = cur.fetchall()
            for r in rows:
                cur.execute(
                    "REPLACE INTO data_cache.settings_cache (setting_key, setting_value) VALUES (%s, %s)",
                    (r["setting_key"], r.get("setting_value", "")),
                )

        elapsed = int((time.time() - t0) * 1000)
        stat["synced"] = len(rows) if not setting_key else 1
        stat["elapsed_ms"] = elapsed
        logger.debug(f"[cache] settings_cache synced in {elapsed}ms")
    except Exception as e:
        stat["status"] = f"error: {e}"
        logger.error(f"[cache] sync_settings_cache failed: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return stat


# ============================================================
# 便捷读取函数 (供业务代码使用)
# ============================================================
def get_market_snapshot() -> Optional[Dict]:
    """读取市场概览快照."""
    try:
        conn = _get_conn(DATA_CACHE_DB)
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM market_snapshot WHERE id=1")
            return cur.fetchone()
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_market_movers(category: str = None, limit: int = 20) -> List[Dict]:
    """读取市场异动."""
    try:
        conn = _get_conn(DATA_CACHE_DB)
        with conn.cursor() as cur:
            if category:
                cur.execute(
                    "SELECT * FROM market_movers_snapshot "
                    "WHERE category=%s ORDER BY rank_no LIMIT %s",
                    (category, limit),
                )
            else:
                cur.execute(
                    "SELECT * FROM market_movers_snapshot ORDER BY category, rank_no LIMIT %s",
                    (limit * 4,),
                )
            return cur.fetchall()
    except Exception:
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def count_candidate_pool_snapshot() -> int:
    """candidate_pool_snapshot 表行数."""
    try:
        conn = _get_conn(DATA_CACHE_DB)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM candidate_pool_snapshot")
            return int((cur.fetchone() or {}).get("c", 0))
    except Exception as e:
        logger.warning(f"[cache] count_candidate_pool_snapshot failed: {e}")
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_candidate_pool(
    min_volume: float = 1000000,
    min_change: Optional[float] = None,
    max_change: Optional[float] = None,
    limit: int = 100,
) -> List[Dict]:
    """读取候选交易对池. 默认不按涨跌幅上限过滤 (避免 >100%% 异动币被误排除)."""
    try:
        conn = _get_conn(DATA_CACHE_DB)
        with conn.cursor() as cur:
            where = ["quote_volume_24h >= %s"]
            params: list = [min_volume]
            if min_change is not None and max_change is not None:
                where.append(
                    "ABS(COALESCE(change_24h, 0)) BETWEEN %s AND %s"
                )
                params.extend([min_change, max_change])
            params.append(limit)
            sql = (
                "SELECT * FROM candidate_pool_snapshot "
                f"WHERE {' AND '.join(where)} "
                "ORDER BY ABS(COALESCE(change_24h, 0)) DESC LIMIT %s"
            )
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception as e:
        logger.warning(f"[cache] get_candidate_pool failed: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_candidate_pool_for_explore(
    tag: str,
    min_volume: float = 1_000_000,
    limit: int = 200,
    min_rows: int = 20,
    wait_timeout_s: int = 180,
    poll_s: int = 10,
) -> Optional[List[Dict]]:
    """探索/预测用候选池: 优先读缓存; 仅冷启动(表空)时短暂等待首次 refresh."""
    deadline = time.time() + wait_timeout_s
    while time.time() < deadline:
        total = count_candidate_pool_snapshot()
        rows = get_candidate_pool(min_volume=min_volume, limit=limit)
        if len(rows) >= min_rows:
            logger.info(f"{tag} 从 candidate_pool_snapshot 读取 {len(rows)} 个 symbol")
            return rows
        if total >= min_rows and len(rows) < min_rows:
            logger.warning(
                f"{tag} 缓存表 {total} 行但过滤后仅 {len(rows)} 个 "
                f"(volume>={min_volume}), 继续等待 refresh"
            )
        elif total == 0:
            logger.info(
                f"{tag} candidate_pool 表为空 (等待首次 refresh), "
                f"{poll_s}s 后重试..."
            )
        else:
            logger.warning(
                f"{tag} candidate_pool 仅 {total} 行 (<{min_rows}), "
                f"{poll_s}s 后重试..."
            )
        time.sleep(poll_s)
    logger.warning(f"{tag} 等待 candidate_pool 超时 ({wait_timeout_s}s)")
    return None


def get_position_stats(source: str, account_id: int = 2) -> Optional[Dict]:
    """读取持仓统计快照."""
    try:
        conn = _get_conn(DATA_CACHE_DB)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM position_stats_snapshot "
                "WHERE source=%s AND account_id=%s",
                (source, account_id),
            )
            return cur.fetchone()
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ============================================================
# 本地设置缓存 (避免频繁查 MySQL)
# ============================================================
_settings_cache_local: Dict[str, Dict] = {}
_SETTINGS_CACHE_TTL = 60  # 60 秒

def get_setting(key: str, default: str = "") -> str:
    """
    带本地内存缓存的 setting 读取。
    优先读 data_cache.settings_cache, 兜底读 system_settings。
    """
    now = time.time()
    cached = _settings_cache_local.get(key)
    if cached and now - cached["time"] < _SETTINGS_CACHE_TTL:
        return cached["value"]

    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            # 先读缓存表
            try:
                cur.execute(
                    "SELECT setting_value FROM data_cache.settings_cache WHERE setting_key=%s",
                    (key,),
                )
                row = cur.fetchone()
                if row:
                    value = str(row["setting_value"])
                    _settings_cache_local[key] = {"value": value, "time": now}
                    return value
            except Exception:
                pass

            # 兜底读主库
            _ensure_main_db()
            cur.execute(
                f"SELECT setting_value FROM `{MAIN_DB}`.system_settings WHERE setting_key=%s LIMIT 1",
                (key,),
            )
            row = cur.fetchone()
            value = str(row["setting_value"]) if row else default
            _settings_cache_local[key] = {"value": value, "time": now}
            return value
    except Exception:
        return _settings_cache_local.get(key, {}).get("value", default)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def invalidate_setting_cache(key: str = None):
    """清除本地缓存."""
    if key:
        _settings_cache_local.pop(key, None)
    else:
        _settings_cache_local.clear()
