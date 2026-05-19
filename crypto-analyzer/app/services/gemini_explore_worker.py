"""
Gemini 探索 worker

每 6h 调用 Google Gemini 检测加密货币市场的红/黑天鹅, 根据 verdict 直接开模拟单。

策略:
- red_swan  + confidence >= 0.6 -> LONG
- black_swan + confidence >= 0.6 -> SHORT
- skip 或低置信度 -> 不开仓

仓位参数 (跟 S9 一致):
- account_id = 2 (U本位模拟盘)
- margin    = 500U
- leverage  = 5x
- 最多 20 仓 (本策略 source 范围内)
- hold     = 6 小时 (planned_close_time = open_time + 6h)
- SL       = 3%
- TP       = 8%

闸门:
- system_settings.gemini_explore_enabled (默认 0, 关时早返回)
- Big4 趋势: BEARISH/STRONG_BEARISH 禁 LONG, BULLISH/STRONG_BULLISH 禁 SHORT
- 同 symbol+side 已有 OPEN gemini_explore 仓位 -> 跳过

复用 gemini_swan_worker 模块的:
- _fetch_movers_24h / _fetch_extreme_funding / _merge_universe (candidate pool 选取)
- SWAN_PROMPT_TEMPLATE + _call_gemini (Gemini 调用)

不走实盘, 不与其它 Gemini 模块共享决策表。
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.gemini_swan_worker import (
    GEMINI_MODEL,
    TOP_MOVER,
    TOP_FUNDING,
    _call_gemini,
    _fetch_movers_24h,
    _fetch_extreme_funding,
    _merge_universe,
    _read_setting,
)


# ---------------- 常量 ----------------
EXPLORE_MARGIN_USD = 500.0
EXPLORE_LEVERAGE = 5
EXPLORE_MAX_POSITIONS = 20
EXPLORE_HOLD_HOURS = 6
EXPLORE_SL_PCT = 3.0   # 3%
EXPLORE_TP_PCT = 8.0   # 8%
EXPLORE_CONFIDENCE_THRESHOLD = 0.6
EXPLORE_ACCOUNT_ID = 2
EXPLORE_SOURCE = 'gemini_explore'


# ---------------- DB 连接 ----------------
def _get_local_db_config() -> dict:
    """从 config_loader 读 binance-data 本地 DB 配置."""
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    cfg = _get_local_db_config()
    return pymysql.connect(
        **cfg,
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


# ---------------- 候选池采集 ----------------
def _build_universe(conn) -> dict:
    """复用 gemini_swan_worker 的辅助函数, 在本地 binance-data 上跑."""
    with conn.cursor() as cur:
        gainers, losers = _fetch_movers_24h(cur, TOP_MOVER)
        fund_pos, fund_neg = _fetch_extreme_funding(cur, TOP_FUNDING)
    return _merge_universe(gainers, losers, fund_pos, fund_neg)


# ---------------- 闸门检查 ----------------
def _get_big4_signal(conn) -> str:
    """读最新 Big4 overall_signal. 失败返回 NEUTRAL."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT overall_signal FROM big4_trend_history "
                "ORDER BY created_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return (row or {}).get('overall_signal', 'NEUTRAL') or 'NEUTRAL'
    except Exception as e:
        logger.warning(f"[Gemini探索] 读 Big4 失败 (保守视为 NEUTRAL): {e}")
        return 'NEUTRAL'


def _big4_blocks(big4_signal: str, side: str) -> bool:
    """Big4 趋势是否封死该方向. 跟 S1-S9 主策略约定一致."""
    if side == 'LONG':
        return big4_signal in ('BEARISH', 'STRONG_BEARISH')
    if side == 'SHORT':
        return big4_signal in ('BULLISH', 'STRONG_BULLISH')
    return False


