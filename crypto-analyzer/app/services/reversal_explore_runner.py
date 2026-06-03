"""顶空底多探索 — Gemini / DeepSeek 共用执行器 (仅模拟仓，不同步实盘)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pymysql
from loguru import logger

from app.services.ai_reversal_explore_prompt import (
    REVERSAL_HOLD_HOURS,
    REVERSAL_SL_PCT,
    REVERSAL_TP_PCT,
    reversal_catalyst_technical_ok,
    reversal_category_to_side,
)

# 顶空底多仍用 reversal 专用映射
from app.services.gemini_explore_worker import (
    EXPLORE_ACCOUNT_ID,
    EXPLORE_HOLD_HOURS,
    EXPLORE_LEVERAGE,
    EXPLORE_MARGIN_USD,
    EXPLORE_SL_PCT,
    EXPLORE_TP_PCT,
    _big4_blocks,
    _connect,
    _get_big4_signal,
    _get_open_price,
    _read_setting,
    _would_instant_tp,
)
from app.services.explore_prepared_bundle import get_explore_prepared_bundle
from app.services.ai_big4_prompt import big4_conflict_risk_note
from app.services.ai_tactical_explore_schedule import (
    tactical_claim_next_slot,
    tactical_next_due_key,
    tactical_round_is_due,
)
from app.services.ai_tactical_explore_prompts import (
    GPT_TACTICAL_MAX_ENTRIES,
    GPT_TACTICAL_MIN_ENTRIES,
    build_tactical_fallback_entries,
    parse_tactical_confidence,
    supplement_empty_tactical_verdicts,
    TACTICAL_STRATEGIES,
)
from app.services.ai_explore_prompt import normalize_explore_llm_payload

# 各 teacher 独立锁
_locks: Dict[str, threading.Lock] = {}

_SKIP_CATEGORIES = frozenset({"skip", "none", "hold", "wait", "neutral", "跳过"})


def _is_llm_skip_category(category: str) -> bool:
    return (category or "skip").lower().strip() in _SKIP_CATEGORIES


def _universe_lookup(universe: dict, symbol: str) -> Optional[dict]:
    """LLM 常输出 MANTAUSDT，universe 键为 MANTA/USDT."""
    if not symbol or not universe:
        return None
    s = str(symbol).upper().strip()
    if s in universe:
        return universe[s]
    if "/" not in s and len(s) > 4 and s.endswith("USDT"):
        slash = f"{s[:-4]}/USDT"
        if slash in universe:
            return universe[slash]
    compact = s.replace("/", "")
    for key, item in universe.items():
        if str(key).upper().replace("/", "") == compact:
            return item
    return None


def _gpt_apply_verdict_fallback(
    cfg: ReversalExploreConfig,
    universe: dict,
    verdicts: List[dict],
    *,
    catalyst_ok: Callable[..., Tuple[bool, str]],
    category_to_side: Callable[[str, float], Optional[str]],
) -> Tuple[List[dict], bool, str]:
    """GPT：过滤 LLM entry，不足时用预筛 TOP 补到至少 MIN 条（最多 MAX）."""
    if not cfg.source.startswith("gpt_"):
        return verdicts, False, ""

    sk = cfg.source.replace("gpt_", "", 1)

    def _entry_passes(v: dict) -> bool:
        if not isinstance(v, dict):
            return False
        sym = str(v.get("symbol") or "").upper().replace("/", "")
        category = (v.get("category") or "skip").lower()
        confidence = parse_tactical_confidence(v)
        side = category_to_side(category, confidence)
        if not side:
            return False
        item = _universe_lookup(universe, sym)
        ok, _reason = catalyst_ok(
            category,
            (v.get("catalyst") or "")[:500],
            (v.get("data_signal") or "")[:255],
            item,
            confidence,
        )
        return ok

    raw_n = len(verdicts) if isinstance(verdicts, list) else 0
    good = [v for v in verdicts if isinstance(v, dict) and _entry_passes(v)]
    if raw_n > len(good):
        logger.warning(
            f"[{cfg.log_tag}] GPT {raw_n - len(good)} 条 LLM verdict catalyst 未过"
        )

    seen = {
        str(v.get("symbol") or "").upper().replace("/", "")
        for v in good
    }
    note = ""
    if not good and raw_n > 0:
        note = "GPT entry 均未过 catalyst，"

    fb: List[dict] = []
    if sk == "reversal":
        from app.services.ai_reversal_explore_prompt import (
            build_reversal_fallback_entries,
        )
        fb = build_reversal_fallback_entries(
            universe,
            max_entries=GPT_TACTICAL_MAX_ENTRIES,
            exclude_symbols=seen,
        )
    else:
        defn = TACTICAL_STRATEGIES.get(sk)
        if defn is not None:
            fb = build_tactical_fallback_entries(
                defn,
                universe,
                max_entries=GPT_TACTICAL_MAX_ENTRIES,
                exclude_symbols=seen,
            )

    merged = list(good)
    for v in fb:
        sym = str(v.get("symbol") or "").upper().replace("/", "")
        if sym and sym not in seen:
            merged.append(v)
            seen.add(sym)
        if len(merged) >= GPT_TACTICAL_MAX_ENTRIES:
            break

    if len(merged) < GPT_TACTICAL_MIN_ENTRIES:
        for v in fb:
            sym = str(v.get("symbol") or "").upper().replace("/", "")
            if sym and sym not in seen:
                merged.append(v)
                seen.add(sym)
            if len(merged) >= GPT_TACTICAL_MIN_ENTRIES:
                break

    filled = len(merged) > len(good)
    if filled:
        logger.warning(
            f"[{cfg.log_tag}] {note}eligible_fallback 补 {len(merged) - len(good)} 个"
            f"（合计 {len(merged)} 条 entry）"
        )
    return merged, filled, note


def _get_lock(key: str) -> threading.Lock:
    if key not in _locks:
        _locks[key] = threading.Lock()
    return _locks[key]


@dataclass(frozen=True)
class ReversalExploreConfig:
    log_tag: str
    source: str
    runs_table: str
    verdicts_table: str
    model_name: str
    min_interval_hours: float = 2.0
    strategy_label: str = "顶空底多"
    sl_pct: float = EXPLORE_SL_PCT
    tp_pct: float = EXPLORE_TP_PCT
    hold_hours: float = EXPLORE_HOLD_HOURS


# 类型别名，战术策略共用同一 runner
TacticalExploreConfig = ReversalExploreConfig


def _get_historical_stats(conn, source: str) -> dict:
    stats: dict = {
        "total_trades": 0,
        "win_trades": 0,
        "win_rate": None,
        "total_pnl": 0,
        "avg_pnl": None,
        "long_win_rate": None,
        "short_win_rate": None,
        "long_count": 0,
        "short_count": 0,
    }
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) AS cnt,
                       SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                       COALESCE(SUM(realized_pnl), 0) AS total_pnl
                FROM futures_positions
                WHERE source=%s AND account_id=%s AND status='closed'
                  AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)
                """,
                (source, EXPLORE_ACCOUNT_ID),
            )
            row = cur.fetchone() or {}
            total = int(row.get("cnt") or 0)
            wins = int(row.get("wins") or 0)
            stats["total_trades"] = total
            stats["win_trades"] = wins
            stats["total_pnl"] = round(float(row.get("total_pnl") or 0), 2)
            if total > 0:
                stats["win_rate"] = round(wins / total * 100, 1)
                stats["avg_pnl"] = round(stats["total_pnl"] / total, 2)
    except Exception as e:
        logger.warning(f"[{source}] 历史统计失败: {e}")
    return stats


