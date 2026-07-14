"""
Gemini 探索 worker (v3 — 2026-05-21 长持仓版)

每 2h 调用 Google Gemini 检测加密货币短时方向异动, 根据 verdict 挂模拟限价单。
持仓 2 小时, SL/TP 读 system_settings; 满 15min 后 Gemini 持仓顾问每 15min 问询是否持有。

仓位参数:
  - account_id = 2 (U本位模拟盘)
  - margin    = 500U
  - leverage  = 5x
  - hold     = 2 小时
  - SL       = 2%
  - TP       = 3%

闸门:
  - system_settings.gemini_explore_enabled (默认 0, 关时早返回)
  - Big4: 仅宏观参考；非极端行情须多空独立评估 (ai_big4_prompt / big4_trading_hint)
  - 同 symbol+side 已有 OPEN gemini_explore 仓位 -> 跳过
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.ai_big4_prompt import (
    big4_conflict_risk_note,
    enrich_global_context,
)
from app.utils.futures_symbol import (
    futures_symbol_clean,
    futures_symbol_rating_canonical,
    resolve_futures_universe_item,
)
from app.services.ai_explore_prompt import (
    get_ai_position_hold_hours,
    get_ai_position_sl_pct,
    get_ai_position_tp_pct,
    EXPLORE_CONFIDENCE_THRESHOLD,
    build_explore_prompt,
    explore_catalyst_technical_ok,
    parse_explore_llm_json,
)
from app.services.ai_predict_schedule import (
    GEMINI_EXPLORE_NEXT_DUE_KEY,
    explore_claim_next_slot,
    explore_round_is_due,
)
from app.services.gemini_swan_worker import (
    GEMINI_MODEL,
    GEMINI_API_KEY,
    GEMINI_TIMEOUT_S,
    TOP_MOVER,
    TOP_FUNDING,
    MIN_QUOTE_VOLUME,
    _is_excluded,
    _merge_universe,
    _read_setting,
)
from app.utils.config_loader import get_db_config
from app.utils.position_time import utc_now_naive

# ── data_cache 层: 尝试从缓存快速读取, 失败时回退到主库 ──
_DATA_CACHE_AVAILABLE = False
try:
    from app.services.data_cache_service import (
        get_candidate_pool,
        get_market_snapshot,
        get_market_movers,
        get_position_stats,
        get_setting,
        load_candidate_pool_for_explore,
    )
    _DATA_CACHE_AVAILABLE = True
except ImportError:
    pass


def _try_candidate_pool(min_volume: float = 1000000, limit: int = 100) -> Optional[List[Dict]]:
    """尝试从 data_cache.candidate_pool_snapshot 读取候选池, 失败返回 None."""
    if not _DATA_CACHE_AVAILABLE:
        return None
    try:
        return load_candidate_pool_for_explore(
            tag="[Gemini探索]",
            min_volume=min_volume,
            limit=limit,
            min_rows=20,
        )
    except Exception as e:
        logger.warning(f"[Gemini探索] 读取 candidate_pool 失败: {e}")
        return None


def _try_market_snapshot() -> Optional[Dict]:
    """尝试读取 market_snapshot 缓存."""
    if not _DATA_CACHE_AVAILABLE:
        return None
    try:
        return get_market_snapshot()
    except Exception:
        return None


def _try_position_stats(source: str, account_id: int = 2) -> Optional[Dict]:
    """尝试读取 position_stats 缓存."""
    if not _DATA_CACHE_AVAILABLE:
        return None
    try:
        return get_position_stats(source, account_id)
    except Exception:
        return None


# ============================================================
# v3 长持仓常量
# ============================================================
EXPLORE_MARGIN_USD = 500.0
EXPLORE_LEVERAGE = 5
EXPLORE_ACCOUNT_ID = 2
EXPLORE_SOURCE = 'gemini_explore'

# ============================================================
# 数据新鲜度门槛
EXPLORE_PRICE_FRESH_MIN = 20
EXPLORE_FUNDING_FRESH_MIN = 30

# 中等波动币参数
NORMAL_MOVER = 8               # 中等涨跌幅各取 top N
NORMAL_MOVER_MIN_VOLUME = 5_000_000   # 500 万 USDT 成交额下限 (比极端池宽松)
NORMAL_CHG_MIN = 3.0           # 最低 |change_24h| ≥ 3%
NORMAL_CHG_MAX = 15.0          # 最高 |change_24h| ≤ 15% (排除极端)
EXCLUDED_EXTREME_SYMBOLS: set = set()  # 运行时填充: 已在极端池的 symbol


# ============================================================
# DB 连接
# ============================================================
def _get_local_db_config() -> dict:
    return get_db_config()


def _connect():
    cfg = _get_local_db_config()
    return pymysql.connect(
        **cfg,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ============================================================
# 历史表现统计
# ============================================================
def _get_historical_stats(conn) -> dict:
    """查询 gemini_explore 的历史表现用于 prompt 尾部反馈.

    优先从 data_cache.position_stats_snapshot 读取.
    """
    stats: dict = {
        'total_trades': 0, 'win_trades': 0, 'win_rate': None,
        'total_pnl': 0, 'avg_pnl': None,
        'long_win_rate': None, 'short_win_rate': None,
        'long_count': 0, 'short_count': 0,
    }

    # 从缓存读取
    cached = _try_position_stats(EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID)
    if cached:
        stats['total_trades'] = int(cached.get('closed_30d', 0) or 0)
        stats['win_trades'] = int(cached.get('wins_30d', 0) or 0)
        stats['total_pnl'] = float(cached.get('pnl_30d', 0) or 0)
        stats['win_rate'] = float(cached.get('win_rate_30d', 0) or 0)
        stats['long_count'] = int(cached.get('long_count', 0) or 0)
        stats['short_count'] = int(cached.get('short_count', 0) or 0)
        if stats['total_trades'] > 0:
            stats['avg_pnl'] = round(stats['total_pnl'] / stats['total_trades'], 2)
        # long win rate / short win rate 从缓存不能精确得到, 用总体近似
        if stats['long_count'] >= 3:
            stats['long_win_rate'] = stats['win_rate']
        if stats['short_count'] >= 3:
            stats['short_win_rate'] = stats['win_rate']
        return stats

    try:
        with conn.cursor() as cur:
            # 总交易数
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM futures_positions "
                "WHERE source=%s AND account_id=%s AND status='closed'",
                (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID),
            )
            row = cur.fetchone()
            total = int((row or {}).get('cnt', 0) or 0)
            stats['total_trades'] = total

            if total >= 3:
                # 盈利交易数
                cur.execute(
                    "SELECT COUNT(*) AS cnt, "
                    "  COALESCE(SUM(realized_pnl), 0) AS total_pnl "
                    "FROM futures_positions "
                    "WHERE source=%s AND account_id=%s AND status='closed'",
                    (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID),
                )
                row = cur.fetchone()
                total_pnl = float((row or {}).get('total_pnl', 0) or 0)
                stats['total_pnl'] = round(total_pnl, 2)
                stats['avg_pnl'] = round(total_pnl / total, 2) if total > 0 else 0

                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM futures_positions "
                    "WHERE source=%s AND account_id=%s AND status='closed' "
                    "AND realized_pnl > 0",
                    (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID),
                )
                row = cur.fetchone()
                wins = int((row or {}).get('cnt', 0) or 0)
                stats['win_trades'] = wins
                stats['win_rate'] = round(wins / total * 100, 1)

                # 分方向
                for side, col in [('LONG', 'long'), ('SHORT', 'short')]:
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM futures_positions "
                        "WHERE source=%s AND account_id=%s AND status='closed' "
                        "AND position_side=%s",
                        (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID, side),
                    )
                    cnt = int((cur.fetchone() or {}).get('cnt', 0) or 0)
                    stats[f'{col}_count'] = cnt
                    if cnt >= 3:
                        cur.execute(
                            "SELECT COUNT(*) AS cnt FROM futures_positions "
                            "WHERE source=%s AND account_id=%s AND status='closed' "
                            "AND position_side=%s AND realized_pnl > 0",
                            (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID, side),
                        )
                        w = int((cur.fetchone() or {}).get('cnt', 0) or 0)
                        stats[f'{col}_win_rate'] = round(w / cnt * 100, 1)
    except Exception as e:
        logger.debug(f"[Gemini探索] 查历史统计失败: {e}")
    return stats


# ============================================================
# 候选池采集 — 中等波动币
# ============================================================


def _fetch_movers_24h(cur, top_n: int):
    """24h 涨/跌 各 top_n. 直接从 kline_data 实时计算."""
    sql = """
        SELECT k.symbol,
               k.close_price                        AS current_price,
               (k.close_price - p24.p24_close) / p24.p24_close * 100
                                                     AS change_24h,
               COALESCE(vol.qvol, 0)                 AS quote_volume_24h
        FROM kline_data k
        -- 最新 5m K 线 (当前价格)
        INNER JOIN (
            SELECT symbol, MAX(open_time) AS max_t
            FROM kline_data
            WHERE timeframe='5m' AND exchange='binance_futures'
              AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 10 MINUTE) * 1000)
            GROUP BY symbol
        ) latest ON k.symbol = latest.symbol
                AND k.open_time = latest.max_t
                AND k.timeframe = '5m' AND k.exchange = 'binance_futures'
        -- 24h 前 1h K 线收盘价
        INNER JOIN (
            SELECT k2.symbol, k2.close_price AS p24_close
            FROM kline_data k2
            INNER JOIN (
                SELECT symbol, MAX(open_time) AS max_t
                FROM kline_data
                WHERE timeframe='1h' AND exchange='binance_futures'
                  AND open_time <= (UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR) * 1000)
                  AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 30 HOUR) * 1000)
                GROUP BY symbol
            ) p24t ON k2.symbol = p24t.symbol
                   AND k2.open_time = p24t.max_t
                   AND k2.timeframe = '1h' AND k2.exchange = 'binance_futures'
        ) p24 ON k.symbol = p24.symbol
        -- 24h 成交额
        INNER JOIN (
            SELECT symbol, SUM(quote_volume) AS qvol
            FROM kline_data
            WHERE timeframe='5m' AND exchange='binance_futures'
              AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR) * 1000)
            GROUP BY symbol
        ) vol ON k.symbol = vol.symbol
        WHERE p24.p24_close > 0
          AND vol.qvol >= %s
          AND k.symbol LIKE '%%/USDT'
        ORDER BY change_24h {order}
        LIMIT %s
    """
    cur.execute(sql.format(order="DESC"), (MIN_QUOTE_VOLUME, top_n * 3))
    gainers = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    cur.execute(sql.format(order="ASC"), (MIN_QUOTE_VOLUME, top_n * 3))
    losers = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    return gainers, losers


def _fetch_normal_movers(cur, top_n: int) -> tuple:
    """中等波动币: 3% <= |change_24h| <= 15%, 成交额 >= NORMAL_MOVER_MIN_VOLUME.

    直接从 kline_data 实时计算. 排除已在极端池的 symbol.
    """
    sql = """
        SELECT k.symbol,
               k.close_price                        AS current_price,
               (k.close_price - p24.p24_close) / p24.p24_close * 100
                                                     AS change_24h,
               COALESCE(vol.qvol, 0)                 AS quote_volume_24h
        FROM kline_data k
        INNER JOIN (
            SELECT symbol, MAX(open_time) AS max_t
            FROM kline_data
            WHERE timeframe='5m' AND exchange='binance_futures'
              AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 10 MINUTE) * 1000)
            GROUP BY symbol
        ) latest ON k.symbol = latest.symbol
                AND k.open_time = latest.max_t
                AND k.timeframe = '5m' AND k.exchange = 'binance_futures'
        INNER JOIN (
            SELECT k2.symbol, k2.close_price AS p24_close
            FROM kline_data k2
            INNER JOIN (
                SELECT symbol, MAX(open_time) AS max_t
                FROM kline_data
                WHERE timeframe='1h' AND exchange='binance_futures'
                  AND open_time <= (UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR) * 1000)
                  AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 30 HOUR) * 1000)
                GROUP BY symbol
            ) p24t ON k2.symbol = p24t.symbol
                   AND k2.open_time = p24t.max_t
                   AND k2.timeframe = '1h' AND k2.exchange = 'binance_futures'
        ) p24 ON k.symbol = p24.symbol
        INNER JOIN (
            SELECT symbol, SUM(quote_volume) AS qvol
            FROM kline_data
            WHERE timeframe='5m' AND exchange='binance_futures'
              AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR) * 1000)
            GROUP BY symbol
        ) vol ON k.symbol = vol.symbol
        WHERE p24.p24_close > 0
          AND vol.qvol >= %s
          AND ABS((k.close_price - p24.p24_close) / p24.p24_close * 100) BETWEEN %s AND %s
          AND k.symbol LIKE '%%/USDT'
        ORDER BY vol.qvol DESC
        LIMIT %s
    """
    cur.execute(sql, (NORMAL_MOVER_MIN_VOLUME, NORMAL_CHG_MIN, NORMAL_CHG_MAX, top_n * 3))
    rows = [r for r in cur.fetchall()
            if not _is_excluded(r["symbol"])
            and r["symbol"] not in EXCLUDED_EXTREME_SYMBOLS]
    normal_gainers = [r for r in rows if float(r['change_24h']) > 0][:top_n]
    normal_losers = [r for r in rows if float(r['change_24h']) < 0][:top_n]
    return normal_gainers, normal_losers


def _fetch_extreme_funding(cur, top_n: int):
    """资金费率 极正/极负 各 top_n."""
    base_sql = """
        SELECT t.symbol AS symbol,
               t.funding_rate AS current_rate,
               NULL AS rate_avg_7d,
               t.timestamp AS updated_at
        FROM funding_rate_data t
        INNER JOIN (
            SELECT symbol, MAX(funding_time) AS max_ft
            FROM funding_rate_data
            WHERE timestamp >= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            GROUP BY symbol
        ) latest ON t.symbol = latest.symbol AND t.funding_time = latest.max_ft
        ORDER BY t.funding_rate {order}
        LIMIT %s
    """
    cur.execute(base_sql.format(order="DESC"),
                (EXPLORE_FUNDING_FRESH_MIN, top_n * 3))
    pos = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    cur.execute(base_sql.format(order="ASC"),
                (EXPLORE_FUNDING_FRESH_MIN, top_n * 3))
    neg = [r for r in cur.fetchall() if not _is_excluded(r["symbol"])][:top_n]
    return pos, neg


def _build_universe(conn) -> dict:
    """构建 Gemini 探索候选池 (极端 + 中等 + 资金费率极值).

    优先从 data_cache.candidate_pool_snapshot 读取 (零 JOIN),
    失败时回退到 kline_data 多层 JOIN.
    """
    global EXCLUDED_EXTREME_SYMBOLS
    EXCLUDED_EXTREME_SYMBOLS = set()

    # ── 加载禁止交易名单（L3 + 手动锁定）──
    from app.services.trading_gates import load_blacklist_level3_symbols
    _level3_set = load_blacklist_level3_symbols(conn)

    # ── 尝试从缓存取 ──
    cached = _try_candidate_pool(min_volume=1000000, limit=200)
    if cached:
        # 过滤黑名单3级
        before = len(cached)
        cached = [r for r in cached if futures_symbol_clean(r['symbol']) not in _level3_set]
        if len(cached) < before:
            logger.info(f"[Gemini探索] 黑名单3级过滤: {before - len(cached)} 个交易对")

        # 缓存数据太少时回退 fallback（防止进程重启后缓存表刚清空或数据不全）
        if len(cached) < 20:
            logger.warning(f"[Gemini探索] 缓存候选池仅 {len(cached)} 个 (<20)，回退 kline_data JOIN")
        else:
            logger.info("[Gemini探索] 从 data_cache.candidate_pool_snapshot 读取候选池 "
                         f"({len(cached)} 个 symbol)")
            return _build_universe_from_cache(cached)

    # ── 回退: 原逻辑 (慢, 可能数分钟) ──
    logger.warning(
        "[Gemini探索] 无法从 candidate_pool_snapshot 读取候选池, "
        "回退 kline_data 多层 JOIN (较慢, 请耐心等待)"
    )
    return _build_universe_fallback(conn, _level3_set)


def _build_universe_from_cache(cached_rows: List[Dict]) -> dict:
    """从缓存行构建 universe."""
    universe = {}
    seen = set()
    move_buy = {"gainers": [], "losers": []}
    move_other = {"norm_up": [], "norm_down": []}
    fund_extremes = {"pos": [], "neg": []}

    for row in cached_rows:
        sym = row["symbol"]
        chg = float(row["change_24h"] or 0)
        vol = float(row["quote_volume_24h"] or 0)
        price = float(row["current_price"] or 0)
        fr = float(row["funding_rate"] or 0)

        # 极端 mover
        if vol >= MIN_QUOTE_VOLUME and abs(chg) >= 8:
            EXCLUDED_EXTREME_SYMBOLS.add(sym)

        triggers = []
        if vol >= MIN_QUOTE_VOLUME:
            triggers.append(f"24h涨跌{chg:+.2f}%")
        if abs(fr) > 0.001:
            triggers.append(f"资金费率{fr:+.6f}")

        if sym not in seen and not _is_excluded(sym):
            seen.add(sym)
            sym_data = {
                "symbol": sym,
                "current_price": price,
                "change_24h": chg,
                "quote_volume_24h": vol,
                "current_rate": fr,
                "triggers": triggers,
                # 从缓存读预先计算好的 K 线叙事
                "kline_narrative": {},
                "tech": {},
            }

            # 如果有预计算的叙事, 直接使用
            if row.get("narrative_1h"):
                sym_data["kline_narrative"]["1h"] = row["narrative_1h"]
            if row.get("narrative_15m"):
                sym_data["kline_narrative"]["15m"] = row["narrative_15m"]
            if row.get("narrative_1d"):
                sym_data["kline_narrative"]["1d"] = row["narrative_1d"]

            if row.get("rsi_14") is not None:
                sym_data["tech"]["rsi_14_1h"] = float(row["rsi_14"])
            if row.get("ema_9") is not None:
                sym_data["tech"]["ema9_15m"] = float(row["ema_9"])
            if row.get("above_7d_low_pct") is not None:
                sym_data["tech"]["above_7d_low_pct"] = float(row["above_7d_low_pct"])
            if row.get("below_7d_high_pct") is not None:
                sym_data["tech"]["below_7d_high_pct"] = float(row["below_7d_high_pct"])

            universe[sym] = sym_data

    return universe


def _build_universe_fallback(conn, level3_set: set = set()) -> dict:
    """回退: 原 kline_data 多层 JOIN 逻辑, 排除黑名单3级."""
    with conn.cursor() as cur:
        # 1. 极端涨跌
        gainers, losers = _fetch_movers_24h(cur, TOP_MOVER)
        for r in gainers + losers:
            EXCLUDED_EXTREME_SYMBOLS.add(r["symbol"])
        universe = _merge_universe(gainers, losers, {}, {})

        # 2. 中等涨跌
        normal_gainers, normal_losers = _fetch_normal_movers(cur, NORMAL_MOVER)
        normal_merged = _merge_universe(normal_gainers, normal_losers, {}, {})
        for sym, data in normal_merged.items():
            if sym not in universe:
                universe[sym] = data

        # 3. 资金费率极值
        fund_pos, fund_neg = _fetch_extreme_funding(cur, TOP_FUNDING)
        fund_merged = _merge_universe({}, {}, fund_pos, fund_neg)
        for sym, data in fund_merged.items():
            if sym not in universe:
                universe[sym] = data
            else:
                for t in data.get('triggers', []):
                    if t not in universe[sym].get('triggers', []):
                        universe[sym].setdefault('triggers', []).append(t)
                if universe[sym].get('current_rate') is None:
                    universe[sym]['current_rate'] = data.get('current_rate')

    # 排除黑名单3级
    if level3_set:
        before = len(universe)
        for sym in list(universe.keys()):
            _clean = sym.replace('/', '')
            if sym in level3_set or _clean in level3_set:
                del universe[sym]
        if len(universe) < before:
            logger.info(f"[Gemini探索] 黑名单3级过滤(fallback): {before - len(universe)} 个交易对")

    return universe


# ============================================================
# 技术指标 + 多周期 K 线增强 (含时间戳/成交量/形态描述)
# ============================================================
def _ema(values: List[float], period: int) -> Optional[float]:
    if not values or len(values) < period:
        return None
    alpha = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
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
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)


def _fetch_klines(cur, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    cur.execute(
        "SELECT open_time, open_price, high_price, low_price, close_price, volume "
        "FROM kline_data "
        "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
        "ORDER BY open_time DESC LIMIT %s",
        (symbol, timeframe, limit),
    )
    rows = list(reversed(cur.fetchall()))
    return rows


def _format_kline_narrative(k_rows: List[Dict], timeframe_label: str, max_lines: int = 4) -> str:
    """将 K 线数据转为自然语言描述, 便于 LLM 理解.
    
    输出示例:
      [1h · 最近 12h]
      - 17:00: 开 1.234 高 1.245 低 1.228 收 1.240 (+0.48%) 量 2.3M
      - 18:00: 开 1.240 高 1.262 低 1.235 收 1.258 (+1.45%) 量 3.1M ↑
    形态总结: 连续 3 根阳线放量上攻, 突破前高阻力
    """
    if not k_rows:
        return f"[{timeframe_label}] 无数据"
    
    lines = []
    prices = []
    total_vol = 0.0
    up_count = 0
    down_count = 0
    
    for r in k_rows:
        try:
            t = datetime.utcfromtimestamp(float(r['open_time']) / 1000)
            o = float(r['open_price'])
            h = float(r['high_price'])
            l = float(r['low_price'])
            c = float(r['close_price'])
            v = float(r.get('volume') or 0)
            pct = (c - o) / o * 100 if o > 0 else 0
            prices.append(c)
            total_vol += v
            if pct >= 0.1:
                up_count += 1
            elif pct <= -0.1:
                down_count += 1
            
            vol_suffix = ''
            # 简化显示, 只取前 N 根
            if len(lines) < max_lines:
                t_str = t.strftime('%H:%M') if timeframe_label in ('15m', '1h') else t.strftime('%m-%d')
                conv = '↑' if pct >= 0.1 else ('↓' if pct <= -0.1 else '→')
                lines.append(
                    f"  {t_str}: O={o:.6g} H={h:.6g} L={l:.6g} C={c:.6g} "
                    f"({pct:+.2f}%){conv} Vol={v:.4g}"
                )
        except Exception:
            continue
    
    # 形态总结
    n = len(prices)
    pattern_desc = "数据不足"
    if n >= 3:
        trend = "上升" if prices[-1] > prices[0] else "下降"
        change_overall = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
        if up_count > down_count * 1.5 and change_overall > 1:
            pattern_desc = f"偏多 ({up_count}阳/{down_count}阴, 整体{change_overall:+.1f}%)"
        elif down_count > up_count * 1.5 and change_overall < -1:
            pattern_desc = f"偏空 ({up_count}阳/{down_count}阴, 整体{change_overall:+.1f}%)"
        elif change_overall > 3:
            pattern_desc = f"强势{trend} ({change_overall:+.1f}%)"
        elif change_overall < -3:
            pattern_desc = f"强势{trend} ({change_overall:+.1f}%)"
        else:
            pattern_desc = f"震荡 ({trend}趋势, {change_overall:+.1f}%)"
    
    # 成交量描述
    if k_rows and len(k_rows) >= 2:
        try:
            mid = len(k_rows) // 2
            vol_first = sum(float(r.get('volume') or 0) for r in k_rows[:mid])
            vol_last = sum(float(r.get('volume') or 0) for r in k_rows[mid:])
            if vol_last > vol_first * 1.5 and vol_first > 0:
                pattern_desc += ", 成交量放大"
            elif vol_last < vol_first * 0.5 and vol_first > 0:
                pattern_desc += ", 成交量萎缩"
        except Exception:
            pass
    
    header = f"[{timeframe_label} · 最近 {len(k_rows)} 根] (总成交量 {total_vol:.4g})"
    body = '\n'.join(lines[:max_lines])
    if len(lines) > max_lines:
        body += f"\n  ... 还有 {len(lines) - max_lines} 根 (略)"
    return f"{header}\n{body}\n形态: {pattern_desc}"


def _enrich_symbol(cur, sym_data: dict) -> None:
    """给单个 symbol 加上 K 线叙事描述 + 技术指标 + 成交量.

    如果 data_cache 中有预计算数据 (已带 narrative), 直接跳过查询.
    """
    symbol = sym_data['symbol']

    # 候选池已写入 1h+1d 叙事则跳过逐币查 K 线
    kn = sym_data.get('kline_narrative') or {}
    if kn.get('1d') and kn.get('1h') and '无数据' not in str(kn.get('1h', '')):
        return

    k_1d = _fetch_klines(cur, symbol, '1d', 7)
    k_1h = _fetch_klines(cur, symbol, '1h', 12)
    k_15m = _fetch_klines(cur, symbol, '15m', 16)

    # 用自然语言描述替代纯数组
    sym_data['kline_narrative'] = {
        '1d': _format_kline_narrative(k_1d, '1d', 4),
        '1h': _format_kline_narrative(k_1h, '1h', 4),
        '15m': _format_kline_narrative(k_15m, '15m', 4),
    }

    # 技术指标
    closes_1h = [float(r['close_price']) for r in k_1h]
    closes_15m = [float(r['close_price']) for r in k_15m]

    rsi_1h = _rsi(closes_1h, 14) if len(closes_1h) >= 15 else None
    ema9_15m = _ema(closes_15m, 9) if len(closes_15m) >= 9 else None

    # 距 7d 高/低距离
    above_7d_low_pct = below_7d_high_pct = None
    if k_1d:
        try:
            highs_7d = [float(r['high_price']) for r in k_1d]
            lows_7d = [float(r['low_price']) for r in k_1d]
            high_7d = max(highs_7d)
            low_7d = min(lows_7d)
            cur_price = sym_data.get('current_price')
            if cur_price and low_7d > 0:
                above_7d_low_pct = round((cur_price - low_7d) / low_7d * 100, 2)
            if cur_price and high_7d > 0:
                below_7d_high_pct = round((cur_price - high_7d) / high_7d * 100, 2)
        except Exception:
            pass

    sym_data['tech'] = {
        'rsi_14_1h': round(rsi_1h, 1) if rsi_1h is not None else None,
        'ema9_15m': round(ema9_15m, 6) if ema9_15m is not None else None,
        'above_7d_low_pct': above_7d_low_pct,
        'below_7d_high_pct': below_7d_high_pct,
    }


def _enrich_universe(conn, universe: dict, *, trust_pool_narratives: bool = False) -> None:
    """加 K 线指标 + 剔除 stale symbol.

    trust_pool_narratives: 候选池 snapshot 已带 narrative 时跳过逐币 MAX(open_time)
    探针（战术多策略复用 universe 时用，可省数百次 SQL）。
    """
    stale_syms = []
    pool_trusted = 0
    with conn.cursor() as cur:
        for sym, sym_data in universe.items():
            try:
                kn = sym_data.get('kline_narrative') or {}
                has_pool_narr = bool(
                    trust_pool_narratives
                    and kn.get('1h')
                    and kn.get('1d')
                    and '无数据' not in str(kn.get('1h', ''))
                    and '无数据' not in str(kn.get('1d', ''))
                )
                if not has_pool_narr:
                    _enrich_symbol(cur, sym_data)
                    kn = sym_data.get('kline_narrative') or {}

                k_1h_narr = kn.get('1h', '')
                k_1d_narr = kn.get('1d', '')
                if not k_1h_narr or not k_1d_narr or '无数据' in str(k_1h_narr):
                    stale_syms.append((sym, 'missing_1h_or_1d_kline'))
                    continue

                if has_pool_narr:
                    pool_trusted += 1
                    continue

                # 1h 新鲜度门槛 4h
                cur.execute(
                    "SELECT MAX(open_time) AS m FROM kline_data "
                    "WHERE symbol=%s AND timeframe='1h' AND exchange='binance_futures'",
                    (sym,)
                )
                row = cur.fetchone()
                if row and row.get('m'):
                    from datetime import datetime as _dt
                    latest_1h = _dt.utcfromtimestamp(int(row['m']) / 1000)
                    age_h = (_dt.now() - latest_1h).total_seconds() / 3600
                    if age_h > 4.0:
                        stale_syms.append((sym, f'1h_kline_stale_{age_h:.1f}h'))
                        continue

                # 1d 新鲜度门槛 2d
                cur.execute(
                    "SELECT MAX(open_time) AS m FROM kline_data "
                    "WHERE symbol=%s AND timeframe='1d' AND exchange='binance_futures'",
                    (sym,)
                )
                row = cur.fetchone()
                if row and row.get('m'):
                    from datetime import datetime as _dt
                    latest_1d = _dt.utcfromtimestamp(int(row['m']) / 1000)
                    age_d = (_dt.now() - latest_1d).total_seconds() / 86400
                    if age_d > 2.0:
                        stale_syms.append((sym, f'1d_kline_stale_{age_d:.1f}d'))
                        continue
            except Exception as e:
                logger.debug(f"[Gemini探索] enrich {sym} 失败: {e}")
                stale_syms.append((sym, f'enrich_exception:{e}'))

    if pool_trusted and trust_pool_narratives:
        logger.info(
            f"[Gemini探索] 候选池 narrative 直用 {pool_trusted} 个 symbol (跳过 freshness 探针)"
        )
    if stale_syms:
        logger.warning(
            f"[Gemini探索] 剔除 {len(stale_syms)} 个 K 线断采的 symbol: "
            f"{[(s[0], s[1]) for s in stale_syms[:10]]}"
            f"{'...' if len(stale_syms) > 10 else ''}"
        )
        for sym, _reason in stale_syms:
            universe.pop(sym, None)


def _build_global_context(conn) -> dict:
    """全局市场环境.

    优先从 data_cache.market_snapshot 读取,
    失败时回退 kline_data 多层 JOIN.
    """
    ctx = {'asof_utc': datetime.now().strftime('%Y-%m-%d %H:%M UTC')}

    # 尝试从缓存读取
    snap = _try_market_snapshot()
    if snap:
        ctx['big4_signal'] = snap.get('big4_signal', 'NEUTRAL')
        for sym, base in [('BTCUSDT', 'btc'), ('ETHUSDT', 'eth'), ('SOLUSDT', 'sol')]:
            if snap.get(f'{sym[:3].lower()}_price'):
                ctx[f'{base}_price'] = float(snap[f'{sym[:3].lower()}_price'])
                ctx[f'{base}_change_24h'] = float(snap[f'{sym[:3].lower()}_change_24h'] or 0)
        # market regime
        btc_chg = abs(ctx.get('btc_change_24h', 0) or 0)
        if btc_chg > 8:
            ctx['market_regime'] = "极端波动 (BTC 24h > 8%) — 注意止损保护"
        elif btc_chg > 4:
            ctx['market_regime'] = "强趋势 (BTC 24h > 4%) — 顺趋势交易"
        elif btc_chg > 1.5:
            ctx['market_regime'] = "温和趋势 (BTC 24h 1.5-4%) — 正常交易环境"
        else:
            ctx['market_regime'] = "低波动盘整 (BTC 24h < 1.5%) — 适合小周期震荡策略"
        return enrich_global_context(ctx)

    # 回退: 原逻辑
    ctx['big4_signal'] = _get_big4_signal(conn)
    market_desc = _describe_market_regime(conn)
    ctx['market_regime'] = market_desc

    with conn.cursor() as cur:
        for sym in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
            base = sym.split('/')[0].lower()
            cur.execute("""
                SELECT k.close_price AS current_price,
                       (k.close_price - p24.p24_close) / p24.p24_close * 100 AS change_24h
                FROM kline_data k
                INNER JOIN (
                    SELECT symbol, MAX(open_time) AS max_t
                    FROM kline_data
                    WHERE timeframe='5m' AND exchange='binance_futures'
                      AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 10 MINUTE) * 1000)
                    GROUP BY symbol
                ) latest ON k.symbol = latest.symbol
                        AND k.open_time = latest.max_t
                        AND k.timeframe = '5m' AND k.exchange = 'binance_futures'
                INNER JOIN (
                    SELECT k2.symbol, k2.close_price AS p24_close
                    FROM kline_data k2
                    INNER JOIN (
                        SELECT symbol, MAX(open_time) AS max_t
                        FROM kline_data
                        WHERE timeframe='1h' AND exchange='binance_futures'
                          AND open_time <= (UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR) * 1000)
                          AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 30 HOUR) * 1000)
                        GROUP BY symbol
                    ) p24t ON k2.symbol = p24t.symbol
                           AND k2.open_time = p24t.max_t
                           AND k2.timeframe = '1h' AND k2.exchange = 'binance_futures'
                ) p24 ON k.symbol = p24.symbol
                WHERE k.symbol = %s AND p24.p24_close > 0
                LIMIT 1
            """, (sym,))
            r = cur.fetchone()
            if r:
                ctx[f'{base}_price'] = float(r['current_price']) if r['current_price'] else None
                ctx[f'{base}_change_24h'] = float(r['change_24h']) if r['change_24h'] is not None else None

    return enrich_global_context(ctx)


def _describe_market_regime(conn) -> str:
    """用简单规则描述大盘状态: 趋势/震荡/极端. 从 kline_data 实时计算."""
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT (k.close_price - p24.p24_close) / p24.p24_close * 100 AS change_24h
                FROM kline_data k
                INNER JOIN (
                    SELECT symbol, MAX(open_time) AS max_t
                    FROM kline_data
                    WHERE timeframe='5m' AND exchange='binance_futures'
                      AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 10 MINUTE) * 1000)
                    GROUP BY symbol
                ) latest ON k.symbol = latest.symbol
                        AND k.open_time = latest.max_t
                        AND k.timeframe = '5m' AND k.exchange = 'binance_futures'
                INNER JOIN (
                    SELECT k2.symbol, k2.close_price AS p24_close
                    FROM kline_data k2
                    INNER JOIN (
                        SELECT symbol, MAX(open_time) AS max_t
                        FROM kline_data
                        WHERE timeframe='1h' AND exchange='binance_futures'
                          AND open_time <= (UNIX_TIMESTAMP(NOW() - INTERVAL 24 HOUR) * 1000)
                          AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 30 HOUR) * 1000)
                        GROUP BY symbol
                    ) p24t ON k2.symbol = p24t.symbol
                           AND k2.open_time = p24t.max_t
                           AND k2.timeframe = '1h' AND k2.exchange = 'binance_futures'
                ) p24 ON k.symbol = p24.symbol
                WHERE k.symbol = 'BTC/USDT' AND p24.p24_close > 0
                LIMIT 1
            """)
            row = cur.fetchone()
            if row and row.get('change_24h') is not None:
                btc_chg = abs(float(row['change_24h']))
                if btc_chg > 8:
                    return "极端波动 (BTC 24h > 8%) — 注意止损保护"
                elif btc_chg > 4:
                    return "强趋势 (BTC 24h > 4%) — 顺趋势交易"
                elif btc_chg > 1.5:
                    return "温和趋势 (BTC 24h 1.5-4%) — 正常交易环境"
                else:
                    return "低波动盘整 (BTC 24h < 1.5%) — 适合小周期震荡策略"
    except Exception:
        pass
    return "未知"


