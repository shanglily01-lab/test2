"""破位机会扫描 — Top50 标的池 + 多周期趋势 + 15m 支撑/阻力破位.

保留 midline_* source / 表名仅为兼容历史订单、持仓和 API。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.securities_filter import is_security
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical

# 默认硬规则 v2.1（放宽：原 ±8% + 贴 20% 极值导致几乎永不开仓）
# 目标：趋势破位，不再使用旧中线的回踩/反抽入场。
DAILY_TREND_PCT = 3.0
DAILY_RSI_LONG = (30.0, 78.0)
DAILY_RSI_SHORT = (22.0, 70.0)
DAILY_VOL_RATIO_MIN = 0.40
H1_RSI_LONG_MIN = 35.0
H1_RSI_SHORT_MAX = 65.0
# 1h MA 允许小幅回撤（ma24 不低于 ma168 的 1.5%）
H1_MA_PULLBACK_TOL = 0.015
# 30d 区间：下 40% 做多 / 上 40% 做空；或贴近近 10 日高低
RANGE_BOTTOM_PCT = 0.40
RANGE_TOP_PCT = 0.60
NEAR_10D_PCT = 0.05
ATR_SHRINK_RATIO = 1.20
VOL_SHRINK_RATIO = 1.05
MIDLINE_TOP50_LIMIT = 50
MIDLINE_BIG_SYMBOLS = ("BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "XRP/USDT")
PLAIN_USDT_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,24}/USDT$")
BREAKOUT_LOOKBACK_15M = 32
BREAKOUT_RECENT_BARS = 4
BREAKDOWN_SUPPORT_BUFFER = 0.0015
BREAKOUT_RESIST_BUFFER = 0.0015
BREAKOUT_VOL_RATIO_MIN = 1.12
BREAKDOWN_1H_DROP_MIN_PCT = 0.45
BREAKOUT_1H_RISE_MIN_PCT = 0.45
BREAKDOWN_4H_DROP_MIN_PCT = 0.80
BREAKOUT_4H_RISE_MIN_PCT = 0.80
PHASE_RANGE_MIN_PCT = 0.35
PHASE_RANGE_MAX_PCT = 4.80
PHASE_BREAK_MAX_4H_MOVE_PCT = 4.50
PHASE_MAJOR_TREND_SCORE_MIN = 2.20
PHASE_MAJOR_TREND_STRONG_SCORE = 3.20
PHASE_OPPOSITE_SCORE_MAX = 2.50
PHASE_STRONG_BREAK_PCT = 0.35
TREND_WINDOWS = (
    ("cycle", "大周期", 120, "1d"),
    ("m3", "近3个月", 90, "1d"),
    ("m1", "近1个月", 30, "1d"),
    ("d7", "近7天", 7, "1d"),
    ("d1", "最近1天", 96, "15m"),
)
FUTURE_4H_LABEL = "未来4小时"


def _is_plain_usdt_symbol(symbol: str) -> bool:
    """Only Binance-style crypto USDT pairs, e.g. BTC/USDT or 1000PEPE/USDT."""
    return bool(PLAIN_USDT_SYMBOL_RE.fullmatch(futures_symbol_rating_canonical(symbol or "")))


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


def load_config_yaml_symbols() -> List[str]:
    """从 config.yaml 读 U 本位交易对，转成 BTCUSDT 格式."""
    try:
        import yaml
    except ImportError:
        logger.error("[中线扫描] 缺少 PyYAML")
        return []

    config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    if not config_path.exists():
        logger.error(f"[中线扫描] 配置不存在: {config_path}")
        return []

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    raw = config.get("symbols") or []
    out: List[str] = []
    seen = set()
    for s in raw:
        if not isinstance(s, str):
            continue
        s = s.strip()
        if not s.endswith("/USDT"):
            continue
        binance = s.replace("/", "")
        canon = futures_symbol_rating_canonical(binance)
        clean = futures_symbol_clean(canon)
        if not clean or clean in seen or not _is_plain_usdt_symbol(canon) or is_security(canon):
            continue
        seen.add(clean)
        out.append(canon)
    return out


def load_midline_universe(conn) -> List[str]:
    """config.yaml 全集，排除 L3/锁定等禁止交易标的."""
    symbols = load_config_yaml_symbols()
    try:
        from app.services.trading_gates import load_trading_forbidden_symbols
        banned = load_trading_forbidden_symbols(conn) or set()
    except Exception as e:
        logger.warning(f"[中线扫描] 读禁止列表失败: {e}")
        banned = set()

    banned_clean = {futures_symbol_clean(futures_symbol_rating_canonical(b)) for b in banned}
    filtered = []
    for sym in symbols:
        clean = futures_symbol_clean(sym)
        if clean and clean not in banned_clean:
            filtered.append(sym)
    return filtered


# 兼容旧名
def load_l0_l1_symbols(conn):
    """已废弃：返回 (symbols, {})，实际为 config.yaml 池."""
    return load_midline_universe(conn), {}


def _fetch_klines(cur, symbol: str, timeframe: str, limit: int) -> List[Dict]:
    cur.execute(
        """
        SELECT open_time, open_price, high_price, low_price, close_price, volume
        FROM kline_data
        WHERE symbol=%s AND timeframe=%s AND exchange='binance_futures'
        ORDER BY open_time DESC LIMIT %s
        """,
        (symbol, timeframe, limit),
    )
    return list(reversed(cur.fetchall()))


def _bar_floats(rows: List[Dict]) -> Tuple[List[float], List[float], List[float], List[float]]:
    closes, highs, lows, vols = [], [], [], []
    for r in rows:
        closes.append(float(r["close_price"]))
        highs.append(float(r["high_price"]))
        lows.append(float(r["low_price"]))
        vols.append(float(r.get("volume") or 0))
    return closes, highs, lows, vols


def _layer1_daily(
    closes_1d: List[float],
    vols_1d: List[float],
    profile: str,
) -> Tuple[bool, Dict[str, Any]]:
    detail: Dict[str, Any] = {"layer": "daily_30d"}
    if len(closes_1d) < 30:
        detail["reason"] = "insufficient_1d"
        return False, detail

    c0, c1 = closes_1d[-30], closes_1d[-1]
    if c0 <= 0:
        detail["reason"] = "bad_price"
        return False, detail
    change_pct = (c1 - c0) / c0 * 100.0
    detail["change_30d_pct"] = round(change_pct, 2)

    rsi = _rsi(closes_1d, 14)
    detail["rsi_1d"] = round(rsi, 1) if rsi is not None else None

    if len(vols_1d) >= 30:
        vol_recent = sum(vols_1d[-10:]) / 10
        vol_prior = sum(vols_1d[-30:-10]) / 20
        vol_ratio = (vol_recent / vol_prior) if vol_prior > 0 else 0.0
    else:
        vol_ratio = 0.0
    detail["vol_ratio_10_20"] = round(vol_ratio, 3)

    if vol_ratio < DAILY_VOL_RATIO_MIN:
        detail["reason"] = "daily_vol_dry"
        return False, detail

    if profile == "long":
        if change_pct < DAILY_TREND_PCT:
            detail["reason"] = "daily_not_bullish"
            return False, detail
        if rsi is None or not (DAILY_RSI_LONG[0] <= rsi <= DAILY_RSI_LONG[1]):
            detail["reason"] = "daily_rsi_out"
            return False, detail
    else:
        if change_pct > -DAILY_TREND_PCT:
            detail["reason"] = "daily_not_bearish"
            return False, detail
        if rsi is None or not (DAILY_RSI_SHORT[0] <= rsi <= DAILY_RSI_SHORT[1]):
            detail["reason"] = "daily_rsi_out"
            return False, detail

    detail["passed"] = True
    return True, detail


def _layer2_hourly(
    closes_1h: List[float],
    profile: str,
) -> Tuple[bool, Dict[str, Any]]:
    detail: Dict[str, Any] = {"layer": "hourly_1w"}
    if len(closes_1h) < 168:
        detail["reason"] = "insufficient_1h"
        return False, detail

    ma_24 = sum(closes_1h[-24:]) / 24
    ma_168 = sum(closes_1h[-168:]) / 168
    detail["ma24"] = round(ma_24, 8)
    detail["ma168"] = round(ma_168, 8)
    detail["ma_bias_pct"] = round((ma_24 - ma_168) / ma_168 * 100, 3) if ma_168 > 0 else None

    rsi = _rsi(closes_1h, 14)
    detail["rsi_1h"] = round(rsi, 1) if rsi is not None else None

    if profile == "long":
        # 允许小幅回踩：ma24 不低于 ma168*(1-tol)
        if ma_168 <= 0 or ma_24 < ma_168 * (1.0 - H1_MA_PULLBACK_TOL):
            detail["reason"] = "h1_ma_not_bullish"
            return False, detail
        if rsi is None or rsi < H1_RSI_LONG_MIN:
            detail["reason"] = "h1_rsi_low"
            return False, detail
    else:
        if ma_168 <= 0 or ma_24 > ma_168 * (1.0 + H1_MA_PULLBACK_TOL):
            detail["reason"] = "h1_ma_not_bearish"
            return False, detail
        if rsi is None or rsi > H1_RSI_SHORT_MAX:
            detail["reason"] = "h1_rsi_high"
            return False, detail

    detail["passed"] = True
    return True, detail


def _layer3_entry(
    closes_1d: List[float],
    highs_1d: List[float],
    lows_1d: List[float],
    closes_15m: List[float],
    highs_15m: List[float],
    lows_15m: List[float],
    vols_15m: List[float],
    profile: str,
) -> Tuple[bool, Dict[str, Any]]:
    detail: Dict[str, Any] = {"layer": "entry_15m"}
    if len(closes_1d) < 30 or len(closes_15m) < 16:
        detail["reason"] = "insufficient_15m_or_1d"
        return False, detail

    hi_30 = max(highs_1d[-30:])
    lo_30 = min(lows_1d[-30:])
    last = closes_1d[-1]
    span = hi_30 - lo_30
    if span <= 0:
        detail["reason"] = "flat_range"
        return False, detail
    pos = (last - lo_30) / span
    detail["range_pos"] = round(pos, 3)
    detail["range_low"] = lo_30
    detail["range_high"] = hi_30

    low_10d = min(lows_1d[-10:])
    high_10d = max(highs_1d[-10:])
    near_10d_low = low_10d > 0 and last <= low_10d * (1.0 + NEAR_10D_PCT)
    near_10d_high = high_10d > 0 and last >= high_10d * (1.0 - NEAR_10D_PCT)
    detail["near_10d_low"] = near_10d_low
    detail["near_10d_high"] = near_10d_high

    def _ranges(h, l, start, end):
        return [h[i] - l[i] for i in range(start, end)]

    n = len(highs_15m)
    recent_r = _ranges(highs_15m, lows_15m, n - 4, n)
    prior_r = _ranges(highs_15m, lows_15m, n - 16, n - 4)
    avg_recent = sum(recent_r) / 4
    avg_prior = sum(prior_r) / 12 if prior_r else 0
    shrink = (avg_recent / avg_prior) if avg_prior > 0 else 9.0
    detail["range_shrink"] = round(shrink, 3)

    vol_recent = sum(vols_15m[-4:]) / 4
    vol_prior = sum(vols_15m[-16:-4]) / 12
    vol_ratio = (vol_recent / vol_prior) if vol_prior > 0 else 9.0
    detail["vol_shrink"] = round(vol_ratio, 3)

    last3 = closes_15m[-3:]
    if profile == "long":
        # 上涨趋势中的回踩：落在 30d 下 40%，或贴近近 10 日低点
        if not (pos <= RANGE_BOTTOM_PCT or near_10d_low):
            detail["reason"] = "not_near_low"
            return False, detail
        # 企稳：波幅收敛，或近 3 收盘未持续创新低
        stabilized = shrink <= ATR_SHRINK_RATIO or last3[-1] >= min(last3[:-1])
        if not stabilized:
            detail["reason"] = "not_stabilized"
            return False, detail
    else:
        if not (pos >= RANGE_TOP_PCT or near_10d_high):
            detail["reason"] = "not_near_high"
            return False, detail
        # 高位缩量或滞涨：量比收敛，或近 3 收盘未持续创新高
        stalled = vol_ratio <= VOL_SHRINK_RATIO or last3[-1] <= max(last3[:-1])
        if not stalled:
            detail["reason"] = "not_stalled_high"
            return False, detail

    detail["passed"] = True
    return True, detail


def evaluate_symbol(
    cur,
    symbol: str,
    profile: str,
) -> Dict[str, Any]:
    """评估单币；返回含 passed / reason / layers / ref_price 的明细."""
    profile = profile.strip().lower()
    side = "LONG" if profile == "long" else "SHORT"
    out: Dict[str, Any] = {
        "symbol": symbol,
        "side": side,
        "passed": False,
        "reason": None,
        "score": 0.0,
        "ref_price": None,
        "layers": {},
    }

    rows_1d = _fetch_klines(cur, symbol, "1d", 35)
    rows_1h = _fetch_klines(cur, symbol, "1h", 180)
    rows_15m = _fetch_klines(cur, symbol, "15m", 24)

    if len(rows_1d) < 30:
        out["reason"] = "insufficient_1d"
        return out
    if len(rows_1h) < 168:
        out["reason"] = "insufficient_1h"
        return out
    if len(rows_15m) < 16:
        out["reason"] = "insufficient_15m"
        return out

    c1d, h1d, l1d, v1d = _bar_floats(rows_1d)
    c1h, _, _, _ = _bar_floats(rows_1h)
    c15, h15, l15, v15 = _bar_floats(rows_15m)
    out["ref_price"] = c15[-1]

    ok1, d1 = _layer1_daily(c1d, v1d, profile)
    out["layers"]["daily"] = d1
    if not ok1:
        out["reason"] = d1.get("reason") or "layer1_fail"
        return out

    ok2, d2 = _layer2_hourly(c1h, profile)
    out["layers"]["hourly"] = d2
    if not ok2:
        out["reason"] = d2.get("reason") or "layer2_fail"
        return out

    ok3, d3 = _layer3_entry(c1d, h1d, l1d, c15, h15, l15, v15, profile)
    out["layers"]["entry"] = d3
    if not ok3:
        out["reason"] = d3.get("reason") or "layer3_fail"
        return out

    out["passed"] = True
    out["reason"] = None
    out["score"] = 100.0
    out["signal_detail"] = {
        "daily": d1,
        "hourly": d2,
        "entry": d3,
        "change_30d_pct": d1.get("change_30d_pct"),
        "rsi_1d": d1.get("rsi_1d"),
        "rsi_1h": d2.get("rsi_1h"),
        "range_pos": d3.get("range_pos"),
        "vol_shrink": d3.get("vol_shrink"),
        "range_shrink": d3.get("range_shrink"),
    }
    return out


def scan_universe(
    conn,
    profile: str,
    *,
    include_rejects: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    扫描 config.yaml 池。
    默认只返回通过三层的信号；include_rejects=True 时返回全部（机会分析落库用）。
    """
    symbols = load_midline_universe(conn)
    universe_size = len(symbols)
    results: List[Dict[str, Any]] = []
    profile_l = profile.strip().lower()

    with conn.cursor() as cur:
        for symbol in symbols:
            try:
                ev = evaluate_symbol(cur, symbol, profile_l)
                if ev["passed"]:
                    results.append({
                        "symbol": ev["symbol"],
                        "side": ev["side"],
                        "score": float(ev["score"]),
                        "signal_detail": ev.get("signal_detail") or ev.get("layers") or {},
                        "ref_price": ev.get("ref_price"),
                        "passed": True,
                        "reason": None,
                    })
                elif include_rejects:
                    results.append({
                        "symbol": ev["symbol"],
                        "side": ev["side"],
                        "score": 0.0,
                        "signal_detail": {
                            "layers": ev.get("layers") or {},
                            "reason": ev.get("reason"),
                        },
                        "ref_price": ev.get("ref_price"),
                        "passed": False,
                        "reason": ev.get("reason"),
                    })
            except Exception as e:
                logger.debug(f"[中线扫描] {symbol} 跳过: {e}")
                if include_rejects:
                    results.append({
                        "symbol": symbol,
                        "side": "LONG" if profile_l == "long" else "SHORT",
                        "score": 0.0,
                        "signal_detail": {"error": str(e)},
                        "ref_price": None,
                        "passed": False,
                        "reason": "eval_error",
                    })
                continue

    results.sort(key=lambda x: (0 if x.get("passed") else 1, -float(x.get("score") or 0)))
    return results, universe_size


