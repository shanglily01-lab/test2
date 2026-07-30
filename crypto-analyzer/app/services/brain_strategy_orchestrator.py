"""REQ-BRAIN 战略编排 — L0/L1 轮询分析，发现机会立即下单。"""
from __future__ import annotations

import threading
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

import pymysql
from loguru import logger

from app.services.brain_config import (
    BARS_15M_DAY,
    BARS_15M_WICK_7D,
    BARS_1H_WEEK,
    BRAIN_ACCOUNT_ID,
    BRAIN_CLOSE_CHECK_EVERY_TICKS,
    BRAIN_CLOSE_MIN_HOLD_MINUTES,
    BRAIN_CLOSE_ONLY_ON_FLIP,
    BRAIN_ENABLED_KEY,
    BRAIN_HOLD_HOURS,
    BRAIN_LEVERAGE,
    BRAIN_LIMIT_TIMEOUT_MINUTES,
    BRAIN_MARGIN_USD,
    BRAIN_POOL_REFRESH_EVERY_TICKS,
    BRAIN_SL_PCT,
    BRAIN_SOURCE,
    BRAIN_STRATEGIC_CLOSE_ENABLED,
    BRAIN_SYMBOL_OPEN_COOLDOWN_MINUTES,
    BRAIN_TICK_BATCH_SIZE,
    BRAIN_TICK_INTERVAL_SECONDS,
    BRAIN_TICK_MAX_OPENS,
    BRAIN_TP_PCT,
    BRAIN_USE_MARKET_ENTRY,
    FLAT_PLAYBOOKS,
    TRADEABLE_PLAYBOOKS,
)
from app.services.brain_market_analyzer import evaluate_big4_gate, _fetch_klines
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

# 进程内轮询状态（前端直播）
_live: Dict[str, Any] = {
    "mode": "round_robin",
    "batch_size": BRAIN_TICK_BATCH_SIZE,
    "interval_seconds": BRAIN_TICK_INTERVAL_SECONDS,
    "pool": [],
    "pool_size": 0,
    "cursor": 0,
    "laps": 0,
    "tick_count": 0,
    "analyzing": False,
    "enabled": True,
    "big4": {},
    "last_tick_at": None,
    "last_batch": [],
    "last_error": None,
    "stats": {"opportunities": 0, "opened": 0, "skipped": 0, "closed": 0},
    "open_cooldown_until": {},  # symbol -> unix ts
}
_winrate_cache: Optional[Dict[str, Any]] = None
_winrate_cache_ts: float = 0.0


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


def get_brain_live_status() -> Dict[str, Any]:
    """前端轮询用快照（不含完整 pool 列表以减小 payload）。"""
    snap = dict(_live)
    pool = snap.get("pool") or []
    cur = int(snap.get("cursor") or 0)
    size = len(pool)
    snap["pool"] = None
    snap["pool_size"] = size
    snap["cursor"] = cur
    snap["progress_pct"] = round(100.0 * cur / size, 1) if size else 0.0
    snap["next_symbols"] = pool[cur: cur + BRAIN_TICK_BATCH_SIZE] if size else []
    if size and len(snap["next_symbols"]) < BRAIN_TICK_BATCH_SIZE:
        snap["next_symbols"] = snap["next_symbols"] + pool[: BRAIN_TICK_BATCH_SIZE - len(snap["next_symbols"])]
    # cooldown map may grow; only expose count
    cd = snap.pop("open_cooldown_until", {}) or {}
    snap["cooldown_symbols"] = len(cd)
    return snap


def _refresh_pool(conn) -> List[str]:
    from app.services.trading_gates import load_l0_l1_scan_symbols
    symbols = sorted(load_l0_l1_scan_symbols(conn))
    _live["pool"] = symbols
    _live["pool_size"] = len(symbols)
    if _live["cursor"] >= len(symbols):
        _live["cursor"] = 0
    return symbols


def _get_winrate(conn, symbols: List[str]) -> Dict[str, Any]:
    global _winrate_cache, _winrate_cache_ts
    now = time.time()
    if _winrate_cache and now - _winrate_cache_ts < 30 * 60:
        return _winrate_cache
    _winrate_cache = compute_pool_winrate(conn, symbols, use_cache=True)
    _winrate_cache_ts = now
    return _winrate_cache


def _in_open_cooldown(symbol: str) -> bool:
    clean = futures_symbol_rating_canonical(symbol).replace("/", "")
    until = (_live.get("open_cooldown_until") or {}).get(clean)
    return bool(until and time.time() < float(until))


