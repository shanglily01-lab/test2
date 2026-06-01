"""顾问审核记录 API — Gemini + DeepSeek 开仓审核."""
from __future__ import annotations

from typing import Any, Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from loguru import logger

router = APIRouter(prefix="/api/advisor", tags=["顾问审核"])

# 兼容旧路径
legacy_router = APIRouter(prefix="/api/gemini-advisor", tags=["Gemini顾问审核(兼容)"])

_REVIEW_COLS = """
  id, review_type, decision, symbol, position_side, source,
  position_id, entry_price, leverage, hold_hours, roi_pct,
  reason, catalyst, created_at
"""


def _connect():
    from app.utils.config_loader import get_db_config
    return pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def _tables_ready(cur) -> tuple[bool, bool]:
    gemini = _table_exists(cur, "gemini_advisor_reviews")
    deepseek = _table_exists(cur, "deepseek_advisor_reviews")
    return gemini, deepseek


def _review_filters(
    review_type: Optional[str],
    decision: Optional[str],
    symbol: Optional[str],
    q: Optional[str],
    provider: Optional[str],
) -> tuple[str, list[Any]]:
    sql = ""
    params: list[Any] = []
    if review_type:
        sql += " AND review_type = %s"
        params.append(review_type)
    if decision:
        sql += " AND decision = %s"
        params.append(decision)
    if symbol:
        sql += " AND symbol = %s"
        params.append(symbol.upper())
    if provider in ("gemini", "deepseek"):
        sql += " AND provider = %s"
        params.append(provider)
    if q:
        q = q.strip()
        if q:
            like = f"%{q}%"
            sql += (
                " AND (symbol LIKE %s OR source LIKE %s OR reason LIKE %s"
                " OR decision LIKE %s OR position_side LIKE %s OR provider LIKE %s)"
            )
            params.extend([like, like, like, like, like, like])
    return sql, params


def _aggregate_summary(cur, table: str, provider: str) -> dict:
    if not _table_exists(cur, table):
        return {}
    cur.execute(
        f"""
        SELECT
          SUM(review_type='open') AS open_total,
          SUM(review_type='open' AND decision='approve') AS open_approve,
          SUM(review_type='open' AND decision='reject') AS open_reject,
          SUM(review_type='hold') AS hold_total,
          SUM(review_type='hold' AND decision='sell') AS hold_sell,
          SUM(review_type='hold' AND decision='hold') AS hold_hold,
          SUM(review_type='hold' AND decision='observe') AS hold_observe
        FROM {table}
        WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
        """
    )
    row = cur.fetchone() or {}
    out = {k: int(row.get(k) or 0) for k in row}
    out["provider"] = provider
    return out


def _build_summary():
    conn = _connect()
    with conn.cursor() as cur:
        gemini_ok, deepseek_ok = _tables_ready(cur)
        if not gemini_ok and not deepseek_ok:
            return {
                "ready": False,
                "message": "请执行 migration 011 / 013",
                "open_total": 0,
                "hold_total": 0,
            }
        g = _aggregate_summary(cur, "gemini_advisor_reviews", "gemini") if gemini_ok else {}
        d = _aggregate_summary(cur, "deepseek_advisor_reviews", "deepseek") if deepseek_ok else {}
    conn.close()
    return {
        "ready": True,
        "gemini_ready": gemini_ok,
        "deepseek_ready": deepseek_ok,
        "open_approve": int(g.get("open_approve", 0)) + int(d.get("open_approve", 0)),
        "open_reject": int(g.get("open_reject", 0)) + int(d.get("open_reject", 0)),
        "open_total": int(g.get("open_total", 0)) + int(d.get("open_total", 0)),
        "hold_sell": int(g.get("hold_sell", 0)),
        "hold_hold": int(g.get("hold_hold", 0)),
        "hold_observe": int(g.get("hold_observe", 0)),
        "hold_total": int(g.get("hold_total", 0)),
    }


def _list_reviews(
    review_type: Optional[str],
    decision: Optional[str],
    symbol: Optional[str],
    q: Optional[str],
    provider: Optional[str],
    limit: int,
    offset: int,
):
    conn = _connect()
    with conn.cursor() as cur:
        gemini_ok, deepseek_ok = _tables_ready(cur)
        if not gemini_ok and not deepseek_ok:
            conn.close()
            return {"reviews": [], "ready": False, "total": 0}

        parts = []
        if gemini_ok and provider in (None, "", "all", "gemini"):
            parts.append(
                f"SELECT 'gemini' AS provider, {_REVIEW_COLS.strip()} "
                f"FROM gemini_advisor_reviews WHERE 1=1"
            )
        if deepseek_ok and provider in (None, "", "all", "deepseek"):
            parts.append(
                f"SELECT 'deepseek' AS provider, {_REVIEW_COLS.strip()} "
                f"FROM deepseek_advisor_reviews WHERE 1=1"
            )
        if not parts:
            conn.close()
            return {"reviews": [], "ready": True, "total": 0}

        union_sql = " UNION ALL ".join(parts)
        filt, params = _review_filters(review_type, decision, symbol, q, provider)

        count_sql = f"SELECT COUNT(*) AS cnt FROM ({union_sql}) AS u WHERE 1=1" + filt
        cur.execute(count_sql, params)
        total = int((cur.fetchone() or {}).get("cnt") or 0)

        list_sql = (
            f"SELECT * FROM ({union_sql}) AS u WHERE 1=1"
            + filt
            + " ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
        )
        qparams = list(params) + [limit, offset]
        cur.execute(list_sql, qparams)
        rows = cur.fetchall() or []
    conn.close()

    for r in rows:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat(sep=" ", timespec="seconds")
        for k in ("entry_price", "hold_hours", "roi_pct"):
            if r.get(k) is not None:
                r[k] = float(r[k])
    return {"ready": True, "total": total, "reviews": rows}


@router.get("/summary")
def get_summary():
    try:
        return _build_summary()
    except Exception as e:
        logger.error(f"[顾问API] summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reviews")
def list_reviews(
    review_type: Optional[str] = Query(None, description="open | hold"),
    decision: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="模糊搜索"),
    provider: Optional[str] = Query(None, description="gemini | deepseek | all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        prov = (provider or "all").lower()
        if prov not in ("all", "gemini", "deepseek"):
            prov = "all"
        return _list_reviews(review_type, decision, symbol, q, prov, limit, offset)
    except Exception as e:
        logger.error(f"[顾问API] reviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@legacy_router.get("/summary")
def legacy_summary():
    return get_summary()


@legacy_router.get("/reviews")
def legacy_reviews(
    review_type: Optional[str] = Query(None),
    decision: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return list_reviews(
        review_type=review_type,
        decision=decision,
        symbol=symbol,
        q=q,
        provider="all",
        limit=limit,
        offset=offset,
    )