def _has_open_position(conn, source: str, symbol: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM futures_positions "
            "WHERE source=%s AND status='open' AND symbol=%s LIMIT 1",
            (source, symbol),
        )
        return cur.fetchone() is not None


def _open_simulated_position(
    conn,
    cfg: ReversalExploreConfig,
    symbol: str,
    side: str,
    price: float,
    catalyst: str,
) -> Tuple[Optional[int], str]:
    from app.services.trading_gates import is_symbol_blocked_level3

    if is_symbol_blocked_level3(symbol):
        logger.warning(f"[{cfg.log_tag}] {symbol} 黑名单3级, 禁止开仓")
        return None, "黑名单3级"

    from app.services.paper_open_gate import gate_simulated_open
    allowed, gate_reason = gate_simulated_open(
        symbol, side, price, cfg.source, catalyst,
        leverage=EXPLORE_LEVERAGE,
        sl_pct=cfg.sl_pct, tp_pct=cfg.tp_pct,
        hold_hours=cfg.hold_hours, conn=conn,
    )
    if not allowed:
        return None, (gate_reason or "开仓顾问拒绝")[:255]

    try:
        notional = EXPLORE_MARGIN_USD * EXPLORE_LEVERAGE
        qty = round(notional / price, 6)
        if qty <= 0:
            return None, "数量计算为0"

        if side == "LONG":
            sl_price = round(price * (1 - cfg.sl_pct / 100), 8)
            tp_price = round(price * (1 + cfg.tp_pct / 100), 8)
        else:
            sl_price = round(price * (1 + cfg.sl_pct / 100), 8)
            tp_price = round(price * (1 - cfg.tp_pct / 100), 8)

        planned_close = datetime.now() + timedelta(hours=cfg.hold_hours)
        max_hold_minutes = int(cfg.hold_hours * 60)
        entry_reason = (catalyst or cfg.source)[:180]
        entry_reason += (
            f" | {cfg.strategy_label} SL={cfg.sl_pct}% TP={cfg.tp_pct}% "
            f"lev={EXPLORE_LEVERAGE}x hold={cfg.hold_hours}h"
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO futures_positions
                  (account_id, symbol, position_side, leverage, quantity, notional_value,
                   margin, entry_price, mark_price,
                   stop_loss_price, take_profit_price,
                   stop_loss_pct, take_profit_pct,
                   max_hold_minutes, planned_close_time,
                   status, source, entry_signal_type, entry_reason, open_time,
                   unrealized_pnl, unrealized_pnl_pct)
                VALUES (%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,%s,%s,%s,%s,%s,%s,
                        'open', %s, %s, %s, %s, 0, 0)
                """,
                (
                    EXPLORE_ACCOUNT_ID,
                    symbol,
                    side,
                    EXPLORE_LEVERAGE,
                    qty,
                    round(notional, 2),
                    EXPLORE_MARGIN_USD,
                    price,
                    price,
                    sl_price,
                    tp_price,
                    cfg.sl_pct,
                    cfg.tp_pct,
                    max_hold_minutes,
                    planned_close,
                    cfg.source,
                    cfg.source,
                    entry_reason,
                    datetime.now(),
                ),
            )
            pid = cur.lastrowid
        logger.info(
            f"[{cfg.log_tag}] 开仓 {symbol} {side} id={pid} "
            f"(模拟, 不同步实盘)"
        )
        return pid, ""
    except Exception as e:
        logger.error(f"[{cfg.log_tag}] 开仓失败 {symbol}: {e}")
        return None, str(e)[:255]


def _insert_run(
    conn,
    cfg: ReversalExploreConfig,
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
            f"""
            INSERT INTO {cfg.runs_table}
              (asof_utc, model, universe_size, summary_zh,
               trades_opened, elapsed_s, status, error_msg, triggered_by,
               prompt_text, raw_response)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s, %s, %s)
            """,
            (
                asof_utc,
                cfg.model_name,
                universe_size,
                summary_zh,
                elapsed_s,
                status,
                error_msg,
                triggered_by,
                prompt_text,
                raw_response,
            ),
        )
        return cur.lastrowid


def _finish_run(
    conn,
    cfg: ReversalExploreConfig,
    run_id: int,
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
            f"""
            UPDATE {cfg.runs_table} SET
              universe_size=%s, summary_zh=%s, elapsed_s=%s,
              status=%s, error_msg=%s, triggered_by=%s,
              prompt_text=%s, raw_response=%s
            WHERE id=%s
            """,
            (
                universe_size,
                summary_zh,
                elapsed_s,
                status,
                error_msg,
                triggered_by,
                prompt_text,
                raw_response,
                run_id,
            ),
        )
    return run_id


def _update_trades_opened(conn, cfg: ReversalExploreConfig, run_id: int, n: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE {cfg.runs_table} SET trades_opened=%s WHERE id=%s",
            (n, run_id),
        )


_STALE_PARTIAL_SECONDS = 900  # 15min，与「上一轮还未结束」运维阈值一致


def _reset_tactical_next_due_now(conn, source: str) -> None:
    key = tactical_next_due_key(source)
    value = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE system_settings
            SET setting_value = %s, updated_by = 'tactical_stale_recovery', updated_at = NOW()
            WHERE setting_key = %s
            """,
            (value, key),
        )


