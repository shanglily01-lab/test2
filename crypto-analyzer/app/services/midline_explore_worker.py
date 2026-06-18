"""中线做多/做空 — 调度 worker（Gemini / DeepSeek 各自调用 LLM，跳过顾问）."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config

from app.services.ai_explore_prompt import explore_catalyst_technical_ok
from app.services.ai_midline_explore_prompt import expected_category
from app.services.midline_llm import call_midline_llm
from app.services.midline_swing_config import (
    MIDLINE_ACCOUNT_ID,
    MIDLINE_CONFIDENCE_THRESHOLD,
    MIDLINE_HOLD_MINUTES,
    MIDLINE_INTERVAL_HOURS,
    MIDLINE_KILL_SWITCH,
    MIDLINE_LEVERAGE,
    MIDLINE_LIMIT_OFFSET_PCT,
    MIDLINE_LIMIT_TIMEOUT_MINUTES,
    MIDLINE_MARGIN_USD,
    MIDLINE_SL_PCT,
    MIDLINE_TP_PCT,
    profile_side,
    source_for,
)
from app.services.midline_swing_scanner import build_midline_universe, signal_detail_json
from app.utils.futures_symbol import (
    futures_symbol_rating_canonical,
    resolve_futures_universe_item,
)
from app.utils.position_time import utc_now_naive

_locks: Dict[str, threading.Lock] = {
    "gemini:long": threading.Lock(),
    "gemini:short": threading.Lock(),
    "deepseek:long": threading.Lock(),
    "deepseek:short": threading.Lock(),
}


def _connect():
    cfg = get_db_config()
    return pymysql.connect(
        **cfg,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _read_setting(cur, key: str, default: str = "0") -> str:
    cur.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
        (key,),
    )
    row = cur.fetchone()
    if not row:
        return default
    return str(row.get("setting_value") if isinstance(row, dict) else row[0] or default)


def _is_enabled(cur, source: str) -> bool:
    key = MIDLINE_KILL_SWITCH.get(source)
    if not key:
        return False
    raw = _read_setting(cur, key, "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _last_ok_within_hours(conn, source: str, hours: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT asof_utc FROM midline_swing_runs
            WHERE source=%s AND status='ok'
            ORDER BY id DESC LIMIT 1
            """,
            (source,),
        )
        row = cur.fetchone()
    if not row:
        return False
    asof = row.get("asof_utc") if isinstance(row, dict) else row[0]
    if not asof:
        return False
    if isinstance(asof, str):
        try:
            asof = datetime.fromisoformat(asof.replace("Z", ""))
        except ValueError:
            return False
    now = utc_now_naive()
    if getattr(asof, "tzinfo", None) is not None:
        asof = asof.replace(tzinfo=None)
    return (now - asof).total_seconds() < hours * 3600


def _get_historical_stats(conn, source: str) -> dict:
    stats = {
        "total_trades": 0, "win_trades": 0, "win_rate": None,
        "total_pnl": 0, "avg_pnl": None,
        "source": source,
    }
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(realized_pnl), 0) AS pnl
                FROM futures_positions
                WHERE source=%s AND account_id=%s AND status='closed'
                  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                """,
                (source, MIDLINE_ACCOUNT_ID),
            )
            row = cur.fetchone() or {}
            total = int(row.get("cnt") or 0)
            wins = int(row.get("wins") or 0)
            pnl = float(row.get("pnl") or 0)
            stats["total_trades"] = total
            stats["win_trades"] = wins
            stats["total_pnl"] = round(pnl, 2)
            if total > 0:
                stats["win_rate"] = round(wins / total * 100, 1)
                stats["avg_pnl"] = round(pnl / total, 2)
    except Exception as e:
        logger.warning(f"[中线] 历史统计失败 {source}: {e}")
    return stats


def _has_open_or_pending(conn, symbol: str, side: str, source: str) -> bool:
    symbol = futures_symbol_rating_canonical(symbol)
    order_side = f"OPEN_{side.upper()}"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM futures_positions
            WHERE account_id=%s AND symbol=%s AND position_side=%s
              AND source=%s AND status='open' LIMIT 1
            """,
            (MIDLINE_ACCOUNT_ID, symbol, side.upper(), source),
        )
        if cur.fetchone():
            return True
        cur.execute(
            """
            SELECT 1 FROM futures_orders
            WHERE account_id=%s AND symbol=%s AND side=%s
              AND order_source=%s AND status='PENDING' AND order_type='LIMIT'
            LIMIT 1
            """,
            (MIDLINE_ACCOUNT_ID, symbol, order_side, source),
        )
        return cur.fetchone() is not None


