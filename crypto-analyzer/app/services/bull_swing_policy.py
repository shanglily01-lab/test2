"""Bull-market auto policy — long-only 1h pullbacks, no hard stop.

Does not write allow_long / allow_short (INV-04). Watchlist stays user-manual.
Existing shorts are not force-closed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence, Tuple

BULL_OPEN_PLAYBOOKS = frozenset({"A1"})
BULL_LONG_ONLY_REASON = "bull_long_only_blocks_short"
BULL_A1_ONLY_PREFIX = "bull_a1_only"
# 大脑 + 破位合计。同一币只留 1 槽，全书最多 12 笔，浮亏到 -200U 就平。
STRUCTURE_BOOK_CAP = 12
STRUCTURE_SYMBOL_CAP = 1
STRUCTURE_MAX_LOSS_USD = 200.0
# 亏损单：满 8h 看已收盘 15m 连续阴线；满 12h 再看已收盘 5m 连续阴线。
LOSS_YIN_15M_MIN_HOURS = 8.0
LOSS_YIN_5M_MIN_HOURS = 12.0
LOSS_YIN_STREAK = 3

_AUTO_EXACT = frozenset({
    "brain_swing",
    "brain_long",
    "brain_short",
    "midline_long",
    "midline_short",
    "deepseek_explore",
    "deepseek_predict",
    "gpt_explore",
    "gpt_predict",
})
_AUTO_PREFIXES = (
    "brain_",
    "midline_",
    "deepseek_",
    "gpt_",
    "gemini_",
)


def _src(source: str) -> str:
    return (source or "").strip().lower()


def is_watchlist_source(source: str) -> bool:
    src = _src(source)
    if src == "manual_watchlist":
        return True
    try:
        from app.services.watchlist_config import is_watchlist_source as _wl
        return _wl(src)
    except Exception:
        return False


def is_auto_strategy_source(source: str) -> bool:
    src = _src(source)
    if not src or is_watchlist_source(src):
        return False
    if src in _AUTO_EXACT:
        return True
    return src.startswith(_AUTO_PREFIXES)


def is_structure_auto_source(source: str) -> bool:
    src = _src(source)
    if src.startswith("brain_") or src.startswith("midline_"):
        return True
    try:
        from app.services.brain_config import is_brain_source
        if is_brain_source(src):
            return True
    except Exception:
        pass
    try:
        from app.services.midline_swing_config import is_midline_source
        if is_midline_source(src):
            return True
    except Exception:
        pass
    return False


def auto_short_blocked(source: str, side: str) -> Tuple[bool, str]:
    if str(side or "").upper() != "SHORT":
        return False, ""
    if not is_auto_strategy_source(source):
        return False, ""
    return True, BULL_LONG_ONLY_REASON


def bull_open_playbook_ok(playbook: str, side: str) -> Tuple[bool, str]:
    side_u = str(side or "").upper()
    pb = str(playbook or "").strip().upper()
    if side_u == "SHORT":
        return False, BULL_LONG_ONLY_REASON
    if side_u != "LONG":
        return False, "bull_long_only_flat"
    if pb and pb not in BULL_OPEN_PLAYBOOKS:
        return False, f"{BULL_A1_ONLY_PREFIX}:{pb}"
    return True, ""


def auto_open_allowed(
    source: str,
    side: str,
    playbook: str = "",
) -> Tuple[bool, str]:
    blocked, why = auto_short_blocked(source, side)
    if blocked:
        return False, why
    if is_structure_auto_source(source):
        return bull_open_playbook_ok(playbook, side)
    return True, ""


def skip_hard_stop(source: str, side: str) -> bool:
    """Auto longs skip the tight price SL. Structure books still cut at STRUCTURE_MAX_LOSS_USD."""
    return is_auto_strategy_source(source) and str(side or "").upper() == "LONG"


def structure_loss_cap_reason(unrealized_usd: float) -> Optional[str]:
    """Brain/breakout longs: cut once the margin loss reaches the cap."""
    try:
        pnl = float(unrealized_usd)
    except (TypeError, ValueError):
        return None
    cap = abs(float(STRUCTURE_MAX_LOSS_USD))
    if pnl <= -cap:
        return f"structure_loss_cap:{pnl:.0f}"
    return None


def _as_utc_naive(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 1e12:
            raw = raw / 1000.0
        return datetime.utcfromtimestamp(raw)
    return None


def _closed_yin_flags(rows: Optional[Sequence[Any]], interval_s: int, now: datetime) -> list:
    """已收盘 K 线里，做多阴线（收盘 < 开盘）为 True。未收盘的一根不算。"""
    flags = []
    step = timedelta(seconds=int(interval_s))
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            open_px = row.get("open")
            if open_px is None:
                open_px = row.get("open_price")
            close_px = row.get("close")
            if close_px is None:
                close_px = row.get("close_price")
            open_px = float(open_px)
            close_px = float(close_px)
        except (TypeError, ValueError):
            continue
        if open_px <= 0 or close_px <= 0:
            continue
        opened = _as_utc_naive(row.get("open_time"))
        if opened is None or opened + step > now:
            continue
        flags.append(close_px < open_px)
    return flags


def _trailing_true(flags: Sequence[bool]) -> int:
    streak = 0
    for flag in reversed(list(flags)):
        if not flag:
            break
        streak += 1
    return streak


def losing_long_candle_stop_reason(
    side: str,
    unrealized_usd: float,
    age_s: float,
    rows_15m: Optional[Sequence[Any]] = None,
    rows_5m: Optional[Sequence[Any]] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """亏损的做多单：8h 后连续 3 根已收盘 15m 阴线，或 12h 后连续 3 根已收盘 5m 阴线。"""
    if str(side or "").upper() != "LONG":
        return None
    try:
        pnl = float(unrealized_usd)
        age_h = float(age_s) / 3600.0
    except (TypeError, ValueError):
        return None
    if pnl >= 0 or age_h < LOSS_YIN_15M_MIN_HOURS:
        return None
    clock = now or datetime.utcnow()
    if clock.tzinfo is not None:
        clock = clock.astimezone(timezone.utc).replace(tzinfo=None)
    n15 = _trailing_true(_closed_yin_flags(rows_15m, 15 * 60, clock))
    if n15 >= LOSS_YIN_STREAK:
        return f"loss_15m_yin:{n15}"
    if age_h < LOSS_YIN_5M_MIN_HOURS:
        return None
    n5 = _trailing_true(_closed_yin_flags(rows_5m, 5 * 60, clock))
    if n5 >= LOSS_YIN_STREAK:
        return f"loss_5m_yin:{n5}"
    return None


def strip_fixed_exits(source: str, side: str) -> bool:
    return skip_hard_stop(source, side)
