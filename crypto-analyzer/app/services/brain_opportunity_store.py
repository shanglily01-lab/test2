"""REQ-BRAIN v2 机会落库 — brain_scan_rounds + brain_opportunities (§7.3.13)"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from loguru import logger

from app.utils.position_time import utc_now_naive

_SCHEMA_READY = False


def ensure_brain_opportunity_schema(conn) -> None:
    """CREATE IF NOT EXISTS — 幂等，可在 API/scheduler 路径调用。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS brain_scan_rounds (
              id BIGINT NOT NULL AUTO_INCREMENT,
              triggered_by VARCHAR(64) DEFAULT NULL,
              status VARCHAR(32) NOT NULL DEFAULT 'running',
              universe_size INT DEFAULT 0,
              opportunities INT DEFAULT 0,
              opened INT DEFAULT 0,
              skipped INT DEFAULT 0,
              closed INT DEFAULT 0,
              big4_ok TINYINT(1) DEFAULT NULL,
              big4_bias VARCHAR(16) DEFAULT NULL,
              error_msg VARCHAR(500) DEFAULT NULL,
              summary_json JSON DEFAULT NULL,
              started_at DATETIME DEFAULT NULL,
              finished_at DATETIME DEFAULT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_brain_scan_started (started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS brain_opportunities (
              id BIGINT NOT NULL AUTO_INCREMENT,
              scan_round_id BIGINT DEFAULT NULL,
              symbol VARCHAR(32) NOT NULL,
              side ENUM('LONG','SHORT','FLAT') NOT NULL DEFAULT 'FLAT',
              playbook VARCHAR(16) NOT NULL,
              signals JSON DEFAULT NULL,
              evidence_summary TEXT,
              ref_price DECIMAL(18,8) DEFAULT NULL,
              win_prob_long FLOAT DEFAULT NULL,
              win_prob_short FLOAT DEFAULT NULL,
              edge_score FLOAT DEFAULT NULL,
              decision ENUM('OPENED','SKIPPED') NOT NULL DEFAULT 'SKIPPED',
              skip_reason VARCHAR(200) DEFAULT NULL,
              order_id BIGINT DEFAULT NULL,
              shadow_pnl_4h FLOAT DEFAULT NULL,
              actual_pnl FLOAT DEFAULT NULL,
              exit_reason VARCHAR(100) DEFAULT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (id),
              KEY idx_brain_opp_playbook_decision (playbook, decision),
              KEY idx_brain_opp_symbol_created (symbol, created_at),
              KEY idx_brain_opp_scan (scan_round_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
    try:
        conn.commit()
    except Exception:
        pass
    _SCHEMA_READY = True
    logger.info("[BRAIN] brain_scan_rounds / brain_opportunities schema ready")


def start_scan_round(conn, *, triggered_by: str, universe_size: int = 0,
                     big4_ok: Optional[bool] = None, big4_bias: Optional[str] = None) -> int:
    ensure_brain_opportunity_schema(conn)
    now = utc_now_naive()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO brain_scan_rounds
              (triggered_by, status, universe_size, big4_ok, big4_bias, started_at)
            VALUES (%s, 'running', %s, %s, %s, %s)
            """,
            (triggered_by[:64], int(universe_size), 1 if big4_ok else 0 if big4_ok is not None else None,
             (big4_bias or "")[:16] or None, now),
        )
        rid = int(cur.lastrowid)
    conn.commit()
    return rid


def finish_scan_round(
    conn,
    round_id: int,
    *,
    status: str = "ok",
    opportunities: int = 0,
    opened: int = 0,
    skipped: int = 0,
    closed: int = 0,
    error_msg: Optional[str] = None,
    summary: Optional[Dict[str, Any]] = None,
) -> None:
    now = utc_now_naive()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE brain_scan_rounds SET
              status=%s, opportunities=%s, opened=%s, skipped=%s, closed=%s,
              error_msg=%s, summary_json=%s, finished_at=%s
            WHERE id=%s
            """,
            (
                status[:32],
                int(opportunities),
                int(opened),
                int(skipped),
                int(closed),
                (error_msg or "")[:500] or None,
                json.dumps(summary, ensure_ascii=False, default=str) if summary else None,
                now,
                int(round_id),
            ),
        )
    conn.commit()


def insert_opportunity(
    conn,
    *,
    scan_round_id: Optional[int],
    symbol: str,
    side: str,
    playbook: str,
    signals: List[str],
    evidence_summary: str,
    ref_price: Optional[float],
    win_prob_long: Optional[float],
    win_prob_short: Optional[float],
    edge_score: Optional[float],
    decision: str = "SKIPPED",
    skip_reason: Optional[str] = None,
    order_id: Optional[int] = None,
) -> int:
    ensure_brain_opportunity_schema(conn)
    side_u = (side or "FLAT").upper()
    if side_u not in ("LONG", "SHORT", "FLAT"):
        side_u = "FLAT"
    decision_u = "OPENED" if (decision or "").upper() == "OPENED" else "SKIPPED"
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO brain_opportunities
              (scan_round_id, symbol, side, playbook, signals, evidence_summary,
               ref_price, win_prob_long, win_prob_short, edge_score,
               decision, skip_reason, order_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                scan_round_id,
                symbol[:32],
                side_u,
                (playbook or "D1")[:16],
                json.dumps(list(signals or []), ensure_ascii=False),
                (evidence_summary or "")[:4000],
                ref_price,
                win_prob_long,
                win_prob_short,
                edge_score,
                decision_u,
                (skip_reason or "")[:200] or None,
                order_id,
            ),
        )
        oid = int(cur.lastrowid)
    conn.commit()
    return oid


def list_opportunities(
    conn,
    *,
    limit: int = 100,
    playbook: Optional[str] = None,
    decision: Optional[str] = None,
    scan_round_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    ensure_brain_opportunity_schema(conn)
    limit = max(1, min(int(limit or 100), 500))
    where = ["1=1"]
    params: List[Any] = []
    if playbook:
        where.append("playbook=%s")
        params.append(playbook)
    if decision:
        where.append("decision=%s")
        params.append(decision.upper())
    if scan_round_id is not None:
        where.append("scan_round_id=%s")
        params.append(int(scan_round_id))
    sql = (
        f"SELECT * FROM brain_opportunities WHERE {' AND '.join(where)} "
        f"ORDER BY id DESC LIMIT %s"
    )
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = list(cur.fetchall() or [])
    for r in rows:
        sig = r.get("signals")
        if isinstance(sig, (bytes, bytearray)):
            sig = sig.decode("utf-8", errors="ignore")
        if isinstance(sig, str):
            try:
                r["signals"] = json.loads(sig)
            except Exception:
                r["signals"] = []
    return rows


def playbook_stats(conn, *, days: int = 30) -> List[Dict[str, Any]]:
    ensure_brain_opportunity_schema(conn)
    days = max(1, min(int(days or 30), 90))
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT playbook,
                   COUNT(*) AS identified,
                   SUM(decision='OPENED') AS opened,
                   SUM(decision='SKIPPED') AS skipped,
                   AVG(CASE WHEN decision='OPENED' AND actual_pnl IS NOT NULL THEN actual_pnl END) AS avg_actual_pnl,
                   AVG(shadow_pnl_4h) AS avg_shadow_pnl_4h
            FROM brain_opportunities
            WHERE created_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
            GROUP BY playbook
            ORDER BY identified DESC
            """,
            (days,),
        )
        return list(cur.fetchall() or [])