def _has_midline_position_on_symbol(conn, symbol: str, teacher: str) -> bool:
    symbol = futures_symbol_rating_canonical(symbol)
    prefix = f"{teacher}_midline_%"
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM futures_positions
            WHERE account_id=%s AND symbol=%s AND source LIKE %s AND status='open'
            LIMIT 1
            """,
            (MIDLINE_ACCOUNT_ID, symbol, prefix),
        )
        return cur.fetchone() is not None


def _get_open_price(conn, symbol: str, universe: dict) -> Optional[float]:
    try:
        from app.utils.futures_price import get_futures_trade_price
        p = get_futures_trade_price(
            conn, symbol, max_age_seconds=60, log_tag="midline_open", require_fresh=False,
        )
        if p and p > 0:
            return float(p)
    except Exception as e:
        logger.debug(f"[中线] 取价失败 {symbol}: {e}")
    item = resolve_futures_universe_item(universe, symbol)
    if item and item.get("current_price"):
        return float(item["current_price"])
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT close_price FROM kline_data
            WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures'
            ORDER BY open_time DESC LIMIT 1
            """,
            (symbol,),
        )
        row = cur.fetchone()
        if row:
            cp = row.get("close_price") if isinstance(row, dict) else row[0]
            if cp and float(cp) > 0:
                return float(cp)
    return None


def _insert_run(conn, source: str, asof_utc: datetime, status: str, triggered_by: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO midline_swing_runs
              (source, asof_utc, universe_size, signals_found, orders_placed,
               elapsed_s, status, triggered_by)
            VALUES (%s, %s, 0, 0, 0, 0, %s, %s)
            """,
            (source, asof_utc, status, triggered_by),
        )
        return int(cur.lastrowid)


def _finish_run(
    conn,
    run_id: int,
    *,
    universe_size: int,
    signals_found: int,
    orders_placed: int,
    elapsed_s: float,
    status: str,
    summary_zh: str,
    error_msg: Optional[str] = None,
    prompt_text: Optional[str] = None,
    raw_response: Optional[str] = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE midline_swing_runs SET
              universe_size=%s, signals_found=%s, orders_placed=%s,
              elapsed_s=%s, status=%s, summary_zh=%s, error_msg=%s,
              prompt_text=%s, raw_response=%s
            WHERE id=%s
            """,
            (
                universe_size, signals_found, orders_placed,
                elapsed_s, status,
                summary_zh[:500] if summary_zh else None,
                (error_msg or "")[:500] or None,
                prompt_text, raw_response, run_id,
            ),
        )


def _insert_verdict(
    conn,
    run_id: int,
    source: str,
    symbol: str,
    side: str,
    score: float,
    signal_detail: dict,
    action_taken: str,
    order_id: Optional[int],
    skip_reason: Optional[str],
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO midline_swing_verdicts
              (run_id, source, symbol, side, score, signal_detail,
               action_taken, order_id, skip_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                run_id, source, symbol, side, score,
                signal_detail_json(signal_detail),
                action_taken[:32], order_id,
                (skip_reason or "")[:255] or None,
            ),
        )


