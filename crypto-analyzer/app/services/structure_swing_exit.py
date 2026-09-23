"""Structure swing hold: buy the low, sell the high (shorts reversed).

BRAIN / breakout no longer force-close at 4–6h. Preferred take-profit is the
opposite 15m structure point. If that point is missed or profit is given back,
lock the remainder — do not ride winners to the hard SL.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.entry_timing import compute_structure_exit

STRUCTURE_MIN_AGE_MIN = 20
# Do not bank 0.4–0.8% crumbs (≈20U on 1000U×5x) while losers still run to SL.
STRUCTURE_MIN_PEAK_PCT = 1.80
STRUCTURE_TRAIL_ACTIVATE_PCT = 1.80
STRUCTURE_TRAIL_PULLBACK_PCT = 0.55
STRUCTURE_TRAIL_MIN_KEEP_PCT = 0.80
STRUCTURE_BULL_TRAIL_ACTIVATE_PCT = 2.50
STRUCTURE_BULL_TRAIL_PULLBACK_PCT = 0.80
STRUCTURE_BULL_TRAIL_MIN_KEEP_PCT = 1.20
STRUCTURE_GIVEBACK_PEAK_PCT = 2.20
STRUCTURE_GIVEBACK_NOW_PCT = 0.15
STRUCTURE_STALE_AGE_H = 4.0
STRUCTURE_STALE_PNL_PCT = 1.50
# C1/C3/B2 enter at the break; "already off the low/high" is normal noise.
STRUCTURE_FOLLOW_PLAYBOOKS = frozenset({"C1", "C3", "B2"})
_KLINE_LIMIT = 48
_KLINE_TTL_S = 20.0
_kline_cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}


def uses_structure_swing_hold(source: str) -> bool:
    src = (source or "").strip().lower()
    if not src:
        return False
    try:
        from app.services.brain_config import is_brain_source
        if is_brain_source(src):
            return True
    except Exception:
        if src.startswith("brain_"):
            return True
    try:
        from app.services.midline_swing_config import is_midline_source
        return is_midline_source(src)
    except Exception:
        return src.startswith("midline_")


def playbook_row_from_position(pos: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pos:
        return None
    raw = pos.get("signal_components")
    components: Any = raw
    if isinstance(raw, str) and raw.strip():
        try:
            import json
            components = json.loads(raw)
        except Exception:
            components = {}
    if not isinstance(components, dict):
        components = {}
    pb = components.get("playbook")
    if isinstance(pb, dict):
        return pb
    signals = components.get("signals") or []
    name = pb or pos.get("entry_signal_type") or ""
    return {"playbook": name, "signals": list(signals or [])}


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def playbook_name(playbook_row: Optional[Dict[str, Any]] = None, raw: str = "") -> str:
    text = raw or ""
    if isinstance(playbook_row, dict):
        nested = playbook_row.get("playbook")
        if isinstance(nested, dict):
            text = text or str(nested.get("name") or nested.get("playbook") or "")
        else:
            text = text or str(nested or playbook_row.get("name") or "")
    blob = str(text or "").upper()
    for name in ("A1", "A2", "B2", "B3", "C1", "C3", "C4"):
        if name in blob:
            return name
    return blob.strip()


def is_follow_playbook(playbook_row: Optional[Dict[str, Any]] = None, raw: str = "") -> bool:
    return playbook_name(playbook_row, raw) in STRUCTURE_FOLLOW_PLAYBOOKS


def _trail_profile(side: str, market_bias: Optional[str]) -> Tuple[float, float, float]:
    if str(side or "").upper() == "LONG" and str(market_bias or "").upper() == "LONG":
        return (
            STRUCTURE_BULL_TRAIL_ACTIVATE_PCT / 100.0,
            STRUCTURE_BULL_TRAIL_PULLBACK_PCT / 100.0,
            STRUCTURE_BULL_TRAIL_MIN_KEEP_PCT / 100.0,
        )
    return (
        STRUCTURE_TRAIL_ACTIVATE_PCT / 100.0,
        STRUCTURE_TRAIL_PULLBACK_PCT / 100.0,
        STRUCTURE_TRAIL_MIN_KEEP_PCT / 100.0,
    )


def check_structure_profit_lock(
    pnl_pct: float,
    peak_pct: float,
    age_s: float,
    *,
    side: str = "",
    market_bias: Optional[str] = None,
    structure_status: str = "",
    playbook_row: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Lock a winner that never printed a clean opposite structure point.

    Does not time-stop losers. Hard SL / midline −120U still cover those.
    """
    pnl = _f(pnl_pct)
    peak = max(_f(peak_pct), pnl)
    age = _f(age_s)
    if age < STRUCTURE_MIN_AGE_MIN * 60:
        return None
    status = (structure_status or "").strip().lower()
    follow = is_follow_playbook(playbook_row)
    if (
        status in ("missed_high", "missed_low")
        and pnl >= STRUCTURE_MIN_PEAK_PCT / 100.0
        and not follow
    ):
        return f"structure_missed_take:{status}:now={pnl * 100:.2f}%"
    if (
        peak >= STRUCTURE_GIVEBACK_PEAK_PCT / 100.0
        and pnl <= STRUCTURE_GIVEBACK_NOW_PCT / 100.0
    ):
        return (
            f"structure_profit_to_loss:"
            f"peak={peak * 100:.2f}%,now={pnl * 100:.2f}%"
        )
    act, pull, keep = _trail_profile(side, market_bias)
    if peak >= act:
        drawdown = peak - pnl
        if drawdown >= pull:
            tag = "below_keep" if pnl < keep else "lock"
            return (
                f"structure_trail_{tag}:"
                f"peak={peak * 100:.2f}%,dd={drawdown * 100:.2f}%,now={pnl * 100:.2f}%"
            )
    if (
        age >= STRUCTURE_STALE_AGE_H * 3600
        and pnl >= STRUCTURE_STALE_PNL_PCT / 100.0
    ):
        return f"structure_stale_take:age={age / 3600:.1f}h,now={pnl * 100:.2f}%"
    return None


