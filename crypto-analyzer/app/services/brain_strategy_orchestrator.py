"""REQ-BRAIN 战略编排 — Playbook 识别落库 → 分向胜率 → DeepSeek 确认限价开仓。"""
from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pymysql
from loguru import logger

from app.services.brain_config import (
    BARS_15M_DAY,
    BARS_15M_WICK_7D,
    BARS_1H_WEEK,
    BRAIN_ACCOUNT_ID,
    BRAIN_ENABLED_KEY,
    BRAIN_HOLD_HOURS,
    BRAIN_LEVERAGE,
    BRAIN_LIMIT_TIMEOUT_MINUTES,
    BRAIN_MARGIN_USD,
    BRAIN_SL_PCT,
    BRAIN_SOURCE,
    BRAIN_TP_PCT,
    TRADEABLE_PLAYBOOKS,
)
from app.services.brain_market_analyzer import analyze_symbol, evaluate_big4_gate, _fetch_klines
from app.services.brain_opportunity_store import (
    finish_scan_round,
    insert_opportunity,
    start_scan_round,
)
from app.services.brain_playbook import classify_playbook
from app.services.brain_wick import limit_offset_pct_from_wicks
from app.services.brain_winrate import (
    compute_pool_winrate,
    directional_open_allowed,
    resolve_directional_win_probs,
)
from app.utils.config_loader import get_db_config
from app.utils.futures_symbol import futures_symbol_rating_canonical
from app.utils.position_time import utc_now_naive

_lock = threading.Lock()
_running = False


def _connect():
    cfg = get_db_config()
    return pymysql.connect(
        **cfg, charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor,
    )


def _setting_enabled(cur, key: str, default: str = "1") -> bool:
    cur.execute(
        "SELECT setting_value FROM system_settings WHERE setting_key=%s LIMIT 1",
        (key,),
    )
    row = cur.fetchone()
    if not row:
        return str(default).strip().lower() in ("1", "true", "yes")
    val = row.get("setting_value") if isinstance(row, dict) else row[0]
    return str(val or default).strip().lower() in ("1", "true", "yes")


def _build_catalyst(playbook_row: Dict[str, Any], win_long: Optional[float], win_short: Optional[float]) -> str:
    return (
        f"[BRAIN] playbook={playbook_row.get('playbook')} side={playbook_row.get('side')} "
        f"edge={playbook_row.get('edge_score')} confirmed={playbook_row.get('confirmed')} "
        f"win_l={win_long} win_s={win_short} "
        f"signals={playbook_row.get('signals')} | "
        f"{playbook_row.get('evidence_summary') or ''}"
    )[:900]


def _open_brain_limit(
    conn,
    *,
    symbol: str,
    side: str,
    price: float,
    playbook_row: Dict[str, Any],
    win_long: Optional[float],
    win_short: Optional[float],
) -> tuple:
    """返回 (order_id|None, gate_reason|None)。"""
    from app.services.paper_open_gate import gate_simulated_open
    from app.services.paper_limit_entry import create_paper_limit_order
    from app.services.trading_gates import get_paper_margin_usd

    catalyst = _build_catalyst(playbook_row, win_long, win_short)
    allowed, gate_reason = gate_simulated_open(
        symbol, side, price, BRAIN_SOURCE,
        catalyst=catalyst,
        leverage=BRAIN_LEVERAGE,
        sl_pct=BRAIN_SL_PCT,
        tp_pct=BRAIN_TP_PCT,
        hold_hours=float(BRAIN_HOLD_HOURS),
        account_id=BRAIN_ACCOUNT_ID,
        conn=conn,
    )
    if not allowed:
        logger.info(f"[BRAIN开仓] 闸门拒绝 {symbol} {side}: {gate_reason}")
        return None, str(gate_reason or "gate_reject")[:200]

    hold_deadline = utc_now_naive() + timedelta(hours=BRAIN_HOLD_HOURS)
    margin = get_paper_margin_usd(symbol, conn) or BRAIN_MARGIN_USD
    wick = playbook_row.get("wick") or {}
    offset = float(playbook_row.get("limit_offset_pct") or 0.5)
    if playbook_row.get("forbid_market") and wick:
        offset = limit_offset_pct_from_wicks(side, wick)
    detail = {
        "playbook": playbook_row.get("playbook"),
        "signals": playbook_row.get("signals"),
        "edge_score": playbook_row.get("edge_score"),
        "win_prob_long": win_long,
        "win_prob_short": win_short,
        "evidence_summary": playbook_row.get("evidence_summary"),
        "candidates": playbook_row.get("candidates"),
        "wick": wick,
        "limit_offset_pct": offset,
        "forbid_market": playbook_row.get("forbid_market"),
    }
    fail: List[str] = []
    entry_score = win_long if side == "LONG" else win_short
    order_id = create_paper_limit_order(
        conn,
        symbol=symbol,
        side=side,
        ref_price=price,
        source=BRAIN_SOURCE,
        leverage=BRAIN_LEVERAGE,
        margin=float(margin),
        stop_loss_pct=BRAIN_SL_PCT,
        take_profit_pct=BRAIN_TP_PCT,
        entry_signal_type=f"brain_{playbook_row.get('playbook')}",
        entry_reason=catalyst[:200],
        entry_score=float(entry_score or playbook_row.get("edge_score") or 0),
        signal_components=detail,
        max_hold_minutes=int(BRAIN_HOLD_HOURS * 60),
        planned_close_time=hold_deadline,
        account_id=BRAIN_ACCOUNT_ID,
        timeout_minutes=BRAIN_LIMIT_TIMEOUT_MINUTES,
        limit_offset_pct=offset,
        skip_open_advisor=True,
        failure_reason=fail,
    )
    if not order_id:
        return None, (fail[0] if fail else "limit_order_failed")[:200]
    return order_id, None