# Prompt: app.services.ai_explore_prompt.EXPLORE_PROMPT_TEMPLATE (Gemini/DeepSeek 共用)

# ============================================================
# Gemini 调用 (含历史表现)
# ============================================================
def _call_gemini_explore(
    universe: dict, global_ctx: dict, historical_stats: dict,
) -> Tuple[Optional[dict], Optional[str]]:
    """调用 Gemini — 按 2 小时持仓趋势判断, 多周期 K 线叙事 + Big4 + 技术指标 + 历史表现."""
    if not GEMINI_API_KEY:
        logger.error("[Gemini探索] GEMINI_API_KEY 未设置")
        return None, "GEMINI_API_KEY 未设置"
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("[Gemini探索] 缺依赖, 请 pip install google-genai")
        return None, "缺 google-genai 依赖"

    prompt, llm_meta = build_explore_prompt(universe, global_ctx, historical_stats)
    logger.info(
        f"[Gemini探索] prompt 长度 = {len(prompt)} chars (~{len(prompt) // 4} tokens), "
        f"送模 {llm_meta['llm_symbol_count']}/{llm_meta['universe_total']} symbols"
    )

    client = genai.Client(api_key=GEMINI_API_KEY)
    cfg = types.GenerateContentConfig(
        response_mime_type="application/json",
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_S * 1000),
    )

    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=prompt, config=cfg,
        )
    except Exception as e:
        logger.error(f"[Gemini探索] Gemini 调用失败: {e}")
        return None, f"API: {e}"

    text = (resp.text or "").strip()
    logger.info(f"[Gemini探索] gemini 用时 {time.time()-t0:.1f}s, output_len={len(text)}")

    parsed, parse_err = parse_explore_llm_json(text, "Gemini探索")
    if parsed is None:
        return None, f"JSON解析失败: {parse_err}"
    parsed['_prompt'] = prompt
    parsed['_raw_response'] = text
    if parse_err:
        parsed['_json_salvaged'] = True
    return parsed, None


