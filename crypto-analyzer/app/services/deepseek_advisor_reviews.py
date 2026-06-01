"""DeepSeek 顾问审核记录 — 开仓落库."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pymysql
from loguru import logger

from app.utils.config_loader import get_db_config


def _connect():
    return pymysql.connect(
        **get_db_config(),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def _table_exists(cur, table: str) -> bool:
    cur.execute("SHOW TABLES LIKE %s", (table,))
    return cur.fetchone() is not None


def log_deepseek_advisor_review(
    review_type: str,
    decision: str,
    symbol: str,
    *,
    position_side: Optional[str] = None,
    source: Optional[str] = None,
    position_id: Optional[int] = None,
    entry_price: Optional[float] = None,
    leverage: Optional[int] = None,
    hold_hours: Optional[float] = None,
    roi_pct: Optional[float] = None,
    reason: Optional[str] = None,
    catalyst: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
    conn=None,
) -> Optional[int]:
    """写入 deepseek_advisor_reviews; 表不存在时静默跳过."""
    own = conn is None
    if own:
        try:
            conn = _connect()
        except Exception as e:
            logger.warning(f"[DeepSeek顾问记录] DB 连接失败: {e}")
            return None
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, "deepseek_advisor_reviews"):
                return None
            extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
            cur.execute(
                """
                INSERT INTO deepseek_advisor_reviews
                  (review_type, decision, symbol, position_side, source,
                   position_id, entry_price, leverage, hold_hours, roi_pct,
                   reason, catalyst, extra_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    review_type,
                    decision[:20],
                    symbol,
                    position_side,
                    (source or "")[:64] or None,
                    position_id,
                    entry_price,
                    leverage,
                    hold_hours,
                    roi_pct,
                    (reason or "")[:500] or None,
                    (catalyst or "")[:500] or None,
                    extra_json,
                ),
            )
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"[DeepSeek顾问记录] 写入失败 {review_type} {symbol}: {e}")
        return None
    finally:
        if own and conn:
            try:
                conn.close()
            except Exception:
                pass
