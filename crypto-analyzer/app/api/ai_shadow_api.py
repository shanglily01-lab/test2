"""AI Shadow 对比 — 只读 API (Teacher vs 规则引擎)."""
from __future__ import annotations

import json
from typing import Any, Optional

import pymysql
import pymysql.cursors
from fastapi import APIRouter, HTTPException, Query
from loguru import logger

router = APIRouter(prefix="/api/ai-shadow", tags=["AI Shadow对比"])


ACTIVE_TEACHER_SOURCES = (
    "gemini_explore",
    "gemini_predict",
    "deepseek_explore",
    "deepseek_predict",
    "gpt_predict",
)


def _connect():
    from app.database.connection_pool import get_api_connection
    return get_api_connection()


def _table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def _table_columns(cur, table: str) -> set[str]:
    cur.execute(f"SHOW COLUMNS FROM {table}")
    return {str(row.get("Field") or row.get("field") or "") for row in cur.fetchall() or []}


def _parse_json(val) -> Any:
    if val is None:
        return None
    if isinstance(val, (list, dict)):
        return val
    try:
        return json.loads(val)
    except Exception:
        return val


def _active_source_clause() -> tuple[str, list[str]]:
    placeholders = ",".join(["%s"] * len(ACTIVE_TEACHER_SOURCES))
    return f"teacher_source IN ({placeholders})", list(ACTIVE_TEACHER_SOURCES)


@router.get("/summary")
def get_summary(include_removed: bool = Query(False)):
    """汇总: 总轮数、平均一致率、按 teacher_source 分组."""
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, "ai_shadow_compare_runs"):
                return {
                    "ready": False,
                    "message": "表 ai_shadow_compare_runs 尚未创建, 请执行 migration 005",
                    "total_runs": 0,
                    "avg_agree_pct": 0,
                    "by_source": [],
                }

            active_where = ""
            active_params: list = []
            if not include_removed:
                clause, values = _active_source_clause()
                active_where = f"WHERE {clause}"
                active_params.extend(values)

            cur.execute(
                """
                SELECT COUNT(*) AS total_runs,
                       COALESCE(AVG(category_match / NULLIF(compared_count, 0) * 100), 0) AS avg_agree_pct,
                       COALESCE(SUM(compared_count), 0) AS total_compared,
                       COALESCE(SUM(category_match), 0) AS total_match
                FROM ai_shadow_compare_runs
                {active_where}
                """.format(active_where=active_where),
                active_params,
            )
            overall = cur.fetchone() or {}

            cur.execute(
                """
                SELECT teacher_source,
                       COUNT(*) AS runs,
                       ROUND(AVG(category_match / NULLIF(compared_count, 0) * 100), 1) AS avg_agree_pct,
                       SUM(compared_count) AS compared,
                       SUM(category_match) AS matched
                FROM ai_shadow_compare_runs
                {active_where}
                GROUP BY teacher_source
                ORDER BY teacher_source
                """.format(active_where=active_where),
                active_params,
            )
            by_source = cur.fetchall() or []

        conn.close()
        return {
            "ready": True,
            "total_runs": int(overall.get("total_runs") or 0),
            "avg_agree_pct": round(float(overall.get("avg_agree_pct") or 0), 1),
            "total_compared": int(overall.get("total_compared") or 0),
            "total_match": int(overall.get("total_match") or 0),
            "by_source": by_source,
            "rules_version": "v1",
            "active_sources": list(ACTIVE_TEACHER_SOURCES),
            "include_removed": include_removed,
        }
    except Exception as e:
        logger.error(f"[AI Shadow API] summary 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs")
