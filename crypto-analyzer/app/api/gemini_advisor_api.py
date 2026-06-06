"""顾问审核记录 API — Gemini + DeepSeek 开仓审核."""
from __future__ import annotations

import json
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

_UNION_SELECT_TEMPLATE = """
SELECT
  '{provider}' COLLATE utf8mb4_general_ci AS provider,
  id,
  review_type COLLATE utf8mb4_general_ci AS review_type,
  decision COLLATE utf8mb4_general_ci AS decision,
  symbol COLLATE utf8mb4_general_ci AS symbol,
  position_side COLLATE utf8mb4_general_ci AS position_side,
  source COLLATE utf8mb4_general_ci AS source,
  position_id,
  entry_price,
  leverage,
  hold_hours,
  roi_pct,
  CAST(reason AS CHAR) COLLATE utf8mb4_general_ci AS reason,
  CAST(catalyst AS CHAR) COLLATE utf8mb4_general_ci AS catalyst,
  (prompt_text IS NOT NULL AND prompt_text != '') AS has_prompt,
  (input_json IS NOT NULL AND input_json != '') AS has_input,
  (raw_response IS NOT NULL AND raw_response != '') AS has_raw,
  created_at
FROM {table} WHERE 1=1
"""


def _union_select(cur, provider: str, table: str) -> str:
    cols = _table_columns(cur, table)
    has_prompt = "(prompt_text IS NOT NULL AND prompt_text != '')" if "prompt_text" in cols else "0"
    has_input = "(input_json IS NOT NULL AND input_json != '')" if "input_json" in cols else "0"
    has_raw = "(raw_response IS NOT NULL AND raw_response != '')" if "raw_response" in cols else "0"
    return f"""
SELECT
  '{provider}' COLLATE utf8mb4_general_ci AS provider,
  id,
  review_type COLLATE utf8mb4_general_ci AS review_type,
  decision COLLATE utf8mb4_general_ci AS decision,
  symbol COLLATE utf8mb4_general_ci AS symbol,
  position_side COLLATE utf8mb4_general_ci AS position_side,
  source COLLATE utf8mb4_general_ci AS source,
  position_id,
  entry_price,
  leverage,
  hold_hours,
  roi_pct,
  CAST(reason AS CHAR) COLLATE utf8mb4_general_ci AS reason,
  CAST(catalyst AS CHAR) COLLATE utf8mb4_general_ci AS catalyst,
  {has_prompt} AS has_prompt,
  {has_input} AS has_input,
  {has_raw} AS has_raw,
  created_at
FROM {table} WHERE 1=1
"""


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


def _table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def _table_columns(cur, table: str) -> set[str]:
    cur.execute(f"SHOW COLUMNS FROM {table}")
    return {str(row.get("Field") or row.get("field") or "") for row in cur.fetchall() or []}


