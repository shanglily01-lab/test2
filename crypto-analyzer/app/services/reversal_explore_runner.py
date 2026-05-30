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
    _get_current_price,
    _read_setting,
)
from app.services.explore_prepared_bundle import get_explore_prepared_bundle
from app.services.ai_big4_prompt import big4_conflict_risk_note

# 各 teacher 独立锁
_locks: Dict[str, threading.Lock] = {}


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
) -> Optional[int]:
    from app.services.trading_gates import is_symbol_blocked_level3

    if is_symbol_blocked_level3(symbol):
        logger.warning(f"[{cfg.log_tag}] {symbol} 黑名单3级, 禁止开仓")
        return None

    try:
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

        planned_close = datetime.now() + timedelta(hours=EXPLORE_HOLD_HOURS)
        max_hold_minutes = EXPLORE_HOLD_HOURS * 60
        entry_reason = (catalyst or cfg.source)[:180]
        entry_reason += (
            f" | {cfg.strategy_label} SL={EXPLORE_SL_PCT}% TP={EXPLORE_TP_PCT}% "
            f"lev={EXPLORE_LEVERAGE}x hold={EXPLORE_HOLD_HOURS}h"
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
                    EXPLORE_SL_PCT,
                    EXPLORE_TP_PCT,
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
        return pid
    except Exception as e:
        logger.error(f"[{cfg.log_tag}] 开仓失败 {symbol}: {e}")
        return None


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
    catalyst_ok: Callable[[str, str, str, Optional[dict]], Tuple[bool, str]],
) -> Optional[int]:
    """战术探索一轮：无 kill switch；不做实盘同步."""
    lock = _get_lock(cfg.source)
    if not lock.acquire(blocking=False):
        logger.warning(f"[{cfg.log_tag}] 上一轮未结束, 跳过")
        return None

    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    if triggered_by != "manual":
        try:
            with _connect() as conn_chk:
                with conn_chk.cursor() as cur:
                    cur.execute(
                        f"SELECT MAX(asof_utc) AS last_run FROM {cfg.runs_table} "
                        f"WHERE status='ok'"
                    )
                    row = cur.fetchone()
                    if row and row.get("last_run"):
                        elapsed_h = (asof_utc - row["last_run"]).total_seconds() / 3600
                        if elapsed_h < cfg.min_interval_hours:
                            logger.info(
                                f"[{cfg.log_tag}] 距上次成功 {elapsed_h:.1f}h "
                                f"< {cfg.min_interval_hours}h, 跳过"
                            )
                            lock.release()
                            return None
        except Exception as e:
            logger.warning(f"[{cfg.log_tag}] 防重检查失败, 继续: {e}")

    logger.info(f"[{cfg.log_tag}] === 一轮开始 ({triggered_by}) ===")
    run_id = None
    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[{cfg.log_tag}] DB 连接失败: {e}")
        lock.release()
        return None

    try:
        run_id = _insert_run(
            conn, cfg, asof_utc, 0, "", 0.0, "partial", None, triggered_by,
        )

        allow_rebuild = triggered_by in ("manual", "scheduler_init", "test")
        universe, global_ctx, _cache_hit = get_explore_prepared_bundle(
            conn, cfg.log_tag, allow_rebuild=allow_rebuild,
        )
        universe_size = len(universe)
        if universe_size == 0:
            elapsed = time.time() - t0
            _finish_run(
                conn, cfg, run_id, asof_utc, 0, "", elapsed,
                "skipped", "候选池为空", triggered_by,
            )
            return run_id

        historical_stats = _get_historical_stats(conn, cfg.source)

        llm_response, call_err = call_llm(universe, global_ctx, historical_stats)
        if llm_response is None:
            elapsed = time.time() - t0
            _finish_run(
                conn, cfg, run_id, asof_utc, universe_size, "", elapsed,
                "error", (call_err or "LLM 失败")[:500], triggered_by,
            )
            return run_id

        summary_zh = (llm_response.get("summary_zh") or "")[:1000]
        verdicts = llm_response.get("verdicts") or []
        elapsed = time.time() - t0
        _finish_run(
            conn,
            cfg,
            run_id,
            asof_utc,
            universe_size,
            summary_zh,
            elapsed,
            "ok",
            None,
            triggered_by,
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
            symbol = (v.get("symbol") or "").upper()
            category = (v.get("category") or "skip").lower()
            try:
                confidence = float(v.get("confidence") or 0)
            except Exception:
                confidence = 0.0
            catalyst = (v.get("catalyst") or "")[:500]
            data_signal = (v.get("data_signal") or "")[:255]
            risk_note = (v.get("risk_note") or "")[:255]

            if not symbol:
                continue

            side = category_to_side(category, confidence)
            if not side:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_confidence", None,
                    f"category={category} confidence={confidence:.2f}",
                ))
                continue

            tech_ok, tech_reason = catalyst_ok(
                category, catalyst, data_signal, universe.get(symbol),
            )
            if not tech_ok:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_weak_catalyst", None, tech_reason,
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
                    "skipped_dedup", None, f"{symbol} 已有 OPEN",
                ))
                continue

            from app.services.trading_gates import is_symbol_blocked_level3

            if is_symbol_blocked_level3(symbol):
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_blacklist", None, "黑名单3级",
                ))
                continue

            price = _get_current_price(conn, symbol)
            if price is None or price <= 0:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_other", None, "无最新价格",
                ))
                continue

            position_id = _open_simulated_position(
                conn, cfg, symbol, side, price, catalyst,
            )
            if position_id is None:
                verdict_rows.append((
                    run_id, symbol, category[:20], confidence,
                    catalyst, data_signal, risk_note,
                    "skipped_other", None, "开仓失败",
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
        logger.info(
            f"[{cfg.log_tag}] === 结束 run_id={run_id} 开仓={trades_opened} "
            f"耗时={time.time()-t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[{cfg.log_tag}] 异常: {e}", exc_info=True)
        try:
            if run_id:
                _finish_run(
                    conn, cfg, run_id, asof_utc, 0, "", time.time() - t0,
                    "error", str(e)[:480], triggered_by,
                )
        except Exception:
            pass
        return run_id
    finally:
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
        catalyst_ok=lambda cat, catl, sig, sym: reversal_catalyst_technical_ok(
            cat, catl, sig, sym,
        ),
    )
