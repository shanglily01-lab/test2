"""Call DeepSeek page API handlers directly to diagnose 500s."""
from __future__ import annotations

import sys
import asyncio
import inspect
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api import deepseek_explore_api as explore
from app.api import deepseek_predict_api as predict


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _call_with_timeout(fn, timeout_s: float = 15.0):
    value = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout_s)
    if inspect.isawaitable(value):
        return await asyncio.wait_for(value, timeout=timeout_s)
    return value


async def main() -> None:
    tests = [
        ("explore_status", lambda: explore.status()),
        ("explore_runs", lambda: explore.list_runs(5)),
        ("explore_positions_open", lambda: explore.list_positions("open", 50)),
        ("explore_positions_closed", lambda: explore.list_positions("closed", 50)),
        ("explore_live", lambda: explore.list_positions_live()),
        ("explore_stats", lambda: explore.stats(30)),
        ("predict_status", lambda: predict.status()),
        ("predict_runs", lambda: predict.list_runs(5)),
        ("predict_positions_open", lambda: predict.list_positions("open", 50)),
        ("predict_positions_closed", lambda: predict.list_positions("closed", 50)),
        ("predict_live", lambda: predict.list_positions_live()),
        ("predict_stats", lambda: predict.stats(30)),
    ]
    for name, fn in tests:
        try:
            print(f"{name}: start", flush=True)
            result = await _call_with_timeout(fn)
            count = result.get("count") if isinstance(result, dict) else ""
            print(f"{name}: OK count={count}", flush=True)
        except asyncio.TimeoutError:
            print(f"{name}: TIMEOUT", flush=True)
        except Exception as exc:
            print(f"{name}: ERR {type(exc).__name__}: {exc!r}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
