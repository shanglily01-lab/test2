"""Legacy import shim for explore worker implementation.

Implementation lives in explore_worker_impl.py. Prefer importing via
explore_worker_common for active DeepSeek / prepared-bundle paths.
"""
from __future__ import annotations

from app.services.explore_worker_impl import (  # noqa: F401
    EXPLORE_ACCOUNT_ID,
    EXPLORE_FUNDING_FRESH_MIN,
    EXPLORE_LEVERAGE,
    EXPLORE_MARGIN_USD,
    EXPLORE_PRICE_FRESH_MIN,
    EXPLORE_SOURCE,
    NORMAL_CHG_MAX,
    NORMAL_CHG_MIN,
    NORMAL_MOVER,
    NORMAL_MOVER_MIN_VOLUME,
    _build_global_context,
    _build_universe,
    _connect,
    _enrich_universe,
    _get_current_price,
    _would_instant_tp,
    run_explore_round,
)

__all__ = [
    "EXPLORE_ACCOUNT_ID",
    "EXPLORE_FUNDING_FRESH_MIN",
    "EXPLORE_LEVERAGE",
    "EXPLORE_MARGIN_USD",
    "EXPLORE_PRICE_FRESH_MIN",
    "EXPLORE_SOURCE",
    "NORMAL_CHG_MAX",
    "NORMAL_CHG_MIN",
    "NORMAL_MOVER",
    "NORMAL_MOVER_MIN_VOLUME",
    "_build_global_context",
    "_build_universe",
    "_connect",
    "_enrich_universe",
    "_get_current_price",
    "_would_instant_tp",
    "run_explore_round",
]


if __name__ == "__main__":
    rid = run_explore_round(triggered_by="manual")
    print(f"run_id={rid}")
