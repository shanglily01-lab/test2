"""REQ-BRAIN 战略编排 — L0/L1 扫描 → 自有分析 → 胜率 → DeepSeek 确认 → 限价开仓。"""
from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pymysql
from loguru import logger

from app.services.brain_config import (
    BRAIN_ACCOUNT_ID,
    BRAIN_ENABLED_KEY,
    BRAIN_HOLD_HOURS,
    BRAIN_LEVERAGE,
    BRAIN_LIMIT_TIMEOUT_MINUTES,
    BRAIN_MARGIN_USD,
    BRAIN_SL_PCT,
    BRAIN_SOURCE,
    BRAIN_TP_PCT,
    WIN_PROB_MIN,
)
from app.services.brain_market_analyzer import analyze_symbol, evaluate_big4_gate
from app.services.brain_winrate import compute_pool_winrate, resolve_win_prob_for_symbol
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


def _build_catalyst(analysis: Dict[str, Any], win_prob: float) -> str:
    wick = analysis.get("wick") or {}
    return (
        f"[BRAIN] side={analysis.get('side')} win_prob={win_prob:.3f} "
        f"edge={analysis.get('edge_score')} big4_ok={analysis.get('big4_ok')} "
        f"aligned={analysis.get('aligned')} "
        f"h1={analysis.get('h1')} m15={analysis.get('m15')} "
        f"rsi={analysis.get('rsi_1h')} "
        f"wick_ratio={wick.get('wick_ratio')} frequent={wick.get('frequent')} "
        f"forbid_market={analysis.get('forbid_market')} "
        f"limit_offset_pct={analysis.get('limit_offset_pct')} | "
        f"{analysis.get('rationale') or ''}"
    )[:900]


def _open_brain_limit(
    conn,
    *,
    symbol: str,
    side: str,
    price: float,
    analysis: Dict[str, Any],
    win_prob: float,
) -> Optional[int]:
    from app.services.paper_open_gate import gate_simulated_open
    from app.services.paper_limit_entry import create_paper_limit_order
    from app.services.trading_gates import get_paper_margin_usd

    catalyst = _build_catalyst(analysis, win_prob)
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
        return None

    hold_deadline = utc_now_naive() + timedelta(hours=BRAIN_HOLD_HOURS)
    margin = get_paper_margin_usd(symbol, conn) or BRAIN_MARGIN_USD
    detail = {
        "win_prob": win_prob,
        "edge_score": analysis.get("edge_score"),
        "rationale": analysis.get("rationale"),
        "big4_ok": analysis.get("big4_ok"),
        "aligned": analysis.get("aligned"),
        "wick": analysis.get("wick"),
        "h1": analysis.get("h1"),
        "m15": analysis.get("m15"),
        "limit_offset_pct": analysis.get("limit_offset_pct"),
        "forbid_market": analysis.get("forbid_market"),
    }
    fail: List[str] = []
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
        entry_signal_type=BRAIN_SOURCE,
        entry_reason=catalyst[:200],
        entry_score=float(win_prob),
        signal_components=detail,
        max_hold_minutes=int(BRAIN_HOLD_HOURS * 60),
        planned_close_time=hold_deadline,
        account_id=BRAIN_ACCOUNT_ID,
        timeout_minutes=BRAIN_LIMIT_TIMEOUT_MINUTES,
        limit_offset_pct=float(analysis.get("limit_offset_pct") or 0.5),
        skip_open_advisor=True,  # 已在 gate_simulated_open 走 DeepSeek 确认
        failure_reason=fail,
    )
    if not order_id:
        logger.info(f"[BRAIN开仓] 限价失败 {symbol} {side}: {fail[:1]}")
    return order_id


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

    from app.services.gemini_position_advisor import GeminiPositionAdvisor

    helper = GeminiPositionAdvisor()  # 复用取价/平仓工具方法
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
    """跑一轮超级大脑：分析 → 胜率门 → DS 确认限价开仓 → 翻转平仓。"""
    global _running
    summary: Dict[str, Any] = {
        "triggered_by": triggered_by,
        "opened": 0,
        "skipped": 0,
        "candidates": 0,
        "closed": 0,
        "error": None,
    }
    if not _lock.acquire(blocking=False):
        summary["error"] = "busy"
        logger.info("[BRAIN] 上一轮未结束，跳过")
        return summary
    _running = True
    conn = None
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

        if not big4.get("big4_ok"):
            summary["error"] = "big4_weak"
            logger.info(f"[BRAIN] Big4 疲软不开仓: {big4.get('reason')}")
            return summary

        from app.services.trading_gates import load_l0_l1_scan_symbols

        symbols = sorted(load_l0_l1_scan_symbols(conn))
        if not symbols:
            summary["error"] = "empty_l0_l1"
            logger.warning("[BRAIN] L0/L1 池为空")
            return summary

        winrate = compute_pool_winrate(conn, symbols)
        summary["winrate"] = {
            "pool_win_prob": winrate.get("pool_win_prob"),
            "pool_n": winrate.get("pool_n"),
            "pass_gate": winrate.get("pass_gate"),
        }
        # 开仓门用单币（或池回退）win_prob≥55%；池整体不过门时仍允许单币达标者尝试

        # 限制每轮开仓尝试，控制 DeepSeek 调用量
        max_opens = 3
        max_llm = 8
        llm_calls = 0

        with conn.cursor() as cur:
            for sym in symbols:
                if summary["opened"] >= max_opens or llm_calls >= max_llm:
                    break
                analysis = analyze_symbol(cur, sym, big4=big4)
                side = (analysis.get("side") or "FLAT").upper()
                if side not in ("LONG", "SHORT"):
                    continue
                if not analysis.get("aligned") or not analysis.get("big4_ok"):
                    continue

                win_prob = resolve_win_prob_for_symbol(winrate, sym)
                if win_prob is None or win_prob < WIN_PROB_MIN:
                    summary["skipped"] += 1
                    continue

                # 单币胜率不足时已用池胜率；再要求 edge 不太差
                if float(analysis.get("edge_score") or 0) < 0.15:
                    summary["skipped"] += 1
                    continue

                price = analysis.get("ref_price")
                if not price or float(price) <= 0:
                    summary["skipped"] += 1
                    continue

                summary["candidates"] += 1
                llm_calls += 1
                order_id = _open_brain_limit(
                    conn,
                    symbol=sym,
                    side=side,
                    price=float(price),
                    analysis=analysis,
                    win_prob=float(win_prob),
                )
                if order_id:
                    summary["opened"] += 1
                    logger.info(
                        f"[BRAIN开仓] OK {sym} {side} order={order_id} "
                        f"win_prob={win_prob:.3f} offset={analysis.get('limit_offset_pct')}"
                    )
                else:
                    summary["skipped"] += 1

        logger.info(
            f"[BRAIN] 一轮结束 opened={summary['opened']} "
            f"candidates={summary['candidates']} skipped={summary['skipped']} "
            f"closed={summary['closed']} by={triggered_by}"
        )
        return summary
    except Exception as e:
        summary["error"] = str(e)[:200]
        logger.error(f"[BRAIN] 一轮异常: {e}", exc_info=True)
        return summary
    finally:
        _running = False
        _lock.release()
        if conn:
            try:
                conn.close()
            except Exception:
                pass
