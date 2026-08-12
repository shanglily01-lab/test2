"""中线 v2 扫描 — config.yaml 标的池 + 30×1d / ~1w×1h / 4h×15m 三层 AND.

权威需求: docs/REQUIREMENTS_LOGIC_ZH.md §7.2.4
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
# 目标：上涨趋势中的回踩做多 / 下跌趋势中的反抽做空
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


def _entry_15m_opportunity(
    closes_15m: List[float],
    highs_15m: List[float],
    lows_15m: List[float],
    vols_15m: List[float],
    side: str,
) -> Tuple[bool, Dict[str, Any]]:
    detail: Dict[str, Any] = {"layer": "entry_15m_opportunity"}
    if len(closes_15m) < 32:
        detail["reason"] = "insufficient_15m"
        return False, detail
    last = closes_15m[-1]
    prev_hi = max(highs_15m[-32:-4])
    prev_lo = min(lows_15m[-32:-4])
    recent_hi = max(highs_15m[-4:])
    recent_lo = min(lows_15m[-4:])
    vol_recent = sum(vols_15m[-4:]) / 4
    vol_prior = sum(vols_15m[-16:-4]) / 12 if len(vols_15m) >= 16 else vol_recent
    vol_ratio = vol_recent / vol_prior if vol_prior > 0 else 1.0
    detail.update({"prev_hi": round(prev_hi, 8), "prev_lo": round(prev_lo, 8), "vol_ratio": round(vol_ratio, 3)})
    if side == "SHORT":
        lower_high = recent_hi <= prev_hi * 1.002 and closes_15m[-1] <= max(closes_15m[-4:-1])
        breakdown = last < prev_lo * 0.998
        stalled = lower_high and vol_ratio <= 1.10
        detail["setup"] = "failed_rebound_short" if stalled else "breakdown_short" if breakdown else "none"
        ok = stalled or breakdown
    else:
        higher_low = recent_lo >= prev_lo * 0.998 and closes_15m[-1] >= min(closes_15m[-4:-1])
        reclaim = last > prev_hi * 1.002
        detail["setup"] = "support_rebound_long" if higher_low else "reclaim_long" if reclaim else "none"
        ok = higher_low or reclaim
    if not ok:
        detail["reason"] = "no_15m_setup"
        return False, detail
    detail["passed"] = True
    return True, detail


def evaluate_symbol_multiperiod(
    cur,
    symbol: str,
    profile: str,
    global_trend: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    profile_l = profile.strip().lower()
    side = "LONG" if profile_l == "long" else "SHORT"
    out: Dict[str, Any] = {"symbol": symbol, "side": side, "passed": False, "reason": None, "score": 0.0, "ref_price": None}
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
    macro_side = dims["cycle"]["side"]
    m3_side = dims["m3"]["side"]
    m1_side = dims["m1"]["side"]
    d7_side = dims["d7"]["side"]
    d1_side = dims["d1"]["side"]
    future_4h = _future_4h_direction(c15, h15, l15, v15)
    future_side = future_4h["side"]
    if future_side != side:
        out.update({
            "reason": "future_4h_direction_mismatch",
            "trend": dims,
            "future_4h": future_4h,
        })
        return out
    if side == "SHORT":
        if global_bias == "LONG" and m3_side != "SHORT":
            reason = "global_bull_blocks_short"
            out.update({"reason": reason, "trend": dims})
            return out
        trend_ok = macro_side in ("SHORT", "FLAT") and m3_side in ("SHORT", "FLAT") and (m1_side == "SHORT" or d7_side == "SHORT")
    else:
        if global_bias == "SHORT" and not (d7_side == "SHORT" and d1_side == "LONG"):
            reason = "global_bear_allows_only_support_rebound"
            out.update({"reason": reason, "trend": dims})
            return out
        trend_ok = (m3_side == "LONG" and m1_side in ("LONG", "FLAT")) or (d7_side == "SHORT" and d1_side == "LONG")
    if not trend_ok:
        out.update({"reason": "trend_dimensions_not_aligned", "trend": dims})
        return out

    ok_entry, entry = _entry_15m_opportunity(c15, h15, l15, v15, side)
    if not ok_entry:
        out.update({"reason": entry.get("reason") or "entry_fail", "trend": dims, "entry": entry})
        return out

    score = 0.0
    for key in ("cycle", "m3", "m1", "d7", "d1"):
        d = dims[key]
        score += 18 if d["side"] == side else 7 if d["side"] == "FLAT" else 0
    score += 10
    if side == "SHORT" and global_bias == "SHORT":
        score += 8
    if side == "LONG" and global_bias == "SHORT":
        score *= 0.55
    out.update({
        "passed": score >= 62,
        "reason": None if score >= 62 else "score_below_threshold",
        "score": round(score, 1),
        "trend": dims,
        "future_4h": future_4h,
        "entry": entry,
        "signal_detail": {
            "global_trend": global_trend,
            "trend_dimensions": dims,
            "future_4h": future_4h,
            "entry": entry,
            "setup": entry.get("setup"),
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
        for symbol in symbols:
            try:
                ev = evaluate_symbol_multiperiod(cur, symbol, profile_l, global_trend=global_trend)
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