def _ema(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _window_direction(closes: List[float], highs: List[float], lows: List[float]) -> Dict[str, Any]:
    if len(closes) < 2:
        return {"side": "FLAT", "score": 0.0, "reason": "insufficient"}
    first, last = closes[0], closes[-1]
    if first <= 0:
        return {"side": "FLAT", "score": 0.0, "reason": "bad_price"}
    change = (last - first) / first * 100.0
    hi, lo = max(highs), min(lows)
    pos = 0.5 if hi <= lo else (last - lo) / (hi - lo)
    ema20 = _ema(closes, min(20, len(closes)))
    side = "FLAT"
    if change >= 3.0 and pos >= 0.45 and (ema20 is None or last >= ema20):
        side = "LONG"
    elif change <= -3.0 and pos <= 0.55 and (ema20 is None or last <= ema20):
        side = "SHORT"
    elif change >= 1.2 and pos >= 0.55:
        side = "LONG"
    elif change <= -1.2 and pos <= 0.45:
        side = "SHORT"
    score = min(1.0, abs(change) / 12.0 + abs(pos - 0.5))
    return {
        "side": side,
        "score": round(score, 3),
        "change_pct": round(change, 2),
        "range_pos": round(pos, 3),
        "last": round(last, 8),
        "ema20": round(ema20, 8) if ema20 else None,
    }


def _future_4h_direction(
    closes_15m: List[float],
    highs_15m: List[float],
    lows_15m: List[float],
    vols_15m: List[float],
) -> Dict[str, Any]:
    """Estimate next 4h directional bias from the latest 15m structure."""
    if len(closes_15m) < 32:
        return {"label": FUTURE_4H_LABEL, "side": "FLAT", "score": 0.0, "reason": "insufficient_15m"}

    last = closes_15m[-1]
    prev_4h = closes_15m[-17]
    prev_8h = closes_15m[-33] if len(closes_15m) >= 33 else closes_15m[0]
    if prev_4h <= 0 or prev_8h <= 0:
        return {"label": FUTURE_4H_LABEL, "side": "FLAT", "score": 0.0, "reason": "bad_price"}

    change_4h = (last - prev_4h) / prev_4h * 100.0
    change_8h = (last - prev_8h) / prev_8h * 100.0
    ema_fast = _ema(closes_15m[-32:], 8)
    ema_slow = _ema(closes_15m[-32:], 21)
    prev_hi = max(highs_15m[-32:-4])
    prev_lo = min(lows_15m[-32:-4])
    span = max(prev_hi - prev_lo, 0.0)
    range_pos = 0.5 if span <= 0 else (last - prev_lo) / span
    vol_recent = sum(vols_15m[-4:]) / 4 if len(vols_15m) >= 4 else 0.0
    vol_prior = sum(vols_15m[-16:-4]) / 12 if len(vols_15m) >= 16 else vol_recent
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else 1.0

    long_score = 0.0
    short_score = 0.0
    if ema_fast is not None and ema_slow is not None:
        if last >= ema_fast >= ema_slow:
            long_score += 1.2
        if last <= ema_fast <= ema_slow:
            short_score += 1.2
    if change_4h > 0.35:
        long_score += min(1.4, change_4h / 1.5)
    elif change_4h < -0.35:
        short_score += min(1.4, abs(change_4h) / 1.5)
    if change_8h > 0.6:
        long_score += 0.8
    elif change_8h < -0.6:
        short_score += 0.8
    if last > prev_hi * 1.001:
        long_score += 1.0
    if last < prev_lo * 0.999:
        short_score += 1.0
    if range_pos >= 0.68:
        long_score += 0.4
    elif range_pos <= 0.32:
        short_score += 0.4
    if vol_ratio >= 1.15:
        if change_4h > 0:
            long_score += 0.3
        elif change_4h < 0:
            short_score += 0.3

    side = "FLAT"
    diff = long_score - short_score
    if diff >= 0.9:
        side = "LONG"
    elif diff <= -0.9:
        side = "SHORT"
    score = min(1.0, abs(diff) / 3.2)
    return {
        "label": FUTURE_4H_LABEL,
        "side": side,
        "score": round(score, 3),
        "change_4h_pct": round(change_4h, 2),
        "change_8h_pct": round(change_8h, 2),
        "range_pos": round(range_pos, 3),
        "vol_ratio": round(vol_ratio, 3),
        "ema_fast": round(ema_fast, 8) if ema_fast else None,
        "ema_slow": round(ema_slow, 8) if ema_slow else None,
    }


def _fetch_window(cur, symbol: str, key: str) -> Tuple[List[float], List[float], List[float], List[float]]:
    _, _, limit, timeframe = next(w for w in TREND_WINDOWS if w[0] == key)
    rows = _fetch_klines(cur, symbol, timeframe, limit)
    return _bar_floats(rows) if rows else ([], [], [], [])


def load_midline_universe(conn) -> List[str]:
    """Top50 liquid crypto universe. Market-cap rank can replace this query later."""
    symbols: List[str] = []
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT symbol
                FROM price_stats_24h
                WHERE symbol LIKE '%%/USDT'
                  AND quote_volume_24h IS NOT NULL
                  AND quote_volume_24h > 0
                ORDER BY quote_volume_24h DESC
                LIMIT 120
                """
            )
            for row in cur.fetchall() or []:
                raw = row.get("symbol") if isinstance(row, dict) else row[0]
                canon = futures_symbol_rating_canonical(str(raw or ""))
                if canon and _is_plain_usdt_symbol(canon) and not is_security(canon):
                    symbols.append(canon)
    except Exception as e:
        logger.warning(f"[中线扫描] 读取流动性Top50失败，回退config池: {e}")
        symbols = load_config_yaml_symbols()

    try:
        from app.services.trading_gates import load_trading_forbidden_symbols
        banned = load_trading_forbidden_symbols(conn) or set()
    except Exception as e:
        logger.warning(f"[中线扫描] 读取禁止交易名单失败: {e}")
        banned = set()
    banned_clean = {futures_symbol_clean(futures_symbol_rating_canonical(b)) for b in banned}

    filtered: List[str] = []
    seen = set()
    for sym in symbols:
        clean = futures_symbol_clean(sym)
        if (
            not clean
            or clean in seen
            or clean in banned_clean
            or not _is_plain_usdt_symbol(sym)
            or is_security(sym)
        ):
            continue
        seen.add(clean)
        filtered.append(sym)
        if len(filtered) >= MIDLINE_TOP50_LIMIT:
            break
    return filtered


def evaluate_global_trend_dimensions(cur) -> Dict[str, Any]:
    dims = []
    votes = {"LONG": 0.0, "SHORT": 0.0, "FLAT": 0.0}
    for key, label, _, _ in TREND_WINDOWS:
        coins = []
        for sym in MIDLINE_BIG_SYMBOLS:
            c, h, l, _ = _fetch_window(cur, sym, key)
            if not c:
                continue
            d = _window_direction(c, h, l)
            coins.append({"symbol": futures_symbol_rating_canonical(sym), **d})
            votes[d["side"]] += max(0.1, float(d.get("score") or 0))
        long_n = sum(1 for x in coins if x["side"] == "LONG")
        short_n = sum(1 for x in coins if x["side"] == "SHORT")
        side = "SHORT" if short_n >= 3 else "LONG" if long_n >= 3 else "FLAT"
        dims.append({"key": key, "label": label, "side": side, "coins": coins})

    future_coins = []
    for sym in MIDLINE_BIG_SYMBOLS:
        c, h, l, v = _fetch_window(cur, sym, "d1")
        if not c:
            continue
        d = _future_4h_direction(c, h, l, v)
        future_coins.append({"symbol": futures_symbol_rating_canonical(sym), **d})
        votes[d["side"]] += max(0.1, float(d.get("score") or 0))
    future_long_n = sum(1 for x in future_coins if x["side"] == "LONG")
    future_short_n = sum(1 for x in future_coins if x["side"] == "SHORT")
    future_side = "SHORT" if future_short_n >= 3 else "LONG" if future_long_n >= 3 else "FLAT"
    dims.append({"key": "future_4h", "label": FUTURE_4H_LABEL, "side": future_side, "coins": future_coins})

    bias = "SHORT" if votes["SHORT"] > votes["LONG"] * 1.15 else "LONG" if votes["LONG"] > votes["SHORT"] * 1.15 else "FLAT"
    return {"bias": bias, "dimensions": dims, "votes": votes}


def _major_trend_context(
    dims: Dict[str, Any],
    global_bias: str,
    side: str,
) -> Tuple[bool, Dict[str, Any]]:
    opposite = "SHORT" if side == "LONG" else "LONG"
    key_sides = {k: (dims.get(k) or {}).get("side", "FLAT") for k in ("cycle", "m3", "m1", "d7", "d1")}
    weights = {"cycle": 0.8, "m3": 1.4, "m1": 1.4, "d7": 1.1, "d1": 0.9}
    trend_score = sum(weights[k] for k, v in key_sides.items() if v == side)
    opposite_score = sum(weights[k] for k, v in key_sides.items() if v == opposite)
    flat_score = sum(weights[k] for k, v in key_sides.items() if v == "FLAT")
    if global_bias == side:
        trend_score += 0.8
    elif global_bias == opposite:
        opposite_score += 0.8

    core_trend_ok = key_sides["m3"] == side or key_sides["m1"] == side
    short_cycle_pullback = key_sides["d1"] == opposite and key_sides["m1"] == side and key_sides["d7"] != opposite
    strong_context = trend_score >= PHASE_MAJOR_TREND_STRONG_SCORE
    detail = {
        "layer": "major_trend_context",
        "side": side,
        "global_bias": global_bias,
        "trend_score": round(trend_score, 2),
        "opposite_score": round(opposite_score, 2),
        "flat_score": round(flat_score, 2),
        "core_trend_ok": core_trend_ok,
        "short_cycle_pullback": short_cycle_pullback,
        "dimension_sides": key_sides,
    }

    if not core_trend_ok:
        detail["reason"] = "major_core_trend_missing"
        return False, detail
    if trend_score < PHASE_MAJOR_TREND_SCORE_MIN:
        detail["reason"] = "major_trend_not_aligned"
        return False, detail
    if opposite_score > PHASE_OPPOSITE_SCORE_MAX and not strong_context:
        detail["reason"] = "major_trend_too_mixed"
        return False, detail
    if key_sides["cycle"] == opposite and key_sides["m3"] != side:
        detail["reason"] = "cycle_trend_opposes_phase"
        return False, detail
    if key_sides["d1"] == opposite and not short_cycle_pullback and not strong_context:
        detail["reason"] = "latest_day_opposes_without_pullback_context"
        return False, detail
    if global_bias == opposite and not strong_context:
        detail["reason"] = "global_trend_opposes_phase"
        return False, detail

    detail["passed"] = True
    return True, detail


def _entry_15m_opportunity(
    closes_15m: List[float],
    highs_15m: List[float],
    lows_15m: List[float],
    vols_15m: List[float],
    side: str,
) -> Tuple[bool, Dict[str, Any]]:
    detail: Dict[str, Any] = {"layer": "entry_15m_opportunity"}
    if len(closes_15m) < BREAKOUT_LOOKBACK_15M:
        detail["reason"] = "insufficient_15m"
        return False, detail
    last = closes_15m[-1]
    prev_hi = max(highs_15m[-BREAKOUT_LOOKBACK_15M:-BREAKOUT_RECENT_BARS])
    prev_lo = min(lows_15m[-BREAKOUT_LOOKBACK_15M:-BREAKOUT_RECENT_BARS])
    recent_hi = max(highs_15m[-BREAKOUT_RECENT_BARS:])
    recent_lo = min(lows_15m[-BREAKOUT_RECENT_BARS:])
    vol_recent = sum(vols_15m[-BREAKOUT_RECENT_BARS:]) / BREAKOUT_RECENT_BARS
    vol_prior = sum(vols_15m[-16:-4]) / 12 if len(vols_15m) >= 16 else vol_recent
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else 1.0
    prev_1h = closes_15m[-5]
    prev_4h = closes_15m[-17]
    change_1h = (last - prev_1h) / prev_1h * 100.0 if prev_1h > 0 else 0.0
    change_4h = (last - prev_4h) / prev_4h * 100.0 if prev_4h > 0 else 0.0
    break_down_pct = (prev_lo - last) / prev_lo * 100.0 if prev_lo > 0 else 0.0
    break_up_pct = (last - prev_hi) / prev_hi * 100.0 if prev_hi > 0 else 0.0
    phase_range_pct = (prev_hi - prev_lo) / last * 100.0 if last > 0 else 0.0
    phase_range_ok = PHASE_RANGE_MIN_PCT <= phase_range_pct <= PHASE_RANGE_MAX_PCT
    detail.update({
        "prev_hi": round(prev_hi, 8),
        "prev_lo": round(prev_lo, 8),
        "recent_hi": round(recent_hi, 8),
        "recent_lo": round(recent_lo, 8),
        "vol_ratio": round(vol_ratio, 3),
        "change_1h_pct": round(change_1h, 2),
        "change_4h_pct": round(change_4h, 2),
        "phase_range_pct": round(phase_range_pct, 2),
    })
    if side == "SHORT":
        breakdown = last < prev_lo * (1.0 - BREAKDOWN_SUPPORT_BUFFER)
        momentum_ok = change_1h <= -BREAKDOWN_1H_DROP_MIN_PCT or change_4h <= -BREAKDOWN_4H_DROP_MIN_PCT
        volume_ok = vol_ratio >= BREAKOUT_VOL_RATIO_MIN
        strong_break = break_down_pct >= PHASE_STRONG_BREAK_PCT
        not_overextended = abs(change_4h) <= PHASE_BREAK_MAX_4H_MOVE_PCT or break_down_pct >= PHASE_STRONG_BREAK_PCT * 2.0
        fresh_breakdown = (
            breakdown
            and phase_range_ok
            and not_overextended
            and momentum_ok
            and (volume_ok or strong_break)
        )
        detail["setup"] = "fresh_breakdown_short" if fresh_breakdown else "breakdown_short" if breakdown else "none"
        if breakdown:
            detail["break_pct"] = round(break_down_pct, 3)
        detail["momentum_ok"] = momentum_ok
        detail["volume_ok"] = volume_ok
        detail["strong_break"] = strong_break
        detail["not_overextended"] = not_overextended
        detail["fresh_breakout"] = fresh_breakdown
        ok = fresh_breakdown
    else:
        breakout = last > prev_hi * (1.0 + BREAKOUT_RESIST_BUFFER)
        momentum_ok = change_1h >= BREAKOUT_1H_RISE_MIN_PCT or change_4h >= BREAKOUT_4H_RISE_MIN_PCT
        volume_ok = vol_ratio >= BREAKOUT_VOL_RATIO_MIN
        strong_break = break_up_pct >= PHASE_STRONG_BREAK_PCT
        not_overextended = abs(change_4h) <= PHASE_BREAK_MAX_4H_MOVE_PCT or break_up_pct >= PHASE_STRONG_BREAK_PCT * 2.0
        fresh_breakout = (
            breakout
            and phase_range_ok
            and not_overextended
            and momentum_ok
            and (volume_ok or strong_break)
        )
        detail["setup"] = "fresh_breakout_long" if fresh_breakout else "breakout_long" if breakout else "none"
        if breakout:
            detail["break_pct"] = round(break_up_pct, 3)
        detail["momentum_ok"] = momentum_ok
        detail["volume_ok"] = volume_ok
        detail["strong_break"] = strong_break
        detail["not_overextended"] = not_overextended
        detail["fresh_breakout"] = fresh_breakout
        ok = fresh_breakout
    if not ok:
        if detail.get("setup") in ("breakdown_short", "breakout_long"):
            detail["reason"] = (
                "phase_range_not_clean"
                if not phase_range_ok else
                "breakout_overextended_4h"
                if not not_overextended else
                "breakout_without_volume_or_momentum"
            )
        else:
            detail["reason"] = "no_15m_setup"
        return False, detail
    detail["passed"] = True
    return True, detail


def evaluate_symbol_multiperiod(
    cur,
    symbol: str,
    profile: str,
    global_trend: Optional[Dict[str, Any]] = None,
    big4: Optional[Dict[str, Any]] = None,
    global_regime: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile_l = profile.strip().lower()
    side = "LONG" if profile_l == "long" else "SHORT"
    out: Dict[str, Any] = {"symbol": symbol, "side": side, "passed": False, "reason": None, "score": 0.0, "ref_price": None}

    from app.services.brain_playbook import classify_playbook

    rows_1h = _fetch_klines(cur, symbol, "1h", 168)
    rows_15m = _fetch_klines(cur, symbol, "15m", 672)
    if len(rows_1h) < 60:
        out["reason"] = "insufficient_1h"
        return out
    if len(rows_15m) < 50:
        out["reason"] = "insufficient_15m"
        return out

    dims: Dict[str, Any] = {}
    c15 = h15 = l15 = v15 = []
    for key, label, _, _ in TREND_WINDOWS:
        c, h, l, v = _fetch_window(cur, symbol, key)
        if not c:
            out["reason"] = f"insufficient_{key}"
            return out
        dims[key] = {"label": label, **_window_direction(c, h, l)}
        if key == "d1":
            c15, h15, l15, v15 = c, h, l, v
            out["ref_price"] = c15[-1]

    global_bias = (global_trend or {}).get("bias") or "FLAT"
    future_4h = _future_4h_direction(c15, h15, l15, v15)

    pb = classify_playbook(rows_1h, rows_15m, big4=big4 or {})
    playbook = str(pb.get("playbook") or "D1")
    pb_side = str(pb.get("side") or "FLAT").upper()
    signals = set(pb.get("signals") or [])
    features = pb.get("features") or {}
    edge = float(pb.get("edge_score") or 0.0)
    confirmed = bool(pb.get("confirmed"))
    global_name = str((global_regime or {}).get("global_regime") or "GLOBAL_UNKNOWN")
    big4_bias = str((big4 or {}).get("bias") or "FLAT").upper()
    big4_ok = bool((big4 or {}).get("big4_ok", True))

    allowed_playbooks = {"LONG": {"A1", "C3"}, "SHORT": {"A2", "C1"}}[side]
    if pb_side != side:
        out.update({"reason": f"playbook_side_{pb_side}", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if playbook not in allowed_playbooks:
        out.update({"reason": f"playbook_{playbook}", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if not big4_ok:
        out.update({"reason": "big4_weak", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out

    has_break_signal = bool(signals & {"break_support", "break_resistance", "ema_reject", "ema_reclaim"})
    has_volume_signal = bool(signals & {"volume_expand_down", "volume_expand_up", "crash_spike", "pump_spike"})
    strong_token_side = features.get("h1_side") == side and features.get("m15_side") == side
    trend_playbook = playbook in {"A1", "A2"}
    breakout_playbook = playbook in {"C1", "C3"}

    min_edge = 0.68 if breakout_playbook else 0.72
    if side == "SHORT" and playbook == "C1":
        min_edge = 0.70
    if edge < min_edge:
        out.update({"reason": "low_edge", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if not confirmed:
        out.update({"reason": "unconfirmed_playbook", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if not (has_break_signal or has_volume_signal or strong_token_side):
        out.update({"reason": "weak_phase_evidence", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out

    if global_name == "DAILY_BEAR_PROBE" and side == "LONG":
        out.update({"reason": "daily_bear_blocks_long", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if global_name == "RELIEF_BOUNCE" and side == "SHORT" and not (playbook == "C1" and "break_support" in signals and "crash_spike" in signals):
        out.update({"reason": "relief_bounce_blocks_fresh_short", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out
    if big4_bias in ("LONG", "SHORT") and big4_bias != side and not strong_token_side:
        out.update({"reason": "big4_bias_opposes_symbol", "trend": dims, "future_4h": future_4h, "playbook": pb})
        return out

    setup = {
        "A1": "brain_A1_trend_continuation_long",
        "A2": "brain_A2_failed_rebound_short",
        "C1": "brain_C1_breakdown_short",
        "C3": "brain_C3_breakout_long",
    }.get(playbook, playbook)
    score = edge * 100.0
    if confirmed:
        score += 6
    if breakout_playbook:
        score += 5
    if has_break_signal:
        score += 4
    if has_volume_signal:
        score += 3
    if strong_token_side:
        score += 5
    if big4_bias == side:
        score += 4
    if global_name in ("DAILY_BEAR_PROBE", "BULL_RECOVERY") and (
        (side == "SHORT" and global_name == "DAILY_BEAR_PROBE")
        or (side == "LONG" and global_name == "BULL_RECOVERY")
    ):
        score += 4

    out.update({
        "passed": score >= 72,
        "reason": None if score >= 72 else "score_below_threshold",
        "score": round(min(100.0, score), 1),
        "trend": dims,
        "future_4h": future_4h,
        "playbook": pb,
        "signal_detail": {
            "strategy": "brain_playbook_phase_break",
            "global_trend": global_trend,
            "global_regime": global_regime,
            "trend_dimensions": dims,
            "future_4h": future_4h,
            "playbook": {
                "name": playbook,
                "side": pb_side,
                "edge_score": edge,
                "confirmed": confirmed,
                "signals": list(pb.get("signals") or []),
                "candidates": pb.get("candidates") or [],
                "evidence_summary": pb.get("evidence_summary"),
                "features": features,
            },
            "entry": {
                "setup": setup,
                "playbook": playbook,
                "edge_score": edge,
                "confirmed": confirmed,
                "h1_side": features.get("h1_side"),
                "m15_side": features.get("m15_side"),
                "rsi_1h": features.get("rsi_1h"),
                "rsi_15m": features.get("rsi_15m"),
                "has_break_signal": has_break_signal,
                "has_volume_signal": has_volume_signal,
                "strong_token_side": strong_token_side,
                "big4_bias": big4_bias,
                "global_regime": global_name,
            },
            "setup": setup,
        },
    })
    return out


def scan_universe(
    conn,
    profile: str,
    *,
    include_rejects: bool = False,
) -> Tuple[List[Dict[str, Any]], int]:
    symbols = load_midline_universe(conn)
    universe_size = len(symbols)
    results: List[Dict[str, Any]] = []
    profile_l = profile.strip().lower()

    with conn.cursor() as cur:
        global_trend = evaluate_global_trend_dimensions(cur)
        try:
            from app.services.brain_market_analyzer import evaluate_big4_gate
            from app.services.brain_market_regime import evaluate_global_daily_regime
            big4 = evaluate_big4_gate(cur)
            global_regime = evaluate_global_daily_regime(cur)
        except Exception as e:
            logger.warning(f"[破位机会] BRAIN 市场背景读取失败，降级为本页趋势背景: {e}")
            big4 = {"big4_ok": True, "bias": (global_trend or {}).get("bias") or "FLAT"}
            global_regime = {"global_regime": "GLOBAL_UNKNOWN", "reason": "fallback_global_trend"}
        for symbol in symbols:
            try:
                ev = evaluate_symbol_multiperiod(
                    cur,
                    symbol,
                    profile_l,
                    global_trend=global_trend,
                    big4=big4,
                    global_regime=global_regime,
                )
                if ev["passed"]:
                    results.append({
                        "symbol": ev["symbol"],
                        "side": ev["side"],
                        "score": float(ev["score"]),
                        "signal_detail": ev.get("signal_detail") or {},
                        "ref_price": ev.get("ref_price"),
                        "passed": True,
                        "reason": None,
                    })
                elif include_rejects:
                    results.append({
                        "symbol": ev["symbol"],
                        "side": ev["side"],
                        "score": float(ev.get("score") or 0),
                        "signal_detail": {
                            "global_trend": global_trend,
                            "trend_dimensions": ev.get("trend") or {},
                            "future_4h": ev.get("future_4h") or {},
                            "entry": ev.get("entry") or {},
                            "reason": ev.get("reason"),
                        },
                        "ref_price": ev.get("ref_price"),
                        "passed": False,
                        "reason": ev.get("reason"),
                    })
            except Exception as e:
                logger.debug(f"[中线扫描] {symbol} 跳过: {e}")
                if include_rejects:
                    results.append({
                        "symbol": symbol,
                        "side": "LONG" if profile_l == "long" else "SHORT",
                        "score": 0.0,
                        "signal_detail": {"error": str(e)},
                        "ref_price": None,
                        "passed": False,
                        "reason": "eval_error",
                    })
    results.sort(key=lambda x: (0 if x.get("passed") else 1, -float(x.get("score") or 0)))
    return results, universe_size


def signal_detail_json(detail: Dict[str, Any]) -> str:
    return json.dumps(detail, ensure_ascii=False, default=str)