def _mark_open_cooldown(symbol: str) -> None:
    clean = futures_symbol_rating_canonical(symbol).replace("/", "")
    cd = _live.setdefault("open_cooldown_until", {})
    cd[clean] = time.time() + BRAIN_SYMBOL_OPEN_COOLDOWN_MINUTES * 60


def _build_catalyst(playbook_row: Dict[str, Any], win_long: Optional[float], win_short: Optional[float]) -> str:
    return (
        f"[BRAIN] playbook={playbook_row.get('playbook')} side={playbook_row.get('side')} "
        f"edge={playbook_row.get('edge_score')} confirmed={playbook_row.get('confirmed')} "
        f"win_l={win_long} win_s={win_short} "
        f"signals={playbook_row.get('signals')} | "
        f"{playbook_row.get('evidence_summary') or ''}"
    )[:900]


def _open_brain_entry(
    conn,
    *,
    symbol: str,
    side: str,
    price: float,
    playbook_row: Dict[str, Any],
    win_long: Optional[float],
    win_short: Optional[float],
) -> tuple:
    """开仓：测试期市价（BRAIN_USE_MARKET_ENTRY）；否则限价。返回 (order_or_position_id, err)。"""
    from app.services.brain_config import BRAIN_USE_MARKET_ENTRY
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
    if playbook_row.get("forbid_market") and wick and not BRAIN_USE_MARKET_ENTRY:
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
        "entry_mode": "market" if BRAIN_USE_MARKET_ENTRY else "limit",
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
        mode = "market" if BRAIN_USE_MARKET_ENTRY else "limit"
        return None, (fail[0] if fail else f"{mode}_order_failed")[:200]
    return order_id, None


def _hold_minutes(pos: Dict[str, Any]) -> float:
    raw = pos.get("open_time") or pos.get("created_at")
    if not raw:
        return 9999.0  # 无开仓时间则不做最短持仓保护
    try:
        if hasattr(raw, "timestamp"):
            open_ts = raw.timestamp()
        else:
            from datetime import datetime
            open_ts = datetime.strptime(str(raw)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
        return max(0.0, (time.time() - open_ts) / 60.0)
    except Exception:
        return 9999.0


def close_brain_positions_on_flip(conn, big4: Dict[str, Any]) -> Dict[str, int]:
    """战略平仓：Playbook 再识别（与开仓同口径）。

    禁止再用旧 analyze_symbol 的「未对齐→FLAT」秒平（会开完几分钟就被 analysis_flat 砍掉）。
    过渡规则（§7.3.16 落地前）：
    - 开仓未满 BRAIN_CLOSE_MIN_HOLD_MINUTES：不战略平（硬 SL/TP 仍由 monitor）
    - 默认仅在 Playbook 方向明确反转时平（BRAIN_CLOSE_ONLY_ON_FLIP）
    - Big4 疲软：仅在过最短持仓后平
    """
    stats = {"checked": 0, "closed": 0, "skipped_grace": 0, "errors": 0}
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, symbol, position_side, entry_price, source, leverage,
                   open_time, created_at
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
        held = _hold_minutes(pos)
        try:
            if held < float(BRAIN_CLOSE_MIN_HOLD_MINUTES):
                stats["skipped_grace"] += 1
                continue

            with conn.cursor() as cur:
                rows_1h = _fetch_klines(cur, sym, "1h", BARS_1H_WEEK)
                rows_15m = _fetch_klines(cur, sym, "15m", max(BARS_15M_DAY, BARS_15M_WICK_7D))
            pb = classify_playbook(rows_1h, rows_15m, big4=big4)
            new_side = (pb.get("side") or "FLAT").upper()
            playbook = str(pb.get("playbook") or "D1")

            should_close = False
            reason = ""
            if not big4.get("big4_ok"):
                should_close = True
                reason = "brain_close:big4_weak"
            elif new_side in ("LONG", "SHORT") and new_side != side:
                should_close = True
                reason = f"brain_close:flip_to_{new_side}_{playbook}"
            elif not BRAIN_CLOSE_ONLY_ON_FLIP and (
                playbook in FLAT_PLAYBOOKS or new_side == "FLAT"
            ):
                # 可选：D1/D2 失效平；默认关闭，避免噪音 FLAT 闷杀
                should_close = True
                reason = f"brain_close:playbook_{playbook}"

            if not should_close:
                continue
            logger.info(
                f"[BRAIN平仓] id={pos['id']} {sym} {side} hold={held:.0f}m "
                f"pb={playbook}/{new_side} → {reason}"
            )
            closed = helper._close_live_position(pos, reason[:80], advisor_tag="brain_close")
            if closed:
                stats["closed"] += 1
            else:
                stats["errors"] += 1
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"[BRAIN平仓] id={pos.get('id')} 异常: {e}")
    return stats