def close_brain_positions_on_flip(conn, big4: Dict[str, Any]) -> Dict[str, int]:
    """大脑主张平：持仓方向与当前分析冲突 / Big4 疲软 → 强制平（INV-BRAIN-05）。"""
    stats = {"checked": 0, "closed": 0, "errors": 0}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, position_side, entry_price, source, leverage
            FROM futures_positions
            WHERE status='OPEN' AND account_id=%s
              AND (source=%s OR source LIKE 'brain_%%')
            """,
            (BRAIN_ACCOUNT_ID, BRAIN_SOURCE),
        )
        rows = list(cur.fetchall() or [])

    if not rows:
        return stats

    from app.services.advisor_core import AdvisorPromptHelper

    helper = AdvisorPromptHelper(get_db_config())
    for pos in rows:
        stats["checked"] += 1
        sym = futures_symbol_rating_canonical(pos["symbol"])
        side = (pos.get("position_side") or "").upper()
        try:
            with conn.cursor() as cur:
                analysis = analyze_symbol(cur, sym, big4=big4)
            new_side = (analysis.get("side") or "FLAT").upper()
            should_close = False
            reason = ""
            if not big4.get("big4_ok"):
                should_close = True
                reason = "brain_close:big4_weak"
            elif new_side == "FLAT":
                should_close = True
                reason = "brain_close:analysis_flat"
            elif new_side != side and new_side in ("LONG", "SHORT"):
                should_close = True
                reason = f"brain_close:flip_to_{new_side}"

            if not should_close:
                continue

            logger.info(
                f"[BRAIN平仓] id={pos['id']} {sym} {side} → {reason} "
                f"(new={new_side} ds_disagree_ok)"
            )
            closed = helper._close_live_position(
                pos, reason[:80], advisor_tag="brain_close",
            )
            if closed:
                stats["closed"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"[BRAIN平仓] id={pos.get('id')} 异常: {e}")
    return stats


def run_brain_round(triggered_by: str = "scheduler") -> Dict[str, Any]:
    """
    一轮：全量 Playbook 识别落库 → 分向胜率门 → DS 确认开仓 → 翻转平仓。
    """
    global _running
    summary: Dict[str, Any] = {
        "triggered_by": triggered_by,
        "opened": 0,
        "skipped": 0,
        "candidates": 0,
        "opportunities": 0,
        "closed": 0,
        "scan_round_id": None,
        "error": None,
    }
    if not _lock.acquire(blocking=False):
        summary["error"] = "busy"
        logger.info("[BRAIN] 上一轮未结束，跳过")
        return summary
    _running = True
    conn = None
    round_id: Optional[int] = None
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _setting_enabled(cur, BRAIN_ENABLED_KEY, "1"):
                summary["error"] = "disabled"
                logger.info(f"[BRAIN] kill switch {BRAIN_ENABLED_KEY}=0，跳过")
                return summary
            big4 = evaluate_big4_gate(cur)

        close_stats = close_brain_positions_on_flip(conn, big4)
        summary["closed"] = int(close_stats.get("closed") or 0)

        from app.services.trading_gates import load_l0_l1_scan_symbols

        symbols = sorted(load_l0_l1_scan_symbols(conn))
        if not symbols:
            summary["error"] = "empty_l0_l1"
            logger.warning("[BRAIN] L0/L1 池为空")
            return summary

        round_id = start_scan_round(
            conn,
            triggered_by=triggered_by,
            universe_size=len(symbols),
            big4_ok=bool(big4.get("big4_ok")),
            big4_bias=str(big4.get("bias") or "FLAT"),
        )
        summary["scan_round_id"] = round_id

        winrate = compute_pool_winrate(conn, symbols)
        summary["winrate"] = {
            "pool_win_prob": winrate.get("pool_win_prob"),
            "pool_win_prob_long": winrate.get("pool_win_prob_long"),
            "pool_win_prob_short": winrate.get("pool_win_prob_short"),
            "pool_n": winrate.get("pool_n"),
            "pass_gate": winrate.get("pass_gate"),
        }

        max_opens = 3
        max_llm = 8
        llm_calls = 0
        big4_ok = bool(big4.get("big4_ok"))

        with conn.cursor() as cur:
            for sym in symbols:
                rows_1h = _fetch_klines(cur, sym, "1h", BARS_1H_WEEK)
                rows_15m = _fetch_klines(
                    cur, sym, "15m", max(BARS_15M_DAY, BARS_15M_WICK_7D),
                )
                dirs = resolve_directional_win_probs(winrate, sym)
                wl, ws = dirs.get("win_prob_long"), dirs.get("win_prob_short")

                pb = classify_playbook(
                    rows_1h, rows_15m, big4=big4,
                    win_prob_long=wl, win_prob_short=ws,
                )
                if pb.get("forbid_market") and pb.get("wick") and pb.get("side") in ("LONG", "SHORT"):
                    pb["limit_offset_pct"] = limit_offset_pct_from_wicks(
                        pb["side"], pb.get("wick") or {},
                    )

                side = (pb.get("side") or "FLAT").upper()
                playbook = pb.get("playbook") or "D1"
                price = pb.get("ref_price")
                summary["opportunities"] += 1

                decision = "SKIPPED"
                skip_reason = None
                order_id = None

                if not big4_ok:
                    skip_reason = "big4_weak"
                elif playbook not in TRADEABLE_PLAYBOOKS or side not in ("LONG", "SHORT"):
                    skip_reason = f"playbook_{playbook}"
                elif not price or float(price) <= 0:
                    skip_reason = "no_price"
                else:
                    ok_wp, wp_reason = directional_open_allowed(side, wl, ws)
                    if not ok_wp:
                        skip_reason = wp_reason
                    elif float(pb.get("edge_score") or 0) < 0.5:
                        skip_reason = "low_edge"
                    elif not pb.get("confirmed") and playbook.startswith("A"):
                        # 趋势类要求更高确认；冲击/破位已在 classifier 标 confirmed
                        skip_reason = "unconfirmed"
                    elif summary["opened"] >= max_opens or llm_calls >= max_llm:
                        skip_reason = "round_quota"
                    else:
                        summary["candidates"] += 1
                        llm_calls += 1
                        order_id, gate_reason = _open_brain_limit(
                            conn,
                            symbol=sym,
                            side=side,
                            price=float(price),
                            playbook_row=pb,
                            win_long=wl,
                            win_short=ws,
                        )
                        if order_id:
                            decision = "OPENED"
                            summary["opened"] += 1
                            logger.info(
                                f"[BRAIN开仓] OK {sym} {side} {playbook} order={order_id} "
                                f"win_l={wl} win_s={ws}"
                            )
                        else:
                            skip_reason = gate_reason or "ds_or_gate_reject"

                if decision == "SKIPPED":
                    summary["skipped"] += 1

                try:
                    insert_opportunity(
                        conn,
                        scan_round_id=round_id,
                        symbol=futures_symbol_rating_canonical(sym),
                        side=side,
                        playbook=playbook,
                        signals=list(pb.get("signals") or []),
                        evidence_summary=str(pb.get("evidence_summary") or ""),
                        ref_price=float(price) if price else None,
                        win_prob_long=wl,
                        win_prob_short=ws,
                        edge_score=float(pb.get("edge_score") or 0),
                        decision=decision,
                        skip_reason=skip_reason,
                        order_id=order_id,
                    )
                except Exception as e:
                    logger.error(f"[BRAIN] 落库失败 {sym}: {e}")

        finish_scan_round(
            conn, round_id,
            status="ok" if not summary.get("error") else "error",
            opportunities=summary["opportunities"],
            opened=summary["opened"],
            skipped=summary["skipped"],
            closed=summary["closed"],
            summary=summary,
        )
        logger.info(
            f"[BRAIN] 一轮结束 round={round_id} opened={summary['opened']} "
            f"opps={summary['opportunities']} skipped={summary['skipped']} "
            f"closed={summary['closed']} by={triggered_by}"
        )
        return summary
    except Exception as e:
        summary["error"] = str(e)[:200]
        logger.error(f"[BRAIN] 一轮异常: {e}", exc_info=True)
        if conn and round_id:
            try:
                finish_scan_round(
                    conn, round_id, status="error",
                    opportunities=summary.get("opportunities") or 0,
                    opened=summary.get("opened") or 0,
                    skipped=summary.get("skipped") or 0,
                    closed=summary.get("closed") or 0,
                    error_msg=summary["error"],
                    summary=summary,
                )
            except Exception:
                pass
        return summary
    finally:
        _running = False
        _lock.release()
        if conn:
            try:
                conn.close()
            except Exception:
                pass