def _parse_json(val):
    if val is None or isinstance(val, (dict, list)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val


def _tables_ready(cur) -> tuple[bool, bool]:
    gemini = _table_exists(cur, "gemini_advisor_reviews")
    deepseek = _table_exists(cur, "deepseek_advisor_reviews")
    gpt = _table_exists(cur, "gpt_advisor_reviews")
    return gemini, deepseek, gpt


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
    if provider in ("gemini", "deepseek", "gpt"):
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
        gemini_ok, deepseek_ok, gpt_ok = _tables_ready(cur)
        if not gemini_ok and not deepseek_ok and not gpt_ok:
            return {
                "ready": False,
                "message": "请执行 migration 011 / 013",
                "open_total": 0,
                "hold_total": 0,
            }
        g = _aggregate_summary(cur, "gemini_advisor_reviews", "gemini") if gemini_ok else {}
        d = _aggregate_summary(cur, "deepseek_advisor_reviews", "deepseek") if deepseek_ok else {}
        p = _aggregate_summary(cur, "gpt_advisor_reviews", "gpt") if gpt_ok else {}
    conn.close()
    return {
        "ready": True,
        "gemini_ready": gemini_ok,
        "deepseek_ready": deepseek_ok,
        "gpt_ready": gpt_ok,
        "open_approve": int(g.get("open_approve", 0)) + int(d.get("open_approve", 0)) + int(p.get("open_approve", 0)),
        "open_reject": int(g.get("open_reject", 0)) + int(d.get("open_reject", 0)) + int(p.get("open_reject", 0)),
        "open_total": int(g.get("open_total", 0)) + int(d.get("open_total", 0)) + int(p.get("open_total", 0)),
        "hold_sell": int(g.get("hold_sell", 0)) + int(d.get("hold_sell", 0)) + int(p.get("hold_sell", 0)),
        "hold_hold": int(g.get("hold_hold", 0)) + int(d.get("hold_hold", 0)) + int(p.get("hold_hold", 0)),
        "hold_observe": int(g.get("hold_observe", 0)) + int(d.get("hold_observe", 0)) + int(p.get("hold_observe", 0)),
        "hold_total": int(g.get("hold_total", 0)) + int(d.get("hold_total", 0)) + int(p.get("hold_total", 0)),
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
        gemini_ok, deepseek_ok, gpt_ok = _tables_ready(cur)
        if not gemini_ok and not deepseek_ok and not gpt_ok:
            conn.close()
            return {"reviews": [], "ready": False, "total": 0}

        parts = []
        if gemini_ok and provider in (None, "", "all", "gemini"):
            parts.append(_union_select(cur, "gemini", "gemini_advisor_reviews"))
        if deepseek_ok and provider in (None, "", "all", "deepseek"):
            parts.append(_union_select(cur, "deepseek", "deepseek_advisor_reviews"))
        if gpt_ok and provider in (None, "", "all", "gpt"):
            parts.append(_union_select(cur, "gpt", "gpt_advisor_reviews"))
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
    provider: Optional[str] = Query(None, description="gemini | deepseek | gpt | all"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    try:
        prov = (provider or "all").lower()
        if prov not in ("all", "gemini", "deepseek", "gpt"):
            prov = "all"
        return _list_reviews(review_type, decision, symbol, q, prov, limit, offset)
    except Exception as e:
        logger.error(f"[顾问API] reviews: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/reviews/{provider}/{review_id}")
def get_review_detail(provider: str, review_id: int):
    provider = (provider or "").lower()
    table_map = {
        "gemini": "gemini_advisor_reviews",
        "deepseek": "deepseek_advisor_reviews",
        "gpt": "gpt_advisor_reviews",
    }
    table = table_map.get(provider)
    if not table:
        raise HTTPException(status_code=400, detail="provider must be gemini/deepseek/gpt")
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, table):
                conn.close()
                raise HTTPException(status_code=404, detail="review table not found")
            cols = _table_columns(cur, table)
            wanted = [
                "id", "review_type", "decision", "symbol", "position_side", "source",
                "position_id", "entry_price", "leverage", "hold_hours", "roi_pct",
                "reason", "catalyst", "extra_json", "prompt_text", "input_json",
                "raw_response", "system_prompt", "created_at",
            ]
            select_cols = [c for c in wanted if c in cols]
            cur.execute(
                f"SELECT {','.join(select_cols)} FROM {table} WHERE id=%s LIMIT 1",
                (review_id,),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="review not found")
        row["provider"] = provider
        for key in ("extra_json", "input_json"):
            if key in row:
                row[key] = _parse_json(row.get(key))
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat(sep=" ", timespec="seconds")
        for k in ("entry_price", "hold_hours", "roi_pct"):
            if row.get(k) is not None:
                row[k] = float(row[k])
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[椤鹃棶API] review detail: {e}")
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


@legacy_router.get("/reviews/{provider}/{review_id}")
def legacy_review_detail(provider: str, review_id: int):
    return get_review_detail(provider, review_id)
