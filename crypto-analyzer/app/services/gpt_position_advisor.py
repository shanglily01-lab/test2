"""GPT 模拟仓顾问 — 仅开仓审核（gpt_* 持仓由 DeepSeek 顾问监管）."""
from __future__ import annotations

from typing import Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gpt_llm_client import _completion_token_param
from app.services.gemini_position_advisor import (
    GeminiPositionAdvisor,
    OPEN_ADVISOR_JSON_SYSTEM_ZH,
)
from app.services.gpt_advisor_reviews import log_gpt_advisor_review
from app.services.open_advisor_routing import is_gpt_order_source
from app.services.open_advisor_strategy_rubrics import (
    build_open_advisor_prompt,
    check_direction_gates,
    check_expected_side,
    precheck_open_advisor,
    resolve_strategy_profile,
    should_skip_llm_for_tactical_open,
)

_gpt_advisor_singleton: Optional["GPTPositionAdvisor"] = None


def get_gpt_open_advisor() -> "GPTPositionAdvisor":
    return get_gpt_advisor()


def get_gpt_advisor() -> "GPTPositionAdvisor":
    global _gpt_advisor_singleton
    if _gpt_advisor_singleton is None:
        from app.utils.config_loader import get_db_config
        _gpt_advisor_singleton = GPTPositionAdvisor(get_db_config())
    return _gpt_advisor_singleton