def list_runs(
    limit: int = Query(30, ge=1, le=100),
    teacher_source: Optional[str] = Query(None),
    include_removed: bool = Query(False),
):
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, "ai_shadow_compare_runs"):
                return {"runs": [], "ready": False}

            cols = _table_columns(cur, "ai_shadow_compare_runs")
            has_input_expr = (
                "(universe_json IS NOT NULL AND universe_json != '' "
                "AND global_ctx_json IS NOT NULL AND global_ctx_json != '')"
                if "universe_json" in cols and "global_ctx_json" in cols else "0"
            )
            sql = """
                SELECT id, teacher_source, teacher_run_id, rules_version,
                       universe_size, compared_count, category_match,
                       teacher_tradeable, shadow_tradeable, tradeable_agree,
                       disagree_samples, elapsed_ms, created_at,
                       ROUND(category_match / NULLIF(compared_count, 0) * 100, 1) AS agree_pct,
                       {has_input_expr} AS has_input
                FROM ai_shadow_compare_runs
            """.format(has_input_expr=has_input_expr)
            params: list = []
            filters = []
            if not include_removed:
                clause, values = _active_source_clause()
                filters.append(clause)
                params.extend(values)
            if teacher_source:
                filters.append("teacher_source = %s")
                params.append(teacher_source)
            if filters:
                sql += " WHERE " + " AND ".join(filters)
            sql += " ORDER BY id DESC LIMIT %s"
            params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall() or []
            for r in rows:
                r["disagree_samples"] = _parse_json(r.get("disagree_samples"))
                if r.get("created_at"):
                    r["created_at"] = r["created_at"].isoformat(sep=" ", timespec="seconds")
        conn.close()
        return {"runs": rows, "ready": True}
    except Exception as e:
        logger.error(f"[AI Shadow API] runs 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/runs/{shadow_run_id}")
def get_run_detail(shadow_run_id: int):
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, "ai_shadow_compare_runs"):
                return {"ready": False}
            cols = _table_columns(cur, "ai_shadow_compare_runs")
            wanted = [
                "id", "teacher_source", "teacher_run_id", "rules_version",
                "universe_size", "compared_count", "category_match", "direction_match",
                "teacher_tradeable", "shadow_tradeable", "tradeable_agree",
                "disagree_samples", "elapsed_ms", "universe_json", "global_ctx_json",
                "teacher_verdicts_json", "shadow_verdicts_json", "created_at",
            ]
            select_cols = [c for c in wanted if c in cols]
            cur.execute(
                f"SELECT {','.join(select_cols)} FROM ai_shadow_compare_runs WHERE id=%s LIMIT 1",
                (shadow_run_id,),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="shadow run not found")
        for key in (
            "disagree_samples", "universe_json", "global_ctx_json",
            "teacher_verdicts_json", "shadow_verdicts_json",
        ):
            if key in row:
                row[key] = _parse_json(row.get(key))
        if row.get("created_at"):
            row["created_at"] = row["created_at"].isoformat(sep=" ", timespec="seconds")
        return {"ready": True, "run": row}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[AI Shadow API] run detail 澶辫触: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdicts")
def list_verdicts(
    shadow_run_id: int = Query(..., ge=1),
    disagreements_only: bool = Query(False),
    limit: int = Query(200, ge=1, le=500),
):
    try:
        conn = _connect()
        with conn.cursor() as cur:
            if not _table_exists(cur, "ai_shadow_verdicts"):
                return {"verdicts": [], "ready": False}

            sql = """
                SELECT id, symbol, teacher_category, teacher_confidence,
                       shadow_category, shadow_confidence, category_match,
                       diff_reason, shadow_signals
                FROM ai_shadow_verdicts
                WHERE shadow_run_id = %s
            """
            params: list = [shadow_run_id]
            if disagreements_only:
                sql += " AND category_match = 0"
            sql += " ORDER BY category_match ASC, symbol ASC LIMIT %s"
            params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall() or []
            for r in rows:
                r["shadow_signals"] = _parse_json(r.get("shadow_signals"))
                for k in ("teacher_confidence", "shadow_confidence"):
                    if r.get(k) is not None:
                        r[k] = float(r[k])
        conn.close()
        return {"verdicts": rows, "ready": True}
    except Exception as e:
        logger.error(f"[AI Shadow API] verdicts 失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))
