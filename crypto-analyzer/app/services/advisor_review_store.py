"""Shared advisor review DB writer (provider-neutral).

DeepSeek / Gemini / GPT review tables share the same column shape.
Provider-specific modules are thin wrappers around log_advisor_review_row.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pymysql
from loguru import logger

from app.services.advisor_review_payloads import dumps_json, table_columns
from app.utils.config_loader import get_db_config

GEMINI_ADVISOR_REVIEWS_TABLE = "gemini_advisor_reviews"
DEEPSEEK_ADVISOR_REVIEWS_TABLE = "deepseek_advisor_reviews"
GPT_ADVISOR_REVIEWS_TABLE = "gpt_advisor_reviews"


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


def log_advisor_review_row(
    table: str,
    review_type: str,
    decision: str,
    symbol: str,
    *,
    log_tag: str = "顾问记录",
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
    prompt_text: Optional[str] = None,
    input_json: Optional[Dict[str, Any]] = None,
    raw_response: Optional[str] = None,
    system_prompt: Optional[str] = None,
    conn=None,
) -> Optional[int]:
    """写入指定顾问审核表；表不存在时静默跳过."""
    own = conn is None
    if own:
        try:
            conn = _connect()
        except Exception as e:
            logger.warning(f"[{log_tag}] DB 连接失败: {e}")
            return None
    try:
        with conn.cursor() as cur:
            if not _table_exists(cur, table):
                return None
            cols = table_columns(cur, table)
            data = {
                "review_type": review_type,
                "decision": decision[:20],
                "symbol": symbol,
                "position_side": position_side,
                "source": (source or "")[:64] or None,
                "position_id": position_id,
                "entry_price": entry_price,
                "leverage": leverage,
                "hold_hours": hold_hours,
                "roi_pct": roi_pct,
                "reason": (reason or "")[:500] or None,
                "catalyst": (catalyst or "")[:500] or None,
                "extra_json": dumps_json(extra),
                "prompt_text": prompt_text,
                "input_json": dumps_json(input_json),
                "raw_response": raw_response,
                "system_prompt": system_prompt,
            }
            keys = [k for k in data if k in cols]
            placeholders = ",".join(["%s"] * len(keys))
            cur.execute(
                f"INSERT INTO {table} ({','.join(keys)}) VALUES ({placeholders})",
                tuple(data[k] for k in keys),
            )
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"[{log_tag}] 写入失败 {review_type} {symbol}: {e}")
        return None
    finally:
        if own and conn:
            try:
                conn.close()
            except Exception:
                pass


__all__ = [
    "GEMINI_ADVISOR_REVIEWS_TABLE",
    "DEEPSEEK_ADVISOR_REVIEWS_TABLE",
    "GPT_ADVISOR_REVIEWS_TABLE",
    "log_advisor_review_row",
]
