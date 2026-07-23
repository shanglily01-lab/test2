"""中线 v2 扫描 — config.yaml 标的池 + 30×1d / ~1w×1h / 4h×15m 三层 AND.

权威需求: docs/REQUIREMENTS_LOGIC_ZH.md §7.2.4
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from app.services.securities_filter import is_security
from app.utils.futures_symbol import futures_symbol_clean, futures_symbol_rating_canonical

# 默认硬规则（二期可调参）
DAILY_TREND_PCT = 8.0
DAILY_RSI_LONG = (40.0, 70.0)
DAILY_RSI_SHORT = (30.0, 60.0)
DAILY_VOL_RATIO_MIN = 0.7
H1_RSI_LONG_MIN = 45.0
H1_RSI_SHORT_MAX = 55.0
RANGE_BOTTOM_PCT = 0.20
RANGE_TOP_PCT = 0.80
ATR_SHRINK_RATIO = 0.85
VOL_SHRINK_RATIO = 0.75


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
        if not clean or clean in seen or is_security(canon):
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
        if ma_24 < ma_168:
            detail["reason"] = "h1_ma_not_bullish"
            return False, detail
        if rsi is None or rsi < H1_RSI_LONG_MIN:
            detail["reason"] = "h1_rsi_low"
            return False, detail
    else:
        if ma_24 > ma_168:
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

    # 近 4 根 vs 前 12 根 波幅
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
        if pos > RANGE_BOTTOM_PCT:
            detail["reason"] = "not_near_low"
            return False, detail
        if shrink > ATR_SHRINK_RATIO:
            detail["reason"] = "not_stabilized_range"
            return False, detail
        # 近 3 根不创新低
        if min(lows_15m[-3:]) < min(lows_15m[-6:-3]) * 0.999:
            # 更严：近 3 收盘不创新低
            pass
        if last3[-1] < min(last3[:-1]):
            detail["reason"] = "still_making_lower_close"
            return False, detail
    else:
        if pos < RANGE_TOP_PCT:
            detail["reason"] = "not_near_high"
            return False, detail
        if vol_ratio > VOL_SHRINK_RATIO:
            detail["reason"] = "not_volume_shrink"
            return False, detail
        if last3[-1] > max(last3[:-1]):
            detail["reason"] = "still_making_higher_close"
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


def signal_detail_json(detail: Dict[str, Any]) -> str:
    return json.dumps(detail, ensure_ascii=False, default=str)
