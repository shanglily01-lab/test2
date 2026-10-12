"""Bull-market auto policy — long-only 1h pullbacks, no hard stop.

Does not write allow_long / allow_short (INV-04). Watchlist stays user-manual.
Existing shorts are not force-closed.
"""
from __future__ import annotations

from typing import Optional, Tuple

BULL_OPEN_PLAYBOOKS = frozenset({"A1"})
BULL_LONG_ONLY_REASON = "bull_long_only_blocks_short"
BULL_A1_ONLY_PREFIX = "bull_a1_only"

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
    """Auto longs: no fixed SL / USD fuse / time stop. Ride losers to liquidation."""
    return is_auto_strategy_source(source) and str(side or "").upper() == "LONG"


def strip_fixed_exits(source: str, side: str) -> bool:
    return skip_hard_stop(source, side)
