#!/usr/bin/env python3
"""Smoke test: all production prompts build in Chinese (no API)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import dotenv_values

for k, v in dotenv_values(ROOT / ".env").items():
    if v is not None and k not in os.environ:
        os.environ[k] = v


def _assert_zh(text: str, label: str) -> None:
    if not any(c >= "\u4e00" for c in text):
        raise AssertionError(f"{label}: no Chinese characters in prompt")
    if "You are a **paper position**" in text or "Momentum chase long" in text:
        raise AssertionError(f"{label}: still contains English production markers")


def main() -> int:
    from app.services.ai_explore_prompt import build_explore_prompt
    from app.services.ai_predict_prompt import build_predict_prompt
    from app.services.ai_reversal_explore_prompt import build_reversal_explore_prompt
    from app.services.ai_tactical_explore_prompts import (
        TACTICAL_STRATEGIES,
        build_strategy_prompt,
    )
    from app.services.gemini_position_advisor import GeminiPositionAdvisor
    from app.services.open_advisor_strategy_rubrics import (
        build_open_advisor_prompt,
        resolve_strategy_profile,
    )
    from app.services.gpt_llm_client import GPT_JSON_SYSTEM_ZH
    import inspect
    import app.services.gpt_explore_worker as gew
    import app.services.gpt_predictor as gp
    import app.services.tactical_explore_workers as tw

    u = {
        "BTC/USDT": {
            "symbol": "BTC/USDT",
            "change_24h": 5,
            "tech": {"rsi_14_1h": 55, "below_7d_high_pct": -8},
            "kline_narrative": {"1h": "整体24根偏多 近6根震荡", "15m": "近6根阳线"},
        }
    }
    ctx = {"big4_signal": "NEUTRAL", "btc_6h_change": 0, "eth_6h_change": 0}

    p, _ = build_explore_prompt(u, ctx, {})
    _assert_zh(p, "explore")
    assert "你是" in p or "探索" in p

    pp = build_predict_prompt(
        [{"symbol": "BTC/USDT", "kline_narrative": {"1h": "x"}, "current_price": 1}],
        ctx,
    )
    _assert_zh(pp, "predict")
    assert "超级交易大师" in pp or "预测" in pp

    rp, _ = build_reversal_explore_prompt(u, ctx, {})
    _assert_zh(rp, "reversal")
    assert "反转" in rp

    for key in TACTICAL_STRATEGIES:
        sp, _ = build_strategy_prompt(key, u, ctx, {})
        _assert_zh(sp, f"tactical:{key}")
        assert TACTICAL_STRATEGIES[key].title_zh in sp

    adv_ctx = {
        "klines_15m": [{"t": "01-01 00:00", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        "klines_1h": [],
        "big4_signal": "NEUTRAL",
        "big4_strength": 0,
        "allow_long": True,
        "allow_short": True,
        "btc_6h_change": 0,
        "eth_6h_change": 0,
        "narrative_1h": "",
        "narrative_15m": "",
        "rsi_14_1h": 55.0,
    }
    sources = [
        "gemini_explore", "gemini_predict", "deepseek_explore", "deepseek_predict",
        "gpt_explore", "gpt_predict",
        "gemini_pullback", "gemini_rebound", "gemini_chase", "gemini_dump",
        "gemini_reversal", "deepseek_pullback", "gpt_chase",
        "s1_early_long", "s6_volume_spike", "s9_gemini_ai", "smart_trader",
    ]
    for src in sources:
        prof = resolve_strategy_profile(src)
        op = build_open_advisor_prompt(
            profile=prof,
            symbol="BTC/USDT",
            side="LONG",
            price=1.0,
            source=src,
            catalyst="1h 近24根偏多 近5根回踩",
            leverage=5,
            sl_pct=4.0,
            tp_pct=6.0,
            hold_hours=4.0,
            ctx=adv_ctx,
            format_kline_table=GeminiPositionAdvisor._format_kline_table,
        )
        _assert_zh(op, f"open_advisor:{src}")

    pos = {
        "entry_price": 100.0,
        "leverage": 5,
        "position_side": "LONG",
        "symbol": "BTC/USDT",
        "hold_hours": 3.0,
        "source": "gemini_explore",
    }
    hp = GeminiPositionAdvisor._build_prompt(pos, 101.0, adv_ctx)
    _assert_zh(hp, "hold_advisor")
    assert "持仓监管" in hp

    assert "量化" in GPT_JSON_SYSTEM_ZH
    assert "GPT_JSON_SYSTEM_ZH" in inspect.getsource(gew._call_gpt_explore)
    assert "GPT_JSON_SYSTEM_ZH" in inspect.getsource(gp._call_gpt_predict)
    assert "GPT_JSON_SYSTEM_ZH" in inspect.getsource(tw._call_gpt)

    print(f"[OK] smoke: explore/predict/reversal + {len(TACTICAL_STRATEGIES)} tactical")
    print(f"[OK] open advisor x{len(sources)} sources + hold advisor")
    print("[OK] GPT system ZH wired in workers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
