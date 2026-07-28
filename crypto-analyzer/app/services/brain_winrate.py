"""REQ-BRAIN 胜率：近 7 日、同规则信号后 4h「方向对就算赢」。"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Set

from loguru import logger

from app.services.brain_config import (
    BARS_1H_WEEK,
    WIN_PROB_MIN,
    WINRATE_FORWARD_HOURS,
    WINRATE_LOOKBACK_DAYS,
    WINRATE_MIN_SAMPLES,
)
from app.services.brain_market_analyzer import _fetch_klines, _trend_side
from app.utils.futures_symbol import futures_symbol_rating_canonical

# 进程内缓存，避免每轮全市场扫爆
_WINRATE_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_WINRATE_TTL_S = 30 * 60


def _direction_at(closes: List[float], idx: int, lookback: int) -> str:
    """在 closes[idx] 时刻，用此前 lookback 根做趋势（不含未来）。"""
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
    """单币 1h 规则回测胜率。"""
    symbol = futures_symbol_rating_canonical(symbol)
    need = lookback_days * 24 + forward_hours + BARS_1H_WEEK
    rows = _fetch_klines(cur, symbol, "1h", need)
    if len(rows) < BARS_1H_WEEK + forward_hours + 10:
        return {"symbol": symbol, "n": 0, "wins": 0, "win_prob": None, "reason": "insufficient"}

    closes = [float(r["close_price"]) for r in rows]
    # 在近 7*24 根上每隔 4h 采样一次信号，避免过度相关
    start = max(BARS_1H_WEEK, len(closes) - lookback_days * 24 - forward_hours)
    wins = 0
    n = 0
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
    win_prob = (wins / n) if n else None
    return {
        "symbol": symbol,
        "n": n,
        "wins": wins,
        "win_prob": round(win_prob, 4) if win_prob is not None else None,
        "reason": "ok" if n >= 3 else "few_samples",
    }


def compute_pool_winrate(
    conn,
    symbols: Sequence[str],
    *,
    max_symbols: int = 80,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    L0/L1 池汇总近 7 日×4h 规则胜率；并附每币 win_prob（样本足够时）。
    """
    now = time.time()
    if use_cache and _WINRATE_CACHE.get("payload") and now - float(_WINRATE_CACHE.get("ts") or 0) < _WINRATE_TTL_S:
        return dict(_WINRATE_CACHE["payload"])

    syms = [futures_symbol_rating_canonical(s) for s in symbols][:max_symbols]
    per: Dict[str, Dict[str, Any]] = {}
    total_n = 0
    total_wins = 0
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

    pool_prob = (total_wins / total_n) if total_n else None
    payload = {
        "pool_n": total_n,
        "pool_wins": total_wins,
        "pool_win_prob": round(pool_prob, 4) if pool_prob is not None else None,
        "min_required": WIN_PROB_MIN,
        "pass_gate": bool(pool_prob is not None and total_n >= WINRATE_MIN_SAMPLES and pool_prob >= WIN_PROB_MIN),
        "per_symbol": per,
        "asof_ts": now,
    }
    _WINRATE_CACHE["ts"] = now
    _WINRATE_CACHE["payload"] = payload
    logger.info(
        f"[BRAIN胜率] 池 n={total_n} wins={total_wins} "
        f"win_prob={payload['pool_win_prob']} pass={payload['pass_gate']}"
    )
    return payload


def resolve_win_prob_for_symbol(
    winrate_payload: Dict[str, Any],
    symbol: str,
) -> Optional[float]:
    """优先用单币（n≥5），否则用池胜率。"""
    clean = futures_symbol_rating_canonical(symbol).replace("/", "")
    per = (winrate_payload or {}).get("per_symbol") or {}
    r = per.get(clean) or {}
    if r.get("win_prob") is not None and int(r.get("n") or 0) >= 5:
        return float(r["win_prob"])
    return winrate_payload.get("pool_win_prob")
