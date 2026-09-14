"""Structure swing hold: buy the low, sell the high (shorts reversed).

BRAIN / breakout no longer force-close at 4–6h. Take-profit is the opposite
15m structure point; hard SL and the BRAIN USD fuse still protect the account.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from app.services.entry_timing import compute_structure_exit

STRUCTURE_MIN_AGE_MIN = 20
STRUCTURE_MIN_PEAK_PCT = 0.40
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


def check_structure_swing_exit(
    side: str,
    rows_15m: List[Dict[str, Any]],
    *,
    peak_pct: float,
    age_s: float,
    ref_price: Optional[float] = None,
    playbook_row: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """Close a long at a suitable high, or a short at a suitable low."""
    if age_s < STRUCTURE_MIN_AGE_MIN * 60:
        return None
    try:
        peak = float(peak_pct or 0.0)
    except (TypeError, ValueError):
        peak = 0.0
    if peak < STRUCTURE_MIN_PEAK_PCT / 100.0:
        return None
    timing = compute_structure_exit(
        side, rows_15m, playbook_row=playbook_row, ref_price=ref_price,
    )
    if not timing.ready:
        return None
    side_u = (side or "").upper()
    if side_u == "LONG":
        return f"structure_sell_high:{timing.status}:{timing.reason}"
    return f"structure_cover_low:{timing.status}:{timing.reason}"


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
