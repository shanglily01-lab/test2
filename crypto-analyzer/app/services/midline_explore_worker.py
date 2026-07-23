"""中线 v2 worker — midline_long / midline_short 独立量化扫描（非 LLM）."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config

from app.services.midline_swing_config import (
    MIDLINE_ACCOUNT_ID,
    MIDLINE_HOLD_MINUTES,
    MIDLINE_KILL_SWITCH,
    MIDLINE_LEVERAGE,
    MIDLINE_SL_PCT,
    MIDLINE_TP_PCT,
    get_midline_interval_hours,
    get_midline_limit_timeout_minutes,
    is_active_midline_source,
    profile_for_source,
    profile_side,
    source_for,
)
from app.services.midline_swing_scanner import scan_universe, signal_detail_json
from app.utils.futures_symbol import futures_symbol_rating_canonical
from app.utils.position_time import utc_now_naive

_locks: Dict[str, threading.Lock] = {
    "long": threading.Lock(),
    "short": threading.Lock(),
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


def _ensure_kill_switches(conn) -> None:
    """确保 v2 kill switch 行存在（默认 0）."""
    with conn.cursor() as cur:
        for key in MIDLINE_KILL_SWITCH.values():
            cur.execute(
                "INSERT IGNORE INTO system_settings (setting_key, setting_value) "
                "VALUES (%s, '0')",
                (key,),
            )


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


def _has_any_midline_position_on_symbol(conn, symbol: str) -> bool:
    """任一中线 source（含旧四路）已持仓该 symbol 则跳过."""
    symbol = futures_symbol_rating_canonical(symbol)
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT 1 FROM futures_positions
            WHERE account_id=%s AND symbol=%s AND status='open'
              AND (
                source IN ('midline_long', 'midline_short')
                OR source LIKE '%%_midline_%%'
              )
            LIMIT 1
            """,
            (MIDLINE_ACCOUNT_ID, symbol),
        )
        return cur.fetchone() is not None


def _get_open_price(conn, symbol: str) -> Optional[float]:
    try:
        from app.utils.futures_price import get_futures_trade_price
        p = get_futures_trade_price(
            conn, symbol, max_age_seconds=60, log_tag="midline_open", require_fresh=False,
        )
        if p and p > 0:
            return float(p)
    except Exception as e:
        logger.debug(f"[中线] 取价失败 {symbol}: {e}")
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


