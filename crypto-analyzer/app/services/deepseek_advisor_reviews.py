"""DeepSeek 顾问审核记录 — 开仓落库."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

import pymysql
from loguru import logger

from app.services.advisor_review_payloads import dumps_json, table_columns
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
    prompt_text: Optional[str] = None,
    input_json: Optional[Dict[str, Any]] = None,
    raw_response: Optional[str] = None,
    system_prompt: Optional[str] = None,
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
            cols = table_columns(cur, "deepseek_advisor_reviews")
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
                f"INSERT INTO deepseek_advisor_reviews ({','.join(keys)}) VALUES ({placeholders})",
                tuple(data[k] for k in keys),
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