class GPTPositionAdvisor:
    """GPT 顾问 — 仅 gpt_* 模拟仓开仓审核."""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self._prompt_helper = GeminiPositionAdvisor(db_config)

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _read_setting_bool(self, key: str, default: str = "1") -> bool:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
                (key,),
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            val = (row or {}).get("setting_value", default)
            return str(val).strip().lower() in ("1", "true", "yes", "on")
        except Exception:
            return default in ("1", "true", "yes", "on")

    def _is_open_advisor_enabled(self) -> bool:
        return self._read_setting_bool("gpt_open_advisor_enabled", "1")

    def _read_direction_gates(self) -> Tuple[bool, bool]:
        allow_long = self._read_setting_bool("allow_long", "1")
        allow_short = self._read_setting_bool("allow_short", "1")
        return allow_long, allow_short

    def _call_gpt_json(self, prompt: str) -> Optional[dict]:
        if not GPT_API_KEY:
            logger.warning("[GPT顾问] OPENAI_API_KEY 未配置,跳过")
            return None
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("[GPT顾问] 缺 openai 库")
            return None
        system_msg = OPEN_ADVISOR_JSON_SYSTEM_ZH
        try:
            client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
            params = {
                "model": GPT_MODEL,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                "timeout": GPT_TIMEOUT_S,
                "response_format": {"type": "json_object"},
            }
            if not GPT_MODEL.startswith("gpt-5"):
                params["temperature"] = 0.2
            params.update(_completion_token_param(1024))
            resp = client.chat.completions.create(**params)
            text = (resp.choices[0].message.content or "").strip()
            from app.services.ai_explore_prompt import _extract_llm_json_text, _try_parse_json
            parsed, _ = _try_parse_json(_extract_llm_json_text(text))
            if parsed is None:
                logger.warning(f"[GPT顾问] 返回非 JSON: {text[:200]}")
                return None
            decision = str(parsed.get("decision", "")).strip().lower()
            if decision not in ("approve", "reject"):
                decision = "approve"
            return {
                "decision": decision,
                "reason": str(parsed.get("reason", ""))[:500],
                "_raw_response": text,
                "_system_prompt": system_msg,
            }
        except Exception as e:
            logger.warning(f"[GPT顾问] API 异常: {e}")
            return None

    @staticmethod
    def _build_gpt_open_prompt(
        symbol: str,
        side: str,
        price: float,
        source: str,
        catalyst: str,
        leverage: int,
        sl_pct: Optional[float],
        tp_pct: Optional[float],
        hold_hours: Optional[float],
        ctx: dict,
    ) -> str:
        profile = resolve_strategy_profile(source)
        return build_open_advisor_prompt(
            profile=profile,
            symbol=symbol,
            side=side,
            price=price,
            source=source,
            catalyst=catalyst,
            leverage=leverage,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            hold_hours=hold_hours,
            ctx=ctx,
            format_kline_table=GeminiPositionAdvisor._format_kline_table,
        )

    def review_open(
        self,
        symbol: str,
        side: str,
        price: float,
        source: str,
        catalyst: str = "",
        leverage: int = 5,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
        hold_hours: Optional[float] = None,
        conn=None,
    ) -> Tuple[bool, str]:
        if not is_gpt_order_source(source):
            return True, "non_gpt_source_skip"
        if not self._is_open_advisor_enabled():
            return True, "GPT 开仓顾问已关闭"

        profile = resolve_strategy_profile(source)
        allow_long, allow_short = self._read_direction_gates()
        ok, gate_reason = check_direction_gates(side, allow_long, allow_short)
        if not ok:
            log_gpt_advisor_review(
                "open", "reject", symbol, position_side=side, source=source,
                entry_price=price, leverage=leverage, reason=gate_reason, catalyst=catalyst, conn=conn,
            )
            return False, gate_reason
        ok_side, side_reason = check_expected_side(profile, side)
        if not ok_side:
            log_gpt_advisor_review(
                "open", "reject", symbol, position_side=side, source=source,
                entry_price=price, leverage=leverage, reason=side_reason, catalyst=catalyst, conn=conn,
            )
            return False, side_reason

        ctx = self._prompt_helper._fetch_market_context(symbol)
        ok_pre, pre_reason = precheck_open_advisor(profile, side, ctx)
        if not ok_pre:
            log_gpt_advisor_review(
                "open", "reject", symbol, position_side=side, source=source,
                entry_price=price, leverage=leverage, reason=pre_reason, catalyst=catalyst, conn=conn,
            )
            return False, pre_reason

        if should_skip_llm_for_tactical_open(
            profile,
            source,
            tactical_llm_enabled=self._read_setting_bool(
                "tactical_open_advisor_llm_enabled", "1"
            ),
            explore_predict_llm_enabled=self._read_setting_bool(
                "explore_predict_open_advisor_llm_enabled", "0"
            ),
        ):
            log_gpt_advisor_review(
                "open", "approve", symbol, position_side=side, source=source,
                entry_price=price, leverage=leverage,
                reason="上游已通过 catalyst 门槛，跳过 LLM 复审",
                catalyst=catalyst, conn=conn,
            )
            return True, "上游已通过 catalyst 门槛，跳过 LLM 复审"

        prompt = self._build_gpt_open_prompt(
            symbol, side, price, source, catalyst, leverage, sl_pct, tp_pct, hold_hours, ctx,
        )
        result = self._call_gpt_json(prompt)
        input_payload = {
            "symbol": symbol,
            "side": side,
            "price": price,
            "source": source,
            "catalyst": catalyst,
            "leverage": leverage,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "hold_hours": hold_hours,
            "market_context": ctx,
        }
        if not result:
            log_gpt_advisor_review(
                "open", "approve", symbol, position_side=side, source=source,
                entry_price=price, leverage=leverage, reason="GPT API 异常，默认放行",
                catalyst=catalyst, conn=conn, prompt_text=prompt,
                input_json=input_payload, system_prompt=OPEN_ADVISOR_JSON_SYSTEM_ZH,
            )
            return True, "GPT API 异常，默认放行"

        approved = str(result.get("decision", "approve")).lower() == "approve"
        reason = str(result.get("reason", ""))[:500]
        log_gpt_advisor_review(
            "open", "approve" if approved else "reject", symbol,
            position_side=side, source=source, entry_price=price, leverage=leverage,
            reason=reason, catalyst=catalyst, conn=conn,
            prompt_text=prompt, input_json=input_payload,
            raw_response=result.get("_raw_response"),
            system_prompt=result.get("_system_prompt"),
        )
        return approved, reason