# ============================================================
# 闸门检查 (不变)
# ============================================================
def _get_big4_signal(conn) -> str:
    """从 big4_trend_history 或 cache 获取 Big4 信号."""
    if _DATA_CACHE_AVAILABLE:
        try:
            snap = _try_market_snapshot()
            if snap and snap.get("big4_signal"):
                return str(snap["big4_signal"])
        except Exception:
            pass
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT overall_signal FROM big4_trend_history "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return (row or {}).get('overall_signal', 'NEUTRAL') or 'NEUTRAL'
    except Exception as e:
        logger.warning(f"[Gemini探索] 读 Big4 失败 (保守视为 NEUTRAL): {e}")
        return 'NEUTRAL'


def _big4_blocks(big4_signal: str, side: str) -> bool:
    if side == 'LONG':
        return big4_signal in ('BEARISH', 'STRONG_BEARISH')
    if side == 'SHORT':
        return big4_signal in ('BULLISH', 'STRONG_BULLISH')
    return False


def _has_open_position(conn, symbol: str) -> bool:
    from app.services.trading_gates import has_open_futures_position
    return has_open_futures_position(conn, EXPLORE_SOURCE, symbol, EXPLORE_ACCOUNT_ID)


# ============================================================
# 价格获取 (探索/战术开仓)
# ============================================================
_KLINE_5M_MAX_AGE_S = 1800   # scheduler 无 WS 时允许 30min 内的 5m 收盘
_KLINE_1H_MAX_AGE_S = 3900   # 与 BACKFILL 1h 滞后一致 (~65min)
_OPEN_KLINE_5M_MAX_AGE_S = 180   # 开仓 5m 兜底最长 3min，避免旧价开仓
_OPEN_PRICE_MAX_DRIFT = 0.025    # 缓存价 vs live 偏离 >2.5% 则弃用缓存