def _has_open_position(conn, symbol: str, side: str) -> bool:
    """同 symbol+side 是否已有 gemini_explore 的 OPEN 仓位."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM futures_positions "
            "WHERE source=%s AND status='open' AND symbol=%s AND position_side=%s "
            "LIMIT 1",
            (EXPLORE_SOURCE, symbol, side),
        )
        return cur.fetchone() is not None


def _count_open_positions(conn) -> int:
    """当前 gemini_explore source 下的 OPEN 仓位数."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) AS cnt FROM futures_positions "
            "WHERE source=%s AND status='open' AND account_id=%s",
            (EXPLORE_SOURCE, EXPLORE_ACCOUNT_ID),
        )
        row = cur.fetchone()
        return int((row or {}).get('cnt', 0) or 0)


# ---------------- 价格获取 ----------------
def _get_current_price(conn, symbol: str) -> Optional[float]:
    """从 price_stats_24h 取当前价 (该表每分钟更新). 不新鲜则返回 None."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT current_price, updated_at FROM price_stats_24h "
                "WHERE symbol=%s LIMIT 1",
                (symbol,),
            )
            row = cur.fetchone()
            if not row or row.get('current_price') is None:
                return None
            updated_at = row.get('updated_at')
            if updated_at is None:
                return None
            if isinstance(updated_at, str):
                try:
                    updated_at = datetime.strptime(updated_at, '%Y-%m-%d %H:%M:%S')
                except Exception:
                    return None
            age = (datetime.utcnow() - updated_at).total_seconds()
            if age > 600:  # 超过 10 分钟视为过时
                logger.warning(f"[Gemini探索] {symbol} 价格过时 ({age:.0f}s),跳过")
                return None
            return float(row['current_price'])
    except Exception as e:
        logger.warning(f"[Gemini探索] 取价失败 {symbol}: {e}")
        return None


# ---------------- 开仓 ----------------
def _open_simulated_position(
    conn,
    symbol: str,
    side: str,
    price: float,
    catalyst: str,
) -> Optional[int]:
    """直接 INSERT 到 futures_positions 表, 模拟单, 返回 position_id 或 None."""
    try:
        notional = EXPLORE_MARGIN_USD * EXPLORE_LEVERAGE
        qty = round(notional / price, 6)
        if qty <= 0:
            logger.error(f"[Gemini探索] {symbol} {side} 数量计算非正,跳过")
            return None

        if side == 'LONG':
            sl_price = round(price * (1 - EXPLORE_SL_PCT / 100), 8)
            tp_price = round(price * (1 + EXPLORE_TP_PCT / 100), 8)
        else:  # SHORT
            sl_price = round(price * (1 + EXPLORE_SL_PCT / 100), 8)
            tp_price = round(price * (1 - EXPLORE_TP_PCT / 100), 8)

        planned_close = datetime.utcnow() + timedelta(hours=EXPLORE_HOLD_HOURS)
        max_hold_minutes = EXPLORE_HOLD_HOURS * 60

        # 截断 catalyst 防止超 entry_reason 列长度 (futures_positions.entry_reason 通常 varchar(255))
        entry_reason = (catalyst or 'gemini_explore')[:200]

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO futures_positions
                  (account_id, symbol, position_side, leverage, quantity, notional_value,
                   margin, entry_price, mark_price,
                   stop_loss_price, take_profit_price,
                   stop_loss_pct, take_profit_pct,
                   max_hold_minutes, planned_close_time,
                   status, source, entry_reason, open_time,
                   unrealized_pnl, unrealized_pnl_pct)
                VALUES (%s,%s,%s,%s,%s,%s,
                        %s,%s,%s,
                        %s,%s,
                        %s,%s,
                        %s,%s,
                        'open', %s, %s, NOW(),
                        0, 0)
                """,
                (
                    EXPLORE_ACCOUNT_ID, symbol, side, EXPLORE_LEVERAGE, qty, round(notional, 2),
                    EXPLORE_MARGIN_USD, price, price,
                    sl_price, tp_price,
                    EXPLORE_SL_PCT, EXPLORE_TP_PCT,
                    max_hold_minutes, planned_close,
                    EXPLORE_SOURCE, entry_reason,
                ),
            )
            position_id = cur.lastrowid

        logger.info(
            f"[Gemini探索] 开仓 {symbol} {side} @ {price:.6g} "
            f"SL={sl_price:.6g} TP={tp_price:.6g} qty={qty} "
            f"planned_close={planned_close.strftime('%Y-%m-%d %H:%M')} id={position_id}"
        )
        return position_id
    except Exception as e:
        logger.error(f"[Gemini探索] 开仓失败 {symbol} {side}: {e}")
        return None