def _open_limit_order(
    conn,
    symbol: str,
    side: str,
    price: float,
    source: str,
    score: float,
    signal_detail: dict,
) -> Optional[int]:
    from app.services.paper_open_gate import gate_simulated_open
    from app.services.paper_limit_entry import create_paper_limit_order

    allowed, _ = gate_simulated_open(
        symbol, side, price, source,
        catalyst=signal_detail.get("catalyst", f"midline conf={score:.2f}"),
        leverage=MIDLINE_LEVERAGE,
        sl_pct=MIDLINE_SL_PCT, tp_pct=MIDLINE_TP_PCT,
        hold_hours=MIDLINE_HOLD_MINUTES / 60.0,
        conn=conn,
    )
    if not allowed:
        return None

    hold_deadline = utc_now_naive() + timedelta(minutes=MIDLINE_HOLD_MINUTES)
    reason = json.dumps(signal_detail, ensure_ascii=False)[:200]
    return create_paper_limit_order(
        conn,
        symbol=symbol,
        side=side,
        ref_price=price,
        source=source,
        leverage=MIDLINE_LEVERAGE,
        margin=MIDLINE_MARGIN_USD,
        stop_loss_pct=MIDLINE_SL_PCT,
        take_profit_pct=MIDLINE_TP_PCT,
        entry_signal_type=source,
        entry_reason=reason,
        entry_score=score,
        signal_components=signal_detail,
        max_hold_minutes=MIDLINE_HOLD_MINUTES,
        planned_close_time=hold_deadline,
        account_id=MIDLINE_ACCOUNT_ID,
        timeout_minutes=MIDLINE_LIMIT_TIMEOUT_MINUTES,
        limit_offset_pct=MIDLINE_LIMIT_OFFSET_PCT,
        skip_open_advisor=True,
    )


def _process_llm_verdicts(
    conn,
    run_id: int,
    teacher: str,
    profile: str,
    source: str,
    side: str,
    universe: dict,
    verdicts: List[dict],
) -> int:
    """处理 LLM verdicts，返回挂单数。"""
    from app.services.trading_gates import check_max_positions_allowed

    expected_cat = expected_category(profile)
    orders_placed = 0

    for v in verdicts:
        symbol = futures_symbol_rating_canonical((v.get("symbol") or "").strip().upper())
        if not symbol:
            continue
        category = (v.get("category") or "").strip().lower()
        try:
            confidence = float(v.get("confidence") or 0)
        except Exception:
            confidence = 0.0
        catalyst = (v.get("catalyst") or "")[:500]
        data_signal = (v.get("data_signal") or "")[:255]
        risk_note = (v.get("risk_note") or "")[:255]
        score = round(confidence * 100, 1)
        detail = {
            "catalyst": catalyst,
            "data_signal": data_signal,
            "risk_note": risk_note,
            "confidence": confidence,
            "category": category,
            "llm": True,
        }

        if category != expected_cat:
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_direction", None,
                f"category={category} 期望 {expected_cat}",
            )
            continue

        if confidence < MIDLINE_CONFIDENCE_THRESHOLD:
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_confidence", None,
                f"confidence={confidence:.2f}",
            )
            continue

        sym_item = resolve_futures_universe_item(universe, symbol)
        tech_ok, tech_reason = explore_catalyst_technical_ok(
            catalyst, data_signal, sym_item,
        )
        if not tech_ok:
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_weak_catalyst", None, tech_reason,
            )
            continue

        if _has_midline_position_on_symbol(conn, symbol, teacher):
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_dedup", None, "同symbol已有中线持仓",
            )
            continue

        if _has_open_or_pending(conn, symbol, side, source):
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_dedup", None, "已有同向持仓或挂单",
            )
            continue

        mp_ok, mp_reason = check_max_positions_allowed(conn, MIDLINE_ACCOUNT_ID)
        if not mp_ok:
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_max_pos", None, mp_reason,
            )
            continue

        price = _get_open_price(conn, symbol, universe)
        if not price or price <= 0:
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_no_price", None, "无有效市价",
            )
            continue

        order_id = _open_limit_order(conn, symbol, side, price, source, score, detail)
        if order_id:
            orders_placed += 1
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "limit_placed", order_id, None,
            )
        else:
            _insert_verdict(
                conn, run_id, source, symbol, side, score, detail,
                "skipped_order_fail", None, "限价单创建失败",
            )

    return orders_placed