def _parse_kline_open_dt(open_dt) -> Optional[datetime]:
    if open_dt is None:
        return None
    if isinstance(open_dt, datetime):
        return open_dt
    if isinstance(open_dt, str):
        try:
            return datetime.strptime(open_dt, '%Y-%m-%d %H:%M:%S')
        except Exception:
            return None
    return None


def _price_from_kline_row(row: dict, max_age_s: int, symbol: str, tag: str) -> Optional[float]:
    if not row or row.get('close_price') is None:
        return None
    open_dt = _parse_kline_open_dt(row.get('open_dt'))
    if open_dt is None:
        return None
    age = (datetime.now() - open_dt).total_seconds()
    if age > max_age_s:
        return None
    price = float(row['close_price'])
    if price <= 0:
        return None
    logger.info(f"[{tag}] {symbol} kline 兜底 close={price} age={age:.0f}s")
    return price


def _price_from_candidate_pool(conn, symbol: str) -> Optional[float]:
    try:
        from app.services.data_cache_service import DATA_CACHE_DB
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT current_price FROM {DATA_CACHE_DB}.candidate_pool_snapshot "
                "WHERE symbol=%s AND current_price > 0 LIMIT 1",
                (symbol,),
            )
            row = cur.fetchone()
            if row and row.get('current_price'):
                return float(row['current_price'])
    except Exception as e:
        logger.debug(f"[探索取价] candidate_pool {symbol}: {e}")
    return None