# ---------------- 持久化 ----------------
def _insert_run(
    conn,
    asof_utc: datetime,
    universe_size: int,
    summary_zh: str,
    elapsed_s: float,
    status: str,
    error_msg: Optional[str],
    triggered_by: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO gemini_explore_runs
              (asof_utc, model, universe_size, summary_zh,
               trades_opened, elapsed_s, status, error_msg, triggered_by)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s)
            """,
            (asof_utc, GEMINI_MODEL, universe_size, summary_zh,
             elapsed_s, status, error_msg, triggered_by),
        )
        return cur.lastrowid


def _update_run_trades_opened(conn, run_id: int, trades_opened: int) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE gemini_explore_runs SET trades_opened=%s WHERE id=%s",
            (trades_opened, run_id),
        )


def _insert_verdicts(conn, run_id: int, verdict_rows: List[Tuple]) -> None:
    if not verdict_rows:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO gemini_explore_verdicts
              (run_id, symbol, category, confidence,
               catalyst, data_signal, risk_note,
               action_taken, position_id, skip_reason)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            verdict_rows,
        )


# ---------------- 主入口 ----------------
def run_explore_round(triggered_by: str = 'scheduler') -> Optional[int]:
    """跑一轮 Gemini 探索. 成功返回 run_id, 失败/跳过返回 None.

    线程安全: 每次新建连接, 不复用全局连接, 跟 gemini_swan_worker 一致.
    """
    t0 = time.time()
    asof_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. 读 kill switch
    try:
        with _connect() as conn_chk:
            with conn_chk.cursor() as cur:
                enabled_raw = _read_setting(cur, 'gemini_explore_enabled', '0').strip().lower()
    except Exception as e:
        logger.error(f"[Gemini探索] 读 kill switch 失败,保守跳过: {e}")
        return None

    if enabled_raw not in ('1', 'true', 'yes', 'on'):
        logger.info(f"[Gemini探索] kill switch=0, 跳过 (triggered_by={triggered_by})")
        return None

    logger.info(f"[Gemini探索] === 一轮开始 (triggered_by={triggered_by}) ===")

    # 2. 候选池
    try:
        conn = _connect()
    except Exception as e:
        logger.error(f"[Gemini探索] DB 连接失败: {e}")
        return None

    try:
        universe = _build_universe(conn)
        universe_size = len(universe)
        logger.info(f"[Gemini探索] 候选池 universe_size={universe_size}")

        if universe_size == 0:
            elapsed = time.time() - t0
            run_id = _insert_run(
                conn, asof_utc, 0, '', elapsed,
                'skipped', '候选池为空 (price_stats_24h 或 funding_rate_data 无数据?)',
                triggered_by,
            )
            logger.warning("[Gemini探索] 候选池为空, 本轮结束")
            return run_id

        # 3. 调 Gemini (单轮, 不像 swan 那样多轮聚合)
        gemini_response = _call_gemini(universe)
        if gemini_response is None:
            elapsed = time.time() - t0
            run_id = _insert_run(
                conn, asof_utc, universe_size, '', elapsed,
                'error', 'Gemini 调用失败 (网络/API key/解析?)', triggered_by,
            )
            logger.error("[Gemini探索] Gemini 调用失败, 本轮结束")
            return run_id

        summary_zh = (gemini_response.get('summary_zh') or '')[:1000]
        verdicts = gemini_response.get('verdicts') or []
        logger.info(f"[Gemini探索] gemini 返回 verdicts={len(verdicts)}, summary={summary_zh[:80]}")

        # 4. 写 run (先占行, 拿 run_id, 等会儿更新 trades_opened)
        elapsed = time.time() - t0
        run_id = _insert_run(
            conn, asof_utc, universe_size, summary_zh, elapsed,
            'ok', None, triggered_by,
        )

        # 5. 逐 verdict 决策开仓
        big4 = _get_big4_signal(conn)
        logger.info(f"[Gemini探索] Big4 当前信号={big4}")

        trades_opened = 0
        verdict_rows: List[Tuple] = []

        for v in verdicts:
            symbol = (v.get('symbol') or '').upper()
            category = (v.get('category') or 'skip').lower()
            try:
                confidence = float(v.get('confidence') or 0)
            except Exception:
                confidence = 0.0
            catalyst = (v.get('catalyst') or '')[:500]
            data_signal = (v.get('data_signal') or '')[:255]
            risk_note = (v.get('risk_note') or '')[:255]

            if not symbol:
                continue

            # 5a. 类别与置信度
            if category == 'red_swan' and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = 'LONG'
            elif category == 'black_swan' and confidence >= EXPLORE_CONFIDENCE_THRESHOLD:
                side = 'SHORT'
            else:
                verdict_rows.append((
                    run_id, symbol, category if category in ('red_swan', 'black_swan', 'skip') else 'skip',
                    confidence, catalyst, data_signal, risk_note,
                    'skipped_confidence', None,
                    f"category={category} confidence={confidence:.2f}",
                ))
                continue

            # 5b. Big4 闸门
            if _big4_blocks(big4, side):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_big4', None,
                    f"Big4={big4} 禁 {side}",
                ))
                continue

            # 5c. 同 symbol+side 去重
            if _has_open_position(conn, symbol, side):
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_dedup', None,
                    f"{symbol} {side} 已存在 OPEN 仓位",
                ))
                continue

            # 5d. 最大仓位限制
            current_open = _count_open_positions(conn)
            if current_open >= EXPLORE_MAX_POSITIONS:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_max_positions', None,
                    f"当前 OPEN={current_open} >= {EXPLORE_MAX_POSITIONS}",
                ))
                continue

            # 5e. 取价
            price = _get_current_price(conn, symbol)
            if price is None or price <= 0:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    "无最新价格 (price_stats_24h 缺数据或过时)",
                ))
                continue

            # 5f. 开仓
            position_id = _open_simulated_position(conn, symbol, side, price, catalyst)
            if position_id is None:
                verdict_rows.append((
                    run_id, symbol, category, confidence,
                    catalyst, data_signal, risk_note,
                    'skipped_other', None,
                    "开仓 INSERT 失败 (见日志)",
                ))
                continue

            trades_opened += 1
            verdict_rows.append((
                run_id, symbol, category, confidence,
                catalyst, data_signal, risk_note,
                'opened', position_id, None,
            ))

        # 6. 落库 verdicts + 更新 trades_opened
        _insert_verdicts(conn, run_id, verdict_rows)
        _update_run_trades_opened(conn, run_id, trades_opened)

        logger.info(
            f"[Gemini探索] === 一轮结束 run_id={run_id} 开仓={trades_opened} "
            f"跳过={len(verdict_rows) - trades_opened} 耗时={time.time() - t0:.1f}s ==="
        )
        return run_id

    except Exception as e:
        logger.error(f"[Gemini探索] 一轮异常: {e}", exc_info=True)
        try:
            elapsed = time.time() - t0
            _insert_run(
                conn, asof_utc, 0, '', elapsed,
                'error', str(e)[:480], triggered_by,
            )
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == '__main__':
    # 手动跑一轮 (调试用): python -m app.services.gemini_explore_worker
    rid = run_explore_round(triggered_by='manual')
    print(f"run_id={rid}")