def _analyze_one(
    conn,
    cur,
    sym: str,
    *,
    big4: Dict[str, Any],
    winrate: Dict[str, Any],
    scan_round_id: Optional[int],
    allow_open: bool,
) -> Dict[str, Any]:
    """分析单币：落库；过门则立即开仓。"""
    rows_1h = _fetch_klines(cur, sym, "1h", BARS_1H_WEEK)
    rows_15m = _fetch_klines(cur, sym, "15m", max(BARS_15M_DAY, BARS_15M_WICK_7D))
    dirs = resolve_directional_win_probs(winrate, sym)
    wl, ws = dirs.get("win_prob_long"), dirs.get("win_prob_short")
    pb = classify_playbook(
        rows_1h, rows_15m, big4=big4, win_prob_long=wl, win_prob_short=ws,
    )
    if pb.get("forbid_market") and pb.get("wick") and pb.get("side") in ("LONG", "SHORT"):
        pb["limit_offset_pct"] = limit_offset_pct_from_wicks(pb["side"], pb.get("wick") or {})

    side = (pb.get("side") or "FLAT").upper()
    playbook = pb.get("playbook") or "D1"
    price = pb.get("ref_price")
    decision = "SKIPPED"
    skip_reason = None
    order_id = None
    big4_ok = bool(big4.get("big4_ok"))

    if not big4_ok:
        skip_reason = "big4_weak"
    elif playbook not in TRADEABLE_PLAYBOOKS or side not in ("LONG", "SHORT"):
        skip_reason = f"playbook_{playbook}"
    elif not price or float(price) <= 0:
        skip_reason = "no_price"
    elif _in_open_cooldown(sym):
        skip_reason = "symbol_cooldown"
    else:
        ok_wp, wp_reason = directional_open_allowed(side, wl, ws)
        if not ok_wp:
            skip_reason = wp_reason
        elif float(pb.get("edge_score") or 0) < 0.5:
            skip_reason = "low_edge"
        elif not pb.get("confirmed") and str(playbook).startswith("A"):
            skip_reason = "unconfirmed"
        elif not allow_open:
            skip_reason = "tick_open_quota"
        else:
            # 发现机会 → 立即下单（测试期市价；见 BRAIN_USE_MARKET_ENTRY）
            order_id, gate_reason = _open_brain_entry(
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
                _mark_open_cooldown(sym)
                logger.info(
                    f"[BRAIN即时开仓] {sym} {side} {playbook} id={order_id}"
                )
            else:
                skip_reason = gate_reason or "ds_or_gate_reject"
                _mark_open_cooldown(sym)  # 失败也冷却，避免连打 DS

    try:
        insert_opportunity(
            conn,
            scan_round_id=scan_round_id,
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

    return {
        "symbol": futures_symbol_rating_canonical(sym),
        "playbook": playbook,
        "side": side,
        "decision": decision,
        "skip_reason": skip_reason,
        "order_id": order_id,
        "edge_score": pb.get("edge_score"),
        "signals": (pb.get("signals") or [])[:8],
        "win_prob_long": wl,
        "win_prob_short": ws,
        "evidence_summary": (pb.get("evidence_summary") or "")[:120],
    }


def run_brain_tick(triggered_by: str = "scheduler") -> Dict[str, Any]:
    """
    轮询一批（默认 5）L0/L1：分析落库；过门则立即开仓。
    scheduler 每 15 秒调用一次。
    """
    global _running
    summary: Dict[str, Any] = {
        "triggered_by": triggered_by,
        "mode": "tick",
        "opened": 0,
        "skipped": 0,
        "opportunities": 0,
        "closed": 0,
        "batch": [],
        "error": None,
    }
    if not _lock.acquire(blocking=False):
        summary["error"] = "busy"
        return summary
    _running = True
    _live["analyzing"] = True
    conn = None
    round_id: Optional[int] = None
    try:
        conn = _connect()
        with conn.cursor() as cur:
            enabled = _setting_enabled(cur, BRAIN_ENABLED_KEY, "1")
            _live["enabled"] = enabled
            if not enabled:
                summary["error"] = "disabled"
                _live["last_error"] = "disabled"
                return summary
            big4 = evaluate_big4_gate(cur)
        _live["big4"] = {
            "ok": bool(big4.get("big4_ok")),
            "bias": big4.get("bias"),
            "reason": big4.get("reason"),
        }

        tick_n = int(_live.get("tick_count") or 0) + 1
        _live["tick_count"] = tick_n

        if tick_n == 1 or tick_n % BRAIN_POOL_REFRESH_EVERY_TICKS == 0 or not _live.get("pool"):
            _refresh_pool(conn)
        pool: List[str] = list(_live.get("pool") or [])
        if not pool:
            summary["error"] = "empty_l0_l1"
            _live["last_error"] = "empty_l0_l1"
            return summary

        if BRAIN_STRATEGIC_CLOSE_ENABLED and tick_n % BRAIN_CLOSE_CHECK_EVERY_TICKS == 1:
            close_stats = close_brain_positions_on_flip(conn, big4)
            summary["closed"] = int(close_stats.get("closed") or 0)
            _live["stats"]["closed"] = int(_live["stats"].get("closed") or 0) + summary["closed"]

        winrate = _get_winrate(conn, pool)
        cursor = int(_live.get("cursor") or 0)
        batch_size = BRAIN_TICK_BATCH_SIZE
        batch_syms: List[str] = []
        for i in range(batch_size):
            batch_syms.append(pool[(cursor + i) % len(pool)])
        new_cursor = (cursor + batch_size) % len(pool)
        wrapped = new_cursor <= cursor and len(pool) > batch_size
        if wrapped or (cursor + batch_size >= len(pool)):
            # 完成一圈
            if cursor + batch_size >= len(pool):
                _live["laps"] = int(_live.get("laps") or 0) + 1

        round_id = start_scan_round(
            conn,
            triggered_by=f"tick:{triggered_by}",
            universe_size=len(pool),
            big4_ok=bool(big4.get("big4_ok")),
            big4_bias=str(big4.get("bias") or "FLAT"),
        )

        opens_this_tick = 0
        batch_results: List[Dict[str, Any]] = []
        with conn.cursor() as cur:
            for sym in batch_syms:
                allow_open = opens_this_tick < BRAIN_TICK_MAX_OPENS
                row = _analyze_one(
                    conn, cur, sym,
                    big4=big4, winrate=winrate,
                    scan_round_id=round_id,
                    allow_open=allow_open,
                )
                batch_results.append(row)
                summary["opportunities"] += 1
                if row.get("decision") == "OPENED":
                    summary["opened"] += 1
                    opens_this_tick += 1
                else:
                    summary["skipped"] += 1

        _live["cursor"] = new_cursor
        _live["last_batch"] = batch_results
        _live["last_tick_at"] = utc_now_naive().isoformat(sep=" ", timespec="seconds")
        _live["last_error"] = None
        st = _live["stats"]
        st["opportunities"] = int(st.get("opportunities") or 0) + summary["opportunities"]
        st["opened"] = int(st.get("opened") or 0) + summary["opened"]
        st["skipped"] = int(st.get("skipped") or 0) + summary["skipped"]

        summary["batch"] = batch_results
        summary["cursor"] = new_cursor
        summary["pool_size"] = len(pool)
        summary["scan_round_id"] = round_id

        finish_scan_round(
            conn, round_id,
            status="ok",
            opportunities=summary["opportunities"],
            opened=summary["opened"],
            skipped=summary["skipped"],
            closed=summary["closed"],
            summary={"tick": True, "cursor": new_cursor, "batch": [b.get("symbol") for b in batch_results]},
        )
        logger.info(
            f"[BRAIN tick] #{tick_n} cursor={new_cursor}/{len(pool)} "
            f"opened={summary['opened']} skipped={summary['skipped']} "
            f"batch={[b.get('symbol') for b in batch_results]}"
        )
        return summary
    except Exception as e:
        summary["error"] = str(e)[:200]
        _live["last_error"] = summary["error"]
        logger.error(f"[BRAIN tick] 异常: {e}", exc_info=True)
        if conn and round_id:
            try:
                finish_scan_round(
                    conn, round_id, status="error",
                    opportunities=summary.get("opportunities") or 0,
                    opened=summary.get("opened") or 0,
                    skipped=summary.get("skipped") or 0,
                    error_msg=summary["error"],
                )
            except Exception:
                pass
        return summary
    finally:
        _running = False
        _live["analyzing"] = False
        _lock.release()
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def run_brain_round(triggered_by: str = "scheduler") -> Dict[str, Any]:
    """兼容入口：执行一次 tick（启动补跑 / 手动「跑一轮」）。"""
    return run_brain_tick(triggered_by=triggered_by)