def _recover_stale_partial_runs(conn, cfg: ReversalExploreConfig) -> int:
    """清理进程中断后遗留的 partial，并允许调度尽快重试."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id FROM `{cfg.runs_table}`
            WHERE status = 'partial'
              AND TIMESTAMPDIFF(SECOND, asof_utc, UTC_TIMESTAMP()) >= %s
            ORDER BY id ASC
            """,
            (_STALE_PARTIAL_SECONDS,),
        )
        rows = cur.fetchall() or []
        if not rows:
            return 0
        ids = [int(r["id"]) for r in rows]
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(
            f"""
            UPDATE `{cfg.runs_table}` SET
              status = 'error',
              elapsed_s = GREATEST(
                  COALESCE(elapsed_s, 0),
                  TIMESTAMPDIFF(SECOND, asof_utc, UTC_TIMESTAMP())
              ),
              error_msg = COALESCE(
                  NULLIF(TRIM(error_msg), ''),
                  '上一轮未完成(进程中断或超时，已自动清理)'
              )
            WHERE id IN ({placeholders}) AND status = 'partial'
            """,
            ids,
        )
        n = cur.rowcount or 0
    if n:
        _reset_tactical_next_due_now(conn, cfg.source)
        logger.warning(
            f"[{cfg.log_tag}] 清理 stale partial x{n} ids={ids[:8]}"
            f"{'...' if len(ids) > 8 else ''}"
        )
    return n