def _price_from_kline_query(
    conn,
    symbol: str,
    timeframe: str,
    max_age_s: int,
    tag: str,
) -> Optional[float]:
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT close_price, FROM_UNIXTIME(open_time/1000) AS open_dt "
                "FROM kline_data "
                "WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures' "
                "ORDER BY open_time DESC LIMIT 1",
                (symbol, timeframe),
            )
            row = cur.fetchone()
            return _price_from_kline_row(row, max_age_s, symbol, tag)
    except Exception as e:
        logger.debug(f"[{tag}] kline {timeframe} 失败 {symbol}: {e}")
    return None


def _get_hub_price(symbol: str, max_age_seconds: int = 90) -> Optional[float]:
    try:
        from app.services.binance_data_hub import get_global_data_hub
        hub = get_global_data_hub()
        if hub is not None:
            p = hub.get_trade_price_sync(
                symbol,
                max_age_seconds=max_age_seconds,
                allow_db_fallback=False,
            )
            if p is not None and p > 0:
                return float(p)
    except Exception:
        pass
    return None


def _get_live_reference_price(conn, symbol: str, tag: str = "探索市价") -> Optional[float]:
    """监控/开仓校验用：Hub → 5m kline，不用探索包缓存."""
    p = _get_hub_price(symbol, 30)
    if p:
        return p
    return _price_from_kline_query(
        conn, symbol, "5m", _OPEN_KLINE_5M_MAX_AGE_S, tag,
    )


