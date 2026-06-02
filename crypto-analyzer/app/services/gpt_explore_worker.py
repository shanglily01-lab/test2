"""GPT 探索 worker (与 Gemini/DeepSeek 主探索一致的执行链路)."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.ai_big4_prompt import big4_conflict_risk_note
from app.services.ai_explore_prompt import (
    AI_POSITION_HOLD_HOURS,
    AI_POSITION_SL_PCT,
    AI_POSITION_TP_PCT,
    EXPLORE_CONFIDENCE_THRESHOLD,
    EXPLORE_MIN_INTERVAL_HOURS,
    build_explore_prompt,
    explore_catalyst_technical_ok,
    parse_explore_llm_json,
)
from app.services.gemini_explore_worker import _get_current_price, _would_instant_tp
from app.services.gemini_swan_worker import _read_setting

GPT_API_KEY = os.getenv("OPENAI_API_KEY", "")
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4o-mini")
GPT_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
GPT_TIMEOUT_S = int(os.getenv("GPT_TIMEOUT_S", "180"))

GPT_SOURCE = "gpt_explore"
EXPLORE_MARGIN_USD = 500.0
EXPLORE_LEVERAGE = 5
EXPLORE_HOLD_HOURS = AI_POSITION_HOLD_HOURS
EXPLORE_SL_PCT = AI_POSITION_SL_PCT
EXPLORE_TP_PCT = AI_POSITION_TP_PCT
EXPLORE_ACCOUNT_ID = 2

_explore_running_lock = threading.Lock()


def _get_local_db_config() -> dict:
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    return pymysql.connect(
        **_get_local_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _get_big4_signal(conn) -> str:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT overall_signal FROM big4_trend_history ORDER BY created_at DESC LIMIT 1")
            row = cur.fetchone()
            return (row or {}).get("overall_signal", "NEUTRAL") or "NEUTRAL"
    except Exception as e:
        logger.warning(f"[GPT探索] 读 Big4 失败 (保守视为 NEUTRAL): {e}")
        return "NEUTRAL"


def _big4_blocks(big4_signal: str, side: str) -> bool:
    if side == "LONG":
        return big4_signal in ("BEARISH", "STRONG_BEARISH")
    if side == "SHORT":
        return big4_signal in ("BULLISH", "STRONG_BULLISH")
    return False


def _has_open_position(conn, symbol: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM futures_positions WHERE source=%s AND status='open' AND symbol=%s LIMIT 1",
            (GPT_SOURCE, symbol),
        )
        return cur.fetchone() is not None


def _call_gpt_explore(universe: dict, global_ctx: dict, historical_stats: dict):
    if not GPT_API_KEY:
        return None, "OPENAI_API_KEY 未设置"
    prompt, _ = build_explore_prompt(universe, global_ctx, historical_stats)
    try:
        from openai import OpenAI
        client = OpenAI(api_key=GPT_API_KEY, base_url=GPT_BASE_URL)
        resp = client.chat.completions.create(
            model=GPT_MODEL,
            messages=[
                {"role": "system", "content": "You are a professional crypto trading analyst. Output ONLY valid JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=2200,
            timeout=GPT_TIMEOUT_S,
            response_format={"type": "json_object"},
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return None, f"GPT API 调用失败: {e}"

    parsed, parse_err = parse_explore_llm_json(text, "GPT探索")
    if parsed is None:
        return None, f"JSON解析失败: {parse_err}"
    parsed["_prompt"] = prompt
    parsed["_raw_response"] = text
    return parsed, None


def _insert_run(
    conn,
    asof_utc: datetime,
    universe_size: int,
    summary_zh: str,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
    triggered_by: str,
    prompt_text: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gpt_explore_runs
              (asof_utc, model, universe_size, summary_zh,
               trades_opened, elapsed_s, status, error_msg, triggered_by,
               prompt_text, raw_response)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s)
            """,
            (asof_utc, GPT_MODEL, universe_size, summary_zh, elapsed_s, status, error_msg, triggered_by, prompt_text, raw_response),
        )
        return cur.lastrowid


def _update_run_trades_opened(conn, run_id: int, trades_opened: int) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE gpt_explore_runs SET trades_opened=%s WHERE id=%s", (trades_opened, run_id))


def _insert_verdicts(conn, verdict_rows: List[Tuple]) -> None:
    if not verdict_rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO gpt_explore_verdicts
              (run_id, symbol, category, confidence,
               catalyst, data_signal, risk_note,
               action_taken, position_id, skip_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            verdict_rows,
        )