def check_structure_swing_exit(
    side: str,
    rows_15m: List[Dict[str, Any]],
    *,
    peak_pct: float,
    age_s: float,
    ref_price: Optional[float] = None,
    playbook_row: Optional[Dict[str, Any]] = None,
    pnl_pct: Optional[float] = None,
    market_bias: Optional[str] = None,
) -> Optional[str]:
    """Close a long at a suitable high, or a short at a suitable low.

    If the structure point never prints, lock leftover profit instead of
    waiting for the hard SL.
    """
    if age_s < STRUCTURE_MIN_AGE_MIN * 60:
        return None
    peak = _f(peak_pct)
    pnl = _f(pnl_pct) if pnl_pct is not None else peak
    peak = max(peak, pnl)
    timing = None
    if rows_15m and len(rows_15m) >= 24:
        timing = compute_structure_exit(
            side, rows_15m, playbook_row=playbook_row, ref_price=ref_price,
        )
        if timing.ready and peak >= STRUCTURE_MIN_PEAK_PCT / 100.0:
            side_u = (side or "").upper()
            if side_u == "LONG":
                return f"structure_sell_high:{timing.status}:{timing.reason}"
            return f"structure_cover_low:{timing.status}:{timing.reason}"
    status = (timing.status if timing else "") or ""
    return check_structure_profit_lock(
        pnl,
        peak,
        age_s,
        side=side,
        market_bias=market_bias,
        structure_status=status,
        playbook_row=playbook_row,
    )


def load_15m_rows(symbol: str, limit: int = _KLINE_LIMIT) -> List[Dict[str, Any]]:
    sym = (symbol or "").strip()
    if not sym:
        return []
    now = time.time()
    hit = _kline_cache.get(sym)
    if hit and now - hit[0] < _KLINE_TTL_S:
        return hit[1]
    rows = _fetch_15m_rows(sym, limit)
    _kline_cache[sym] = (now, rows)
    return rows


def _fetch_15m_rows(symbol: str, limit: int) -> List[Dict[str, Any]]:
    try:
        from app.utils.futures_symbol import futures_symbol_rating_canonical
        symbol = futures_symbol_rating_canonical(symbol)
    except Exception:
        pass
    try:
        from app.services.binance_data_hub import BinanceDataHub, get_global_data_hub

        hub = get_global_data_hub()
        rows = []
        if hub is not None:
            rows = hub.get_klines_sync(
                symbol, interval="15m", limit=limit, allow_rest_fallback=False,
            ) or []
        if not rows:
            rows = BinanceDataHub.rest_klines_emergency(symbol, "15m", limit) or []
        if rows:
            return list(rows)
    except Exception:
        pass
    try:
        import pymysql
        from app.utils.config_loader import get_db_config

        conn = pymysql.connect(
            **get_db_config(),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=3,
            read_timeout=8,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT open_time, open_price, high_price, low_price, close_price, volume
                    FROM kline_data
                    WHERE symbol=%s AND timeframe='15m' AND exchange='binance_futures'
                    ORDER BY open_time DESC
                    LIMIT %s
                    """,
                    (symbol, int(limit)),
                )
                rows = list(reversed(cur.fetchall() or []))
            return rows
        finally:
            conn.close()
    except Exception:
        return []
