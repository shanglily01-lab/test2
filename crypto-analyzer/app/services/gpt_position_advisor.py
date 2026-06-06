"""GPT 模拟仓顾问 — 开仓审核 + gpt_* 持仓监管."""
from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.gpt_config import GPT_API_KEY, GPT_BASE_URL, GPT_MODEL, GPT_TIMEOUT_S
from app.services.gemini_position_advisor import (
    GeminiPositionAdvisor,
    HOLD_15M_BARS,
    HOLD_1H_BARS,
    HOLD_ADVISOR_JSON_SYSTEM_ZH,
    HOLD_CHECK_INTERVAL_S,
    HOLD_MIN_HOURS,
    HOLD_MIN_MINUTES,
    OPEN_ADVISOR_JSON_SYSTEM_ZH,
)
from app.services.gpt_advisor_reviews import log_gpt_advisor_review
from app.services.open_advisor_routing import is_gpt_order_source, should_use_gpt_hold_advisor
from app.services.open_advisor_strategy_rubrics import (
    build_open_advisor_prompt,
    check_direction_gates,
    check_expected_side,
    precheck_open_advisor,
    resolve_strategy_profile,
    should_skip_llm_for_tactical_open,
)

GPT_PER_CALL_DELAY_S = 1.0
GPT_HOLD_ADVISOR_TAG = "gpt_advisor"

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
    """GPT 顾问 — 开仓审核 + gpt_* 模拟仓持仓 hold/observe/sell."""

    def __init__(self, db_config: dict):
        self.db_config = db_config
        self._prompt_helper = GeminiPositionAdvisor(db_config)
        self._last_check_ts: Dict[int, float] = {}

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

    def _is_hold_advisor_enabled(self) -> bool:
        return self._read_setting_bool("gpt_position_advisor_enabled", "1")

    def _read_direction_gates(self) -> Tuple[bool, bool]:
        allow_long = self._read_setting_bool("allow_long", "1")
        allow_short = self._read_setting_bool("allow_short", "1")
        return allow_long, allow_short

    def _call_gpt_json(self, prompt: str, *, hold_mode: bool = False) -> Optional[dict]:
        if not GPT_API_KEY:
            logger.warning("[GPT顾问] OPENAI_API_KEY 未配置,跳过")
            return None
        try:
            from openai import OpenAI
        except ImportError:
            logger.warning("[GPT顾问] 缺 openai 库")
            return None
        system_msg = OPEN_ADVISOR_JSON_SYSTEM_ZH
        if hold_mode:
            system_msg = HOLD_ADVISOR_JSON_SYSTEM_ZH
        try:
            client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
            resp = client.chat.completions.create(
                model=GPT_MODEL,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=1024,
                timeout=GPT_TIMEOUT_S,
                response_format={"type": "json_object"},
            )
            text = (resp.choices[0].message.content or "").strip()
            from app.services.ai_explore_prompt import _extract_llm_json_text, _try_parse_json
            parsed, _ = _try_parse_json(_extract_llm_json_text(text))
            if parsed is None:
                logger.warning(f"[GPT顾问] 返回非 JSON: {text[:200]}")
                return None
            if hold_mode:
                action = str(parsed.get("action", "")).strip().lower()
                if action not in ("hold", "observe", "sell"):
                    action = "observe"
                return {
                    "action": action,
                    "reason": str(parsed.get("reason", ""))[:500],
                    "_raw_response": text,
                    "_system_prompt": system_msg,
                }
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

    def get_eligible_positions(self):
        """gpt_* 模拟仓，持仓 >= 30min."""
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT id, account_id, symbol, position_side, entry_price,
                       quantity, leverage, margin, open_time, source,
                       TIMESTAMPDIFF(MINUTE, open_time, NOW())/60.0 AS hold_hours
                FROM futures_positions
                WHERE status='open'
                  AND account_id = 2
                  AND TIMESTAMPDIFF(MINUTE, open_time, NOW()) >= %s
                  AND LOWER(source) LIKE 'gpt_%%'
                ORDER BY open_time ASC
                """,
                (HOLD_MIN_MINUTES,),
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"[GPT顾问] 查模拟仓失败: {e}")
            return []

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
        result = self._call_gpt_json(prompt, hold_mode=False)
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

    def tick(self) -> dict:
        """gpt_* 模拟仓持仓监管；外部每15min调用一次."""
        stats = {"evaluated": 0, "hold": 0, "observe": 0, "sell": 0, "skipped": 0, "errors": 0, "closed": 0}
        if not self._is_hold_advisor_enabled():
            stats["note"] = "gpt_position_advisor_disabled"
            return stats

        positions = self.get_eligible_positions()
        if not positions:
            return stats

        helper = self._prompt_helper
        now = time.time()
        for pos in positions:
            if not should_use_gpt_hold_advisor(pos.get("source") or ""):
                stats["skipped"] += 1
                continue
            hold_h = float(pos.get("hold_hours") or 0)
            if hold_h < HOLD_MIN_HOURS:
                stats["skipped"] += 1
                continue
            pid = int(pos["id"])
            last = self._last_check_ts.get(pid)
            if last and (now - last) < HOLD_CHECK_INTERVAL_S:
                stats["skipped"] += 1
                continue
            self._last_check_ts[pid] = now
            try:
                current_price = helper._get_current_price(pos["symbol"])
                if not current_price:
                    stats["errors"] += 1
                    continue
                ctx = helper._fetch_market_context(pos["symbol"])
                prompt = helper._build_prompt(pos, current_price, ctx)
                decision = self._call_gpt_json(prompt, hold_mode=True)
                if not decision:
                    stats["errors"] += 1
                    continue
                entry = float(pos["entry_price"])
                pct = ((current_price - entry) / entry * 100) if pos["position_side"] == "LONG" else ((entry - current_price) / entry * 100)
                roi = pct * int(pos["leverage"])
                k15 = helper._recent_klines(ctx.get("klines_15m", []), HOLD_15M_BARS)
                k1h = helper._recent_klines(ctx.get("klines_1h", []), HOLD_1H_BARS)
                s15 = helper._score_klines_for_side(k15, pos["position_side"])
                s1h = helper._score_klines_for_side(k1h, pos["position_side"])
                action, reason = helper._temper_losing_hold(roi, decision["action"], decision["reason"], pos["position_side"], s15, s1h)
                stats[action] += 1
                stats["evaluated"] += 1
                log_gpt_advisor_review(
                    "hold", action, pos["symbol"], position_side=pos.get("position_side"),
                    source=pos.get("source"), position_id=int(pos["id"]), entry_price=float(pos["entry_price"]),
                    leverage=int(pos.get("leverage") or 5), hold_hours=hold_h, roi_pct=round(roi, 2), reason=reason,
                    prompt_text=prompt,
                    input_json={
                        "position": pos,
                        "current_price": current_price,
                        "market_context": ctx,
                        "roi_pct": round(roi, 2),
                        "kline_scores": {"15m": s15, "1h": s1h},
                    },
                    raw_response=decision.get("_raw_response"),
                    system_prompt=decision.get("_system_prompt"),
                )
                if action == "sell":
                    closed = helper._close_live_position(
                        pos, f"{GPT_HOLD_ADVISOR_TAG}:{reason[:50]}", advisor_tag=GPT_HOLD_ADVISOR_TAG,
                    )
                    if closed:
                        stats["closed"] += 1
                    else:
                        stats["errors"] += 1
                        self._last_check_ts.pop(pid, None)
                if GPT_PER_CALL_DELAY_S > 0:
                    time.sleep(GPT_PER_CALL_DELAY_S)
            except Exception as e:
                logger.error(f"[GPT顾问] 处理 id={pos['id']} 异常: {e}")
                stats["errors"] += 1
        return stats
