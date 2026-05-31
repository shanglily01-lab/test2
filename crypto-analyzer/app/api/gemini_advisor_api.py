"""Gemini 顾问审核记录 API."""
from __future__ import annotations

import json
from typing import Any, Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from loguru import logger

router = APIRouter(prefix="/api/gemini-advisor", tags=["Gemini顾问审核"])


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


@router.get("/summary")
def get_summary():
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, "gemini_advisor_reviews"):
                return {
                    "ready": False,
                    "message": "请执行 migration 011_gemini_advisor_reviews.sql",
                    "open_total": 0,
                    "hold_total": 0,
                }
            cur.execute(
                """
                SELECT
                  SUM(review_type='open') AS open_total,
                  SUM(review_type='open' AND decision='approve') AS open_approve,
                  SUM(review_type='open' AND decision='reject') AS open_reject,
                  SUM(review_type='hold') AS hold_total,
                  SUM(review_type='hold' AND decision='sell') AS hold_sell,
                  SUM(review_type='hold' AND decision='hold') AS hold_hold,
                  SUM(review_type='hold' AND decision='observe') AS hold_observe
                FROM gemini_advisor_reviews
                WHERE created_at >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                """
            )
            row = cur.fetchone() or {}
        conn.close()
        return {"ready": True, **{k: int(row.get(k) or 0) for k in row}}
    except Exception as e:
        logger.error(f"[Gemini顾问API] summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _review_filters(
    review_type: Optional[str],
    decision: Optional[str],
    symbol: Optional[str],
    q: Optional[str],
) -> tuple[str, list[Any]]:
    """共用 WHERE 片段（不含 ORDER/LIMIT）。"""
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
    if q:
        q = q.strip()
        if q:
            like = f"%{q}%"
            sql += (
                " AND (symbol LIKE %s OR source LIKE %s OR reason LIKE %s"
                " OR decision LIKE %s OR position_side LIKE %s)"
            )
            params.extend([like, like, like, like, like])
    return sql, params


@router.get("/reviews")
def list_reviews(
    review_type: Optional[str] = Query(None, description="open | hold"),
    decision: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    q: Optional[str] = Query(None, description="模糊搜索 symbol/source/reason/decision"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, "gemini_advisor_reviews"):
                return {"reviews": [], "ready": False, "total": 0}

            filt, params = _review_filters(review_type, decision, symbol, q)
            sql = "SELECT COUNT(*) AS cnt FROM gemini_advisor_reviews WHERE 1=1" + filt
            cur.execute(sql, params)
            total = int((cur.fetchone() or {}).get("cnt") or 0)

            sql = """
                SELECT id, review_type, decision, symbol, position_side, source,
                       position_id, entry_price, leverage, hold_hours, roi_pct,
                       reason, catalyst, created_at
                FROM gemini_advisor_reviews
                WHERE 1=1
            """ + filt
            qparams = list(params)
            sql += " ORDER BY id DESC LIMIT %s OFFSET %s"
            qparams.extend([limit, offset])
            cur.execute(sql, qparams)
            rows = cur.fetchall() or []
        conn.close()
        for r in rows:
            if r.get("created_at"):
                r["created_at"] = r["created_at"].isoformat(sep=" ", timespec="seconds")
            for k in ("entry_price", "hold_hours", "roi_pct"):
                if r.get(k) is not None:
                    r[k] = float(r[k])
        return {"ready": True, "total": total, "reviews": rows}
    except Exception as e:
        logger.error(f"[Gemini顾问API] reviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))