def _get_open_price(
    conn,
    symbol: str,
    sym_data: Optional[dict] = None,
) -> Optional[float]:
    """模拟开仓价：live 优先，禁止用过期探索包价导致「开仓即止盈」."""
    tag = "探索开仓价"
    live = _get_live_reference_price(conn, symbol, tag)
    cached = None
    if isinstance(sym_data, dict) and sym_data.get("current_price"):
        cached = float(sym_data["current_price"])
    if not cached:
        cached = _price_from_candidate_pool(conn, symbol)

    if live and live > 0:
        if cached and cached > 0:
            drift = abs(live - cached) / live
            if drift > _OPEN_PRICE_MAX_DRIFT:
                logger.warning(
                    f"[{tag}] {symbol} 缓存价偏离 live={live} cached={cached} "
                    f"drift={drift * 100:.1f}%, 用 live"
                )
        return float(live)

    logger.warning(f"[{tag}] {symbol} 无 30s Hub/3min 5m 新鲜价格，跳过开仓")
    return None


def _would_instant_tp(
    conn,
    symbol: str,
    side: str,
    tp_price: float,
) -> Tuple[bool, Optional[float]]:
    """若当前市价已越过按 entry 计算的 TP，开仓后 monitor 会秒平."""
    ref = _get_live_reference_price(conn, symbol, "开仓TP校验")
    if not ref or ref <= 0 or not tp_price:
        return False, ref
    side_u = (side or "").upper()
    if side_u == "SHORT" and ref <= tp_price:
        return True, ref
    if side_u == "LONG" and ref >= tp_price:
        return True, ref
    return False, ref


def _get_current_price(
    conn,
    symbol: str,
    sym_data: Optional[dict] = None,
) -> Optional[float]:
    """探索/战术模拟开仓取价（同 _get_open_price）."""
    return _get_open_price(conn, symbol, sym_data)


# ============================================================
# SL 缓冲 + 开仓
# ============================================================
# 数据库兼容层：新 category → 旧 ENUM 映射
_CATEGORY_MAP = {
    'bullish': 'red_swan',
    'bearish': 'black_swan',
    'skip': 'skip',
}


def _map_category(cat: str) -> str:
    """将新 prompt 的 category 映射到 DB ENUM 允许的值."""
    normalized = (cat or 'skip').lower().strip()
    if normalized in ('red_swan', 'black_swan', 'skip'):
        return normalized
    return _CATEGORY_MAP.get(normalized, 'skip')


def _sync_to_live(
    paper_position_id: int,
    symbol: str,
    side: str,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    qty: float,
    catalyst: str,
) -> None:
    """将模拟单同步到实盘账号 (调用 Binance API 真实下单).

    受统一闸门 system_settings.live_trading_enabled 控制,
    打开后, 每次 Gemini 探索开模拟单, 会同步在各实盘账号开真实仓位。

    逻辑参照 market_predictor._sync_live():
    1. 检查 live_trading_enabled 开关
    2. 获取所有活跃的 API Key
    3. 对每个账号调用 BinanceFuturesEngine.open_position()
    4. 通过 paper_position_id 关联实盘单与模拟单
    """
    try:
        conn = _connect()
        with conn.cursor() as cur:
            from app.services.trading_gates import check_live_open_allowed
            allowed, reason = check_live_open_allowed(symbol, EXPLORE_SOURCE, cursor=cur)
            if not allowed:
                logger.info(f"[Gemini探索] {symbol} {reason}, 跳过实盘同步")
                conn.close()
                return
        conn.close()
    except Exception as e:
        logger.warning(f"[Gemini探索] 检查实盘闸门失败, 跳过实盘同步: {e}")
        return

    # 2. 获取实盘账号
    try:
        from app.services.api_key_service import APIKeyService
        from app.trading.binance_futures_engine import BinanceFuturesEngine
        from decimal import Decimal

        db_config = _get_local_db_config()
        svc = APIKeyService(db_config)
        active_keys = svc.get_all_active_api_keys('binance')
    except Exception as e:
        logger.error(f"[Gemini探索] 获取实盘账号失败: {e}")
        return

    if not active_keys:
        logger.info("[Gemini探索] 无活跃 API Key, 跳过实盘同步")
        return

    # 3. 对每个账号下单
    db_config = _get_local_db_config()
    try:
        conn = _connect()
        with conn.cursor() as cur:
            from app.services.trading_gates import get_live_margin_ratio
            margin_ratio = get_live_margin_ratio(symbol, cursor=cur)
        conn.close()
    except Exception:
        margin_ratio = 1.0

    if margin_ratio <= 0:
        logger.info(f"[Gemini探索] {symbol} 保证金比例={margin_ratio}, 跳过实盘同步")
        return

    for ak in active_keys:
        try:
            base_margin = float(ak.get('max_position_value') or EXPLORE_MARGIN_USD)
            act_margin = base_margin * margin_ratio
            act_lev = int(ak.get('max_leverage') or EXPLORE_LEVERAGE)
            # 按账号实际保证金计算数量
            if act_margin < 5:
                logger.info(f"[Gemini探索] {symbol} 账号{ak.get('account_name','')} margin={act_margin:.1f}U < 5, 跳过")
                continue
            notional = act_margin * act_lev
            live_qty = Decimal(str(round(notional / entry_price, 6)))
            if live_qty <= 0:
                continue

            engine = BinanceFuturesEngine(
                db_config,
                api_key=ak['api_key'],
                api_secret=ak['api_secret'],
            )
            result = engine.open_position(
                account_id=ak['id'],
                symbol=symbol,
                position_side=side,
                quantity=live_qty,
                leverage=act_lev,
                stop_loss_price=Decimal(str(sl_price)),
                take_profit_price=Decimal(str(tp_price)),
                stop_loss_pct=Decimal(str(get_ai_position_sl_pct())),
                take_profit_pct=Decimal(str(get_ai_position_tp_pct())),
                source='gemini_explore',
                paper_position_id=paper_position_id,
            )
            if result.get('success'):
                logger.info(
                    f"[Gemini探索] 实盘下单成功 {ak['account_name']} {symbol} {side} "
                    f"margin={act_margin}U lev={act_lev}x "
                    f"paper_id={paper_position_id}"
                )
            else:
                logger.error(
                    f"[Gemini探索] 实盘下单失败 {ak['account_name']} {symbol}: "
                    f"{result.get('error', '')}"
                )
        except Exception as e:
            logger.error(
                f"[Gemini探索] 实盘下单异常 {ak.get('account_name','')} {symbol}: {e}"
            )


def _open_simulated_position(
    conn,
    symbol: str,
    side: str,
    price: float,
    catalyst: str,
) -> Optional[int]:
    """创建模拟盘限价开仓单, 返回 futures_orders.id 或 None."""
    symbol = futures_symbol_rating_canonical(symbol)
    from app.services.trading_gates import is_symbol_blocked_level3
    if is_symbol_blocked_level3(symbol):
        logger.warning(f"[Gemini探索] {symbol} 黑名单3级, 禁止开仓模拟单")
        return None

    from app.services.paper_open_gate import gate_simulated_open
    allowed, _gate_reason = gate_simulated_open(
        symbol, side, price, EXPLORE_SOURCE, catalyst,
        leverage=EXPLORE_LEVERAGE,
        sl_pct=get_ai_position_sl_pct(), tp_pct=get_ai_position_tp_pct(),
        hold_hours=get_ai_position_hold_hours(), account_id=EXPLORE_ACCOUNT_ID, conn=conn,
    )
    if not allowed:
        return None

    entry_reason = (catalyst or 'gemini_explore')[:180]
    entry_reason += f" | SL={get_ai_position_sl_pct()}% TP={get_ai_position_tp_pct()}% lev={EXPLORE_LEVERAGE}x hold={get_ai_position_hold_hours()}h"

    from app.services.paper_limit_entry import create_paper_limit_order
    from app.services.trading_gates import get_paper_margin_usd
    paper_margin = get_paper_margin_usd(symbol, conn)
    return create_paper_limit_order(
        conn,
        symbol=symbol,
        side=side,
        ref_price=price,
        source=EXPLORE_SOURCE,
        leverage=EXPLORE_LEVERAGE,
        margin=paper_margin,
        stop_loss_pct=get_ai_position_sl_pct(),
        take_profit_pct=get_ai_position_tp_pct(),
        entry_signal_type='gemini_explore',
        entry_reason=entry_reason,
        max_hold_minutes=get_ai_position_hold_hours() * 60,
        planned_close_time=utc_now_naive() + timedelta(hours=get_ai_position_hold_hours()),
        account_id=EXPLORE_ACCOUNT_ID,
    )


