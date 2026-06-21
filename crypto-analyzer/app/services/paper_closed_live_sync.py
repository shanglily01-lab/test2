"""Close live positions whose linked paper position is already closed."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List

import pymysql
import pymysql.cursors
from loguru import logger

from app.services.trading_gates import is_live_close_enabled


class PaperClosedLiveSync:
    """Best-effort recovery for paper-close -> live-close sync failures."""

    def __init__(self, db_config: dict):
        self.db_config = db_config

    def _get_conn(self):
        return pymysql.connect(
            **self.db_config,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _fetch_candidates(self, limit: int) -> List[dict]:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    lp.id AS live_id,
                    lp.account_id AS live_account_id,
                    lp.paper_position_id,
                    lp.symbol,
                    lp.position_side,
                    lp.quantity,
                    lp.entry_price,
                    lp.open_time AS live_open_time,
                    lp.source AS live_source,
                    fp.close_time AS paper_close_time,
                    fp.notes AS paper_close_notes,
                    fp.source AS paper_source
                FROM live_futures_positions lp
                JOIN futures_positions fp ON fp.id = lp.paper_position_id
                WHERE lp.status = 'OPEN'
                  AND lp.paper_position_id IS NOT NULL
                  AND fp.status = 'closed'
                ORDER BY fp.close_time ASC, lp.id ASC
                LIMIT %s
                """,
                (limit,),
            )
            return cur.fetchall() or []
        finally:
            try:
                cur.close()
            except Exception:
                pass
            conn.close()

    def _mark_closed(self, row: dict, result: dict, reason: str) -> None:
        conn = self._get_conn()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE live_futures_positions
                SET status='CLOSED',
                    close_time=NOW(),
                    close_price=%s,
                    realized_pnl=%s,
                    close_reason=%s,
                    notes=CONCAT(IFNULL(notes,''), %s),
                    updated_at=NOW()
                WHERE id=%s AND status='OPEN'
                """,
                (
                    result.get("close_price"),
                    result.get("realized_pnl"),
                    reason,
                    f"|{reason}",
                    row["live_id"],
                ),
            )
            cur.close()
        finally:
            conn.close()

    def run_once(self, limit: int = 50) -> Dict[str, int]:
        stats = {"checked": 0, "closed": 0, "skipped": 0, "errors": 0}
        if not is_live_close_enabled():
            stats["skipped"] += 1
            return stats

        rows = self._fetch_candidates(limit)
        if not rows:
            return stats

        from app.services.api_key_service import APIKeyService
        from app.trading.binance_futures_engine import BinanceFuturesEngine

        key_service = APIKeyService(self.db_config)
        keys = key_service.get_all_active_api_keys("binance")
        keys_by_id = {k["id"]: k for k in keys}

        for row in rows:
            stats["checked"] += 1
            paper_source = (row.get("paper_source") or row.get("live_source") or "").strip()
            from app.services.trading_gates import should_sync_live_for_source
            if not should_sync_live_for_source(paper_source):
                stats["skipped"] += 1
                continue
            key_info = keys_by_id.get(row["live_account_id"])
            if not key_info:
                logger.warning(
                    f"[PaperClosedLiveSync] live_id={row['live_id']} "
                    f"account_id={row['live_account_id']} has no active API key"
                )
                stats["errors"] += 1
                continue

            reason = f"paper_closed_sync:{row['paper_position_id']}"
            try:
                engine = BinanceFuturesEngine(
                    self.db_config,
                    api_key=key_info["api_key"],
                    api_secret=key_info["api_secret"],
                )
                result = engine.close_position_direct(
                    symbol=row["symbol"],
                    position_side=row["position_side"],
                    quantity=Decimal(str(row["quantity"])),
                    entry_price=Decimal(str(row["entry_price"])),
                    reason=reason,
                    strategy_name=row.get("paper_source") or row.get("live_source"),
                    open_time=row.get("live_open_time"),
                )
                if not result.get("success"):
                    logger.error(
                        f"[PaperClosedLiveSync] close failed live_id={row['live_id']} "
                        f"paper_id={row['paper_position_id']} {row['symbol']} "
                        f"{row['position_side']}: {result.get('error') or result}"
                    )
                    stats["errors"] += 1
                    continue

                self._mark_closed(row, result, reason)
                stats["closed"] += 1
                logger.info(
                    f"[PaperClosedLiveSync] closed live_id={row['live_id']} "
                    f"paper_id={row['paper_position_id']} {row['symbol']} "
                    f"{row['position_side']}"
                )
            except Exception as e:
                logger.error(
                    f"[PaperClosedLiveSync] exception live_id={row.get('live_id')} "
                    f"paper_id={row.get('paper_position_id')}: {e}"
                )
                stats["errors"] += 1

        return stats


def run_paper_closed_live_sync(db_config: dict, limit: int = 50) -> Dict[str, int]:
    return PaperClosedLiveSync(db_config).run_once(limit=limit)