def _insert_verdicts(
    conn,
    cfg: ReversalExploreConfig,
    verdict_rows: List[Tuple],
) -> None:
    if not verdict_rows:
        return
    safe = []
    for row in verdict_rows:
        r = list(row)
        r[2] = (r[2] or "skip")[:20]
        r[7] = (r[7] or "")[:50]
        if r[9] is not None:
            r[9] = str(r[9])[:255]
        safe.append(tuple(r))
    with conn.cursor() as cur:
        cur.executemany(
            f"""
            INSERT INTO {cfg.verdicts_table}
              (run_id, symbol, category, confidence,
               catalyst, data_signal, risk_note,
               action_taken, position_id, skip_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            safe,
        )


def run_tactical_explore_round(
    cfg: TacticalExploreConfig,
    call_llm: Callable[[dict, dict, dict], Tuple[Optional[dict], Optional[str]]],
    triggered_by: str = "scheduler",
    *,
    category_to_side: Callable[[str, float], Optional[str]],
    catalyst_ok: Callable[..., Tuple[bool, str]],
) -> Optional[int]:
    """战术探索一轮：无 kill switch；不做实盘同步."""
    lock = _get_lock(cfg.source)
    if not lock.acquire(blocking=False):
        logger.warning(f"[{cfg.log_tag}] 上一轮未结束, 跳过")
        return None

    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    manual = triggered_by == "manual"

    if not manual:
        try:
            with _connect() as conn_chk:
                due, due_reason = tactical_round_is_due(
                    conn_chk,
                    runs_table=cfg.runs_table,
                    next_due_key=tactical_next_due_key(cfg.source),
                    now=asof_utc,
                    manual=False,
                    log_tag=cfg.log_tag,
                )
                if not due:
                    logger.info(f"[{cfg.log_tag}] {due_reason}, 跳过")
                    lock.release()
                    return None
                tactical_claim_next_slot(
                    conn_chk,
                    next_due_key=tactical_next_due_key(cfg.source),
                    now=asof_utc,
                    log_tag=cfg.log_tag,
                )
                conn_chk.commit()
        except Exception as e:
            logger.warning(f"[{cfg.log_tag}] 调度检查失败, 保守跳过: {e}")
            lock.release()
            return None

    logger.info(f"[{cfg.log_tag}] === 一轮开始 ({triggered_by}) ===")
    run_id = None
    run_closed = False
    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[{cfg.log_tag}] DB 连接失败: {e}")
        lock.release()
        return None

    def _close_run(
        universe_size: int,
        summary_zh: str,
        elapsed_s: float,
        status: str,
        error_msg: Optional[str],
        prompt_text: Optional[str] = None,
        raw_response: Optional[str] = None,
    ) -> None:
        nonlocal run_closed
        if run_id and not run_closed:
            _finish_run(
                conn, cfg, run_id, asof_utc, universe_size, summary_zh, elapsed_s,
                status, error_msg, triggered_by, prompt_text, raw_response,
            )
            run_closed = True

    try:
        _recover_stale_partial_runs(conn, cfg)

        run_id = _insert_run(
            conn, cfg, asof_utc, 0, "", 0.0, "partial", None, triggered_by,
        )

        allow_rebuild = triggered_by in ("manual", "scheduler", "scheduler_init", "test")
        universe, global_ctx, _cache_hit = get_explore_prepared_bundle(
            conn, cfg.log_tag, allow_rebuild=allow_rebuild,
        )
        if len(universe) == 0 and not allow_rebuild:
            logger.warning(
                f"[{cfg.log_tag}] 共用探索包为空/过期, 现场构建 universe (scheduler)"
            )
            universe, global_ctx, _cache_hit = get_explore_prepared_bundle(
                conn, cfg.log_tag, allow_rebuild=True,
            )
        universe_size = len(universe)
        if universe_size == 0:
            _close_run(0, "", time.time() - t0, "skipped", "候选池为空")
            return run_id

        historical_stats = _get_historical_stats(conn, cfg.source)

        llm_response, call_err = call_llm(universe, global_ctx, historical_stats)
        if llm_response is None:
            _close_run(
                universe_size, "", time.time() - t0, "error",
                (call_err or "LLM 失败")[:500],
            )
            return run_id

        llm_response = normalize_explore_llm_payload(llm_response)
        if not isinstance(llm_response, dict):
            _close_run(universe_size, "", time.time() - t0, "error", "LLM JSON 结构无效")
            return run_id

        summary_zh = (llm_response.get("summary_zh") or "")[:1000]
        verdicts = llm_response.get("verdicts") or []
        if not isinstance(verdicts, list):
            verdicts = []
        verdicts, gpt_filled, gpt_note = _gpt_apply_verdict_fallback(
            cfg,
            universe,
            verdicts,
            catalyst_ok=catalyst_ok,
            category_to_side=category_to_side,
        )
        if gpt_filled and not summary_zh.strip():
            summary_zh = (
                f"{gpt_note}代码从预筛 TOP 补 {len(verdicts)} 个"
            )[:1000]
        _close_run(
            universe_size,
            summary_zh,
            time.time() - t0,
            "ok",
            None,
            llm_response.get("_prompt"),
            llm_response.get("_raw_response"),
        )

        big4 = _get_big4_signal(conn)
        with conn.cursor() as cur:
            allow_long_raw = _read_setting(cur, "allow_long", "1").strip().lower()
            allow_short_raw = _read_setting(cur, "allow_short", "1").strip().lower()
        allow_long = allow_long_raw in ("1", "true", "yes", "on")
        allow_short = allow_short_raw in ("1", "true", "yes", "on")

        trades_opened = 0
        verdict_rows: List[Tuple] = []

        for v in verdicts:
            if not isinstance(v, dict):
                continue
            symbol = (v.get("symbol") or "").upper()
            category = (v.get("category") or "skip").lower()
            confidence = parse_tactical_confidence(v)
            catalyst = (v.get("catalyst") or "")[:500]
            data_signal = (v.get("data_signal") or "")[:255]
            risk_note = (v.get("risk_note") or "")[:255]

            if not symbol:
                continue

            side = category_to_side(category, confidence)
            if not side:
                if not _is_llm_skip_category(category):
                    verdict_rows.append((
                        run_id, symbol, category[:20], confidence,
                        catalyst, data_signal, risk_note,
                        "skipped_confidence", None,
                        f"category={category},conf={confidence:.2f}",
                    ))
                continue

            if risk_note == "eligible_fallback":
                tech_ok, tech_reason = True, ""
            else:
                tech_ok, tech_reason = catalyst_ok(
                    category,
                    catalyst,
                    data_signal,
                    _universe_lookup(universe, symbol),
                    confidence,
                )
            if not tech_ok:
                logger.info(f"[{cfg.log_tag}] {symbol} 跳过 catalyst: {tech_reason}")
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_weak_catalyst", None, (tech_reason or "")[:255],
                ))
                continue

            if side == "LONG" and not allow_long:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_direction_lock", None, "系统禁止做多",
                ))
                continue
            if side == "SHORT" and not allow_short:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_direction_lock", None, "系统禁止做空",
                ))
                continue

            if _big4_blocks(big4, side):
                warn = big4_conflict_risk_note(big4, side)
                risk_note = (risk_note + " | " + warn) if risk_note else warn

            if _has_open_position(conn, cfg.source, symbol):
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_dedup", None, "已有OPEN仓位",
                ))
                continue

            from app.services.trading_gates import is_symbol_blocked_level3

            if is_symbol_blocked_level3(symbol):
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_other", None, "黑名单3级",
                ))
                continue

            price = _get_open_price(
                conn, symbol, _universe_lookup(universe, symbol),
            )
            if price is None or price <= 0:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_other", None, "无有效开仓价",
                ))
                continue

            if side == "LONG":
                tp_check = round(price * (1 + cfg.tp_pct / 100), 8)
            else:
                tp_check = round(price * (1 - cfg.tp_pct / 100), 8)
            instant, ref_px = _would_instant_tp(conn, symbol, side, tp_check)
            if instant:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_other", None, f"市价{ref_px:.6g}已越过TP",
                ))
                continue

            position_id, open_fail = _open_simulated_position(
                conn, cfg, symbol, side, price, catalyst,
            )
            if position_id is None:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_other", None, open_fail or "开仓失败",
                ))
                continue

            trades_opened += 1
            verdict_rows.append((
                run_id, symbol, category[:20], confidence,
                catalyst, data_signal, risk_note,
                "opened", position_id, None,
            ))

        _insert_verdicts(conn, cfg, verdict_rows)
        _update_trades_opened(conn, cfg, run_id, trades_opened)

        try:
            from app.services.ai_shadow_explore import run_shadow_after_teacher_explore
            run_shadow_after_teacher_explore(
                teacher_source=cfg.source,
                teacher_run_id=run_id,
                universe=universe,
                global_ctx=global_ctx,
                teacher_verdicts=verdicts,
                conn=conn,
            )
        except Exception as _shadow_err:
            logger.warning(f"[{cfg.log_tag}] Shadow 对比跳过: {_shadow_err}")

        logger.info(
            f"[{cfg.log_tag}] === 结束 run_id={run_id} 开仓={trades_opened} "
            f"耗时={time.time()-t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[{cfg.log_tag}] 异常: {e}", exc_info=True)
        try:
            _close_run(0, "", time.time() - t0, "error", str(e)[:480])
        except Exception:
            pass
        return run_id
    finally:
        if run_id and not run_closed:
            try:
                _close_run(
                    0, "", time.time() - t0, "error",
                    "轮次异常退出未完成",
                )
            except Exception as fin_err:
                logger.error(f"[{cfg.log_tag}] finally 收尾失败 run_id={run_id}: {fin_err}")
        try:
            conn.close()
        except Exception:
            pass
        try:
            lock.release()
        except Exception:
            pass


def run_reversal_explore_round(
    cfg: ReversalExploreConfig,
    call_llm: Callable[[dict, dict, dict], Tuple[Optional[dict], Optional[str]]],
    triggered_by: str = "scheduler",
) -> Optional[int]:
    return run_tactical_explore_round(
        cfg,
        call_llm,
        triggered_by,
        category_to_side=lambda cat, conf: reversal_category_to_side(cat, conf),
        catalyst_ok=lambda cat, catl, sig, sym, conf=0.0: reversal_catalyst_technical_ok(
            cat, catl, sig, sym,
        ),
    )