# ============================================================
# 持久化
# ============================================================
def _insert_run(
    conn,
    asof_utc: datetime,
    universe_size: int,
    summary_zh: str,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
    triggered_by: str,
    prompt_text: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> int:
    from app.utils.explore_sql import prompt_flags

    hp, hr = prompt_flags(prompt_text, raw_response)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gemini_explore_runs
              (asof_utc, model, universe_size, summary_zh,
               trades_opened, elapsed_s, status, error_msg, triggered_by,
               prompt_text, raw_response, has_prompt, has_raw)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (asof_utc, GEMINI_MODEL, universe_size, summary_zh,
             elapsed_s, status, error_msg, triggered_by,
             prompt_text, raw_response, hp, hr),
        )
        return cur.lastrowid


def _update_run_trades_opened(conn, run_id: int, trades_opened: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gemini_explore_runs SET trades_opened=%s WHERE id=%s",
            (trades_opened, run_id),
        )


def _finish_run(
    conn,
    run_id: Optional[int],
    asof_utc: datetime,
    universe_size: int,
    summary_zh: str,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
    triggered_by: str,
    prompt_text: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> int:
    """更新已登记的 partial(进行中) 记录, 无 run_id 时退化为 INSERT."""
    from app.utils.explore_sql import prompt_flags

    hp, hr = prompt_flags(prompt_text, raw_response)
    if run_id:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE gemini_explore_runs SET
                  universe_size=%s, summary_zh=%s, elapsed_s=%s,
                  status=%s, error_msg=%s, triggered_by=%s,
                  prompt_text=%s, raw_response=%s, has_prompt=%s, has_raw=%s
                WHERE id=%s
                """,
                (
                    universe_size, summary_zh, elapsed_s,
                    status, error_msg, triggered_by,
                    prompt_text, raw_response, hp, hr, run_id,
                ),
            )
        return run_id
    return _insert_run(
        conn, asof_utc, universe_size, summary_zh, elapsed_s,
        status, error_msg, triggered_by, prompt_text, raw_response,
    )


def _insert_verdicts(conn, run_id: int, verdict_rows: List[Tuple]) -> None:
    if not verdict_rows:
        return
    # 安全截断: 防止 category / action_taken / skip_reason 过长导致 Data truncated 错误
    safe_rows = []
    for row in verdict_rows:
        # row = (run_id, symbol, category, confidence, catalyst, data_signal,
        #        risk_note, action_taken, position_id, skip_reason)
        row_list = list(row)
        row_list[2] = (row_list[2] or 'skip')[:20]       # category
        row_list[7] = (row_list[7] or '')[:30]            # action_taken
        if row_list[9] is not None:
            row_list[9] = str(row_list[9])[:255]          # skip_reason
        safe_rows.append(tuple(row_list))
    with conn.cursor() as cur:
        try:
            cur.executemany(
                """
                INSERT INTO gemini_explore_verdicts
                  (run_id, symbol, category, confidence,
                   catalyst, data_signal, risk_note,
                   action_taken, position_id, skip_reason)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                safe_rows,
            )
        except Exception as e:
            logger.error(f"[Gemini探索] 写入 verdicts 失败: {e}")
            # 打印第一条记录的字段长度帮助排查
            if safe_rows:
                sample = safe_rows[0]
                lengths = {
                    'run_id': len(str(sample[0])),
                    'symbol': len(str(sample[1])),
                    'category': len(str(sample[2])),
                    'action_taken': len(str(sample[7])),
                    'skip_reason': len(str(sample[9])) if sample[9] else 0,
                }
                logger.error(f"[Gemini探索] 首条 verdict 各字段长度: {lengths}")
            raise


# ============================================================
# 全局并发锁
# ============================================================
_explore_running_lock = threading.Lock()


