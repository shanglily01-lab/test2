"""REQ-BRAIN 胜率：近 7 日、同规则信号后 4h「方向对就算赢」；分向 long/short。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from app.services.brain_config import (
    BARS_1H_WEEK,
    WIN_PROB_MIN,
    WIN_PROB_REL_EDGE,
    WINRATE_FORWARD_HOURS,
    WINRATE_LOOKBACK_DAYS,
    WINRATE_MIN_SAMPLES,
    WINRATE_SYMBOL_MIN_N,
)
from app.services.brain_market_analyzer import _fetch_klines, _trend_side
from app.utils.futures_symbol import futures_symbol_rating_canonical

_WINRATE_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_WINRATE_TTL_S = 30 * 60


def _direction_at(closes: List[float], idx: int, lookback: int) -> str:
    if idx < lookback or idx >= len(closes):
        return "FLAT"
    window = closes[idx - lookback + 1 : idx + 1]
    side, _ = _trend_side(window, lookback)
    return side


def _forward_win(closes: List[float], idx: int, side: str, forward: int) -> Optional[bool]:
    if side not in ("LONG", "SHORT"):
        return None
    j = idx + forward
    if j >= len(closes):
        return None
    p0, p1 = closes[idx], closes[j]
    if p0 <= 0:
        return None
    if side == "LONG":
        return p1 > p0
    return p1 < p0


def evaluate_symbol_winrate(
    cur,
    symbol: str,
    *,
    lookback_days: int = WINRATE_LOOKBACK_DAYS,
    forward_hours: int = WINRATE_FORWARD_HOURS,
) -> Dict[str, Any]:
    """单币 1h 规则回测胜率（总分 + 分向）。"""
    symbol = futures_symbol_rating_canonical(symbol)
    need = lookback_days * 24 + forward_hours + BARS_1H_WEEK
    rows = _fetch_klines(cur, symbol, "1h", need)
    if len(rows) < BARS_1H_WEEK + forward_hours + 10:
        return {
            "symbol": symbol, "n": 0, "wins": 0, "win_prob": None,
            "n_long": 0, "wins_long": 0, "win_prob_long": None,
            "n_short": 0, "wins_short": 0, "win_prob_short": None,
            "reason": "insufficient",
        }

    closes = [float(r["close_price"]) for r in rows]
    start = max(BARS_1H_WEEK, len(closes) - lookback_days * 24 - forward_hours)
    wins = n = 0
    wins_l = n_l = 0
    wins_s = n_s = 0
    for idx in range(start, len(closes) - forward_hours, forward_hours):
        side = _direction_at(closes, idx, BARS_1H_WEEK)
        if side == "FLAT":
            continue
        ok = _forward_win(closes, idx, side, forward_hours)
        if ok is None:
            continue
        n += 1
        if ok:
            wins += 1
        if side == "LONG":
            n_l += 1
            if ok:
                wins_l += 1
        else:
            n_s += 1
            if ok:
                wins_s += 1

    def _p(w, cnt):
        return round(w / cnt, 4) if cnt else None

    return {
        "symbol": symbol,
        "n": n,
        "wins": wins,
        "win_prob": _p(wins, n),
        "n_long": n_l,
        "wins_long": wins_l,
        "win_prob_long": _p(wins_l, n_l),
        "n_short": n_s,
        "wins_short": wins_s,
        "win_prob_short": _p(wins_s, n_s),
        "reason": "ok" if n >= 3 else "few_samples",
    }


def compute_pool_winrate(
    conn,
    symbols: Sequence[str],
    *,
    max_symbols: int = 80,
    use_cache: bool = True,
) -> Dict[str, Any]:
    now = time.time()
    if use_cache and _WINRATE_CACHE.get("payload") and now - float(_WINRATE_CACHE.get("ts") or 0) < _WINRATE_TTL_S:
        return dict(_WINRATE_CACHE["payload"])

    syms = [futures_symbol_rating_canonical(s) for s in symbols][:max_symbols]
    per: Dict[str, Dict[str, Any]] = {}
    total_n = total_wins = 0
    pool_l_n = pool_l_w = 0
    pool_s_n = pool_s_w = 0
    with conn.cursor() as cur:
        for sym in syms:
            try:
                r = evaluate_symbol_winrate(cur, sym)
            except Exception as e:
                logger.debug(f"[BRAIN胜率] {sym} 失败: {e}")
                continue
            per[sym.replace("/", "")] = r
            total_n += int(r.get("n") or 0)
            total_wins += int(r.get("wins") or 0)
            pool_l_n += int(r.get("n_long") or 0)
            pool_l_w += int(r.get("wins_long") or 0)
            pool_s_n += int(r.get("n_short") or 0)
            pool_s_w += int(r.get("wins_short") or 0)

    def _p(w, cnt):
        return round(w / cnt, 4) if cnt else None

    pool_prob = _p(total_wins, total_n)
    pool_long = _p(pool_l_w, pool_l_n)
    pool_short = _p(pool_s_w, pool_s_n)
    payload = {
        "pool_n": total_n,
        "pool_wins": total_wins,
        "pool_win_prob": pool_prob,
        "pool_win_prob_long": pool_long,
        "pool_win_prob_short": pool_short,
        "pool_n_long": pool_l_n,
        "pool_n_short": pool_s_n,
        "min_required": WIN_PROB_MIN,
        "rel_edge": WIN_PROB_REL_EDGE,
        "pass_gate": bool(
            pool_prob is not None and total_n >= WINRATE_MIN_SAMPLES and pool_prob >= WIN_PROB_MIN
        ),
        "per_symbol": per,
        "asof_ts": now,
    }
    _WINRATE_CACHE["ts"] = now
    _WINRATE_CACHE["payload"] = payload
    logger.info(
        f"[BRAIN胜率] 池 n={total_n} win={pool_prob} "
        f"long={pool_long}(n={pool_l_n}) short={pool_short}(n={pool_s_n}) "
        f"pass={payload['pass_gate']}"
    )
    return payload


def resolve_win_prob_for_symbol(
    winrate_payload: Dict[str, Any],
    symbol: str,
) -> Optional[float]:
    """兼容旧接口：综合 win_prob。"""
    clean = futures_symbol_rating_canonical(symbol).replace("/", "")
    per = (winrate_payload or {}).get("per_symbol") or {}
    r = per.get(clean) or {}
    if r.get("win_prob") is not None and int(r.get("n") or 0) >= WINRATE_SYMBOL_MIN_N:
        return float(r["win_prob"])
    return winrate_payload.get("pool_win_prob")


def resolve_directional_win_probs(
    winrate_payload: Dict[str, Any],
    symbol: str,
) -> Dict[str, Optional[float]]:
    """返回 win_prob_long / win_prob_short（单币不够则回退池）。"""
    clean = futures_symbol_rating_canonical(symbol).replace("/", "")
    per = (winrate_payload or {}).get("per_symbol") or {}
    r = per.get(clean) or {}
    wl = r.get("win_prob_long")
    ws = r.get("win_prob_short")
    if wl is None or int(r.get("n_long") or 0) < WINRATE_SYMBOL_MIN_N:
        wl = winrate_payload.get("pool_win_prob_long")
    if ws is None or int(r.get("n_short") or 0) < WINRATE_SYMBOL_MIN_N:
        ws = winrate_payload.get("pool_win_prob_short")
    return {
        "win_prob_long": float(wl) if wl is not None else None,
        "win_prob_short": float(ws) if ws is not None else None,
    }


def directional_open_allowed(
    side: str,
    win_prob_long: Optional[float],
    win_prob_short: Optional[float],
    *,
    abs_min: float = WIN_PROB_MIN,
    rel_edge: float = WIN_PROB_REL_EDGE,
) -> tuple:
    """
    §7.3.14：该方向绝对 ≥ abs_min 且比反方向至少高 rel_edge。
    返回 (ok, reason)。
    """
    s = (side or "").upper()
    if s == "LONG":
        me, other = win_prob_long, win_prob_short
    elif s == "SHORT":
        me, other = win_prob_short, win_prob_long
    else:
        return False, "flat_side"
    if me is None:
        return False, "no_winprob"
    if me < abs_min:
        return False, f"winprob_below_{abs_min}"
    if other is not None and (me - other) < rel_edge:
        return False, f"rel_edge_lt_{rel_edge}"
    return True, "ok"