def _open_simulated_position(conn, symbol: str, side: str, price: float, catalyst: str) -> Optional[int]:
    from app.services.paper_open_gate import gate_simulated_open
    allowed, _ = gate_simulated_open(
        symbol, side, price, GPT_SOURCE, catalyst,
        leverage=EXPLORE_LEVERAGE, sl_pct=EXPLORE_SL_PCT, tp_pct=EXPLORE_TP_PCT,
        hold_hours=EXPLORE_HOLD_HOURS, conn=conn,
    )
    if not allowed:
        return None

    notional = EXPLORE_MARGIN_USD * EXPLORE_LEVERAGE
    qty = round(notional / price, 6)
    if qty <= 0:
        return None
    if side == "LONG":
        sl_price = round(price * (1 - EXPLORE_SL_PCT / 100), 8)
        tp_price = round(price * (1 + EXPLORE_TP_PCT / 100), 8)
    else:
        sl_price = round(price * (1 + EXPLORE_SL_PCT / 100), 8)
        tp_price = round(price * (1 - EXPLORE_TP_PCT / 100), 8)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO futures_positions
              (account_id, symbol, position_side, leverage, quantity, notional_value,
               margin, entry_price, mark_price, stop_loss_price, take_profit_price,
               stop_loss_pct, take_profit_pct, max_hold_minutes, planned_close_time,
               status, source, entry_signal_type, entry_reason, open_time, unrealized_pnl, unrealized_pnl_pct)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s,%s,%s,0,0)
            """,
            (
                EXPLORE_ACCOUNT_ID, symbol, side, EXPLORE_LEVERAGE, qty, round(notional, 2),
                EXPLORE_MARGIN_USD, price, price, sl_price, tp_price, EXPLORE_SL_PCT, EXPLORE_TP_PCT,
                EXPLORE_HOLD_HOURS * 60, datetime.now() + timedelta(hours=EXPLORE_HOLD_HOURS),
                GPT_SOURCE, "gpt_explore", (catalyst or "gpt_explore")[:180], datetime.now(),
            ),
        )
        return cur.lastrowid


def run_explore_round(triggered_by: str = "scheduler") -> Optional[int]:
    if not _explore_running_lock.acquire(blocking=False):
        logger.warning(f"[GPT探索] 上一轮还未结束, 跳过 (triggered_by={triggered_by})")
        return None
    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    conn = None
    try:
        conn = _connect()
        with conn.cursor() as cur:
            enabled_raw = _read_setting(cur, "gpt_explore_enabled", "0").strip().lower()
        if enabled_raw not in ("1", "true", "yes", "on"):
            return None
        if triggered_by != "manual":
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(asof_utc) AS last_run FROM gpt_explore_runs WHERE status='ok'")
                row = cur.fetchone()
                if row and row.get("last_run"):
                    elapsed_h = (asof_utc - row["last_run"]).total_seconds() / 3600
                    if elapsed_h < EXPLORE_MIN_INTERVAL_HOURS:
                        return None

        from app.services.explore_prepared_bundle import get_explore_prepared_bundle
        universe, global_ctx, _ = get_explore_prepared_bundle(
            conn, "GPT探索", allow_rebuild=triggered_by in ("manual", "scheduler_init", "test"),
        )
        if not universe:
            return _insert_run(conn, asof_utc, 0, "", time.time() - t0, "skipped", "候选池为空", triggered_by)

        historical_stats = {"total_trades": 0, "win_rate": None, "total_pnl": 0}
        resp, call_err = _call_gpt_explore(universe, global_ctx, historical_stats)
        if resp is None:
            return _insert_run(conn, asof_utc, len(universe), "", time.time() - t0, "error", (call_err or "")[:500], triggered_by)

        run_id = _insert_run(
            conn, asof_utc, len(universe), (resp.get("summary_zh") or "")[:1000],
            time.time() - t0, "ok", None, triggered_by, resp.get("_prompt"), resp.get("_raw_response"),
        )
        big4 = _get_big4_signal(conn)
        with conn.cursor() as cur:
            allow_long = _read_setting(cur, "allow_long", "1").strip().lower() in ("1", "true", "yes", "on")
            allow_short = _read_setting(cur, "allow_short", "1").strip().lower() in ("1", "true", "yes", "on")

        trades_opened = 0
        verdict_rows: List[Tuple] = []
        for v in (resp.get("verdicts") or []):
            symbol = (v.get("symbol") or "").upper()
            category = (v.get("category") or "skip").lower()
            confidence = float(v.get("confidence") or 0)
            catalyst = (v.get("catalyst") or "")[:500]
            data_signal = (v.get("data_signal") or "")[:500]
            risk_note = (v.get("risk_note") or "")[:500]
            if not symbol:
                continue
            if category == "bullish" and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = "LONG"
            elif category == "bearish" and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = "SHORT"
            else:
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_confidence", None, f"category={category}"))
                continue
            tech_ok, tech_reason = explore_catalyst_technical_ok(catalyst, data_signal, universe.get(symbol))
            if not tech_ok:
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_weak_catalyst", None, tech_reason))
                continue
            if (side == "LONG" and not allow_long) or (side == "SHORT" and not allow_short):
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_direction_lock", None, "系统方向锁"))
                continue
            if _big4_blocks(big4, side):
                risk_note = (risk_note + " | " + big4_conflict_risk_note(big4, side))[:500]
            if _has_open_position(conn, symbol):
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_dedup", None, "已有OPEN仓位"))
                continue
            price = _get_current_price(conn, symbol, universe.get(symbol))
            if not price or price <= 0:
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_other", None, "无有效开仓价"))
                continue
            tp_check = round(price * (1 + EXPLORE_TP_PCT / 100), 8) if side == "LONG" else round(price * (1 - EXPLORE_TP_PCT / 100), 8)
            instant, ref_px = _would_instant_tp(conn, symbol, side, tp_check)
            if instant:
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_other", None, f"市价{ref_px:.6g}已越过TP"))
                continue
            position_id = _open_simulated_position(conn, symbol, side, price, catalyst)
            if not position_id:
                verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "skipped_other", None, "开仓失败"))
                continue
            trades_opened += 1
            verdict_rows.append((run_id, symbol, category, confidence, catalyst, data_signal, risk_note, "opened", position_id, None))

        _insert_verdicts(conn, verdict_rows)
        _update_run_trades_opened(conn, run_id, trades_opened)
        return run_id
    except Exception as e:
        logger.error(f"[GPT探索] 一轮异常: {e}", exc_info=True)
        if conn:
            try:
                _insert_run(conn, asof_utc, 0, "", time.time() - t0, "error", str(e)[:480], triggered_by)
            except Exception:
                pass
        return None
    finally:
        try:
            if conn:
                conn.close()
        finally:
            _explore_running_lock.release()
