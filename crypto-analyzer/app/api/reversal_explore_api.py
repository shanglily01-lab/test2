"""顶空底多探索 HTTP API — Gemini / DeepSeek 共用工厂."""
from __future__ import annotations

import threading
from typing import Callable, Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from loguru import logger


def _get_db_config():
    from app.utils.config_loader import get_db_config
    return get_db_config()


def _connect():
    return pymysql.connect(
        **_get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _get_price_hub():
    try:
        from app.services.binance_data_hub import get_global_data_hub
        return get_global_data_hub()
    except Exception:
        return None


def _live_price(symbol: str, hub) -> Optional[float]:
    if hub is not None:
        try:
            lp = hub.get_price_sync(symbol, max_age_seconds=90)
            if lp is not None and lp > 0:
                return float(lp)
        except Exception:
            pass
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT close_price FROM kline_data "
                    "WHERE symbol=%s AND timeframe='5m' AND exchange='binance_futures' "
                    "ORDER BY open_time DESC LIMIT 1",
                    (symbol,),
                )
                row = cur.fetchone()
                if row and row.get("close_price"):
                    return float(row["close_price"])
        finally:
            conn.close()
    except Exception:
        pass
    return None


def _build_live_positions(positions):
    if not positions:
        return [], {
            "total_unrealized_pnl": 0.0,
            "total_margin_used": 0.0,
            "total_unrealized_pnl_pct": 0.0,
            "open_count": 0,
        }
    hub = _get_price_hub()
    result = []
    for p in positions:
        symbol = p["symbol"]
        side = p["position_side"]
        entry = float(p["entry_price"])
        qty = float(p["quantity"])
        margin = float(p["margin"])
        live_price = _live_price(symbol, hub)
        unrealized_pnl = None
        unrealized_pnl_pct = None
        if live_price is not None and live_price > 0:
            if side == "LONG":
                unrealized_pnl = (live_price - entry) * qty
            else:
                unrealized_pnl = (entry - live_price) * qty
            unrealized_pnl_pct = (unrealized_pnl / margin * 100) if margin > 0 else 0
        result.append({
            "id": p["id"],
            "symbol": symbol,
            "position_side": side,
            "leverage": p["leverage"],
            "quantity": qty,
            "entry_price": entry,
            "mark_price": live_price,
            "margin": margin,
            "stop_loss_price": float(p["stop_loss_price"]) if p.get("stop_loss_price") else None,
            "take_profit_price": float(p["take_profit_price"]) if p.get("take_profit_price") else None,
            "unrealized_pnl": round(unrealized_pnl, 4) if unrealized_pnl is not None else None,
            "unrealized_pnl_pct": round(unrealized_pnl_pct, 2) if unrealized_pnl_pct is not None else None,
            "open_time": p["open_time"].isoformat() if p.get("open_time") else None,
            "planned_close_time": p["planned_close_time"].isoformat() if p.get("planned_close_time") else None,
            "entry_reason": p.get("entry_reason"),
        })
    total_pnl = sum((r["unrealized_pnl"] or 0) for r in result)
    total_margin = sum(r["margin"] for r in result)
    total_pnl_pct = (total_pnl / total_margin * 100) if total_margin > 0 else 0
    return result, {
        "total_unrealized_pnl": round(total_pnl, 2),
        "total_margin_used": round(total_margin, 2),
        "total_unrealized_pnl_pct": round(total_pnl_pct, 2),
        "open_count": len(result),
    }