def run_midline_round(
    teacher: str,
    profile: str,
    triggered_by: str = "scheduler",
) -> Optional[int]:
    """
    跑一轮中线策略。teacher: gemini|deepseek；profile: long|short。
    通过对应 LLM 寻找交易对并下限价单（跳过开仓/持仓顾问）。
    """
    teacher = teacher.strip().lower()
    profile = profile.strip().lower()
    source = source_for(teacher, profile)
    side = profile_side(profile)
    lock_key = f"{teacher}:{profile}"
    lock = _locks[lock_key]

    if not lock.acquire(blocking=False):
        logger.warning(f"[中线/{source}] 上一轮还未结束, 跳过")
        return None

    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id: Optional[int] = None
    conn = None

    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _is_enabled(cur, source):
                logger.info(f"[中线/{source}] kill switch=0, 跳过")
                return None

        manual = triggered_by == "manual"
        if not manual and _last_ok_within_hours(conn, source, MIDLINE_INTERVAL_HOURS):
            logger.info(
                f"[中线/{source}] 上次成功距今 < {MIDLINE_INTERVAL_HOURS}h, 跳过"
            )
            return None

        run_id = _insert_run(conn, source, asof_utc, "partial", triggered_by)
        conn.commit()

        universe, meta, universe_size = build_midline_universe(conn, profile)
        if not universe:
            elapsed = time.time() - t0
            _finish_run(
                conn, run_id,
                universe_size=universe_size, signals_found=0, orders_placed=0,
                elapsed_s=elapsed, status="skipped",
                summary_zh="L0/L1 候选池为空或 K 线不足",
            )
            conn.commit()
            logger.warning(f"[中线/{source}] 候选池为空, 本轮结束")
            return run_id

        from app.services.gemini_explore_worker import _build_global_context

        global_ctx = _build_global_context(conn)
        historical_stats = _get_historical_stats(conn, source)
        logger.info(
            f"[中线/{source}] 全局 Big4={global_ctx.get('big4_signal')} "
            f"送模 {meta.get('llm_symbol_count')}/{universe_size}"
        )

        llm_response, call_err = call_midline_llm(
            teacher, profile, universe, global_ctx, historical_stats, meta,
        )
        if llm_response is None:
            elapsed = time.time() - t0
            _finish_run(
                conn, run_id,
                universe_size=universe_size, signals_found=0, orders_placed=0,
                elapsed_s=elapsed, status="error",
                summary_zh="", error_msg=(call_err or "LLM 调用失败")[:500],
            )
            conn.commit()
            logger.error(f"[中线/{source}] LLM 失败: {call_err}")
            return run_id

        verdicts = llm_response.get("verdicts") or []
        summary_zh = (llm_response.get("summary_zh") or "")[:500]
        prompt_text = llm_response.get("_prompt")
        raw_response = llm_response.get("_raw_response")

        orders_placed = _process_llm_verdicts(
            conn, run_id, teacher, profile, source, side, universe, verdicts,
        )

        elapsed = time.time() - t0
        summary = (
            f"{summary_zh or ''} | 池{universe_size} L0/L1 | "
            f"LLM信号{len(verdicts)} | 挂单{orders_placed} | {profile} {side}"
        )[:500]
        _finish_run(
            conn, run_id,
            universe_size=universe_size,
            signals_found=len(verdicts),
            orders_placed=orders_placed,
            elapsed_s=elapsed,
            status="ok",
            summary_zh=summary,
            prompt_text=prompt_text,
            raw_response=raw_response,
        )
        conn.commit()
        logger.info(f"[中线/{source}] === 一轮结束 run_id={run_id} {summary} elapsed={elapsed:.1f}s")
        return run_id
    except Exception as e:
        logger.error(f"[中线/{source}] 异常: {e}", exc_info=True)
        try:
            if conn is None:
                conn = _connect()
            if run_id:
                _finish_run(
                    conn, run_id,
                    universe_size=0, signals_found=0, orders_placed=0,
                    elapsed_s=time.time() - t0, status="error",
                    summary_zh="", error_msg=str(e),
                )
                conn.commit()
        except Exception:
            pass
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        lock.release()


def run_all_midline_scheduled(triggered_by: str = "scheduler") -> None:
    """调度入口：依次尝试 4 个策略槽位。"""
    for teacher in ("gemini", "deepseek"):
        for profile in ("long", "short"):
            try:
                run_midline_round(teacher, profile, triggered_by=triggered_by)
            except Exception as e:
                logger.error(f"[中线] {teacher}/{profile} 调度异常: {e}", exc_info=True)