# ============================================================
# 主入口
# ============================================================
def run_explore_round(triggered_by: str = 'scheduler') -> Optional[int]:
    """跑一轮 Gemini 探索 (v2 优化版). 成功返回 run_id, 失败/跳过返回 None.

    新增/修改:
      1. SL 3%, TP 5%, 杠杆 5x
      2. 候选池加入中等波动币 (NORMAL_MOVER)
      3. K 线用自然语言描述替代纯数组
      4. 新 prompt: 去天鹅化 + Few-shot + 置信度校准 + 鼓励空 verdicts
      5+6. 传递历史表现数据给 Gemini
      7. SL 缓冲: 硬 SL 3% + 入场保护 30min
      8. 置信度判定沿用 EXPLORE_CONFIDENCE_THRESHOLD=0.65
      9+10. 新增 _get_historical_stats 并注入 prompt
    """
    # 并发锁: 防止 scheduler + manual 同时触发
    if not _explore_running_lock.acquire(blocking=False):
        logger.warning(f"[Gemini探索] 上一轮还未结束, 跳过 (triggered_by={triggered_by})")
        return None

    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. 读 kill switch
    try:
        with _connect() as conn_chk:
            with conn_chk.cursor() as cur:
                enabled_raw = _read_setting(cur, 'gemini_explore_enabled', '0').strip().lower()
    except Exception as e:
        logger.error(f"[Gemini探索] 读 kill switch 失败,保守跳过: {e}")
        _explore_running_lock.release()
        return None

    if enabled_raw not in ('1', 'true', 'yes', 'on'):
        logger.info(f"[Gemini探索] kill switch=0, 跳过 (triggered_by={triggered_by})")
        _explore_running_lock.release()
        return None

    manual = triggered_by == 'manual'
    if not manual:
        try:
            with _connect() as conn_chk:
                due, due_reason = explore_round_is_due(
                    conn_chk,
                    strategy_key='gemini_explore',
                    runs_table='gemini_explore_runs',
                    next_due_key=GEMINI_EXPLORE_NEXT_DUE_KEY,
                    now=asof_utc,
                    manual=False,
                    log_tag='Gemini探索',
                )
                if not due:
                    logger.info(
                        f"[Gemini探索] {due_reason}, 跳过 (triggered_by={triggered_by})"
                    )
                    _explore_running_lock.release()
                    return None
                explore_claim_next_slot(
                    conn_chk,
                    strategy_key='gemini_explore',
                    next_due_key=GEMINI_EXPLORE_NEXT_DUE_KEY,
                    now=asof_utc,
                    log_tag='Gemini探索',
                )
        except Exception as e:
            logger.warning(f"[Gemini探索] 调度检查失败, 保守跳过: {e}")
            _explore_running_lock.release()
            return None

    logger.info(f"[Gemini探索] === 一轮开始 (triggered_by={triggered_by}) ===")

    # 建立 DB 连接
    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[Gemini探索] DB 连接失败: {e}")
        _explore_running_lock.release()
        return None

    run_id = None
    try:
        # status 列为 ENUM(ok/partial/error/skipped), 无 running; partial=本轮进行中
        run_id = _insert_run(
            conn, asof_utc, 0, '', 0.0,
            'partial', None, triggered_by,
        )
        logger.info(f"[Gemini探索] run_id={run_id} 已登记 (status=partial, 进行中)")

        # 2. 共用预计算包 (scheduler 每 15min 刷新)
        from app.services.explore_prepared_bundle import get_explore_prepared_bundle

        allow_rebuild = triggered_by in ("manual", "scheduler_init", "test")
        universe, global_ctx, from_shared = get_explore_prepared_bundle(
            conn, "Gemini探索", allow_rebuild=allow_rebuild,
        )
        universe_size = len(universe)
        logger.info(
            f"[Gemini探索] universe_size={universe_size} "
            f"({'共用包' if from_shared else '现场构建'})"
        )

        if universe_size == 0:
            # 诊断: 检查各数据源为何为空
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(DISTINCT symbol) AS cnt FROM kline_data "
                    "WHERE timeframe='5m' AND exchange='binance_futures' "
                    "AND open_time >= (UNIX_TIMESTAMP(NOW() - INTERVAL 10 MINUTE) * 1000)"
                )
                fresh_rows = cur.fetchone() or {}
                cur.execute(
                    "SELECT COUNT(DISTINCT symbol) AS cnt FROM kline_data "
                    "WHERE timeframe='1h' AND exchange='binance_futures'"
                )
                total_rows = cur.fetchone() or {}
                cur.execute(
                    "SELECT COUNT(*) AS cnt FROM funding_rate_data "
                    "WHERE timestamp >= DATE_SUB(NOW(), INTERVAL 30 MINUTE)"
                )
                fresh_funding = cur.fetchone() or {}
                logger.warning(
                    f"[Gemini探索] 候选池为空诊断: "
                    f"kline_data 5m 最新10分钟有币数={fresh_rows.get('cnt', -1)}, "
                    f"kline_data 1h 总币数={total_rows.get('cnt', -1)}, "
                    f"新鲜资金费率(30min内)={fresh_funding.get('cnt', -1)}"
                )
            elapsed = time.time() - t0
            run_id = _finish_run(
                conn, run_id, asof_utc, 0, '', elapsed,
                'skipped', '候选池为空', triggered_by,
            )
            logger.warning("[Gemini探索] 候选池为空, 本轮结束")
            return run_id

        logger.info(
            f"[Gemini探索] 全局: Big4={global_ctx.get('big4_signal')} "
            f"market={global_ctx.get('market_regime')} "
            f"BTC chg24h={global_ctx.get('btc_change_24h')}%"
        )

        # 3c. 历史表现统计
        historical_stats = _get_historical_stats(conn)
        if historical_stats.get('total_trades', 0) >= 3:
            logger.info(
                f"[Gemini探索] 历史: {historical_stats['total_trades']}笔 "
                f"胜率={historical_stats['win_rate']}% "
                f"总盈亏={historical_stats['total_pnl']}U"
            )
        else:
            logger.info(f"[Gemini探索] 历史: 样本不足 ({historical_stats['total_trades']}笔)")

        # 3d. 调 Gemini
        gemini_response, call_err = _call_gemini_explore(
            universe, global_ctx, historical_stats,
        )
        if gemini_response is None:
            elapsed = time.time() - t0
            err_msg = (call_err or "Gemini 调用失败")[:500]
            run_id = _finish_run(
                conn, run_id, asof_utc, universe_size, '', elapsed,
                'error', err_msg, triggered_by,
            )
            logger.error(f"[Gemini探索] Gemini 调用失败: {err_msg}")
            return run_id

        summary_zh = (gemini_response.get('summary_zh') or '')[:1000]
        verdicts = gemini_response.get('verdicts') or []
        logger.info(
            f"[Gemini探索] gemini 返回 verdicts={len(verdicts)}, "
            f"summary={summary_zh[:80]}"
        )

        # 4. 更新 run 记录
        elapsed = time.time() - t0
        prompt_text = gemini_response.get('_prompt')
        raw_response = gemini_response.get('_raw_response')
        run_id = _finish_run(
            conn, run_id, asof_utc, universe_size, summary_zh, elapsed,
            'ok', None, triggered_by, prompt_text, raw_response,
        )

        # 5. 逐 verdict 决策开仓
        big4 = _get_big4_signal(conn)
        logger.info(f"[Gemini探索] Big4 当前信号={big4}")

        # 5a. 读系统方向开关 (用户可在管理后台手动勾选)
        with conn.cursor() as cur:
            allow_long_raw = _read_setting(cur, 'allow_long', '1').strip().lower()
            allow_short_raw = _read_setting(cur, 'allow_short', '1').strip().lower()
        allow_long = allow_long_raw in ('1', 'true', 'yes', 'on')
        allow_short = allow_short_raw in ('1', 'true', 'yes', 'on')
        logger.info(f"[Gemini探索] 方向开关: allow_long={allow_long} allow_short={allow_short}")

        trades_opened = 0
        verdict_rows: List[Tuple] = []

        for v in verdicts:
            symbol = futures_symbol_rating_canonical(v.get('symbol') or '')
            category = (v.get('category') or 'skip').lower()
            db_category = _map_category(category)  # 映射到 DB ENUM
            try:
                confidence = float(v.get('confidence') or 0)
            except Exception:
                confidence = 0.0
            catalyst = (v.get('catalyst') or '')[:500]
            data_signal = (v.get('data_signal') or '')[:255]
            risk_note = (v.get('risk_note') or '')[:255]

            if not symbol:
                continue

            # 5b. 类别与置信度 (按校准表)
            # category 映射: bullish=LONG, bearish=SHORT
            if category == 'bullish' and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = 'LONG'
            elif category == 'bearish' and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = 'SHORT'
            else:
                verdict_rows.append((
                    run_id, symbol, db_category,
                    confidence, catalyst, data_signal, risk_note,
                    'skipped_confidence', None,
                    f"category={category} confidence={confidence:.2f}",
                ))
                continue

            tech_ok, tech_reason = explore_catalyst_technical_ok(
                catalyst, data_signal, resolve_futures_universe_item(universe, symbol),
                category=category,
            )
            if not tech_ok:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_weak_catalyst', None, tech_reason,
                ))
                logger.info(f"[Gemini探索] {symbol} 跳过主观 catalyst: {tech_reason}")
                continue

            # 5c. 系统方向开关闸门
            if side == 'LONG' and not allow_long:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_direction_lock', None,
                    "系统配置禁止做多",
                ))
                continue
            if side == 'SHORT' and not allow_short:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_direction_lock', None,
                    "系统配置禁止做空",
                ))
                continue

            # 5d. Big4 仅备注冲突 (不拦截开仓)
            if _big4_blocks(big4, side):
                big4_warning = big4_conflict_risk_note(big4, side)
                risk_note = (risk_note + ' | ' + big4_warning) if risk_note else big4_warning

            # 5e. 同 symbol 去重 (不管方向)
            if _has_open_position(conn, symbol):
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_dedup', None,
                    f"{symbol} 已有 OPEN 仓位, 跳过反方向",
                ))
                continue

            # 5g. 黑名单3级检查 (system_settings.blacklist_level3_enabled)
            from app.services.trading_gates import is_symbol_blocked_level3, check_max_positions_allowed
            if is_symbol_blocked_level3(symbol):
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_blacklist', None,
                    "黑名单3级, 永久禁止交易",
                ))
                continue

            mp_ok, mp_reason = check_max_positions_allowed(conn, EXPLORE_ACCOUNT_ID)
            if not mp_ok:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_max_positions', None,
                    mp_reason,
                ))
                continue

            # 5h. 取价 + 防开仓即止盈
            price = _get_open_price(conn, symbol, resolve_futures_universe_item(universe, symbol))
            if price is None or price <= 0:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    "无有效开仓价",
                ))
                continue
            if side == 'LONG':
                tp_check = round(price * (1 + get_ai_position_tp_pct() / 100), 8)
            else:
                tp_check = round(price * (1 - get_ai_position_tp_pct() / 100), 8)
            instant, ref_px = _would_instant_tp(conn, symbol, side, tp_check)
            if instant:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    f"市价{ref_px:.6g}已越过TP{tp_check:.6g}(防开仓即止盈)",
                ))
                continue

            # 5i. 开仓 (SL 3%, 杠杆 5x)
            position_id = _open_simulated_position(conn, symbol, side, price, catalyst)
            if position_id is None:
                verdict_rows.append((
                    run_id, symbol, db_category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    "开仓 INSERT 失败 (见日志)",
                ))
                continue

            trades_opened += 1
            logger.info(
                f"[Gemini探索] 限价挂单已创建 {symbol} {side} order_db_id={position_id} "
                f"(待成交，非即时持仓)"
            )
            verdict_rows.append((
                run_id, symbol, db_category, confidence,
                catalyst, data_signal, risk_note,
                'opened', position_id, None,
            ))

        # 6. 落库
        _insert_verdicts(conn, run_id, verdict_rows)
        _update_run_trades_opened(conn, run_id, trades_opened)

        # 6b. Shadow 规则对比 (不开仓, 与 LLM 原始 verdict 对比)
        try:
            from app.services.ai_shadow_explore import run_shadow_after_teacher_explore
            run_shadow_after_teacher_explore(
                teacher_source="gemini_explore",
                teacher_run_id=run_id,
                universe=universe,
                global_ctx=global_ctx,
                teacher_verdicts=verdicts,
                conn=conn,
            )
        except Exception as _shadow_err:
            logger.warning(f"[Gemini探索] Shadow 对比跳过: {_shadow_err}")

        logger.info(
            f"[Gemini探索] === 一轮结束 run_id={run_id} 开仓={trades_opened} "
            f"跳过={len(verdict_rows) - trades_opened} "
            f"候选池={universe_size} "
            f"耗时={time.time() - t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[Gemini探索] 一轮异常: {e}", exc_info=True)
        try:
            elapsed = time.time() - t0
            _finish_run(
                conn, run_id, asof_utc, 0, '', elapsed,
                'error', str(e)[:480], triggered_by,
            )
        except Exception:
            pass
        return run_id
    finally:
        try:
            conn.close()
        except Exception:
            pass
        try:
            _explore_running_lock.release()
        except Exception:
            pass


if __name__ == '__main__':
    rid = run_explore_round(triggered_by='manual')
    print(f"run_id={rid}")