def create_tactical_explore_router(
    prefix: str,
    tag: str,
    source: str,
    runs_table: str,
    verdicts_table: str,
    run_round_fn: Callable[[str], Optional[int]],
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=[tag])
    _run_lock = threading.Lock()

    open_sql = (
        "SELECT id, symbol, position_side, leverage, quantity, "
        "       entry_price, margin, stop_loss_price, take_profit_price, "
        "       open_time, planned_close_time, entry_reason "
        "FROM futures_positions "
        f"WHERE source='{source}' AND status='open' AND account_id=2 "
        "ORDER BY open_time DESC"
    )

    @router.get("/status")
    async def status():
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, asof_utc, model, universe_size, trades_opened, "
                        f"       elapsed_s, status, error_msg, triggered_by, created_at "
                        f"FROM {runs_table} ORDER BY id DESC LIMIT 1"
                    )
                    last_run = cur.fetchone()
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM futures_positions "
                        f"WHERE source='{source}' AND status='open' AND account_id=2"
                    )
                    open_count = int((cur.fetchone() or {}).get("cnt", 0) or 0)
                    cur.execute(
                        "SELECT COUNT(*) AS cnt FROM futures_positions "
                        f"WHERE source='{source}' AND status='closed' AND account_id=2 "
                        "AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY)"
                    )
                    closed_30d = int((cur.fetchone() or {}).get("cnt", 0) or 0)
            finally:
                conn.close()
            return {
                "success": True,
                "data": {
                    "enabled": True,
                    "no_kill_switch": True,
                    "last_run": last_run,
                    "open_positions": open_count,
                    "closed_positions_30d": closed_30d,
                    "params": {
                        "margin_usd": 500,
                        "leverage": 5,
                        "hold_hours": 4,
                        "sl_pct": 3,
                        "tp_pct": 5,
                        "confidence_threshold": 0.5,
                        "strategy": "top_reversal=SHORT, bottom_reversal=LONG",
                    },
                },
            }
        except Exception as e:
            logger.error(f"[{tag}] /status: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/runs")
    async def list_runs(limit: int = Query(20, ge=1, le=200)):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, asof_utc, model, universe_size, trades_opened, "
                        f"       elapsed_s, status, error_msg, triggered_by, "
                        f"       LEFT(summary_zh, 200) AS summary_short, created_at, "
                        f"       (prompt_text IS NOT NULL AND prompt_text != '') AS has_prompt, "
                        f"       (raw_response IS NOT NULL AND raw_response != '') AS has_raw "
                        f"FROM {runs_table} ORDER BY id DESC LIMIT %s",
                        (limit,),
                    )
                    runs = cur.fetchall()
            finally:
                conn.close()
            return {"success": True, "data": runs, "count": len(runs)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/runs/{run_id}/detail")
    async def get_run_detail(run_id: int):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, prompt_text, raw_response, summary_zh "
                        f"FROM {runs_table} WHERE id=%s",
                        (run_id,),
                    )
                    row = cur.fetchone()
            finally:
                conn.close()
            if not row:
                raise HTTPException(status_code=404, detail="run not found")
            return {"success": True, "data": row}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/verdicts")
    async def list_verdicts(run_id: int = Query(..., ge=1)):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        f"SELECT id, run_id, symbol, category, confidence, "
                        f"       catalyst, data_signal, risk_note, "
                        f"       action_taken, position_id, skip_reason, created_at "
                        f"FROM {verdicts_table} WHERE run_id=%s AND action_taken='opened' "
                        f"ORDER BY confidence DESC, id ASC",
                        (run_id,),
                    )
                    verdicts = cur.fetchall()
            finally:
                conn.close()
            for v in verdicts:
                if v.get("confidence") is not None:
                    v["confidence"] = float(v["confidence"])
            return {"success": True, "data": verdicts, "count": len(verdicts)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/positions")
    async def list_positions(
        status: str = Query("open", pattern="^(open|closed)$"),
        limit: int = Query(50, ge=1, le=500),
    ):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    if status == "open":
                        cur.execute(
                            f"SELECT id, symbol, position_side, leverage, quantity, "
                            f"entry_price, mark_price, stop_loss_price, take_profit_price, "
                            f"margin, unrealized_pnl, open_time, planned_close_time, entry_reason "
                            f"FROM futures_positions "
                            f"WHERE source='{source}' AND status='open' AND account_id=2 "
                            f"ORDER BY open_time DESC LIMIT %s",
                            (limit,),
                        )
                    else:
                        cur.execute(
                            f"SELECT id, symbol, position_side, leverage, quantity, "
                            f"entry_price, mark_price, margin, realized_pnl, "
                            f"open_time, close_time, entry_reason, notes "
                            f"FROM futures_positions "
                            f"WHERE source='{source}' AND status='closed' AND account_id=2 "
                            f"AND close_time >= DATE_SUB(NOW(), INTERVAL 30 DAY) "
                            f"ORDER BY close_time DESC LIMIT %s",
                            (limit,),
                        )
                    rows = cur.fetchall()
            finally:
                conn.close()
            for r in rows:
                for k, v in list(r.items()):
                    if hasattr(v, "isoformat"):
                        r[k] = v.isoformat()
                    elif hasattr(v, "__float__") and not isinstance(v, (int, float)):
                        try:
                            r[k] = float(v)
                        except Exception:
                            pass
            if status == "closed":
                from app.utils.position_display import enrich_closed_position_rows
                enrich_closed_position_rows(rows)
            return {"success": True, "data": rows, "count": len(rows)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/positions/live")
    async def list_positions_live():
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    cur.execute(open_sql)
                    positions = cur.fetchall()
            finally:
                conn.close()
            result, summary = _build_live_positions(positions)
            return {"success": True, "data": result, "count": len(result), "summary": summary}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/stats")
    async def stats(days: int = Query(30, ge=1, le=365)):
        try:
            conn = _connect()
            try:
                with conn.cursor() as cur:
                    from app.utils.pnl_stats import PNL_COUNT_SELECT, parse_pnl_counts

                    cur.execute(
                        f"""
                        SELECT {PNL_COUNT_SELECT},
                               COALESCE(SUM(realized_pnl), 0) AS total_pnl,
                               COALESCE(AVG(realized_pnl), 0) AS avg_pnl
                        FROM futures_positions
                        WHERE source='{source}' AND status='closed' AND account_id=2
                          AND close_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                        """,
                        (days,),
                    )
                    row = cur.fetchone()
                    cur.execute(open_sql)
                    open_positions = cur.fetchall()
            finally:
                conn.close()
            counts = parse_pnl_counts(row)
            total = counts["total_trades"]
            wins = counts["wins"]
            _, live_summary = _build_live_positions(open_positions)
            floating_pnl = float(live_summary["total_unrealized_pnl"])
            total_pnl = float(row["total_pnl"] or 0)
            return {
                "success": True,
                "data": {
                    "total_trades": total,
                    "wins": wins,
                    "losses": counts["losses"],
                    "breakeven": counts["breakeven"],
                    "win_rate": counts["win_rate"],
                    "total_realized_pnl": round(total_pnl, 2),
                    "avg_realized_pnl": round(float(row["avg_pnl"] or 0), 2),
                    "floating_pnl": round(floating_pnl, 2),
                    "total_pnl": round(total_pnl + floating_pnl, 2),
                    "days": days,
                },
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    def _bg(triggered_by: str):
        try:
            run_round_fn(triggered_by)
        except Exception as e:
            logger.error(f"[{tag}] 后台异常: {e}", exc_info=True)
        finally:
            try:
                _run_lock.release()
            except Exception:
                pass

    @router.post("/run-now")
    async def run_now():
        if not _run_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="上一轮还在跑")
        t = threading.Thread(
            target=_bg, args=("manual",), name=f"{source}_manual", daemon=True,
        )
        t.start()
        return {"success": True, "message": f"已在后台启动{tag}一轮"}

    return router


create_reversal_explore_router = create_tactical_explore_router

from app.services.gemini_reversal_explore_worker import run_gemini_reversal_explore_round
from app.services.deepseek_reversal_explore_worker import run_deepseek_reversal_explore_round
from app.services.tactical_explore_workers import (
    run_deepseek_chase_explore_round,
    run_deepseek_dump_explore_round,
    run_deepseek_pullback_explore_round,
    run_deepseek_rebound_explore_round,
    run_gemini_chase_explore_round,
    run_gemini_dump_explore_round,
    run_gemini_pullback_explore_round,
    run_gemini_rebound_explore_round,
)

gemini_reversal_router = create_tactical_explore_router(
    prefix="/api/gemini-reversal-explore",
    tag="Gemini顶空底多",
    source="gemini_reversal",
    runs_table="gemini_reversal_explore_runs",
    verdicts_table="gemini_reversal_explore_verdicts",
    run_round_fn=run_gemini_reversal_explore_round,
)

deepseek_reversal_router = create_tactical_explore_router(
    prefix="/api/deepseek-reversal-explore",
    tag="DeepSeek顶空底多",
    source="deepseek_reversal",
    runs_table="deepseek_reversal_explore_runs",
    verdicts_table="deepseek_reversal_explore_verdicts",
    run_round_fn=run_deepseek_reversal_explore_round,
)

_TACTICAL_API_SPECS = [
    ("gemini", "pullback", "回调做多", run_gemini_pullback_explore_round),
    ("gemini", "rebound", "反弹做空", run_gemini_rebound_explore_round),
    ("gemini", "chase", "追涨做多", run_gemini_chase_explore_round),
    ("gemini", "dump", "杀跌做空", run_gemini_dump_explore_round),
    ("deepseek", "pullback", "回调做多", run_deepseek_pullback_explore_round),
    ("deepseek", "rebound", "反弹做空", run_deepseek_rebound_explore_round),
    ("deepseek", "chase", "追涨做多", run_deepseek_chase_explore_round),
    ("deepseek", "dump", "杀跌做空", run_deepseek_dump_explore_round),
]

tactical_four_routers = []
for teacher, key, title, fn in _TACTICAL_API_SPECS:
    tag_prefix = "Gemini" if teacher == "gemini" else "DeepSeek"
    tactical_four_routers.append(
        create_tactical_explore_router(
            prefix=f"/api/{teacher}-{key}-explore",
            tag=f"{tag_prefix}{title}",
            source=f"{teacher}_{key}",
            runs_table=f"{teacher}_{key}_explore_runs",
            verdicts_table=f"{teacher}_{key}_explore_verdicts",
            run_round_fn=fn,
        )
    )