def _format_run_summary(
    universe_size: int,
    signals_count: int,
    orders_placed: int,
    profile: str,
    side: str,
    rejected: int = 0,
) -> str:
    profile_cn = "做多" if profile == "long" else "做空"
    return (
        f"池{universe_size}个 config | 通过{signals_count}个 | 拒绝{rejected}个 | "
        f"挂单{orders_placed}笔 | {profile_cn} {side}"
    )


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
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE midline_swing_runs SET
              universe_size=%s, signals_found=%s, orders_placed=%s,
              elapsed_s=%s, status=%s, summary_zh=%s, error_msg=%s
            WHERE id=%s
            """,
            (
                universe_size, signals_found, orders_placed,
                elapsed_s, status, summary_zh[:500] if summary_zh else None,
                (error_msg or "")[:500] or None, run_id,
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
    from app.services.trading_gates import get_paper_margin_usd

    allowed, _ = gate_simulated_open(
        symbol, side, price, source,
        catalyst="midline_v2_pass",
        leverage=MIDLINE_LEVERAGE,
        sl_pct=MIDLINE_SL_PCT, tp_pct=MIDLINE_TP_PCT,
        hold_hours=MIDLINE_HOLD_MINUTES / 60.0,
        conn=conn,
    )
    if not allowed:
        return None

    hold_deadline = utc_now_naive() + timedelta(minutes=MIDLINE_HOLD_MINUTES)
    reason = "midline_v2 " + json.dumps(signal_detail, ensure_ascii=False)[:140]
    paper_margin = get_paper_margin_usd(symbol, conn)
    return create_paper_limit_order(
        conn,
        symbol=symbol,
        side=side,
        ref_price=price,
        source=source,
        leverage=MIDLINE_LEVERAGE,
        margin=paper_margin,
        stop_loss_pct=MIDLINE_SL_PCT,
        take_profit_pct=MIDLINE_TP_PCT,
        entry_signal_type=source,
        entry_reason=reason[:200],
        entry_score=score,
        signal_components=signal_detail,
        max_hold_minutes=MIDLINE_HOLD_MINUTES,
        planned_close_time=hold_deadline,
        account_id=MIDLINE_ACCOUNT_ID,
        timeout_minutes=get_midline_limit_timeout_minutes(),
        skip_open_advisor=True,
    )


def run_midline_round(
    teacher: str = "",
    profile: str = "",
    triggered_by: str = "scheduler",
    source: Optional[str] = None,
) -> Optional[int]:
    """
    跑一轮中线策略。
    优先用 source=midline_long|midline_short；或 profile=long|short。
    teacher 参数保留兼容，已忽略。
    """
    if source:
        source = source.strip().lower()
        if not is_active_midline_source(source):
            logger.warning(f"[中线] 非法 source={source}")
            return None
        profile = profile_for_source(source)
    else:
        profile = (profile or "long").strip().lower()
        source = source_for(teacher or "", profile)

    side = profile_side(profile)
    lock = _locks[profile]

    if not lock.acquire(blocking=False):
        logger.warning(f"[中线/{source}] 上一轮还未结束, 跳过")
        return None

    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    run_id: Optional[int] = None

    try:
        conn = _connect()
        try:
            _ensure_kill_switches(conn)
            with conn.cursor() as cur:
                if not _is_enabled(cur, source):
                    logger.info(f"[中线/{source}] kill switch=0, 跳过")
                    return None

            manual = triggered_by == "manual"
            interval_h = get_midline_interval_hours()
            if not manual and _last_ok_within_hours(conn, source, interval_h):
                logger.info(
                    f"[中线/{source}] 上次成功距今 < {interval_h}h, 跳过"
                )
                return None

            run_id = _insert_run(conn, source, asof_utc, "partial", triggered_by)

            # 全量评估（含拒绝）便于机会分析页
            all_rows, universe_size = scan_universe(conn, profile, include_rejects=True)
            signals = [r for r in all_rows if r.get("passed")]
            rejected = len(all_rows) - len(signals)
            orders_placed = 0

            from app.services.trading_gates import check_max_positions_allowed

            # 先落拒绝（限制数量避免爆表：每轮最多记 80 条拒绝 + 全部通过）
            reject_budget = 80
            for row in all_rows:
                if row.get("passed"):
                    continue
                if reject_budget <= 0:
                    break
                reject_budget -= 1
                _insert_verdict(
                    conn, run_id, source, row["symbol"], side, 0.0,
                    row.get("signal_detail") or {},
                    "skipped_signal", None,
                    (row.get("reason") or "layer_fail")[:255],
                )

            for sig in signals:
                symbol = sig["symbol"]
                score = float(sig["score"])
                detail = sig.get("signal_detail") or {}

                if _has_any_midline_position_on_symbol(conn, symbol):
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

                price = _get_open_price(conn, symbol) or float(sig.get("ref_price") or 0)
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
                        "skipped_order_fail", None, "限价单创建失败或闸门拒绝",
                    )

            elapsed = time.time() - t0
            summary = _format_run_summary(
                universe_size, len(signals), orders_placed, profile, side, rejected,
            )
            _finish_run(
                conn, run_id,
                universe_size=universe_size,
                signals_found=len(signals),
                orders_placed=orders_placed,
                elapsed_s=elapsed,
                status="ok",
                summary_zh=summary,
            )
            logger.info(f"[中线/{source}] === 一轮结束 run_id={run_id} {summary} elapsed={elapsed:.1f}s")
            return run_id
        finally:
            conn.close()
    except Exception as e:
        logger.error(f"[中线/{source}] 异常: {e}", exc_info=True)
        try:
            conn = _connect()
            try:
                if run_id:
                    _finish_run(
                        conn, run_id,
                        universe_size=0, signals_found=0, orders_placed=0,
                        elapsed_s=time.time() - t0, status="error",
                        summary_zh="", error_msg=str(e),
                    )
            finally:
                conn.close()
        except Exception:
            pass
        return None
    finally:
        lock.release()


def run_all_midline_scheduled(triggered_by: str = "scheduler") -> None:
    """调度入口：依次尝试 midline_long / midline_short."""
    for profile in ("long", "short"):
        try:
            run_midline_round(profile=profile, triggered_by=triggered_by)
        except Exception as e:
            logger.error(f"[中线] {profile} 调度异常: {e}", exc_info=True)
